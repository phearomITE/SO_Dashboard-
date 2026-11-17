# Production Admin Dashboard

This dashboard is built into the SO Alert Bot image and uses the same PostgreSQL database. Redis is an expendable cache and login throttle; no business data is stored only in Redis.

## Windows PowerShell quick start

From the project directory:

```powershell
if (-not (Test-Path .env)) { throw '.env is missing and this project copy has no .env.example; restore a trusted project template first.' }
notepad .env
```

Set fresh values for `TELEGRAM_BOT_TOKEN`, `DB_PASSWORD`, `NGROK_AUTHTOKEN`, and `DASHBOARD_ADMIN_TOKEN`. Use a dashboard token of at least 32 random characters. The root Compose profile relies on dashboard sign-in and does not add ngrok Basic Auth. Credentials pasted into chat must be revoked/rotated before any public exposure. Never send or commit `.env`.

If PostgreSQL has already initialized its volume and you are changing its password, first update the role inside PostgreSQL (do not delete the volume):

```powershell
docker compose exec postgres psql -U postgres -d SA_Alert
```

At the `psql` prompt run `\password postgres`, follow the hidden prompts, then `\q`. Set the same value as `DB_PASSWORD` in `.env`.

Build and start the bot, database, Redis, and dashboard:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs --tail 80 so-bot dashboard
```

Open `http://127.0.0.1:8081` and sign in with `DASHBOARD_ADMIN_TOKEN`. The dashboard APIs are documented at `http://127.0.0.1:8081/api/docs`. The write APIs require an expiring bearer session issued after login. Do not use `0.0.0.0:8001` in a browser: it is the container bind address, not the Windows host URL.

To start the optional public tunnel:

```powershell
docker compose --profile public up -d --build ngrok
docker compose ps -a ngrok
docker compose logs --tail 80 ngrok
(Invoke-RestMethod http://127.0.0.1:4040/api/tunnels).tunnels | Where-Object proto -eq 'https' | Select-Object public_url
```

The ngrok CLI currently may print a Basic Auth deprecation warning. This warning is not a failure if the logs also show `started tunnel` and an HTTPS URL. Verify that the inspector API returns an HTTPS tunnel. Visiting that URL requires ngrok Basic Auth and then dashboard sign-in. The inspector is exposed only to localhost; PostgreSQL and Redis are not published through the tunnel.

Roster, plan upload, performance, AL corrections, dealer-origin updates, and global bot/question settings use PostgreSQL-backed APIs and are audited. AL supports full-day, morning (08:00–12:00), and afternoon (13:00–17:00) leave. Dashboard leave intervals are written to the same table the bot checks and suppress only overlapping missing-report windows. Future dates without a saved leave show as Scheduled, not AL. Master Sale displays the latest valid Telegram reporting group; the bot reconciles assignment from valid reports, and administrators can apply the latest observed group from the master table. Plan upload accepts a validated `.xlsx` up to 20 MB and preserves the prior active plan on failure.

Before relying on this for business operations, configure scheduled database backups and verify restore procedures. This package uses one shared dashboard admin secret; for multiple administrators, deploy behind company SSO/VPN and add individual roles.
