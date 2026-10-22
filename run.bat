@echo off
rem xr-mirror daily launcher. ASCII only: cmd.exe reads this file in the OEM code page,
rem so Korean text here would be garbled. Korean output comes from the Python program.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

set "VPY=%~dp0.venv\Scripts\python.exe"

rem First run (or broken venv): hand everything to the installer, which also installs Python.
if not exist "%VPY%" (
    echo [xr-mirror] No virtual environment yet. Running installer...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
    set "RC=%errorlevel%"
    if not "%RC%"=="0" pause
    exit /b %RC%
)

if not exist "%~dp0.venv\.deps-ok" (
    echo [xr-mirror] Installing dependencies...
    "%VPY%" -m pip install --retries 10 --timeout 60 -r "%~dp0requirements.txt" || (pause & exit /b 1)
    echo ok> "%~dp0.venv\.deps-ok"
)

rem adb and scrcpy-server: downloads only what is missing (vendor copy of the server is used if present)
"%VPY%" "%~dp0setup_env.py" || (pause & exit /b 1)

"%VPY%" -m xr_mirror %*
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo [xr-mirror] exited with code %RC%
    echo [xr-mirror] logs: "%~dp0logs\xr-mirror.log"  and  "%~dp0logs\crash.log"
    pause
)
exit /b %RC%
