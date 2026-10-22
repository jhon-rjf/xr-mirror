"""xr-mirror: Windows용 Python/ADB 기반 XR 헤드셋 USB(C to C) 화면 미러링."""

__version__ = "0.1.0"

# 함께 배포하는 scrcpy 서버 버전. 서버는 클라이언트가 넘기는 버전 문자열이
# 자기 BuildConfig.VERSION_NAME과 정확히 같지 않으면 즉시 종료하므로 바꿀 때 주의.
SCRCPY_VERSION = "5.0.1"
SCRCPY_SERVER_SHA256 = "764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536"


def find_server_jar():
    """루트(다운로드본) -> vendor(저장소 동봉본) 순으로 scrcpy-server 파일을 찾는다."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    name = f"scrcpy-server-v{SCRCPY_VERSION}"
    for cand in (root / name, root / "vendor" / name):
        if cand.is_file():
            return cand
    return root / name
