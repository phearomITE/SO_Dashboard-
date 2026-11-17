# November 11, 2026 dashboard and AL updates

## Live map notification navigation
- Every late/missing first-check notification is clickable and navigates to Live Map Check In.
- Date, employee identity, assigned region and dealer are sent to the embedded map as URL parameters. The map selects the employee and reveals their first outlet marker (including clustered markers) when GPS coordinates exist.
- For an employee without a valid check-in, the UI explicitly says no outlet location exists. It only centers the assigned dealer when dealer coordinates are known; it never fabricates an employee/outlet point.
- The `first-check-alerts` API now includes `telegram_id` to assist deep linking.

## Employee route
- Employee identity, Telegram link, full-width metrics, check-in times and visit-status details now use compact responsive layout.
- Each route stop is a real button: selecting it zooms to and opens that outlet's map popup.
- Layout includes accessible keyboard focus and responsive mobile columns.

## Morning Talk AL parser
- Recognizes `AL`, `A/L`, and `No (AL)` terminal roster entries, without confusing `No` alone or `AL Half Morning` with a full-day leave.
- Morning Talk alert shows total full-day AL entries, roster matched count, and unmatched count independently.
- A duplicate Morning Talk report is not an additional unique person for manual daily rollups.

## Verification
- `python -m pytest -q tests/test_morning_al_variants.py` passed (2 tests).
- `node --check` on embedded dashboard script passed.
- Full application tests could not run because `python-telegram-bot` and related environment dependencies are not installed here.
- **Build step on your development/deployment machine:** `cd admin_frontend && npm install && npm run build`. The React source/CSS are updated; the pre-existing compiled frontend assets in `app/static/admin/assets` are **not** rebuilt in this package due to unavailable npm dependencies in the execution environment. Deploy the rebuilt frontend assets through your normal release process.
- No changes were applied to a running service or PostgreSQL database.

## Master Sale dashboard (2026-11-11 follow-up)
- Master Sale now has **Upload Master Sale**, importing a compatible `List_SO.xlsx` workbook using the existing roster importer. Successful updates are persisted to `data/List_SO.xlsx` on the shared data volume and recorded in the admin audit table. Workbook validation is transactional.
- The **Delete** action requires confirmation, disables the employee, hides them from the Master Sale screen, and keeps historical check-ins. Deletion is persistent across subsequent workbook uploads. It is not a hard database/history deletion.
- Upload and delete require an authenticated dashboard admin session.
- `.env` has been restored from the user's original SO_Alert_Bot (2).zip. **It contains real credentials. Keep this ZIP private.** Do not commit `.env` to version control or share the ZIP publicly.
- Start only with `RUN_NGROK_DASHBOARD.bat`; it runs the existing configuration checks, rebuilds the Docker frontend and backend, and launches ngrok.
