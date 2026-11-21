"""Collect the original Telegram messages for a qualifying Parasol visit."""
import asyncio
import re
from datetime import timedelta
from types import SimpleNamespace

from app.alerts import copy_central_messages
from app.concurrency import chat_lock
from app.geo import coordinates_from_text, is_cambodia_coordinate
from app.rules import is_form, link_visits


def qualifies_for_parasol(text):
    text = str(text or '').replace('\u200b', '')
    if not is_form(text):
        return False
    fields = {}
    for line in text.splitlines():
        match = re.match(r'^\s*\*?\s*([^:៖：]+)[:៖：](.*)$', line)
        if match:
            name = ' '.join(match[1].casefold().split())
            fields.setdefault(name, match[2].strip().strip('*').strip())
    if fields.get('posm'):
        return True
    if 'posm' not in fields:
        return False
    return all(fields.get(key) for key in ('water', 'c-cola', 'cb ed', 'exprez'))


def collect_parasol_message(application, message, user_id, text, at):
    """Pair report, photos and pin using the existing outlet association rules."""
    location = message.location
    coordinates = (location.latitude, location.longitude) if location else coordinates_from_text(text)
    has_photo = bool(message.photo or (
        message.document and (message.document.mime_type or '').startswith('image/')
    ))
    if coordinates and is_cambodia_coordinate(*coordinates):
        kind = 'pin'
    elif has_photo:
        kind = 'photo'
    elif is_form(text):
        kind = 'form'
    else:
        return

    buckets = application.bot_data.setdefault('parasol_evidence', {})
    scope = (message.chat_id, message.message_thread_id, user_id)
    loop = asyncio.get_running_loop()
    now = loop.time()
    for key, old in list(buckets.items()):
        if now - old['updated'] > 8 * 3600 and old['task'] is None:
            del buckets[key]
    bucket = buckets.setdefault(scope, {'events': {}, 'used': set(), 'task': None, 'updated': now})
    cutoff = at - timedelta(hours=8)
    bucket['events'] = {mid: event for mid, event in bucket['events'].items() if event.at >= cutoff}
    bucket['used'].intersection_update(bucket['events'])
    mid = message.message_id
    if mid in bucket['used']:
        return
    bucket['events'][mid] = SimpleNamespace(
        id=mid, mid=mid, chat=message.chat_id, uid=user_id, at=at, kind=kind,
        data={'text': text, 'album': message.media_group_id,
              'reply': getattr(message.reply_to_message, 'message_id', None)},
    )
    bucket['updated'] = now
    events = bucket['events'].values()
    if bucket['task'] is None and any(e.kind == 'pin' for e in events) and any(
        e.kind == 'photo' for e in events
    ) and any(qualifies_for_parasol(e.data['text']) for e in events):
        bucket['task'] = application.create_task(forward_ready_parasol(application, scope, bucket))


async def forward_ready_parasol(application, scope, bucket):
    chat_id = scope[0]
    try:
        # Wait for the remaining Telegram album updates before forwarding.
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(max(0, 2 - (loop.time() - bucket['updated'])))
            async with chat_lock(application, chat_id):
                if loop.time() - bucket['updated'] < 2:
                    continue
                for report in link_visits(bucket['events'].values()):
                    pin = report['pin']
                    items = report['events']
                    if not pin or not any(e.kind == 'photo' for e in items):
                        continue
                    if not qualifies_for_parasol(report['text']):
                        continue
                    mids = {e.mid for e in items} | {pin.mid}
                    if mids & bucket['used']:
                        continue
                    anchor = report['event']
                    identity = anchor.data.get('album') or anchor.mid
                    delivered = await copy_central_messages(
                        application, f'parasol:{chat_id}:{identity}', chat_id, mids, 'parasol'
                    )
                    if delivered:
                        bucket['used'].update(mids)
                return
    finally:
        bucket['task'] = None
