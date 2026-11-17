@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
if not exist ".venv\Scripts\python.exe" (
  echo Run SETUP_SO.bat first, or use START_DOCKER_SO.bat.
  goto fail
)
".venv\Scripts\python.exe" -m app.check --db --require-ocr --require-token
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m app.main
if errorlevel 1 goto fail
exit /b 0
:fail
echo Native startup incomplete. Read the diagnostic above.
pause
exit /b 1
