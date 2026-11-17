import asyncio
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, Mock
from app import db
from app.bot import banner_type_value,queue_banner_forward


def test_banner_type_accepts_named_banners():
    assert banner_type_value('Banner Type : CB Sport 1,Water 1') == 'CB Sport 1,Water 1'
    assert banner_type_value('Banner Type : C.ED 1') == 'C.ED 1'
    assert banner_type_value('Banner Type :ED1, C-sport 1') == 'ED1, C-sport 1'


def test_banner_type_rejects_blank_or_negative_values():
    for text in ('Banner Type:', 'Banner Type : 0', 'Banner Type: No',
                 'Banner Type: N/A', 'Banner Type: None', 'Banner Type: Nil',
                 'Outlet Type: RT'):
        assert banner_type_value(text) == ''


def test_banner_type_is_line_scoped_and_case_insensitive():
    text='Dealer: CHK6\nBANNER TYPE： Dazz 1\nPROGRAM:'
    assert banner_type_value(text) == 'Dazz 1'


def test_photo_without_banner_value_is_not_queued():
    application=N(bot_data={},create_task=Mock())
    message=N(media_group_id=None,chat_id=-100,message_id=20,photo=[object()],document=None)
    queue_banner_forward(application,message,'Dealer: CHK6\nBanner Type :\nPROGRAM:')
    assert not application.create_task.called
    queue_banner_forward(application,message,'Dealer: CHK6\nBanner Type : Water 1\nPROGRAM:')
    application.create_task.assert_called_once()
    application.create_task.call_args.args[0].close()


def test_album_forward_is_native_complete_ordered_and_deduplicated(monkeypatch):
    from app import alerts
    db.connect('sqlite:///:memory:')
    monkeypatch.setattr(alerts,'automation_enabled',lambda _session: True)
    monkeypatch.setattr(alerts,'central_chat_id',lambda _session: -200)
    monkeypatch.setattr(alerts,'central_topic_id',lambda _session,_category: 99)
    async def forward(**kwargs):
        await asyncio.sleep(.01)
        return [N(message_id=501),N(message_id=502),N(message_id=503)]
    application=N(bot_data={},bot=N(forward_messages=AsyncMock(side_effect=forward)))
    async def run():
        first,duplicate=await asyncio.gather(
            alerts.copy_central_messages(application,'banner_report:-100:album-1',-100,[22,20,21,21],'banner_report'),
            alerts.copy_central_messages(application,'banner_report:-100:album-1',-100,[22,20,21],'banner_report'))
        return first,duplicate
    first,duplicate=asyncio.run(run())
    assert first is not None and duplicate is None
    application.bot.forward_messages.assert_awaited_once_with(
        chat_id=-200,from_chat_id=-100,message_ids=[20,21,22],message_thread_id=99)
    db.engine.dispose()
