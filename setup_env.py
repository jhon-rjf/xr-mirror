"""실행 환경 준비: platform-tools(adb)와 scrcpy-server 내려받기.

사용:  python setup_env.py [--force] [--no-adb]
실패하면 원인(예외 종류, URL)과 수동 우회 방법을 출력하고 종료 코드 1로 끝난다.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import socket
import ssl
import sys
import time
import traceback
import urllib.error
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
VENDOR_SERVER = ROOT / "vendor" / f"scrcpy-server-v{SCRCPY_VERSION}"
# dl.google.com이 막힌 망을 위한 예비 경로 (이 저장소의 GitHub Release 'tools'에 같은 zip을 올려 둠)
PT_FALLBACK = {
    "win32": "https://github.com/jhon-rjf/xr-mirror/releases/download/tools/platform-tools-latest-windows.zip",
}

RETRIES = 3


class SetupError(Exception):
    pass


def _ssl_contexts() -> list[ssl.SSLContext | None]:
    """기본 컨텍스트 먼저, 인증서 검증 실패 시 certifi 번들로 재시도."""
    ctxs: list[ssl.SSLContext | None] = [None]
    try:
        import certifi  # type: ignore
        ctxs.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:  # noqa: BLE001
        pass
    return ctxs


def download(url: str) -> bytes:
    print(f"다운로드: {url}", flush=True)
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        for ctx in _ssl_contexts():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "xr-mirror-setup"})
                with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
                    total = int(r.headers.get("Content-Length") or 0)
                    buf = io.BytesIO()
                    got = 0
                    next_mark = 0
                    while True:
                        chunk = r.read(1 << 16)
                        if not chunk:
                            break
                        buf.write(chunk)
                        got += len(chunk)
                        # 파이프/로그로 갈 때 줄이 넘치지 않도록 10% 단위로만 출력
                        if total and got * 10 // total >= next_mark:
                            print(f"  {got / 1e6:6.1f} / {total / 1e6:.1f} MB", flush=True)
                            next_mark = got * 10 // total + 1
                if total and got < total:
                    raise SetupError(f"다운로드가 중간에 끊겼습니다 ({got}/{total} bytes)")
                return buf.getvalue()
            except ssl.SSLCertVerificationError as e:
                last = e
                continue          # 다음 SSL 컨텍스트(certifi)로 재시도
            except (urllib.error.URLError, socket.timeout, ConnectionError, SetupError, OSError) as e:
                last = e
                break             # 같은 컨텍스트로 잠시 후 재시도
        if attempt < RETRIES:
            print(f"  실패({type(last).__name__}: {last}), {attempt * 3}초 후 재시도 {attempt}/{RETRIES - 1}", flush=True)
            time.sleep(attempt * 3)
    assert last is not None
    hint = _hint_for(last, url)
    raise SetupError(f"{type(last).__name__}: {last}\n{hint}")


def _hint_for(e: Exception, url: str) -> str:
    host = url.split("/")[2]
    if isinstance(e, ssl.SSLCertVerificationError):
        return ("  원인 추정: TLS 인증서 검증 실패. 회사망의 TLS 검사 프록시일 수 있습니다.\n"
                "  해결: 브라우저로 위 URL을 직접 받아 아래 '수동 설치' 위치에 두세요.")
    if isinstance(e, urllib.error.HTTPError):
        return f"  원인 추정: 서버가 HTTP {e.code}를 돌려줬습니다. 잠시 후 다시 시도하거나 수동 설치하세요."
    if isinstance(e, (urllib.error.URLError, socket.timeout, socket.gaierror, ConnectionError, OSError)):
        return (f"  원인 추정: {host} 에 연결할 수 없습니다 (DNS/방화벽/프록시).\n"
                "  확인: 브라우저로 위 URL이 열리는지 보고, 프록시를 쓰면 HTTPS_PROXY 환경변수를 설정하세요.\n"
                "  해결: 브라우저로 받은 파일을 아래 '수동 설치' 위치에 두세요.")
    return ""


def manual_help() -> str:
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    return (
        "\n수동 설치 (브라우저로 받아서 넣기)\n"
        f"  1. {PT_URL.get(sys.platform, PT_URL['linux'])}\n"
        f"     압축을 풀어 {PT_DIR / exe} 가 되게 둡니다.\n"
        f"  2. {SERVER_URL}\n"
        f"     받은 파일을 이름 그대로 {SERVER} 에 둡니다.\n"
        "  그 다음 run.bat(또는 ./install.sh)을 다시 실행하면 다운로드를 건너뜁니다.\n"
    )


def setup_platform_tools(force: bool) -> None:
    exe = PT_DIR / ("adb.exe" if sys.platform == "win32" else "adb")
    if exe.is_file() and not force:
        print(f"platform-tools 있음: {exe}")
        return
    found = shutil.which("adb")
    if found and not force:
        print(f"PATH의 adb를 사용합니다 (다운로드 생략): {found}")
        return
    urls = [PT_URL.get(sys.platform, PT_URL["linux"])]
    if sys.platform in PT_FALLBACK:
        urls.append(PT_FALLBACK[sys.platform])
    data = None
    errors = []
    for url in urls:
        try:
            data = download(url)
            break
        except SetupError as e:
            errors.append(str(e))
            print(f"  이 경로는 실패, 다음 경로 시도", flush=True)
    if data is None:
        raise SetupError("모든 다운로드 경로 실패:\n" + "\n".join(errors))
    try:
        if PT_DIR.exists():
            shutil.rmtree(PT_DIR)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(ROOT)   # zip 안에 platform-tools/ 폴더가 들어 있음
    except zipfile.BadZipFile as e:
        raise SetupError(f"받은 platform-tools zip이 손상됨: {e}") from e
    if not exe.is_file():
        raise SetupError(f"압축을 풀었지만 {exe} 가 없습니다. zip 구조가 바뀌었을 수 있습니다.")
    if sys.platform != "win32":
        for f in PT_DIR.iterdir():
            if f.is_file() and not f.suffix:
                f.chmod(0o755)
    print(f"platform-tools 설치: {exe}")


def _sha_ok(path: Path) -> bool:
    return path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == SCRCPY_SERVER_SHA256


def setup_server(force: bool) -> None:
    if _sha_ok(SERVER) and not force:
        print(f"scrcpy-server 있음: {SERVER}")
        return
    if _sha_ok(VENDOR_SERVER):
        print(f"scrcpy-server 저장소 동봉본 사용: {VENDOR_SERVER}")
        return
    if SERVER.is_file():
        print("scrcpy-server 해시 불일치, 다시 받습니다.")
    data = download(SERVER_URL)
    digest = hashlib.sha256(data).hexdigest()
    if digest != SCRCPY_SERVER_SHA256:
        raise SetupError(f"scrcpy-server SHA256 불일치 (손상 또는 변조)\n  기대: {SCRCPY_SERVER_SHA256}\n  실제: {digest}")
    SERVER.write_bytes(data)
    print(f"scrcpy-server v{SCRCPY_VERSION} 저장: {SERVER}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true", help="이미 있어도 다시 받기")
    p.add_argument("--no-adb", action="store_true", help="platform-tools는 건너뛰기")
    a = p.parse_args()
    print(f"setup_env: Python {sys.version.split()[0]} / {sys.platform} / 폴더 {ROOT}", flush=True)
    steps = []
    if not a.no_adb:
        steps.append(("platform-tools(adb)", lambda: setup_platform_tools(a.force)))
    steps.append(("scrcpy-server", lambda: setup_server(a.force)))
    failed = False
    for name, fn in steps:
        try:
            fn()
        except SetupError as e:
            failed = True
            print(f"\n[{name}] 실패: {e}", flush=True)
        except Exception as e:  # noqa: BLE001
            failed = True
            print(f"\n[{name}] 예상치 못한 오류: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
    if failed:
        print(manual_help(), flush=True)
        return 1
    print("완료. 실행: python -m xr_mirror  (또는 run.bat)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
