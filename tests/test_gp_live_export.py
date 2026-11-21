from datetime import date, datetime
from pathlib import Path
from openpyxl import load_workbook
import pytest

from app import db
from app.db import Base, Leave, Roster, SilverVisit
from app.gp_live_export import export_live_gp_reports
from app.gp_reports import _validate


def test_export_live_gp_reports_populates_checkins_without_scraping(tmp_path):
    db.connect("sqlite:///:memory:")
    try:
        day = date(2026, 11, 21)
        with db.session() as session:
            session.add(Roster(id=1, employee="9001", uid=1001, name="SO One", region="R1", dealer="CA1", position="SO", active=1, source="excel"))
            session.add(Roster(id=2, employee="9002", uid=1002, name="SS One", region="R1", dealer="CA1", position="SS", active=1, source="excel"))
            session.add(SilverVisit(
                visit_id=1, source_event_id=101, chat_id=-1001,
                employee_id="9001", telegram_id=1001, report_date=day,
                submitted_at=datetime(2026, 11, 21, 9, 15),
                latitude=11.55, longitude=104.91,
                region="R1", dealer="CA1", position="SO", group_name="R1-SO-CA1",
                actual_province="Phnom Penh", actual_district="Daun Penh", actual_commune="Phsar Thmei 1",
                outlet_type="WS", water="6", c_cola="1", emergency_outlet="Yes",
            ))
            session.add(SilverVisit(
                visit_id=2, source_event_id=102, chat_id=-1001,
                employee_id="9001", telegram_id=1001, report_date=day,
                submitted_at=datetime(2026, 11, 21, 9, 45),
                latitude=11.56, longitude=104.92,
                region="R1", dealer="CA1", position="SO", group_name="R1-SO-CA1",
                actual_province="Phnom Penh", actual_district="Chamkar Mon", actual_commune="Tonle Bassac",
                outlet_type="RT", water="4", c_cola="2", attacking_stock="1",
            ))
            session.commit()

        template = Path("data/gp_reporting/templates/Simple-Report GP.xlsx")
        paths = export_live_gp_reports(day.isoformat(), day.isoformat(), template, tmp_path)
        assert len(paths) == 1
        out = paths[0]
        assert out.exists()

        meta = _validate(out)
        assert meta["dealers"] == 65
        assert meta["districts"] == 2
        assert meta["communes"] == 2

        wb = load_workbook(out, data_only=False)
        try:
            gp = wb["Report GP"]
            assert gp["B10"].value == "R1"
            assert gp["D10"].value == 1  # SO
            assert gp["E10"].value == 1  # SS
            assert gp["H10"].value == 2  # Districts
            assert gp["I10"].value == 2  # Communes
            assert gp["J10"].value == 2  # Check-ins (never blank!)
            assert gp["L10"].value == 1  # Emergency Outlet WS
            assert gp["Q10"].value == 1  # Attacking Stock Retail
            assert gp["AB10"].value == 1 # Water WS (1 purchasing outlet)
            assert gp["AC10"].value == 1 # Water Retail (1 purchasing outlet)

            dlr = wb["Report with Dealer"]
            ca1_row = next(r for r in range(10, 75) if dlr.cell(r, 3).value == "CA1")
            assert dlr.cell(ca1_row, 10).value == 2
        finally:
            wb.close()
    finally:
        db.engine.dispose()


def test_export_live_gp_reports_works_without_template_file(tmp_path):
    db.connect("sqlite:///:memory:")
    try:
        day = date(2026, 11, 21)
        missing_template = tmp_path / "does_not_exist.xlsx"
        paths = export_live_gp_reports(day.isoformat(), day.isoformat(), missing_template, tmp_path)
        assert len(paths) == 1
        meta = _validate(paths[0])
        assert meta["sheets"] == 2
        assert meta["dealers"] == 65
    finally:
        db.engine.dispose()


def test_gp_ignores_leaked_labels_and_preserves_merged_template_headers(tmp_path):
    from app.gp_live_export import _metric_qty
    assert _metric_qty("Attacking Stock Outlet:") == 0
    assert _metric_qty("Tour Program Outlet:") == 0
    assert _metric_qty("0") == 0
    assert _metric_qty("No") == 0
    assert _metric_qty("6") == 1
    assert _metric_qty("Yes") == 1

    db.connect("sqlite:///:memory:")
    try:
        day = date(2026, 11, 21)
        paths = export_live_gp_reports(day.isoformat(), day.isoformat(), tmp_path / "missing.xlsx", tmp_path)
        wb = load_workbook(paths[0], data_only=False)
        try:
            gp = wb["Report GP"]
            merges = {str(r) for r in gp.merged_cells.ranges}
            for expected in ("B5:AQ5", "AR5:AR9", "B6:C9", "D6:G7", "H6:K6", "J7:J9", "K7:K9", "L6:AQ6", "L7:AQ7", "L8:O8"):
                assert expected in merges, f"Missing merge {expected}"
            assert gp["K10"].number_format == "0.00"
        finally:
            wb.close()
    finally:
        db.engine.dispose()
