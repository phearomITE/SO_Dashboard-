# Hermes reliability update — 2026-11-12

The existing `RUN_NGROK_DASHBOARD.bat` remains the **only** launcher. It starts Ollama on Windows, Docker services and ngrok Tunnel. Do not share your `.env` or ngrok token.

## What changed

1. Hermes receives meaningful Khmer and English written explanations immediately through `/api/generate`; evidence-only outlet forms and GPS messages are rejected without inference.
2. Failed requests are retried once. Errors are stored as `ai_status=unavailable` and `ai_error` instead of silently appearing as successful AI classifications.
3. A new admin endpoint `GET /api/v1/admin/hermes/status` shows model availability and historical reprocessing status.
4. A new admin endpoint `POST /api/v1/admin/reasons/reprocess?day=YYYY-MM-DD` reprocesses up to 100 historical pending replies in the background; Employee Route triggers this once when it sees pending replies, then refreshes.
5. `AUTO_PULL_HERMES=1` downloads the model when missing (internet and disk space required). `CHECK_HERMES_DOCKER.ps1` checks that the bot container can reach Ollama on the Windows host.

## What the launcher cannot guarantee

- It cannot install GPU drivers, grant Windows Firewall permission, or ensure a compatible GPU has enough memory.
- An Ollama service *already running* and listening only on 127.0.0.1 may not be reachable from Docker even though Windows can use it. Stop that service and relaunch it with `OLLAMA_HOST=0.0.0.0:11434` using Windows PowerShell before restarting the dashboard. Restrict access to this port in Windows Firewall; never publish port 11434 to the internet.
- The full Docker + Telegram + GPU environment cannot be tested in this build environment.

## Verify on the machine

Run `RUN_NGROK_DASHBOARD.bat`. Confirm no `[Hermes] ERROR` messages. If there are, correct those issues before expecting AI classifications. `ollama ps` displays whether GPU layers are offloaded. You can also review the admin API status when authenticated.

If the employee's reason remains pending, check Hermes status and reprocessing failures. Ambiguous text must not be invented into a reason simply to remove Needs review.
