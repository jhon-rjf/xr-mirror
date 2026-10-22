@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [xr-mirror] 가상환경 생성 중...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Python 3.10 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요.
        pause & exit /b 1
    )
)
set PY=.venv\Scripts\python.exe

if not exist ".venv\.deps-ok" (
    echo [xr-mirror] 의존성 설치 중...
    "%PY%" -m pip install --upgrade pip >nul
    "%PY%" -m pip install --retries 10 --timeout 60 -r requirements.txt || (pause & exit /b 1)
    echo ok> ".venv\.deps-ok"
)

if not exist "platform-tools\adb.exe" "%PY%" setup_env.py
if not exist "scrcpy-server-v5.0.1" "%PY%" setup_env.py --no-adb

"%PY%" -m xr_mirror %*
if errorlevel 1 pause
