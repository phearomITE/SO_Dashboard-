@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  echo Run SETUP_SO.bat first, or use START_DOCKER_SO.bat.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.check --db --require-ocr --require-token
set "SO_CHECK_RESULT=%errorlevel%"
pause
exit /b %SO_CHECK_RESULT%
