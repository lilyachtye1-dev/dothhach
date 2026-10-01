@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"
py -3.11 -c "import struct,tkinter; assert struct.calcsize('P') == 8, '64-bit Python required'"
if errorlevel 1 (
  echo Requires 64-bit Python 3.11 with tkinter. No extra pip packages needed.
  pause
  exit /b 1
)
echo [HACHBOB ZOOM exp4] Automatic-mode fix; build-pinned camera profile. No fog, weather, DLL or code patches.
echo Start Dota and enter a hero demo. Enable Zoom explicitly in its own window.
echo End or normal window close attempts to restore the original distance.
echo Force-closing the process cannot guarantee restoration. See zoom\zoom.log for errors.
py -3.11 -m zoom.app
if errorlevel 1 pause
endlocal
