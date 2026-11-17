import asyncio
from datetime import datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app import db
from app.db import Event,Group,Roster,put
from app.tracking import analyze_pin_event
from app.rules import normalize_position,is_so,is_ss,is_reporter

@pytest.fixture(autouse=True)
def database():
    db.connect('sqlite:///:memory:')
    with db.session() as s:
        s.add(Roster(employee='90024146',uid=7166364893,name='Ra Phearom',username='Phearom619',region='R1',dealer='CA2',position='SO'))
        s.add(Group(id=-1004303041907,title='Test_SO',code='R1-CA2',test_uids=[7166364893],active=1))
        put(s,'dealer_origin:R1-CA2',{'lat':11.55,'lon':104.90})
    yield
    db.engine.dispose()

def pin(mid,hour,minute,lat,lon,commune):
    return Event(id=mid,chat=-1004303041907,mid=mid,uid=7166364893,at=datetime(2026,9,11,hour,minute),kind='pin',data={
        'lat':lat,'lon':lon,'geo':{'province':'Phnom Penh','district':'District','commune':commune,'commune_code':'1','geo_issue':''},
        'person':{'employee':'90024146','uid':7166364893,'name':'Ra Phearom','username':'Phearom619','region':'R1','dealer':'CA2','position':'SO'}})

def app():
    send=AsyncMock()
    send.side_effect=[N(message_id=900+i) for i in range(20)]
    return N(bot=N(send_message=send),bot_data={})

def test_position_titles_normalize_to_so_ss():
    assert normalize_position('Sales Officer')=='SO'
    assert normalize_position('Sr. Sales Officer')=='SO'
    assert normalize_position('Sales Supervisor')=='SS'
    assert normalize_position('Sr. Sales Supervisor')=='SS'
    assert is_so('Sr. Sales Officer') and is_ss('Sr. Sales Supervisor')
    assert is_reporter('Sales Officer') and is_reporter('Sales Supervisor')

def test_wrong_plan_and_sequential_road_distance_alerts(monkeypatch):
    import app.tracking as tracking
    with db.session() as s:s.add_all([pin(101,8,10,11.56,104.91,'Wrong Commune'),pin(102,8,30,11.70,105.05,'Planned Commune')])
    monkeypatch.setattr(tracking,'plan_check',lambda s,employee,day,geo,name: ('Incorrect','Actual commune is outside planned commune') if geo['commune']=='Wrong Commune' else ('Correct',''))
    road=AsyncMock(side_effect=[(4.25,''),(12.60,'')]);monkeypatch.setattr(tracking,'road_distance_km',road)
    application=app()
    asyncio.run(analyze_pin_event(application,101))
    asyncio.run(analyze_pin_event(application,102))
    assert application.bot.send_message.await_count==5
    texts=[x.args[1] for x in application.bot.send_message.await_args_list]
    wrong=next(x for x in texts if 'Wrong Plan Check-In' in x)
    dealer_first=next(x for x in texts if 'Dealer to First Check-In Road Distance' in x)
    distance_alert=next(x for x in texts if 'Road Distance Over 10 km' in x)
    assert 'Ra Phearom' in wrong and 'Actual:' in wrong and 'Plan:' in wrong and 'Date Plan:' in wrong and 'Plan Map:' in wrong and 'Telegram:' in wrong
    assert 'Time: 08:10 AM' in dealer_first and '4.25 km' in dealer_first and 'Dealer → Check-in #1' in dealer_first
    assert 'From Dealer:' in dealer_first and 'To First Check In:' in dealer_first
    assert dealer_first.count('Link Map: https://maps.google.com/')==2 and 'To Telegram:' in dealer_first
    assert '12.60 km' in distance_alert and 'Check-in #1 → Check-in #2' in distance_alert
    assert 'From: 08:10 AM | Phnom Penh / District / Wrong Commune (1)' in distance_alert
    assert 'To: 08:30 AM | Phnom Penh / District / Planned Commune (1)' in distance_alert
    assert 'From Telegram:' in distance_alert and 'To Telegram:' in distance_alert
    with db.session() as s:
        first=s.scalar(select(Event).where(Event.mid==101));second=s.scalar(select(Event).where(Event.mid==102))
        assert first.data['route']['kind']=='Dealer to First Check-in' and first.data['route']['distance_km']==4.25
        assert second.data['route']['kind']=='Previous Check-in to Current Check-in' and second.data['route']['status']=='Over limit'
    with db.session() as s:
        first=s.scalar(select(Event).where(Event.mid==101));second=s.scalar(select(Event).where(Event.mid==102))
        assert first.data['wrong_plan_question_mid']
        assert second.data['over_10km_question_mid']
    # Retrying the same analysis does not duplicate delivered alerts or questions.
    asyncio.run(analyze_pin_event(application,102))
    assert application.bot.send_message.await_count==5

def test_disabled_exception_questions_keep_analysis_and_admin_alerts(monkeypatch):
    import app.tracking as tracking
    with db.session() as s:
        s.add_all([pin(301,8,10,11.56,104.91,'Wrong Commune'),
                   pin(302,8,30,11.70,105.05,'Other Commune')])
        put(s,'question_settings_v1',{
            'wrong_plan':False,'over_10km':False,
            'reason_acknowledgement':False,'missing_report_followup':True,
        })
    monkeypatch.setattr(tracking,'plan_check',lambda *args:('Incorrect','Outside plan'))
    monkeypatch.setattr(tracking,'road_distance_km',AsyncMock(side_effect=[(4.0,''),(12.0,'')]))
    application=app()
    asyncio.run(analyze_pin_event(application,301))
    asyncio.run(analyze_pin_event(application,302))
    texts=[x.args[1] for x in application.bot.send_message.await_args_list]
    assert any('Wrong Plan Check-In' in value for value in texts)
    assert any('Road Distance Over 10 km' in value for value in texts)
    assert not any(value.startswith('@Phearom619\n➡️') for value in texts)
    with db.session() as s:
        first=s.get(Event,301);second=s.get(Event,302)
        assert not first.data.get('wrong_plan_question_mid')
        assert not second.data.get('over_10km_question_mid')

def test_first_checkin_alert_is_not_silently_skipped_when_routing_fails(monkeypatch):
    import app.tracking as tracking
    with db.session() as s:s.add(pin(201,9,5,11.56,104.91,'Commune'))
    monkeypatch.setattr(tracking,'plan_check',lambda *args: ('Correct',''))
    monkeypatch.setattr(tracking,'road_distance_km',AsyncMock(return_value=(None,'Road routing failed: TimeoutException')))
    application=app()
    asyncio.run(analyze_pin_event(application,201))
    text=application.bot.send_message.await_args.args[1]
    assert 'Dealer to First Check-In Road Distance' in text
    assert 'Road Distance: Unavailable' in text
    assert 'Routing Status: Road routing failed: TimeoutException' in text
    assert application.bot.send_message.await_args.kwargs['parse_mode']=='HTML'
