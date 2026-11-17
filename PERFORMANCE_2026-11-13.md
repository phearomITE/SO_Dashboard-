# Dashboard performance changes — 13 November 2026

The existing services are retained: PostgreSQL for dashboard and bot operational data, Redis for short-lived derived responses, SQLite for the imported GP collection database, and the dashboard worker for GP generation. ngrok is only the public tunnel; it does not accelerate queries. No additional server or paid service is needed for this update.

Implemented:
- Auto GP mode reuses finalized complete group-days and scans missing groups. Today's group scans can be reused for 60 seconds; Force Telegram refresh remains available to capture edits and late posts. Saved complete scans never contact Telegram. Coverage checks still reject incomplete totals.
- Direct two-sheet GP exports retain attendance and pin reconciliation without creating, styling, saving and deleting three detail sheets.
- Sender-ID indexes accelerate identity reconciliation in the GP SQLite database.
- Validated, unedited GP artifacts are cached separately from user-edited workbooks. Source database/reference file versions invalidate reuse; current-day artifacts expire after 60 seconds, historical artifacts after 12 hours. Edited workbook downloads never overwrite cached generated artifacts.
- Redis stores compressed GP previews, keyed by workbook modification version, for 30 minutes. A bounded 16-entry memory fallback works if Redis is unavailable. Editing replaces the workbook and naturally selects a new cache key.
- AL summaries cache for 5 seconds; reason summaries and first-check notifications for 10 seconds. Existing mutation invalidation clears these keys. Existing roster, plan, performance, live-map and route Redis caches remain in use.
- Five PostgreSQL indexes cover employee/date visits, pin sender/time, pin date/sender, leave identity/range and reason date. The index application script uses a 1-second lock timeout to avoid waiting behind live transactions. All five were applied and the incomplete concurrent index was repaired.
- Unchanged local GP database copies are not written back to the Windows bind mount. Changed data still uses SQLite's consistent backup API.
- Python compilation is checked in the Docker build before deployment. The session advisory-lock connection now uses AUTOCOMMIT in source, preventing an idle transaction on the next normal bot deployment. The running bot was not restarted for this dashboard change.

Measured on saved November 3 data with cProfile enabled on both generation runs:
- Previous pipeline: 64.073 seconds.
- Optimized uncached pipeline: 28.768 seconds (before the additional unchanged-write avoidance).
- Repeated artifact reuse: 0.058 seconds without profiling.
- Every summary cell matched the previous workbook exactly; both output sheets and all 65 dealer rows were retained.

Authenticated local API measurements (first request / repeated request, seconds): AL 0.243 / 0.008; reasons 0.006 / 0.003; performance 0.461 / 0.028. Redis PING and populated GP preview keys were verified. These are local measurements, not guarantees for a full Telegram network scrape. Initial scans still depend on Telegram response times and rate limits.

Regression tests cover scan freshness, missing groups, forced refresh, Redis fallback/invalidation, edits, formulas, backups and hide/unhide. Test and benchmark artifacts live under data/gp_reporting/verification/performance. Run tests in the container with the installed application dependencies.
