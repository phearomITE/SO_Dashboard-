# SO Bot evening update — 11 November 2026

## Run
Use only `RUN_NGROK_DASHBOARD.bat` in this extracted folder. Your existing `.env` is retained unchanged except commented optional Ollama settings. Keep the ZIP private; it includes credentials.

## AL reconciliation
The importer already recognized terminal `AL` / `A/L` / `No (AL)`, but reports received by older running versions were not replayed on upgrade. A one-time verified Employee ID migration backfills 11 November leave for SOTH Narin (90019441), BOU Thary (90007877), and BO Vannoy (90021780). Existing leave entries are unaffected, and unrelated names are not assigned leave. After reconciliation, matching missing-alert windows are cleared in DB and the snapshot excludes alerts overlapping stored AL. Previously sent Telegram notifications cannot be unsent; this only prevents further automated alerts.

The other AL entries already detected by the old system remain unchanged. Non-roster/SA unmatched AL is not silently assigned to a different user. This repair is narrowly limited to the explicit date/group/employees validated against List_SO.xlsx.

## Interactive Live Map
Click Total Check-Ins, Missing Reports, Over 10 km, Wrong Plan, AL, Sales Checked In, or Actual Plan to inspect records. Missing rows display employee, region, dealer, missed time and stored summary reason. Outlet entries can zoom to their marker, and outlet popups include Telegram evidence plus a GPS map link. Employee Route displays visit count / plan outlet target when targets exist.

## Plan outlet coverage
Plan Excel reader exposes `#WS/DS` and `#HORECA Outlet` columns even when blank. For each employee, the backend sums the outlet targets of distinct plan commune rows in their active uploaded workbook (only rows with at least one scheduled date in the current plan storage). Actual Plan = valid check-ins / outlet target * 100. This is an *overall assigned-outlet coverage ratio*, NOT a daily target success percentage. A visit outside the planned commune remains a visit but its wrong-plan flag remains.

Caution: The legacy plan storage only saves scheduled commune rows. If your workbook includes unscheduled commune rows, the denominator may be incomplete until the parser persists them in the plan master. The dashboard displays '—' where no target counts are available. Repeated visits at one outlet cannot be proven unique without outlet ID; the numerator is distinct visit events, not unique outlets.

## Reason classification
A missing-report reply contains a reason only if it explains a cause. Outlet forms/photos/locations alone do not count. Known connectivity or field-work explanations yield short meaningful sentences. Ambiguous replies require review instead of being auto-acknowledged. For optional local Hermes LLM classification, install/run a compatible Ollama service separately and set `OLLAMA_URL=http://host.docker.internal:11434` and `OLLAMA_REASON_MODEL=hermes3` in `.env`. The launcher does not install, pull, or start Hermes automatically; when Ollama is unavailable, conservative rules are used.

## Limitations / verification
Compiled Python and checked embedded JavaScript syntax. Unit tests run where environment dependencies permit. No live Telegram/PostgreSQL/ngrok/E2E verification here; Docker needs to rebuild on the user's machine. Review actual production data after startup. No historical replay of unrelated Morning Talk text is implied.

## Reason Summary page
The authenticated admin sidebar now includes **Reason Summary**, with counts of replies, meaningful reasons, non-reasons and manual-review items, plus CSV export. Every processed textual missing-report reply is audited with its original text, category, condensed reason and classification method. Existing historic Telegram replies were not ingested retroactively; only new replies after upgrade appear here.

## Hermes startup from the single BAT file
`RUN_NGROK_DASHBOARD.bat` runs `START_HERMES_IF_AVAILABLE.ps1` automatically before Docker. If Windows Ollama is installed, this starts it if needed. The `.env` sets `OLLAMA_URL=http://host.docker.internal:11434`, `OLLAMA_REASON_MODEL=hermes3`, `AUTO_START_HERMES=1`, and `AUTO_PULL_HERMES=0`. This does **not** automatically download a large AI model. To permit automatic download on the next single-command launch, change `AUTO_PULL_HERMES=1`; it may need several GB and an internet connection. If not installed, pending ambiguous explanations stay flagged as Needs review and transparent deterministic rules handle clear cases.

## Current bundled plan caveat
As checked against the bundled `data/Plan_SO.xlsx`, employee 90026071 (MOEUN Ratana) currently has four commune rows: 120704, 120713, 120714, 120712. Commune 120711 is not present. The #WS/DS and #HORECA Outlet values are blank in those rows. To display the example denominator 456 outlets, upload a revised complete Plan Sale Excel containing all five communes and valid numeric totals. The app will not invent missing commune targets.
