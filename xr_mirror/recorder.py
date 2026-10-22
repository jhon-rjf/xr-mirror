"""세션 녹화: 들어오는 H.264 패킷을 그대로 .h264 파일에 쓰고, 종료 시 mp4로 재포장."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import av
from fractions import Fraction

from .decoder import NAL_IDR, NAL_PPS, NAL_SPS, nal_types

log = logging.getLogger(__name__)


class Recorder:
    """키프레임 직전에 캐시한 SPS/PPS를 먼저 쓰고, 다음 IDR부터 기록을 시작한다."""

    def __init__(self):
        self._sps: bytes | None = None
        self._pps: bytes | None = None
        self._fh = None
        self._path: Path | None = None
        self._armed = False      # 녹화 요청됨, 키프레임 대기 중
        self._writing = False
        self._lock = threading.Lock()
        self.bytes_written = 0

    # ----- 상태 -----
    @property
    def active(self) -> bool:
        return self._armed or self._writing

    @property
    def waiting_keyframe(self) -> bool:
        return self._armed and not self._writing

    @property
    def path(self) -> Path | None:
        return self._path

    # ----- 제어 -----
    def start(self, path: Path) -> None:
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._path = path.with_suffix(".h264")
            self._fh = open(self._path, "wb")
            self._armed = True
            self._writing = False
            self.bytes_written = 0

    def stop(self) -> Path | None:
        """파일을 닫고 mp4 재포장을 백그라운드로 시작. 최종 mp4 경로를 돌려준다."""
        with self._lock:
            if not self._fh:
                return None
            self._fh.close()
            self._fh = None
            self._armed = self._writing = False
            raw = self._path
        if raw is None or self.bytes_written == 0:
            if raw and raw.exists():
                raw.unlink()
            return None
        mp4 = raw.with_suffix(".mp4")
        threading.Thread(target=_remux, args=(raw, mp4), daemon=True).start()
        return mp4

    # ----- 데이터 -----
    def feed(self, packet: av.Packet) -> None:
        data = bytes(packet)
        types = nal_types(data)
        # SPS/PPS는 늘 최신으로 캐시 (스트림 재시작 시 갱신됨)
        if NAL_SPS in types or NAL_PPS in types:
            self._cache_parameter_sets(data)
        with self._lock:
            if not self._fh:
                return
            if not self._writing:
                if NAL_IDR not in types:
                    return
                if self._sps and NAL_SPS not in types:
                    self._fh.write(self._sps)
                    self.bytes_written += len(self._sps)
                if self._pps and NAL_PPS not in types:
                    self._fh.write(self._pps)
                    self.bytes_written += len(self._pps)
                self._writing = True
            self._fh.write(data)
            self.bytes_written += len(data)

    def _cache_parameter_sets(self, data: bytes) -> None:
        # NAL 단위로 쪼개 SPS/PPS만 따로 보관
        idx = [i for i in range(len(data) - 3) if data[i:i + 3] == b"\x00\x00\x01"]
        for k, i in enumerate(idx):
            end = idx[k + 1] if k + 1 < len(idx) else len(data)
            start = i - 1 if i > 0 and data[i - 1] == 0 else i
            if end < len(data) and data[end - 1] == 0:
                end -= 1
            nal = data[start:end]
            t = data[i + 3] & 0x1F
            if t == NAL_SPS:
                self._sps = nal
            elif t == NAL_PPS:
                self._pps = nal


def _remux(raw: Path, mp4: Path) -> None:
    try:
        with av.open(str(raw), format="h264") as src, av.open(str(mp4), "w") as dst:
            vs = src.streams.video[0]
            if hasattr(dst, "add_stream_from_template"):   # PyAV >= 14
                out = dst.add_stream_from_template(vs)
            else:
                out = dst.add_stream(template=vs)
            # 라이브 캡처는 pts가 없으므로 프레임 순서대로 60fps 가정 타임스탬프를 붙인다.
            # 패킷 time_base는 고정값으로 두어야 mux()가 컨테이너 time_base로 환산한다
            # (헤더 기록 후 스트림 time_base가 1/15360 등으로 바뀌므로 out.time_base를 쓰면 안 됨).
            src_tb = Fraction(1, 60)
            out.time_base = src_tb
            n = 0
            for pkt in src.demux(vs):
                if pkt.dts is None and pkt.size == 0:
                    continue
                pkt.stream = out
                pkt.pts = pkt.dts = n
                pkt.duration = 1
                pkt.time_base = src_tb
                n += 1
                dst.mux(pkt)
        raw.unlink(missing_ok=True)
        log.info("녹화 저장: %s", mp4)
    except Exception as e:  # noqa: BLE001
        log.error("mp4 재포장 실패(%s). 원본 %s는 그대로 둡니다.", e, raw)
