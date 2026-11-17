# Final live-map validation — 13 October 2026

94 automated tests passed. The final Power BI live-map contract now exposes
numbered real Telegram check-ins, hides intermediate OSRM coordinates as
markers, keeps road-following path geometry, and downsamples each employee/day
to approximately 150 geometry points for reliable Azure Maps rendering. Route
analysis retries reuse completed distances and do not call OSRM or duplicate
alerts. Startup geo backfill is scheduled after Application startup, removing
the previous PTB un-awaited-task warning.

# Startup repair validation

72 automated tests passed on Linux with mocked Telegram delivery and SQLite. Added regression tests for connection-refused messages, missing OCR, missing English language data, password/database diagnostics, read-only probes, day-number plan headers, Google Maps URL parsing, supplied-KML lookup, cached reverse geocoding, OSRM driving-distance parsing, dealer-origin commands, sequential check-in distance alerts, wrong-plan global-admin alerts, position normalization, multiple outlet exports, clickable Telegram photo links, exact Sale Name plan fallback, missing SO schedules, invalid source commune codes, keeping unrelated warnings visible, suppressing the five-minute question after a simple reason whose Telegram reply-chain ID does not exactly match the stored alert, and notifying global admins when a user joins or is added to a Telegram group. The new dealer-location identity commands, roster-upload prompt, Actual/Plan comparison, planned map, and readable From/To commune alert fields are covered. Data-engineering tests verify all 1,652 Location_All rows, Bronze/Silver reconciliation, idempotent incremental visit loading, Khmer geography enrichment, and DQ errors for visits without GPS. Alert tests cover duplicate suppression and confirm dealer→first distance does not alert while first→second above 10 km does. The `Gap Minutes` raw-export column is also verified absent. Windows batch execution and a live Docker/PostgreSQL deployment were not available here.

`/currect_plan` exact-file persistence was verified with `Plan_SO_WEEK3 , WEEK4_Sep'26.xlsx`: the imported and returned workbooks are byte-for-byte identical (371,591 bytes), retain the original filename, and contain 6,268 parsed schedule rows. The new `so_plan_files_v1` table is created automatically at startup. Plans uploaded before this upgrade must be uploaded once more to store their original workbook bytes.

The uploaded `Plan_SO(4).xlsx` was copied byte-for-byte to `data/Plan_SO.xlsx` and parsed directly: 9,755 scheduled commune/date rows, covering 3 August–11 September 2026. The uploaded `List_SO(1).xlsx` was copied byte-for-byte to `data/List_SO.xlsx`: 234 employees, normalized to 198 SO and 36 SS. Ra Phearom is Employee ID `90024146`, Telegram ID `7166364893`, and has Mkak (`080809`) plan rows through 11 September. An actual point inside the supplied Mkak KML polygon returned `Correct` for Ra Phearom on 11 September; the supplied Phnom Penh point in Veal Vong (`120307`) returned `Incorrect`. The source roster says CA2 while the source plan says CA1; matching remains Employee-ID based. Exact Sale Name fallback remains supported.

The supplied KML contains 1,652 commune records and 1,721 polygons. The supplied coordinate `11.563299,104.912002` was matched locally to Phnom Penh / ខណ្ឌ៧មករា / Veal Vong, commune code 120307.

Additional complete command coverage:
- `/start`, `/myid`, `/chatid`, `/pin`, unknown command handling
- `/setup_group`, `/register_topic`, `/status`, private/bad-scope rejection
- all leave variants and all `/change` fields
- `/update_plan` upload-session creation
- all four Excel export command paths
- `/reset_today`, controller scope denial, and friendly validation errors
- same-window enforcement for both report and GPS evidence

One defect was found and fixed: a directly replied GPS from an earlier window could be counted with a later report. Both messages must now be inside the same reporting window.

The observations below describe the original SO feature tests, retained as historical validation.

# Validation — 15 September 2026

24 automated tests passed. Tests run with Python 3.12 on Linux, using the pinned python-telegram-bot 21.6 and SQLAlchemy 2.0.36 versions. The distribution targets Python 3.11 on Windows or the supplied Python 3.11 Docker image.

Behavior coverage:
- 30 exact daily windows and lunch exclusion
- AM/PM and 24-hour timestamp parsing, English/numeric dates, 30-minute boundary, future and unreadable times
- One-to-one GPS matching, direct replies, album deduplication, other-user rejection
- Roster and real plan workbook imports; invalid upload preserves existing plan
- Controller add/remove persistence and authorization; restricted export scope
- Manual SO addition, persistent removal/restoration, morning leave
- Missing alert, repeated check deduplication, five-minute question, direct and plain text reasons
- Completed outlet suppresses alert; exact-boundary report belongs to next window
- Requested workbook headers and summary outlet count
- Edited-message deduplication and original submission time
- KML and GeoJSON polygons, plan Correct/Incorrect/Not checked
- PostgreSQL table DDL compilation and offline Telegram Application construction

Real input inspection:
- List_SO.xlsx: 246 employee rows, 197 SO/Sr. SO; no duplicate numeric Telegram IDs.
- Plan_SO.xlsx: 6,268 scheduled commune/date rows; read limited to first 100 columns to avoid excessive formatting dimensions.
- The original workbooks are copied unchanged. Read-only openpyxl warnings about unsupported formatting extensions do not modify these files.

Actual local Tesseract 5.3.4 observations:

| Input | Extracted time | Submission time | Gap minutes | Result |
|---|---|---|---:|---|
| Supplied 08:34 AM screenshot | 08:34 | 09:40 (requested example) | 66.00 | Review |
| History photo_7101, 9 September | 14:24 | 14:25:04 | 1.07 | OK |
| History photo_7637, 11 September | 10:28 | 10:30:15 | 2.25 | OK |
| History photo_7643, 11 September | 10:38 | 10:38:42 | 0.70 | OK |

The third history sample required a separate white-badge crop. In the fourth sample the date could not be read reliably, so the assumed submission date is recorded in OCR Issue. These samples demonstrate operation, not an OCR accuracy rate for the full photo archive.

Not live-tested here:
- Telegram polling and real delivery in Test_SO
- Connection to the user's local PostgreSQL at 127.0.0.1:5435
- Windows batch scripts and Docker Desktop execution
- Live Telegram delivery and end-to-end verification against the user's running PostgreSQL instance

Tests use an in-memory SQLite database and mocked Telegram sends. PostgreSQL DDL was compiled but no live PostgreSQL server was available. Use CHECK_SO.bat and the Test_SO procedure in README.md before production monitoring.

## 2026-10-14 — Validated dealer-transfer command

- Added canonical and compact `/change_dealer_from` command formats.
- Validates Telegram ID, exact real name, current region/dealer, and destination dealer.
- Persists an audited roster override across `/update_list_so` and `/sync_so_list`.
- Refreshes the Power BI warehouse after a successful transfer while preserving historical facts.
- Verified: **97 automated tests passed**; Python compilation passed.
# Roster and plan identity correction — 20 Sep 2026

- Excludes `Head Of Sales` and `Area Sales Manager` during roster import.
- Deactivates duplicate `manual:<telegram_id>` users when the official Employee ID is available.
- Resolves legacy manual identities through Telegram ID before checking the plan.
- Prefers the active official Excel roster identity when processing Telegram activity.
- Verified: 73 automated tests passed.

## Management-role removal follow-up

- `/update_list_so` now permanently deletes legacy `Head Of Sales` and
  `Area Sales Manager` records previously retained as inactive history.
- `/export_so_list` independently excludes both roles as a safety check.
- Verified: 74 automated tests passed.

## Dealer routing and Wrong Plan alert update — 21 Sep 2026

- Loaded 68 dealer-location source rows representing 65 dealer codes.
- Duplicate dealers prefer the normal sales-coverage row over `WH_*` rows.
- Every dealer-to-first-check-in OSRM road distance sends a global-admin alert.
- Later sequential check-ins alert only when road distance is greater than 10 km.
- Wrong Plan alerts include the active plan-period filename and Khmer `Date Plan` text.
- Verified: 75 automated tests passed; archive syntax and startup input checks passed.
## 2026-09-21 — First check-in alert format v21

- Updated the dealer-to-first-check-in alert with time, dealer location, first-check-in location, two map links, and Telegram link.
- Dealer and first-check-in locations render as commune, district, and province; the commune is bold in Telegram.
- A first check-in now still alerts when routing is unavailable, with the routing failure reason.
- Automated tests: **76 passed**.
- Startup validation: **passed** (234 roster rows, 198 SO/Sr. SO, 9,755 plan rows, 65 dealer origins, 1,721 boundary polygons).
## 2026-09-22 — Power BI raw export schema v22

- `/export_raw_data` now returns exactly the 19 requested BI columns and no operational extras.
- Export grain remains one logical report/check-in row so Province, District, Commune, Lat and Lon remain accurate for each visit.
- First/last report, plan flag, over-10-km flag, dealer-first distance, missing flag and reason are calculated consistently per SO/day.
- SOs with missing alerts and no report remain visible as rows with blank location values.
- Automated tests: **77 passed**.
## 2026-09-22 — Power BI visit master schema v23

- Removed `Missing` from `/export_raw_data`.
- The export now contains exactly the requested 18 visit-master columns.
- Missing-only records are kept out of the visit export; Power BI should use `gold_fact_missing_report_v1` for that subject area.
- Automated tests: **77 passed**; startup validation passed.
## 2026-09-22 — Relationship-ready Power BI Gold model v24

- Added Gold Date, Dealer and Telegram Group dimensions.
- Added consistent Date, Employee, Dealer, Location and Group relationship keys to Gold facts.
- Enriched Missing Report with employee ID, name, position, region, dealer, report date, group, reason status and numeric missing count.
- Enriched Plan with employee, dealer, date and location relationship keys plus Khmer location fields.
- Road Exception now contains sequential check-in legs over 10 km only; dealer-first distance remains in the Visit fact.
- Automated tests: **79 passed**; startup validation passed.
## 2026-09-23 — Remove management roles from Power BI v25

- Gold Employee, Visit, Missing and Plan views now expose only normalized SO/SS positions.
- Gold Dealer no longer derives dealer members from management records.
- Startup permanently deletes legacy Head Of Sales and Area Sales Manager rows regardless of workbook hash state.
- Automated tests: **80 passed**; startup validation passed.
# Global Bot Pause Control — 23 October 2026

## Missing-report follow-up question control

- Added persistent `/stop_ask`, `/start_ask`, and `/ask_status` commands.
- `/stop_ask` disables only the delayed question after a missing-report alert.
- The original missing-report alert and all data/BI processing stay active.
- Full automated test result after this update: **109 passed**.

- Added persistent `/stop_bot`, `/start_bot`, and `/bot_status` commands.
- Automatic alerts, questions, acknowledgements, new-member notifications and central alert copies are muted while stopped.
- Telegram commands and the data/BI production stack remain online.
- Resume starts from the current time; no retroactive missing-report alerts.
- Earlier stop-bot validation result: **106 passed**.

