from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .commune import commune_identity, default_commune_resolver
from .database import Database
from .excel_links import set_clickable_link
from .groups import EXPECTED_DEALERS
from .parser import explicit_product_display_yes, product_display_count, product_display_status
from .product_display_identity import unique_product_display_outlets
from .staff_directory import load_staff_directory, normalize_telegram_id
from .telegram_identity import is_excluded_telegram_name

NAVY = "23364A"
WHITE = "FFFFFF"
LIGHT_BLUE = "D9EAF7"
GRID = "D9E2F3"
GOLD = "F4B183"
DARK_GRAY = "6B7280"


def _as_date(value) -> date:
    if isinstance(value, datetime): return value.date()
    if isinstance(value, date): return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def _loose_field(text: str, label_pattern: str) -> str:
    """Read basic values from rejected-format reports for Product Display audit."""
    import re
    match = re.search(rf"(?im)^\s*(?:{label_pattern})\s*(?::|=|-)?[ \t]*(.*)$", text or "")
    return match.group(1).strip() if match else ""


def _header(ws, headers):
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A2"
    ws.append(headers)
    ws.row_dimensions[1].height = 28
    for cell in ws[1]:
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _finish(ws):
    thin = Side(style="thin", color=GRID)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.border = Border(bottom=thin)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{max(1,ws.max_row)}"


def _report_title(ws, last_col: int, title: str, start: date, end: date) -> None:
    ws.sheet_view.showGridLines = False
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=last_col)
    ws.cell(1, 1, "KHMER BEVERAGES CO.")
    ws.cell(1, 1).font = Font(size=18, bold=True, color=WHITE)
    ws.cell(1, 1).fill = PatternFill("solid", fgColor=NAVY)
    ws.cell(1, 1).alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 32
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=last_col)
    ws.cell(2, 1, title)
    ws.cell(2, 1).font = Font(size=13, bold=True, color=WHITE)
    ws.cell(2, 1).fill = PatternFill("solid", fgColor=NAVY)
    ws.cell(2, 1).alignment = Alignment(horizontal="center", vertical="center")
    ws.merge_cells(start_row=3, start_column=1, end_row=3, end_column=last_col)
    ws.cell(3, 1, f"DATE: From {start:%d/%m/%Y} ==> {end:%d/%m/%Y}")
    ws.cell(3, 1).font = Font(bold=True, color=NAVY)
    ws.cell(3, 1).alignment = Alignment(horizontal="left")


def _report_header(ws, row: int, headers: list[str]) -> None:
    for col, value in enumerate(headers, 1):
        cell = ws.cell(row, col, value)
        cell.fill = PatternFill("solid", fgColor=NAVY)
        cell.font = Font(color=WHITE, bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 30


def _style_total_row(ws, row: int, last_col: int, *, grand: bool = False) -> None:
    """Apply the shared regional/grand-total style used by all report sheets."""
    fill = NAVY if grand else DARK_GRAY
    thin = Side(style="thin", color=GRID)
    for cells in ws.iter_rows(min_row=row, max_row=row, min_col=1, max_col=last_col):
        for cell in cells:
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.font = Font(color=WHITE, bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = Border(bottom=thin)
    ws.row_dimensions[row].height = 22


def export_product_display(
    db: Database,
    template_path: Path,
    output: Path,
    start_date: str,
    end_date: str,
    staff_master_path: Path | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    id_recovery = db.ensure_telegram_ids()
    if id_recovery["total"]:
        print(f"Telegram IDs repaired before Product Display export: {id_recovery}")
    gps_recovery = db.rebuild_product_display_gps_links(start_date, end_date)
    print(f"Product Display GPS links rebuilt: {gps_recovery}")
    wb = Workbook()
    wb.active.title = "Detail"
    summary_ws = wb.create_sheet("Summary")
    performance_ws = wb.create_sheet("Performance")
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    dates = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    commune_resolver = default_commune_resolver(db.path.resolve().parent.parent)
    if not commune_resolver.available:
        commune_resolver = default_commune_resolver(Path(__file__).resolve().parent.parent)
    if staff_master_path is None:
        staff_master_path = (
            Path(__file__).resolve().parent.parent
            / "templates" / "R1-R8-Master List.xlsx"
        )
    staff_directory = load_staff_directory(staff_master_path)

    # Only active dealer Execution groups feed this export.
    merged: dict[tuple[int, int], dict] = {}
    with db.connect() as con:
        sources = [
            con.execute(
                """SELECT f.*, m.image_message_id FROM banner_forms f
                   LEFT JOIN product_display_media m ON m.group_id=f.group_id AND m.form_message_id=f.message_id
                   LEFT JOIN telegram_groups g ON g.group_id=f.group_id
                   WHERE g.source_kind='EXECUTION'
                     AND (g.group_id IS NULL OR g.is_active=1)
                     AND substr(f.submitted_at,1,10) BETWEEN ? AND ?""",
                (start_date, end_date),
            ).fetchall(),
        ]
    for source_rows in sources:
        for source_row in source_rows:
            row = dict(source_row)
            if is_excluded_telegram_name(row.get("sender_name") or ""):
                continue
            raw = row.get("raw_text") or ""
            # Old SQLite 'Yes' values mean field presence and may represent
            # raw No/blank captions. Only the actual caption can qualify.
            if not explicit_product_display_yes(raw):
                continue
            key = (int(row["group_id"]), int(row["message_id"]))
            current = merged.get(key, {})
            combined = {**current, **{k: v for k, v in row.items() if v not in (None, "")}}
            combined["product_display"] = "Yes"
            combined.setdefault("outlet_name", _loose_field(raw, r"outlet\s*name"))
            combined.setdefault("phone", _loose_field(raw, r"phone|telephone|tel"))
            combined.setdefault("remark_issues", _loose_field(
                raw, r"\*?(?:remarks?|remake)\s*(?:issues?|iusse|issuee|isue)"
            ))
            combined.setdefault("latitude", None)
            combined.setdefault("longitude", None)
            combined.setdefault("sender_id", None)
            combined.setdefault("sender_name", "")
            combined.setdefault("sender_username", "")
            combined.setdefault("outlet_name", "")
            combined.setdefault("phone", "")
            combined.setdefault("remark_issues", "")
            # A Product Display visit is exportable only after Telegram has
            # supplied both parts of the visit: the report and its matched map
            # pin.  Staff-master membership is deliberately NOT required; a
            # real SO/SS posting in the group must still be counted even when
            # their Telegram ID has not yet been added to the master list.
            if combined.get("latitude") in (None, "") or combined.get("longitude") in (None, ""):
                continue
            commune = commune_resolver.resolve(
                combined.get("latitude"), combined.get("longitude")
            )
            combined["commune_name"] = commune.name if commune else ""
            combined["commune_code"] = commune_identity(commune)
            merged[key] = combined
    rows = sorted(merged.values(), key=lambda r: (
        r["submitted_at"], r["region"], r["dealer"], int(r['group_id']), int(r['message_id'])
    ))
    candidate_count = len(rows)
    rows = unique_product_display_outlets(rows)
    print(f"Product Display Yes outlets: {len(rows)}; duplicate outlets excluded: {candidate_count-len(rows)}")

    detail = wb["Detail"]
    headers = ["Date","Region","Dealer","Outlet Name","Phone","Product Display","* Product Display","Image","Lat","Lon",
               "Commune","Commune Code","Remark Issues","Telegram Group ID","Message ID",
               "Telegram Name","Username","Telegram ID"]
    _header(detail,headers)
    for r in rows:
        d=_as_date(r["submitted_at"])
        detail.append([d,r["region"],r["dealer"],r["outlet_name"],r["phone"],
                       product_display_count(r["product_display"] or ""),product_display_status(r.get("raw_text") or ""),
                       "Open Image" if r.get("image_message_id") else "",r["latitude"],r["longitude"],
                       r.get("commune_name") or "",r.get("commune_code") or "",
                       r["remark_issues"] or "",str(r["group_id"]),r["message_id"],r["sender_name"],
                       ("@"+r["sender_username"].lstrip("@")) if r["sender_username"] else "",str(r["sender_id"] or "")])
        detail.cell(detail.max_row, 7).data_type = "s"
        if r.get("image_message_id"):
            chat_id = str(r["group_id"])
            internal_id = chat_id[4:] if chat_id.startswith("-100") else chat_id.lstrip("-")
            image_cell = detail.cell(detail.max_row, 8)
            set_clickable_link(image_cell,
                f"https://t.me/c/{internal_id}/{int(r['image_message_id'])}", "Open Image")
    for row in range(2, detail.max_row + 1):
        detail.row_dimensions[row].height = 30
    for c in detail["A"][1:]: c.number_format="dd/mm/yyyy"
    widths=[12,10,11,32,15,16,20,18,14,14,24,16,30,19,12,28,20,14]
    for i,w in enumerate(widths,1): detail.column_dimensions[get_column_letter(i)].width=w
    _finish(detail)

    # Keep the filter on detail records only, then add the same region summary
    # block used by the tracking workbook shown to users.
    detail_data_end = detail.max_row
    detail.append([])
    grand_detail=detail.max_row+1
    detail.cell(grand_detail,1,"GRAND TOTAL")
    detail.cell(grand_detail,6,f"=SUM(F2:F{detail_data_end})" if detail_data_end >= 2 else 0)
    _style_total_row(detail,grand_detail,len(headers),grand=True)
    detail.merge_cells(start_row=grand_detail,start_column=1,end_row=grand_detail,end_column=5)
    detail.cell(grand_detail,6).number_format='#,##0'
    for cell in detail.iter_rows(min_row=2, max_row=max(2, detail_data_end), min_col=6, max_col=6):
        cell[0].number_format = '#,##0'
    # The dropdown must include records only.  Otherwise Excel offers region
    # totals (6, 15, 394, ...) as Product Display filter values.
    detail.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(1, detail_data_end)}"
    detail.sheet_properties.pageSetUpPr.fitToPage=True
    detail.page_setup.orientation="landscape"
    detail.page_setup.fitToWidth=1

    daily_dealer={}
    users={}
    for r in rows:
        count=product_display_count(r["product_display"] or "")
        if not count: continue
        d=_as_date(r["submitted_at"])
        daily_dealer[(r["region"],r["dealer"],d)]=daily_dealer.get((r["region"],r["dealer"],d),0)+1
        # Do not recreate this dictionary for every message: doing that erased
        # days 1-11 and retained only the user's latest day. Include the dealer
        # in the key so the same Telegram account working in two dealer groups
        # is reported on two correct performance rows.
        telegram_id=normalize_telegram_id(r["sender_id"])
        identity=telegram_id or f"unknown:{r['sender_name']}:{r['sender_username']}"
        user_key=(identity,r["region"],r["dealer"])
        member=staff_directory.get(telegram_id)
        mapped_name=(member.name if member and member.name else (r["sender_name"] or "Unknown"))
        user=users.setdefault(user_key,{"region":r["region"],"dealer":r["dealer"],
                   "telegram_name":r["sender_name"] or "Unknown",
                   "mapped_name":mapped_name,
                   "id":telegram_id,"communes":set()})
        # Keep the newest nonblank Telegram profile while retaining all counts.
        if r["sender_name"]: user["telegram_name"]=r["sender_name"]
        # Refresh the master name if the ID has a mapping; never replace it
        # with a differently ordered Telegram profile name.
        if member and member.name:
            user["mapped_name"] = member.name
        elif r["sender_name"]:
            user["mapped_name"] = r["sender_name"]
        if r.get("commune_code"):
            user["communes"].add(r["commune_code"])
        user[d]=user.get(d,0)+1

    date_headers=[str(d.day) for d in dates]
    summary_headers=["Region","Dealer",*date_headers,"Total"]
    _report_title(summary_ws,len(summary_headers),f"Daily Product Display — {start:%b %Y}",start,end)
    _report_header(summary_ws,5,summary_headers)
    summary_first=6
    region_total_rows=[]
    for region,dealers in EXPECTED_DEALERS.items():
        for dealer in dealers:
            nums=[daily_dealer.get((region,dealer,d),0) for d in dates]
            summary_ws.append([region,dealer,*nums,sum(nums)])
    summary_data_end=summary_ws.max_row
    summary_ws.append(["GRAND TOTAL","",*[
        f"=SUM({get_column_letter(c)}6:{get_column_letter(c)}{summary_data_end})"
        for c in range(3,len(summary_headers)+1)
    ]])
    grand_summary=summary_ws.max_row
    summary_ws.auto_filter.ref=f"A5:{get_column_letter(len(summary_headers))}{summary_data_end}"

    perf_headers=["Region","Dealer","Telegram Name","Name","ID","Commune",*date_headers,"Total"]
    _report_title(performance_ws,len(perf_headers),f"Product Display Performance by Telegram Name — {start:%b %Y}",start,end)
    _report_header(performance_ws,5,perf_headers)
    for uid,u in sorted(users.items(),key=lambda x:(x[1]["region"],x[1]["dealer"],x[1]["telegram_name"],x[0])):
        nums=[u.get(d,0) for d in dates]
        performance_ws.append([
            u["region"],u["dealer"],u["telegram_name"],u["mapped_name"],u["id"],
            len(u["communes"]),*nums,sum(nums),
        ])
    performance_data_end=performance_ws.max_row
    performance_ws.auto_filter.ref=f"A5:{get_column_letter(len(perf_headers))}{max(5,performance_data_end)}"
    for ws in [summary_ws,performance_ws]:
        ws.freeze_panes="C6" if ws.title=="Summary" else "G6"
        for col in range(3 if ws.title=="Summary" else 7,ws.max_column+1): ws.column_dimensions[get_column_letter(col)].width=10
        format_end=grand_summary if ws.title=="Summary" else performance_data_end
        for row in ws.iter_rows(min_row=6,max_row=max(6,format_end)):
            for cell in row:
                cell.border=Border(bottom=Side(style="thin",color=GRID))
                cell.alignment=Alignment(horizontal="center",vertical="center")
            row[-1].fill=PatternFill("solid",fgColor=LIGHT_BLUE)
    summary_ws.column_dimensions["A"].width=13
    summary_ws.column_dimensions["B"].width=11
    for r in region_total_rows:
        _style_total_row(summary_ws,r,len(summary_headers))
    _style_total_row(summary_ws,grand_summary,len(summary_headers),grand=True)
    performance_ws.column_dimensions["A"].width=10
    performance_ws.column_dimensions["B"].width=11
    performance_ws.column_dimensions["C"].width=28
    performance_ws.column_dimensions["D"].width=24
    performance_ws.column_dimensions["E"].width=18
    performance_ws.column_dimensions["F"].width=12

    grand_performance=performance_ws.max_row+1
    performance_ws.cell(grand_performance,1,"GRAND TOTAL")
    for col in range(6,len(perf_headers)+1):
        letter=get_column_letter(col)
        performance_ws.cell(grand_performance,col,
            f"=SUM({letter}6:{letter}{performance_data_end})" if performance_data_end >= 6 else 0)
        performance_ws.cell(grand_performance,col).number_format='#,##0'
    _style_total_row(performance_ws,grand_performance,len(perf_headers),grand=True)
    performance_ws.merge_cells(start_row=grand_performance,start_column=1,end_row=grand_performance,end_column=5)

    summary_ws.sheet_properties.pageSetUpPr.fitToPage=True
    summary_ws.page_setup.orientation="landscape"
    summary_ws.page_setup.fitToWidth=1
    performance_ws.sheet_properties.pageSetUpPr.fitToPage=True
    performance_ws.page_setup.orientation="landscape"
    performance_ws.page_setup.fitToWidth=1
    wb.calculation.calcMode="auto"
    wb.calculation.fullCalcOnLoad=True
    wb.calculation.forceFullCalc=True
    wb.active=0
    wb.save(output)
    check=load_workbook(output,read_only=True); check.close()
    return output
