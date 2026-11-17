from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from copy import copy
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import MergedCell
from openpyxl.formatting.rule import CellIsRule, DataBarRule
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .database import Database
from .groups import DEALER_TO_REGION, EXPECTED_DEALERS, normalized_dealer, region_for_dealer
from .parser import (
    BannerNormalizer, extract_banner_text, has_meaningful_banner_text,
    is_non_banner_text,
)
from .outlet_visit import normalize_outlet_type
from .outlet_gps import OutletGPSIndex, coordinates, distance_m
from .telegram_identity import stable_telegram_id
from .banner_identity import (select_banner_reports, _banner_duplicate_key,
                              _banner_nearby_key, _nearby_duplicate_distance)


OFFICIAL_BANNERS = [
    "CBC", "CB 4.4", "CB Black", "CB Lite", "C-Cola", "C-ED", "C-Sport",
    "CB Wedding", "Dazz", "Dazz Zero", "Exprez", "Wurkz Ice", "Wurkz", "Water", "IZE",
]

FORMULA_DATA_SHEET = "_Banner_Formula_Data"
DEPLOYED_BRAND_LABELS = {
    "CBC": "CBC", "CB 4.4": "CB 4.4", "CB Black": "CB Black",
    "CB Lite": "CB Lite", "C-Cola": "C-Cola", "C-ED": "C-ED",
    "C-Sport": "C-Sport", "CB Wedding": "CB Wedding", "Dazz": "Dazz",
    "DAZZ ZERO": "Dazz Zero", "Dazz Zero": "Dazz Zero", "Exprez": "Exprez",
    "Wurkz Ice": "Wurkz Ice", "Wurkz": "Wurkz", "Water": "Water", "IZE": "IZE",
}

# Monthly targets shown in the approved 65-dealer reference report.
SEPTEMBER_DEALER_TARGETS = {
    "CA1": 40, "CA2": 25, "CA3": 30, "CA4": 35, "CA5": 25, "CA6": 40, "CA7": 40, "CA8": 65, "CA9": 45, "PTM6": 50,
    "BTI6": 30, "CPH2": 30, "KDL1": 45, "KHT3": 30, "KRV5": 35, "KSV5": 30, "KTB1": 35, "PKB4": 30, "PMR5": 30, "SVA7": 35, "SVR4": 30, "TAK2": 30,
    "CUK7": 30, "KCG4": 45, "KKG3": 20, "KPS7": 35, "KPT2": 30, "KSM1": 35, "KSP3": 35, "KTL6": 30, "KTR4": 30, "PNP6": 30, "SRA5": 30, "TPG2": 30,
    "BTB1": 40, "BVL6": 35, "KRG7": 25, "MRS5": 45, "PLN4": 35, "PUS5": 45, "VVG1": 25,
    "BMC2": 30, "CHK6": 25, "KTH2": 30, "KTM8": 30, "PPR8": 40, "PPT3": 30, "PUK7": 35, "SRP1": 40,
    "BTY8": 40, "KCM1": 35, "MMT3": 30, "STT4": 35, "TKM2": 45,
    "BKE7": 30, "KRT1": 40, "KSA5": 25, "MDK2": 45, "RTK4": 40, "SNL6": 35, "STR3": 40,
    "AVG4": 30, "CKS1": 25, "PVH3": 35, "SMR5": 35,
}

AUGUST_DEALER_TARGETS = {'AVG4': 20,
 'BKE7': 25,
 'BMC2': 20,
 'BTB1': 30,
 'BTI6': 30,
 'BTY8': 15,
 'BVL6': 25,
 'CA1': 20,
 'CA2': 20,
 'CA3': 20,
 'CA4': 20,
 'CA5': 20,
 'CA6': 20,
 'CA7': 20,
 'CA8': 20,
 'CA9': 20,
 'CHK6': 20,
 'CKS1': 20,
 'CPH2': 30,
 'CUK7': 25,
 'KCG4': 25,
 'KCM1': 15,
 'KDL1': 25,
 'KHT3': 25,
 'KKG3': 15,
 'KPS7': 25,
 'KPT2': 25,
 'KRG7': 35,
 'KRT1': 30,
 'KRV5': 35,
 'KSA5': 25,
 'KSM1': 20,
 'KSP3': 25,
 'KSV5': 25,
 'KTB1': 30,
 'KTH2': 25,
 'KTL6': 25,
 'KTM8': 20,
 'KTR4': 30,
 'MDK2': 25,
 'MMT3': 15,
 'MRS5': 30,
 'PKB4': 25,
 'PLN4': 25,
 'PMR5': 35,
 'PNP6': 20,
 'PPR8': 25,
 'PPT3': 20,
 'PTM6': 20,
 'PUK7': 20,
 'PUS5': 35,
 'PVH3': 20,
 'RTK4': 25,
 'SMR5': 20,
 'SNL6': 25,
 'SRA5': 20,
 'SRP1': 25,
 'STR3': 20,
 'STT4': 20,
 'SVA7': 25,
 'SVR4': 30,
 'TAK2': 30,
 'TKM2': 20,
 'TPG2': 35,
 'VVG1': 30}

# Backward-compatible August fallback for legacy non-month-aware callers.
DEALER_TARGETS = AUGUST_DEALER_TARGETS

NAVY = "23364A"
DARK_GRAY = "727272"
LIGHT_BLUE = "D9EAF7"
PINK = "F8CDD3"
RED = "ED1C24"
YELLOW = "FFF200"
WHITE = "FFFFFF"
GRID = "A6A6A6"
BLUE_BAR = "5B9BD5"


def _normalized_mapping(normalizer: BannerNormalizer, banner_text: str) -> tuple[str, int, str]:
    """Create visible official-brand mapping for the Raw export."""
    items = normalizer.parse(banner_text or "")
    matched = [item for item in items if item.status in {"OK", "SPECIAL"}]
    labels = ", ".join(f"{item.official_name}={item.quantity}" for item in matched)
    quantity = sum(item.quantity for item in matched)
    if any(item.status not in {"OK", "SPECIAL"} for item in items):
        status = "NEED_REVIEW"
    elif any(item.status == "SPECIAL" for item in items):
        status = "MATCHED_WITH_SPECIAL" if any(item.status == "OK" for item in items) else "SPECIAL"
    elif matched:
        status = "MATCHED"
    else:
        status = "EMPTY"
    return labels, quantity, status


def _display_item_status(status: str) -> str:
    if status == "OK":
        return "MATCHED"
    if status == "SPECIAL":
        return "SPECIAL"
    return "NEED_REVIEW"


def _unified_status(mapping_status: str, parse_status: str) -> str:
    """One Banner-focused status; imperfect form layout does not reject a valid brand."""
    del parse_status  # Retained in the signature for compatibility with existing calls/tests.
    if mapping_status == "NEED_REVIEW":
        return "NEED_REVIEW"
    if mapping_status in {"SPECIAL", "MATCHED_WITH_SPECIAL"}:
        return "SPECIAL"
    return "MATCHED"


def _excel_date(value) -> date | None:
    """Convert stored ISO timestamps to date-only values safe for Excel."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _excel_datetime(value, timezone_name: str = "Asia/Phnom_Penh") -> datetime | None:
    """Convert stored ISO timestamps to timezone-free Excel datetimes."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo(timezone_name))
    return parsed.replace(tzinfo=None)


def _numeric_quantity(value) -> int:
    """Return a safe non-negative integer for a deployed brand quantity.

    Historical template cells can be blank, numeric strings, floats, or
    formulas with no cached result.  Row totals are exported as finished
    numbers, so Excel's calculation mode can never make a populated row show
    Total=0.
    """
    if value in (None, "") or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    try:
        return max(0, int(float(str(value).strip())))
    except (TypeError, ValueError):
        return 0


def _loose_field(text: str, pattern: str) -> str:
    import re
    match = re.search(
        rf"(?im)^[ \t]*\*?[ \t]*(?:{pattern})[ \t]*(?::|=|-)?[ \t]*(.*)$",
        text or "",
    )
    return match.group(1).strip() if match else ""


def _new_workbook(template_path: Path | None = None) -> Workbook:
    """Create the export workbook, using the approved Aug'26 workbook as a
    visual/reference template when it is available.

    The template's original sheets (especially Banner(R1-R8), Index, Aug'26,
    Data and PO) are preserved. Generated scrape sheets are replaced on each
    export so the output never accumulates stale rows.
    """
    template = Path(template_path) if template_path else None
    if template and template.exists():
        wb = load_workbook(template)
        # The supplied workbook carries obsolete named ranges whose targets are
        # literally #REF!. They are not used by the live Banner formulas and
        # make Excel report a broken-formula warning even when all report cells
        # are correct. Remove only invalid names; keep every valid formula/name.
        for defined_name, definition in list(wb.defined_names.items()):
            if "#REF!" in str(getattr(definition, "attr_text", "") or ""):
                del wb.defined_names[defined_name]
        for worksheet in wb.worksheets:
            for defined_name, definition in list(worksheet.defined_names.items()):
                if "#REF!" in str(getattr(definition, "attr_text", "") or ""):
                    del worksheet.defined_names[defined_name]
        generated = ["Raw", "Detail", "GPS Missing", "Daily by Dealer",
                     "Banner", "Summary", "Scrape_Deployed_Telegram",
                     "Performance_individual_Sales", "Duplicate Reports"]
        for name in generated:
            if name in wb.sheetnames:
                del wb[name]
        required = {"Deployed_Telegram", "Banner(R1-R8)"}
        missing = required.difference(wb.sheetnames)
        if missing:
            raise RuntimeError(f"Banner template is missing required sheet(s): {sorted(missing)}")
        # Only the report sheets are user-facing. Existing
        # calculation-support sheets remain hidden because the approved
        # Banner(R1-R8) template still references them.
        for ws in wb.worksheets:
            ws.sheet_state = "visible" if ws.title in required else "hidden"
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
        wb.calculation.calcMode = "auto"
        return wb

    # Unit tests and legacy callers may intentionally pass a non-existent
    # template path. Keep the original clean-workbook behavior.
    wb = Workbook()
    wb.active.title = "Raw"
    wb.create_sheet("Detail")
    wb.create_sheet("GPS Missing")
    wb.create_sheet("Daily by Dealer")
    wb.create_sheet("Banner")
    wb.create_sheet("Deployed_Telegram")
    wb.create_sheet("Summary")
    wb.calculation.fullCalcOnLoad = True
    wb.calculation.forceFullCalc = True
    wb.calculation.calcMode = "auto"
    return wb


def _repair_template_region_references(wb: Workbook) -> dict[str, int]:
    """Materialize canonical Region values in retained template data sheets.

    Monthly allocation sheets use business-owned support sheets such as
    ``List Sales`` and ``Index`` in XLOOKUP formulas. A historical Region in
    those sheets can therefore override the generated report sheets. Dealer
    is the authoritative Region key across the entire workbook.
    """
    repaired: dict[str, int] = {}
    dealer_headers = {"DEALER", "DEALERS", "DEALER CODE", "STORAGE"}
    for ws in wb.worksheets:
        header_row = region_column = dealer_column = None
        for row_number in range(1, min(ws.max_row, 20) + 1):
            headers = {
                str(ws.cell(row_number, column).value or "").strip().upper(): column
                for column in range(1, ws.max_column + 1)
            }
            region_column = headers.get("REGION")
            dealer_column = next((headers[h] for h in dealer_headers if h in headers), None)
            if region_column and dealer_column:
                header_row = row_number
                break
        if not header_row:
            continue
        changes = 0
        for row_number in range(header_row + 1, ws.max_row + 1):
            dealer = normalized_dealer(ws.cell(row_number, dealer_column).value)
            canonical_region = region_for_dealer(dealer)
            if not canonical_region:
                continue
            region_cell = ws.cell(row_number, region_column)
            if isinstance(region_cell, MergedCell):
                continue
            if region_cell.value != canonical_region:
                region_cell.value = canonical_region
                changes += 1
        if changes:
            repaired[ws.title] = changes
    return repaired


def _master_sales_counts(path: Path | None) -> dict[str, int]:
    """Count distinct sales employee IDs by dealer in MASTER_Name.xlsx."""
    if not path or not Path(path).is_file():
        return {}
    source = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = source["MASTER SCHEDULE "]
        people: dict[str, set[str]] = defaultdict(set)
        for row in ws.iter_rows(min_row=8, values_only=True):
            dealer = str(row[2] or "").strip().upper() if len(row) > 2 else ""
            employee = str(row[3] or "").strip() if len(row) > 3 else ""
            if dealer in DEALER_TO_REGION and employee:
                people[dealer].add(employee)
        return {dealer: len(ids) for dealer, ids in people.items()}
    finally:
        source.close()


def _template_sales_counts(wb: Workbook) -> dict[str, int]:
    """Count the approved sales roster embedded in the Banner template.

    The business-owned ``List Sales`` sheet is authoritative for the Banner
    report.  Counting its nonblank employee names directly avoids depending on
    Excel's cached ``COUNTA`` results and prevents the broader MASTER_Name
    schedule from inflating ``# Sales``.
    """
    if "List Sales" not in wb.sheetnames:
        return {}
    ws = wb["List Sales"]
    header_row = None
    dealer_col = None
    employee_col = None
    for row in range(1, min(ws.max_row, 20) + 1):
        headers = {
            " ".join(str(ws.cell(row, col).value or "").split()).casefold(): col
            for col in range(1, ws.max_column + 1)
        }
        dealer_col = headers.get("dealer")
        employee_col = headers.get("sale name") or headers.get("sales name")
        if dealer_col and employee_col:
            header_row = row
            break
    if not header_row or not dealer_col or not employee_col:
        return {}

    people: dict[str, set[str]] = defaultdict(set)
    for row in range(header_row + 1, ws.max_row + 1):
        dealer = str(ws.cell(row, dealer_col).value or "").strip().upper()
        employee = " ".join(
            str(ws.cell(row, employee_col).value or "").split()
        ).casefold()
        if dealer in DEALER_TO_REGION and employee:
            people[dealer].add(employee)
    return {dealer: len(names) for dealer, names in people.items()}


def _apply_sales_counts(summary, sales_counts: dict[str, int]) -> None:
    """Write dealer, region, and grand sales totals as stable numeric values."""
    for row in range(7, summary.max_row + 1):
        dealer = str(summary.cell(row, 3).value or "").strip().upper()
        label = str(summary.cell(row, 2).value or "").strip().upper()
        if dealer in DEALER_TO_REGION:
            summary.cell(row, 8, sales_counts.get(dealer, 0))
        elif label.startswith("TOTAL R"):
            region = label.removeprefix("TOTAL ").strip()
            summary.cell(
                row,
                8,
                sum(
                    sales_counts.get(item, 0)
                    for item, item_region in DEALER_TO_REGION.items()
                    if item_region == region
                ),
            )
        elif label == "GRAND TOTAL":
            summary.cell(row, 8, sum(sales_counts.values()))


def _unique_sender_key(form: dict) -> str:
    """Count one sender name once per dealer across the selected period."""
    name = " ".join(str(form.get("sender_name") or "").split()).casefold()
    if name:
        return f"name:{name}"
    username = str(form.get("sender_username") or "").strip().lstrip("@").casefold()
    if username:
        return f"username:{username}"
    sender_id = str(form.get("sender_id") or "").strip()
    return f"id:{sender_id}" if sender_id else ""


def _add_banner_people_metrics(
    wb: Workbook, forms: list[dict], details: list, staff_master_path: Path | None,
    outlet_base_path: Path | None = None,
) -> None:
    """Populate # Sales/# Outlets/# Sender and individual brand performance."""
    # Use the approved roster embedded in this month's Banner template.  Fall
    # back to MASTER_Name only for older/legacy templates without List Sales.
    sales_counts = _template_sales_counts(wb) or _master_sales_counts(staff_master_path)
    outlets: dict[str, set[tuple]] = defaultdict(set)
    base_path = outlet_base_path or Path(__file__).resolve().parent.parent / 'templates' / 'outlet.xlsx'
    gps_index = OutletGPSIndex.from_workbook(base_path)
    gps_audit = []
    senders: dict[str, set[str]] = defaultdict(set)
    form_dealer_by_message = {
        int(form.get("message_id") or 0): normalized_dealer(form.get("dealer"))
        for form in forms if int(form.get("message_id") or 0) > 0
    }
    valid_banner_reports: set[tuple[str, int]] = set()
    valid_message_keys: set[tuple[int, int]] = set()
    official_banner_keys = {name.casefold() for name in OFFICIAL_BANNERS}
    for item in details:
        if (
            str(item.get("status") or "") not in {"OK", "SPECIAL"}
            or str(item.get("banner_name") or "").strip().casefold() not in official_banner_keys
            or int(item.get("quantity") or 0) <= 0
        ):
            continue
        message = int(item.get("message_id") or 0)
        group = int(item.get("group_id") or 0)
        dealer = normalized_dealer(item.get("dealer")) or form_dealer_by_message.get(message, "")
        if dealer and message > 0:
            valid_banner_reports.add((dealer, message))
        if group and message > 0:
            valid_message_keys.add((group, message))
    deployed = wb["Deployed_Telegram"]
    deployed_headers: dict[str, int] = {}
    deployed_header_row = 0
    for row_number in range(1, min(20, deployed.max_row) + 1):
        candidate = {
            " ".join(str(deployed.cell(row_number, column).value or "").split()): column
            for column in range(1, deployed.max_column + 1)
            if deployed.cell(row_number, column).value not in (None, "")
        }
        if {"Dealer", "Message ID", "Sender", "Outlet Type"}.issubset(candidate):
            deployed_headers = candidate
            deployed_header_row = row_number
            break
    if not deployed_headers:
        raise RuntimeError(
            "Cannot calculate # Outlets/# Sender: Deployed_Telegram headers are missing."
        )
    if not {'Latitude', 'Longitude'}.issubset(deployed_headers):
        raise RuntimeError('Cannot calculate # Outlets: Latitude/Longitude headers are missing.')
    for row_number in range(deployed_header_row + 1, deployed.max_row + 1):
        dealer = str(deployed.cell(row_number, deployed_headers["Dealer"]).value or "").strip().upper()
        if dealer not in DEALER_TO_REGION:
            continue
        message_id = deployed.cell(row_number, deployed_headers["Message ID"]).value
        try:
            numeric_message_id = int(float(message_id))
        except (TypeError, ValueError):
            numeric_message_id = 0
        has_valid_banner = (dealer, numeric_message_id) in valid_banner_reports
        latitude = deployed.cell(row_number, deployed_headers['Latitude']).value
        longitude = deployed.cell(row_number, deployed_headers['Longitude']).value
        point = coordinates(latitude, longitude)
        match = gps_index.match(latitude, longitude)
        # ``# Outlets`` requires BOTH a valid official Banner quantity and a
        # valid GPS pin. GPS-only rows such as KSP3 "Ws" remain visible for
        # audit but contribute zero to Banner outlets and senders.
        # The outlet master is an audit/reference source only: a new outlet or
        # a slightly stale master must not turn a valid field report into zero.
        # The exported Deployed_Telegram rows have already passed the canonical
        # duplicate-report rule, and this local key protects against accidental
        # repeated rows in a customised workbook.
        sender = deployed.cell(row_number, deployed_headers['Sender']).value
        date_column = deployed_headers.get('Message date') or deployed_headers.get('Message Date')
        message_date = deployed.cell(row_number, date_column).value if date_column else ''
        report_key = (str(message_id) if message_id is not None else f'row:{row_number}',
                      str(sender), str(message_date), coordinates(latitude, longitude))
        counted = int(has_valid_banner and point is not None and report_key not in outlets[dealer])
        if counted:
            outlets[dealer].add(report_key)
            if match['status'] == 'NO_BASE_WITHIN_20M':
                match['status'] = 'VALID_GPS_NO_BASE_MATCH'
        elif not has_valid_banner:
            match['status'] = 'NO_VALID_BANNER'
        elif point is not None:
            match['status'] = 'DUPLICATE_REPORT'
        gps_audit.append([dealer, row_number, str(message_id or ''), latitude, longitude,
                          match['matches'], match['code'], match['distance_m'],
                          match['status'], counted])
        identity = _unique_sender_key({
            "sender_name": deployed.cell(row_number, deployed_headers["Sender"]).value,
            "sender_username": deployed.cell(
                row_number, deployed_headers["Username"]
            ).value if "Username" in deployed_headers else "",
            "sender_id": deployed.cell(
                row_number, deployed_headers["Telegram ID"]
            ).value if "Telegram ID" in deployed_headers else "",
        })
        if has_valid_banner and identity:
            senders[dealer].add(identity)

    summary = wb["Banner(R1-R8)"]
    _apply_sales_counts(summary, sales_counts)
    for row in range(7, summary.max_row + 1):
        dealer = str(summary.cell(row, 3).value or "").strip().upper()
        if dealer in DEALER_TO_REGION:
            summary.cell(row, 9, len(outlets.get(dealer, set())))
            summary.cell(row, 10, len(senders.get(dealer, set())))
        else:
            label = str(summary.cell(row, 2).value or '').strip().upper()
            if label.startswith('TOTAL R'):
                region = label.removeprefix('TOTAL ').strip()
                summary.cell(row, 9, sum(len(keys) for d, keys in outlets.items()
                                         if DEALER_TO_REGION.get(d) == region))
            elif label == 'GRAND TOTAL':
                summary.cell(row, 9, sum(map(len, outlets.values())))

    if 'Outlet_GPS_Audit' in wb.sheetnames:
        del wb['Outlet_GPS_Audit']
    _write_data_sheet(wb.create_sheet('Outlet_GPS_Audit'), [
        'Dealer', 'Deployed Row', 'Message ID', 'Latitude', 'Longitude',
        'Base Rows Within 20m', 'Nearest Outlet Code', 'Distance (m)', 'Status', '# Outlets Contribution',
    ], gps_audit)
    print('Outlet GPS audit:', {
        'reports': len(gps_audit), 'counted': sum(r[-1] for r in gps_audit),
        'unmatched_or_missing': sum(r[-2] in {'VALID_GPS_NO_BASE_MATCH', 'MISSING_OR_INVALID_GPS'} for r in gps_audit),
    })

    brands = ["CBC", "CB 4.4", "CB Black", "CB Lite", "C-Cola", "C-ED",
              "C-Sport", "CB Wedding", "Dazz", "Dazz Zero", "Exprez",
              "Wurkz Ice", "Wurkz", "Water", "IZE"]
    by_key: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    form_lookup = {(int(f["group_id"]), int(f["message_id"])): f for f in forms}
    form_keys: dict[tuple[str, str, str], set[tuple[int, int]]] = defaultdict(set)
    for key_tuple, form in form_lookup.items():
        if key_tuple not in valid_message_keys:
            continue
        if normalize_outlet_type(form.get("outlet_type") or "") not in {"WS", "Retail", "DS", "ON"}:
            continue
        name = str(form.get("sender_name") or "").strip() or (
            "@" + str(form.get("sender_username") or "").lstrip("@")
        ) or "Unknown"
        key = (str(form.get("region") or ""), str(form.get("dealer") or ""), name)
        form_keys[key].add(key_tuple)
    for item in details:
        key_tuple = (int(item["group_id"]), int(item["message_id"]))
        form = form_lookup.get(key_tuple)
        if not form or str(item["status"]) != "OK":
            continue
        name = str(form.get("sender_name") or "").strip() or (
            "@" + str(form.get("sender_username") or "").lstrip("@")
        ) or "Unknown"
        key = (str(form.get("region") or ""), str(form.get("dealer") or ""), name)
        by_key[key][str(item["banner_name"])] += int(item["quantity"] or 0)

    ws = wb.create_sheet("Performance_individual_Sales")
    headers = ["Region", "Dealer", "Telegram Name", *[
        "DAZZ ZERO" if b == "Dazz Zero" else b for b in brands
    ], "Total", "Outlets"]
    rows = []
    for key in sorted(form_keys):
        quantities = [by_key[key].get(brand, 0) for brand in brands]
        rows.append([*key, *quantities, sum(quantities), len(form_keys[key])])
    _write_data_sheet(ws, headers, rows)
    # Keep a visible, auditable footer. Brand totals, overall Total and
    # Outlets are formulas so the footer stays correct if rows are edited.
    grand_row = ws.max_row + 1
    data_end = grand_row - 1
    ws.cell(grand_row, 1, "Grand Total")
    for column in range(4, len(headers) + 1):
        letter = get_column_letter(column)
        ws.cell(
            grand_row, column,
            f"=SUM({letter}2:{letter}{data_end})" if data_end >= 2 else "=0",
        )
        ws.cell(grand_row, column).number_format = "#,##0"
    for column in range(1, len(headers) + 1):
        cell = ws.cell(grand_row, column)
        cell.fill = PatternFill("solid", fgColor="FFE699")
        cell.font = Font(name="Arial", size=10, bold=True, color="000000")
        cell.alignment = Alignment(
            horizontal="left" if column <= 3 else "center",
            vertical="center",
        )
        cell.border = Border(
            top=Side(style="medium", color="7F6000"),
            bottom=Side(style="medium", color="7F6000"),
        )
    ws.row_dimensions[grand_row].height = 22
    ws.freeze_panes = "D2"
    # The Grand Total footer stays outside the filterable individual rows.
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, data_end)}"
    ws.column_dimensions["C"].width = 28


def _write_data_sheet(ws, headers: list[str], rows: list[list]) -> None:
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A2"
    ws.append(headers)
    for row in rows:
        # Telegram text may legitimately start with '='. Prefixing an Excel
        # apostrophe keeps it as literal text instead of a broken formula.
        ws.append([("'" + value) if isinstance(value, str) and value.startswith("=") else value
                   for value in row])

    last_col = get_column_letter(len(headers))
    ws.auto_filter.ref = f"A1:{last_col}{max(1, ws.max_row)}"
    ws.row_dimensions[1].height = 26
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    thin = Side(style="thin", color="D9E2F3")
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    for cell in ws["A"][1:]:
        cell.number_format = "dd/mm/yyyy"

    widths = {}
    for row in ws.iter_rows():
        for cell in row:
            displayed = cell.value.strftime("%d/%m/%Y") if isinstance(cell.value, (date, datetime)) else str(cell.value or "")
            widths[cell.column] = min(36, max(widths.get(cell.column, 0), len(displayed) + 2))
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = max(11, width)


def _formula_key(dealer: object, message_id: object) -> str:
    dealer_text = normalized_dealer(dealer)
    try:
        message_text = str(int(float(message_id)))
    except (TypeError, ValueError):
        message_text = str(message_id or "").strip()
    return f"{dealer_text}|{message_text}"


def _save_with_excel_lock_fallback(wb: Workbook, output: Path) -> Path:
    """Save to the requested path, or a unique refresh path if Excel locks it."""
    try:
        wb.save(output)
        return output
    except PermissionError:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fallback = output.with_name(f"{output.stem}_REFRESH_{stamp}{output.suffix}")
        counter = 2
        while fallback.exists():
            fallback = output.with_name(
                f"{output.stem}_REFRESH_{stamp}_{counter}{output.suffix}"
            )
            counter += 1
        wb.save(fallback)
        print(f"OUTPUT FILE WAS OPEN: saved refreshed workbook as {fallback.resolve()}")
        return fallback



def _restore_deployed_formulas(
    ws, headers: dict[str, int], header_row: int, final_data_row: int,
    normalizer: BannerNormalizer, authoritative_keys: set[str] | None = None,
) -> None:
    """Restore live formulas without losing the parser's audited mappings.

    Brand parsing remains in Python because it handles quantities, dimensions,
    spelling variants and composite values that are unsafe to reproduce with a
    large SEARCH formula. Parsed quantities are stored on a very-hidden support
    sheet. Every visible brand cell, helper date cell, row number and Total is
    then a normal Excel formula again.
    """
    wb = ws.parent
    authoritative_keys = authoritative_keys or set()
    previous: dict[str, dict[str, int]] = {}
    if FORMULA_DATA_SHEET in wb.sheetnames:
        old = wb[FORMULA_DATA_SHEET]
        old_headers = {
            str(old.cell(1, column).value or "").strip(): column
            for column in range(1, old.max_column + 1)
        }
        if "Key" in old_headers:
            for row_number in range(2, old.max_row + 1):
                key = str(old.cell(row_number, old_headers["Key"]).value or "").strip()
                if not key:
                    continue
                saved = previous.setdefault(key, {})
                for label, official in DEPLOYED_BRAND_LABELS.items():
                    if label in old_headers:
                        saved[official] = max(
                            saved.get(official, 0),
                            _numeric_quantity(old.cell(row_number, old_headers[label]).value),
                        )
        del wb[FORMULA_DATA_SHEET]

    visible_brands: list[tuple[str, str, int]] = []
    used_columns: set[int] = set()
    for label, official in DEPLOYED_BRAND_LABELS.items():
        column = headers.get(label)
        if column is not None and column not in used_columns:
            visible_brands.append((label, official, column))
            used_columns.add(column)
    visible_brands.sort(key=lambda item: item[2])

    support = wb.create_sheet(FORMULA_DATA_SHEET)
    support.append(["Key", "Dealer", "Message ID", *[label for label, _, _ in visible_brands]])
    data_start = header_row + 1
    for row_number in range(data_start, final_data_row + 1):
        dealer = normalized_dealer(ws.cell(row_number, headers["Dealer"]).value)
        message_id = ws.cell(row_number, headers["Message ID"]).value
        has_message_id = message_id not in (None, "")
        key = _formula_key(dealer, message_id) if has_message_id else f"{dealer}|ROW:{row_number}"
        message_override = normalizer.record_override(
            ws.cell(row_number, headers["Outlet Name"]).value,
            ws.cell(row_number, headers["Phone"]).value,
            ws.cell(row_number, headers["Latitude"]).value,
            ws.cell(row_number, headers["Longitude"]).value,
        )
        # Blank historical IDs previously shared one dealer-level support key,
        # which copied several brands into each row (EXPREZ 1 could total 7).
        quantities = (
            dict(previous.get(key, {}))
            if has_message_id and key not in authoritative_keys
            else {}
        )

        # Preserve approved historical values, then add newly recognized text
        # only where it improves a previously blank/zero mapping.
        if message_override:
            # A verified message-level correction is authoritative and must
            # replace stale/manual quantities already present in the template.
            quantities = {}
        else:
            for _, official, column in visible_brands:
                quantities[official] = max(
                    quantities.get(official, 0),
                    _numeric_quantity(ws.cell(row_number, column).value),
                )
        parsed: dict[str, int] = defaultdict(int)
        banner_text = str(ws.cell(row_number, headers["Banner"]).value or "")
        # This is authoritative even for a pre-existing template row whose old
        # Wurkz cell contains 1. Never preserve that stale reviewed value.
        if is_non_banner_text(banner_text):
            quantities = {}
        for item in normalizer.parse_for_record(
            ws.cell(row_number, headers["Outlet Name"]).value,
            ws.cell(row_number, headers["Phone"]).value,
            ws.cell(row_number, headers["Latitude"]).value,
            ws.cell(row_number, headers["Longitude"]).value,
            banner_text,
        ):
            if item.status in {"OK", "SPECIAL"} and item.official_name in OFFICIAL_BANNERS:
                parsed[item.official_name] += item.quantity
        for official, quantity in parsed.items():
            # The template's reviewed historical mapping wins. Parsing is a
            # fallback only for a blank mapping (including newly added aliases).
            # This prevents phone/dimension text in a legacy Banner cell from
            # replacing an approved value with an implausibly large quantity.
            if quantities.get(official, 0) <= 0:
                quantities[official] = quantity

        support.append([
            key, dealer, message_id,
            *[quantities.get(official, 0) for _, official, _ in visible_brands],
        ])

    support.sheet_state = "veryHidden"
    support.freeze_panes = "D2"
    support.sheet_view.showGridLines = False
    for row_number in range(data_start, final_data_row + 1):
        if "No" in headers:
            ws.cell(row_number, headers["No"], f'=IF(B{row_number}<>"",ROW()-{header_row},"")')
        if "Day" in headers:
            ws.cell(row_number, headers["Day"], f'=IF($M{row_number}="","",TEXT($M{row_number},"dd"))')
        if "Month" in headers:
            ws.cell(row_number, headers["Month"], f'=IF($M{row_number}="","",TEXT($M{row_number},"mmm"))')
        support_row = row_number - data_start + 2
        for index, (_, _, visible_column) in enumerate(visible_brands, start=4):
            support_letter = get_column_letter(index)
            # Support rows are deliberately one-to-one with visible rows.
            # A direct formula avoids double-counting legacy records whose
            # Telegram message ID is blank or repeated in the old template.
            formula = (
                f"=IFERROR('{FORMULA_DATA_SHEET}'!"
                f"${support_letter}${support_row},0)"
            )
            cell = ws.cell(row_number, visible_column, formula)
            cell.number_format = "0;-0;;"
        if "Total" in headers and visible_brands:
            first_letter = get_column_letter(visible_brands[0][2])
            last_letter = get_column_letter(visible_brands[-1][2])
            ws.cell(
                row_number, headers["Total"],
                f"=SUM({first_letter}{row_number}:{last_letter}{row_number})",
            )

    # Filter totals remain live when users filter any dealer/day/brand.
    for _, _, column in visible_brands:
        letter = get_column_letter(column)
        ws.cell(1, column, f"=SUBTOTAL(9,{letter}{data_start}:{letter}{final_data_row})")
    if "Total" in headers:
        column = headers["Total"]
        letter = get_column_letter(column)
        ws.cell(1, column, f"=SUBTOTAL(9,{letter}{data_start}:{letter}{final_data_row})")


def _refresh_deployed_template_sheet(
    ws, forms: list[dict], normalizer: BannerNormalizer,
    base_through_date: str = "2026-08-18",
    report_start_date: str | None = None,
    report_end_date: str | None = None,
) -> int:
    """Refresh the approved Deployed sheet for one reporting month.

    Rows dated 01--18 Aug already belong to the supplied workbook and are
    preserved for the August report. For later months, template rows outside
    the requested period are removed before that month's database rows are
    appended, keyed by (Dealer, actual Telegram Message ID).
    """
    header_row = None
    headers: dict[str, int] = {}
    for row_number in range(1, min(ws.max_row, 20) + 1):
        row_headers = {
            str(ws.cell(row_number, column).value).strip(): column
            for column in range(1, ws.max_column + 1)
            if ws.cell(row_number, column).value not in (None, "")
        }
        if {"Dealer", "Message date", "Message ID", "Raw"}.issubset(row_headers):
            header_row = row_number
            headers = row_headers
            break
    if header_row is None:
        raise RuntimeError(
            "Template sheet 'Deployed_Telegram' is missing Dealer, Message date, Message ID or Raw."
        )

    # Excel displays this heading on two lines, while the exporter uses the
    # canonical name ``Total``. Keep the approved visual label and expose a
    # stable internal alias so every appended/refreshed row gets its formula.
    if "Total" not in headers:
        for label, column in list(headers.items()):
            if " ".join(label.split()).casefold() in {"total", "total banner"}:
                headers["Total"] = column
                break

    # The approved template predates the Telegram ID requirement. Append the
    # identity columns at the far right so none of the existing Banner formula
    # columns move or lose their formulas.
    header_style_source = headers.get("Sender", ws.max_column)
    for label in ("Username", "Telegram ID"):
        if label in headers:
            continue
        column = ws.max_column + 1
        headers[label] = column
        target = ws.cell(header_row, column, label)
        target._style = copy(ws.cell(header_row, header_style_source)._style)
        target.alignment = copy(ws.cell(header_row, header_style_source).alignment)
        ws.column_dimensions[get_column_letter(column)].width = 18

    # Preserve a complete body-row style before a later month removes every
    # historical August row from the template.
    template_style_row = header_row + 1 if ws.max_row > header_row else header_row
    template_style_cells = [
        copy(ws.cell(template_style_row, column)._style)
        for column in range(1, ws.max_column + 1)
    ]
    template_number_formats = [
        ws.cell(template_style_row, column).number_format
        for column in range(1, ws.max_column + 1)
    ]
    template_alignments = [
        copy(ws.cell(template_style_row, column).alignment)
        for column in range(1, ws.max_column + 1)
    ]
    template_row_height = ws.row_dimensions[template_style_row].height or 18

    # Do not delete rows by Telegram sender here.  All valid Banner reports,
    # including the five guideline/reference accounts, belong in
    # All Banner Tracking in Aug'26.  Combined/Outlet/Product outputs enforce
    # their own sender exclusions independently.

    # Replace fragile historical Region formulas with the canonical R1..R8
    # value from Dealer. Rows without one of the approved 65 dealers are not
    # valid deployment records; removing them also prevents Excel filters from
    # showing Region 0/(Blanks) and Dealer (Blanks).
    invalid_rows = []
    for row_number in range(ws.max_row, header_row, -1):
        dealer = normalized_dealer(ws.cell(row_number, headers["Dealer"]).value)
        canonical_region = region_for_dealer(dealer)
        if not canonical_region:
            invalid_rows.append(row_number)
            continue
        ws.cell(row_number, headers["Dealer"], dealer)
        ws.cell(row_number, headers["Region"], canonical_region)
    # A new monthly template can contain thousands of preformatted blank
    # rows. Delete contiguous blocks, otherwise openpyxl shifts the sheet once
    # per row and makes a simple export take minutes.
    invalid_rows.sort()
    invalid_blocks = []
    for row_number in invalid_rows:
        if not invalid_blocks or row_number != invalid_blocks[-1][1] + 1:
            invalid_blocks.append([row_number, row_number])
        else:
            invalid_blocks[-1][1] = row_number
    for first_row, last_row in reversed(invalid_blocks):
        ws.delete_rows(first_row, last_row - first_row + 1)
    invalid_rows_removed = len(invalid_rows)
    if invalid_rows_removed:
        print(f"Blank/invalid Dealer rows removed from Banner template: {invalid_rows_removed}")

    # Remove orphan quantity rows that have no Dealer or Message date. They are
    # not Telegram reports, but two such cells in the supplied template made
    # the Deployed subtotal exceed the visible Banner grand total by 2.
    orphan_rows_removed = 0
    source_brand_labels = (
        "CBC", "CB 4.4", "CB Black", "CB Lite", "C-Cola", "C-ED", "C-Sport",
        "CB Wedding", "Dazz", "DAZZ ZERO", "Dazz Zero", "Exprez", "Wurkz Ice",
        "Wurkz", "Water", "IZE",
    )
    source_brand_columns = [headers[label] for label in source_brand_labels if label in headers]
    for row_number in range(ws.max_row, header_row, -1):
        dealer = str(ws.cell(row_number, headers["Dealer"]).value or "").strip()
        submitted = _excel_datetime(ws.cell(row_number, headers["Message date"]).value)
        has_quantity = any(
            isinstance(ws.cell(row_number, column).value, (int, float))
            and int(ws.cell(row_number, column).value) > 0
            for column in source_brand_columns
        )
        if has_quantity and (not dealer or submitted is None):
            ws.delete_rows(row_number, 1)
            orphan_rows_removed += 1
    if orphan_rows_removed:
        print(f"Orphan quantity rows removed from Banner template: {orphan_rows_removed}")

    if report_start_date and report_end_date:
        period_start = date.fromisoformat(report_start_date)
        outside_rows = []
        for row_number in range(header_row + 1, ws.max_row + 1):
            submitted = _excel_datetime(ws.cell(row_number, headers["Message date"]).value)
            if submitted is not None and (
                submitted.year, submitted.month
            ) != (period_start.year, period_start.month):
                outside_rows.append(row_number)
        blocks = []
        for row_number in outside_rows:
            if not blocks or row_number != blocks[-1][1] + 1:
                blocks.append([row_number, row_number])
            else:
                blocks[-1][1] = row_number
        for first_row, last_row in reversed(blocks):
            ws.delete_rows(first_row, last_row - first_row + 1)
        if outside_rows:
            print(
                f"Template rows outside {period_start:%Y-%m} removed: "
                f"{len(outside_rows)}"
            )

    base_end = date.fromisoformat(base_through_date)
    if report_start_date and report_end_date:
        # Execution data is source-owned, just like the warehouse fact. A
        # previous export is a layout template, not an additional data source.
        # Rebuild these rows from the canonical forms selected above; otherwise
        # removed duplicates, stale quantities and future days survive reruns.
        execution_rows = []
        for row_number in range(header_row + 1, ws.max_row + 1):
            submitted = _excel_datetime(ws.cell(row_number, headers["Message date"]).value)
            if submitted is not None and submitted.date() > base_end:
                execution_rows.append(row_number)
        blocks = []
        for row_number in execution_rows:
            if not blocks or row_number != blocks[-1][1] + 1:
                blocks.append([row_number, row_number])
            else:
                blocks[-1][1] = row_number
        for first_row, last_row in reversed(blocks):
            ws.delete_rows(first_row, last_row - first_row + 1)
        if execution_rows:
            print(f"Execution template rows rebuilt from canonical source: {len(execution_rows)}")
    mapping_refresh = any(
        str(form.get("source_kind") or "").upper() == "BANNER_MAPPING"
        and (submitted := _excel_datetime(form.get("submitted_at"))) is not None
        and submitted.date() <= base_end
        for form in forms
    )
    if mapping_refresh:
        rows_to_replace = []
        for row_number in range(header_row + 1, ws.max_row + 1):
            submitted = _excel_datetime(ws.cell(row_number, headers["Message date"]).value)
            if submitted is not None and submitted.date() <= base_end:
                rows_to_replace.append(row_number)
        # Delete contiguous blocks instead of thousands of individual rows.
        # This keeps a full 01-18 August refresh fast even with a large template.
        blocks = []
        for row_number in rows_to_replace:
            if not blocks or row_number != blocks[-1][1] + 1:
                blocks.append([row_number, row_number])
            else:
                blocks[-1][1] = row_number
        for first_row, last_row in reversed(blocks):
            ws.delete_rows(first_row, last_row - first_row + 1)
        replaced = len(rows_to_replace)
        print(f"Historical template rows replaced from Banner Mapping: {replaced}")

    last_data_row = header_row
    existing_keys: set[tuple[str, int]] = set()
    dealer_col = headers["Dealer"]
    message_col = headers["Message ID"]
    for row_number in range(header_row + 1, ws.max_row + 1):
        dealer = normalized_dealer(ws.cell(row_number, dealer_col).value)
        raw_id = ws.cell(row_number, message_col).value
        if dealer or raw_id not in (None, ""):
            last_data_row = row_number
        try:
            message_id = int(float(raw_id))
        except (TypeError, ValueError):
            continue
        if dealer and message_id > 0:
            existing_keys.add((dealer, message_id))

    # Backfill identity values on the historical template rows as well as the
    # newly appended rows. ``ensure_telegram_ids`` has already guaranteed every
    # matching database form has a real or stable fallback ID.
    form_identity = {
        (normalized_dealer(form.get("dealer")), int(form.get("message_id") or 0)): form
        for form in forms
        if normalized_dealer(form.get("dealer")) and int(form.get("message_id") or 0) > 0
    }
    for row_number in range(header_row + 1, last_data_row + 1):
        dealer = normalized_dealer(ws.cell(row_number, dealer_col).value)
        raw_message_id = ws.cell(row_number, message_col).value
        try:
            message_id = int(float(raw_message_id))
        except (TypeError, ValueError):
            message_id = 0
        identity = form_identity.get((dealer, message_id)) if message_id > 0 else None
        sender_text = str(ws.cell(row_number, headers.get("Sender", 0)).value or "").strip()
        if identity:
            sender_name = str(identity.get("sender_name") or "").strip()
            username = str(identity.get("sender_username") or "").strip().lstrip("@")
            sender_id = stable_telegram_id(
                identity.get("sender_id"), sender_name, username,
                fallback_key=f"banner:{dealer}:{message_id}",
            )
            # Existing template rows must receive the rebuilt source fields,
            # not only Telegram identity. Previously the database had repaired
            # GPS and Outlet Name values but the approved Excel row kept its
            # old blanks, making a rerun appear not to have changed anything.
            raw_text = str(identity.get("raw_text") or "")
            refreshed_values = {
                "Region": region_for_dealer(dealer),
                "Dealer": dealer,
                "Outlet Name": identity.get("outlet_name") or "",
                "Outlet Type": identity.get("outlet_type") or "",
                "Phone": identity.get("phone") or "",
                "Latitude": identity.get("latitude"),
                "Longitude": identity.get("longitude"),
                "Has": (
                    "Yes" if identity.get("latitude") is not None
                    and identity.get("longitude") is not None else "No"
                ),
                "Message date": _excel_datetime(identity.get("submitted_at")),
                "Raw": raw_text,
                "POSM": _loose_field(raw_text, r"posm"),
                "PROGRAM": _loose_field(raw_text, r"program|progrm"),
            }
            for label, value in refreshed_values.items():
                if label in headers:
                    ws.cell(row_number, headers[label], value)
            if "Banner" in headers:
                current_banner = str(
                    identity.get("original_banner_text")
                    or ws.cell(row_number, headers["Banner"]).value or ""
                )
                ws.cell(
                    row_number, headers["Banner"],
                    normalizer.display_text_for_record(
                        identity.get("outlet_name"), identity.get("phone"),
                        identity.get("latitude"), identity.get("longitude"),
                        current_banner,
                    ),
                )
        else:
            # A small number of the approved historical template rows do not
            # have a matching source row in SQLite. They still need a durable
            # nonblank export identity, derived from the visible Sender.
            username = sender_text.lstrip("@") if sender_text.startswith("@") else ""
            sender_name = sender_text if not sender_text.startswith("@") else ""
            sender_id = stable_telegram_id(
                None, sender_text, username,
                fallback_key=f"banner-template:{dealer}:{raw_message_id or row_number}",
            )
        if sender_name and "Sender" in headers:
            ws.cell(row_number, headers["Sender"], sender_name)
        ws.cell(row_number, headers["Username"], f"@{username}" if username else "")
        ws.cell(row_number, headers["Telegram ID"], str(sender_id))

    if last_data_row > header_row:
        style_row = last_data_row
        style_cells = [copy(ws.cell(style_row, column)._style) for column in range(1, ws.max_column + 1)]
        number_formats = [ws.cell(style_row, column).number_format for column in range(1, ws.max_column + 1)]
        alignments = [copy(ws.cell(style_row, column).alignment) for column in range(1, ws.max_column + 1)]
        style_row_height = ws.row_dimensions[style_row].height or 18
    else:
        style_cells = template_style_cells
        number_formats = template_number_formats
        alignments = template_alignments
        style_row_height = template_row_height

    official_columns = DEPLOYED_BRAND_LABELS
    deployed = []
    for form in forms:
        submitted = _excel_datetime(form.get("submitted_at"))
        source_kind = str(form.get("source_kind") or "EXECUTION").upper()
        if submitted is None:
            continue
        if submitted.date() <= base_end and source_kind != "BANNER_MAPPING":
            continue
        # Banner tracking is based on the report's Banner Type.  GPS is useful
        # metadata but must never hide a valid Banner report from day 19/20.
        if not has_meaningful_banner_text(form.get("original_banner_text") or ""):
            continue
        dealer = normalized_dealer(form.get("dealer"))
        if dealer not in DEALER_TO_REGION:
            continue
        key = (dealer, int(form.get("message_id") or 0))
        if key in existing_keys:
            continue
        deployed.append(form)
    deployed.sort(key=lambda form: (
        _excel_datetime(form.get("submitted_at")) or datetime.min,
        form.get("region") or "", form.get("dealer") or "",
        int(form.get("message_id") or 0),
    ))

    sequence_start = last_data_row - header_row
    for offset, form in enumerate(deployed, 1):
        sequence = sequence_start + offset
        row_number = last_data_row + offset
        raw_text = form.get("raw_text") or ""
        sender = str(form.get("sender_name") or "").strip()
        dealer = normalized_dealer(form.get("dealer"))
        values = {
            "No": sequence,
            "Region": region_for_dealer(dealer),
            "Dealer": dealer,
            "Outlet Name": form.get("outlet_name") or "",
            "Outlet Type": form.get("outlet_type") or "",
            "Phone": form.get("phone") or "",
            "Banner": normalizer.display_text_for_record(
                form.get("outlet_name"), form.get("phone"),
                form.get("latitude"), form.get("longitude"),
                form.get("original_banner_text") or "",
            ),
            "POSM": _loose_field(raw_text, r"posm"),
            "PROGRAM": _loose_field(raw_text, r"program|progrm"),
            "Latitude": form.get("latitude"),
            "Longitude": form.get("longitude"),
            "Has": (
                "Yes" if form.get("latitude") is not None
                and form.get("longitude") is not None else "No"
            ),
            "Message date": _excel_datetime(form.get("submitted_at")),
            "Message ID": int(form.get("message_id") or 0),
            "Sender": sender,
            "Username": (
                "@" + str(form.get("sender_username") or "").strip().lstrip("@")
                if form.get("sender_username") else ""
            ),
            "Telegram ID": str(form.get("sender_id") or ""),
            "Raw": raw_text,
        }
        quantities: dict[str, int] = {}
        for item in normalizer.parse_for_record(
            form.get("outlet_name"), form.get("phone"),
            form.get("latitude"), form.get("longitude"),
            form.get("original_banner_text") or "",
        ):
            if item.status in {"OK", "SPECIAL"} and item.official_name in OFFICIAL_BANNERS:
                quantities[item.official_name] = quantities.get(item.official_name, 0) + item.quantity

        for column in range(1, ws.max_column + 1):
            cell = ws.cell(row_number, column)
            cell._style = copy(style_cells[column - 1])
            cell.number_format = number_formats[column - 1]
            cell.alignment = copy(alignments[column - 1])
        for label, value in values.items():
            if label in headers:
                ws.cell(row_number, headers[label], value)
        submitted = values["Message date"]
        if "Day" in headers:
            ws.cell(row_number, headers["Day"], f"{submitted.day:02d}" if submitted else None)
        if "Month" in headers:
            ws.cell(row_number, headers["Month"], submitted.strftime("%b") if submitted else None)
        for label, official_name in official_columns.items():
            if label in headers:
                ws.cell(row_number, headers[label], quantities.get(official_name, 0) or None)
        if "Total" in headers:
            ws.cell(row_number, headers["Total"], sum(quantities.values()))
        ws.row_dimensions[row_number].height = style_row_height

    final_data_row = last_data_row + len(deployed)

    authoritative_keys = {
        _formula_key(form.get("dealer"), form.get("message_id"))
        for form in forms
        if normalized_dealer(form.get("dealer"))
        and form.get("message_id") not in (None, "")
    }
    _restore_deployed_formulas(
        ws, headers, header_row, final_data_row, normalizer,
        authoritative_keys=authoritative_keys,
    )

    filter_end_row = max(header_row, final_data_row)
    filter_ref = f"A{header_row}:{get_column_letter(ws.max_column)}{filter_end_row}"
    ws.auto_filter.ref = filter_ref
    for table in ws.tables.values():
        table.ref = filter_ref
    # Remove empty formatted row records left behind by the original template
    # and reset the frozen-pane viewport inside the retained data area.
    for row_number in list(ws.row_dimensions):
        if row_number > filter_end_row:
            del ws.row_dimensions[row_number]
    ws.freeze_panes = "Q3"
    return len(deployed)


def _monthly_dealer_targets(reference_path: Path | None, end_date: str) -> dict[str, int]:
    """Load the export month's approved targets from the warehouse sources."""
    from warehouse.etl.targets import discover_target_workbooks, read_all_dealer_targets

    report_date = date.fromisoformat(end_date)
    target_month = report_date.replace(day=1).isoformat()
    primary = reference_path or (
        Path(__file__).resolve().parents[1] / "reference" / "Target_Banner_By_Dealer.xlsx"
    )
    history = primary.parent / "targets"
    if primary.is_file() or history.is_dir():
        paths = discover_target_workbooks(primary, history)
        rows = read_all_dealer_targets(paths)
        loaded = {row["dealer_code"]: row["banner_target"]
                  for row in rows if row["target_month"] == target_month}
        if loaded:
            expected = set(DEALER_TO_REGION)
            if set(loaded) != expected:
                raise ValueError(
                    f"{target_month} must contain all 65 dealers; "
                    f"missing={sorted(expected - set(loaded))}, "
                    f"extra={sorted(set(loaded) - expected)}"
                )
            return loaded
    # Preserve the legacy approved allocations when an older deployment lacks
    # its reference files. Never silently use another month's targets.
    legacy = {(2026, 8): AUGUST_DEALER_TARGETS,
              (2026, 9): SEPTEMBER_DEALER_TARGETS}
    if (report_date.year, report_date.month) in legacy:
        return dict(legacy[(report_date.year, report_date.month)])
    raise ValueError(f"No approved dealer targets found for {target_month}; add the monthly target source.")


def _repair_banner_summary_dealer_layout(wb: Workbook) -> None:
    """Rebuild the visible dealer blocks from the authoritative Region map.

    Old templates physically placed KCG4/KTL6 inside the merged R3 block and
    KRG7 inside R4. Replacing cell values alone cannot move those rows across
    merged Region ranges, so reconstruct the 65 dealer rows and eight total
    rows before the export formulas are written.
    """
    if "Banner(R1-R8)" not in wb.sheetnames:
        return
    ws = wb["Banner(R1-R8)"]
    first_row = 7
    dealer_template_row = next(
        (row for row in range(first_row, ws.max_row + 1)
         if normalized_dealer(ws.cell(row, 3).value) in DEALER_TO_REGION),
        None,
    )
    total_template_row = next(
        (row for row in range(first_row, ws.max_row + 1)
         if str(ws.cell(row, 2).value or "").strip().upper().startswith("TOTAL R")),
        None,
    )
    if dealer_template_row is None or total_template_row is None:
        return

    dealer_styles = [copy(ws.cell(dealer_template_row, col)._style)
                     for col in range(1, ws.max_column + 1)]
    total_styles = [copy(ws.cell(total_template_row, col)._style)
                    for col in range(1, ws.max_column + 1)]
    dealer_height = ws.row_dimensions[dealer_template_row].height
    total_height = ws.row_dimensions[total_template_row].height

    for merged in list(ws.merged_cells.ranges):
        if merged.min_col == 2 and merged.max_col == 2 and merged.min_row >= first_row:
            ws.unmerge_cells(str(merged))

    row_number = first_row
    region_ranges: list[tuple[int, int]] = []
    for region, dealers in EXPECTED_DEALERS.items():
        region_start = row_number
        for dealer in dealers:
            for col, style in enumerate(dealer_styles, 1):
                cell = ws.cell(row_number, col)
                cell._style = copy(style)
                template_value = ws.cell(dealer_template_row, col).value
                if isinstance(template_value, str) and template_value.startswith("="):
                    try:
                        cell.value = Translator(
                            template_value,
                            origin=ws.cell(dealer_template_row, col).coordinate,
                        ).translate_formula(cell.coordinate)
                    except Exception:
                        cell.value = template_value
                else:
                    cell.value = None
            ws.cell(row_number, 3, dealer)
            ws.row_dimensions[row_number].height = dealer_height
            row_number += 1
        region_end = row_number - 1
        ws.cell(region_start, 2, region)
        region_ranges.append((region_start, region_end))

        for col, style in enumerate(total_styles, 1):
            cell = ws.cell(row_number, col)
            cell._style = copy(style)
            cell.value = None
        ws.cell(row_number, 2, f"TOTAL {region}")
        ws.row_dimensions[row_number].height = total_height
        row_number += 1

    # Clear obsolete B/C labels from layouts with different historical block
    # sizes, while preserving the approved blank spacer and Grand Total row.
    for old_row in range(row_number, 80):
        ws.cell(old_row, 2).value = None
        ws.cell(old_row, 3).value = None
    for start, end in region_ranges:
        if end > start:
            ws.merge_cells(start_row=start, start_column=2, end_row=end, end_column=2)
        ws.cell(start, 2).alignment = Alignment(horizontal="center", vertical="center")


def _refresh_banner_summary_values(
    wb: Workbook, end_date: str, dealer_targets: dict[str, int] | None = None,
) -> int:
    """Restore formula-driven dealer, region and grand totals.

    Python still calculates a control total for logging/tests, but every
    visible derived result is written as an Excel formula. This preserves the
    approved workbook behavior when a user filters or edits deployed rows.
    """
    deployed = wb["Deployed_Telegram"]
    summary = wb["Banner(R1-R8)"]
    report_end = date.fromisoformat(end_date)
    dealer_targets = dealer_targets or _monthly_dealer_targets(None, end_date)

    header_row = None
    headers: dict[str, int] = {}
    for row_number in range(1, min(deployed.max_row, 20) + 1):
        candidate = {
            str(deployed.cell(row_number, column).value).strip(): column
            for column in range(1, deployed.max_column + 1)
            if deployed.cell(row_number, column).value not in (None, "")
        }
        if {"Dealer", "Message date"}.issubset(candidate):
            header_row, headers = row_number, candidate
            break
    if header_row is None:
        raise RuntimeError("Cannot recalculate summary: Deployed_Telegram headers are missing.")

    if "Total" not in headers:
        for label, column in list(headers.items()):
            if " ".join(label.split()).casefold() in {"total", "total banner"}:
                headers["Total"] = column
                break

    brand_columns = {
        "CBC": "CBC", "CB 4.4": "CB 4.4", "CB Black": "CB Black",
        "CB Lite": "CB Lite", "C-Cola": "C-Cola", "C-ED": "C-ED",
        "C-Sport": "C-Sport", "CB Wedding": "CB Wedding", "Dazz": "Dazz",
        "DAZZ ZERO": "Dazz Zero", "Dazz Zero": "Dazz Zero", "Exprez": "Exprez",
        "Wurkz Ice": "Wurkz Ice", "Wurkz": "Wurkz", "Water": "Water", "IZE": "IZE",
    }
    source_brand_columns = {
        official: headers[label]
        for label, official in brand_columns.items()
        if label in headers
    }
    valid_dealers = {dealer for dealers in EXPECTED_DEALERS.values() for dealer in dealers}
    dealer_day: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    dealer_brand: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    support_quantities: dict[int, dict[str, int]] = {}

    def quantity(value) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            return max(0, int(value))
        try:
            return max(0, int(float(str(value).strip())))
        except (TypeError, ValueError):
            return 0

    if FORMULA_DATA_SHEET in wb.sheetnames:
        support = wb[FORMULA_DATA_SHEET]
        support_headers = {
            str(support.cell(1, column).value or "").strip(): column
            for column in range(1, support.max_column + 1)
        }
        support_brand_columns: dict[str, int] = {}
        for label, official in DEPLOYED_BRAND_LABELS.items():
            if label in support_headers and official not in support_brand_columns:
                support_brand_columns[official] = support_headers[label]
        for support_row in range(2, support.max_row + 1):
            key = str(support.cell(support_row, 1).value or "").strip()
            if not key:
                continue
            deployed_row = header_row + support_row - 1
            support_quantities[deployed_row] = {
                official: quantity(support.cell(support_row, support_column).value)
                for official in set(source_brand_columns)
                if (support_column := support_brand_columns.get(official)) is not None
            }

    for row_number in range(header_row + 1, deployed.max_row + 1):
        dealer = str(deployed.cell(row_number, headers["Dealer"]).value or "").strip().upper()
        if dealer not in valid_dealers:
            continue
        submitted = _excel_datetime(deployed.cell(row_number, headers["Message date"]).value)
        if (
            submitted is None or submitted.date() > report_end
            or submitted.year != report_end.year or submitted.month != report_end.month
        ):
            continue
        row_total = 0
        support_row = support_quantities.get(row_number, {})
        for official, column in source_brand_columns.items():
            qty = support_row.get(official, quantity(deployed.cell(row_number, column).value))
            dealer_brand[dealer][official] += qty
            row_total += qty
        dealer_day[dealer][submitted.day] += row_total

    day_columns = {
        int(summary.cell(6, column).value): column
        for column in range(1, summary.max_column + 1)
        if isinstance(summary.cell(6, column).value, int)
        and 1 <= int(summary.cell(6, column).value) <= 31
    }
    summary_brand_columns = {}
    for column in range(1, summary.max_column + 1):
        label = str(summary.cell(6, column).value or "").strip()
        official = brand_columns.get(label)
        if official:
            summary_brand_columns[official] = column
    summary_total_column = next(
        (column for column in range(1, summary.max_column + 1)
         if " ".join(str(summary.cell(6, column).value or "").split()).casefold() == "total"),
        None,
    )

    dealer_rows: dict[str, int] = {}
    total_rows: dict[str, int] = {}
    grand_total_row = None
    for row_number in range(7, summary.max_row + 1):
        dealer = str(summary.cell(row_number, 3).value or "").strip().upper()
        region_label = str(summary.cell(row_number, 2).value or "").strip().upper()
        if dealer in valid_dealers:
            dealer_rows[dealer] = row_number
        elif region_label.startswith("TOTAL R"):
            total_rows[region_label.replace("TOTAL ", "")] = row_number
        elif region_label == "GRAND TOTAL":
            grand_total_row = row_number

    # Minimal/custom templates used by integrations may keep the sheet name
    # without the approved day/brand matrix. There is nothing to restore in
    # that case, so leave that sheet untouched.
    if (not day_columns or not summary_brand_columns or not dealer_rows
            or "Total" not in headers or summary_total_column is None):
        return sum(sum(values.values()) for values in dealer_day.values())

    deployed_start = header_row + 1
    deployed_end = max(deployed_start, deployed.max_row)
    deployed_dealer = get_column_letter(headers["Dealer"])
    deployed_date = get_column_letter(headers["Message date"])
    deployed_day = get_column_letter(headers["Day"])
    deployed_total = get_column_letter(headers["Total"])
    first_day_column = min(day_columns.values())
    last_day_column = max(day_columns.values())
    first_brand_column = min(summary_brand_columns.values())
    last_brand_column = max(summary_brand_columns.values())

    target_sheet_name = "_Banner_Month_Targets"
    if target_sheet_name in wb.sheetnames:
        del wb[target_sheet_name]
    target_sheet = wb.create_sheet(target_sheet_name)
    target_sheet.append(["Dealer", "Approved TG", "Report Month"])
    for dealer, target in sorted(dealer_targets.items()):
        target_sheet.append([dealer, target, report_end.strftime("%Y-%m")])
    target_sheet.sheet_state = "veryHidden"

    def sumifs_formula(sum_column: int, row_number: int, day_column: int | None = None) -> str:
        sum_letter = get_column_letter(sum_column)
        next_day = report_end + timedelta(days=1)
        # Entire columns keep inserted/appended deployed rows inside the totals.
        criteria = (
            f"'Deployed_Telegram'!${sum_letter}:${sum_letter},"
            f"'Deployed_Telegram'!${deployed_dealer}:${deployed_dealer},$C{row_number},"
            f"'Deployed_Telegram'!${deployed_date}:${deployed_date},"
            f'">="&DATE({report_end.year},{report_end.month},1),'
            f"'Deployed_Telegram'!${deployed_date}:${deployed_date},"
            f'"<"&DATE({next_day.year},{next_day.month},{next_day.day})'
        )
        if day_column is not None:
            day_letter = get_column_letter(day_column)
            criteria += (
                f",'Deployed_Telegram'!${deployed_day}:${deployed_day},"
                f'TEXT({day_letter}$6,"00")'
            )
        return f"=SUMIFS({criteria})"

    def write_dealer_formulas(dealer: str, row_number: int) -> int:
        for day, column in day_columns.items():
            summary.cell(row_number, column, sumifs_formula(headers["Total"], row_number, column))
        for official, column in summary_brand_columns.items():
            source_column = source_brand_columns.get(official)
            summary.cell(row_number, column,
                         sumifs_formula(source_column, row_number) if source_column else "=0")
        summary.cell(row_number, 4,
                     f"=SUMIF('{target_sheet_name}'!$A:$A,$C{row_number},'{target_sheet_name}'!$B:$B)")
        summary.cell(row_number, 7,
                     f"=SUM({get_column_letter(first_day_column)}{row_number}:"
                     f"{get_column_letter(last_day_column)}{row_number})")
        summary.cell(row_number, 11, f"=IFERROR(G{row_number}/D{row_number},0)")
        summary.cell(row_number, 12, f"=G{row_number}-D{row_number}")
        summary.cell(row_number, summary_total_column,
                     f"=SUM({get_column_letter(first_brand_column)}{row_number}:"
                     f"{get_column_letter(last_brand_column)}{row_number})")
        # Rebuild weekly formulas even if a previously exported template has values.
        for column in range(1, summary.max_column + 1):
            label = str(summary.cell(6, column).value or "").strip().upper()
            if re.fullmatch(r"W[1-5]", label):
                if summary.cell(row_number, column).data_type == "f":
                    continue  # Preserve the approved template's week boundaries.
                week = int(label[1:])
                first_week_end = 7 - date(report_end.year, report_end.month, 1).weekday()
                days = [day for day in sorted(day_columns)
                        if (1 if day <= first_week_end else 2 + (day - first_week_end - 1) // 7) == week]
                refs = ",".join(f"{get_column_letter(day_columns[day])}{row_number}" for day in days)
                summary.cell(row_number, column, f"=SUM({refs})" if refs else "=0")
        return sum(dealer_day[dealer].values())

    formula_columns = sorted({4, 7, 9, 10, 12, *day_columns.values(),
                              *summary_brand_columns.values(), summary_total_column,
                              *(c for c in range(1, summary.max_column + 1)
                                if re.fullmatch(r"W[1-5]", str(summary.cell(6, c).value or "").strip().upper()))})

    def write_contiguous_total(row_number: int, dealer_row_numbers: list[int]) -> None:
        for column in formula_columns:
            letter = get_column_letter(column)
            refs = ",".join(f"{letter}{r}" for r in dealer_row_numbers)
            summary.cell(row_number, column, f"=SUM({refs})")
        summary.cell(row_number, 11, f"=IFERROR(G{row_number}/D{row_number},0)")

    def write_grand_total(row_number: int, source_rows: list[int]) -> None:
        for column in formula_columns:
            letter = get_column_letter(column)
            refs = ",".join(f"{letter}{r}" for r in source_rows)
            summary.cell(row_number, column, f"=SUM({refs})" if refs else "=0")
        summary.cell(row_number, 11, f"=IFERROR(G{row_number}/D{row_number},0)")

    control_total = 0
    for dealer, row_number in dealer_rows.items():
        control_total += write_dealer_formulas(dealer, row_number)
    for region, dealers in EXPECTED_DEALERS.items():
        if region in total_rows:
            rows = [dealer_rows[dealer] for dealer in dealers if dealer in dealer_rows]
            if rows:
                write_contiguous_total(total_rows[region], rows)
    if grand_total_row is not None:
        write_grand_total(
            grand_total_row,
            [total_rows[region] for region in EXPECTED_DEALERS if region in total_rows],
        )
    return control_total


def _style_summary(ws, start_date: str, end_date: str, values: dict[tuple[str, str, str], int]) -> None:
    monthly_targets = _monthly_dealer_targets(None, end_date)
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "F6"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_options.horizontalCentered = True

    last_col = 21  # U
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    ws["A1"] = "KHMER BEVERAGES CO,"
    ws["A1"].font = Font(name="Arial", size=18, bold=True, color="000000")
    ws["A1"].alignment = Alignment(horizontal="left")
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    ws["A2"] = f"All Banner Tracking from {datetime.strptime(start_date, '%Y-%m-%d'):%d/%m/%Y} to {datetime.strptime(end_date, '%Y-%m-%d'):%d/%m/%Y}"
    ws["A2"].font = Font(name="Arial", size=14, bold=True)
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=last_col)
    ws["A3"] = f"DATE: From {datetime.strptime(start_date, '%Y-%m-%d'):%d/%m/%Y} ==> {datetime.strptime(end_date, '%Y-%m-%d'):%d/%m/%Y}"
    ws["A3"].font = Font(name="Arial", size=10, bold=True)

    headers = ["Region", "Dealer", "TG", "Total\nUsage\nMTD", "%\nProgress", *OFFICIAL_BANNERS, "Total"]
    for col, value in enumerate(headers, 1):
        cell = ws.cell(5, col, value)
        cell.fill = PatternFill("solid", fgColor=RED if col == 4 else NAVY)
        cell.font = Font(name="Arial", size=9, color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[5].height = 48

    first_dealer_row = 6
    dealer_rows: list[int] = []
    dealer_blocks: list[tuple[int, int]] = []
    region_total_rows: list[int] = []
    row = first_dealer_row
    for region, dealers in EXPECTED_DEALERS.items():
        region_start = row
        for dealer in dealers:
            dealer_rows.append(row)
            ws.cell(row, 2, dealer)
            ws.cell(row, 3, monthly_targets[dealer])
            for index, banner in enumerate(OFFICIAL_BANNERS, 6):
                ws.cell(row, index, int(values.get((region, dealer, banner), 0)))
            ws.cell(row, 21, f"=SUM(F{row}:T{row})")
            ws.cell(row, 4, f"=U{row}")
            ws.cell(row, 5, f'=IFERROR(D{row}/C{row},0)')
            row += 1

        region_end = row - 1
        dealer_blocks.append((region_start, region_end))
        ws.merge_cells(start_row=region_start, start_column=1, end_row=region_end, end_column=1)
        ws.cell(region_start, 1, region)
        ws.cell(region_start, 1).alignment = Alignment(horizontal="center", vertical="center")

        total_row = row
        region_total_rows.append(total_row)
        ws.merge_cells(start_row=total_row, start_column=1, end_row=total_row, end_column=2)
        ws.cell(total_row, 1, f"TOTAL {region}")
        for col in range(3, 22):
            letter = get_column_letter(col)
            ws.cell(total_row, col, f"=SUM({letter}{region_start}:{letter}{region_end})")
        ws.cell(total_row, 5, f'=IFERROR(D{total_row}/C{total_row},0)')
        row += 1

    grand_row = row
    ws.merge_cells(start_row=grand_row, start_column=1, end_row=grand_row, end_column=2)
    ws.cell(grand_row, 1, "GRAND TOTAL")
    for col in range(3, 22):
        letter = get_column_letter(col)
        refs = ",".join(f"{letter}{r}" for r in region_total_rows)
        ws.cell(grand_row, col, f"=SUM({refs})")
    ws.cell(grand_row, 5, f'=IFERROR(D{grand_row}/C{grand_row},0)')

    thin = Side(style="thin", color=GRID)
    for data_row in range(5, grand_row + 1):
        for col in range(1, 22):
            cell = ws.cell(data_row, col)
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
            cell.font = Font(name="Arial", size=9, bold=(data_row in region_total_rows or data_row == grand_row))
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    for data_row in dealer_rows:
        ws.cell(data_row, 2).font = Font(name="Arial", size=9, bold=True)
        ws.cell(data_row, 4).fill = PatternFill("solid", fgColor=WHITE)
        ws.cell(data_row, 5).fill = PatternFill("solid", fgColor=PINK)
        ws.cell(data_row, 21).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
        for col in range(3, 22):
            ws.cell(data_row, col).number_format = "0;-0;-"
        ws.cell(data_row, 5).number_format = "0%"

    for total_row in [*region_total_rows, grand_row]:
        for col in range(1, 22):
            ws.cell(total_row, col).fill = PatternFill("solid", fgColor=DARK_GRAY)
            ws.cell(total_row, col).font = Font(name="Arial", size=9, color=WHITE, bold=True)
            ws.cell(total_row, col).number_format = "0;-0;-"
        ws.cell(total_row, 5).number_format = "0%"

    # Yellow highlights for zero usage and blue progress bars, matching the reference.
    for block_start, block_end in dealer_blocks:
        ws.conditional_formatting.add(
            f"D{block_start}:D{block_end}",
            CellIsRule(operator="equal", formula=["0"], fill=PatternFill("solid", fgColor=YELLOW)),
        )
        ws.conditional_formatting.add(
            f"E{block_start}:E{block_end}",
            DataBarRule(start_type="num", start_value=0, end_type="num", end_value=1,
                        color=BLUE_BAR, showValue=True),
        )

    widths = {"A": 10, "B": 11, "C": 8, "D": 11, "E": 11, "U": 9}
    for letter, width in widths.items():
        ws.column_dimensions[letter].width = width
    for col in range(6, 21):
        ws.column_dimensions[get_column_letter(col)].width = 9
    for data_row in range(6, grand_row + 1):
        ws.row_dimensions[data_row].height = 20
    ws.auto_filter.ref = f"A5:U{grand_row}"
    ws.print_area = f"A1:U{grand_row}"


def export_excel(db: Database, template_path: Path, output: Path, start_date: str, end_date: str) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    normalizer = BannerNormalizer(Path(__file__).resolve().parent.parent / "config" / "banner_aliases.json")
    # Reparse previously rejected captions with the current parser before GPS
    # recovery and detail rebuilding. Standalone/notebook exports must recover
    # '.Dealer:' reports even when no new Telegram scrape has happened yet.
    recovery = db.recover_forms_from_saved_sources(normalizer, start_date, end_date)
    if recovery["forms_recovered"]:
        print(f"Saved Banner captions recovered before export: {recovery}")
    id_recovery = db.ensure_telegram_ids()
    if id_recovery["total"]:
        print(f"Telegram IDs repaired before Banner export: {id_recovery}")
    # Standalone Banner and Jupyter exports also need the GPS recovery pass.
    # Direct reply links are authoritative; remaining rows may use the nearest
    # unused map from the same sender inside the configured 60-minute window.
    gps_recovery = db.rebuild_gps_links(
        start_date, end_date, same_sender_window_minutes=60,
    )
    if gps_recovery.get("checked"):
        print(f"Banner GPS links rebuilt before export: {gps_recovery}")
    rebuild = db.rebuild_banner_details(normalizer, start_date, end_date)
    print(f"Banner details rebuilt before export: {rebuild}")
    wb = _new_workbook(template_path)
    template_mode = Path(template_path).exists()

    with db.connect() as con:
        accepted_forms = [dict(r) for r in con.execute(
            """SELECT f.*,COALESCE(g.source_kind,'EXECUTION') source_kind FROM banner_forms f
               LEFT JOIN telegram_groups g ON g.group_id=f.group_id
               WHERE (g.group_id IS NULL
                      OR (substr(f.submitted_at,1,10)<='2026-08-18'
                          AND g.source_kind='BANNER_MAPPING')
                      OR (substr(f.submitted_at,1,10)>='2026-08-19'
                          AND g.source_kind='EXECUTION'))
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(f.submitted_at,1,10) BETWEEN ? AND ?
               ORDER BY f.submitted_at,f.region,f.dealer,f.group_id,f.message_id""",
            (start_date, end_date)
        ).fetchall()]
        rejected_forms = [dict(r) for r in con.execute(
            """SELECT i.* FROM invalid_reports i
               LEFT JOIN telegram_groups g ON g.group_id=i.group_id
               WHERE (g.group_id IS NULL
                      OR (substr(i.submitted_at,1,10)<='2026-08-18'
                          AND g.source_kind='BANNER_MAPPING')
                      OR (substr(i.submitted_at,1,10)>='2026-08-19'
                          AND g.source_kind='EXECUTION'))
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(i.submitted_at,1,10) BETWEEN ? AND ?
                 AND NOT EXISTS (SELECT 1 FROM banner_forms f
                                 WHERE f.group_id=i.group_id AND f.message_id=i.message_id)
               ORDER BY i.submitted_at,i.region,i.dealer,i.group_id,i.message_id""",
            (start_date, end_date),
        ).fetchall()]
        details = [dict(r) for r in con.execute(
            """SELECT f.submitted_at,f.region,f.dealer,f.outlet_name,f.phone,d.banner_name,d.quantity,
                      f.latitude,f.longitude,d.status,f.group_id,f.message_id
               FROM banner_forms f JOIN banner_details d USING(group_id,message_id)
               LEFT JOIN telegram_groups g ON g.group_id=f.group_id
               WHERE (g.group_id IS NULL
                      OR (substr(f.submitted_at,1,10)<='2026-08-18'
                          AND g.source_kind='BANNER_MAPPING')
                      OR (substr(f.submitted_at,1,10)>='2026-08-19'
                          AND g.source_kind='EXECUTION'))
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(f.submitted_at,1,10) BETWEEN ? AND ?
               ORDER BY f.submitted_at,f.region,f.dealer""",
            (start_date, end_date),
        ).fetchall()]
        summary = con.execute(
            """SELECT f.region,f.dealer,d.banner_name,SUM(d.quantity) qty
               FROM banner_forms f JOIN banner_details d USING(group_id,message_id)
               LEFT JOIN telegram_groups g ON g.group_id=f.group_id
               WHERE (g.group_id IS NULL
                      OR (substr(f.submitted_at,1,10)<='2026-08-18'
                          AND g.source_kind='BANNER_MAPPING')
                      OR (substr(f.submitted_at,1,10)>='2026-08-19'
                          AND g.source_kind='EXECUTION'))
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(f.submitted_at,1,10) BETWEEN ? AND ?
                 AND d.status IN ('OK','SPECIAL')
               GROUP BY f.region,f.dealer,d.banner_name""", (start_date, end_date)
        ).fetchall()
        daily = con.execute(
            """SELECT substr(f.submitted_at,1,10) report_date,f.region,f.dealer,f.sender_name,
                      f.sender_username,f.sender_id,COUNT(*) form_count,
                      SUM(CASE WHEN f.gps_status LIKE 'GPS_MATCHED%' THEN 1 ELSE 0 END) gps_matched,
                      SUM(CASE WHEN f.gps_status NOT LIKE 'GPS_MATCHED%' THEN 1 ELSE 0 END) gps_missing,
                      SUM(CASE WHEN EXISTS (
                          SELECT 1 FROM banner_details rd
                          WHERE rd.group_id=f.group_id AND rd.message_id=f.message_id
                            AND rd.status NOT IN ('OK','SPECIAL')
                      ) THEN 1 ELSE 0 END) review_count,
                      COALESCE(SUM((SELECT SUM(d.quantity) FROM banner_details d
                                   WHERE d.group_id=f.group_id AND d.message_id=f.message_id
                                     AND d.status='OK')),0) banner_quantity
                      ,COALESCE(SUM((SELECT SUM(d.quantity) FROM banner_details d
                                   WHERE d.group_id=f.group_id AND d.message_id=f.message_id
                                     AND d.status='SPECIAL' AND d.banner_name='Banner Concert')),0) concert_quantity
                      ,COALESCE(SUM((SELECT SUM(d.quantity) FROM banner_details d
                                   WHERE d.group_id=f.group_id AND d.message_id=f.message_id
                                     AND d.status='SPECIAL' AND d.banner_name='Banner Theater')),0) theater_quantity
               FROM banner_forms f
               LEFT JOIN telegram_groups g ON g.group_id=f.group_id
               WHERE (g.group_id IS NULL
                      OR (substr(f.submitted_at,1,10)<='2026-08-18'
                          AND g.source_kind='BANNER_MAPPING')
                      OR (substr(f.submitted_at,1,10)>='2026-08-19'
                          AND g.source_kind='EXECUTION'))
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(f.submitted_at,1,10) BETWEEN ? AND ?
               GROUP BY substr(f.submitted_at,1,10),f.region,f.dealer,f.sender_id,f.sender_name,f.sender_username
               ORDER BY substr(f.submitted_at,1,10),f.region,f.dealer,f.sender_name,f.sender_id""",
            (start_date, end_date),
        ).fetchall()

    # One physical outlet/banner installation can be submitted by a Team
    # Leader and an SO. Count it once regardless of sender. The earliest
    # Telegram message is authoritative and remains visible for audit.
    detail_signatures: dict[tuple[int, int], list[tuple[str, int, str]]] = defaultdict(list)
    for item in details:
        # Duplicate identity is based on counted Banner quantities only.  A
        # rejected/review token is audit metadata and must not make Excel keep
        # a row that Gold/Power BI suppresses as the same installation.
        if str(item["status"]) not in {"OK", "SPECIAL"}:
            continue
        detail_signatures[(int(item["group_id"]), int(item["message_id"]))].append(
            (str(item["banner_name"]), int(item["quantity"] or 0), str(item["status"]))
        )
    # Match Gold/Power BI eligibility: a deployed row must contain at least
    # one accepted Banner detail, not only a form/header record.
    accepted_forms = [
        form for form in accepted_forms
        if detail_signatures.get((int(form["group_id"]), int(form["message_id"])))
    ]
    accepted_forms = [f for f in accepted_forms
                      if has_meaningful_banner_text(f.get("original_banner_text") or "")]
    accepted_forms, decisions = select_banner_reports(accepted_forms, details)
    canonical_message_keys = {(int(f["group_id"]), int(f["message_id"])) for f in accepted_forms}
    duplicate_message_keys = set()
    duplicate_audit_rows = []
    for decision in decisions:
        form, kept = decision["form"], decision["kept"]
        business_key = decision["business_key"]
        signature = decision["signature"]
        nearby_distance = decision["distance_m"]
        duplicate_message_keys.add((int(form["group_id"]), int(form["message_id"])))
        duplicate_audit_rows.append([
            business_key[0], business_key[1], form.get("outlet_name"), business_key[3],
            business_key[4], business_key[5],
            ", ".join(f"{name}={qty}" for name, qty, status in signature),
            kept.get("message_id"), kept.get("sender_name"),
            form.get("message_id"), form.get("sender_name"),
            (f"Same date/dealer/outlet/banner; GPS within {nearby_distance:.1f} m; phone and sender ignored"
             if nearby_distance is not None else "Same date/dealer/outlet/phone/Lat/Lon/Banner; sender ignored"),
        ])
    if duplicate_message_keys:
        accepted_forms = [f for f in accepted_forms if (int(f["group_id"]), int(f["message_id"])) in canonical_message_keys]
        details = [d for d in details if (int(d["group_id"]), int(d["message_id"])) in canonical_message_keys]
        print(f"Duplicate outlet/banner reports suppressed: {len(duplicate_message_keys)}")

        # SQL aggregates were calculated before canonical-message selection;
        # rebuild dealer/brand totals from the deduplicated detail records.
        summary_totals: dict[tuple[str, str, str], int] = defaultdict(int)
        for item in details:
            # Gold/Power BI treats both OK and SPECIAL as valid mapped Banner
            # quantities. Excel must use the identical metric definition,
            # including after duplicate-message suppression.
            if str(item["status"]) in {"OK", "SPECIAL"}:
                summary_totals[(str(item["region"]), str(item["dealer"]), str(item["banner_name"]))] += int(item["quantity"] or 0)
        summary = [
            {"region": k[0], "dealer": k[1], "banner_name": k[2], "qty": value}
            for k, value in summary_totals.items()
        ]

    if "Duplicate Reports" in wb.sheetnames:
        del wb["Duplicate Reports"]
    duplicate_ws = wb.create_sheet("Duplicate Reports")
    _write_data_sheet(duplicate_ws, [
        "Date", "Dealer", "Outlet Name", "Phone", "Latitude", "Longitude",
        "Normalized Banner", "Kept Message ID", "Kept Sender",
        "Duplicate Message ID", "Duplicate Sender", "Detection Rule",
    ], duplicate_audit_rows)

    # Rebuild daily sender statistics from canonical messages so duplicate
    # submissions do not survive in non-template exports either.
    items_by_message: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for item in details:
        items_by_message[(int(item["group_id"]), int(item["message_id"]))].append(item)
    daily_totals: dict[tuple, dict] = {}
    for form in accepted_forms:
        report_date = str(form.get("submitted_at") or "")[:10]
        key = (report_date, form.get("region"), form.get("dealer"), form.get("sender_id"),
               form.get("sender_name"), form.get("sender_username"))
        record = daily_totals.setdefault(key, {
            "report_date": report_date, "region": form.get("region"), "dealer": form.get("dealer"),
            "sender_id": form.get("sender_id"), "sender_name": form.get("sender_name"),
            "sender_username": form.get("sender_username"), "form_count": 0,
            "gps_matched": 0, "gps_missing": 0, "review_count": 0,
            "banner_quantity": 0, "concert_quantity": 0, "theater_quantity": 0,
        })
        record["form_count"] += 1
        if str(form.get("gps_status") or "").startswith("GPS_MATCHED"):
            record["gps_matched"] += 1
        else:
            record["gps_missing"] += 1
        message_items = items_by_message.get((int(form["group_id"]), int(form["message_id"])), [])
        record["review_count"] += int(any(str(x["status"]) not in {"OK", "SPECIAL"} for x in message_items))
        for item in message_items:
            status, name, quantity = str(item["status"]), str(item["banner_name"]), int(item["quantity"] or 0)
            if status == "OK": record["banner_quantity"] += quantity
            elif status == "SPECIAL" and name == "Banner Concert": record["concert_quantity"] += quantity
            elif status == "SPECIAL" and name == "Banner Theater": record["theater_quantity"] += quantity
    daily = list(daily_totals.values())

    # Banner exports intentionally keep every sender.  The five configured
    # guideline/reference accounts are excluded only by the output-specific
    # exporters that require that rule (for example Combined Summary).

    loose_field = _loose_field

    if template_mode and "Deployed_Telegram" in wb.sheetnames:
        base_through = db.get_state("banner_base_through_date") or "2026-08-18"
        appended = _refresh_deployed_template_sheet(
            wb["Deployed_Telegram"], accepted_forms, normalizer, base_through,
            report_start_date=start_date, report_end_date=end_date,
        )
        summary_ws = wb["Banner(R1-R8)"]
        # The supplied template has =B1 in B1, a direct self-reference that
        # triggers Excel's circular-reference warning and can leave filters and
        # formula caches stale.  B1 is intentionally blank in the layout.
        summary_ws["B1"] = None
        title_start = datetime.strptime(start_date, "%Y-%m-%d")
        title_end = datetime.strptime(end_date, "%Y-%m-%d")
        # Preserve the approved template's unpadded first month (01/8/2026)
        # while still adapting the value to every selected reporting month.
        summary_ws["B3"] = f"All Banner Tracking in {title_start:%b %Y}"
        summary_ws["C4"] = (
            f"From {title_start.day:02d}/{title_start.month}/{title_start.year} "
            f"==>{title_end:%d/%m/%Y}"
        )
        target_reference = Path(template_path).parent.parent / "reference" / "Target_Banner_By_Dealer.xlsx"
        month_targets = _monthly_dealer_targets(target_reference, end_date)
        _repair_banner_summary_dealer_layout(wb)
        calculated_total = _refresh_banner_summary_values(wb, end_date, month_targets)
        print(
            f"Banner TG loaded for {end_date[:7]}: "
            f"{sum(month_targets.values()):,} from {target_reference}"
        )
        _add_banner_people_metrics(
            wb, accepted_forms, details,
            Path(template_path).parent / "MASTER_Name.xlsx",
        )
        reference_repairs = _repair_template_region_references(wb)
        if reference_repairs:
            print(f"Canonical Region references repaired: {reference_repairs}")
        # The approved formulas use Deployed_Telegram entire columns, so all
        # appended day/brand quantities automatically roll into daily and MTD
        # totals when Excel opens the file.
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
        wb.calculation.calcMode = "auto"
        wb.calculation.calcId = 0
        wb.active = wb.sheetnames.index("Banner(R1-R8)")
        # Excel locks an open .xlsx on Windows. Do not fail a long Telegram
        # scrape/export after all calculations have completed.
        output = _save_with_excel_lock_fallback(wb, output)
        check = load_workbook(output, read_only=True, data_only=False)
        visible = [ws.title for ws in check.worksheets if ws.sheet_state == "visible"]
        expected_visible = {
            "Deployed_Telegram", "Banner(R1-R8)", "Performance_individual_Sales",
            "Outlet_GPS_Audit", "Duplicate Reports"
        }
        if set(visible) != expected_visible or len(visible) != len(expected_visible):
            check.close()
            raise RuntimeError(f"Unexpected visible Banner sheets: {visible}")
        if check["Banner(R1-R8)"]["B1"].data_type == "f":
            check.close()
            raise RuntimeError("Banner workbook still contains the B1 circular reference.")
        check.close()
        print(f"Template rows appended from database: {appended}")
        print(f"Banner summary recalculated total: {calculated_total}")
        return output
    rejected_mapped: list[dict] = []

    raw_headers = ["Date", "Region", "Dealer", "Outlet Name", "Phone", "Outlet Type", "Banner Type",
                   "Matched Banner Type", "Matched Quantity", "Status",
                   "Lat", "Lon", "Telegram Group ID", "Message ID", "Sender Telegram ID", "Sender Name",
                   "Username", "GPS Status", "Raw Source", "Format Errors", "Raw Text"]
    raw_rows = []
    gps_missing_rows = []
    for r in accepted_forms:
        banner_text = r["original_banner_text"] or ""
        if not has_meaningful_banner_text(banner_text):
            continue
        matched_text, matched_quantity, mapping_status = _normalized_mapping(normalizer, banner_text)
        status = _unified_status(mapping_status, r["parse_status"])
        output_row = [
            _excel_date(r["submitted_at"]), r["region"], r["dealer"], r["outlet_name"], r["phone"],
            r["outlet_type"], banner_text, matched_text, matched_quantity, status,
            r["latitude"], r["longitude"], str(r["group_id"]), r["message_id"],
            str(r["sender_id"] or ""), r["sender_name"], r["sender_username"],
            r["gps_status"], "Accepted", "", r["raw_text"] or "",
        ]
        if r["latitude"] is None or r["longitude"] is None:
            gps_missing_rows.append(output_row)
        else:
            raw_rows.append(output_row)
    for r in rejected_forms:
        text = r["raw_text"] or ""
        banner_text = extract_banner_text(text)
        if not has_meaningful_banner_text(banner_text):
            continue
        matched_text, matched_quantity, mapping_status = _normalized_mapping(normalizer, banner_text)
        items = normalizer.parse(banner_text)
        rejected_mapped.append({"report": r, "text": text, "banner_text": banner_text,
                                "items": items, "mapping_status": mapping_status})
        # Wrong-format reports do not own coordinate columns. Keep them in the
        # explicit GPS audit sheet instead of leaving blank Lat/Lon in Raw.
        gps_missing_rows.append([
            _excel_date(r["submitted_at"]), r["region"], r["dealer"],
            loose_field(text, r"outlet\s*name"), loose_field(text, r"phone|telephone|tel"),
            loose_field(text, r"outlet\s*type|outlet(?!\s*name)"), banner_text,
            matched_text, matched_quantity, _unified_status(mapping_status, "NEED_REVIEW"), None, None,
            str(r["group_id"]), r["message_id"], str(r["sender_id"] or ""),
            r["sender_name"], r["sender_username"], "",
            "Rejected format", r["error_message"], text,
        ])
    raw_rows.sort(key=lambda row: (row[0] or date.min, row[1], row[2], row[12], row[13]))
    _write_data_sheet(wb["Raw"], raw_headers, raw_rows)
    raw_ws = wb["Raw"]
    raw_ws.column_dimensions["S"].width = 18
    raw_ws.column_dimensions["T"].width = 42
    raw_ws.column_dimensions["U"].width = 60
    for row_number in range(2, raw_ws.max_row + 1):
        raw_ws.row_dimensions[row_number].height = 30
        raw_ws.cell(row_number, 20).alignment = Alignment(vertical="top", wrap_text=True)
        raw_ws.cell(row_number, 21).alignment = Alignment(vertical="top", wrap_text=False)

    gps_missing_rows.sort(key=lambda row: (row[0] or date.min, row[1], row[2], row[12], row[13]))
    _write_data_sheet(wb["GPS Missing"], raw_headers, gps_missing_rows)
    missing_ws = wb["GPS Missing"]
    missing_ws.column_dimensions["S"].width = 18
    missing_ws.column_dimensions["T"].width = 42
    missing_ws.column_dimensions["U"].width = 60

    detail_headers = ["Date", "Region", "Dealer", "Outlet Name", "Phone", "Product Type", "Quantity", "Lat", "Lon",
                      "Item Status", "Telegram Group ID", "Message ID"]
    detail_rows = [
        [_excel_date(r["submitted_at"]), r["region"], r["dealer"], r["outlet_name"], r["phone"],
         r["banner_name"], r["quantity"], r["latitude"], r["longitude"], _display_item_status(r["status"]),
         str(r["group_id"]), r["message_id"]]
        for r in details if r["latitude"] is not None and r["longitude"] is not None
    ]
    # Rejected-format rows remain available in GPS Missing and still contribute
    # recognized quantities to Summary, but Detail contains coordinate-complete
    # rows only.
    detail_rows.sort(key=lambda row: (row[0] or date.min, row[1], row[2], row[10], row[11], row[5]))
    _write_data_sheet(wb["Detail"], detail_headers, detail_rows)

    daily_headers = [
        "Date", "Region", "Dealer", "Telegram Name", "Username", "Telegram ID",
        "Total Forms", "Official Banner Quantity", "Banner Concert Qty", "Banner Theater Qty",
        "GPS Matched", "GPS Missing", "Need Review",
    ]
    daily_records = {}
    for r in daily:
        record = dict(r)
        key = (str(record["report_date"]), record["region"], record["dealer"],
               record["sender_id"], record["sender_name"], record["sender_username"])
        daily_records[key] = record

    for entry in rejected_mapped:
        r, items = entry["report"], entry["items"]
        report_date = _excel_date(r["submitted_at"])
        report_date_text = report_date.isoformat() if report_date else str(r["submitted_at"])[:10]
        key = (report_date_text, r["region"], r["dealer"], r["sender_id"],
               r["sender_name"], r["sender_username"])
        record = daily_records.setdefault(key, {
            "report_date": report_date_text, "region": r["region"], "dealer": r["dealer"],
            "sender_name": r["sender_name"], "sender_username": r["sender_username"],
            "sender_id": r["sender_id"], "form_count": 0, "gps_matched": 0,
            "gps_missing": 0, "review_count": 0, "banner_quantity": 0,
            "concert_quantity": 0, "theater_quantity": 0,
        })
        record["form_count"] += 1
        record["gps_missing"] += 1
        record["review_count"] += int(entry["mapping_status"] == "NEED_REVIEW")
        record["banner_quantity"] += sum(item.quantity for item in items if item.status == "OK")
        record["concert_quantity"] += sum(
            item.quantity for item in items
            if item.status == "SPECIAL" and item.official_name == "Banner Concert"
        )
        record["theater_quantity"] += sum(
            item.quantity for item in items
            if item.status == "SPECIAL" and item.official_name == "Banner Theater"
        )

    daily_rows = [
        [_excel_date(r["report_date"]), r["region"], r["dealer"], r["sender_name"] or "Unknown",
         ("@" + r["sender_username"].lstrip("@")) if r["sender_username"] else "",
         str(r["sender_id"] or ""), int(r["form_count"]), int(r["banner_quantity"]),
         int(r["concert_quantity"]), int(r["theater_quantity"]),
         int(r["gps_matched"]), int(r["gps_missing"]), int(r["review_count"])]
        for r in daily_records.values()
    ]
    daily_rows.sort(key=lambda row: (row[0] or date.min, row[1], row[2], row[3], row[5]))
    _write_data_sheet(wb["Daily by Dealer"], daily_headers, daily_rows)
    daily_ws = wb["Daily by Dealer"]
    for col in range(7, 14):
        for cell in daily_ws[get_column_letter(col)][1:]:
            cell.number_format = "#,##0"
            cell.alignment = Alignment(horizontal="center", vertical="center")
    daily_ws.auto_filter.ref = f"A1:M{max(1, daily_ws.max_row)}"

    # Coordinate-aware Banner sheet matching the requested reference layout.
    # Location metadata is reconstructed from the locations table so the sheet
    # contains the actual Location Message ID and match direction.
    location_rows = {}
    with db.connect() as con:
        for loc in con.execute(
            """SELECT * FROM locations
               WHERE date(sent_at) BETWEEN ? AND ?""",
            (start_date, end_date),
        ).fetchall():
            location_rows[(int(loc["group_id"]), int(loc["message_id"]))] = dict(loc)

    def banner_location_info(form: dict) -> tuple[str, str, int, object]:
        """Return Has Location, Match Type, Match Score, Location Message ID."""
        group_id = int(form["group_id"])
        form_message_id = int(form["message_id"])
        candidate = None

        # Prefer the location explicitly linked to this form.
        with db.connect() as con:
            loc = con.execute(
                """SELECT * FROM locations
                   WHERE group_id=? AND matched_form_message_id=?
                   ORDER BY ABS(message_id-?) LIMIT 1""",
                (group_id, form_message_id, form_message_id),
            ).fetchone()
            if loc:
                candidate = dict(loc)

        if not candidate:
            return "No", "", 0, ""

        location_message_id = int(candidate["message_id"])
        location_sender_id = candidate.get("sender_id")
        form_sender_id = form.get("sender_id")
        reply_to = candidate.get("reply_to_message_id")

        if reply_to == form_message_id:
            return "Yes", "REPLY", 10, location_message_id

        same_sender = (
            location_sender_id is not None and form_sender_id is not None
            and int(location_sender_id) == int(form_sender_id)
        )
        if same_sender:
            match_type = "FORWARD_SAME_SENDER" if location_message_id > form_message_id else "BACKWARD_SAME_SENDER"
            return "Yes", match_type, 6, location_message_id

        match_type = "FORWARD_OTHER_SENDER" if location_message_id > form_message_id else "BACKWARD_OTHER_SENDER"
        return "Yes", match_type, 4, location_message_id

    banner_headers = [
        "Dealer", "Outlet Name", "Outlet Type", "Phone", "Banner Type", "POSM", "Program",
        "Latitude", "Longitude", "Has Location", "Match Type", "Match Score", "Message Date",
        "Message ID", "Location Message ID", "Sender", "Sender ID", "Raw Text",
    ]
    banner_rows = []
    for r in accepted_forms:
        raw_text = r["raw_text"] or ""
        has_location, match_type, match_score, location_message_id = banner_location_info(r)
        sender = (
            "@" + r["sender_username"].lstrip("@")
            if r["sender_username"] else (r["sender_name"] or "")
        )
        banner_rows.append([
            r["dealer"],
            r["outlet_name"],
            r["outlet_type"],
            r["phone"],
            r["original_banner_text"] or "",
            loose_field(raw_text, r"posm"),
            loose_field(raw_text, r"program|progrm"),
            r["latitude"],
            r["longitude"],
            has_location,
            match_type,
            match_score,
            _excel_datetime(r["submitted_at"]),
            r["message_id"],
            location_message_id,
            sender,
            r["sender_id"],
            raw_text,
        ])

    banner_rows.sort(key=lambda row: (row[12] or datetime.min, row[0] or "", row[13] or 0))
    _write_data_sheet(wb["Banner"], banner_headers, banner_rows)
    # Keep the legacy sheet name and legacy columns for existing notebooks/tests.
    legacy_headers = [
        "No", "Region", "Dealer", "Outlet Name", "Outlet Type", "Phone", "Banner",
        "POSM", "PROGRAM", "Latitude", "Longitude", "Has", "Message date",
        "Message ID", "Sender", "Raw",
    ]
    legacy_rows = []
    for number, r in enumerate(accepted_forms, 1):
        if r["latitude"] is None or r["longitude"] is None:
            continue
        raw_text = r["raw_text"] or ""
        sender = (
            "@" + r["sender_username"].lstrip("@")
            if r["sender_username"] else (r["sender_name"] or "")
        )
        legacy_rows.append([
            number, r["region"], r["dealer"], r["outlet_name"], r["outlet_type"], r["phone"],
            r["original_banner_text"] or "", loose_field(raw_text, r"posm"),
            loose_field(raw_text, r"program|progrm"), r["latitude"], r["longitude"],
            "Yes" if r["latitude"] is not None and r["longitude"] is not None else "No",
            _excel_datetime(r["submitted_at"]), r["message_id"], sender, raw_text,
        ])
    legacy_rows.sort(key=lambda row: (row[12] or datetime.min, row[1], row[2], row[13] or 0))
    for number, row_data in enumerate(legacy_rows, 1):
        row_data[0] = number
    legacy_sheet_name = "Scrape_Deployed_Telegram" if template_mode else "Deployed_Telegram"
    _write_data_sheet(wb[legacy_sheet_name], legacy_headers, legacy_rows)
    for cell in wb[legacy_sheet_name]["A"][1:]:
        cell.number_format = "0"
    banner_ws = wb["Banner"]
    for cell in banner_ws["M"][1:]:
        cell.number_format = "dd/mm/yyyy hh:mm"
    for letter, width in {
        "A": 11, "B": 32, "C": 14, "D": 16, "E": 30, "F": 24, "G": 24,
        "H": 14, "I": 14, "J": 13, "K": 24, "L": 12, "M": 20, "N": 13,
        "O": 20, "P": 24, "Q": 15, "R": 70,
    }.items():
        banner_ws.column_dimensions[letter].width = width
    for row_number in range(2, banner_ws.max_row + 1):
        banner_ws.row_dimensions[row_number].height = 34
        banner_ws.cell(row_number, 18).alignment = Alignment(vertical="top", wrap_text=False)

    summary_values = {(r["region"], r["dealer"], r["banner_name"]): int(r["qty"]) for r in summary}
    for entry in rejected_mapped:
        r = entry["report"]
        for item in entry["items"]:
            if item.status == "OK" and item.official_name in OFFICIAL_BANNERS:
                key = (r["region"], r["dealer"], item.official_name)
                summary_values[key] = summary_values.get(key, 0) + item.quantity
    _style_summary(wb["Summary"], start_date, end_date, summary_values)

    wb.active = wb.sheetnames.index("Summary")
    wb.save(output)
    # Immediate structural re-open catches invalid workbook parts before returning.
    check = load_workbook(output, read_only=True, data_only=False)
    check.close()
    return output
