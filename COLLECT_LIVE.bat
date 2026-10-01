@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
py -3.11 -c "import pymem; import struct; assert struct.calcsize('P') == 8, 'Use 64-bit Python'"
if errorlevel 1 (
  echo Requires 64-bit Python 3.11 and pymem, just like start.bat.
  pause
  exit /b 1
)
echo Read-only local-hero probe. No automatic casts. Start Dota Demo first.
py -3.11 collect_live.py
pause
