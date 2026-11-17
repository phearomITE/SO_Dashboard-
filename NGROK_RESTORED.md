# ngrok-only startup restored — November 13, 2026

Use only `RUN_NGROK_DASHBOARD.bat`. It validates `.env`, starts the Windows-hosted Ollama/Hermes process when available, runs `docker compose --profile public up -d --build --remove-orphans postgres redis so-bot dashboard ngrok`, checks Hermes connectivity, and waits for the actual HTTPS ngrok URL from the inspector at `127.0.0.1:4040`. The URL is saved in `logs/ngrok_public_url.txt`.

Cloudflare startup scripts, binary and URL leftovers were removed; dashboard tunnel status continues to query the ngrok inspector. Existing database, Telegram, dashboard credentials and Hermes configuration are preserved; rotate tokens if they have been exposed. An ngrok bandwidth-limit error (ERR_NGROK_725) cannot be fixed with code.

Run with Docker Desktop active. Check `docker compose --profile public logs --tail 80 ngrok` if tunnel creation fails. This version was statically checked, but Windows/Docker/ngrok live execution has not been verified here.
