# Report GP dashboard

Choose inclusive From/To dates (up to 31 days), select Telegram refresh or saved complete scans, and generate. A workbook is produced for each day.

The page contains two Excel-style preview tabs: Report GP and Report with Dealer. Their merged headers, colours, values and totals follow the supplied Excel template. Download Excel exports exactly those two sheets; multi-day ZIP downloads contain only the daily Excel workbooks. Internal audit files remain private.

Live scans reuse the saved authorized Telegram session. The dashboard never asks for login codes or 2FA passwords and never starts an interactive login. If the session expires, reconnect outside the dashboard or use saved complete scans. Private account configuration and reference workbooks remain in data/gp_reporting; setup controls are not shown on this reporting page.

The supplied legacy calculation engine remains unchanged. GP Active counts eligible SO/SS with captured authored messages or locations. GP AL is eligible roster minus Active, independently of scheduled annual leave. Current-day reports are snapshots. All 65 Execution groups must pass completeness checks before export. Region/dealer totals and template formulas are validated before downloads are offered.

Runs are serialized in a background thread, with cancellation and persisted history. Restarted unfinished jobs are marked interrupted. SQLite processing uses container-local temporary storage and persists the private database, session and backed-up roster to the mounted data directory. Private storage must never be placed in app/static or source control.

Verification: the November 3 saved report has two sheets, 65 dealers, 229 Active, 4,021 visits, matching regional/dealer totals and no unsupported preview formulas. Existing completed reports were converted to the two-sheet format. A full live historical rescan is not part of these UI checks.


Editing: both report tabs support cell selection, typing, double-click/Enter/F2 editing, arrow/Tab navigation, rectangular paste from Excel, and Ctrl+Z undo before saving. The formula bar exposes a selected cell's original value or formula. Save changes writes the workbook and refreshes both sheet previews; Discard removes pending edits. Pending edits block switching workbook/run and downloading until saved or discarded. Formulas support the template's cell arithmetic, SUM(range), IFERROR(formula,0) and cross-sheet references. Totals recalculate on save and Excel recalculates formulas when opening the downloaded file. Unsupported, circular or invalid formulas are rejected.

Each successful save retains the previous workbook and change list privately under the run's edit_backups directory, updates the daily Excel download, and rebuilds the multi-day ZIP. SHA-256 revision checking prevents overwriting changes saved from another session. Manual report edits affect that generated workbook only; they do not rewrite Telegram source records or the report template. A newly generated report uses the source database again.


Column visibility: select a cell and choose Hide column, or open Columns to toggle individual columns using their Excel letter and header label. Unhide all restores every column on the selected sheet. Hidden columns are omitted from the preview, merged headers shrink to the visible column count, and keyboard navigation/paste skip hidden columns. Save changes stores visibility in the XLSX and updates the ZIP. Each sheet retains its own visibility; hidden values and formulas remain intact. Discard and Ctrl+Z also apply to pending visibility changes. At least one column must remain visible.
