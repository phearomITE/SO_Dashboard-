from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .database import Database
from .groups import EXPECTED_DEALERS
from .parser import BannerNormalizer, parse_product_display, product_display_count
from .telegram_identity import normalize_telegram_name, stable_telegram_id


BANNERS = [
    "CBC", "CB 4.4", "CB Black", "CB Lite", "C-Cola", "C-ED", "C-Sport",
    "CB Wedding", "Dazz", "Dazz Zero", "Exprez", "Wurkz Ice", "Wurkz", "Water", "IZE",
]
OUTLET_FIELDS = [
    ("Emergency Outlet", "emergency_outlet"),
    ("Attacking Stock", "attacking_stock"),
    ("Tour Program", "tour_program"),
    ("Key Wholesale", "key_wholesale"),
    ("Water", "water"),
    ("C-Cola", "c_cola"),
    ("Ize PET 300mL", "ize_pet_300ml"),
    ("Ize PET 1500mL", "ize_pet_1500ml"),
]

NAVY, BLUE, TEAL, GREEN = "23364A", "4472C4", "2F75B5", "548235"
WHITE, LIGHT_BLUE, GRID = "FFFFFF", "D9EAF7", "D9E2F3"
REGION_TOTAL, GRAND_TOTAL = "7F8C8D", "44546A"
REGION_BY_DEALER = {
    dealer: region for region, dealers in EXPECTED_DEALERS.items() for dealer in dealers
}


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in (
        "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
        "%d/%m/%Y", "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _quantity(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(0, int(value))
    try:
        return max(0, int(float(str(value or "").strip())))
    except (TypeError, ValueError):
        return 0


def _find_headers(ws, required: set[str]) -> tuple[int, dict[str, int]]:
    for row_number in range(1, min(ws.max_row, 20) + 1):
        headers = {
            str(ws.cell(row_number, column).value).strip(): column
            for column in range(1, ws.max_column + 1)
            if ws.cell(row_number, column).value not in (None, "")
        }
        if required.issubset(headers):
            return row_number, headers
    raise RuntimeError(
        f"Required columns {sorted(required)} were not found in sheet {ws.title!r}."
    )


def _ensure_record(
    records: dict, report_date, region, dealer, display_name,
    username: object = "", telegram_id: object = None,
) -> dict | None:
    dealer = str(dealer or "").strip().upper()
    region = str(region or "").strip().upper() or REGION_BY_DEALER.get(dealer, "")
    display_name = " ".join(str(display_name or "Unknown").strip().split()) or "Unknown"
    if report_date is None or not dealer or not region:
        return None
    clean_username = str(username or "").strip().lstrip("@")
    resolved_id = stable_telegram_id(
        telegram_id, display_name, clean_username,
        fallback_key=f"combined:{report_date}:{region}:{dealer}:{display_name}",
    )
    key = (report_date, region, dealer, resolved_id)
    record = records.setdefault(key, {
        "date": report_date, "region": region, "dealer": dealer, "name": display_name,
        "username": clean_username, "telegram_id": resolved_id,
        "banners": defaultdict(int), "outlet": defaultdict(int), "product_display": 0,
    })
    if display_name and display_name != "Unknown":
        record["name"] = display_name
    if clean_username:
        record["username"] = clean_username
    return record


def _open_source(path: Path, sheet_name: str):
    if not path.is_file():
        raise FileNotFoundError(
            f"Combined Summary source is missing: {path}. Run export_all first."
        )
    workbook = load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in workbook.sheetnames:
        workbook.close()
        raise RuntimeError(f"{path.name} is missing required sheet {sheet_name!r}.")
    return workbook, workbook[sheet_name]


def _records_from_workbooks(
    banner_path: Path, outlet_path: Path, product_path: Path,
    banner_start: str, outlet_start: str, product_start: str, end_date: str,
) -> tuple[dict, dict[str, int]]:
    """Read the three approved exported workbooks as the combined source of truth."""
    records: dict = {}
    starts = {
        "banner": date.fromisoformat(banner_start),
        "outlet": date.fromisoformat(outlet_start),
        "product": date.fromisoformat(product_start),
    }
    report_end = date.fromisoformat(end_date)
    counts = {"banner_rows": 0, "outlet_rows": 0, "product_rows": 0}
    normalizer = BannerNormalizer(
        Path(__file__).resolve().parent.parent / "config" / "banner_aliases.json"
    )

    workbook, ws = _open_source(banner_path, "Deployed_Telegram")
    try:
        header_row, headers = _find_headers(ws, {"Dealer", "Message date", "Sender"})
        brand_columns: dict[str, int] = {}
        for official in BANNERS:
            for label in (("DAZZ ZERO", "Dazz Zero") if official == "Dazz Zero" else (official,)):
                if label in headers:
                    brand_columns[official] = headers[label]
                    break
        missing = [name for name in BANNERS if name not in brand_columns]
        if missing:
            raise RuntimeError(f"{banner_path.name} is missing Banner columns: {missing}")
        formula_support: list[dict[str, int]] = []
        if "_Banner_Formula_Data" in workbook.sheetnames:
            support = workbook["_Banner_Formula_Data"]
            support_header_row, support_headers = _find_headers(support, {"Key"})
            support_labels = {
                "CBC": "CBC", "CB 4.4": "CB 4.4", "CB Black": "CB Black",
                "CB Lite": "CB Lite", "C-Cola": "C-Cola", "C-ED": "C-ED",
                "C-Sport": "C-Sport", "CB Wedding": "CB Wedding", "Dazz": "Dazz",
                "DAZZ ZERO": "Dazz Zero", "Dazz Zero": "Dazz Zero",
                "Exprez": "Exprez", "Wurkz Ice": "Wurkz Ice", "Wurkz": "Wurkz",
                "Water": "Water", "IZE": "IZE",
            }
            for values in support.iter_rows(min_row=support_header_row + 1, values_only=True):
                mapped: dict[str, int] = {}
                for label, official in support_labels.items():
                    if label in support_headers:
                        mapped[official] = max(
                            mapped.get(official, 0),
                            _quantity(values[support_headers[label] - 1]),
                        )
                formula_support.append(mapped)
        for data_index, values in enumerate(
            ws.iter_rows(min_row=header_row + 1, values_only=True)
        ):
            report_date = _as_date(values[headers["Message date"] - 1])
            if report_date is None or not (starts["banner"] <= report_date <= report_end):
                continue
            sender = values[headers["Sender"] - 1]
            dealer = values[headers["Dealer"] - 1]
            region = values[headers["Region"] - 1] if "Region" in headers else None
            record = _ensure_record(
                records, report_date, region, dealer, sender,
                values[headers["Username"] - 1] if "Username" in headers else "",
                values[headers["Telegram ID"] - 1] if "Telegram ID" in headers else None,
            )
            if record is None:
                continue
            row_quantity = 0
            for banner_name, column in brand_columns.items():
                quantity = _quantity(values[column - 1])
                record["banners"][banner_name] += quantity
                row_quantity += quantity
            # openpyxl cannot calculate formulas, so a just-generated workbook
            # may not yet contain cached formula results. Prefer the exporter's
            # audited numeric support data, then reparse text only for older
            # workbooks that predate the formula-support sheet.
            if row_quantity == 0 and data_index < len(formula_support):
                for banner_name, quantity in formula_support[data_index].items():
                    record["banners"][banner_name] += quantity
                    row_quantity += quantity
            if row_quantity == 0 and "Banner" in headers and not formula_support:
                parsed = normalizer.parse(values[headers["Banner"] - 1])
                for item in parsed:
                    if item.status != "OK" or item.official_banner not in BANNERS:
                        continue
                    quantity = _quantity(item.quantity)
                    record["banners"][item.official_banner] += quantity
                    row_quantity += quantity
            if row_quantity:
                counts["banner_rows"] += 1
    finally:
        workbook.close()

    workbook, ws = _open_source(outlet_path, "Outlet_Visit")
    try:
        required = {"Date", "Region", "Dealer", "Telegram Name"}
        required.update(label for label, _ in OUTLET_FIELDS)
        header_row, headers = _find_headers(ws, required)
        for values in ws.iter_rows(min_row=header_row + 1, values_only=True):
            report_date = _as_date(values[headers["Date"] - 1])
            if report_date is None or not (starts["outlet"] <= report_date <= report_end):
                continue
            sender = values[headers["Telegram Name"] - 1]
            record = _ensure_record(
                records, report_date, values[headers["Region"] - 1],
                values[headers["Dealer"] - 1], sender,
                values[headers["Username"] - 1] if "Username" in headers else "",
                values[headers["Telegram ID"] - 1] if "Telegram ID" in headers else None,
            )
            if record is None:
                continue
            any_quantity = False
            for label, column_name in OUTLET_FIELDS:
                quantity = _quantity(values[headers[label] - 1])
                record["outlet"][column_name] += quantity
                any_quantity = any_quantity or quantity > 0
            if any_quantity:
                counts["outlet_rows"] += 1
    finally:
        workbook.close()

    workbook, ws = _open_source(product_path, "Detail")
    try:
        header_row, headers = _find_headers(
            ws, {"Date", "Region", "Dealer", "Telegram Name", "Product Display"}
        )
        for values in ws.iter_rows(min_row=header_row + 1, values_only=True):
            report_date = _as_date(values[headers["Date"] - 1])
            if report_date is None or not (starts["product"] <= report_date <= report_end):
                continue
            sender = values[headers["Telegram Name"] - 1]
            if not product_display_count(values[headers["Product Display"] - 1]):
                continue
            record = _ensure_record(
                records, report_date, values[headers["Region"] - 1],
                values[headers["Dealer"] - 1], sender,
                values[headers["Username"] - 1] if "Username" in headers else "",
                values[headers["Telegram ID"] - 1] if "Telegram ID" in headers else None,
            )
            if record is not None:
                record["product_display"] += 1
                counts["product_rows"] += 1
    finally:
        workbook.close()
    return records, counts


def _records_from_database(
    db: Database, banner_start: str, outlet_start: str, product_start: str, end_date: str,
) -> tuple[dict, dict[str, int]]:
    """Compatibility source for older scripts and focused unit tests."""
    records: dict = {}
    counts = {"banner_rows": 0, "outlet_rows": 0, "product_rows": 0}

    def ensure(row) -> dict | None:
        display = str(row["sender_name"] or "").strip()
        username = str(row["sender_username"] or "").strip()
        display = display or (f"@{username.lstrip('@')}" if username else str(row["sender_id"] or "Unknown"))
        return _ensure_record(
            records, _as_date(row["submitted_at"]), row["region"], row["dealer"], display,
            username, row["sender_id"],
        )

    with db.connect() as con:
        banner_rows = con.execute(
            """SELECT f.submitted_at,f.region,f.dealer,f.sender_id,f.sender_name,
                      f.sender_username,d.banner_name,d.quantity
               FROM banner_forms f JOIN banner_details d USING(group_id,message_id)
               LEFT JOIN telegram_groups g ON g.group_id=f.group_id
               WHERE (g.group_id IS NULL OR g.source_kind='EXECUTION')
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(f.submitted_at,1,10) BETWEEN ? AND ? AND d.status='OK'""",
            (banner_start, end_date),
        ).fetchall()
        outlet_rows = con.execute(
            """SELECT v.* FROM outlet_visits v
               LEFT JOIN telegram_groups g ON g.group_id=v.group_id
               WHERE (g.group_id IS NULL OR g.source_kind='EXECUTION')
                 AND (g.group_id IS NULL OR g.is_active=1)
                 AND substr(v.submitted_at,1,10) BETWEEN ? AND ?""",
            (outlet_start, end_date),
        ).fetchall()
        product_sources = [
            con.execute(
                """SELECT f.group_id,f.message_id,f.submitted_at,f.region,f.dealer,f.sender_id,
                          f.sender_name,f.sender_username,f.raw_text,f.product_display
                   FROM banner_forms f
                   LEFT JOIN telegram_groups g ON g.group_id=f.group_id
                   WHERE (g.group_id IS NULL OR g.source_kind='PRODUCT_DISPLAY')
                     AND (g.group_id IS NULL OR g.is_active=1)
                     AND substr(f.submitted_at,1,10) BETWEEN ? AND ?""",
                (product_start, end_date),
            ).fetchall(),
        ]
    for row in banner_rows:
        record = ensure(row)
        if record is not None and row["banner_name"] in BANNERS:
            record["banners"][row["banner_name"]] += int(row["quantity"] or 0)
            counts["banner_rows"] += 1
    for row in outlet_rows:
        record = ensure(row)
        if record is not None:
            for _, column in OUTLET_FIELDS:
                record["outlet"][column] += int(row[column] or 0)
            counts["outlet_rows"] += 1
    seen_product: set[tuple[int, int]] = set()
    for source_rows in product_sources:
        for row in source_rows:
            message_key = (int(row["group_id"]), int(row["message_id"]))
            if message_key in seen_product:
                continue
            if parse_product_display(row["raw_text"] or "") != "Yes" and str(
                row["product_display"] or ""
            ).strip().casefold() != "yes":
                continue
            seen_product.add(message_key)
            record = ensure(row)
            if record is not None:
                record["product_display"] += 1
                counts["product_rows"] += 1
    return records, counts


def export_combined_summary(
    db: Database,
    output: Path,
    start_date: str,
    end_date: str,
    *,
    banner_start_date: str | None = None,
    outlet_start_date: str | None = None,
    product_start_date: str | None = None,
    banner_workbook: Path | None = None,
    outlet_workbook: Path | None = None,
    product_workbook: Path | None = None,
) -> Path:
    """Export merged daily detail with a subtotal after every date/region block.

    When workbook paths are supplied they are the authoritative source.  This
    deliberately includes the historical Banner rows already present in the
    finished Banner workbook; Telegram continuation cutoffs must never be used
    as reporting cutoffs for Combined Summary.
    """
    id_recovery = db.ensure_telegram_ids()
    if id_recovery["total"]:
        print(f"Telegram IDs repaired before Combined export: {id_recovery}")
    banner_start = max(start_date, banner_start_date or start_date)
    outlet_start = max(start_date, outlet_start_date or start_date)
    product_start = max(start_date, product_start_date or start_date)
    sources = (banner_workbook, outlet_workbook, product_workbook)
    if any(sources) and not all(sources):
        raise ValueError("Pass all three source workbooks, or pass none of them.")
    if all(sources):
        records, source_counts = _records_from_workbooks(
            Path(banner_workbook), Path(outlet_workbook), Path(product_workbook),
            banner_start, outlet_start, product_start, end_date,
        )
        source_mode = "three exported workbooks"
    else:
        records, source_counts = _records_from_database(
            db, banner_start, outlet_start, product_start, end_date
        )
        source_mode = "database fallback"

    output.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Combined Summary"
    ws.sheet_view.showGridLines = False
    headers = [
        "Date", "Region", "Dealer", "Telegram Name", "Username", "Telegram ID",
        *[("DAZZ ZERO" if name == "Dazz Zero" else name) for name in BANNERS],
        *[name for name, _ in OUTLET_FIELDS], "Product Display",
    ]
    last_col = len(headers)
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    ws["A1"] = "KHMER BEVERAGES CO., LTD"
    ws["A1"].font = Font(size=18, bold=True, color=WHITE)
    ws["A1"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A1"].alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 32
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    ws["A2"] = "Combined Banner, Outlet Visit & Product Display Summary"
    ws["A2"].font = Font(size=13, bold=True, color=WHITE)
    ws["A2"].fill = PatternFill("solid", fgColor=NAVY)
    ws["A2"].alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=last_col)
    ws["A3"] = (
        f"DATE: From {datetime.strptime(start_date, '%Y-%m-%d'):%d/%m/%Y} "
        f"==> {datetime.strptime(end_date, '%Y-%m-%d'):%d/%m/%Y}"
    )
    ws["A3"].font = Font(bold=True, color=NAVY)

    for start_col, end_col, label, color in [
        (1, 6, "Detail", NAVY), (7, 21, "Banner", BLUE),
        (22, 29, "Outlet Visit", TEAL), (30, 30, "Product Display", GREEN),
    ]:
        if start_col != end_col:
            ws.merge_cells(start_row=5, start_column=start_col, end_row=5, end_column=end_col)
        ws.cell(5, start_col, label).font = Font(color=WHITE, bold=True)
        ws.cell(5, start_col).alignment = Alignment(horizontal="center", vertical="center")
        for column in range(start_col, end_col + 1):
            ws.cell(5, column).fill = PatternFill("solid", fgColor=color)
    for column, header in enumerate(headers, 1):
        cell = ws.cell(6, column, header)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[5].height = 24
    ws.row_dimensions[6].height = 36

    ordered = sorted(records.values(), key=lambda item: (
        item["date"], item["region"], item["dealer"], normalize_telegram_name(item["name"])
    ))
    grouped: dict[tuple[date, str], list[dict]] = defaultdict(list)
    for record in ordered:
        grouped[(record["date"], record["region"])].append(record)

    metric_count = last_col - 6
    grand_values = [0] * metric_count
    detail_rows: list[int] = []
    daily_region_total_rows: list[int] = []
    for (report_date, region), group_records in grouped.items():
        subtotal_values = [0] * metric_count
        for record in group_records:
            metrics = [
                *[record["banners"].get(name, 0) for name in BANNERS],
                *[record["outlet"].get(column, 0) for _, column in OUTLET_FIELDS],
                record["product_display"],
            ]
            ws.append([
                record["date"], record["region"], record["dealer"], record["name"],
                ("@" + record["username"].lstrip("@")) if record["username"] else "",
                str(record["telegram_id"]), *metrics
            ])
            detail_rows.append(ws.max_row)
            subtotal_values = [
                left + right for left, right in zip(subtotal_values, metrics)
            ]

        ws.append([None, None, None, None, None, None, *subtotal_values])
        subtotal_row = ws.max_row
        ws.merge_cells(
            start_row=subtotal_row, start_column=1,
            end_row=subtotal_row, end_column=6,
        )
        ws.cell(subtotal_row, 1, f"TOTAL {region}")
        daily_region_total_rows.append(subtotal_row)
        grand_values = [
            left + right for left, right in zip(grand_values, subtotal_values)
        ]

    ws.append([None, None, None, None, None, None, *grand_values])
    grand_row = ws.max_row
    ws.merge_cells(start_row=grand_row, start_column=1, end_row=grand_row, end_column=6)
    ws.cell(grand_row, 1, "GRAND TOTAL")

    thin = Side(style="thin", color=GRID)
    for row_number in detail_rows:
        for cell in ws[row_number]:
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.cell(row_number, 4).alignment = Alignment(horizontal="left", vertical="center")
        ws.cell(row_number, last_col).fill = PatternFill("solid", fgColor=LIGHT_BLUE)
    for row_number in daily_region_total_rows:
        for cell in ws[row_number]:
            cell.fill = PatternFill("solid", fgColor=REGION_TOTAL)
            cell.font = Font(color=WHITE, bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(top=Side(style="thin", color=WHITE))
    for row_number in [grand_row]:
        for cell in ws[row_number]:
            cell.fill = PatternFill("solid", fgColor=GRAND_TOTAL)
            cell.font = Font(color=WHITE, bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(top=Side(style="thin", color=WHITE))
    for row_number in detail_rows:
        ws.cell(row_number, 1).number_format = "dd/mm/yyyy"
    for column in range(7, last_col + 1):
        for row_number in range(7, grand_row + 1):
            ws.cell(row_number, column).number_format = "#,##0"

    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 13
    ws.column_dimensions["C"].width = 11
    ws.column_dimensions["D"].width = 30
    ws.column_dimensions["E"].width = 20
    ws.column_dimensions["F"].width = 18
    for column in range(7, last_col + 1):
        ws.column_dimensions[get_column_letter(column)].width = 13
    ws.freeze_panes = "G7"
    ws.auto_filter.ref = f"A6:{get_column_letter(last_col)}{grand_row}"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.print_title_rows = "1:6"

    try:
        wb.save(output)
    except PermissionError as exc:
        raise PermissionError(
            f"Cannot write {output}. Close this workbook in Microsoft Excel and rerun export."
        ) from exc
    check = load_workbook(output, read_only=True, data_only=False)
    if check.sheetnames != ["Combined Summary"] or check["Combined Summary"].cell(grand_row, 1).value != "GRAND TOTAL":
        check.close()
        raise RuntimeError("Combined summary workbook or totals are incomplete.")
    check.close()
    print(
        f"Combined Summary source: {source_mode}; detail rows={len(ordered)}; "
        f"source counts={source_counts}"
    )
    return output
