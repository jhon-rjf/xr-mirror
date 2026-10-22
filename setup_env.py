"""Windows(및 macOS/Linux) 실행 환경 준비: platform-tools(adb)와 scrcpy-server 내려받기.

사용:  python setup_env.py [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from xr_mirror import SCRCPY_SERVER_SHA256, SCRCPY_VERSION  # noqa: E402

ROOT = Path(__file__).resolve().parent
PT_DIR = ROOT / "platform-tools"
SERVER = ROOT / f"scrcpy-server-v{SCRCPY_VERSION}"

PT_URL = {
    "win32": "https://dl.google.com/android/repository/platform-tools-latest-windows.zip",
    "darwin": "https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",
    "linux": "https://dl.google.com/android/repository/platform-tools-latest-linux.zip",
}
SERVER_URL = (f"https://github.com/Genymobile/scrcpy/releases/download/"
              f"v{SCRCPY_VERSION}/scrcpy-server-v{SCRCPY_VERSION}")


def download(url: str) -> bytes:
    print(f"다운로드: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "xr-mirror-setup"})
    with urllib.request.urlopen(req, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            buf.write(chunk)
            got += len(chunk)
            if total:
                print(f"\r  {got / 1e6:6.1f} / {total / 1e6:.1f} MB", end="", flush=True)
        print()
    return buf.getvalue()


def setup_platform_tools(force: bool) -> None:
    exe = PT_DIR / ("adb.exe" if sys.platform == "win32" else "adb")
    if exe.is_file() and not force:
        print(f"platform-tools 있음: {exe}")
        return
    url = PT_URL.get(sys.platform, PT_URL["linux"])
    data = download(url)
    if PT_DIR.exists():
        shutil.rmtree(PT_DIR)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(ROOT)   # zip 안에 platform-tools/ 폴더가 들어 있음
    if sys.platform != "win32":
        for f in PT_DIR.iterdir():
            if f.is_file() and not f.suffix:
                f.chmod(0o755)
    print(f"platform-tools 설치: {exe}")


def setup_server(force: bool) -> None:
    if SERVER.is_file() and not force:
        if hashlib.sha256(SERVER.read_bytes()).hexdigest() == SCRCPY_SERVER_SHA256:
            print(f"scrcpy-server 있음: {SERVER}")
            return
        print("scrcpy-server 해시 불일치, 다시 받습니다.")
    data = download(SERVER_URL)
    digest = hashlib.sha256(data).hexdigest()
    if digest != SCRCPY_SERVER_SHA256:
        raise SystemExit(f"scrcpy-server SHA256 불일치!\n  기대: {SCRCPY_SERVER_SHA256}\n  실제: {digest}")
    SERVER.write_bytes(data)
    print(f"scrcpy-server v{SCRCPY_VERSION} 저장: {SERVER}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="이미 있어도 다시 받기")
    p.add_argument("--no-adb", action="store_true", help="platform-tools는 건너뛰기")
    a = p.parse_args()
    if not a.no_adb:
        setup_platform_tools(a.force)
    setup_server(a.force)
    print("완료. 실행: python -m xr_mirror  (또는 run.bat)")


if __name__ == "__main__":
    main()
