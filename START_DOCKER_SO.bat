@echo off
setlocal
cd /d "%~dp0"
if not exist .env (
  copy .env.example .env >nul
  echo Set TELEGRAM_BOT_TOKEN and database settings in .env, then run again.
  goto fail
)
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker Desktop is not running. Open Docker Desktop and wait until it is ready.
  goto fail
)
docker compose version >nul 2>&1
if errorlevel 1 (
  echo Docker Compose is missing. Update Docker Desktop.
  goto fail
)
echo Starting bundled PostgreSQL. Existing Docker data is preserved.
docker compose up -d --wait --wait-timeout 90 postgres
if errorlevel 1 goto dbfail
echo Building bot with Tesseract OCR included.
docker compose build so-bot
if errorlevel 1 goto fail
docker compose run --rm --no-deps so-bot python -m app.check --db --require-ocr --require-token
if errorlevel 1 goto fail
docker compose up -d so-bot
if errorlevel 1 goto fail
echo Bot container started. Check the log below for Telegram connection status.
docker compose logs --tail 40 so-bot
echo To follow logs: docker compose logs -f so-bot
exit /b 0
:dbfail
echo PostgreSQL did not become ready. Check the Docker error above.
echo If port 5435 is already occupied, use the existing database instead. See START_HERE.md.
echo Do not delete database volumes to fix startup errors.
:fail
echo Startup incomplete. Correct the message above and run this script again.
pause
exit /b 1
