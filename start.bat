@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"
if exist ".patch_pending" (
  echo Incomplete patch transaction. Use patch_update.py --rollback with the printed backup.
  pause
  exit /b 1
)
tasklist /FI "IMAGENAME eq esp_overlay.exe" /NH 2>nul | find /I "esp_overlay.exe" >nul
if not errorlevel 1 (
  echo Close the previous overlay with END and close its feed first.
  pause
  exit /b 1
)
py -3.11 -c "import pymem; import struct; assert struct.calcsize('P') == 8, 'Use 64-bit Python'"
if errorlevel 1 (
  echo Requires 64-bit Python 3.11 and pymem.
  echo Install dependency: py -3.11 -m pip install -r requirements.txt
  pause
  exit /b 1
)
py -3.11 patch_bundle.py --apply-staged
if errorlevel 1 (
  echo Staged patch could not be safely applied. Review the printed error/backup and do not start the feed.
  pause
  exit /b 1
)
if exist ".patch_pending" (
  echo Patch transaction is incomplete. Use patch_update.py --rollback with the printed backup.
  pause
  exit /b 1
)
py -3.11 ks_core.py
if errorlevel 1 (
  echo Invalid ks_profiles.json. No input will be sent.
  pause
  exit /b 1
)
call build_overlay.bat
if errorlevel 1 (
  pause
  exit /b 1
)
echo [HACHBOB KS5] Insert = menu. Killsteal is controlled only by the program button. F6 and Esc do not toggle it. Quickcast on key-down.
echo [Last Hit alpha7] Short mouse priority for held LMB/RMB; physical release respected. Mouse4 hold or toggle. Dota auto-attack: your choice. Enable right-click allies ATTACK for denies.
echo [Last Hit DAMAGE1] Live Quell bonus before armor; no deny bonus. HP-loss evidence, not exact incoming projectile attribution.
start "HACHBOB" "%~dp0esp_overlay.exe"
py -3.11 esp_feed.py %*
echo Feed stopped. Close the overlay with END.
pause
