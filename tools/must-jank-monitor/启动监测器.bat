@echo off
setlocal
cd /d "%~dp0"
set "PATH=%~dp0platform-tools;%PATH%"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 main.py
) else (
  python main.py
)
if errorlevel 1 pause
endlocal
