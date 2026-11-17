@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
  py -3.11 -m venv .venv
  if errorlevel 1 goto fail
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
if not exist .env copy .env.example .env
".venv\Scripts\python.exe" -m app.check
if errorlevel 1 goto fail
echo Python dependencies installed. This does not start PostgreSQL or install native OCR.
echo Recommended: open Docker Desktop, then run START_DOCKER_SO.bat.
echo Native Windows: start PostgreSQL, install Tesseract, then run CHECK_SO.bat.
pause
exit /b 0
:fail
echo Setup needs attention. Read the error above.
pause
exit /b 1
