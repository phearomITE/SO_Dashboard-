# Dashboard popup and Reason Summary fixes — 2026-11-12

Source: the SO_Alert_Bot.zip attached in this conversation. Cloudflare launcher and Compose settings restored from the previously generated account-ready version, preserving your current .env credentials and other source files.

## Outlet popup
- Removed the unbounded image/fallback element that could cover the outlet details, replacing it with an unobtrusive Telegram evidence link.
- Isolated the Leaflet popup CSS under `.outlet-map-popup` and enforced responsive label/value column sizing.
- Popup retains distance, region/dealer, district, actual/plan commune, plan status, employee, check-in time and Google Maps directions from the viewer's location.
- Keeps the selected popup open when asynchronous route information triggers a map marker re-render; Clear Filters dismisses it.

## Employee Route and reasons
- Employee profile displays `Position` when available in dashboard profile data.
- Matched replies with status `Needs review` now appear in Employee Route as pending instead of a misleading "0 verified explanations" empty state. Reason status remains transparent.
- Added an explicit high-confidence Khmer distance-reason rule (`ផ្លូវឆ្ងាយ`) and an English dealer-meeting rule. Pending historical records matching the rules are reclassified on the existing admin reason-summary endpoint; outlet forms/photos/locations remain excluded.
- Existing ambiguous replies still require working Hermes 3/Ollama or manual review; a GPU cannot be guaranteed from code alone. Bot already attempts Hermes inference for new alerts, when Ollama is reachable, and classifier timeout was increased to 20 seconds.

## Start
Run only `RUN_NGROK_DASHBOARD.bat` (original filename kept). It now starts Cloudflare quick tunnel with Docker services. Never publish the `.env` file or the generated ZIP, because they include secrets.

## Tests and limitations
- 11 focused pytest checks passed.
- Dashboard inline JavaScript passed `node --check`.
- Python modules compiled.
- Live browser, PostgreSQL, Windows GPU/Ollama and Cloudflare connectivity still require end-to-end validation on the user's computer.
