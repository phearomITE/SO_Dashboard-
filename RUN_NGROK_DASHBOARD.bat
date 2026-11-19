@echo off
setlocal
cd /d "%~dp0"
title SO Alert Bot - ngrok Dashboard

echo ================================================================
echo           SO Alert Bot - ngrok Dashboard
echo ================================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0CHECK_NGROK_CONFIG.ps1"
if errorlevel 1 goto fail

where docker >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker CLI not found. Install and start Docker Desktop.
  goto fail
)
docker info >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Docker Desktop is not running.
  goto fail
)

echo [1/2] Starting PostgreSQL, Redis, Bot, Dashboard, ngrok...
REM --profile public activates the ngrok container; --remove-orphans clears stale services.
docker compose --profile public up -d --build --remove-orphans postgres redis so-bot dashboard ngrok
if errorlevel 1 goto fail

echo [2/2] Reading ngrok HTTPS public URL...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0WAIT_NGROK_TUNNEL.ps1"
if errorlevel 1 goto fail

echo.
echo Dashboard and ngrok tunnel started. Sign in using DASHBOARD_ADMIN_TOKEN.
endlocal
exit /b 0

:fail
echo.
echo [ERROR] Startup did not complete. Check Docker/ngrok logs above.
endlocal
exit /b 1
