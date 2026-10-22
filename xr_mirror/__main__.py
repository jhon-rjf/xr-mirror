"""python -m xr_mirror [옵션]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def parse_bitrate(text: str) -> int:
    t = text.strip().upper().replace("BPS", "")
    mult = 1
    if t.endswith("M"):
        mult, t = 1_000_000, t[:-1]
    elif t.endswith("K"):
        mult, t = 1_000, t[:-1]
    return int(float(t) * mult)


def main(argv: list[str] | None = None) -> int:
    from .ui import Settings, run_app

    p = argparse.ArgumentParser(prog="xr_mirror",
                                description="ADB 기반 XR 헤드셋 USB 화면 미러링")
    p.add_argument("-s", "--serial", help="adb 기기 시리얼 (생략 시 UI에서 선택)")
    p.add_argument("-b", "--backend", choices=["scrcpy", "screenrecord", "demo"], default="scrcpy")
    p.add_argument("-m", "--max-size", type=int, default=1920, help="긴 변 최대 픽셀, 0=원본")
    p.add_argument("--bitrate", type=parse_bitrate, default="8M", help="예: 8M, 12M")
    p.add_argument("--max-fps", type=int, default=60)
    p.add_argument("--crop", help="full | left | right | W:H:X:Y | 프리셋 이름")
    p.add_argument("--display-id", type=int, default=0)
    p.add_argument("--keyframe-interval", type=int, default=5, help="scrcpy I-프레임 간격(초)")
    p.add_argument("--fullscreen", action="store_true")
    p.add_argument("--always-on-top", action="store_true")
    p.add_argument("--adb", help="adb 실행 파일 경로")
    p.add_argument("--server", type=Path, help="scrcpy-server 파일 경로")
    p.add_argument("--out-dir", type=Path, help="스크린샷/녹화 저장 폴더")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    s = Settings(serial=a.serial, backend=a.backend, max_size=a.max_size, bit_rate=a.bitrate,
                 max_fps=a.max_fps, crop=a.crop, fullscreen=a.fullscreen,
                 always_on_top=a.always_on_top, adb_path=a.adb, display_id=a.display_id,
                 keyframe_interval=a.keyframe_interval)
    if a.server:
        s.server_jar = a.server
    if a.out_dir:
        s.out_dir = a.out_dir
    return run_app(s)


if __name__ == "__main__":
    sys.exit(main())
