import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import alerts, bot_control, db, service


@pytest.mark.parametrize("day", [21, 22])  # Saturday, Sunday
def test_weekend_mutes_alerts_but_keeps_report_forwarding_active(monkeypatch, day):
    db.connect("sqlite:///:memory:")
    try:
        with db.session() as session:
            session.add(db.Group(id=-100, title="R1-CA2", code="R1-CA2", active=1))
            db.put(session, "alert_interval_minutes", {
                "minutes": 15, "changed_at": datetime(2026, 11, day, 8, 10).isoformat(),
            })
        monkeypatch.setattr(service, "enabled", lambda group: True)
        monkeypatch.setattr(db, "now", lambda: datetime(2026, 11, day, 8, 39))
        monkeypatch.setattr(bot_control, "now", lambda: datetime(2026, 11, day, 8, 39))

        with db.session() as session:
            ctrl = bot_control.get_bot_control(session)
            assert ctrl["weekend_muted"] is True
            assert ctrl["configured_enabled"] is True
            assert ctrl["enabled"] is False
            assert bot_control.automation_enabled(session) is False

        check_window = AsyncMock()
        followups = AsyncMock()
        monkeypatch.setattr(service, "check_window", check_window)
        monkeypatch.setattr(service, "followups", followups)
        app = SimpleNamespace(running=True, updater=SimpleNamespace(running=True),
                              bot_data={"started": datetime(2026, 11, day, 8)})
        asyncio.run(service.tick(SimpleNamespace(application=app)))
        check_window.assert_not_awaited()
        followups.assert_not_awaited()

        # Report forwarding (Banner_Report / Parasol) must still work on Sat & Sun
        monkeypatch.setattr(alerts, "central_chat_id", lambda _s: -200)
        monkeypatch.setattr(alerts, "central_topic_id", lambda _s, _cat: 99)
        bot = SimpleNamespace(forward_messages=AsyncMock(return_value=[object()]))
        fwd_app = SimpleNamespace(bot=bot, bot_data={})
        delivered = asyncio.run(alerts.copy_central_messages(fwd_app, f"parasol:{day}", -100, [10], "parasol"))
        assert delivered is not None
        bot.forward_messages.assert_awaited_once()
    finally:
        db.engine.dispose()


def test_weekday_runs_alert_scheduler(monkeypatch):
    day = 23  # Monday
    db.connect("sqlite:///:memory:")
    try:
        with db.session() as session:
            session.add(db.Group(id=-100, title="R1-CA2", code="R1-CA2", active=1))
            db.put(session, "alert_interval_minutes", {
                "minutes": 15, "changed_at": datetime(2026, 11, day, 8, 10).isoformat(),
            })
        monkeypatch.setattr(service, "enabled", lambda group: True)
        monkeypatch.setattr(db, "now", lambda: datetime(2026, 11, day, 8, 39))
        monkeypatch.setattr(bot_control, "now", lambda: datetime(2026, 11, day, 8, 39))
        check_window = AsyncMock()
        monkeypatch.setattr(service, "check_window", check_window)
        monkeypatch.setattr(service, "followups", AsyncMock())
        app = SimpleNamespace(running=True, updater=SimpleNamespace(running=True),
                              bot_data={"started": datetime(2026, 11, day, 8)})
        asyncio.run(service.tick(SimpleNamespace(application=app)))
        check_window.assert_awaited_once_with(
            app, -100, datetime(2026, 11, day, 8, 15), datetime(2026, 11, day, 8, 30),
        )
    finally:
        db.engine.dispose()


def test_working_days_can_be_configured_for_saturday_and_sunday(monkeypatch):
    db.connect("sqlite:///:memory:")
    try:
        sat = datetime(2026, 11, 21, 9, 0)
        monkeypatch.setattr(bot_control, "now", lambda: sat)
        with db.session() as session:
            # Default: Mon-Fri active, Sat & Sun off
            defaults = bot_control.get_working_days(session)
            assert defaults == {
                "mon": True, "tue": True, "wed": True, "thu": True,
                "fri": True, "sat": False, "sun": False,
            }
            assert bot_control.automation_enabled(session) is False

            # Enable Saturday in Working Time settings
            bot_control.set_working_days(session, {"sat": True}, changed_by=1, changed_at=sat)
            assert bot_control.automation_enabled(session) is True
    finally:
        db.engine.dispose()


def test_khmer_calendar_holiday_pauses_alerts_while_keeping_forwarding_active(monkeypatch):
    db.connect("sqlite:///:memory:")
    try:
        # Khmer New Year Day 1 (Tuesday, April 14, 2026) — a weekday that is a Khmer Calendar public holiday
        kny = datetime(2026, 4, 14, 9, 0)
        monkeypatch.setattr(bot_control, "now", lambda: kny)
        with db.session() as session:
            ctrl = bot_control.get_bot_control(session)
            assert ctrl["working_day_active"] is True
            assert ctrl["holiday_muted"] is True
            assert "Khmer New Year" in ctrl["holiday_name"]
            assert ctrl["enabled"] is False
            assert bot_control.automation_enabled(session) is False

            # Admin can optionally toggle off holiday pause if needed
            bot_control.set_khmer_holidays_pause(session, False, changed_by=1, changed_at=kny)
            assert bot_control.automation_enabled(session) is True
    finally:
        db.engine.dispose()
