import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import parasol

FORM = "Dealer: KTR4\nOutlet Name: Example\n"


@pytest.mark.parametrize("value", ["1", "parasol", "1parasol", "1 parasol", "parasol 1", "parasol1"])
def test_nonempty_posm(value):
    assert parasol.qualifies_for_parasol(FORM + "POSM: " + value)


def test_blank_posm_requires_all_four_fields():
    fields = "POSM: \nWater: 6\nC-Cola: 1\nCB ED : 0.5\nExprez : 1"
    assert parasol.qualifies_for_parasol(FORM + fields)
    assert parasol.qualifies_for_parasol(FORM + "POSM: \nWater: 5\nC-Cola: 1\nED: 1\nExprez: 1")
    assert not parasol.qualifies_for_parasol(FORM + fields[:-1])
    assert not parasol.qualifies_for_parasol("POSM: 1 parasol")


def test_photo_album_waits_for_pin_then_forwards_once(monkeypatch):
    forward = AsyncMock(return_value=[object(), object(), object()])
    monkeypatch.setattr(parasol, "copy_central_messages", forward)

    async def run():
        app = SimpleNamespace(bot_data={}, create_task=asyncio.create_task)
        now = datetime(2026, 11, 21, 9)
        def add(mid, text="", *, photo=False, album=None, reply=None):
            msg = SimpleNamespace(chat_id=-100123, message_thread_id=7, message_id=mid,
                                  location=None, photo=photo, document=None, media_group_id=album,
                                  reply_to_message=SimpleNamespace(message_id=reply) if reply else None)
            parasol.collect_parasol_message(app, msg, 42, text, now)

        add(5, FORM + "POSM: Parasol: 1", photo=True, album="group")
        add(6, photo=True, album="group")
        bucket = next(iter(app.bot_data["parasol_evidence"].values()))
        assert bucket["task"] is None
        add(7, "https://maps.google.com/maps?q=10.479418,104.291173", reply=5)
        bucket["updated"] -= 3
        await bucket["task"]
        forward.assert_awaited_once_with(app, "parasol:-100123:group", -100123, {5, 6, 7}, "parasol")
        add(7, "10.479418,104.291173", reply=5)
        assert bucket["task"] is None
    asyncio.run(run())
