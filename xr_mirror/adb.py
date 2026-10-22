"""adb 실행 파일 탐색과 디바이스 제어 래퍼."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Windows에서 콘솔 창이 깜빡이지 않도록 하는 subprocess 플래그
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class AdbError(RuntimeError):
    pass


@dataclass
class Device:
    serial: str
    state: str          # device | unauthorized | offline | no permissions ...
    model: str = ""
    product: str = ""
    transport: str = "" # usb | tcpip 추정

    @property
    def ready(self) -> bool:
        return self.state == "device"

    def label(self) -> str:
        name = self.model.replace("_", " ") if self.model else self.product
        base = f"{name} ({self.serial})" if name else self.serial
        if not self.ready:
            base += f"  [{self.state}]"
        return base


def _candidates() -> list[Path]:
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    out: list[Path] = []
    env = os.environ.get("XR_MIRROR_ADB")
    if env:
        out.append(Path(env))
    out.append(ROOT / "platform-tools" / exe)
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            out.append(Path(local) / "Android" / "Sdk" / "platform-tools" / exe)
        for pf in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(pf, "")
            if base:
                out.append(Path(base) / "scrcpy" / exe)
    else:
        out.append(Path.home() / "Library" / "Android" / "sdk" / "platform-tools" / exe)
        out.append(Path.home() / "Android" / "Sdk" / "platform-tools" / exe)
    found = shutil.which("adb")
    if found:
        out.append(Path(found))
    return out


def find_adb() -> Path | None:
    for p in _candidates():
        if p.is_file():
            return p
    return None


class Adb:
    def __init__(self, path: Path | str | None = None):
        p = Path(path) if path else find_adb()
        if p is not None and path and not p.is_file():
            raise AdbError(f"지정한 adb 경로가 없습니다: {p}")
        if p is None:
            raise AdbError(
                "adb를 찾을 수 없습니다. setup_env.py를 실행해 platform-tools를 받거나 "
                "XR_MIRROR_ADB 환경변수로 adb 경로를 지정하세요."
            )
        self.path = p

    # ---- 저수준 ---------------------------------------------------------
    def _base(self, serial: str | None) -> list[str]:
        cmd = [str(self.path)]
        if serial:
            cmd += ["-s", serial]
        return cmd

    def run(self, *args: str, serial: str | None = None, timeout: float = 20,
            check: bool = True) -> subprocess.CompletedProcess:
        cmd = self._base(serial) + list(args)
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                            creationflags=_NO_WINDOW, encoding="utf-8", errors="replace")
        if check and cp.returncode != 0:
            raise AdbError(f"adb {' '.join(args)} 실패 (rc={cp.returncode}): "
                           f"{(cp.stderr or cp.stdout).strip()}")
        return cp

    def popen(self, *args: str, serial: str | None = None,
              stdin=subprocess.DEVNULL) -> subprocess.Popen:
        cmd = self._base(serial) + list(args)
        return subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, bufsize=0,
                                creationflags=_NO_WINDOW)

    # ---- 고수준 ---------------------------------------------------------
    def version(self) -> str:
        cp = self.run("version", check=False)
        return (cp.stdout or cp.stderr).strip().splitlines()[0] if (cp.stdout or cp.stderr) else "?"

    def start_server(self) -> None:
        self.run("start-server", check=False, timeout=30)

    def devices(self) -> list[Device]:
        cp = self.run("devices", "-l", timeout=15)
        return parse_devices(cp.stdout)

    def shell(self, *args: str, serial: str, timeout: float = 20) -> str:
        return self.run("shell", *args, serial=serial, timeout=timeout).stdout

    def getprop(self, prop: str, serial: str) -> str:
        try:
            return self.shell("getprop", prop, serial=serial, timeout=10).strip()
        except (AdbError, subprocess.TimeoutExpired):
            return ""

    def push(self, local: Path, remote: str, serial: str) -> None:
        self.run("push", str(local), remote, serial=serial, timeout=60)

    def forward(self, local_port: int, remote: str, serial: str) -> None:
        self.run("forward", f"tcp:{local_port}", remote, serial=serial)

    def forward_remove(self, local_port: int, serial: str) -> None:
        self.run("forward", "--remove", f"tcp:{local_port}", serial=serial, check=False)

    def display_size(self, serial: str) -> tuple[int, int] | None:
        """`wm size` 결과(Physical size: WxH, Override size 우선)."""
        try:
            out = self.shell("wm", "size", serial=serial, timeout=10)
        except (AdbError, subprocess.TimeoutExpired):
            return None
        size = None
        for line in out.splitlines():
            if ":" in line and "x" in line:
                try:
                    w, h = line.split(":", 1)[1].strip().split("x")
                    size = (int(w), int(h))
                except ValueError:
                    continue
        return size


def parse_devices(text: str) -> list[Device]:
    devs: list[Device] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        if state == "no" and len(parts) >= 3 and parts[2].startswith("permissions"):
            state = "no permissions"
        d = Device(serial=serial, state=state)
        for kv in parts[2:]:
            if ":" in kv:
                k, v = kv.split(":", 1)
                if k == "model":
                    d.model = v
                elif k == "product":
                    d.product = v
                elif k == "transport_id":
                    pass
        d.transport = "tcpip" if ":" in serial else "usb"
        devs.append(d)
    return devs


STATE_HINTS = {
    "unauthorized": "헤드셋을 착용하고 'USB 디버깅을 허용하시겠습니까?' 창에서 허용을 누르세요.",
    "offline": "장치가 offline 상태입니다. 케이블을 다시 꽂거나 adb kill-server 후 재시도하세요.",
    "no permissions": "USB 권한이 없습니다. 드라이버(Windows: OEM USB 드라이버 또는 Meta Quest ADB 드라이버)를 확인하세요.",
}
