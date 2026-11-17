@echo off
setlocal
cd /d "%~dp0"
if not exist .env (
  if exist .env.example copy .env.example .env >nul
  echo Created .env from .env.example.
  echo Fill in your private credentials in .env, then run this file again.
  pause
  exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not ready. Open Docker Desktop and wait for Engine running.
  pause
  exit /b 1
)
echo Starting the bot, PostgreSQL, Redis, and Admin Dashboard...
docker compose up -d --build
if errorlevel 1 goto :error
echo Waiting for the dashboard health check...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; 1..30 | ForEach-Object { if(-not $ok){ try { Invoke-WebRequest 'http://127.0.0.1:8081/health' -UseBasicParsing -TimeoutSec 2 | Out-Null; $ok=$true } catch { Start-Sleep -Seconds 2 } } }; if(-not $ok){ exit 1 }"
if errorlevel 1 goto :error
echo.
docker compose ps
echo.
echo Dashboard: http://127.0.0.1:8081
echo Health:    http://127.0.0.1:8081/health
echo Sign in with DASHBOARD_ADMIN_TOKEN from your private .env file.
start "" http://127.0.0.1:8081
exit /b 0
:error
echo Startup or dashboard health check failed. Diagnostics:
docker compose ps -a
docker compose logs --tail=80 dashboard
exit /b 1
