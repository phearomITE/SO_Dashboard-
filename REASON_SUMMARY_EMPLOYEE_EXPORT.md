# Employee reason summary export

Reason Summary opens with a By employee table. Each row contains Name, Employee ID, Region, Dealer, Total Outlets and Reason Summary. Reply details remain on a separate view. Find employee searches by name or ID; the Excel export includes matching employees. Date, region, dealer and language filters also apply to the export.

Select Export by employee · Excel to download the designed single-sheet workbook. It has a navy header, alternating rows, wrapped Khmer/English summaries, text Employee IDs, a frozen identity/header area, filters and an A4 landscape print layout. The original per-reply CSV remains available separately.

Total Outlets means recorded outlet check-ins from silver_so_visit_v1 on the selected date for employees who have reason replies in the selected scope. Repeated visits count separately. It is not a unique-outlet census. The header's total sums the employee rows included in the export, rather than all employees in the organisation.

Employee identity uses a positive Telegram ID where available, then an unambiguous Employee ID mapping. Same display names are not merged. Multiple reasons are combined in source order and duplicate summaries removed. Non-reasons are labelled as no stated reason; ambiguous explanations retain a Needs review label. Original replies are unchanged.

Source queries use two grouped PostgreSQL reads, avoiding one query per employee. The grouped preview is cached in Redis for 10 seconds and invalidated by Hermes analysis updates. Downloads query current source data and require an authenticated admin session.

Verification on 13 November: 41 reply records produced 35 employee rows and 669 recorded outlet check-ins. Six requested columns, Unicode text, identifier formatting, row counts and summed outlet totals were checked. Single-employee search and unauthenticated export rejection were verified. Four aggregation/export tests and the page language/action DOM test passed.
