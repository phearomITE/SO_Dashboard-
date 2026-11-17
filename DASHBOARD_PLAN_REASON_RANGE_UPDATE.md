# Plan, annual leave and reason ranges

Annual leave scheduling is a fixed, always-visible panel. Attendance review has its own Search Employee input; scheduling retains a separate search and preserves selected employees while searching. Attendance cards follow the attendance search.

Plan Sale has Plan workbook and Actual Plan · All employees tabs. Excel outlet-total breakdown is removed. Actual Plan uses inclusive From/To dates (up to 92 days), region/dealer filters, employee search and CSV export. It includes active SO/SS roster entries plus identities present in the selected plan or visits, including vacant plan slots. The table is paginated at 50 rows; exports include all filtered rows.

Actual Plan % is recorded visits in the selected inclusive date range divided by Total Outlet, multiplied by 100. Total Outlet is the full assigned WS/DS + HORECA outlet pool from the last active workbook in that range. Each employee/commune is counted once across repeated calendar entries; unmarked commune rows are included. For example, 100 visits / 400 assigned outlets = 25%. Repeat visits count in the numerator and percentages may exceed 100%. Missing or zero targets display —. The overall rate uses visits and outlets only for employees with positive targets. Position comes from the roster, with a recorded-position fallback for historical identities. Position, Total Outlet, Visited Outlets and Actual Plan % are included in the CSV.

Scheduled communes and Completed planned remain separate commune-day measures. Their matching uses commune codes first, with a commune-name/district fallback when codes are missing. No location or no active workbook on a date is Unassessed. A known location outside the employee's schedule on an active-plan day is Incorrect. Duplicate roster aliases with the same Telegram ID are merged, preferring official IDs.

Reason Summary now uses inclusive From/To dates across reply counts, grouped employee summaries, Hermes processing and CSV/Excel exports. The old day query remains supported. Excel shows the range in the header. Multiple days combine each employee's replies and check-in totals; summaries are deduplicated. Hermes retains the existing 100-reply limit per run.

Read queries use PostgreSQL and short Redis caches (20 seconds Actual Plan, 10 seconds reasons). Plan uploads invalidate Actual Plan cache. Reason reprocessing invalidates reason caches. Authentication and access controls are preserved.
