@echo off
cd /d "%~dp0"
if exist ".controller-python" (
  set /p CONTROLLER_PYTHON=<".controller-python"
  goto configured
)
if exist ".venv\Scripts\python.exe" (
  set "CONTROLLER_PYTHON=%~dp0.venv\Scripts\python.exe"
  goto configured
)
if exist "..\PandR_v3\.venv\Scripts\python.exe" (
  set "CONTROLLER_PYTHON=%~dp0..\PandR_v3\.venv\Scripts\python.exe"
  goto configured
)
where py >nul 2>nul
if errorlevel 1 (
  python -m framelm studio
) else (
  py -3 -m framelm studio
)
if errorlevel 1 pause
exit /b
:configured
"%CONTROLLER_PYTHON%" -m framelm studio
if errorlevel 1 pause
