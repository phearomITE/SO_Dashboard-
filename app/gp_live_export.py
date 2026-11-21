"""Instant Report GP workbook generator backed directly by live PostgreSQL data (no Telegram scraping)."""
from __future__ import annotations

import re
from collections import defaultdict
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import db
from app.db import Event, Group, Leave, Roster, SilverVisit
from app.gp_legacy.banner_tracker.groups import DEALER_TO_REGION, normalized_dealer
from app.miss_report import EXCLUDED_CHAT_IDS, _is_test_group
from app.rules import is_form, link_visits

OUTLET_TYPES = ("WS", "Retail", "DS", "ON")
REPORT_METRICS = [
    ("emergency_outlet", "Emergency Outlet", 12),
    ("attacking_stock", "Attacking Stock Outlet", 16),
    ("tour_program_outlet", "Tour Program Outlet", 20),
    ("key_wholesale", "Key Wholesale", 24),
    ("water", "Water", 28),
    ("c_cola", "C-Cola", 32),
    ("ize_pet_300ml", "Ize PET 300mL", 36),
    ("ize_pet_1500ml", "Ize PET 1500mL", 40),
]

THIN_BORDER = Border(
    left=Side(style="thin", color="B7B7B7"),
    right=Side(style="thin", color="B7B7B7"),
    top=Side(style="thin", color="B7B7B7"),
    bottom=Side(style="thin", color="B7B7B7"),
)


def _norm_outlet_type(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    if raw in ("WS", "WHOLESALE", "W/S"):
        return "WS"
    if raw in ("DS", "DISTRIBUTOR"):
        return "DS"
    if raw in ("ON", "ON-PREMISE", "ON PREMISE", "HORECA"):
        return "ON"
    return "Retail"


_LEAKED_LABEL_PREFIXES = (
    "attacking stock", "tour program", "key wholesale", "emergency outlet",
    "water", "c-cola", "c cola", "ize pet", "product display", "remark",
    "program", "posm", "banner", "outlet type", "outlet name", "dealer", "phone",
)


def _metric_qty(value: object) -> int:
    """Binary outlet-count conversion (0 or 1) matching GP Execution Purchase outlet By SKU."""
    text_val = str(value or "").strip().strip("*").strip()
    if not text_val:
        return 0
    low = text_val.casefold()
    if low in ("-", "—", "–", "no", "n", "o", "none", "nil", "null", "n/a", "na", "false"):
        return 0
    # Guard against legacy rows where a blank field accidentally captured the next form label
    if ":" in text_val or "៖" in text_val:
        return 0
    if any(low.startswith(prefix) for prefix in _LEAKED_LABEL_PREFIXES):
        return 0
    m = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", text_val)
    if m:
        try:
            return 1 if float(text_val) > 0 else 0
        except ValueError:
            return 0
    return 1


def _parse_form_fields(text_val: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in str(text_val or "").splitlines():
        m = re.match(r"^\s*\*?\s*([^:៖：]+)[:៖：](.*)$", line)
        if m:
            k = " ".join(m[1].casefold().split())
            fields.setdefault(k, m[2].strip().strip("*").strip())
    return fields


def _collect_day_visits(conn: Session, target_day: date) -> list[dict]:
    """Combine silver_so_visit_v1 and any un-synced live so_events_v1 visits for target_day."""
    excluded_chats = set(EXCLUDED_CHAT_IDS)
    group_by_id: dict[int, Group] = {}
    for g in conn.scalars(select(Group)).all():
        group_by_id[int(g.id)] = g
        if _is_test_group(g.title, g.code):
            excluded_chats.add(int(g.id))

    silver_rows = conn.scalars(
        select(SilverVisit).where(SilverVisit.report_date == target_day)
    ).all()

    seen_event_ids: set[int] = set()
    visits: list[dict] = []
    for v in silver_rows:
        chat_id = int(v.chat_id or 0)
        if chat_id in excluded_chats or _is_test_group(v.group_name):
            continue
        pos = str(v.position or "SO").strip().upper()
        if pos not in ("SO", "SS"):
            continue
        if v.source_event_id:
            seen_event_ids.add(int(v.source_event_id))
        reg = str(v.region or "").strip().upper()
        dlr = normalized_dealer(v.dealer)
        if not reg and dlr in DEALER_TO_REGION:
            reg = DEALER_TO_REGION[dlr]
        visits.append({
            "region": reg,
            "dealer": dlr,
            "district": str(v.actual_district or "").strip(),
            "commune": str(getattr(v, "actual_commune_code", None) or v.actual_commune or "").strip(),
            "outlet_type": _norm_outlet_type(v.outlet_type),
            "emergency_outlet": _metric_qty(v.emergency_outlet),
            "attacking_stock": _metric_qty(v.attacking_stock),
            "tour_program_outlet": _metric_qty(v.tour_program_outlet),
            "key_wholesale": _metric_qty(v.key_wholesale),
            "water": _metric_qty(v.water),
            "c_cola": _metric_qty(v.c_cola),
            "ize_pet_300ml": _metric_qty(v.ize_pet_300ml),
            "ize_pet_1500ml": _metric_qty(v.ize_pet_1500ml),
        })

    # Include live events from today/day that have not yet landed in silver_so_visit_v1
    day_start = datetime.combine(target_day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    live_events = conn.scalars(
        select(Event).where(Event.at >= day_start, Event.at < day_end).order_by(Event.at, Event.mid)
    ).all()
    if live_events:
        roster_by_uid: dict[int, Roster] = {}
        for r in conn.scalars(select(Roster).where(Roster.active == 1)).all():
            if r.uid:
                roster_by_uid.setdefault(int(r.uid), r)
        for item in link_visits(live_events):
            ev = item["event"]
            pin = item["pin"]
            if not pin or int(ev.id) in seen_event_ids:
                continue
            chat_id = int(ev.chat or 0)
            grp = group_by_id.get(chat_id)
            if chat_id in excluded_chats or (grp and _is_test_group(grp.title, grp.code)):
                continue
            r_obj = roster_by_uid.get(int(ev.uid or 0))
            pos = str((r_obj.position if r_obj else None) or "SO").strip().upper()
            if pos not in ("SO", "SS"):
                continue
            fields = _parse_form_fields(item.get("text") or "")
            dlr = normalized_dealer((r_obj.dealer if r_obj else "") or fields.get("dealer", ""))
            reg = str((r_obj.region if r_obj else "") or "").strip().upper()
            if not reg and dlr in DEALER_TO_REGION:
                reg = DEALER_TO_REGION[dlr]
            if not reg and grp and grp.code and "-" in grp.code:
                reg = grp.code.split("-", 1)[0].strip().upper()
            pin_data = pin.data if isinstance(pin.data, dict) else {}
            geo = pin_data.get("geo") if isinstance(pin_data.get("geo"), dict) else {}
            visits.append({
                "region": reg,
                "dealer": dlr,
                "district": str(geo.get("district") or "").strip(),
                "commune": str(geo.get("commune_code") or geo.get("commune") or "").strip(),
                "outlet_type": _norm_outlet_type(fields.get("outlet type")),
                "emergency_outlet": _metric_qty(fields.get("emergency outlet")),
                "attacking_stock": _metric_qty(fields.get("attacking stock outlet") or fields.get("attacking stock")),
                "tour_program_outlet": _metric_qty(fields.get("tour program outlet") or fields.get("tour program")),
                "key_wholesale": _metric_qty(fields.get("key wholesale")),
                "water": _metric_qty(fields.get("water")),
                "c_cola": _metric_qty(fields.get("c-cola") or fields.get("c cola")),
                "ize_pet_300ml": _metric_qty(fields.get("ize pet 300ml")),
                "ize_pet_1500ml": _metric_qty(fields.get("ize pet 1500ml")),
            })
    return visits


def _safe_write(sheet, coord_or_row, col_or_val, val=None):
    if val is None and isinstance(coord_or_row, str):
        cell = sheet[coord_or_row]
        if not isinstance(cell, MergedCell):
            cell.value = col_or_val
    else:
        cell = sheet.cell(coord_or_row, col_or_val)
        if not isinstance(cell, MergedCell):
            cell.value = val


def _setup_headers(sheet, row1_label: str, is_dealer: bool, day: date) -> None:
    from openpyxl.utils import get_column_letter

    # Unmerge any stale merges so header & data cells are safely writable
    for rng in list(sheet.merged_cells.ranges):
        sheet.unmerge_cells(str(rng))

    # Clear header block B2:AR9 cleanly
    for r in range(2, 10):
        for c in range(2, 45):
            cell = sheet.cell(r, c)
            cell.value = None
            cell.fill = PatternFill(fill_type=None)
            cell.border = THIN_BORDER if r >= 5 else Border()

    _safe_write(sheet, "B2", "Region:R1 to R8")
    _safe_write(sheet, "B3", f"Date:{day:%d.%m.%Y}")
    _safe_write(sheet, "B4", "#Dealer:65")
    for coord in ("B2", "B3", "B4"):
        sheet[coord].font = Font(name="Calibri", size=11, bold=True, color="000000")

    _safe_write(sheet, "B5", "Daily Sales Report by Dealer" if is_dealer else "Daily Sales Report")
    _safe_write(sheet, "AR5", "REMARK")
    _safe_write(sheet, "B6", row1_label)
    if is_dealer:
        _safe_write(sheet, "C6", "DEALER")
    _safe_write(sheet, "D6", "Sales Team")
    _safe_write(sheet, "H6", "Location")
    _safe_write(sheet, "H7", "Actual Visit Location")
    _safe_write(sheet, "J7", "# Outlets Visit\nBased on Report")
    _safe_write(sheet, "K7", "Average #\nof Outlets Visit")
    _safe_write(sheet, "L6", "Execution")
    _safe_write(sheet, "L7", "Purchase outlet By SKU")

    # Apply exact merges matching the approved Daily Sales Report layout
    sheet.merge_cells("B5:AQ5")
    sheet.merge_cells("AR5:AR9")
    if is_dealer:
        sheet.merge_cells("B6:B9")
        sheet.merge_cells("C6:C9")
    else:
        sheet.merge_cells("B6:C9")
    sheet.merge_cells("D6:G7")
    sheet.merge_cells("H6:K6")
    sheet.merge_cells("H7:I7")
    sheet.merge_cells("J7:J9")
    sheet.merge_cells("K7:K9")
    sheet.merge_cells("L6:AQ6")
    sheet.merge_cells("L7:AQ7")
    for col_let in ("D", "E", "F", "G", "H", "I"):
        sheet.merge_cells(f"{col_let}8:{col_let}9")
    for _key, _label, start_col in REPORT_METRICS:
        sheet.merge_cells(start_row=8, start_column=start_col, end_row=8, end_column=start_col + 3)

    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Row 5: Title blue banner + Peach REMARK
    for col in range(2, 44):
        c = sheet.cell(5, col)
        c.fill = PatternFill("solid", fgColor="D9E2F3")
        c.border = THIN_BORDER
    sheet["B5"].font = Font(name="Calibri", size=18, bold=True, italic=True, color="FF0000")
    sheet["B5"].alignment = center

    for r in range(5, 10):
        c = sheet.cell(r, 44)
        c.fill = PatternFill("solid", fgColor="FCE4D6")
        c.border = THIN_BORDER
    sheet["AR5"].font = Font(name="Calibri", size=10, bold=True, color="000000")
    sheet["AR5"].alignment = center

    # Gray section headers: H6:K6, L6:AQ6, L7:AQ7
    for r, c1, c2 in [(6, 8, 11), (6, 12, 43), (7, 12, 43)]:
        for c in range(c1, c2 + 1):
            sheet.cell(r, c).fill = PatternFill("solid", fgColor="E7E6E6")

    for coord, sz, italic in [
        ("B6", 13, False), ("C6", 13, False), ("D6", 13, False),
        ("H6", 13, False), ("H7", 11, False), ("J7", 10, False), ("K7", 10, False),
        ("L6", 16, True), ("L7", 14, True),
    ]:
        cell = sheet[coord]
        cell.font = Font(name="Calibri", size=sz, bold=True, italic=italic, color="000000")
        cell.alignment = center

    for col, title in [(4, "SO"), (5, "SS"), (6, "AL"), (7, "Active"), (8, "District"), (9, "Commune")]:
        c = sheet.cell(8, col)
        c.value = title
        c.font = Font(name="Calibri", size=12, bold=True, color="FF0000" if col == 6 else ("00B050" if col == 7 else "000000"))
        c.alignment = center
        c.fill = PatternFill("solid", fgColor="FFFFFF")

    for idx, (_key, label, start_col) in enumerate(REPORT_METRICS):
        color = "00B0F0" if idx < 4 else "FF0000"
        h_cell = sheet.cell(8, start_col)
        h_cell.value = label
        h_cell.fill = PatternFill("solid", fgColor="FFFFFF")
        h_cell.font = Font(name="Calibri", size=11, bold=True, color=color)
        h_cell.alignment = center
        for i, ot in enumerate(OUTLET_TYPES):
            sub = sheet.cell(9, start_col + i)
            sub.value = ot
            sub.fill = PatternFill("solid", fgColor="FFFFFF")
            sub.font = Font(name="Calibri", size=10, bold=True, color="000000")
            sub.alignment = center

    # Row heights & column widths so nothing clips in Excel or Dashboard preview
    sheet.row_dimensions[5].height = 30
    sheet.row_dimensions[6].height = 24
    sheet.row_dimensions[7].height = 34
    sheet.row_dimensions[8].height = 22
    sheet.row_dimensions[9].height = 20
    widths = {2: 14, 3: 11, 4: 9, 5: 9, 6: 9, 7: 10, 8: 11, 9: 11, 10: 15, 11: 15, 44: 18}
    for col in range(12, 44):
        widths[col] = 7.5
    for col_idx, w in widths.items():
        sheet.column_dimensions[get_column_letter(col_idx)].width = w


def _collect_range_visits(conn: Session, start_day: date, end_day: date) -> list[dict]:
    visits: list[dict] = []
    cur = start_day
    while cur <= end_day:
        visits.extend(_collect_day_visits(conn, cur))
        cur += timedelta(days=1)
    return visits


def _is_ready_gp_template(wb) -> bool:
    """Return True when the loaded workbook already has the full 2-sheet GP template design."""
    if "Report GP" not in wb.sheetnames or "Report with Dealer" not in wb.sheetnames:
        return False
    report = wb["Report GP"]
    dealer_ws = wb["Report with Dealer"]
    for sheet in (report, dealer_ws):
        for col in (12, 16, 20, 24, 28, 32, 36, 40):
            if [str(sheet.cell(9, col + i).value or "").strip() for i in range(4)] != list(OUTLET_TYPES):
                return False
    dealers = [dealer_ws.cell(r, 3).value for r in range(10, 75)]
    if len(set(dealers)) != 65 or not all(dealers):
        return False
    return True


def _write_gp_workbook_for_day(conn: Session, target_day: date, template_path: Path, output_path: Path, end_day: date | None = None) -> None:
    win_end = end_day or target_day
    visits = _collect_day_visits(conn, target_day) if win_end == target_day else _collect_range_visits(conn, target_day, win_end)

    roster_rows = conn.scalars(select(Roster).where(Roster.active == 1)).all()
    day_start = datetime.combine(target_day, datetime.min.time())
    day_end = datetime.combine(win_end + timedelta(days=1), datetime.min.time())
    leave_rows = conn.scalars(
        select(Leave).where(Leave.start < day_end, Leave.end > day_start)
    ).all()
    al_emps = {str(lv.employee or "").strip() for lv in leave_rows if lv.employee}
    al_uids = {int(lv.uid) for lv in leave_rows if lv.uid}

    reg_so: dict[str, int] = defaultdict(int)
    reg_ss: dict[str, int] = defaultdict(int)
    reg_al: dict[str, int] = defaultdict(int)
    dlr_so: dict[str, int] = defaultdict(int)
    dlr_ss: dict[str, int] = defaultdict(int)
    dlr_al: dict[str, int] = defaultdict(int)

    for r in roster_rows:
        pos = str(r.position or "SO").strip().upper()
        if pos not in ("SO", "SS"):
            continue
        reg = str(r.region or "").strip().upper()
        dlr = normalized_dealer(r.dealer)
        if not reg and dlr in DEALER_TO_REGION:
            reg = DEALER_TO_REGION[dlr]
        on_al = (str(r.employee or "").strip() in al_emps) or (r.uid and int(r.uid) in al_uids)
        if pos == "SO":
            reg_so[reg] += 1
            if dlr:
                dlr_so[dlr] += 1
        else:
            reg_ss[reg] += 1
            if dlr:
                dlr_ss[dlr] += 1
        if on_al:
            reg_al[reg] += 1
            if dlr:
                dlr_al[dlr] += 1

    # Aggregate visits by region & dealer
    reg_visits: dict[str, int] = defaultdict(int)
    reg_districts: dict[str, set[str]] = defaultdict(set)
    reg_communes: dict[str, set[str]] = defaultdict(set)
    reg_metrics: dict[tuple[str, str, str], float] = defaultdict(float)

    dlr_visits: dict[str, int] = defaultdict(int)
    dlr_districts: dict[str, set[str]] = defaultdict(set)
    dlr_communes: dict[str, set[str]] = defaultdict(set)
    dlr_metrics: dict[tuple[str, str, str], float] = defaultdict(float)

    all_districts: set[str] = set()
    all_communes: set[str] = set()

    for v in visits:
        reg = v["region"]
        dlr = v["dealer"]
        ot = v["outlet_type"]
        if reg:
            reg_visits[reg] += 1
            if v["district"]:
                reg_districts[reg].add(v["district"])
                all_districts.add(v["district"])
            if v["commune"]:
                reg_communes[reg].add(v["commune"])
                all_communes.add(v["commune"])
            for mkey, _, _ in REPORT_METRICS:
                reg_metrics[(reg, mkey, ot)] += float(v[mkey])
        if dlr:
            dlr_visits[dlr] += 1
            if v["district"]:
                dlr_districts[dlr].add(v["district"])
            if v["commune"]:
                dlr_communes[dlr].add(v["commune"])
            for mkey, _, _ in REPORT_METRICS:
                dlr_metrics[(dlr, mkey, ot)] += float(v[mkey])

    date_label = f"Date:{target_day:%d.%m.%Y}" if win_end == target_day else f"Date:{target_day:%d.%m.%Y} - {win_end:%d.%m.%Y}"

    if template_path and Path(template_path).exists():
        wb = load_workbook(template_path)
        if _is_ready_gp_template(wb):
            # Preserve 100% of the uploaded/bundled template's exact design, styles, merges, widths & heights.
            for title in list(wb.sheetnames):
                if title not in ("Report GP", "Report with Dealer"):
                    del wb[title]
            report = wb["Report GP"]
            dealer_ws = wb["Report with Dealer"]
            _safe_write(report, "B3", date_label)
            _safe_write(dealer_ws, "B3", date_label)

            # Populate Report GP (R1..R8 in rows 10..17, Grand Total in row 18)
            for idx in range(8):
                row_num = 10 + idx
                reg = str(report.cell(row_num, 2).value or f"R{idx + 1}").strip().upper()
                _safe_write(report, row_num, 4, reg_so[reg])
                _safe_write(report, row_num, 5, reg_ss[reg])
                _safe_write(report, row_num, 6, reg_al[reg])
                _safe_write(report, row_num, 7, f"=D{row_num}+E{row_num}-F{row_num}")
                _safe_write(report, row_num, 8, len(reg_districts[reg]))
                _safe_write(report, row_num, 9, len(reg_communes[reg]))
                _safe_write(report, row_num, 10, reg_visits[reg])
                _safe_write(report, row_num, 11, f"=IFERROR(J{row_num}/G{row_num},0)")
                for mkey, _, start_col in REPORT_METRICS:
                    for i, ot in enumerate(OUTLET_TYPES):
                        val = reg_metrics[(reg, mkey, ot)]
                        _safe_write(report, row_num, start_col + i, int(val) if val == int(val) else round(val, 2))
            _safe_write(report, 18, 8, len(all_districts))
            _safe_write(report, 18, 9, len(all_communes))
            _safe_write(report, 18, 11, "=IFERROR(J18/G18,0)")

            # Populate Report with Dealer (65 dealers in rows 10..74, Grand Total in row 75)
            for row_num in range(10, 75):
                dlr = normalized_dealer(dealer_ws.cell(row_num, 3).value)
                _safe_write(dealer_ws, row_num, 4, dlr_so[dlr])
                _safe_write(dealer_ws, row_num, 5, dlr_ss[dlr])
                _safe_write(dealer_ws, row_num, 6, dlr_al[dlr])
                _safe_write(dealer_ws, row_num, 7, f"=D{row_num}+E{row_num}-F{row_num}")
                _safe_write(dealer_ws, row_num, 8, len(dlr_districts[dlr]))
                _safe_write(dealer_ws, row_num, 9, len(dlr_communes[dlr]))
                _safe_write(dealer_ws, row_num, 10, dlr_visits[dlr])
                _safe_write(dealer_ws, row_num, 11, f"=IFERROR(J{row_num}/G{row_num},0)")
                for mkey, _, start_col in REPORT_METRICS:
                    for i, ot in enumerate(OUTLET_TYPES):
                        val = dlr_metrics[(dlr, mkey, ot)]
                        _safe_write(dealer_ws, row_num, start_col + i, int(val) if val == int(val) else round(val, 2))
            _safe_write(dealer_ws, 75, 8, len(all_districts))
            _safe_write(dealer_ws, 75, 9, len(all_communes))
            _safe_write(dealer_ws, 75, 11, "=IFERROR(J75/G75,0)")

            output_path.parent.mkdir(parents=True, exist_ok=True)
            wb.save(output_path)
            wb.close()
            return

        if "Report GP" not in wb.sheetnames:
            wb.active.title = "Report GP"
        for title in list(wb.sheetnames):
            if title != "Report GP":
                del wb[title]
    else:
        wb = Workbook()
        wb.active.title = "Report GP"

    report = wb["Report GP"]
    _setup_headers(report, "REGION", False, target_day)

    center_align = Alignment(horizontal="center", vertical="center")
    # Clear & write R1..R8 (rows 10..17)
    for idx in range(8):
        row_num = 10 + idx
        reg = f"R{idx + 1}"
        report.cell(row_num, 2, reg)
        report.merge_cells(start_row=row_num, start_column=2, end_row=row_num, end_column=3)
        report.cell(row_num, 4, reg_so[reg])
        report.cell(row_num, 5, reg_ss[reg])
        report.cell(row_num, 6, reg_al[reg])
        report.cell(row_num, 7, f"=D{row_num}+E{row_num}-F{row_num}")
        report.cell(row_num, 8, len(reg_districts[reg]))
        report.cell(row_num, 9, len(reg_communes[reg]))
        report.cell(row_num, 10, reg_visits[reg])
        report.cell(row_num, 11, f"=IFERROR(J{row_num}/G{row_num},0)")
        for mkey, _, start_col in REPORT_METRICS:
            for i, ot in enumerate(OUTLET_TYPES):
                val = reg_metrics[(reg, mkey, ot)]
                report.cell(row_num, start_col + i, int(val) if val == int(val) else round(val, 2))
        for col in range(2, 45):
            cell = report.cell(row_num, col)
            cell.border = THIN_BORDER
            cell.fill = PatternFill("solid", fgColor="FFFFFF")
            cell.font = Font(name="Calibri", size=10, bold=(col == 2), color="000000")
            cell.alignment = center_align
            if col == 11:
                cell.number_format = "0.00"

    # Grand total row 18
    report.cell(18, 2, "Grand Total")
    report.merge_cells("B18:C18")
    for col in range(4, 8):
        col_let = report.cell(10, col).column_letter
        report.cell(18, col, f"=SUM({col_let}10:{col_let}17)")
    report.cell(18, 8, len(all_districts))
    report.cell(18, 9, len(all_communes))
    report.cell(18, 10, "=SUM(J10:J17)")
    report.cell(18, 11, "=IFERROR(J18/G18,0)")
    for col in range(12, 44):
        col_let = report.cell(10, col).column_letter
        report.cell(18, col, f"=SUM({col_let}10:{col_let}17)")
    for col in range(2, 45):
        cell = report.cell(18, col)
        cell.border = THIN_BORDER
        cell.fill = PatternFill("solid", fgColor="70AD47" if col == 7 else "FFE699")
        cell.font = Font(name="Calibri", size=10, bold=True, color="FF0000" if col == 6 else "000000")
        cell.alignment = center_align
        if col == 11:
            cell.number_format = "0.00"

    # Create 'Report with Dealer' sheet (65 dealers rows 10..74, total row 75)
    dealer_ws = wb.copy_worksheet(report)
    dealer_ws.title = "Report with Dealer"
    _setup_headers(dealer_ws, "REGION", True, target_day)

    ordered_dealers = sorted(DEALER_TO_REGION.items(), key=lambda item: (item[1], item[0]))
    for idx, (dlr, reg) in enumerate(ordered_dealers):
        row_num = 10 + idx
        dealer_ws.cell(row_num, 2, reg)
        dealer_ws.cell(row_num, 3, dlr)
        dealer_ws.cell(row_num, 4, dlr_so[dlr])
        dealer_ws.cell(row_num, 5, dlr_ss[dlr])
        dealer_ws.cell(row_num, 6, dlr_al[dlr])
        dealer_ws.cell(row_num, 7, f"=D{row_num}+E{row_num}-F{row_num}")
        dealer_ws.cell(row_num, 8, len(dlr_districts[dlr]))
        dealer_ws.cell(row_num, 9, len(dlr_communes[dlr]))
        dealer_ws.cell(row_num, 10, dlr_visits[dlr])
        dealer_ws.cell(row_num, 11, f"=IFERROR(J{row_num}/G{row_num},0)")
        for mkey, _, start_col in REPORT_METRICS:
            for i, ot in enumerate(OUTLET_TYPES):
                val = dlr_metrics[(dlr, mkey, ot)]
                dealer_ws.cell(row_num, start_col + i, int(val) if val == int(val) else round(val, 2))
        for col in range(2, 45):
            cell = dealer_ws.cell(row_num, col)
            cell.border = THIN_BORDER
            cell.fill = PatternFill("solid", fgColor="FFFFFF")
            cell.font = Font(name="Calibri", size=10, bold=(col in (2, 3)), color="000000")
            cell.alignment = center_align
            if col == 11:
                cell.number_format = "0.00"

    total_row = 75
    dealer_ws.cell(total_row, 2, "Grand Total")
    dealer_ws.merge_cells(f"B{total_row}:C{total_row}")
    for col in range(4, 8):
        col_let = dealer_ws.cell(10, col).column_letter
        dealer_ws.cell(total_row, col, f"=SUM({col_let}10:{col_let}74)")
    dealer_ws.cell(total_row, 8, len(all_districts))
    dealer_ws.cell(total_row, 9, len(all_communes))
    dealer_ws.cell(total_row, 10, "=SUM(J10:J74)")
    dealer_ws.cell(total_row, 11, f"=IFERROR(J{total_row}/G{total_row},0)")
    for col in range(12, 44):
        col_let = dealer_ws.cell(10, col).column_letter
        dealer_ws.cell(total_row, col, f"=SUM({col_let}10:{col_let}74)")
    for col in range(2, 45):
        cell = dealer_ws.cell(total_row, col)
        cell.border = THIN_BORDER
        cell.fill = PatternFill("solid", fgColor="70AD47" if col == 7 else "FFE699")
        cell.font = Font(name="Calibri", size=10, bold=True, color="FF0000" if col == 6 else "000000")
        cell.alignment = center_align
        if col == 11:
            cell.number_format = "0.00"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    wb.close()


def export_live_gp_reports(start_date: str, end_date: str, template_path: Path, output_dir: Path) -> list[Path]:
    """Generate daily Report GP workbooks from live PostgreSQL data without Telegram scraping."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        raise ValueError("GP end date must be on or after start date.")
    days = (end - start).days + 1
    if days > 31:
        raise ValueError("Choose a date range of 31 days or fewer.")
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with Session(db.engine) as conn:
        for offset in range(days):
            day = start + timedelta(days=offset)
            out_path = output_dir / f"report_gp_{day.isoformat()}.xlsx"
            _write_gp_workbook_for_day(conn, day, template_path, out_path)
            paths.append(out_path)
    return paths


def export_live_gp_summary(start_date: str, end_date: str, template_path: Path, output_path: Path) -> Path:
    """Generate one combined live Report GP workbook for start_date..end_date (fast, no scraping)."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if end < start:
        end = start
    if (end - start).days > 31:
        start = end - timedelta(days=31)
    with Session(db.engine) as conn:
        _write_gp_workbook_for_day(conn, start, template_path, output_path, end_day=end)
    return output_path
