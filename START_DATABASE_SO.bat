@echo off
setlocal
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Open Docker Desktop first, then run this script again.
  pause
  exit /b 1
)
echo Starting the bundled PostgreSQL database only.
docker compose up -d --wait --wait-timeout 90 postgres
if errorlevel 1 (
  echo Database startup failed. Check the Docker error above, including port conflicts.
  pause
  exit /b 1
)
echo Database is ready. Run CHECK_SO.bat to check your native Windows connection.
exit /b 0
