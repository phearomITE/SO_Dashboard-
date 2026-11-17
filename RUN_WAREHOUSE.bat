@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Python environment missing. Run SETUP_SO.bat first.
  exit /b 1
)
set ACTION=%~1
if "%ACTION%"=="" set ACTION=refresh
".venv\Scripts\python.exe" -m app.warehouse_cli %ACTION%
exit /b %ERRORLEVEL%
