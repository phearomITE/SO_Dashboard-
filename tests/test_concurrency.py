import asyncio

from app.concurrency import chat_lock, scheduler_lock


class Application:
    def __init__(self):
        self.bot_data = {}


def test_chat_locks_are_stable_and_isolated():
    app = Application()
    assert chat_lock(app, -1001) is chat_lock(app, -1001)
    assert chat_lock(app, -1001) is not chat_lock(app, -1002)
    assert isinstance(chat_lock(app, -1001), asyncio.Lock)


def test_scheduler_lock_is_not_a_chat_lock():
    app = Application()
    assert scheduler_lock(app) is scheduler_lock(app)
    assert scheduler_lock(app) is not chat_lock(app, -1001)
