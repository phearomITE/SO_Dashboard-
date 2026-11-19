import importlib.util
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from app import db
from app.db import Controller, Event, Group, Leave, Missing, Roster, SilverVisit, State
from app.miss_report import delete_miss_reason, miss_report_data, save_miss_reason

ROOT = Path(__file__).resolve().parents[1]


def _setup_sqlite_miss_db():
    db.connect("sqlite:///:memory:")
    day = date(2026, 11, 19)
    with db.session() as session:
        session.add(Group(id=-1001, title="R1-SO-CA1", code="R1-CA1", active=1, test_uids=[]))
        session.add(Group(id=-1004303041907, title="test_so", code="R9-TEST", active=1, test_uids=[]))
        session.add(Controller(uid=9001, code="R1-CA1", name="Controller Sok", username="ctrlsok"))

        session.add(Roster(employee="E001", uid=101, name="Dara SO", region="R1", dealer="CA1", position="SO", active=1))
        session.add(Roster(employee="E002", uid=102, name="Bopha SO", region="R1", dealer="CA1", position="SO", active=1))
        session.add(Roster(employee="E003", uid=103, name="Vanna SS", region="R1", dealer="CA1", position="SS", active=1))
        # Inactive user and non-SO manager should be excluded
        session.add(Roster(employee="E004", uid=104, name="Inactive SO", region="R1", dealer="CA1", position="SO", active=0))
        session.add(Roster(employee="E005", uid=105, name="ASM User", region="R1", dealer="CA1", position="ASM", active=1))

        # Visits for E001: 2 silver visits + 3 live pins on same day -> reconciled max = 3
        session.add(SilverVisit(
            source_event_id=1, chat_id=-1001, group_name="R1-SO-CA1", message_id=10,
            telegram_id=101, employee_id="E001", real_name="Dara SO", region="R1",
            dealer="CA1", position="SO", submitted_at=datetime(2026, 11, 19, 9, 0),
            report_date=day,
        ))
        session.add(SilverVisit(
            source_event_id=2, chat_id=-1001, group_name="R1-SO-CA1", message_id=11,
            telegram_id=101, employee_id="E001", real_name="Dara SO", region="R1",
            dealer="CA1", position="SO", submitted_at=datetime(2026, 11, 19, 10, 0),
            report_date=day,
        ))
        for mid, hour in ((10, 9), (11, 10), (12, 11)):
            session.add(Event(
                chat=-1001, mid=mid, uid=101, at=datetime(2026, 11, 19, hour, 0),
                kind="pin", data={"lat": 11.55, "lon": 104.92, "person": {"employee": "E001", "position": "SO"}},
            ))
        # Test group pin must be excluded
        session.add(Event(
            chat=-1004303041907, mid=99, uid=102, at=datetime(2026, 11, 19, 12, 0),
            kind="pin", data={"lat": 11.55, "lon": 104.92, "person": {"employee": "E002", "position": "SO"}},
        ))

        # Missing alerts: E001 has 2 sent alerts, E002 has 3 sent alerts, E003 has 1 alert suppressed by Leave
        session.add(Missing(chat=-1001, uid=101, start=datetime(2026, 11, 19, 13, 0), end=datetime(2026, 11, 19, 14, 0), alert_mid=501, person={"employee": "E001"}))
        session.add(Missing(chat=-1001, uid=101, start=datetime(2026, 11, 19, 15, 0), end=datetime(2026, 11, 19, 16, 0), alert_mid=502, person={"employee": "E001"}))
        session.add(Missing(chat=-1001, uid=102, start=datetime(2026, 11, 19, 10, 0), end=datetime(2026, 11, 19, 11, 0), alert_mid=601, person={"employee": "E002"}))
        session.add(Missing(chat=-1001, uid=102, start=datetime(2026, 11, 19, 13, 0), end=datetime(2026, 11, 19, 14, 0), alert_mid=602, person={"employee": "E002"}))
        session.add(Missing(chat=-1001, uid=102, start=datetime(2026, 11, 19, 15, 0), end=datetime(2026, 11, 19, 16, 0), alert_mid=603, person={"employee": "E002"}))
        # Suppressed by leave
        session.add(Missing(chat=-1001, uid=103, start=datetime(2026, 11, 19, 10, 0), end=datetime(2026, 11, 19, 11, 0), alert_mid=701, person={"employee": "E003"}))
        session.add(Leave(chat=-1001, uid=103, employee="E003", name="Vanna SS", region="R1", dealer="CA1", position="SS",
                          start=datetime(2026, 11, 19, 0, 0), end=datetime(2026, 11, 20, 0, 0), source="Morning Talk"))

        # Written reason review for E001
        session.add(State(key="reason_review:-1001:801", data={
            "date": "2026-11-19", "recorded_at": "2026-11-19T14:05:00",
            "employee_id": "E001", "telegram_id": 101, "name": "Dara SO",
            "region": "R1", "dealer": "CA1", "message": "ចរចាជាមួយអតិថិជន",
            "is_reason": True, "summary_km": "ចរចាជាមួយអតិថិជន។", "summary_en": "Negotiating with customer.",
        }))
        # Non-reason noise should be ignored in written reasons
        session.add(State(key="reason_review:-1001:802", data={
            "date": "2026-11-19", "recorded_at": "2026-11-19T14:10:00",
            "employee_id": "E002", "telegram_id": 102, "name": "Bopha SO",
            "region": "R1", "dealer": "CA1", "message": "Outlet Name: Shop A",
            "is_reason": False,
        }))
    return day


def test_miss_report_aggregation_and_sorting():
    day = _setup_sqlite_miss_db()
    try:
        res = miss_report_data(db.engine, day, day, language="km")
        s = res["summary"]
        assert s["total_so"] == 3
        assert s["so_miss_report"] == 2
        assert s["total_visit"] == 3
        assert s["total_miss"] == 5
        assert s["written_reasons"] == 1
        assert s["not_written_reasons"] == 4
        assert s["written_so_count"] == 1
        assert s["not_written_so_count"] == 2

        items = res["items"]
        # Sorted by total_miss DESC: E002 (3), E001 (2), E003 (0)
        assert [x["employee_id"] for x in items] == ["E002", "E001", "E003"]
        assert items[0]["miss_report_status"] == "Awaiting reason"
        assert items[0]["not_written_count"] == 3
        assert items[1]["has_written_reason"] is True
        assert items[1]["summary_reason"] == "ចរចាជាមួយអតិថិជន។"
        assert items[1]["not_written_count"] == 1
        assert items[1]["controller"] == "Controller Sok"

        # Language toggle to bilingual
        res_both = miss_report_data(db.engine, day, day, language="both")
        e001 = next(x for x in res_both["items"] if x["employee_id"] == "E001")
        assert "Negotiating with customer." in e001["summary_reason"]
        assert "ចរចាជាមួយអតិថិជន។" in e001["summary_reason"]
    finally:
        db.engine.dispose()


def test_miss_report_edit_and_delete_reason():
    day = _setup_sqlite_miss_db()
    try:
        with db.Session.begin() as session:
            save_miss_reason(session, "E002", day, day, "បែកកង់ឡាន\nដាច់ភ្លើង", "admin")

        res_after_edit = miss_report_data(db.engine, day, day, q="Bopha")
        assert len(res_after_edit["items"]) == 1
        bopha = res_after_edit["items"][0]
        assert bopha["has_written_reason"] is True
        assert bopha["miss_report_status"] == "Written"
        assert bopha["written_reasons"] == ["បែកកង់ឡាន", "ដាច់ភ្លើង"]
        assert bopha["written_count"] == 3
        assert bopha["not_written_count"] == 0

        with db.Session.begin() as session:
            delete_miss_reason(session, "E001", day, day, "admin")

        res_after_del = miss_report_data(db.engine, day, day)
        dara = next(x for x in res_after_del["items"] if x["employee_id"] == "E001")
        assert dara["has_written_reason"] is False
        assert dara["miss_report_status"] == "Awaiting reason"
        assert dara["written_count"] == 0
        assert dara["not_written_count"] == 2
        with db.session() as session:
            st = session.get(State, "reason_review:-1001:801")
            assert st.data.get("cleared_by_admin") is True
    finally:
        db.engine.dispose()


def test_sa_ux_contracts_across_frontend_and_map():
    dashboard_html = (ROOT / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")
    assert "tile.openstreetmap.org" in dashboard_html
    assert "street.addTo(map)" in dashboard_html
    assert "dealerLayer=L.layerGroup().addTo(map)" in dashboard_html
    assert "dealerLayer.clearLayers()" in dashboard_html
    assert "sa-num-pin" in dashboard_html
    assert "#167554" in dashboard_html
    assert "bindTooltip(tipTxt" in dashboard_html
    assert "check in detail" in dashboard_html
    assert "saMapBar" in dashboard_html

    map_css = (ROOT / "app" / "static" / "dashboard-sa-theme.css").read_text(encoding="utf-8")
    assert "border-radius: 50% !important" in map_css
    assert "2.5px solid #0b7e48" in map_css
    assert ".sa-map-toolbar" in map_css

    app_jsx = (ROOT / "admin_frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "On annual leave now" in app_jsx
    assert "Scheduled leave" in app_jsx
    assert "SO in roster" in app_jsx
    assert "Set annual leave" in app_jsx
    assert "AL Date Range" in app_jsx
    assert "Clear annual leave" in app_jsx

    reason_jsx = (ROOT / "admin_frontend" / "src" / "ReasonSummaryPage.jsx").read_text(encoding="utf-8")
    assert "Written reasons detail" in reason_jsx
    assert "Not Written reasons detail" in reason_jsx
    assert "Edit reason" in reason_jsx
    assert "Click to view detail" in reason_jsx
    assert "Reply details" in reason_jsx

    if importlib.util.find_spec("python_multipart") is not None:
        from app.admin_api import AttendanceRangeOverride
        model = AttendanceRangeOverride(
            start=date(2026, 11, 19),
            end=date(2026, 11, 20),
            employee_ids=["E001"],
            attendance="Clear dashboard AL",
        )
        assert model.attendance == "Clear dashboard AL"
