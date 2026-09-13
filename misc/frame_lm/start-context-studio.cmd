@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  python -m framelm studio
) else (
  py -3 -m framelm studio
)
if errorlevel 1 pause
