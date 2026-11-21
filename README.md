# SO Alert Bot — operations and production admin dashboard

## Admin dashboard quick start (Docker Desktop)

The bundled production dashboard is served by FastAPI on container port `8001`
and published locally at `http://127.0.0.1:8081` by default. Do not browse to
`0.0.0.0`; that is only the address the server binds to inside its container.
On Windows, configure `.env` and double-click `RUN_DASHBOARD.bat`, or run:

```powershell
if (-not (Test-Path .env)) { throw '.env is missing and this project copy has no .env.example; restore a trusted project template first.' }
notepad .env
docker compose up -d --build
Start-Process http://127.0.0.1:8081
```

Sign in with `DASHBOARD_ADMIN_TOKEN` from the private `.env`. Check
`http://127.0.0.1:8081/health`; API documentation is at
`http://127.0.0.1:8081/api/docs`. To expose it through ngrok, use
`RUN_NGROK_DASHBOARD.bat`; the script prints the actual HTTPS URL when the
tunnel is ready. The root Compose profile does not enable ngrok Basic Auth;
sign in with the dashboard admin token. If Chrome shows a native sign-in
prompt, verify that you opened the URL returned by this project's inspector
and not an older or separate tunnel. Use `127.0.0.1:8081` locally, never
`0.0.0.0:8001`.

The downloadable source package intentionally excludes `.env` because it
contains private Telegram, database, dashboard, and tunnel credentials. Keep
your existing `.env` when updating code. This project copy also has no
`.env.example`; for a fresh setup, restore a trusted template before starting
instead of reconstructing credentials from logs or chat.

**Security:** credentials posted in chat or screenshots should be revoked and
rotated before public use. In particular rotate the Telegram bot token,
ngrok authtoken, database password, and dashboard admin token.

---

## Telegram operations and warehouse

## Telegram operations and warehouse

The production Compose stack contains PostgreSQL and the Telegram bot only.
Streamlit and the dashboard/ngrok services were removed. Check-in collection,
alerts, exports, OCR, routing, and Bronze–Silver–Gold processing remain active.

## Runtime alert interval

Global admins can change the missing-report window without rebuilding or restarting:

```text
/change_ALERT_INTERVAL_MINUTES_15
/change_ALERT_INTERVAL_MINUTES_20
/change_ALERT_INTERVAL_MINUTES_30
/change_alert_interval | 25
/alert_interval
```

Valid values are 5 through 60 minutes in steps of 5. The selection is persisted
in PostgreSQL and starts from the time the command is accepted, so changing the
interval never creates retroactive missing-report alerts.

## Global question controls

Run these commands in the private chat with the bot as a global admin:

```text
/questions | off_Wrong Plan
/questions | on_Wrong Plan
/questions | off_Over 10Km
/questions | on_Over 10Km
/questions | off_Reason Acknowledgement
/questions | on_Reason Acknowledgement
/questions | off
/questions | on
/questions | status
```

The settings apply to every registered group and survive restarts. They
suppress only the selected user-facing questions. Operational alerts, reason
storage, location analysis, warehouse refreshes, and Power BI remain active.
Missing-report follow-up questions intentionally remain ON.

Keep the existing `.env` when updating this project. This copy has no
`.env.example`; a fresh setup needs a trusted template from the original
release. Never commit or share `.env`.

**Start with START_HERE.md for the connection-refused / missing-OCR fix.** The recommended command is `START_DOCKER_SO.bat` with Docker Desktop running. This starts the bundled database and the OCR-equipped bot. The native Windows instructions below require an existing reachable PostgreSQL server and installed Tesseract.

A separate SO bot following the supplied SA project's form/photo + GPS reporting workflow. It uses the supplied List_SO.xlsx and Plan_SO.xlsx. Its `so_*_v1` PostgreSQL tables are separate from the SA tables. It does not migrate old SA records or send anything until you start it.

The bundled `.env` is configured for production group registration with `TEST_MODE=false`. In production, `PURGE_TEST_DATA_ON_STARTUP=true` removes legacy `Test_SO`, known test chat `-1004303041907`, and R9 operational records before the warehouse refresh. Gold BI views also exclude them permanently. For a backup-first manual cleanup, run `CLEAN_TEST_DATA.ps1 -ConfirmCleanup`; it creates a PostgreSQL backup, deletes test data, rebuilds the warehouse, and then Power BI must be refreshed.

The project includes a small Bronze–Silver–Gold data-engineering layer for Power BI. It imports `data/Location_All.xlsx`, incrementally cleans bot visits, records data-quality exceptions and reconciles source/target counts. See `DATA_ENGINEERING.md`.

Successful plan, roster and location uploads now trigger an immediate serialized warehouse refresh. User/report changes also refresh automatically, while the periodic job remains a safety net for 65-group operation. If Power BI shows a cyclic-reference error, follow `POWER_BI_REFRESH_FIX.md`; that error is in the `.pbix` query dependency and does not mean `/update_plan` failed.

## Start on Windows

1. Extract the ZIP to `D:\Bot\SO_Alert_Bot` (the folder containing RUN_SO.bat).
2. Install Python 3.11. PostgreSQL must already be running at `127.0.0.1:5435`, database `SA_Alert`, user `postgres`.
3. Set `TELEGRAM_BOT_TOKEN` in `.env`. The token supplied in chat is intentionally not bundled; revoke it in BotFather and use its replacement. The provided local database settings are already filled in.
4. Run:

```powershell
cd D:\Bot\SO_Alert_Bot
.\SETUP_SO.bat
.\CHECK_SO.bat
.\RUN_SO.bat
```

No PowerShell activation or execution-policy change is needed. `CHECK_SO.bat` checks PostgreSQL, roster, plan and OCR without sending Telegram messages.

OCR needs the Tesseract executable and its English language data. Windows: install Tesseract at `C:\Program Files\Tesseract-OCR\tesseract.exe`, or set `TESSERACT_CMD` to its full path. Installation information: https://tesseract-ocr.github.io/tessdoc/Installation.html . Missing OCR is reported by the check script and yields Review records, rather than crashing report ingestion.

Alternative using Docker Desktop (includes Tesseract automatically):

```powershell
docker compose up -d --build
docker compose logs -f so-bot
```

The default compose file now includes a persistent PostgreSQL service. It creates a new database on first use and reuses that volume on later runs. To connect to an existing PostgreSQL instead, use `compose.external.yaml` as described in START_HERE.md. Run only one instance with this SO token; do not simultaneously run the Windows and Docker bot. An advisory database lock prevents two SO instances sharing this database.

For production across 65 groups, the bot must be added separately to every Telegram group and promoted to administrator. A Telegram bot cannot join a group by itself. Test `/myid` before `/setup_group`; if `/myid` has no reply while the container is running, verify group membership, the bot username/token, and that no second process uses the same token. This release processes groups concurrently with per-group ordering, so a slow OCR, map, routing, export, or alert operation in one group does not block commands in all other groups.

## Test_SO setup

`TEST_MODE=true` permits collection and scheduled alerts only in the registered group whose exact title is `Test_SO`. No numeric chat ID was supplied, so it is learned using `/setup_group`.

The global management/export admin is Telegram ID `5400514256`. Authorization uses numeric IDs, not usernames. Send `/myid` to verify your account. If your test owner is a different account, explicitly add its verified numeric ID to `GLOBAL_ADMIN_IDS` in `.env`, then restart.

All operational alerts are mirrored to `Group_SO_Controll`. Each forum topic is
registered once by sending `/register_alert_topic | CATEGORY` inside that topic.
The current production forum chat ID is `-1003887339884` and is configured in
`.env` as `CENTRAL_ALERT_CHAT_ID`.
The first successful registration also learns the current central forum chat ID.
This keeps routing working if Telegram converts the group to a supergroup and
changes its numeric ID; the configured `CENTRAL_ALERT_CHAT_ID` remains a fallback.
The large warehouse refresh is intentionally deferred until 30 seconds after
Telegram polling starts, so commands remain available immediately during startup.
Supported categories are Morning Talk, Wrong Plan, Over 10Km, User AL, Distant
Dealer, User In Wrong Group, New User Join, Missing Report, and Banner Report. `/alert_topics`
shows the stored routing. Environment topic IDs are an optional fallback.
Follow-up questions remain in the original dealer group so the SO/SS can reply.
When a valid outlet report proves that a user's master dealer is stale, the bot
persists the new assignment, removes that user's invalid same-day missing rows
from the old dealer, stops their old follow-ups, and evaluates future windows
only in the actual dealer group.

For OL Nan, whose roster ID is 7791580455:

```text
/setup_group | R1-CA2 | 7791580455
```

That ID must actually be participating in Test_SO to submit reports. The screenshot shows test accounts, so for testing with one of those accounts instead:

1. Have the test account send `/myid`.
2. As global admin, run `/add | ACTUAL_TEST_ID | EMPLOYEE_ID | Phearom 619 | R1 | CA2 | SO` if that ID is not already in List_SO.
3. Run `/setup_group | R1-CA2 | ACTUAL_TEST_ID`.
4. Run `/status`.
5. During a 15-minute work window, send a photo/report and reply with a GPS location using the same test account. A completed pair suppresses the missing alert. Leave a subsequent window empty to test the alert and five-minute question.

Monitoring starts with the next full reporting window after startup. For example, starting at 08:05 means the first automatic check is for 08:15–08:30. `/alert_now` explicitly checks the most recent completed window and may be used for a controlled test. It can create actual missing rows and send actual alerts.

The bot must be a member of the group and able to read reports and locations and send messages/documents. Your screenshot shows it as admin with message access. In forum groups, run `/register_topic` inside the specific reporting topic; only that topic is monitored.

## Commands

| Command | Syntax / behavior |
|---|---|
| `/start` | Full help |
| `/myid`, `/chatid` | Numeric user, chat, and topic IDs |
| `/add controller` | `/add controller | 1468225026 | R1-CA1,R1-CA2 |` |
| `/remove controller` | `/remove controller | 1468225026 |` removes all assigned group scopes persistently |
| `/add` | `/add | Telegram ID | Employee ID | Real Name | R1 | CA2 | SO`; Employee ID is required for plan matching |
| `/remove`, `/restore` | `/remove | ID`, `/restore | ID`; group-specific persistent exclusions |
| `/al` or `/AL` | Any rostered SO/SS can pause their own full-day alerts; controller syntax: `/al | ID | 2026-09-15` |
| `/AL | 4day` | Rostered user pauses their own full-day alerts for four calendar days |
| `/AL | ID | 5day` | Controller/global admin pauses the target user's alerts for five calendar days |
| `/AL | ID | 2026-10-21 | 5day` | Multi-day leave beginning on the supplied date |
| `/al_morning` or `/AL_Morning` | Any rostered SO/SS can pause their own morning alerts; afternoon remains active |
| `/al_afternoon` or `/AL_Afternoon` | Any rostered SO/SS can pause their own afternoon alerts; morning remains active |

A rostered SO/SS may also send a standalone `AL`, `al`, `(AL)`,
`ច្បាប់មួយថ្ងៃ`, `ឈប់មួយថ្ងៃ`, or `សុំច្បាប់មួយថ្ងៃ` in their registered group.
The bot records full-day leave for that sender and that message date. Detection
is intentionally exact. A Morning Talk attendance line ending with `AL`, such
as `Mi Mony (SO): AL`, is matched to one active roster identity and suppresses
that user for the form date. Invisible Telegram characters and reversed Latin
name order are normalized. `Sales`, `Phally`, `Actual`, blanks, and `Yes` are
not AL. Same-period missing rows are removed so follow-up questions and Power BI
do not keep counting an AL user. Every AL source is mirrored to `User_AL`.
| `/change` | `/change | ID | name | New Name`; fields: name, region, dealer, position |
| `/change_dealer_from` | `/change_dealer_from | TELEGRAM_ID | Real Name | R1 | CA2 | CA6`; validates identity/current assignment, records an audit override, and keeps the transfer after roster re-import |
| `/pin` | Instructions to submit a GPS reply in the reporting group |
| `/status` | Scope, monitored count, test state, plan dates and boundaries |
| `/alert_now` | Latest completed window, no duplicate already-delivered alerts |
| `/reset_today` | Global admin clears today's missing rows in current group; keeps reports/pins |
| `/setup_group` | `/setup_group | R1-CA2 | test IDs`; omit IDs in production |
| `/register_topic` | Select current forum topic, or main group |
| `/register_alert_topic` | Run inside a central topic, e.g. `/register_alert_topic | Wrong Plan` |
| `/alert_topics` | Show all central category-to-topic registrations |
| `/register_alert_topic | Banner Report` | Run once inside `Banner_Report`; qualifying reports and their full photo albums are copied here |
| `/sync_so_list` | Global admin reloads `data/List_SO.xlsx`; preserves manual additions and exclusions |
| `/update_list_so` | Global admin starts a 15-minute upload session; reply to its prompt with the new roster `.xlsx` |
| `/update_plan` | Global admin starts a 15-minute upload session; reply to the prompt with .xlsx |
| `/export` | Missing report, optional date `YYYY-MM-DD` |
| `/summary_by_so` | Daily SO summary, optional date `YYYY-MM-DD` |
| `/export_raw_data` | Power BI-ready report/check-in data, optional date `YYYY-MM-DD` |
| `/export_so_list` | Roster workbook |
| `/currect_plan` | Global admin downloads the exact original workbook from the latest successful `/update_plan`; formatting, logo, merged cells, formulas and filename are preserved |
| `/set_dealer_location` | Global admin saves dealer origin: `/set_dealer_location | R1-CA2 | 11.563299,104.912002` or a Google Maps link |
| `/dealer_location` | Global admin views a saved dealer origin: `/dealer_location | R1-CA2` |
| `/add_dealer_location` | Add a dealer origin with verified roster ownership: `/add_dealer_location | R1-CA2 | TELEGRAM_ID | Employee ID | Real Name | LAT,LON` |
| `/update_dealer_location` | Update an existing dealer origin using the same verified identity fields |

Global admins can export from any Telegram group or private chat; their exports cover all registered groups. Controllers can export only their assigned group scopes and manage members only within the current assigned group. Controller assignments grant no global plan-upload permission. Removing a controller assignment does not remove a separately configured global admin ID.

### Transfer one SO/SS to another dealer

Run either format as a global admin (it also works in private chat):

```text
/change_dealer_from | 7166364893 | Ra Phearom | R1 | CA2 | CA6
/change_dealer_from_7166364893 | Ra Phearom | R1 | CA2 | _to_dealer_CA6
```

Replace the sample ID and name with the exact active roster identity. The bot
rejects a wrong name, wrong current assignment, unknown dealer, or same-dealer
transfer. The current employee dimension changes to the destination dealer;
historical visit facts keep their original dealer. The saved override is
re-applied after `/update_list_so` and `/sync_so_list`.

When `AUTO_RECONCILE_DEALER=true`, a valid outlet form sent by an active SO/SS
inside another registered production group automatically corrects that Telegram
ID's current Region/Dealer assignment. The evidence message and old/new dealer
are audited, a persistent override survives roster re-import, and the change is
reported in `User_In_Wrong_Group`. Plain text, GPS-only messages, Morning Talk,
test groups, unknown users, and duplicate names cannot trigger a dealer move.

The optional `Controller` column in `List_SO.xlsx` is synchronized automatically by startup, `/sync_so_list`, and `/update_list_so`. Put a controller's numeric Telegram ID on each employee row; the bot consolidates duplicate rows into one controller assignment per dealer group. Multiple controller IDs may be separated by commas. Workbook-managed assignments are replaced on the next roster sync, while assignments created manually with `/add controller` are preserved. Dealer codes are normalized to the official 65-group region mapping, preventing a mistyped Region cell from creating an invalid group scope.

### New Telegram group-member alert

When a user joins a group or is added by another member, the bot sends a private alert to every numeric ID in `GLOBAL_ADMIN_IDS` (default: `5400514256`). The alert includes the group name and ID, new user's name/ID/username, whether they joined or were added, the actor's details, and the local event time. Duplicate Telegram service-message and `chat_member` events within two minutes produce only one admin alert. For reliable membership updates, keep the bot as a group administrator. Each global admin must also start the bot in private once so Telegram permits private notifications.

Controller names are learned from their group activity or from the roster. Until learned, their numeric ID is shown with a clickable mention. If no controller is assigned, the requested `Ra Phearom | @Phearom6996` label is displayed; this label does not grant permissions.

## Reporting rules

- Monday through Sunday, Phnom Penh time.
- 15 morning windows: 08:00–11:45. No windows during 11:45–13:00.
- 15 afternoon windows: 13:00–16:45. Last window: 16:30–16:45.
- Windows are half-open: a report at exactly 08:15 belongs to 08:15–08:30. Checks run within about 2–7 seconds after the boundary, allowing the configured settle delay plus the five-second scheduler tick.
- The interval defaults to `ALERT_INTERVAL_MINUTES` and can be changed at runtime by a global admin with `/change_alert_interval | MINUTES` or the 15/20/30-minute shortcut commands. The runtime setting is stored in PostgreSQL and survives restarts.
- The updated roster has 234 employee rows: 198 normalized `SO` and 36 normalized `SS`. `Sales Officer` and `Sr. Sales Officer` become `SO`; `Sales Supervisor` and `Sr. Sales Supervisor` become `SS`. Only `SO` is monitored for missing-report alerts.
- Official names come from List_SO. Live Telegram usernames are learned by numeric user ID. A user cannot impersonate a roster member by changing their display name.
- A valid outlet is a report/photo plus one same-user GPS. Direct reply linkage is preferred. Otherwise one nearest same-user pin within five minutes is linked; the match method is exported. This fallback is a timing inference, not a verified outlet identity.
- An album counts once. Uncaptioned photos are attached to a unique form within two minutes. A GPS cannot be reused for two outlets. Ambiguous separate messages may need direct replies for reliable matching.
- Both report and pin must have arrived before the checked window closes. Missing alerts represent the state at the deadline; later reports do not erase that history.
- Form detection recognizes `Dealer:` plus `Outlet Name:` from the supplied history. Any photo from a monitored user is stored as photo evidence, following the SA flow; unrelated photos should not be accompanied by a sales GPS pair.
- Repeated delivery and edits of the same Telegram message do not create duplicate events. Submission time comes from the original Telegram message date, not processing time or edit time.

## Khmer reason follow-up

Each missing SO receives the requested alert format in an individual message. This makes the reply target unambiguous and avoids Telegram message-length limits. The count is that person's missed windows today in the group.

After five minutes without a reason, the bot asks one randomly selected Khmer question and requests a reply. The pending state survives restarts. A direct text/caption reply to the alert or question is accepted only from the missing SO. A plain text message can answer the latest unresolved alert for that same SO/group within 30 minutes. Use direct replies when several misses need separate explanations.

The bot can request and track a response; Telegram cannot force a person to answer. Unanswered reasons stay pending. Voice transcription is not included.

## Photo time rule

The configured rule follows the request exactly:

| Gap: Telegram submission minus photographed time | Status |
|---|---|
| Greater than 30 minutes | Review |
| Exactly 30 minutes or less | OK |
| Future time, unreadable/ambiguous timestamp, OCR failure | Review |

The image time `13:33` sent at `14:25` gives 52 minutes and `Review`. A gap of 30 minutes or less gives `OK`. Supported formats include AM/PM, 24-hour HH:MM, DD/MM/YYYY, and English dates such as 09 Sep 2026. OCR scans the stamp area and tries a separate white-badge crop where needed. It runs asynchronously after the report is saved, with two concurrent workers. An export made before OCR finishes shows Review/OCR pending.

When no date can be read, the submission date is assumed and the OCR Issue column says so. A time earlier/later across midnight is not silently shifted to another day. OCR is imperfect: Review is a request to inspect evidence, not proof of a reporting violation. If an album contains multiple timestamps, any photo marked Review makes the report Review.

## Plan and boundaries

The supplied updated `Plan_SO(4).xlsx` is bundled unchanged as `data/Plan_SO.xlsx`. It parses 9,755 scheduled employee/commune/date rows covering 3 August–11 September 2026. The reader limits processing to the first 100 columns because the workbook's formatting extends far beyond its live schedule columns. It reads nonempty route marks as planned visits and matches employees by Employee ID first, then exact normalized Sale Name fallback.

Ra Phearom is present in the updated roster as Employee ID `90024146`, Telegram ID `7166364893`, dealer `R1-CA2`, position `SO`. The plan contains that same Employee ID and schedules Mkak (`080809`) on 5, 12, 20, 21, 27 and 28 August plus 4 and 11 September 2026. Note that the uploaded plan labels Ra Phearom under dealer `CA1` while the roster labels him `CA2`; plan matching remains employee-ID based, but the source files should be corrected if that dealer mismatch is unintended.

Upload a new plan through `/update_plan`. Invalid files do not replace the previous plan. Each valid import records the uploading admin and file hash and stores versioned schedule rows in PostgreSQL. The latest successful upload becomes the only active plan for checking and Power BI; older batches remain archived for audit but are excluded from `gold_fact_plan_v1`. Dates absent from the latest workbook are not silently inherited from an older plan. This build detects a changed bundled roster/plan hash once at startup and imports the supplied updated files automatically; later restarts do not repeatedly overwrite newer uploads.

## Plan and road-distance alerts

Every new SO pin is resolved against the bundled KML/GeoJSON boundary and compared with that employee's planned commune for the pin date. `Incorrect` results—including no scheduled commune for that employee/date—send a private alert to every `GLOBAL_ADMIN_IDS` account. `Correct` and `Not checked` do not send wrong-plan alerts.

Road distance uses the configured OSRM driving-route endpoint backed by OpenStreetMap road data; it does not substitute straight-line distance. The bundled `Dealer Location With sales coverage.xlsx` automatically loads 65 dealer origins from 68 source rows. For duplicated dealers, the ordinary sales-coverage row is primary and a row whose Remark starts with `WH_` is retained as secondary. Every first pin sends a dealer→first-check-in alert to all global admins, regardless of distance. The alert includes time, dealer and first-check-in administrative locations, both map links, and the Telegram link. If routing fails, the first-pin alert is still sent with `Road Distance: Unavailable` and the routing reason. Each later leg (first→second, second→third, and so on) sends an alert only when road distance is greater than `ROUTING_ALERT_KM` (default `10`). Duplicate processing does not duplicate delivered alerts. The bot never invents a straight-line result. For production volume, set `ROUTING_URL` to a dependable/self-hosted OSRM service instead of relying indefinitely on the public demonstration endpoint.

`/export_raw_data` exposes a stable, minimal Power BI schema. Route details are converted into the business flags `Check in Over 10Km` and `Dealer First Check In (Distance Km)`.

The bundled `data/Plan_SO.xlsx` is now the uploaded `Plan_SO(1).xlsx`, preserved unchanged. Its row 7 contains day numbers and the title `WEEK1 , WEEK2_Sep'26` supplies the period. The reader uses the weekday row to determine whether that label is the starting or ending month and handles month rollover. It contains 9,755 scheduled commune/date rows covering 3 August–13 September 2026. Exact employee ID matching is preferred; when a manually added SO has a different placeholder ID, the bot uses an exact normalized Sale Name match such as `Phearom 619`. There is no 15 September schedule in this file.

The supplied `Map_KML.kml` is bundled in `data/boundaries/`: 1,652 commune records and 1,721 polygons. The loader supports its `HRName`, `PRO_CODE`, `DIS_CODE`, `COM_CODE`, and `0001- DEAL` schema and safely repairs self-intersecting polygons in memory. The test coordinate `11.563299,104.912002` resolves to Phnom Penh / ខណ្ឌ៧មករា / Veal Vong, commune code 120307. Plan Check is Correct/Incorrect only when the relevant employee/date schedule is also available; otherwise it is Not checked with the reason.

Telegram GPS pins and full Google Maps URLs containing `q=lat,lon` or `ll=lat,lon` are accepted. The bot first checks the bundled local boundary. If none match, it performs a rate-limited OpenStreetMap Nominatim reverse lookup and caches the result in PostgreSQL, including across restarts. Existing unresolved/online-fallback pin rows are upgraded from the local KML after startup. The online fallback fills Province, District and Commune but deliberately does not claim an official commune code or Correct/Incorrect Plan Check.

Plan Check uses the SO and report date, resolves the pin to a KML/GeoJSON commune, and compares official commune codes against every commune scheduled for that SO on that date. A matching code is `Correct`; a different pin commune or no SO schedule on an otherwise loaded plan date is `Incorrect`. Missing plan dates, pins, or boundary resolution are `Not checked`. Employee ID is matched first and exact normalized Sale Name is the fallback for manual/placeholder IDs. If a source plan has a missing or nonnumeric commune code, exact normalized commune name is the fallback. Incorrect raw rows state the actual and planned commune names/codes in `Remark Issue`.

## Exports

All exports default to today in Asia/Phnom_Penh. Date filters use the original report submission date.

- `/export`: Region, Dealer, Real Name, Position, Date, Time Submit (missing window), Time Late (alert sent time), Reason, First_Report, Last_Report.
- `/summary_by_so`: Region, Dealer, Real Name, Position, First_Report, Last_Report, Total Outlet (one report + one GPS), Total Missing, Plan Check, Total District, Total Commune and Reason.
- `/export_raw_data`: exactly 18 BI columns: Region, Date, Dealer, Province, District, Commune, Lat, Lon, Real Name, Employee ID, Telegram ID, Position, First Report, Last Report, Plan Check In, Check in Over 10Km, Dealer First Check In (Distance Km) and Reason. It keeps one row per logical report/check-in; daily SO metrics repeat consistently across that SO's rows. Missing-report fact rows remain separate for a clean Power BI star schema.

The bot still calculates the photo-to-submission gap internally for operational review, but `/export_raw_data` exposes only the approved 18-column BI visit schema. The separate `/export` command remains the missed-window operational export. First/Last Report include received reports even if GPS is missing; each raw row represents one logical report/album, including unpaired reports, and orphan GPS-only messages are not counted as outlets. Summary district/commune totals count distinct resolved locations of matched visits.

For BI flags, `Plan Check In` is `Yes` only when the actual commune matches the active plan and `Check in Over 10Km` is `Yes` when any sequential check-in leg for that SO/day exceeds the configured threshold. The export separates explanations into `Reason_Miss`, `Reason_Wrong_Plan`, and `Reason_Over_10Km`. `Dealer First Check In (Distance Km)` is numeric so Power BI can aggregate it without text conversion. The Gold facts expose the same three reasons in the matching business table.

When a pin is outside the scheduled commune, the bot posts one of five randomized Khmer questions in the reporting group as a reply to that pin. It does the same when a sequential road leg is over the configured distance threshold. The SO must reply to the specific bot question (or to the original pin); the response is stored against that exception and is not treated as a missing-report reason.

The Power BI Gold model contains `gold_dim_date_v1`, `gold_dim_employee_v1`, `gold_dim_location_v1`, `gold_dim_dealer_v1`, `gold_dim_group_v1`, `gold_fact_plan_v1`, `gold_fact_missing_report_v1`, `gold_fact_wrong_plan_v1`, `gold_fact_road_exception_v1`, and the live-map views. The generic `gold_fact_so_visit_v1` view is intentionally removed. `gold_dim_date_v1` includes the month-position label `week` (`Week1` through `Week5`). Both Wrong Plan and Road Exception facts expose `province`, `district`, and `commune`, populated by the boundary lookup of each pin's latitude/longitude. Missing, wrong-plan, and road-exception facts expose focused, report-ready columns and remain filterable by date, employee, dealer, and the dimensions retained in the model. `gold_fact_live_location_v1` keeps the newest valid Cambodia pin for each SO/SS. `gold_fact_live_route_v1` supplies a Power BI-ready five-minute live map: sampled OSRM road paths plus numbered actual check-in markers and employee/location/Telegram tooltips.

All live-map views use the current `Asia/Phnom_Penh` calendar date at query time. They automatically roll over at Cambodia midnight, so a new day does not require a container restart or manual view rebuild.

Only normalized `SO` and `SS` positions are monitored and exposed through the Gold employee and business fact views. Both roles use the same missing-report, wrong-plan, dealer-first-distance, over-10-km, reason-question and reason-reply flows. Alert labels retain the employee's actual normalized position (`SO` or `SS`). Legacy `Head Of Sales` and `Area Sales Manager` rows are deleted on every startup, even when the bundled `List_SO.xlsx` hash has not changed, preventing inactive historical management rows from returning in Power BI.

## History and operation limits

The supplied Telegram HTML/photo history was used to inspect report formats and test OCR; it is not imported as new live SO attendance data. A bot does not automatically receive old group history. Telegram pending updates are retained for at most 24 hours: https://core.telegram.org/bots/api#getting-updates . Keep the bot running for complete daily monitoring.

Scheduled alerts deliberately skip windows that started before this process came online. This avoids fabricated misses for downtime. `/alert_now` overrides that protection for an explicit manual check. Restart the bot after changing .env. Controller assignments, leaves, exclusions, plan versions and collected reports are database-persistent.

Normal retries avoid duplicate delivered alerts. Like any Telegram/database integration, a process crash after Telegram accepts a message but before its message ID is committed can cause one duplicate on retry; Telegram does not provide a transaction shared with PostgreSQL.

## Source layout

- `app/bot.py`: commands, access control, live event ingestion and OCR queue
- `app/service.py`: schedule, missing alerts, Khmer follow-ups
- `app/rules.py`: 15-minute windows, report/GPS matching, timestamp rule
- `app/imports.py`: roster and versioned plan ingestion
- `app/geo.py`: boundary resolution and plan comparison
- `app/ocr.py`: local Tesseract processing
- `app/exports.py`: requested Excel outputs
- `app/db.py`: isolated PostgreSQL models and runtime lock
- `app/check.py`: offline/input checks and optional database connection check
- `tests/test_so.py`: automated behavior tests with mocked Telegram delivery

Run tests after installing pytest:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest
.\.venv\Scripts\python.exe -m pytest -q
```

Validation details and limitations are in TEST_RESULTS.md. Do not interpret passing mocked tests as a completed live deployment test.

Missing-report alerts are sent once per group/window and list every missing SO/SS
in the same message. Five minutes later, one reply question mentions the same
users. Each reply is stored only against the responding user's missing record.

Telegram IDs `477167813`, `424220108`, `831220255`, and `7166364893` are
permanently excluded. Startup removes their roster, plan, event, missing-alert,
controller, and BI visit records, and roster imports cannot add them again.
## Pause automatic bot behaviour without stopping Docker

Global administrators can temporarily mute every unsolicited bot output while
keeping commands and the production data stack online:

```text
/stop_bot
/start_bot
/bot_status
/break_bot_1h
/break_bot_2h
```

`/stop_bot` pauses missing-report alerts and follow-ups, Wrong Plan and road
distance alerts/questions, reason acknowledgements, new-member alerts and all
central alert mirroring. It does not stop Docker, Telegram commands, report and
location ingestion, OCR/geocoding, warehouse processing, API, backups, Grafana,
or Power BI data refresh. The setting survives a container restart. `/start_bot`
resumes from the current time and does not send missed alerts retroactively.

`/break_bot_1h` and `/break_bot_2h` provide persistent timed breaks. Automatic
alerts, questions, acknowledgements and central mirrors resume automatically at
the displayed Cambodia time. Reporting windows elapsed during the break are not
replayed. Commands, ingestion and BI processing continue throughout the break.

Every recognized `Morning Talk` message from a registered reporting group is
mirrored once to `Group_SO_Controll`, including the source group, group code,
sender name, username, Telegram ID, business date, detected AL names and the
complete report text. Each matched AL is also sent to `User_AL`; unmatched or
ambiguous names are clearly flagged and never suppress another user. The receipt
is also stored by group and business date. A global admin can check
all registered groups that have not submitted today with either command:

```text
/group_not_Morning Talk
/group_not_morning_talk
```

To stop only the delayed missing-report question shown after a red missing
alert, use:

```text
/stop_ask
/start_ask
/ask_status
```

This switch is persistent and independent of `/stop_bot`. `/stop_ask` does not
disable the original missing-report alert or any data/BI processing.

# Banner Report forwarding

Create the `Banner_Report` topic in `Group_SO_Controll`, then run this command
inside that topic once:

```text
/register_alert_topic | Banner Report
```

For an enabled reporting group/topic, the bot performs one native Telegram
forward of the original photo or complete album with its complete caption when
the caption contains a non-empty `Banner Type:` value. Telegram displays
`Forwarded from <sender>` automatically. No separate heading or sender message
is added, and the caption is never shortened. Blank, `0`, `No`, `N/A`, and
`None` values are ignored. The source group must allow message forwarding;
Telegram privacy-protected content cannot retain native sender attribution.
This is deterministic text matching only; it does not use OCR, image
recognition, an LLM, or an AI agent. Duplicate Telegram updates are suppressed.

Global-admin master-data updates are available directly in Telegram:

- `/update_location` or `/update_location_master`, then reply to the bot prompt
  with `Location_All.xlsx`.
- `/update_dealer_master`, then reply to the bot prompt with
  `DEALERS STATION All Location.csv`.

Both flows validate before activation, replace the bundled master atomically,
and refresh PostgreSQL/Gold models without rebuilding the Docker image. The
dealer upload must contain all 65 canonical dealer codes.

## Production Admin Dashboard

The dashboard is served by FastAPI on `http://127.0.0.1:8081` (or the configured `DASHBOARD_PORT`). It reads and writes the same PostgreSQL database as the Telegram bot. Redis is used only for short-lived dashboard cache entries and login throttling; PostgreSQL remains authoritative. Authenticated APIs under `/api/v1/admin` cover roster read/add/edit/disable, active plan read and validated `.xlsx` upload, date-range performance summaries, AL attendance and audited corrections, dealer-origin edits, global alert settings, and the audit log. `/api/v1/dashboard/snapshot` supplies live map data. API docs are at `/api/docs` on the local dashboard.

Before starting this version, update `.env` with newly rotated credentials. The old Telegram/ngrok credentials and database password were pasted into chat and must not be reused. Add `DASHBOARD_ADMIN_TOKEN` as a random secret of at least 32 characters; it is exchanged for an expiring eight-hour browser session. The root Compose profile uses dashboard sign-in without ngrok Basic Auth. Never commit or share `.env`.

Windows PowerShell setup from the project folder:

```powershell
if (-not (Test-Path .env)) { throw '.env is missing and this project copy has no .env.example; restore a trusted project template first.' }
notepad .env
```

Set the Telegram bot token and admin ID, database password, newly generated ngrok authtoken, and `DASHBOARD_ADMIN_TOKEN`. Generate a random dashboard token locally:

```powershell
$dashboardSecret = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
$dashboardSecret
```

Then build and start PostgreSQL, Redis, the bot, and dashboard:

```powershell
docker compose up -d --build
docker compose ps
docker compose logs --tail 80 so-bot dashboard
```

If this database volume already exists and you are rotating the database password, first run `docker compose exec postgres psql -U postgres -d SA_Alert`, then enter `\password postgres` at the `psql` prompt and follow its hidden password prompts. Exit with `\q`, update `DB_PASSWORD` in `.env` to that same new value, and recreate the app services with `docker compose up -d --build`. Do not delete the PostgreSQL volume to rotate a password; that would erase the database.

Open `http://127.0.0.1:8081` and sign in with `DASHBOARD_ADMIN_TOKEN`. To enable the public tunnel:

```powershell
docker compose --profile public up -d --build ngrok
docker compose logs --tail 80 ngrok
(Invoke-RestMethod http://127.0.0.1:4040/api/tunnels).tunnels | Where-Object proto -eq 'https' | Select-Object public_url
```

The root Compose profile does not add ngrok Basic Auth. A working tunnel must show `started tunnel` and a `Forwarding https://...` URL, or appear in `/api/tunnels`. The inspector is bound to `127.0.0.1` on the host and forwards only to the dashboard container; never publish PostgreSQL or Redis through ngrok.

Dashboard plan uploads accept `.xlsx` files up to 20 MB. The workbook is validated before a PostgreSQL transaction updates the active plan; old versions remain archived by the bot importer. If validation or commit fails, the previous plan remains active. Roster changes, AL corrections, settings changes, plan uploads, and dealer-origin edits are audited. AL is initially inferred as no matching check-in for the selected date; verify official Morning Talk/leave records before using it for payroll decisions.

This is a single-admin-secret deployment, not a multi-user identity provider. A public enterprise deployment should use organizational OIDC/VPN, per-user roles, database backups, and pinned image versions. Redis is non-persistent by design because it stores only expendable cache/rate-limit state.

