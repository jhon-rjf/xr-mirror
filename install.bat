@echo off
rem 더블클릭용: install.ps1을 실행 정책 제한 없이 실행 (Python 설치부터 실행까지 전부)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 pause
