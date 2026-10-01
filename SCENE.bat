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
echo [HACHBOB SCENE exp6] Optional FarZ and atmospheric haze controls. Startup OFF.
echo Accepted Zoom is not modified. Fog-of-War, DLL and code patches are excluded.
echo Normal close / End attempts to restore original data. No force-kill guarantee.
py -3.11 -m zoom.scene_app
if errorlevel 1 pause
endlocal
