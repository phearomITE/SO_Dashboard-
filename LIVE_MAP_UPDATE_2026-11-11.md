# Live Map and Employee Route update — 2026-11-11

## Changes
- Default Live Map base layer changed from Light to Satellite.
- Removed the Source dropdown and Source field from outlet popups.
- Added **Back / Clear filters** to reset all region/dealer/employee/location filters and recenter Cambodia.
- Outlet popups keep distance, dealer, district, actual/plan commune, plan status, name, time and Telegram report evidence; map links now offer the outlet GPS location and directions from the viewer's device location. Viewer location requires permission from the browser/Google Maps.
- Employee Route keeps its six core metrics, adds a total user count from the available dashboard data, and displays up to five unique verified reason summaries from the authenticated reason API. If no verified explanations exist, the panel states that rather than inventing five reasons. The number is limited to users returned by the snapshot, not necessarily the whole master roster.
- Removed Location Dealer from navigation and removed its page route. Dealer markers remain on Live Map.

## Operation
Use **RUN_NGROK_DASHBOARD.bat** as before. It builds the Docker dashboard and opens ngrok. Keep .env private. There are no new configuration variables.

## Verification
- JS syntax: `node --check` succeeded on inline Live Map JavaScript.
- Python tests: reason and AL tests 6 passed.
- React production build was not run in this build environment (Vite dependencies are not installed); Docker on the user's machine will build the frontend with dependencies.
- Live PostgreSQL, ngrok, Ollama/Hermes and browser flows have not been tested here.
