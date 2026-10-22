"""프레임 소스 백엔드.

ScrcpySource      : scrcpy-server를 기기에 올려 raw H.264 소켓 스트림 수신 (권장, 저지연)
ScreenrecordSource: adb exec-out screenrecord 파이프 (서버 파일 없이 동작, 3분마다 자동 재시작)
DemoSource        : 기기 없이 UI를 확인하기 위한 합성 프레임
"""

from __future__ import annotations

import logging
import random
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from . import SCRCPY_VERSION
from .adb import Adb, AdbError
from .decoder import H264Decoder

log = logging.getLogger(__name__)

FrameCb = Callable[[np.ndarray], None]
PacketCb = Callable[[object], None]
StatusCb = Callable[[str], None]
ErrorCb = Callable[[str], None]

REMOTE_SERVER = "/data/local/tmp/xr-mirror-server.jar"
SCREENRECORD_LIMIT_S = 180   # Android screenrecord 하드 리밋


class FrameSource(threading.Thread):
    def __init__(self, on_frame: FrameCb, on_status: StatusCb, on_error: ErrorCb,
                 on_packet: PacketCb | None = None):
        super().__init__(daemon=True)
        self.on_frame = on_frame
        self.on_status = on_status
        self.on_error = on_error
        self.on_packet = on_packet
        self._stop_evt = threading.Event()
        self.decoder: H264Decoder | None = None
        self.started_at = 0.0

    def stop(self) -> None:
        self._stop_evt.set()

    @property
    def stopping(self) -> bool:
        return self._stop_evt.is_set()

    def _pump(self, read: Callable[[int], bytes], chunk: int = 1 << 16) -> None:
        """read()에서 바이트를 읽어 디코더에 넣고 콜백을 호출. EOF/중지 시 반환."""
        assert self.decoder is not None
        while not self._stop_evt.is_set():
            data = read(chunk)
            if not data:
                return
            for dec in self.decoder.feed(data):
                if self.on_packet:
                    self.on_packet(dec.packet)
                for frame in dec.frames:
                    self.on_frame(frame)


# --------------------------------------------------------------------------
class ScrcpySource(FrameSource):
    def __init__(self, adb: Adb, serial: str, server_jar: Path, *, max_size: int = 1920,
                 bit_rate: int = 8_000_000, max_fps: int = 60, keyframe_interval: int = 5,
                 display_id: int = 0, **cb):
        super().__init__(**cb)
        self.adb = adb
        self.serial = serial
        self.server_jar = server_jar
        self.max_size = max_size
        self.bit_rate = bit_rate
        self.max_fps = max_fps
        self.keyframe_interval = keyframe_interval
        self.display_id = display_id
        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._port: int | None = None
        self.server_log: list[str] = []

    def stop(self) -> None:
        super().stop()
        self._close_io()          # recv() 블로킹 해제. forward 해제는 run()의 finally에서

    def run(self) -> None:
        self.decoder = H264Decoder()
        try:
            self._run()
        except AdbError as e:
            self.on_error(str(e))
        except Exception as e:  # noqa: BLE001
            if not self.stopping:
                log.exception("scrcpy source crashed")
                self.on_error(f"scrcpy 스트림 오류: {e}")
        finally:
            self._cleanup()

    def _run(self) -> None:
        if not self.server_jar.is_file():
            raise AdbError(f"scrcpy 서버 파일이 없습니다: {self.server_jar}\n"
                           f"setup_env.py를 실행하거나 --server 로 경로를 지정하세요.")
        self.on_status("서버 전송 중...")
        self.adb.push(self.server_jar, REMOTE_SERVER, self.serial)

        scid = random.randint(1, 0x7FFFFFFF)
        self._port = _free_port()
        self.adb.forward(self._port, f"localabstract:scrcpy_{scid:08x}", self.serial)

        args = [
            f"CLASSPATH={REMOTE_SERVER}", "app_process", "/", "com.genymobile.scrcpy.Server",
            SCRCPY_VERSION,
            f"scid={scid:08x}",
            "log_level=info",
            "tunnel_forward=true",
            "video=true", "audio=false", "control=false",
            # raw_stream 대신 더미 바이트만 켠다: adb forward는 기기측 리스너가 없어도
            # TCP 연결을 일단 받아주므로, 첫 1바이트 수신으로 진짜 연결을 판별한다.
            "send_device_meta=false", "send_frame_meta=false",
            "send_stream_meta=false", "send_dummy_byte=true",
            "video_codec=h264",
            f"max_size={self.max_size}",
            f"video_bit_rate={self.bit_rate}",
            f"max_fps={self.max_fps}",
            f"display_id={self.display_id}",
            f"video_codec_options=i-frame-interval={self.keyframe_interval}",
            "cleanup=true",
            "downsize_on_error=true",
        ]
        self.on_status("서버 시작 중...")
        self._proc = self.adb.popen("shell", *args, serial=self.serial)
        threading.Thread(target=self._drain_server_log, daemon=True).start()

        # 서버가 listen할 때까지 연결을 재시도 (더미 바이트 1개가 오면 성공)
        self._sock = self._connect(self._port, timeout=8.0)
        self.on_status("스트림 수신 중")
        self.started_at = time.time()
        sock = self._sock

        def read(n: int) -> bytes:
            try:
                return sock.recv(n)
            except OSError:
                return b""

        self._pump(read)
        if not self.stopping:
            tail = "\n".join(self.server_log[-5:])
            self.on_error("기기 스트림이 끊어졌습니다." + (f"\n{tail}" if tail else ""))

    def _connect(self, port: int, timeout: float) -> socket.socket:
        deadline = time.time() + timeout
        last: Exception | None = None
        while time.time() < deadline and not self.stopping:
            if self._proc and self._proc.poll() is not None:
                tail = "\n".join(self.server_log[-8:])
                raise AdbError(f"scrcpy 서버가 종료되었습니다 (rc={self._proc.returncode}).\n{tail}")
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", port))
                s.settimeout(3.0)
                head = s.recv(1)          # 서버 accept 직후 보내는 더미 바이트(0x00)
                if head:
                    s.settimeout(None)
                    return s
                s.close()                 # adb가 기기측 소켓 부재로 바로 닫은 경우
            except OSError as e:
                last = e
                s.close()
            time.sleep(0.15)
        raise AdbError(f"scrcpy 서버 소켓 연결 실패: {last}")

    def _drain_server_log(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                self.server_log.append(line)
                log.info("[server] %s", line)
        if proc.stderr:
            err = proc.stderr.read().decode("utf-8", "replace").strip()
            if err:
                self.server_log.extend(err.splitlines())
                log.warning("[server:stderr] %s", err)

    def _close_io(self) -> None:
        sock, self._sock = self._sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def _cleanup(self) -> None:
        self._close_io()
        if self._port is not None:
            try:
                self.adb.forward_remove(self._port, self.serial)
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
class ScreenrecordSource(FrameSource):
    def __init__(self, adb: Adb, serial: str, *, max_size: int = 1920,
                 bit_rate: int = 8_000_000, **cb):
        super().__init__(**cb)
        self.adb = adb
        self.serial = serial
        self.max_size = max_size
        self.bit_rate = bit_rate
        self._proc: subprocess.Popen | None = None
        self.restarts = 0

    def stop(self) -> None:
        super().stop()
        if self._proc and self._proc.poll() is None:
            self._proc.kill()    # stdout.read() 블로킹 해제

    def run(self) -> None:
        self.decoder = H264Decoder()
        try:
            size_arg = self._size_arg()
            self.started_at = time.time()
            while not self.stopping:
                self.on_status("스트림 수신 중 (screenrecord)" + (f", 재시작 {self.restarts}회" if self.restarts else ""))
                args = ["exec-out", "screenrecord", "--output-format=h264",
                        f"--bit-rate={self.bit_rate}", f"--time-limit={SCREENRECORD_LIMIT_S}"]
                if size_arg:
                    args.append(f"--size={size_arg}")
                args.append("-")
                self._proc = self.adb.popen(*args, serial=self.serial)
                assert self._proc.stdout is not None
                t0 = time.time()
                self._pump(self._proc.stdout.read)
                if self.stopping:
                    break
                rc = self._proc.wait(timeout=5)
                err = (self._proc.stderr.read() if self._proc.stderr else b"").decode("utf-8", "replace").strip()
                if time.time() - t0 < 2.0:   # 즉시 종료 = 실패
                    raise AdbError(f"screenrecord 실패 (rc={rc}): {err or '출력 없음'}")
                self.restarts += 1
        except AdbError as e:
            self.on_error(str(e))
        except Exception as e:  # noqa: BLE001
            if not self.stopping:
                log.exception("screenrecord source crashed")
                self.on_error(f"screenrecord 오류: {e}")
        finally:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()

    def _size_arg(self) -> str | None:
        if self.max_size <= 0:
            return None
        size = self.adb.display_size(self.serial)
        if not size:
            return None
        w, h = size
        m = max(w, h)
        if m <= self.max_size:
            return None
        s = self.max_size / m
        w2 = int(w * s) // 8 * 8
        h2 = int(h * s) // 8 * 8
        return f"{w2}x{h2}"


# --------------------------------------------------------------------------
class DemoSource(FrameSource):
    """기기 없이 좌우 양안 형태의 움직이는 패턴을 30fps로 생성."""

    def __init__(self, width: int = 1600, height: int = 800, fps: int = 30, **cb):
        super().__init__(**cb)
        self.width, self.height, self.fps = width, height, fps

    def run(self) -> None:
        self.on_status("데모 스트림")
        self.started_at = time.time()
        w, h = self.width, self.height
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        half = w // 2
        t0 = time.time()
        n = 0
        while not self.stopping:
            t = time.time() - t0
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            for eye, ox in ((0, 0), (1, half)):
                cx = ox + half / 2 + np.sin(t * 1.3 + eye * 0.15) * half * 0.25
                cy = h / 2 + np.cos(t * 0.9) * h * 0.25
                r = np.hypot(xx - cx, yy - cy)
                ring = ((r % 80) < 12)
                frame[..., 0] = np.where(ring, 230, frame[..., 0])
                frame[..., 1] = np.where(ring, 120 + eye * 80, frame[..., 1])
                frame[..., 2] = np.where(ring, 60, frame[..., 2])
            frame[:, half - 2:half + 2] = 255
            grid = ((xx % 100) < 1) | ((yy % 100) < 1)
            frame[grid] = (50, 50, 60)
            self.on_frame(frame)
            n += 1
            time.sleep(max(0.0, t0 + n / self.fps - time.time()))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
