from __future__ import annotations

import json
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .commune import commune_identity, default_commune_resolver, district_identity
from .database import Database
from .groups import EXPECTED_DEALERS
from .outlet_visit import METRICS, _binary_quantity
from .staff_directory import load_staff_directory, normalize_telegram_id

NAVY = "23364A"
GRAY = "727272"
WHITE = "FFFFFF"
LIGHT_BLUE = "D9EAF7"
LIGHT_GREEN = "C6EFCE"
WEEK_HEADER = "FCE4D6"
GRID = "B7B7B7"

# Telegram export rule: exclude only the five guideline/reference accounts;
# every other Telegram account remains eligible.
from .telegram_identity import is_excluded_telegram_name, normalize_telegram_name


def _telegram_name_key(value) -> str:
    return normalize_telegram_name(value)


def _is_exportable_telegram_name(value) -> bool:
    return not is_excluded_telegram_name(value)


def _excel_date(value):
    if isinstance(value, (date, datetime)):
        return value.date() if isinstance(value, datetime) else value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _identity(row: dict) -> tuple[str, str, str]:
    """Return clean Telegram Name, Username, and ID for export."""
    name = str(row.get("sender_name") or "").strip()
    username = str(row.get("sender_username") or "").strip().lstrip("@")
    sender_id = normalize_telegram_id(row.get("sender_id"))
    return name, username, sender_id


def _mapped_staff_name(staff_directory, telegram_id: str, telegram_name: str) -> str:
    """Return the HR/master-list name for an ID, with a safe Telegram fallback."""
    member = (staff_directory or {}).get(normalize_telegram_id(telegram_id))
    return (member.name if member and member.name else telegram_name).strip()


def _data_sheet(ws, headers, rows):
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A2"
    ws.append(headers)
    for row in rows:
        ws.append(row)
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, ws.max_row)}"
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    # Avoid per-cell body styling on the two large detail sheets. Applying
    # borders/alignment to hundreds of thousands of cells dominated export
    # time while adding little value. Header styling, filters, widths, and date
    # formats keep the sheets readable and make the save substantially faster.
    for cell in ws["A"][1:]:
        cell.number_format = "dd/mm/yyyy"
    widths = [
        12, 10, 12, 24, 16, 14, 18, 18, 16, 18, 12, 12,
        18, 18, 12, 12, 18, 18, 18, 22, 60,
    ]
    for index in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(index)].width = (
            widths[index - 1] if index <= len(widths) else 16
        )
    for header, width in {
        "Outlet Name": 24, "Remark Issues": 60, "District": 22,
        "Commune": 22, "Telegram Name": 28, "Username": 20,
        "Telegram ID": 18,
    }.items():
        if header in headers:
            ws.column_dimensions[get_column_letter(headers.index(header) + 1)].width = width
    ws.row_dimensions[1].height = 32


def _summary_sheet(ws, start_date: str, end_date: str, source_row_count: int):
    metric_labels = [label for _key, label, _aliases in METRICS]
    headers = ["Region", "Dealer", *metric_labels, "Total Outlet"]
    last_col = len(headers)
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "C6"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    ws["A1"] = "KHMER BEVERAGES CO,"
    ws["A1"].font = Font(size=20, bold=True)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    ws["A2"] = (
        f"Tracking Outlet from {datetime.strptime(start_date, '%Y-%m-%d'):%d/%m/%Y} "
        f"to {datetime.strptime(end_date, '%Y-%m-%d'):%d/%m/%Y}"
    )
    ws["A2"].font = Font(size=15, bold=True)
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=last_col)
    ws["A3"] = (
        f"DATE: From {datetime.strptime(start_date, '%Y-%m-%d'):%d/%m/%Y} ==> "
        f"{datetime.strptime(end_date, '%Y-%m-%d'):%d/%m/%Y}"
    )
    ws["A3"].font = Font(size=11, bold=True)
    for col, value in enumerate(headers, 1):
        cell = ws.cell(5, col, value)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[5].height = 48

    source_cols = {
        "Emergency Outlet": "H", "Attacking Stock": "I", "Tour Program": "J",
        "Key Wholesale": "K", "Water": "L", "C-Cola": "M",
        "Ize PET 300mL": "N", "Ize PET 1500mL": "O",
    }
    source_last_row = max(2, int(source_row_count) + 1)
    row = 6
    region_total_rows = []
    for region, dealers in EXPECTED_DEALERS.items():
        first = row
        for dealer in dealers:
            ws.cell(row, 1, region if row == first else None)
            ws.cell(row, 2, dealer)
            for metric_index, label in enumerate(metric_labels, 3):
                source_col = source_cols[label]
                ws.cell(
                    row, metric_index,
                    f'=SUMIFS(\'Outlet_Visit\'!${source_col}$2:${source_col}${source_last_row},'
                    f'\'Outlet_Visit\'!$C$2:$C${source_last_row},$B{row})',
                )
            ws.cell(
                row, last_col,
                f'=COUNTIFS(\'Outlet_Visit\'!$C$2:$C${source_last_row},$B{row},'
                f'\'Outlet_Visit\'!$P$2:$P${source_last_row},"<>",'
                f'\'Outlet_Visit\'!$Q$2:$Q${source_last_row},"<>")',
            )
            row += 1
        total_row = row
        region_total_rows.append(total_row)
        ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=2)
        ws.cell(total_row, 1, f"TOTAL {region}")
        for col in range(3, last_col + 1):
            letter = get_column_letter(col)
            ws.cell(total_row, col, f"=SUM({letter}{first}:{letter}{row - 1})")
        for cell in ws[total_row]:
            cell.fill = PatternFill("solid", fgColor=GRAY)
            cell.font = Font(color=WHITE, bold=True)
            cell.alignment = Alignment(horizontal="center")
        if row - first > 1:
            ws.merge_cells(start_row=first, start_column=1, end_row=row - 1, end_column=1)
        ws.cell(first, 1).alignment = Alignment(horizontal="center", vertical="center")
        row += 1

    grand = row
    ws.merge_cells(start_row=grand, start_column=1, end_row=grand, end_column=2)
    ws.cell(grand, 1, "GRAND TOTAL")
    for col in range(3, last_col + 1):
        letter = get_column_letter(col)
        ws.cell(grand, col, "=" + "+".join(f"{letter}{r}" for r in region_total_rows))
    for cell in ws[grand]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center")
    thin = Side(style="thin", color=GRID)
    for cells in ws.iter_rows(min_row=5, max_row=grand, min_col=1, max_col=last_col):
        for cell in cells:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            if cell.row > 5 and cell.fill.fill_type is None:
                cell.alignment = Alignment(horizontal="center", vertical="center")
    for col, width in {"A": 12, "B": 14, "C": 17, "D": 17, "E": 15, "F": 17,
                       "G": 12, "H": 12, "I": 17, "J": 17, "K": 12}.items():
        ws.column_dimensions[col].width = width
    ws.auto_filter.ref = f"A5:{get_column_letter(last_col)}{grand}"



def _daily_summary_sheet(
    ws,
    start_date: str,
    end_date: str,
    source_rows: list[dict],
    staff_directory=None,
    commune_resolver=None,
):
    """Daily Summary grouped by Date + Region + Dealer + Telegram account.

    Each Telegram account remains an individual row.  Region totals are inserted
    after the dealers for each region on each day.  Remark Issues are never
    combined into region/day/grand totals.
    """
    metric_labels = [label for _key, label, _aliases in METRICS]
    headers = [
        "Date", "Region", "Dealer", "Telegram Name", "Name", "ID",
        "District", "Commune", *metric_labels,
        "Total Outlet", "Remark Issues",
    ]
    last_col = len(headers)
    total_outlet_col = 8 + len(metric_labels) + 1
    remark_col = total_outlet_col + 1

    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "I6"

    for r, title in (
        (1, "KHMER BEVERAGES CO,"),
        (2, f"Daily Tracking Outlet from {datetime.strptime(start_date, '%Y-%m-%d'):%d/%m/%Y} to {datetime.strptime(end_date, '%Y-%m-%d'):%d/%m/%Y}"),
        (3, f"DATE: From {datetime.strptime(start_date, '%Y-%m-%d'):%d/%m/%Y} ==> {datetime.strptime(end_date, '%Y-%m-%d'):%d/%m/%Y}"),
    ):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
        ws.cell(r, 1, title)
        ws.cell(r, 1).font = Font(size=20 if r == 1 else (15 if r == 2 else 11), bold=True)

    for col, value in enumerate(headers, 1):
        cell = ws.cell(5, col, value)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[5].height = 48

    # The actual metric columns in Outlet_Visit are H:O because Remark Issues
    # is now column G.
    source_cols = {
        "Emergency Outlet": "H", "Attacking Stock": "I", "Tour Program": "J",
        "Key Wholesale": "K", "Water": "L", "C-Cola": "M",
        "Ize PET 300mL": "N", "Ize PET 1500mL": "O",
    }
    source_last_row = max(2, len(source_rows) + 1)

    # Build individual Telegram records from the already filtered source rows.
    staff_directory = staff_directory or {}
    metadata = defaultdict(lambda: {
        "remarks": [], "outlets": 0, "districts": set(), "communes": set(),
    })
    display_values = {}
    for source in source_rows:
        name, _username, sender_id = _identity(source)
        if not _is_exportable_telegram_name(name):
            continue
        d = _excel_date(source.get("submitted_at"))
        if not d:
            continue
        dealer = str(source.get("dealer") or "").strip()
        region = str(source.get("region") or "").strip()
        key = (d.isoformat(), region.casefold(), dealer.casefold(), sender_id)
        meta = metadata[key]
        # Total Outlet means a completed report form followed by its matched
        # pin map.  Forms without both coordinates remain visible for audit but
        # do not increase Total Outlet.
        if source.get("latitude") not in (None, "") and source.get("longitude") not in (None, ""):
            meta["outlets"] += 1
        # Telegram Name is the live profile. Name is the authoritative staff
        # name from R1-R8-Master List, matched only by Telegram ID.
        mapped_name = _mapped_staff_name(staff_directory, sender_id, name)
        display_values[key] = (region, dealer, name, mapped_name, sender_id)
        if commune_resolver is not None:
            commune = commune_resolver.resolve(
                source.get("latitude"), source.get("longitude")
            )
            district_key = district_identity(commune)
            commune_key = commune_identity(commune)
            if district_key:
                meta["districts"].add(district_key)
            if commune_key:
                meta["communes"].add(commune_key)
        remark = str(source.get("remark_issues") or "").strip()
        # Keep remarks on the individual Telegram row only.  Do not merge them
        # into any TOTAL row.  Duplicate copies on the same account/day are removed.
        if remark and remark not in meta["remarks"]:
            meta["remarks"].append(remark)

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    row = 6

    # Stable dealer order from the 65-dealer reference list.
    dealer_order = {
        str(dealer).casefold(): i
        for i, dealer in enumerate(
            dealer for dealers in EXPECTED_DEALERS.values() for dealer in dealers
        )
    }

    current = start
    daily_region_total_rows = []
    daily_grand_total_rows = []

    while current <= end:
        day_iso = current.isoformat()
        day_keys = [key for key in metadata if key[0] == day_iso]

        # Region order follows EXPECTED_DEALERS. Unknown regions are appended after.
        region_order = {str(region).casefold(): i for i, region in enumerate(EXPECTED_DEALERS)}
        day_keys.sort(key=lambda key: (
            region_order.get(key[1], 9999),
            dealer_order.get(key[2], 9999),
            key[3],
        ))

        # Group keys by region so TOTAL R1 / TOTAL R2 ... appear after each region.
        grouped = defaultdict(list)
        for key in day_keys:
            grouped[key[1]].append(key)

        day_total_rows = []
        for region_key, keys in sorted(
            grouped.items(), key=lambda item: region_order.get(item[0], 9999)
        ):
            region_first = row
            for key in keys:
                region, dealer, telegram_display, mapped_name, telegram_id = display_values[key]
                ws.cell(row, 1, current)
                ws.cell(row, 1).number_format = "dd/mm/yyyy"
                ws.cell(row, 2, region)
                ws.cell(row, 3, dealer)
                ws.cell(row, 4, telegram_display)
                ws.cell(row, 5, mapped_name)
                ws.cell(row, 6, telegram_id)
                ws.cell(row, 7, len(metadata[key]["districts"]))
                ws.cell(row, 8, len(metadata[key]["communes"]))

                for metric_index, label in enumerate(metric_labels, 9):
                    source_col = source_cols[label]
                    ws.cell(
                        row, metric_index,
                        f'=SUMIFS(\'Outlet_Visit\'!${source_col}$2:${source_col}${source_last_row},'
                        f'\'Outlet_Visit\'!$A$2:$A${source_last_row},$A{row},'
                        f'\'Outlet_Visit\'!$C$2:$C${source_last_row},$C{row},'
                        f'\'Outlet_Visit\'!$T$2:$T${source_last_row},$D{row},'
                        f'\'Outlet_Visit\'!$V$2:$V${source_last_row},$F{row})',
                    )

                meta = metadata[key]
                ws.cell(row, total_outlet_col, int(meta["outlets"]))
                ws.cell(row, remark_col, " | ".join(meta["remarks"]))
                row += 1

            # Region total: unique coverage, metrics, and Total Outlet only.
            # Remark Issues is intentionally blank.
            total_row = row
            daily_region_total_rows.append(total_row)
            day_total_rows.append(total_row)
            ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=6)
            ws.cell(total_row, 1, f"TOTAL {display_values[keys[0]][0]}")
            for col in range(7, total_outlet_col + 1):
                letter = get_column_letter(col)
                ws.cell(total_row, col, f"=SUM({letter}{region_first}:{letter}{row - 1})")
            ws.cell(total_row, remark_col, "")
            for cell in ws[total_row]:
                cell.fill = PatternFill("solid", fgColor=GRAY)
                cell.font = Font(color=WHITE, bold=True)
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            row += 1

        # A blank separator between dates makes multi-day reports easier to read.
        if day_total_rows:
            daily_grand_total_rows.extend(day_total_rows)
        current += timedelta(days=1)

    grand = row
    ws.merge_cells(start_row=grand, start_column=1, end_row=grand, end_column=6)
    ws.cell(grand, 1, "GRAND TOTAL")
    for col in range(7, total_outlet_col + 1):
        letter = get_column_letter(col)
        if daily_region_total_rows:
            ws.cell(grand, col, "=" + "+".join(f"{letter}{r}" for r in daily_region_total_rows))
        else:
            ws.cell(grand, col, 0)
    ws.cell(grand, remark_col, "")

    for cell in ws[grand]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    thin = Side(style="thin", color=GRID)
    for cells in ws.iter_rows(min_row=5, max_row=grand, min_col=1, max_col=last_col):
        for cell in cells:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            if cell.row > 5 and cell.fill.fill_type is None:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    widths = {
        "A": 13, "B": 12, "C": 14, "D": 28, "E": 24, "F": 18,
        "G": 12, "H": 12, "I": 17, "J": 17, "K": 15, "L": 17,
        "M": 12, "N": 12, "O": 17, "P": 18,
        "Q": 14, "R": 70,
    }
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    ws.auto_filter.ref = f"A5:{get_column_letter(last_col)}{grand}"


def _summary_by_date_sheet(
    ws,
    start_date: str,
    end_date: str,
    source_rows: list[dict],
    staff_directory=None,
    commune_resolver=None,
):
    """Build a monthly user matrix with eight execution metrics by week.

    Weeks are fixed calendar buckets: W1=days 1-7, W2=8-14,
    W3=15-21, W4=22-28, and W5=29 through month end.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if (start.year, start.month) == (end.year, end.month):
        # Outlet Visit in Aug'26 is a monthly template. Always show day 1
        # through the last calendar day, even when the selected scrape range is
        # a single day or begins on 18-Aug. Days outside the selected range are
        # correctly displayed as zero.
        period_dates = [
            date(start.year, start.month, day_number)
            for day_number in range(1, monthrange(start.year, start.month)[1] + 1)
        ]
    else:
        period_dates = [
            start + timedelta(days=offset)
            for offset in range((end - start).days + 1)
        ]
    week_labels = ["W1", "W2", "W3", "W4", "W5"]

    metric_keys = [key for key, _label, _aliases in METRICS]
    metric_labels = [
        "Emergency Outlet", "Attacking Stock Outlet", "Tour Program Outlet",
        "Key Wholesale", "Water", "C-Cola", "Ize PET 300mL", "Ize PET 1500mL",
    ]
    # The weekly management view intentionally hides Telegram profile details.
    # Telegram ID remains part of the internal grouping key, so two staff with
    # similar names are still counted separately and accurately.
    identity_headers = ["Region", "Dealer", "Name", "District", "Commune"]
    first_metric_col = len(identity_headers) + 1
    total_outlet_col = len(identity_headers) + len(week_labels) * len(metric_labels) + 1
    last_col = total_outlet_col

    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "F7"

    for r, title in (
        (1, "KHMER BEVERAGES CO,"),
        (2, f"Summary By Date — {start:%B %Y}"),
        (3, f"DATE: From {start:%d/%m/%Y} ==> {end:%d/%m/%Y}"),
    ):
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=last_col)
        ws.cell(r, 1, title)
        ws.cell(r, 1).font = Font(size=20 if r == 1 else (15 if r == 2 else 11), bold=True)

    header_fill = PatternFill("solid", fgColor=WHITE)
    header_border = Border(
        left=Side(style="medium", color="000000"),
        right=Side(style="medium", color="000000"),
        top=Side(style="medium", color="000000"),
        bottom=Side(style="medium", color="000000"),
    )
    for col, value in enumerate(identity_headers, 1):
        cell = ws.cell(6, col, value)
        cell.fill = header_fill
        cell.font = Font(color="000000", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = header_border

    for week_index, week_label in enumerate(week_labels):
        group_start = first_metric_col + week_index * len(metric_labels)
        group_end = group_start + len(metric_labels) - 1
        for col in range(group_start, group_end + 1):
            top = ws.cell(5, col)
            top.fill = PatternFill("solid", fgColor=WEEK_HEADER)
            top.border = header_border
        ws.merge_cells(
            start_row=5, start_column=group_start,
            end_row=5, end_column=group_end,
        )
        day_cell = ws.cell(5, group_start, week_label)
        day_cell.fill = PatternFill("solid", fgColor=WEEK_HEADER)
        day_cell.font = Font(color="000000", bold=True, size=12)
        day_cell.alignment = Alignment(horizontal="center", vertical="center")
        day_cell.border = header_border
        for offset, label in enumerate(metric_labels):
            cell = ws.cell(6, group_start + offset, label)
            cell.fill = header_fill
            cell.font = Font(color="000000", bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = header_border

    cell = ws.cell(6, total_outlet_col, "Total Outlet")
    cell.fill = header_fill
    cell.font = Font(color="000000", bold=True)
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    cell.border = header_border

    # Keep the unused top cells visually connected to the grouped header.
    for col in [*range(1, first_metric_col), total_outlet_col]:
        cell = ws.cell(5, col)
        cell.fill = PatternFill("solid", fgColor="E7E6E6")
        cell.border = header_border
    ws.row_dimensions[5].height = 26
    ws.row_dimensions[6].height = 48

    staff_directory = staff_directory or {}
    metadata = defaultdict(lambda: {
        "outlets": 0, "districts": set(), "communes": set(),
        "weekly_metrics": defaultdict(lambda: [0] * len(metric_keys)),
    })
    display_values = {}
    for source in source_rows:
        name, _username, sender_id = _identity(source)
        if not _is_exportable_telegram_name(name):
            continue
        submitted = _excel_date(source.get("submitted_at"))
        if not submitted or submitted < start or submitted > end:
            continue
        dealer = str(source.get("dealer") or "").strip()
        region = str(source.get("region") or "").strip()
        key = (region.casefold(), dealer.casefold(), sender_id)
        if source.get("latitude") not in (None, "") and source.get("longitude") not in (None, ""):
            metadata[key]["outlets"] += 1
        raw_values = json.loads(source.get("raw_values") or "{}")
        week_index = min(4, (submitted.day - 1) // 7)
        day_values = metadata[key]["weekly_metrics"][week_index]
        for metric_index, metric_key in enumerate(metric_keys):
            day_values[metric_index] += _binary_quantity(
                raw_values.get(metric_key, "")
            )[0]
        mapped_name = _mapped_staff_name(staff_directory, sender_id, name)
        display_values[key] = (region, dealer, name, mapped_name, sender_id)
        if commune_resolver is not None:
            commune = commune_resolver.resolve(
                source.get("latitude"), source.get("longitude")
            )
            district_key = district_identity(commune)
            commune_key = commune_identity(commune)
            if district_key:
                metadata[key]["districts"].add(district_key)
            if commune_key:
                metadata[key]["communes"].add(commune_key)

    dealer_order = {
        str(dealer).casefold(): i
        for i, dealer in enumerate(
            dealer for dealers in EXPECTED_DEALERS.values() for dealer in dealers
        )
    }
    region_order = {str(region).casefold(): i for i, region in enumerate(EXPECTED_DEALERS)}
    grouped = defaultdict(list)
    for key in metadata:
        grouped[key[0]].append(key)

    row = 7
    region_total_rows = []

    for region_key, keys in sorted(
        grouped.items(), key=lambda item: region_order.get(item[0], 9999)
    ):
        keys.sort(key=lambda key: (dealer_order.get(key[1], 9999), key[2]))
        region_first = row
        for key in keys:
            region, dealer, telegram_display, mapped_name, telegram_id = display_values[key]
            ws.cell(row, 1, region)
            ws.cell(row, 2, dealer)
            ws.cell(row, 3, mapped_name)
            ws.cell(row, 4, len(metadata[key]["districts"]))
            ws.cell(row, 5, len(metadata[key]["communes"]))

            for week_index, _week_label in enumerate(week_labels):
                group_start = first_metric_col + week_index * len(metric_labels)
                values = metadata[key]["weekly_metrics"].get(
                    week_index, [0] * len(metric_keys)
                )
                for metric_index, value in enumerate(values):
                    cell = ws.cell(row, group_start + metric_index, int(value))
                    if int(value) > 0:
                        cell.fill = PatternFill("solid", fgColor=LIGHT_GREEN)

            ws.cell(row, total_outlet_col, int(metadata[key]["outlets"]))
            row += 1

        total_row = row
        region_total_rows.append(total_row)
        ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=3)
        ws.cell(total_row, 1, f"TOTAL {display_values[keys[0]][0]}")
        for col in range(4, total_outlet_col + 1):
            letter = get_column_letter(col)
            ws.cell(total_row, col, f"=SUM({letter}{region_first}:{letter}{row - 1})")
        for cell in ws[total_row]:
            cell.fill = PatternFill("solid", fgColor=GRAY)
            cell.font = Font(color=WHITE, bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        row += 1

    grand = row
    ws.merge_cells(start_row=grand, start_column=1, end_row=grand, end_column=3)
    ws.cell(grand, 1, "GRAND TOTAL")
    for col in range(4, total_outlet_col + 1):
        letter = get_column_letter(col)
        if region_total_rows:
            ws.cell(grand, col, "=" + "+".join(f"{letter}{r}" for r in region_total_rows))
        else:
            ws.cell(grand, col, 0)

    for cell in ws[grand]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Keep weekly progress visible at the top of the sheet. Each value links
    # to the matching GRAND TOTAL cell and is not cumulative.
    # The detailed Telegram/dealer rows and the existing bottom totals remain
    # unchanged and auditable.
    progress_row = 4
    for col in range(1, last_col + 1):
        cell = ws.cell(progress_row, col)
        cell.fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        cell.font = Font(color=NAVY, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.merge_cells(
        start_row=progress_row, start_column=1,
        end_row=progress_row, end_column=len(identity_headers),
    )
    ws.cell(progress_row, 1, "WEEKLY PROGRESS")
    for col in range(first_metric_col, total_outlet_col + 1):
        letter = get_column_letter(col)
        ws.cell(progress_row, col, f"={letter}{grand}")
        ws.cell(progress_row, col).number_format = "#,##0"
    ws.row_dimensions[progress_row].height = 24

    thin = Side(style="thin", color=GRID)
    for cell in ws[progress_row]:
        cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for cells in ws.iter_rows(min_row=6, max_row=grand, min_col=1, max_col=last_col):
        for cell in cells:
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            if cell.row > 6 and cell.fill.fill_type is None:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    identity_widths = [12, 14, 24, 12, 12]
    for col, width in enumerate(identity_widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = width
    for week_index, _week_label in enumerate(week_labels):
        group_start = first_metric_col + week_index * len(metric_labels)
        for offset in range(len(metric_labels)):
            ws.column_dimensions[get_column_letter(group_start + offset)].width = 7.55
    ws.column_dimensions[get_column_letter(total_outlet_col)].width = 14
    ws.auto_filter.ref = f"A6:{get_column_letter(last_col)}{grand}"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.print_title_rows = "1:6"

def export_outlet_visit(
    db: Database,
    output: Path,
    start_date: str,
    end_date: str,
    staff_master_path: Path | None = None,
) -> Path:
    from .coverage_audit import warn_outlet_coverage
    warn_outlet_coverage(db, start_date, end_date)
    output.parent.mkdir(parents=True, exist_ok=True)
    id_recovery = db.ensure_telegram_ids()
    if id_recovery["total"]:
        print(f"Telegram IDs repaired before Outlet Visit export: {id_recovery}")
    gps_recovery = db.rebuild_outlet_visit_gps_links(start_date, end_date)
    print(f"Outlet Visit GPS links rebuilt: {gps_recovery}")
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(
            """SELECT v.* FROM outlet_visits v
               LEFT JOIN telegram_groups g ON g.group_id=v.group_id
               WHERE (g.group_id IS NULL OR g.source_kind='EXECUTION')
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(v.submitted_at,1,10) BETWEEN ? AND ?
               ORDER BY v.submitted_at,v.region,v.dealer,v.group_id,v.message_id""",
            (start_date, end_date),
        ).fetchall()]

    wb = Workbook()
    wb.active.title = "Outlet_Visit"
    wb.create_sheet("Detail Clean Data")
    wb.create_sheet("Summary")
    wb.create_sheet("Daily Summary")
    wb.create_sheet("Summary By Date")
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.calculation.calcMode = "auto"

    # EXCLUSION rule: remove only guideline/reference accounts, then require a
    # completed Telegram visit (report + matched map pin).  Do NOT use the
    # SO/SS master as an allow-list: new or missing-list staff must remain when
    # their actual group report has both coordinates.
    rows = [
        r for r in rows
        if _is_exportable_telegram_name(r.get("sender_name"))
        and r.get("latitude") not in (None, "")
        and r.get("longitude") not in (None, "")
    ]

    metric_keys = [key for key, _label, _aliases in METRICS]
    metric_labels = [label for _key, label, _aliases in METRICS]
    if staff_master_path is None:
        staff_master_path = (
            Path(__file__).resolve().parent.parent
            / "templates" / "R1-R8-Master List.xlsx"
        )
    staff_directory = load_staff_directory(staff_master_path)
    commune_resolver = default_commune_resolver(db.path.resolve().parent.parent)
    if not commune_resolver.available:
        commune_resolver = default_commune_resolver(Path(__file__).resolve().parent.parent)

    outlet_headers = [
        "Date", "Region", "Dealer", "Outlet Name", "Phone", "Outlet Type", "Remark Issues",
        *metric_labels, "Lat", "Lon", "District", "Commune",
        "Telegram Name", "Username", "Telegram ID",
        "Group ID", "Message ID", "Status",
    ]
    detail_headers = [
        "Date", "Region", "Dealer", "Outlet Name", "Phone", "Outlet Type", "Remark Issues", "Metric",
        "Quantity", "Raw Value", "Field Status", "Telegram Name", "Username", "Telegram ID",
        "Lat", "Lon", "Group ID", "Message ID",
    ]

    outlet_rows = []
    detail_rows = []

    for row in rows:
        raw_values = json.loads(row["raw_values"] or "{}")
        statuses = json.loads(row["field_statuses"] or "{}")
        name, username, sender_id = _identity(row)

        # ALWAYS recalculate Quantity from Raw Value.
        # This prevents stale database values such as:
        #   quantity=1 + raw_value='Outlet: No'
        # from becoming 1 in Excel.
        quantities = [
            _binary_quantity(raw_values.get(key, ""))[0]
            for key in metric_keys
        ]
        resolved = commune_resolver.resolve(row.get("latitude"), row.get("longitude"))
        district_name = resolved.district if resolved else ""
        commune_name = resolved.name if resolved else ""

        common = [
            _excel_date(row["submitted_at"]), row["region"], row["dealer"],
            row["outlet_name"], row["phone"], row["outlet_type"],
        ]

        outlet_rows.append([
            *common, row["remark_issues"] or "", *quantities, row["latitude"], row["longitude"],
            district_name, commune_name,
            name, ("@" + username if username else ""), sender_id,
            str(row["group_id"]), row["message_id"], row["parse_status"],
        ])

        for key, label, _aliases in METRICS:
            raw_value = raw_values.get(key, "")
            quantity = _binary_quantity(raw_value)[0]
            # Keep Detail Clean Data sparse. The previous implementation wrote
            # eight rows for every outlet even when seven fields were blank,
            # creating 150k+ Excel rows and minute-long saves. Explicit values
            # such as Yes/No/0 remain auditable; only truly blank metric rows
            # are omitted. Summary calculations still use Outlet_Visit.
            if not str(raw_value or "").strip() and quantity == 0:
                continue
            detail_rows.append([
                *common, row["remark_issues"] or "", label, quantity, raw_value, statuses.get(key, ""),
                name, ("@" + username if username else ""), sender_id,
                row["latitude"], row["longitude"], str(row["group_id"]), row["message_id"],
            ])

    _data_sheet(wb["Outlet_Visit"], outlet_headers, outlet_rows)
    _data_sheet(wb["Detail Clean Data"], detail_headers, detail_rows)
    _summary_sheet(wb["Summary"], start_date, end_date, len(outlet_rows))
    _daily_summary_sheet(
        wb["Daily Summary"], start_date, end_date, rows,
        staff_directory, commune_resolver,
    )
    _summary_by_date_sheet(
        wb["Summary By Date"], start_date, end_date, rows,
        staff_directory, commune_resolver,
    )

    wb.active = 0
    wb.save(output)

    # Verify that the generated workbook can be opened before returning.
    check = load_workbook(output, read_only=True)
    check.close()
    return output
