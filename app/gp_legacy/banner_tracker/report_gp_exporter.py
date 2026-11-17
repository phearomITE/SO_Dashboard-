from __future__ import annotations

import json
import re
from collections import defaultdict
from copy import copy
from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Color, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .commune import commune_identity, default_commune_resolver, district_identity
from .database import Database
from .excel_links import set_clickable_link
from .groups import DEALER_TO_REGION, EXPECTED_DEALERS, normalized_dealer
from .outlet_visit import METRICS, _binary_quantity, normalize_outlet_type
from .staff_directory import (
    StaffMember,
    load_staff_directory,
    normalize_sales_role,
    normalize_telegram_id,
    staff_is_active,
)
from .telegram_identity import is_excluded_telegram_name


GP_PIN_ATTENDANCE_VERSION = "ROSTER_DAY_V1"

NAVY = "23364A"
WHITE = "FFFFFF"
GRID = "B7B7B7"
REFERENCE_TITLE_BLUE = "D9E2F3"
REFERENCE_SECTION_GRAY = "E7E6E6"
REFERENCE_REMARK_PEACH = "FCE4D6"
REFERENCE_TOTAL_YELLOW = "FFE699"
REFERENCE_ACTIVE_GREEN = "70AD47"
REFERENCE_CYAN = "00B0F0"
REFERENCE_RED = "FF0000"

REPORT_METRICS = [
    ("emergency_outlet", "Emergency Outlet", 12),
    ("attacking_stock", "Attacking Stock Outlet", 16),
    ("tour_program", "Tour Program Outlet", 20),
    ("key_wholesale", "Key Wholesale", 24),
    ("water", "Water", 28),
    ("c_cola", "C-Cola", 32),
    ("ize_pet_300ml", "Ize PET 300mL", 36),
    ("ize_pet_1500ml", "Ize PET 1500mL", 40),
]
OUTLET_TYPES = ("WS", "Retail", "DS", "ON")
REPORT_COUNT_COLUMN = 10  # J
AVERAGE_COLUMN = 11       # K
LAST_METRIC_COLUMN = 43   # AQ
REMARK_COLUMN = 44        # AR
DEALER_DATA_START = 10
DEALER_DATA_END = DEALER_DATA_START + len(DEALER_TO_REGION) - 1
DEALER_TOTAL_ROW = DEALER_DATA_END + 1


def _apply_reference_report_palette(sheet, data_start: int, data_end: int, total_row: int) -> None:
    """Apply the approved Daily Sales Report colors from the user's reference."""
    # Main title and execution hierarchy.
    sheet["B5"].fill = PatternFill("solid", fgColor=REFERENCE_TITLE_BLUE)
    sheet["B5"].font = Font(name="Calibri", size=20, bold=True, italic=True, color=REFERENCE_RED)
    for cell in sheet["B5:AQ5"][0]:
        cell.fill = PatternFill("solid", fgColor=REFERENCE_TITLE_BLUE)
    for address in ("H6:K6", "L6:AQ6", "L7:AQ7"):
        for row in sheet[address]:
            for cell in row:
                cell.fill = PatternFill("solid", fgColor=REFERENCE_SECTION_GRAY)
    for address in ("L6", "L7"):
        sheet[address].font = Font(name="Calibri", size=18, bold=True, italic=True, color="000000")

    # Large structural header block requested in the approved reference.
    for address in ("B6", "D6", "H6", "H7", "J7", "K7", "D8", "E8", "F8", "G8", "H8", "I8"):
        font = copy(sheet[address].font)
        font.sz = 14
        font.bold = True
        sheet[address].font = font

    # Remark block is the pale peach panel shown at the right of the reference.
    for row in sheet["AR5:AR9"]:
        for cell in row:
            cell.fill = PatternFill("solid", fgColor=REFERENCE_REMARK_PEACH)
            cell.font = Font(name="Calibri", size=10, bold=True, color="000000")

    # Sales-team semantic colors: AL is red, Active is green.
    sheet["F8"].font = Font(name="Calibri", size=14, bold=True, color=REFERENCE_RED)
    sheet["G8"].font = Font(name="Calibri", size=14, bold=True, color="00B050")

    # First four activity labels are cyan; beverage SKU labels are red.
    for metric_index, (_key, _label, start) in enumerate(REPORT_METRICS):
        color = REFERENCE_CYAN if metric_index < 4 else REFERENCE_RED
        sheet.cell(8, start).fill = PatternFill("solid", fgColor="FFFFFF")
        sheet.cell(8, start).font = Font(
            name="Calibri", size=12, bold=True, color=color
        )
        for column in range(start, start + 4):
            sheet.cell(9, column).fill = PatternFill("solid", fgColor="FFFFFF")
            sheet.cell(9, column).font = Font(
                name="Calibri", size=12, bold=True, color="000000"
            )

    # More vertical space prevents the larger wrapped headers from clipping.
    sheet.row_dimensions[5].height = 34
    sheet.row_dimensions[6].height = 30
    sheet.row_dimensions[7].height = 48
    sheet.row_dimensions[8].height = 30
    sheet.row_dimensions[9].height = 30

    # Keep all report rows white and make the grand total a single yellow band.
    for row in range(data_start, data_end + 1):
        for column in range(2, REMARK_COLUMN + 1):
            sheet.cell(row, column).fill = PatternFill("solid", fgColor="FFFFFF")
        # Numeric values for C-Cola, Ize PET 300mL and Ize PET 1500mL use
        # Excel's theme color "Black, Text 1". Keep their red headers and the
        # red data bars unchanged.
        for _key, _label, start in REPORT_METRICS[5:]:
            for column in range(start, start + 4):
                font = copy(sheet.cell(row, column).font)
                font.color = Color(theme=1)
                sheet.cell(row, column).font = font
    for column in range(2, REMARK_COLUMN + 1):
        sheet.cell(total_row, column).fill = PatternFill(
            "solid", fgColor=REFERENCE_TOTAL_YELLOW
        )
        sheet.cell(total_row, column).font = Font(
            name="Calibri", size=10, bold=True, color="000000"
        )
    sheet.cell(total_row, 6).font = Font(
        name="Calibri", size=10, bold=True, color=REFERENCE_RED
    )
    sheet.cell(total_row, 7).fill = PatternFill(
        "solid", fgColor=REFERENCE_ACTIVE_GREEN
    )
    sheet.cell(total_row, 7).font = Font(
        name="Calibri", size=10, bold=True, color="000000"
    )


def _excel_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _coordinate(row: dict) -> tuple[float, float] | None:
    try:
        latitude = float(row.get("latitude"))
        longitude = float(row.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        return None
    return latitude, longitude


def _capture_cell_style(cell) -> tuple:
    return (
        copy(cell._style),
        cell.number_format,
        copy(cell.alignment),
        copy(cell.protection),
    )


def _apply_cell_style(style: tuple, target) -> None:
    target._style = copy(style[0])
    target.number_format = style[1]
    target.alignment = copy(style[2])
    target.protection = copy(style[3])


def _read_manual_region_headcounts(path: Path) -> dict[str, dict[str, int]]:
    """Read the legacy Region/SS/SO summary table as a compatibility fallback."""
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            values = list(sheet.iter_rows(values_only=True))
            for row_index, row in enumerate(values):
                labels = [str(value or "").strip().casefold() for value in row]
                for start in range(max(0, len(labels) - 2)):
                    header = labels[start:start + 3]
                    if not header or header[0] != "region":
                        continue
                    if set(header[1:]) != {"ss", "so"}:
                        continue
                    offsets = {label.upper(): offset for offset, label in enumerate(header)}
                    result: dict[str, dict[str, int]] = {}
                    for data_row in values[row_index + 1:]:
                        region = str(
                            data_row[start] if start < len(data_row) else ""
                        ).strip().upper()
                        if region == "GRAND TOTAL":
                            break
                        if region not in EXPECTED_DEALERS:
                            if result:
                                break
                            continue
                        counts: dict[str, int] = {}
                        for role in ("SO", "SS"):
                            column = start + offsets[role]
                            raw = data_row[column] if column < len(data_row) else 0
                            try:
                                counts[role] = max(0, int(float(raw or 0)))
                            except (TypeError, ValueError):
                                counts[role] = 0
                        result[region] = counts
                    if set(result) == set(EXPECTED_DEALERS):
                        return result
    finally:
        workbook.close()
    return {}


def _read_master_data_region_headcounts(path: Path) -> dict[str, dict[str, int]]:
    """Read the visible R1--R8 SO/SS control totals from ``Master Data``.

    The uploaded master workbook keeps the approved staffing plan in its top
    summary block.  Those values are authoritative for the Region report and
    are recalculated by Excel whenever the user updates the master workbook.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Master Data" not in workbook.sheetnames:
            return {}
        sheet = workbook["Master Data"]
        rows = list(
            sheet.iter_rows(
                min_row=1,
                max_row=min(50, sheet.max_row),
                values_only=True,
            )
        )
        if not rows:
            return {}
        max_columns = max(len(row) for row in rows)
        expected_regions = [f"R{number}" for number in range(1, 9)]
        for start_row in range(0, len(rows) - len(expected_regions) + 1):
            for region_column in range(max_columns):
                actual_regions = [
                    str(
                        rows[start_row + offset][region_column]
                        if region_column < len(rows[start_row + offset])
                        else ""
                    ).strip().upper()
                    for offset in range(len(expected_regions))
                ]
                if actual_regions != expected_regions:
                    continue
                role_columns: dict[str, int] = {}
                for header_row in range(max(0, start_row - 6), start_row):
                    for column, value in enumerate(rows[header_row]):
                        if column < region_column or column > region_column + 10:
                            continue
                        label = str(value or "").strip().upper().replace(" ", "")
                        if label in {"SO", "SS"}:
                            role_columns[label] = column
                if set(role_columns) != {"SO", "SS"}:
                    continue
                result: dict[str, dict[str, int]] = {}
                for offset, region in enumerate(expected_regions):
                    source_row = rows[start_row + offset]
                    counts: dict[str, int] = {}
                    for role in ("SO", "SS"):
                        column = role_columns[role]
                        raw = source_row[column] if column < len(source_row) else 0
                        try:
                            counts[role] = max(0, int(float(raw or 0)))
                        except (TypeError, ValueError):
                            counts[role] = 0
                    result[region] = counts
                return result
    finally:
        workbook.close()
    return {}


def _load_dealer_headcounts(path: Path) -> dict[str, dict[str, int]]:
    """Count active SO/SS assignments for all 65 dealers from ``Master Data``.

    A staff row assigned to multiple dealers (for example ``CA3/CA6``) counts
    once in each listed dealer. Employee ID is used to prevent duplicate rows.
    Blank/Active staff count; Goodbye/Inactive/Resigned staff do not.
    """
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        if "Master Data" not in workbook.sheetnames:
            return {}
        sheet = workbook["Master Data"]
        header_row = 0
        headers: dict[str, int] = {}
        for row_number, values in enumerate(
            sheet.iter_rows(
                min_row=1,
                max_row=min(80, sheet.max_row),
                values_only=True,
            ),
            start=1,
        ):
            candidate = {
                str(value or "").strip().casefold(): column
                for column, value in enumerate(values)
                if str(value or "").strip()
            }
            if {"region", "dealer", "id", "name", "position"}.issubset(candidate):
                header_row = row_number
                headers = candidate
                break
        if not header_row:
            return {}

        status_column = (
            headers.get("status")
            or headers.get("employment status")
            or headers.get("active status")
        )
        identities = {
            dealer: {"SO": set(), "SS": set()}
            for dealer in DEALER_TO_REGION
        }
        role_rows = 0
        assigned_rows = 0
        unassigned: list[str] = []

        for row_number, values in enumerate(
            sheet.iter_rows(min_row=header_row + 1, values_only=True),
            start=header_row + 1,
        ):
            def value_at(label: str):
                column = headers.get(label)
                return values[column] if column is not None and column < len(values) else ""

            employee_id = normalize_telegram_id(value_at("id"))
            name = str(value_at("name") or "").strip()
            position = str(value_at("position") or "").strip()
            status = (
                str(values[status_column] or "").strip()
                if status_column is not None and status_column < len(values)
                else ""
            )
            member = StaffMember(
                employee_id=employee_id,
                name=name,
                position=position,
                telegram_id="",
                role=normalize_sales_role(position),
                region=str(value_at("region") or "").strip().upper(),
                status=status,
            )
            if member.role not in {"SO", "SS"} or not staff_is_active(member):
                continue
            role_rows += 1
            identity = employee_id or f"ROW-{row_number}:{name.casefold()}"
            dealer_codes = {
                normalized_dealer(match)
                for match in re.findall(
                    r"[A-Z]{2,4}\d",
                    str(value_at("dealer") or "").upper(),
                )
            }
            dealer_codes = {
                dealer for dealer in dealer_codes if dealer in DEALER_TO_REGION
            }
            if not dealer_codes:
                unassigned.append(f"{name or employee_id or f'row {row_number}'}")
                continue
            assigned_rows += 1
            for dealer in dealer_codes:
                identities[dealer][member.role].add(identity)

        if role_rows and assigned_rows != role_rows:
            sample = ", ".join(unassigned[:8])
            raise RuntimeError(
                "Cannot build automatic Report with Dealer SO/SS counts: "
                f"{role_rows - assigned_rows} active master row(s) have no valid "
                f"Dealer. Fill the Master Data Dealer column for: {sample}"
            )
        if not assigned_rows:
            return {}
        return {
            dealer: {
                "SO": len(identities[dealer]["SO"]),
                "SS": len(identities[dealer]["SS"]),
            }
            for dealer in DEALER_TO_REGION
        }
    finally:
        workbook.close()


def _historical_region_by_telegram_id(db: Database) -> dict[str, str]:
    """Map staff Telegram IDs to their most-used active Execution region.

    This provides an automatic region for older master lists that do not yet
    contain the per-person ``Region`` column. The complete report history is
    used, so a new SO/SS becomes countable after their first valid report and
    is not dependent on the selected Report GP date window.
    """
    candidates: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    with db.connect() as connection:
        rows = connection.execute(
            """SELECT v.sender_id, v.region, COUNT(*) AS report_count,
                      MAX(v.submitted_at) AS last_report
               FROM outlet_visits v
               LEFT JOIN telegram_groups g ON g.group_id=v.group_id
               WHERE v.sender_id IS NOT NULL
                 AND (g.group_id IS NULL OR g.source_kind='EXECUTION')
                 AND (g.group_id IS NULL OR g.is_active=1)
               GROUP BY v.sender_id, v.region"""
        ).fetchall()
    for row in rows:
        telegram_id = normalize_telegram_id(row["sender_id"])
        region = str(row["region"] or "").strip().upper()
        if telegram_id and region in EXPECTED_DEALERS:
            candidates[telegram_id].append(
                (int(row["report_count"] or 0), str(row["last_report"] or ""), region)
            )
    return {
        telegram_id: max(options, key=lambda item: (item[0], item[1]))[2]
        for telegram_id, options in candidates.items()
    }


def _load_region_headcounts(
    path: Path,
    region_by_telegram_id: dict[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Count active SO/SS rows in the current master list by Telegram ID.

    The per-person ``Region`` column is authoritative when present. Older
    files can use the Telegram-ID history mapping supplied by the exporter.
    Blank or ``Active`` status counts; Goodbye/Inactive/Resigned rows do not.
    The old Region/SS/SO table is used only when no staff row can be assigned.
    """
    master_summary = _read_master_data_region_headcounts(path)
    if set(master_summary) == set(EXPECTED_DEALERS):
        return master_summary

    history = {
        normalize_telegram_id(telegram_id): str(region or "").strip().upper()
        for telegram_id, region in (region_by_telegram_id or {}).items()
    }
    members = load_staff_directory(path)
    identities = {
        region: {"SO": set(), "SS": set()}
        for region in EXPECTED_DEALERS
    }
    active_role_rows = 0
    assigned_role_rows = 0
    unassigned: list[str] = []
    for telegram_id, member in members.items():
        if member.role not in {"SO", "SS"} or not staff_is_active(member):
            continue
        active_role_rows += 1
        region = member.region or history.get(telegram_id, "")
        if region not in identities:
            unassigned.append(f"{member.name or 'Unknown'} ({telegram_id})")
            continue
        assigned_role_rows += 1
        identities[region][member.role].add(telegram_id)

    if assigned_role_rows:
        if assigned_role_rows != active_role_rows:
            sample = ", ".join(unassigned[:8])
            raise RuntimeError(
                "Cannot build correct Report GP SO/SS totals: "
                f"{active_role_rows - assigned_role_rows} active master row(s) have "
                "no R1-R8 Region and no Telegram report history. Fill the master "
                f"Region column for: {sample}"
            )
        return {
            region: {
                "SO": len(identities[region]["SO"]),
                "SS": len(identities[region]["SS"]),
            }
            for region in EXPECTED_DEALERS
        }
    return _read_manual_region_headcounts(path)


def _write_al(cell, manual, entries, days):
    # F is a headcount. Multi-day exports therefore use average daily leave.
    automatic = len(entries) / days
    cell.value = (manual or 0) + automatic if automatic else manual
    cell.number_format = "0.##"
    if automatic:
        cell.comment = Comment(
            "TEAM_TALK_AL " + json.dumps({"automatic": automatic, "manual": manual or 0})
            + "\nAL full days (SO/SS roster); multiple days show average daily AL.\n"
            + "\n".join(sorted(f"{day}: {name}" for day, identity, name in entries)),
            "Banner Tracker",
        )
    else:
        cell.comment = None


def _write_attendance_al(cell, manual, total, al, *, report_senders=False):
    active = total - al
    cell.value = f"=D{cell.row}+E{cell.row}-{active:.15g}"
    cell.number_format = "0" if float(al).is_integer() else "0.##"
    cell.comment = Comment(
        "TEAM_TALK_ATTENDANCE " + json.dumps({"manual": manual or 0, "active": active, "al": al, "source": "report senders" if report_senders else "Team Talk Yes"})
        + ("\nAL = active SO-list total minus unique report senders for this full Cambodia date. " if report_senders else "\nAL = Total SO/SS minus independently matched Team Talk Yes attendance. ")
        + "Unmarked/absent staff are inferred AL. Manual/explicit AL is not added again. "
        "Multi-day ranges use average daily attendance. See Attendance_Reconciliation.csv.",
        "Banner Tracker",
    )


def _read_manual_al_values(output: Path) -> dict[str, dict[str, int | float | None]]:
    """Preserve user-entered AL values when Report GP is regenerated."""
    saved = {"Report GP": {}, "Report with Dealer": {}}
    if not output.exists():
        return saved
    try:
        workbook = load_workbook(output, read_only=False, data_only=False)
    except Exception:
        return saved
    try:
        if "Report GP" in workbook.sheetnames:
            sheet = workbook["Report GP"]
            for row in range(10, 18):
                key = str(sheet.cell(row, 2).value or "").strip().upper()
                cell = sheet.cell(row, 6)
                value = cell.value
                if cell.comment and cell.comment.text.startswith("TEAM_TALK_ATTENDANCE "):
                    value = json.loads(cell.comment.text.splitlines()[0].split(" ", 1)[1])["manual"]
                if isinstance(value, (int, float)) and cell.comment and cell.comment.text.startswith("TEAM_TALK_AL "):
                    metadata = json.loads(cell.comment.text.splitlines()[0][13:])
                    value = max(0, (value or 0) - metadata["automatic"])
                if (
                    key in EXPECTED_DEALERS
                    and isinstance(value, (int, float))
                    and value > 0
                ):
                    saved["Report GP"][key] = value
        if "Report with Dealer" in workbook.sheetnames:
            sheet = workbook["Report with Dealer"]
            for row in range(DEALER_DATA_START, DEALER_DATA_END + 1):
                key = normalized_dealer(sheet.cell(row, 3).value)
                cell = sheet.cell(row, 6)
                value = cell.value
                if cell.comment and cell.comment.text.startswith("TEAM_TALK_ATTENDANCE "):
                    value = json.loads(cell.comment.text.splitlines()[0].split(" ", 1)[1])["manual"]
                if isinstance(value, (int, float)) and cell.comment and cell.comment.text.startswith("TEAM_TALK_AL "):
                    metadata = json.loads(cell.comment.text.splitlines()[0][13:])
                    value = max(0, (value or 0) - metadata["automatic"])
                if (
                    key in DEALER_TO_REGION
                    and isinstance(value, (int, float))
                    and value > 0
                ):
                    saved["Report with Dealer"][key] = value
    finally:
        workbook.close()
    return saved


def _prepare_report_sheet(workbook):
    if "Report GP" not in workbook.sheetnames:
        raise RuntimeError("The Report GP template must contain a 'Report GP' sheet.")
    for sheet in list(workbook.worksheets):
        if sheet.title != "Report GP":
            workbook.remove(sheet)
    sheet = workbook["Report GP"]
    sheet.sheet_state = "visible"
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "L10"

    # Preserve the uploaded template's visual language while extending every
    # metric to the same four outlet-type columns: WS, Retail, DS, and ON.
    for column in range(10, REMARK_COLUMN + 1):
        sheet.column_dimensions[get_column_letter(column)].hidden = False
        sheet.column_dimensions[get_column_letter(column)].width = 9.0

    # Capture template styles before changing any merged ranges.
    product_style_starts = {
        "c_cola": 25,
        "ize_pet_300ml": 28,
        "ize_pet_1500ml": 31,
    }
    style_sources = {
        (column, row): _capture_cell_style(sheet.cell(row, column))
        for column in range(10, 35)
        for row in range(5, 19)
    }
    location_style_sources = {
        (column, row): _capture_cell_style(sheet.cell(row, column))
        for column in range(8, 10)
        for row in range(5, 19)
    }
    source_widths = {
        column: sheet.column_dimensions[get_column_letter(column)].width or 9.0
        for column in range(10, 35)
    }

    # Rebuild the location, execution, and remark headers while keeping the
    # sales-team area from the uploaded template unchanged.
    for merged in list(sheet.merged_cells.ranges):
        intersects_header = not (
            merged.max_col < 8
            or merged.min_col > REMARK_COLUMN
            or merged.max_row < 5
            or merged.min_row > 9
        )
        if intersects_header:
            sheet.unmerge_cells(str(merged))
    if "D2:AH4" in {str(item) for item in sheet.merged_cells.ranges}:
        sheet.unmerge_cells("D2:AH4")
        sheet.merge_cells("D2:AR4")
    if "B5:AF5" in {str(item) for item in sheet.merged_cells.ranges}:
        sheet.unmerge_cells("B5:AF5")
    sheet.merge_cells("B5:AQ5")
    sheet.merge_cells("H6:K6")
    sheet.merge_cells("H7:I7")
    sheet.merge_cells("H8:H9")
    sheet.merge_cells("I8:I9")
    sheet.merge_cells("J7:J9")
    sheet.merge_cells("K7:K9")
    sheet.merge_cells("L6:AQ6")
    sheet.merge_cells("L7:AQ7")

    # Template style sources: J:L is the standard execution block; Y:AA,
    # AB:AD, and AE:AG contain the three product color treatments.
    for key, _label, start in REPORT_METRICS:
        header_source_start = product_style_starts.get(key, 10)
        body_source_start = 25 if key in product_style_starts else 10
        for offset in range(4):
            header_source_column = header_source_start + min(offset, 2)
            body_source_column = body_source_start + min(offset, 2)
            target_column = start + offset
            for row in range(8, 10):
                _apply_cell_style(
                    style_sources[(header_source_column, row)],
                    sheet.cell(row, target_column),
                )
            for row in range(10, 19):
                _apply_cell_style(
                    style_sources[(body_source_column, row)],
                    sheet.cell(row, target_column),
                )
            sheet.column_dimensions[get_column_letter(target_column)].width = (
                source_widths[header_source_column]
            )

    # Report count and average use the same clean number style as the actual
    # District/Commune coverage columns.  AL remains the red manual input.
    for target_column, source_column in ((REPORT_COUNT_COLUMN, 8), (AVERAGE_COLUMN, 9)):
        for row in range(7, 10):
            _apply_cell_style(
                location_style_sources[(8, row)],
                sheet.cell(row, target_column),
            )
        for row in range(10, 19):
            _apply_cell_style(
                location_style_sources[(source_column, row)],
                sheet.cell(row, target_column),
            )
        sheet.column_dimensions[get_column_letter(target_column)].width = 15
    _apply_cell_style(style_sources[(10, 6)], sheet.cell(6, 12))
    _apply_cell_style(style_sources[(10, 7)], sheet.cell(7, 12))

    # Move the template's remark style from AH to the new AR column.
    for row in range(5, 19):
        _apply_cell_style(style_sources[(34, row)], sheet.cell(row, REMARK_COLUMN))
    sheet.column_dimensions["AR"].width = 15

    for _key, _label, start in REPORT_METRICS:
        sheet.merge_cells(start_row=8, start_column=start, end_row=8, end_column=start + 3)
    sheet.merge_cells("AR5:AR9")

    sheet["B5"] = "Daily Sales Report"
    sheet["H6"] = "Location"
    sheet["H7"] = "Actual Visit Location"
    sheet["H8"] = "District"
    sheet["I8"] = "Commune"
    sheet["J7"] = "# Outlets Visit\nBased on Report"
    sheet["K7"] = "Average #\nof Outlets Visit"
    for address in ("H7", "J7", "K7"):
        sheet[address].alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
            shrink_to_fit=False,
        )
    # These heights keep the multi-line location/report headers readable in
    # Excel at normal zoom instead of letting the words spill into each other.
    sheet.row_dimensions[7].height = 34
    sheet.row_dimensions[8].height = 25
    sheet.row_dimensions[9].height = 25
    sheet.column_dimensions["J"].width = 18
    sheet.column_dimensions["K"].width = 17
    # One combined execution title, matching the approved single-line layout.
    sheet["L6"] = ""
    sheet["L7"] = "Execution Purchase outlet By SKU"
    # Remove the internal horizontal divider left by the former two-line
    # Execution/Purchase header. Preserve the outside border of the block.
    for column in range(12, LAST_METRIC_COLUMN + 1):
        upper = sheet.cell(6, column)
        upper_border = copy(upper.border)
        upper_border.bottom = Side(style=None)
        upper.border = upper_border

        lower = sheet.cell(7, column)
        lower_border = copy(lower.border)
        lower_border.top = Side(style=None)
        lower.border = lower_border
    for _key, label, start in REPORT_METRICS:
        sheet.cell(8, start, label)
        sheet.cell(8, start).alignment = Alignment(
            horizontal="center", vertical="center", wrap_text=True
        )

    for _key, _label, start in REPORT_METRICS:
        for offset, outlet_type in enumerate(OUTLET_TYPES):
            sheet.cell(9, start + offset, outlet_type)

    sheet["AR5"] = "REMARK"
    sheet.conditional_formatting._cf_rules.clear()
    sheet.conditional_formatting.add(
        "L10:AQ17",
        DataBarRule(start_type="min", end_type="max", color="FFFF555A"),
    )
    _apply_reference_report_palette(sheet, 10, 17, 18)
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.print_area = "B2:AR18"
    return sheet


def _prepare_dealer_report_sheet(workbook, report_sheet):
    """Clone the approved GP template and expand it to all 65 dealers."""
    sheet = workbook.copy_worksheet(report_sheet)
    sheet.title = "Report with Dealer"
    data_styles = {
        column: _capture_cell_style(sheet.cell(10, column))
        for column in range(2, REMARK_COLUMN + 1)
    }
    total_styles = {
        column: _capture_cell_style(sheet.cell(18, column))
        for column in range(2, REMARK_COLUMN + 1)
    }
    data_height = sheet.row_dimensions[10].height
    total_height = sheet.row_dimensions[18].height

    # Eight region rows become 65 dealer rows; the old total row moves to 75.
    sheet.insert_rows(18, amount=len(DEALER_TO_REGION) - len(EXPECTED_DEALERS))
    for row in range(DEALER_DATA_START, DEALER_DATA_END + 1):
        sheet.row_dimensions[row].height = data_height
        for column, style in data_styles.items():
            _apply_cell_style(style, sheet.cell(row, column))
            sheet.cell(row, column).value = None
    sheet.row_dimensions[DEALER_TOTAL_ROW].height = total_height
    for column, style in total_styles.items():
        _apply_cell_style(style, sheet.cell(DEALER_TOTAL_ROW, column))
        sheet.cell(DEALER_TOTAL_ROW, column).value = None

    merged = {str(item) for item in sheet.merged_cells.ranges}
    if "B6:C9" in merged:
        sheet.unmerge_cells("B6:C9")
    header_style = _capture_cell_style(sheet["B6"])
    sheet.merge_cells("B6:B9")
    sheet.merge_cells("C6:C9")
    _apply_cell_style(header_style, sheet["B6"])
    _apply_cell_style(header_style, sheet["C6"])
    sheet["B6"] = "REGION"
    sheet["C6"] = "DEALER"
    sheet["B5"] = "Daily Sales Report by Dealer"
    sheet["B2"] = "Region:R1 to R8"
    sheet.freeze_panes = "L10"
    sheet.conditional_formatting._cf_rules.clear()
    sheet.conditional_formatting.add(
        f"L{DEALER_DATA_START}:AQ{DEALER_DATA_END}",
        DataBarRule(start_type="min", end_type="max", color="FFFF555A"),
    )
    _apply_reference_report_palette(
        sheet, DEALER_DATA_START, DEALER_DATA_END, DEALER_TOTAL_ROW
    )
    sheet.print_area = f"B2:AR{DEALER_TOTAL_ROW}"
    sheet.print_title_rows = "1:9"
    return sheet


def _empty_aggregate() -> dict:
    return {
        "SO": set(), "SS": set(), "districts": set(), "communes": set(),
        "reports": 0, "metrics": defaultdict(int),
    }


def _detail_sheet(workbook, title: str, headers: list[str], rows: list[list]) -> None:
    sheet = workbook.create_sheet(title)
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    sheet.append(headers)
    sheet.row_dimensions[1].height = 30
    for row in rows:
        sheet.append(row)
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, sheet.max_row)}"
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Side(style="thin", color=GRID)
    for row in sheet.iter_rows(min_row=2):
        sheet.row_dimensions[row[0].row].height = 30
        for cell in row:
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in sheet["A"][1:]:
        cell.number_format = "dd/mm/yyyy"
    for column in range(1, len(headers) + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 16
    for header, width in {
        "Telegram Name": 28,
        "Outlet Name": 24,
        "Remark Issues": 70,
        "District": 22,
        "Commune": 26,
        "Link Map": 16,
        "Key Wholesale": 18,
        "Tour Program Outlet": 24,
        "Image": 18,
    }.items():
        if header in headers:
            sheet.column_dimensions[get_column_letter(headers.index(header) + 1)].width = width


def export_report_gp(
    db: Database,
    output: Path,
    start_date: str,
    end_date: str,
    template_path: Path,
    staff_master_path: Path,
    *,
    require_complete_al_scan: bool = False,
    so_master_path: Path | None = None,
    allow_current_day_snapshot: bool = False,
    selected_date_mode: bool = False,
    summary_only: bool = False,
) -> Path:
    """Export the uploaded Report GP layout from the 65 Execution groups."""
    if so_master_path is not None:
        from .gp_so_roster import require_report_coverage
        require_report_coverage(db, start_date, end_date, allow_current_day=allow_current_day_snapshot, selected_date_mode=selected_date_mode)
    elif require_complete_al_scan:
        from .team_talk import require_al_coverage
        require_al_coverage(db, start_date, end_date)
    from .gp_scan_snapshot import local_now, scan_snapshot, write_snapshot_audit
    snapshot = scan_snapshot(db, start_date, end_date, track="OUTLET_VISIT" if so_master_path is not None else "TEAM_TALK_V2")
    snapshot_today = local_now().date().isoformat()
    live_rows = [item for item in snapshot if item['Status'] == 'AS_OF_NOW']
    from .coverage_audit import warn_outlet_coverage
    if start_date < snapshot_today and not selected_date_mode:
        yesterday = (datetime.fromisoformat(snapshot_today).date() - timedelta(days=1)).isoformat()
        warn_outlet_coverage(db, start_date, min(end_date, yesterday))
    output.parent.mkdir(parents=True, exist_ok=True)
    # Selected-date GP exports keep only workbooks in the export folder.
    audit_output = output
    if selected_date_mode:
        audit_output = db.path.parent / 'gp_export_audits' / output.name
        audit_output.parent.mkdir(parents=True, exist_ok=True)
    snapshot_audit = write_snapshot_audit(audit_output, snapshot)
    if start_date <= snapshot_today <= end_date:
        print(f"GP today snapshot: {len(live_rows)}/{len(db.groups())} groups scanned today. "
              f"This report includes messages available at scan time; audit: {snapshot_audit}")
    manual_al = _read_manual_al_values(output)
    db.ensure_telegram_ids()
    gps_recovery = db.rebuild_outlet_visit_gps_links(start_date, end_date)
    print(f"Report GP Outlet Visit GPS links rebuilt: {gps_recovery}")
    directory = load_staff_directory(staff_master_path)
    so_roster = None
    if so_master_path is not None:
        from .gp_so_roster import load_so_roster, sync_so_list
        sync_so_list(db, so_master_path, start_date, end_date)
        roster_audit = []
        so_roster = load_so_roster(so_master_path, audit=roster_audit)
        from .gp_so_roster import write_roster_audit
        write_roster_audit(audit_output, roster_audit)
        directory.update({m.telegram_id: m for m in so_roster.values()})
    from .team_talk import resolve_al, write_al_audit
    al_audit = []
    leave = (resolve_al(db, staff_master_path, directory, start_date, end_date, audit=al_audit)
             if so_roster is None else {})
    al_audit_path = write_al_audit(audit_output, al_audit)
    unresolved_al = defaultdict(set)
    for declaration in al_audit:
        if declaration["Status"] in {"UNMATCHED", "AMBIGUOUS"}:
            unresolved_al[declaration["Dealer"]].add(declaration["Name"])
    print(f"Explicit Team Talk AL: {len(leave)} unique person/dealer-days; audit: {al_audit_path}")
    period_days = (datetime.fromisoformat(end_date) - datetime.fromisoformat(start_date)).days + 1
    al_regions = defaultdict(set)
    al_dealers = defaultdict(set)
    al_details = set()
    for (day, dealer, identity), member in leave.items():
        al_details.add((day, dealer, member.telegram_id))
        if member.role in {"SO", "SS"}:
            entry = (day, identity, member.name)
            al_regions[DEALER_TO_REGION[dealer]].add(entry)
            al_dealers[dealer].add(entry)
    from .attendance_reconciliation import reconcile_attendance, write_attendance_audits
    if so_roster is None:
        region_headcounts = _load_region_headcounts(
            staff_master_path, _historical_region_by_telegram_id(db))
        dealer_headcounts = _load_dealer_headcounts(staff_master_path)
        attendance, inferred_al_details, attendance_rows, attendance_audit = reconcile_attendance(
            db, staff_master_path, directory, start_date, end_date, leave,
            region_headcounts, dealer_headcounts)
    else:
        from .gp_so_roster import headcounts, reconcile_so_attendance
        region_headcounts, dealer_headcounts = headcounts(so_roster)
        attendance, inferred_al_details, attendance_rows, attendance_audit = reconcile_so_attendance(
            db, so_roster, start_date, end_date, allow_current_day=allow_current_day_snapshot, selected_date_mode=selected_date_mode)
    write_attendance_audits(audit_output, attendance_rows, attendance_audit)
    for region, calculated_al in attendance["Region"].items():
        planned = region_headcounts[region]
        total = planned.get("SO", 0) + planned.get("SS", 0)
        print(f"{region} attendance reconciliation: Total Sales={total:g}, "
              + ("AL pending until the day ends" if calculated_al is None else f"Active={total-calculated_al:g}, AL={calculated_al:g}"))
    attendance_notes = defaultdict(set)
    for item in attendance_rows:
        if item["Status"] not in {"APPLIED", "APPLIED_REPORT_SENDERS", "APPLIED_SNAPSHOT", "APPLIED_SELECTED_DATE"}:
            attendance_notes[(item["Scope"], item["Key"])].add(item["Status"])
    commune_resolver = default_commune_resolver(db.path.resolve().parent.parent)
    if not commune_resolver.available:
        commune_resolver = default_commune_resolver(Path(__file__).resolve().parent.parent)
    with db.connect() as connection:
        rows = [dict(row) for row in connection.execute(
            """SELECT v.*,m.image_message_id FROM outlet_visits v
               LEFT JOIN product_display_media m ON m.group_id=v.group_id AND m.form_message_id=v.message_id
               LEFT JOIN telegram_groups g ON g.group_id=v.group_id
               WHERE (g.group_id IS NULL OR g.source_kind='EXECUTION')
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(v.submitted_at,1,10) BETWEEN ? AND ?
               ORDER BY v.submitted_at,v.region,v.dealer,v.group_id,v.message_id""",
            (start_date, end_date),
        )]

    rows = [
        row for row in rows
        if not is_excluded_telegram_name(row.get("sender_name") or "")
    ]
    metric_keys = [key for key, _label, _aliases in METRICS]
    aggregates = {region: _empty_aggregate() for region in EXPECTED_DEALERS}
    dealer_aggregates = {dealer: _empty_aggregate() for dealer in DEALER_TO_REGION}
    global_districts: set[str] = set()
    global_communes: set[str] = set()
    remark_rows: list[list] = []
    pin_rows: list[list] = []
    pin_image_links: list[str] = []
    roster_members = {m.telegram_id: m for m in (so_roster or {}).values()}
    from .gp_so_roster import attendance_label
    attendance_status = {
        (item['Date'], item['Telegram ID']):
            attendance_label(item['Status'])
        for item in attendance_audit
    } if so_roster is not None else {}

    for row in rows:
        dealer = normalized_dealer(row.get("dealer"))
        region = DEALER_TO_REGION.get(dealer, "")
        if region not in aggregates or dealer not in dealer_aggregates:
            continue
        telegram_id = normalize_telegram_id(row.get("sender_id"))
        member = directory.get(telegram_id)
        from .gp_so_roster import report_local_day
        report_day = report_local_day(row.get('submitted_at'))
        so_status = attendance_status.get((report_day, telegram_id), 'Not in SO List')
        if member and member.role in {"SO", "SS"}:
            aggregates[region][member.role].add(telegram_id)
            dealer_aggregates[dealer][member.role].add(telegram_id)

        outlet_type = normalize_outlet_type(row.get("outlet_type") or "")
        raw_values = json.loads(row.get("raw_values") or "{}")
        quantities = {
            key: _binary_quantity(raw_values.get(key, ""))[0]
            for key in metric_keys
        }
        if outlet_type in OUTLET_TYPES:
            for key, _label, _start in REPORT_METRICS:
                aggregates[region]["metrics"][(key, outlet_type)] += quantities[key]
                dealer_aggregates[dealer]["metrics"][(key, outlet_type)] += quantities[key]

        coordinate = _coordinate(row)
        # All three report views use the same population: one valid Outlet
        # Visit form with one matched map pin equals one counted visit.
        # District/Commune use official administrative polygons. Repeated pins
        # in one commune (for example Banteay Neang) count only once.
        if coordinate:
            aggregates[region]["reports"] += 1
            dealer_aggregates[dealer]["reports"] += 1
            latitude, longitude = coordinate
            resolved = commune_resolver.resolve(latitude, longitude)
            district_key = district_identity(resolved)
            commune_key = commune_identity(resolved)
            district_name = resolved.district if resolved else ""
            commune_name = resolved.name if resolved else ""
            commune_code = resolved.code if resolved else ""
            if district_key:
                aggregates[region]["districts"].add(district_key)
                dealer_aggregates[dealer]["districts"].add(district_key)
                global_districts.add(district_key)
            if commune_key:
                aggregates[region]["communes"].add(commune_key)
                dealer_aggregates[dealer]["communes"].add(commune_key)
                global_communes.add(commune_key)
            pin_row = [
                _excel_date(row.get("submitted_at")), region, dealer,
                row.get("sender_name"), telegram_id, outlet_type,
                quantities.get("key_wholesale", 0), quantities.get("tour_program", 0),
                "Open Image" if row.get("image_message_id") else "",
                latitude, longitude, district_name, commune_name, commune_code,
                "Open Map",
                so_status if so_roster is not None else
                "AL" if (str(row.get("submitted_at"))[:10], dealer, telegram_id) in al_details else
                "AL (inferred)" if (str(row.get("submitted_at"))[:10], dealer, telegram_id) in inferred_al_details else "",
            ]
            if so_roster is not None:
                pin_row.extend(["Outlet Visit", roster_members[telegram_id].employee_id if telegram_id in roster_members else ""])
            pin_rows.append(pin_row)
            chat_id = str(row["group_id"])
            internal_id = chat_id[4:] if chat_id.startswith("-100") else chat_id.lstrip("-")
            pin_image_links.append(
                f"https://t.me/c/{internal_id}/{int(row['image_message_id'])}"
                if row.get("image_message_id") else ""
            )

        remark = str(row.get("remark_issues") or "").strip()
        if remark:
            # One source report is one row. Never concatenate remarks.
            remark_rows.append([
                _excel_date(row.get("submitted_at")), region, dealer,
                row.get("sender_name"), telegram_id, outlet_type,
                row.get("outlet_name"), remark,
                so_status if so_roster is not None else
                "AL" if (str(row.get("submitted_at"))[:10], dealer, telegram_id) in al_details else
                "AL (inferred)" if (str(row.get("submitted_at"))[:10], dealer, telegram_id) in inferred_al_details else "",
            ])

    workbook = load_workbook(template_path)
    sheet = _prepare_report_sheet(workbook)
    sheet["B2"] = "Region:R1 to R8"
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    sheet["B3"] = (
        f"Date:{start:%d.%m.%Y}"
        if start_date == end_date
        else f"Date:{start:%d}-{end:%d.%m.%Y}"
    )
    if live_rows:
        stamps = [datetime.fromisoformat(item['Scanned At Local']) for item in live_rows]
        earliest_scan, latest_scan = min(stamps), max(stamps)
        sheet['B3'] = sheet['B3'].value + f"\nAs of {earliest_scan:%H:%M}–{latest_scan:%H:%M} (Cambodia)"
        if so_roster is not None:
            sheet['B3'] = sheet['B3'].value + " — Provisional; AL pending"
        sheet['B3'].alignment = Alignment(horizontal='left', vertical='center', wrap_text=True)
        sheet.row_dimensions[3].height = 80 if so_roster is not None else 42
        sheet['B3'].comment = Comment(
            "Current-day live snapshot. Successful group scans ran from "
            f"{earliest_scan.isoformat()} to {latest_scan.isoformat()}. "
            "Messages posted or edited later require a new refresh. This is not a closed full-day report.",
            'Banner Tracker',
        )
    if selected_date_mode:
        sheet['B3'] = f"Date:{start:%d.%m.%Y}" if start_date == end_date else f"Date:{start:%d.%m.%Y} - {end:%d.%m.%Y}"
        sheet['B3'].alignment = Alignment(horizontal='left', vertical='center', wrap_text=False)
        sheet.row_dimensions[3].height = 22
        scans = [x['Scanned At Local'] for x in snapshot if x['Scanned At Local']]
        sheet['B3'].comment = Comment(
            'Selected reporting dates, inclusive. AL = eligible SO-list accounts minus '
            'unique report senders for each selected date, using data available at scan time. '
            'Later reports require a refresh. Scan timestamps: ' +
            (min(scans) + ' through ' + max(scans) if scans else 'unavailable'), 'Banner Tracker')
    sheet["B4"] = f"#Dealer:{sum(len(dealers) for dealers in EXPECTED_DEALERS.values())}"

    for row_index, region in enumerate(EXPECTED_DEALERS, 10):
        aggregate = aggregates[region]
        planned = region_headcounts.get(region, {})
        sheet.cell(row_index, 2, region)
        sheet.cell(row_index, 4, planned.get("SO", len(aggregate["SO"])))
        sheet.cell(row_index, 5, planned.get("SS", len(aggregate["SS"])))
        _write_al(sheet.cell(row_index, 6), manual_al["Report GP"].get(region), al_regions[region], period_days)
        if region in attendance["Region"] and attendance["Region"][region] is not None:
            total = sheet.cell(row_index, 4).value + sheet.cell(row_index, 5).value
            _write_attendance_al(sheet.cell(row_index, 6), manual_al["Report GP"].get(region), total, attendance["Region"][region], report_senders=so_roster is not None)
        sheet.cell(row_index, 7, f"=D{row_index}+E{row_index}-F{row_index}")
        sheet.cell(row_index, 8, len(aggregate["districts"]))
        sheet.cell(row_index, 9, len(aggregate["communes"]))
        sheet.cell(row_index, REPORT_COUNT_COLUMN, aggregate["reports"])
        sheet.cell(
            row_index, AVERAGE_COLUMN,
            f"=IFERROR(J{row_index}/G{row_index},0)",
        )
        sheet.cell(row_index, AVERAGE_COLUMN).number_format = "0"
        for key, _label, start in REPORT_METRICS:
            for offset, outlet_type in enumerate(OUTLET_TYPES):
                sheet.cell(row_index, start + offset, aggregate["metrics"][(key, outlet_type)])
        unresolved = sorted({name for dealer in EXPECTED_DEALERS[region] for name in unresolved_al[dealer]})
        sheet.cell(row_index, REMARK_COLUMN,
                   "AL needs roster match: " + ", ".join(unresolved) if unresolved else "")
        if attendance_notes[("Region", region)]:
            cell = sheet.cell(row_index, REMARK_COLUMN)
            cell.value = (cell.value + "; " if cell.value else "") + ("Attendance review: " if so_roster is not None else "Attendance fallback unavailable: ") + ", ".join(sorted(attendance_notes[("Region", region)]))

    total_row = 18
    sheet.cell(total_row, 2, "Grand Total")
    for column in range(4, LAST_METRIC_COLUMN + 1):
        if column in (8, 9):
            continue
        letter = get_column_letter(column)
        sheet.cell(total_row, column, f"=SUM({letter}10:{letter}17)")
    sheet.cell(total_row, 8, len(global_districts))
    sheet.cell(total_row, 9, len(global_communes))
    sheet.cell(total_row, AVERAGE_COLUMN).number_format = "0"
    sheet.cell(total_row, REPORT_COUNT_COLUMN).comment = Comment(
        "One valid Outlet Visit report form with a matched map pin equals one "
        "outlet visit. This total counts only outlet visits in Pin Detail. "
        "Attendance-only rows do not count. This is not the sum of the eight execution metrics.",
        "Banner Tracker",
    )
    distinct_note = (
        "Global distinct KML areas from all matched scraped map pins. "
        "This is not a sum of the rows because the same area can be covered "
        "by more than one Region or Dealer."
    )
    sheet.cell(total_row, 8).comment = Comment(distinct_note, "Banner Tracker")
    sheet.cell(total_row, 9).comment = Comment(distinct_note, "Banner Tracker")
    sheet.cell(total_row, REMARK_COLUMN, "")

    dealer_sheet = _prepare_dealer_report_sheet(workbook, sheet)
    dealer_sheet["B3"] = sheet["B3"].value
    if live_rows or selected_date_mode:
        dealer_sheet['B3'].alignment = copy(sheet['B3'].alignment)
        dealer_sheet['B3'].comment = copy(sheet['B3'].comment)
        dealer_sheet.row_dimensions[3].height = sheet.row_dimensions[3].height
    dealer_sheet["B4"] = sheet["B4"].value
    row_index = DEALER_DATA_START
    for region, dealers in EXPECTED_DEALERS.items():
        for dealer in dealers:
            aggregate = dealer_aggregates[dealer]
            planned = dealer_headcounts.get(dealer, {})
            dealer_sheet.cell(row_index, 2, region)
            dealer_sheet.cell(row_index, 3, dealer)
            dealer_sheet.cell(row_index, 4, planned.get("SO", len(aggregate["SO"])))
            dealer_sheet.cell(row_index, 5, planned.get("SS", len(aggregate["SS"])))
            _write_al(dealer_sheet.cell(row_index, 6), manual_al["Report with Dealer"].get(dealer), al_dealers[dealer], period_days)
            if dealer in attendance["Dealer"] and attendance["Dealer"][dealer] is not None:
                total = dealer_sheet.cell(row_index, 4).value + dealer_sheet.cell(row_index, 5).value
                _write_attendance_al(dealer_sheet.cell(row_index, 6), manual_al["Report with Dealer"].get(dealer), total, attendance["Dealer"][dealer], report_senders=so_roster is not None)
            dealer_sheet.cell(row_index, 7, f"=D{row_index}+E{row_index}-F{row_index}")
            dealer_sheet.cell(row_index, 8, len(aggregate["districts"]))
            dealer_sheet.cell(row_index, 9, len(aggregate["communes"]))
            dealer_sheet.cell(row_index, REPORT_COUNT_COLUMN, aggregate["reports"])
            dealer_sheet.cell(
                row_index, AVERAGE_COLUMN,
                f"=IFERROR(J{row_index}/G{row_index},0)",
            )
            dealer_sheet.cell(row_index, AVERAGE_COLUMN).number_format = "0"
            for key, _label, metric_start in REPORT_METRICS:
                for offset, outlet_type in enumerate(OUTLET_TYPES):
                    dealer_sheet.cell(row_index, metric_start + offset,
                                      aggregate["metrics"][(key, outlet_type)])
            unresolved = sorted(unresolved_al[dealer])
            dealer_sheet.cell(row_index, REMARK_COLUMN,
                              "AL needs roster match: " + ", ".join(unresolved) if unresolved else "")
            if attendance_notes[("Dealer", dealer)]:
                cell = dealer_sheet.cell(row_index, REMARK_COLUMN)
                cell.value = (cell.value + "; " if cell.value else "") + ("Attendance review: " if so_roster is not None else "Attendance fallback unavailable: ") + ", ".join(sorted(attendance_notes[("Dealer", dealer)]))
            row_index += 1

    dealer_sheet.cell(DEALER_TOTAL_ROW, 2, "Grand Total")
    for column in range(4, LAST_METRIC_COLUMN + 1):
        if column in (8, 9):
            continue
        letter = get_column_letter(column)
        dealer_sheet.cell(
            DEALER_TOTAL_ROW, column,
            f"=SUM({letter}{DEALER_DATA_START}:{letter}{DEALER_DATA_END})",
        )
    if dealer_headcounts:
        # Dealer rows are coverage assignments: a shared SS appears in each
        # dealer they manage. Grand Total remains the unique R1--R8 master
        # headcount and therefore links to the Region report control total.
        dealer_sheet.cell(DEALER_TOTAL_ROW, 4, "='Report GP'!D18")
        dealer_sheet.cell(DEALER_TOTAL_ROW, 5, "='Report GP'!E18")
        dealer_sheet.cell(
            DEALER_TOTAL_ROW,
            7,
            f"=D{DEALER_TOTAL_ROW}+E{DEALER_TOTAL_ROW}-F{DEALER_TOTAL_ROW}",
        )
        unique_note = (
            "Unique active staff headcount from the configured GP roster region control "
            "totals. Dealer rows may include the same SS in multiple dealers "
            "when the master assignment contains more than one dealer."
        )
        dealer_sheet.cell(DEALER_TOTAL_ROW, 4).comment = Comment(
            unique_note, "Banner Tracker"
        )
        dealer_sheet.cell(DEALER_TOTAL_ROW, 5).comment = Comment(
            unique_note, "Banner Tracker"
        )
    if leave or attendance["Region"] or attendance["Dealer"]:
        # Shared assignments must not double-count leave in the control total.
        dealer_sheet.cell(DEALER_TOTAL_ROW, 6, "='Report GP'!F18")
        dealer_sheet.cell(DEALER_TOTAL_ROW, 7,
                          f"=D{DEALER_TOTAL_ROW}+E{DEALER_TOTAL_ROW}-F{DEALER_TOTAL_ROW}")
    dealer_sheet.cell(DEALER_TOTAL_ROW, 8, len(global_districts))
    dealer_sheet.cell(DEALER_TOTAL_ROW, 9, len(global_communes))
    dealer_sheet.cell(DEALER_TOTAL_ROW, AVERAGE_COLUMN).number_format = "0"
    dealer_sheet.cell(DEALER_TOTAL_ROW, REPORT_COUNT_COLUMN).comment = Comment(
        "One valid Outlet Visit report form with a matched map pin equals one "
        "outlet visit. This total counts only outlet visits in Pin Detail. "
        "Attendance-only rows do not count. This is not the sum of the eight execution metrics.",
        "Banner Tracker",
    )
    dealer_sheet.cell(DEALER_TOTAL_ROW, 8).comment = Comment(
        distinct_note, "Banner Tracker"
    )
    dealer_sheet.cell(DEALER_TOTAL_ROW, 9).comment = Comment(
        distinct_note, "Banner Tracker"
    )
    dealer_sheet.cell(DEALER_TOTAL_ROW, REMARK_COLUMN, "")

    if selected_date_mode:
        for summary, first_row, last_row in ((sheet,10,17),(dealer_sheet,DEALER_DATA_START,DEALER_DATA_END)):
            for row_index in range(first_row,last_row+1):
                cell = summary.cell(row_index,6)
                if cell.comment:
                    cell.comment.text = cell.comment.text.splitlines()[0] + '\nAL = roster total minus unique message/location senders for the selected date, based on available scanned data. Refresh to include later reports.'
    if so_roster is not None and any(item.get('AL') is None for item in attendance_rows):
        for summary, scope, first_row, last_row, key_column in (
            (sheet, 'Region', 10, 17, 2),
            (dealer_sheet, 'Dealer', DEALER_DATA_START, DEALER_DATA_END, 3),
        ):
            for row_index in range(first_row, last_row + 1):
                key = summary.cell(row_index, key_column).value
                if attendance[scope].get(key) is not None:
                    continue
                records = [x for x in attendance_rows if x['Scope'] == scope and x['Key'] == key]
                active = sum(x['Active Reports'] for x in records) / len(records)
                pending = sum(x.get('Pending', 0) for x in records) / len(records)
                total = summary.cell(row_index, 4).value + summary.cell(row_index, 5).value
                summary.cell(row_index, 6).value = None
                summary.cell(row_index, 6).comment = Comment(
                    'Provisional current-day snapshot: AL is not finalized. '
                    f'Active report senders: {active:g}; Pending (no report yet): {pending:g}. '
                    'Refresh after the day ends for final AL.', 'Banner Tracker')
                summary.cell(row_index, 7, f'=D{row_index}+E{row_index}-{total-active:.15g}')
                summary.cell(row_index, REMARK_COLUMN,
                             f'Provisional; Pending: {pending:g} (no report yet). AL not final.')
        sheet.cell(18, 6).value = None
        dealer_sheet.cell(DEALER_TOTAL_ROW, 6).value = None
        dealer_sheet.cell(DEALER_TOTAL_ROW, 7, "='Report GP'!G18")
        for summary, total_row_number in ((sheet,18),(dealer_sheet,DEALER_TOTAL_ROW)):
            summary.cell(total_row_number, REMARK_COLUMN, 'Provisional current-day snapshot. AL pending until the day ends.')

    if not summary_only:
        _detail_sheet(
            workbook,
            "Remark Detail",
            ["Date", "Region", "Dealer", "Telegram Name", "Telegram ID", "Outlet Type", "Outlet Name", "Remark Issues", "AL Status"],
            remark_rows,
        )
    outlet_pin_count = len(pin_rows)
    if so_roster is not None:
        from .gp_so_roster import append_attendance_records, validate_pin_attendance
        append_attendance_records(pin_rows, pin_image_links, so_roster, attendance_audit)
        validate_pin_attendance(pin_rows, so_roster, attendance_rows, start_date, end_date)
    if not summary_only:
        _detail_sheet(
            workbook,
            "Pin Detail",
            ["Date", "Region", "Dealer", "Telegram Name", "Telegram ID", "Outlet Type",
             "Key Wholesale", "Tour Program Outlet", "Image",
             "Lat", "Lon", "District", "Commune", "Commune Code", "Link Map", "AL Status"]
            + (["Row Type", "Employee ID"] if so_roster is not None else []),
            pin_rows,
        )
    region_report_total = sum(
        aggregate["reports"] for aggregate in aggregates.values()
    )
    dealer_report_total = sum(
        aggregate["reports"] for aggregate in dealer_aggregates.values()
    )
    if region_report_total != outlet_pin_count or dealer_report_total != outlet_pin_count:
        raise RuntimeError(
            "Report GP reconciliation failed: "
            f"region visits={region_report_total}, "
            f"dealer visits={dealer_report_total}, Pin Detail outlet visits={outlet_pin_count}"
        )
    if not summary_only:
        pin_sheet = workbook["Pin Detail"]
        if so_roster is not None:
            pin_sheet['P1'].comment = Comment(
                'Active = at least one authored message or location for this Cambodia date. Pending = no report yet today. AL = no report '
                'for the completed day. Not in SO List is excluded from staffing totals. '
                'Filter Row Type = Attendance for exactly one roster account per assigned dealer/day. '
                'Attendance records have no outlet location and do not increase visit totals.', 'Banner Tracker')
            if selected_date_mode:
                pin_sheet['P1'].comment = Comment('Selected-date attendance: Active = at least one captured message or location; AL = no captured message or location for that selected date, using available scanned data. Refresh to include later reports. Filter Row Type = Attendance for roster counts; only Outlet Visit rows count as visits.', 'Banner Tracker')
            pin_sheet.column_dimensions['P'].width = 22
            pin_sheet.column_dimensions['Q'].width = 20
            pin_sheet.column_dimensions['R'].width = 18
            for cells in pin_sheet.iter_rows(min_row=2):
                if cells[16].value == 'Attendance' and cells[15].value == 'AL':
                    for cell in cells:
                        cell.fill = PatternFill('solid', fgColor='FFF2CC')
                    cells[15].font = Font(color='C00000', bold=True)
                for index in (3, 4, 17):
                    if cells[index].value is not None:
                        cells[index].data_type = 's'
        for row_index in range(2, pin_sheet.max_row + 1):
            image_url = pin_image_links[row_index - 2]
            if image_url:
                image_cell = pin_sheet.cell(row_index, 9)
                set_clickable_link(image_cell, image_url, "Open Image")
            latitude = pin_sheet.cell(row_index, 10).value
            longitude = pin_sheet.cell(row_index, 11).value
            link_cell = pin_sheet.cell(row_index, 15)
            if latitude is None or longitude is None:
                link_cell.value = ""
                continue
            set_clickable_link(link_cell,
                f"https://www.google.com/maps?q={latitude},{longitude}", "Open Map")
        from .telegram_members import add_members_sheet
        add_members_sheet(workbook, db, audit_output)
    else:
        for name in list(workbook.sheetnames):
            if name not in {'Report GP','Report with Dealer'}:del workbook[name]
    workbook.active = workbook.sheetnames.index("Report GP")
    workbook.calculation.fullCalcOnLoad = True
    workbook.calculation.forceFullCalc = True
    workbook.calculation.calcMode = "auto"
    workbook.save(output)

    check = load_workbook(output, read_only=True, data_only=False)
    try:
        expected_sheets = ["Report GP","Report with Dealer"] if summary_only else [
            "Report GP", "Report with Dealer", "Remark Detail", "Pin Detail", "All_Sale_From_Telegram",
        ]
        if check.sheetnames != expected_sheets:
            raise RuntimeError(f"Unexpected Report GP sheets: {check.sheetnames}")
    finally:
        check.close()
    return output
