"""Single-container cloud entrypoint running both the FastAPI dashboard and Telegram bot."""
import logging
import os
import signal
import subprocess
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("cloud_entry")


def _truthy(value: str) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    port = str(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or "8001").strip()
    run_bot = _truthy(os.getenv("RUN_BOT_IN_WEB", "true"))
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()

    web_cmd = [
        sys.executable, "-m", "uvicorn", "app.dashboard:app",
        "--host", "0.0.0.0", "--port", port, "--workers", "1",
    ]
    log.info("Starting dashboard HTTP server on 0.0.0.0:%s", port)
    web_proc = subprocess.Popen(web_cmd)
    bot_proc = None
    bot_backoff = 5
    next_bot_start = time.monotonic() + 2.0
    stopping = False

    def _shutdown(signum, _frame):
        nonlocal stopping
        stopping = True
        log.info("Received signal %s; shutting down dashboard and bot", signum)
        for proc in (bot_proc, web_proc):
            if proc and proc.poll() is None:
                proc.terminate()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    if run_bot and not token:
        log.warning("TELEGRAM_BOT_TOKEN is not set; running dashboard only.")
        run_bot = False

    try:
        while not stopping:
            web_code = web_proc.poll()
            if web_code is not None:
                log.error("Dashboard web server exited with code %s", web_code)
                if bot_proc and bot_proc.poll() is None:
                    bot_proc.terminate()
                    bot_proc.wait(timeout=10)
                return web_code

            if run_bot:
                if bot_proc is None:
                    if time.monotonic() >= next_bot_start:
                        log.info("Starting Telegram bot subprocess (app.main)")
                        bot_proc = subprocess.Popen([sys.executable, "-m", "app.main"])
                        bot_backoff = 5
                else:
                    bot_code = bot_proc.poll()
                    if bot_code is not None:
                        log.warning("Telegram bot exited with code %s; restarting in %ss", bot_code, bot_backoff)
                        bot_proc = None
                        next_bot_start = time.monotonic() + bot_backoff
                        bot_backoff = min(bot_backoff * 2, 60)

            time.sleep(1.0)
    finally:
        for proc in (bot_proc, web_proc):
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
