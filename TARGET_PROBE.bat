@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo WR + CK: read-only target comparison. Close start.bat first.
py -3.11 collect_targets.py --spell both
pause
