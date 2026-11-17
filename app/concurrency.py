import asyncio


def chat_lock(application, chat_id):
    """Return one lock per Telegram chat so groups never block each other."""
    locks = application.bot_data.setdefault('chat_locks', {})
    key = int(chat_id or 0)
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


def scheduler_lock(application):
    """Prevent overlapping scheduler ticks without blocking Telegram commands."""
    return application.bot_data.setdefault('scheduler_lock', asyncio.Lock())
