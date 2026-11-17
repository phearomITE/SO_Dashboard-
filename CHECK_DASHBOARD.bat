@echo off
setlocal
cd /d "%~dp0"
docker compose ps
echo.
curl.exe -f http://127.0.0.1:8081/health
echo.
docker compose logs --tail=30 dashboard
