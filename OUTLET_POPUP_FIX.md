# Outlet Map Popup — responsive layout fix

Updated `app/static/dashboard.html`:

- Replaced the legacy outlet `.popup .grid` and float-based photo layout with the isolated `.outlet-detail` component.
- The popup uses flexible, minimum-width label/value columns, a separate photo area, and wraps Khmer and English text naturally.
- Details: distance from dealer/previous outlet, region/dealer, district, actual commune, plan commune, plan status, employee, check-in time.
- Directions uses the actual marker's `latitude,longitude` and the Google Maps directions endpoint, which may request the viewer's location.
- Marker click opens its existing popup without changing filters; responsive popup width and Leaflet autopan keep content visible.
- Existing one-command startup script `RUN_NGROK_DASHBOARD.bat` is unchanged.

## Validation
- Inline dashboard JavaScript: `node --check` passed.
- `test_morning_al_variants.py` and `test_reason_analysis.py`: 6 passed.
- Full Docker/browser/Telegram integration was not run in this environment.

Note: `.env` is preserved from the user-provided archive; keep the ZIP private.
