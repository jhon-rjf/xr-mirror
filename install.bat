@echo off
rem Double-click installer: runs install.ps1 (installs Python if needed, then everything else).
rem ASCII only: cmd.exe reads batch files in the OEM code page.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 pause
