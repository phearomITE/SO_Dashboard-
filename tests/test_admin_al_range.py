from datetime import date, datetime
from types import SimpleNamespace as N

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app import db
from app.db import Event, Group, Leave, Missing, Roster
from app.admin_api import (AttendanceRangeOverride, _override_attendance_range_impl,
                           al_range_summary, router)


@pytest.fixture
def al_database(monkeypatch):
    db.connect('sqlite:///:memory:')
    monkeypatch.setattr('app.admin_api._audit', lambda *args, **kwargs: None)
    monkeypatch.setenv('DASHBOARD_ADMIN_ID', '999')
    with db.session() as session:
        session.add(Roster(employee='9001', uid=101, name='Test SO', region='R1',
                           dealer='CA1', position='SO', active=1))
        session.add(Group(id=-100, title='R1-CA1', code='R1-CA1', active=1, test_uids=[]))
        session.add(Event(chat=-100, mid=1, uid=101, at=datetime(2026, 11, 1, 8),
                          kind='form', data={}))
        session.add(Leave(chat=-100, uid=101, employee='9001', name='Test SO', region='R1',
                          dealer='CA1', position='SO', start=datetime(2026, 11, 2),
                          end=datetime(2026, 11, 3), source='Morning Talk', recorded_at=datetime(2026, 11, 1)))
        session.add(Missing(chat=-100, uid=101, start=datetime(2026, 11, 3, 8),
                            end=datetime(2026, 11, 3, 9), sent=datetime(2026, 11, 3, 9), person={}))
    request=N(app=N(state=N(redis=None)))
    yield request
    db.engine.dispose()


def test_admin_can_set_ten_day_al_without_removing_telegram_leave(al_database):
    body=AttendanceRangeOverride(start=date(2026, 11, 1), end=date(2026, 11, 10),
                                 employee_ids=['9001'], attendance='AL Full Day')
    result=_override_attendance_range_impl(body, al_database, 'admin')
    assert result['days']==10 and result['employee_ids']==['9001']
    with db.session() as session:
        dashboard=list(session.scalars(select(Leave).where(
            Leave.employee=='9001', Leave.source=='Dashboard admin correction')))
        original=session.scalar(select(Leave).where(Leave.source=='Morning Talk'))
        missing=session.scalar(select(Missing).where(Missing.uid==101))
    assert len(dashboard)==9
    assert all(row.start.hour==0 and row.end.hour==0 for row in dashboard)
    assert original is not None
    assert {row.start.date() for row in dashboard} | {original.start.date()} == {
        date(2026, 11, day) for day in range(1, 11)}
    assert missing is None
    report=al_range_summary(al_database,date(2026, 11, 1),date(2026, 11, 10))
    dashboard_periods=[row for row in report['items'] if row['source']=='Dashboard admin correction']
    assert any(row['employee_id']=='9001' and row['real_name']=='Test SO' and
               row['telegram_group']=='R1-CA1' and row['from_date']=='2026-11-03' and
               row['to_date']=='2026-11-10' and row['days']==8 for row in dashboard_periods)


def test_admin_al_range_rejects_more_than_ninety_days(al_database):
    body=AttendanceRangeOverride(start=date(2026, 1, 1), end=date(2026, 4, 1),
                                 employee_ids=['9001'], attendance='AL Morning')
    with pytest.raises(HTTPException) as error:
        _override_attendance_range_impl(body, al_database, 'admin')
    assert error.value.status_code==422


def test_al_range_report_includes_employee_name_and_telegram_group(al_database):
    result=al_range_summary(al_database,date(2026, 11, 2),date(2026, 11, 2))
    assert result['count']==1
    row=result['items'][0]
    assert row['employee_id']=='9001'
    assert row['real_name']=='Test SO'
    assert row['telegram_group']=='R1-CA1'
    assert row['leave_type']=='AL Full Day'
    assert row['from_date']==row['to_date']=='2026-11-02'


def test_static_range_route_precedes_employee_id_route():
    paths=[route.path for route in router.routes if 'PUT' in getattr(route,'methods',set())]
    assert paths.index('/api/v1/admin/al/range') < paths.index('/api/v1/admin/al/{employee_id}')
