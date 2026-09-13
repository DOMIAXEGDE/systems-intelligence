@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" -m pandr gui --session sessions\workspace.json
) else if exist ".venv\bin\pythonw.exe" (
  start "" ".venv\bin\pythonw.exe" -m pandr gui --session sessions\workspace.json
) else if exist ".venv\bin\python.exe" (
  ".venv\bin\python.exe" -m pandr gui --session sessions\workspace.json
) else (
  python -m pandr gui --session sessions\workspace.json
)
