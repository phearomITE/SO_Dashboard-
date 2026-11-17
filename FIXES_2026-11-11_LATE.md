# Live Map / AL fixes (11 November 2026)

## Changes
- Outlet pin clicks keep the Leaflet popup open. Previously, pin-click handler changed the employee filter and rebuilt all map layers, closing the popup before it could be viewed.
- Popup contains distance from dealer/previous outlet, region/dealer, district, actual commune, plan commune, plan status, employee and time. It offers Google Maps driving/walking directions using the viewer's current location where supported. Removed the Telegram Report row from that popup.
- Actual Plan modal lists employee names instead of employee IDs when available, with an ID fallback when no matching name is available.
- Employee Route shows two separate cards: Total Outlet and Actual Plan (visits/targets and percent). Summary Reason is now immediately below employee profile and these cards, above the outlet sequence.
- Dashboard AL snapshot includes all active roster positions with recorded leave (rather than only SO/SS). Missing-report enforcement continues to apply to its configured eligible positions. The AL API only displays records known to the database; unmatched Morning Talk names require roster reconciliation.
- Existing .env, GPU/Ollama setup, and RUN_NGROK_DASHBOARD.bat are preserved.

## Verification/limitations
- JavaScript syntax and targeted Python AL/reason tests passed.
- Not verified against your live PostgreSQL, Telegram updates, Docker build, or map browser interactions.
- The full React frontend production rebuild is performed in the existing Docker launcher on your system.
- ZIP is NOT a Git commit; it contains no repository history.
- ZIP includes credentials from your existing .env and must be kept private.
