# SO Alert Bot startup and dashboard

## Admin dashboard and public URL

For the full bot + PostgreSQL + Redis + admin dashboard stack, set credentials
in `.env` and run `RUN_DASHBOARD.bat` or `docker compose up -d --build`.
Open `http://127.0.0.1:8081` (health: `/health`; API docs: `/api/docs`).
`0.0.0.0` is a bind address and will produce a browser `ERR_ADDRESS_INVALID`;
it is not a URL to visit. For remote access, use `RUN_NGROK_DASHBOARD.bat`
and open the HTTPS URL printed by its inspector lookup. The ngrok
service in the root Compose profile does not use Basic Auth; sign in through
the dashboard. A successful ngrok log includes `started tunnel` and an HTTPS
URL. If Chrome shows a native sign-in prompt, check that the URL came from
this project's inspector and not an older or separate tunnel.

Keep your existing `.env` when updating this package. `.env` is excluded from
the ZIP for credential safety. This project copy has no `.env.example`; a
fresh setup needs a trusted template from the original release. Rotate any
bot, database, dashboard, or ngrok secrets that were posted in chat/screenshots
before using the public tunnel.

## Bot startup and database notes

The uploaded log shows two separate missing prerequisites:

1. PostgreSQL refused the connection at `127.0.0.1:5435`. No PostgreSQL connection was established; this is not a failed password check.
2. Tesseract was not installed or discoverable, so photo timestamp OCR was unavailable.

The original startup package assumed you already had both prerequisites. This version provides them together through Docker and improves native diagnostics.

## Recommended: start PostgreSQL + OCR + bot together

1. Stop any previously running SO bot process.
2. Extract this ZIP. The main folder is `SO_Alert_Bot`. You can replace the files in `D:\Bot\SO_Alert_Bot`. Your uploaded `.env` settings are preserved in this ZIP. The old Windows virtual environment is not bundled.
3. Open Docker Desktop and wait until its engine is running.
4. Run in PowerShell:

```powershell
cd D:\Bot\SO_Alert_Bot
.\START_DOCKER_SO.bat
```

No Windows Python installation or separate Tesseract installation is required for this Docker route. The first run requires internet access to download/build the images.

The script starts PostgreSQL, waits for its health check, builds the bot image with Tesseract and English data, checks the inputs/database/OCR/token presence, then starts the bot container. A configured token still needs to be accepted by Telegram; check the logs for actual polling status.

```powershell
docker compose logs -f so-bot
```

Stop the services without removing database data:

```powershell
.\STOP_DOCKER_SO.bat
```

For later days, run `START_DOCKER_SO.bat` again. The persistent database volume is reused. Do not run `docker compose down -v`; that command would remove the database volume.

## Database behavior

The bundled service creates a **new persistent database on first use**, using DB_NAME, DB_USER and DB_PASSWORD from `.env`. It does not locate or recover records from another PostgreSQL installation. If you have an existing database you intend to retain, start that existing service/container and use the external-database option below instead.

Host/pgAdmin connection for the bundled database:

| Setting | Value |
|---|---|
| Host | 127.0.0.1 |
| Port | DB_PORT from .env, normally 5435 |
| Database | DB_NAME from .env, normally SA_Alert |
| User | DB_USER from .env |
| Password | DB_PASSWORD from .env |

Inside Docker, the bot connects to `postgres:5432`. This is the database service on the internal Compose network. The container overrides DB_HOST, DB_PORT and any DATABASE_URL so it cannot accidentally connect to a different database.

If port 5435 is already occupied, the script stops with a Docker error. It does not kill another database or change its data. Use the existing database, or explicitly choose a different free DB_PORT in .env for the new bundled service.

Changing DB_PASSWORD in .env does not reset a password already stored in an existing PostgreSQL volume. Keep the original password or change it through PostgreSQL administration.

## If you already have the intended PostgreSQL database

Start that existing Windows PostgreSQL service or Docker container. You can inspect containers with:

```powershell
docker ps -a
```

To run the bot in Docker with OCR while connecting to that existing database:

```powershell
docker compose -p so-alert-external -f compose.external.yaml up -d --build
docker compose -p so-alert-external -f compose.external.yaml logs -f so-bot
```

This option uses `host.docker.internal` and DB_PORT/DB_NAME from .env. Leave DATABASE_URL unset unless deliberately overriding the connection. Do not run this and the bundled bot concurrently.

## Native Windows route

If you prefer the original native Python bot:

```powershell
.\SETUP_SO.bat
```

Start your existing PostgreSQL, or explicitly create/start the bundled database only:

```powershell
.\START_DATABASE_SO.bat
```

Install Tesseract with English data on Windows. If installed outside the standard directory, set this in `.env`:

```dotenv
TESSERACT_CMD=C:/Program Files/Tesseract-OCR/tesseract.exe
```

Then:

```powershell
.\CHECK_SO.bat
.\RUN_SO.bat
```

`CHECK_SO.bat` now returns a failing exit code when PostgreSQL, OCR or token configuration is missing. It prints a short explanation instead of a long database traceback. It only probes the database with SELECT 1; it does not create tables. `RUN_SO.bat` checks readiness before starting.

## Other log lines

- The pip upgrade notice was not the cause of the crash.
- The two openpyxl formatting-extension warnings came from reading the supplied workbook. The importer now suppresses only those known formatting warnings during read-only extraction. Input files remain unchanged.
- `Boundary polygons: 1721` confirms the bundled `Map_KML.kml` loaded. A different number is valid only if you intentionally replace or add boundary files.
- Test_SO settings and all reporting/controller/plan/export features remain as supplied.

## Verification

The updated tests cover connection-refused diagnostics, authentication/database-name diagnostics, required OCR and English-data checks, read-only database probes, and the existing bot features. See TEST_RESULTS.md.

The Windows host and Docker Desktop cannot be run from this environment. No live Telegram messages were sent and no connection to your local database was attempted. The Docker build/download and actual Telegram login still require verification on your computer.

Implementation references: [Docker startup readiness](https://docs.docker.com/compose/how-tos/startup-order/) and [Tesseract installation](https://tesseract-ocr.github.io/tessdoc/Installation.html).
# Update master data from Telegram

As the global admin, send `/update_location_master` and reply to the bot's
prompt with `Location_All.xlsx`. For dealer points, send
`/update_dealer_master` and reply with `DEALERS STATION All Location.csv`.
Successful uploads refresh the warehouse automatically.

## Banner Report topic

Create `Banner_Report` in `Group_SO_Controll`, open that topic, and run:

```text
/register_alert_topic | Banner Report
```

The bot copies a report plus its complete photo album only when the caption has
a non-empty `Banner Type:` value. The detector is rule-based and uses no AI.

