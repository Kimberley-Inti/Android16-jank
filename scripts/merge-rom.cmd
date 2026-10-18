@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 merge_rom.py
) else (
  python merge_rom.py
)
pause
endlocal
