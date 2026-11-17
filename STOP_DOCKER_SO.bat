@echo off
setlocal
cd /d "%~dp0"
docker compose stop
exit /b %errorlevel%
