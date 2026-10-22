"""로그 파일, 네이티브 크래시 덤프, 처리되지 않은 예외 기록.

logs/xr-mirror.log : 일반 로그 (2MB x 3 회전)
logs/crash.log     : faulthandler가 남기는 네이티브 크래시(접근 위반 등) 스택
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import platform
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "xr-mirror.log"
CRASH_FILE = LOG_DIR / "crash.log"

_crash_fh = None   # faulthandler가 쓰는 파일 객체는 프로세스가 끝날 때까지 열어 둬야 한다


def setup_logging(verbose: bool = False) -> Path:
    global _crash_fh
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s")

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    fileh = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3,
                                                 encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(fileh)

    _crash_fh = open(CRASH_FILE, "a", encoding="utf-8")
    _crash_fh.write(f"\n===== 시작 {platform.platform()} python {sys.version.split()[0]} =====\n")
    _crash_fh.flush()
    faulthandler.enable(file=_crash_fh, all_threads=True)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("crash").critical("처리되지 않은 예외", exc_info=(exc_type, exc, tb))
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args):
        logging.getLogger("crash").critical("스레드 %s 예외", args.thread.name if args.thread else "?",
                                            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook

    log = logging.getLogger(__name__)
    log.info("로그 파일: %s", LOG_FILE)
    log.info("플랫폼: %s, Python %s", platform.platform(), sys.version.split()[0])
    return LOG_FILE
