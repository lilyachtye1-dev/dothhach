@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
tasklist /FI "IMAGENAME eq esp_overlay.exe" /NH 2>nul | find /I "esp_overlay.exe" >nul
if not errorlevel 1 (
  echo Close HACHBOB with END and close its feed console. Leave Dota running.
  pause
  exit /b 1
)
echo Last Hit patch check: READ ONLY. No casts, clicks, patching or approval changes.
echo Leave Dota open with a live hero. No attacks, purchases or recording required.
py -3.11 last_hit_patch_probe.py
set "RESULT=%ERRORLEVEL%"
echo.
if exist "last_hit_patch_report.json" (
  echo If "Report saved:" appeared above, send last_hit_patch_report.json from this folder.
  echo Otherwise send the console error, not an old JSON.
) else (
  echo Report not created. Copy the error shown above.
)
pause
exit /b %RESULT%
