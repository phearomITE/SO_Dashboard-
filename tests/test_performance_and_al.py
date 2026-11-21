import pytest
from datetime import date, datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base, Controller, Event, Group, Leave, Missing, Roster, SilverVisit, State
from app.performance_analytics import build_performance_report, build_al_report, cancel_employee_leave


@pytest.fixture
def test_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        # Create groups
        session.add(Group(id=-1001001, code="R1-SO-CA1", title="R1-SO-CA1", active=True))
        session.add(Group(id=-1001002, code="R2-SO-CA2", title="R2-SO-CA2", active=True))
        session.add(Group(id=-1004303041907, code="TEST", title="test_so", active=True))

        # Create controllers
        session.add(Controller(id=1, uid="111", username="ctrl1", name="Sovannry On CE (R1)", code="R1"))
        session.add(Controller(id=2, uid="222", username="ctrl2", name="Maneth Houth CE (R2)", code="R2"))

        # Create roster
        session.add(Roster(id=1, employee="9001", uid=1001, name="Veng Seang", region="R1", dealer="CA1", position="SO", active=1, source="excel"))
        session.add(Roster(id=2, employee="9002", uid=1002, name="Ang Sokay", region="R1", dealer="CA1", position="SO", active=1, source="excel"))
        session.add(Roster(id=3, employee="9003", uid=1003, name="Buoy Lichuoy", region="R2", dealer="CA2", position="SO", active=1, source="excel"))

        # Visits on 2026-11-19
        d19 = date(2026, 11, 19)
        dt19 = datetime(2026, 11, 19, 9, 30)
        session.add(SilverVisit(
            visit_id=1, source_event_id=101, chat_id=-1001001,
            employee_id="9001", telegram_id=1001, report_date=d19,
            submitted_at=dt19, latitude=11.55, longitude=104.91,
            region="R1", dealer="CA1", position="SO", group_name="R1-SO-CA1",
            actual_province="Phnom Penh", actual_district="Daun Penh", actual_commune="Phsar Thmei 1",
            plan_check="Correct", road_distance_km=4.5
        ))
        session.add(Event(
            id=1, kind="pin", chat=-1001001, uid=1001, at=dt19,
            data={"latitude": 11.55, "longitude": 104.91}
        ))

        # Missing report for 9002
        session.add(Missing(
            id=1, chat=-1001001, uid=1002, start=dt19, end=dt19 + timedelta(hours=2),
            alert_mid=888, person={"employee": "9002", "name": "Ang Sokay", "region": "R1", "dealer": "CA1", "position": "SO"},
            reason="Customer delayed meeting", reason_at=dt19 + timedelta(minutes=45)
        ))

        # Leave for 9003 on 2026-11-19
        session.add(Leave(
            id=1, employee="9003", uid=1003, start=datetime(2026, 11, 19, 0, 0),
            end=datetime(2026, 11, 20, 0, 0), source="Telegram Morning AL", recorded_at=dt19
        ))

        session.commit()
    return engine


def test_build_performance_report(test_engine):
    report = build_performance_report(test_engine, date(2026, 11, 19), date(2026, 11, 19))
    assert report["start"] == "2026-11-19"
    assert report["end"] == "2026-11-19"
    assert report["totals"]["checkins"] == 1
    assert report["totals"]["miss_reports"] == 1
    assert len(report["timeline"]) == 1
    assert report["timeline"][0]["checkins"] == 1
    assert report["timeline"][0]["miss_reports"] == 1
    assert len(report["by_region"]) >= 1
    assert len(report["individuals"]) == 3
    # 9001 is Present
    ind9001 = next(x for x in report["individuals"] if x["employee_id"] == "9001")
    assert ind9001["checkins"] == 1
    assert ind9001["attendance"] == "Present"
    # 9002 has miss report
    ind9002 = next(x for x in report["individuals"] if x["employee_id"] == "9002")
    assert ind9002["miss_count"] == 1
    # 9003 has AL
    ind9003 = next(x for x in report["individuals"] if x["employee_id"] == "9003")
    assert ind9003["al_days"] == 1


def test_build_al_report(test_engine):
    report = build_al_report(test_engine, date(2026, 11, 19))
    assert report["al_count"] == 1
    assert report["roster_total"] == 3
    # Check 9003 on leave
    lv_row = next(x for x in report["items"] if x["employee_id"] == "9003")
    assert lv_row["status_kind"] == "on_leave"
    assert lv_row["status_label"] == "On leave"
    assert lv_row["leave_type_label"] == "Full day"
    assert lv_row["controller"] == "Maneth Houth CE (R2)"
    # Check 9001 no leave
    nl_row = next(x for x in report["items"] if x["employee_id"] == "9001")
    assert nl_row["status_kind"] == "no_leave"
    assert nl_row["status_label"] == "No leave"
    assert nl_row["controller"] == "Sovannry On CE (R1)"


def test_cancel_employee_leave(test_engine):
    with Session(test_engine) as session:
        # Check leave exists
        res = cancel_employee_leave(session, "9003", start=date(2026, 11, 19), end=date(2026, 11, 19))
        assert res is not None
        assert res["cleared_rows"] == 1
        session.commit()

    # Re-check AL report: 9003 should no longer be on leave
    report = build_al_report(test_engine, date(2026, 11, 19))
    lv_row = next(x for x in report["items"] if x["employee_id"] == "9003")
    assert lv_row["status_kind"] == "no_leave"
    assert report["al_count"] == 0
