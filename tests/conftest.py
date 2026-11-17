import pytest

@pytest.fixture(autouse=True)
def disable_external_central_alerts(monkeypatch):
    """Unit tests opt in explicitly when validating the central mirror."""
    monkeypatch.setattr('app.alerts.CENTRAL_ALERT_CHAT_ID',0)
