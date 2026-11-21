import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import db, service


@pytest.mark.parametrize("day", [21, 22])
def test_alert_scheduler_runs_saturday_and_sunday(monkeypatch, day):
    db.connect("sqlite:///:memory:")
    try:
        with db.session() as session:
            session.add(db.Group(id=-100, title="R1-CA2", code="R1-CA2", active=1))
            db.put(session, "alert_interval_minutes", {
                "minutes": 15, "changed_at": datetime(2026, 11, day, 8, 10).isoformat(),
            })
        monkeypatch.setattr(service, "enabled", lambda group: True)
        monkeypatch.setattr(db, "now", lambda: datetime(2026, 11, day, 8, 39))
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
