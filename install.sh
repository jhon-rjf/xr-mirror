#!/usr/bin/env bash
# xr-mirror 올인원 설치 + 실행 (macOS / Linux / Windows Git Bash)
#
#   아무것도 없는 PC에서:
#     curl -fsSL https://raw.githubusercontent.com/jhon-rjf/xr-mirror/main/install.sh | bash
#   저장소 폴더 안에서:
#     ./install.sh [xr_mirror 옵션...]
#
# 하는 일: 1) 저장소 확보  2) Python 3.10+ 확인, 없으면 설치  3) venv + 의존성
#         4) platform-tools(adb) + scrcpy-server 다운로드  5) 프로그램 실행
set -euo pipefail

REPO="jhon-rjf/xr-mirror"
PY_VER="3.12.10"
PY_MIN="(3, 10)"
INSTALL_DIR="${XR_MIRROR_DIR:-$HOME/xr-mirror}"

log()  { printf '\033[1;36m[xr-mirror]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[xr-mirror] 오류:\033[0m %s\n' "$*" >&2; exit 1; }

OS="$(uname -s)"
case "$OS" in
  MINGW*|MSYS*|CYGWIN*) PLATFORM=windows ;;
  Darwin)               PLATFORM=mac ;;
  Linux)                PLATFORM=linux ;;
  *) die "지원하지 않는 OS: $OS" ;;
esac

# ---------------------------------------------------------------- 1) 저장소
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$SCRIPT_DIR" ] && [ -d "$SCRIPT_DIR/xr_mirror" ]; then
  cd "$SCRIPT_DIR"
elif [ -d "$INSTALL_DIR/xr_mirror" ]; then
  cd "$INSTALL_DIR"
  if command -v git >/dev/null && [ -d .git ]; then git pull --ff-only -q || true; fi
else
  log "저장소 내려받기 -> $INSTALL_DIR"
  if command -v git >/dev/null; then
    git clone -q "https://github.com/$REPO.git" "$INSTALL_DIR"
  else
    tmp="$(mktemp -d)"
    curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.zip" -o "$tmp/src.zip"
    if command -v unzip >/dev/null; then
      unzip -q "$tmp/src.zip" -d "$tmp"
    else
      python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$tmp/src.zip" "$tmp" 2>/dev/null \
        || die "unzip이나 python3 중 하나가 필요합니다."
    fi
    mkdir -p "$INSTALL_DIR"
    cp -R "$tmp"/xr-mirror-main/. "$INSTALL_DIR"/
    rm -r "$tmp"
  fi
  cd "$INSTALL_DIR"
fi
log "작업 폴더: $(pwd)"

# ---------------------------------------------------------------- 2) Python
py_ok() { $1 -c "import sys; sys.exit(0 if sys.version_info >= $PY_MIN else 1)" >/dev/null 2>&1; }

find_python() {
  local c
  for c in python3.12 python3.13 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then echo "$c"; return 0; fi
  done
  if [ "$PLATFORM" = windows ] && command -v py >/dev/null 2>&1; then
    for v in 3.12 3.13 3.11 3.10; do
      if py -"$v" -c "import sys" >/dev/null 2>&1; then echo "py -$v"; return 0; fi
    done
  fi
  return 1
}

install_python() {
  log "Python 3.10 이상이 없습니다. Python $PY_VER 설치를 시작합니다."
  case "$PLATFORM" in
    mac)
      if command -v brew >/dev/null; then
        brew install python@3.12
      else
        pkg="$(mktemp -d)/python.pkg"
        curl -fSL "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-macos11.pkg" -o "$pkg"
        log "macOS 설치 프로그램을 실행합니다. 관리자 암호를 묻습니다."
        sudo installer -pkg "$pkg" -target /
        export PATH="/Library/Frameworks/Python.framework/Versions/3.12/bin:$PATH"
      fi ;;
    linux)
      if command -v apt-get >/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3 python3-venv python3-pip libgl1 libegl1 libxkbcommon0 libdbus-1-3
      elif command -v dnf >/dev/null; then
        sudo dnf install -y python3 python3-pip mesa-libGL libxkbcommon
      elif command -v pacman >/dev/null; then
        sudo pacman -Sy --noconfirm python python-pip
      else
        die "패키지 관리자를 찾지 못했습니다. Python 3.10 이상을 직접 설치한 뒤 다시 실행하세요."
      fi ;;
    windows)
      if command -v winget >/dev/null; then
        winget install -e --id Python.Python.3.12 --scope user --silent \
          --accept-package-agreements --accept-source-agreements || true
      else
        exe="$(mktemp -d)/python-installer.exe"
        curl -fSL "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-amd64.exe" -o "$exe"
        log "Python 설치 프로그램을 조용히 실행합니다 (1~2분)."
        "$exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
      fi
      # 새 PATH가 현재 셸에 아직 반영되지 않으므로 기본 설치 위치를 직접 추가
      export PATH="$LOCALAPPDATA/Programs/Python/Python312:$LOCALAPPDATA/Programs/Python/Python312/Scripts:$LOCALAPPDATA/Programs/Python/Launcher:$PATH" ;;
  esac
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
  install_python
  PY="$(find_python || true)"
  [ -n "$PY" ] || die "Python 설치 후에도 찾지 못했습니다. 터미널을 새로 열고 다시 실행하세요."
fi
log "Python: $($PY --version 2>&1) ($PY)"

# ---------------------------------------------------------------- 3) venv
if [ "$PLATFORM" = windows ]; then VPY=".venv/Scripts/python.exe"; else VPY=".venv/bin/python"; fi
if [ ! -x "$VPY" ]; then
  log "가상환경 생성"
  if ! $PY -m venv .venv; then
    if [ "$PLATFORM" = linux ] && command -v apt-get >/dev/null; then
      sudo apt-get install -y -qq python3-venv && $PY -m venv .venv
    else
      die "venv 생성 실패"
    fi
  fi
fi
if [ ! -f .venv/.deps-ok ] || [ requirements.txt -nt .venv/.deps-ok ]; then
  log "의존성 설치 (av, numpy, PySide6)"
  "$VPY" -m pip install -q --upgrade pip
  "$VPY" -m pip install -q --retries 10 --timeout 60 -r requirements.txt
  touch .venv/.deps-ok
fi

# ---------------------------------------------------------------- 4) adb, 서버
if [ "$PLATFORM" = windows ]; then ADB="platform-tools/adb.exe"; else ADB="platform-tools/adb"; fi
if [ ! -f "$ADB" ] && ! command -v adb >/dev/null; then
  "$VPY" setup_env.py
else
  "$VPY" setup_env.py --no-adb
fi

# ---------------------------------------------------------------- 5) 실행
log "준비 완료. 프로그램을 실행합니다. (다음부터는 ./install.sh 또는 run.bat)"
exec "$VPY" -m xr_mirror "$@"
