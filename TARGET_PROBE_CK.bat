@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
echo CK only: read-only target comparison. Close HACHBOB first.
py -3.11 collect_targets.py --spell ck
pause
