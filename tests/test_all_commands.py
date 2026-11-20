import asyncio
from io import BytesIO
from datetime import datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock
import pytest
from openpyxl import load_workbook
from sqlalchemy import select
from app import db
from app.db import Roster,Group,Missing,Controller,Event,Leave
from app.bot import command,parse,route,notify_global_admins_new_member,chat_member_update,is_self_full_day_leave,thank_for_reason
from app.bot_control import automation_enabled,get_bot_control
from app.question_settings import question_enabled
from app.rules import (windows,is_morning_talk,morning_talk_date,
                       morning_talk_full_day_leave_names,
                       morning_talk_full_day_leave_entries)
from app.service import check_window,members
from app.config import TZ
from app.alerts import send_central_alert

@pytest.fixture(autouse=True)
def database(monkeypatch):
    db.connect('sqlite:///:memory:')
    with db.session() as s:
        s.add(Roster(employee='1',uid=100,name='Test SO',username='tester',region='R1',dealer='CA2',position='Sales Officer'))
        s.add(Group(id=-100,title='Test_SO',code='R1-CA2',test_uids=[100],active=1))
    yield
    db.engine.dispose()

def upd(text='',uid=5400514256,chat_id=-100,title='Test_SO',kind='supergroup',thread=None,reply=None):
    msg=N(text=text,caption=None,chat_id=chat_id,message_id=5,message_thread_id=thread,date=datetime(2026,9,15,8,10,tzinfo=TZ),reply_to_message=reply,reply_text=AsyncMock(return_value=N(message_id=777)),reply_document=AsyncMock(),photo=[],document=None,location=None,media_group_id=None)
    return N(effective_message=msg,effective_user=N(id=uid,full_name='Admin',username='admin',is_bot=False),effective_chat=N(id=chat_id,title=title,type=kind))
def ctx():return N(application=N(bot=N(send_message=AsyncMock(return_value=N(message_id=900))),bot_data={'lock':asyncio.Lock()}))

def run(text,**kw):
    u=upd(text,**kw);asyncio.run(command(u,ctx()));return u

def test_public_commands_and_unknown():
    for cmd,expected in [('/start','SO Alert Bot'),('/myid','5400514256'),('/chatid','-100'),('/pin','GPS')]:
        u=run(cmd);assert expected in u.effective_message.reply_text.call_args.args[0]
    u=run('/does_not_exist');assert 'Unknown command' in u.effective_message.reply_text.call_args.args[0]

def test_dated_al_summary_command_parser():
    assert parse('/AL_Summary_2026-11-08')==('al_summary',['2026-11-08'])
    assert parse('/AL_Summary_2026-11-08@SO_Controller_Bot')==('al_summary',['2026-11-08'])

def test_al_summary_workbook_is_sent_to_user_al_topic(monkeypatch):
    monkeypatch.setattr('app.alerts.CENTRAL_ALERT_CHAT_ID',-5322681004)
    with db.session() as s:
        s.add(Roster(employee='2',uid=101,name='Not Leave User',username='active',
                     region='R2',dealer='PMR5',position='SA'))
        s.add(Leave(chat=-100,uid=100,employee='1',name='Test SO',region='R1',
                    dealer='CA2',position='SO',start=datetime(2026,11,8),
                    end=datetime(2026,11,9),source='Morning Talk',
                    source_message_id=88,recorded_by=5400514256,
                    recorded_at=datetime(2026,11,8,7,30)))
        db.put(s,'central_topic:user_al',{'topic_id':8})
    bot=N(send_document=AsyncMock(return_value=N(message_id=901)),
          send_message=AsyncMock(return_value=N(message_id=902)))
    application=N(bot=bot,bot_data={'lock':asyncio.Lock()})
    update=upd('/AL_Summary_2026-11-08',chat_id=5400514256,kind='private')
    asyncio.run(command(update,N(application=application)))
    kwargs=bot.send_document.call_args.kwargs
    assert kwargs['chat_id']==-5322681004
    assert kwargs['message_thread_id']==8
    assert kwargs['filename']=='AL_Summary_2026-11-08.xlsx'
    workbook_file=load_workbook(BytesIO(kwargs['document'].getvalue()))
    detail=workbook_file['User Detail']
    statuses={row[4].value:row[7] for row in detail.iter_rows(min_row=2)}
    assert statuses['Test SO'].value=='AL'
    assert statuses['Test SO'].fill.fgColor.rgb.endswith('F4CCCC')
    assert statuses['Not Leave User'].value=='NOT AL'

def test_runtime_alert_interval_commands():
    for command_text,expected in [('/change_ALERT_INTERVAL_MINUTES_15',15),('/change_ALERT_INTERVAL_MINUTES_20',20),('/change_ALERT_INTERVAL_MINUTES_30',30),('/change_alert_interval | 25',25)]:
        u=run(command_text)
        assert f'{expected} minutes' in u.effective_message.reply_text.call_args.args[0]
        with db.session() as s:assert db.state(s,'alert_interval_minutes')['minutes']==expected
    u=run('/alert_interval')
    assert '25 minutes' in u.effective_message.reply_text.call_args.args[0]

def test_global_question_controls_are_restored_and_persistent():
    for command_text,label in [
        ('/questions | off_Wrong Plan','Wrong Plan questions: OFF'),
        ('/questions | off_Over 10Km','Over-10 km questions: OFF'),
        ('/questions | off_Reason Acknowledgement','Reason acknowledgements: OFF'),
    ]:
        u=run(command_text,chat_id=5400514256,kind='private')
        assert label in u.effective_message.reply_text.call_args.args[0]
        assert 'Missing-report follow-up questions: ON' in u.effective_message.reply_text.call_args.args[0]
    u=run('/questions | status',chat_id=5400514256,kind='private')
    result=u.effective_message.reply_text.call_args.args[0]
    assert 'Scope: All 1 registered groups (global admin via bot chat)' in result
    assert 'Wrong Plan questions: OFF' in result
    assert 'Over-10 km questions: OFF' in result
    assert 'Reason acknowledgements: OFF' in result
    assert result.endswith('Missing-report follow-up questions: ON')
    u=run('/questions | on',chat_id=5400514256,kind='private')
    assert 'Wrong Plan questions: ON' in u.effective_message.reply_text.call_args.args[0]

def test_question_controls_require_global_admin():
    with pytest.raises(ValueError,match='global bot-management admin'):
        run('/questions | off',uid=100)

def test_stop_bot_is_persistent_and_commands_still_work():
    u=run('/stop_bot',chat_id=5400514256,kind='private')
    assert 'Bot automation: STOPPED' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:assert automation_enabled(s) is False
    response=run('/myid',chat_id=5400514256,kind='private')
    assert 'Telegram ID: 5400514256' in response.effective_message.reply_text.call_args.args[0]
    status=run('/bot_status',chat_id=5400514256,kind='private')
    assert 'Bot automation: STOPPED' in status.effective_message.reply_text.call_args.args[0]
    resumed=run('/start_bot',chat_id=5400514256,kind='private')
    assert 'Bot automation: RUNNING' in resumed.effective_message.reply_text.call_args.args[0]
    with db.session() as s:assert automation_enabled(s) is True

def test_stop_bot_requires_global_admin():
    with pytest.raises(ValueError,match='global bot-management admin'):
        run('/stop_bot',uid=100)

def test_temporary_bot_breaks_are_persistent_and_commands_keep_working():
    for command_text,hours in [('/break_bot_1h',1),('/break_bot_2h',2)]:
        u=run(command_text,chat_id=5400514256,kind='private')
        result=u.effective_message.reply_text.call_args.args[0]
        assert 'Bot automation: BREAK' in result
        with db.session() as s:
            value=get_bot_control(s)
            assert value['pause_active'] is True
            assert automation_enabled(s) is False
            assert db.state(s,'bot_automation_v1')['break_hours']==hours
        response=run('/myid',chat_id=5400514256,kind='private')
        assert 'Telegram ID: 5400514256' in response.effective_message.reply_text.call_args.args[0]
    resumed=run('/start_bot',chat_id=5400514256,kind='private')
    assert 'Bot automation: RUNNING' in resumed.effective_message.reply_text.call_args.args[0]

def test_break_bot_requires_global_admin():
    with pytest.raises(ValueError,match='global bot-management admin'):
        run('/break_bot_1h',uid=100)

def test_morning_talk_recognition_and_central_delivery(monkeypatch):
    assert is_morning_talk('Morning Talk\nDate:05.11.2026')
    assert is_morning_talk('**Morning   Talk**\nDate:05/11/2026')
    assert not is_morning_talk('Evening Talk\nDate:05.11.2026')
    monkeypatch.setattr('app.alerts.CENTRAL_ALERT_CHAT_ID',-5322681004)
    application=ctx().application
    update=upd('Morning Talk\nDate:05.11.2026\nBTI6 :(SS=1, SO=3)\n*Attain\n1.Lib Raseth: Yes',uid=100)
    asyncio.run(route(update,N(application=application)))
    assert application.bot.send_message.await_count==1
    destination,text=application.bot.send_message.call_args.args[:2]
    assert destination==-5322681004
    assert 'Morning Talk Report Received' in text
    assert 'Group Code: R1-CA2' in text
    assert 'Telegram ID: 100' in text
    assert 'Full-day AL detected: 0 — None' in text
    assert 'BTI6 :(SS=1, SO=3)' in text
    with db.session() as s:
        receipt=db.state(s,'morning_talk:-100:2026-09-15')
        assert receipt['received'] is True
        assert receipt['sender_id']==100

def test_morning_talk_al_suppresses_missing_and_cleans_existing_rows():
    sample='''Morning Talk
Date: 06.11.2026
CA2 Morning talk
*Attain
1. Test SO (SO): AL
2. Sales Phally (ASS): Yes
1. Compititor Activity'''
    assert morning_talk_date(sample)==datetime(2026,11,6).date()
    assert morning_talk_full_day_leave_names(sample)==['Test SO']
    with db.session() as s:
        s.add(Missing(chat=-100,uid=100,start=datetime(2026,11,6,8),
                      end=datetime(2026,11,6,8,30),person={'name':'Test SO'}))
    update=upd(sample,uid=100)
    update.effective_message.date=datetime(2026,11,6,7,45,tzinfo=TZ)
    asyncio.run(route(update,ctx()))
    with db.session() as s:
        leave=db.state(s,'leave:-100:100')
        assert leave['start']=='2026-11-06T00:00:00'
        assert leave['source']=='Morning Talk'
        assert s.scalar(select(Leave).where(Leave.chat==-100,Leave.uid==100)) is not None
        assert s.scalar(select(Missing).where(Missing.chat==-100,Missing.uid==100)) is None

def test_morning_talk_al_matches_employee_id_for_any_active_position():
    sample='''Morning Talk
Date: 06.11.2026
Dealer:PMR5 (SL=1,SO=2,Ass Sale:2)
* Attendent
1.ID:90015103-Mab Vayuk (SL): Yes
2.ID:90021882-Eol Phearun (SA) : AL
1. Competitor Activity'''
    assert morning_talk_full_day_leave_entries(sample)==[
        {'employee_id':'90021882','name':'Eol Phearun'}]
    with db.session() as s:
        r=s.scalar(select(Roster).where(Roster.uid==100))
        r.employee='90021882';r.name='Master Name Can Differ';r.position='SA'
    update=upd(sample,uid=100)
    update.effective_message.date=datetime(2026,11,6,7,45,tzinfo=TZ)
    asyncio.run(route(update,ctx()))
    with db.session() as s:
        leave=s.scalar(select(Leave).where(Leave.chat==-100,Leave.uid==100))
        receipt=db.state(s,'morning_talk:-100:2026-11-06')
        assert leave is not None and leave.employee=='90021882'
        assert receipt['al_matched'][0]['match_method']=='employee_id'
        assert receipt['al_matched'][0]['supplied_name']=='Eol Phearun'

def test_morning_talk_unknown_employee_id_never_falls_back_to_name():
    sample='''Morning Talk
Date: 06.11.2026
1.ID:99999999-Test SO (SO): AL
1. Competitor Activity'''
    update=upd(sample,uid=100)
    update.effective_message.date=datetime(2026,11,6,7,45,tzinfo=TZ)
    asyncio.run(route(update,ctx()))
    with db.session() as s:
        assert s.scalar(select(Leave).where(Leave.uid==100)) is None
        receipt=db.state(s,'morning_talk:-100:2026-11-06')
        assert receipt['al_matched']==[]
        assert receipt['al_unmatched']==['ID 99999999 — Test SO']

def test_valid_form_auto_reconciles_stale_master_dealer():
    with db.session() as s:
        s.get(Group,-100).test_uids=[]
        s.get(Group,-100).code='R1-CA2'
        s.add(Group(id=-101,title='R1-CA1 Execution Excellent',code='R1-CA1',test_uids=[],active=1))
        r=s.scalar(select(Roster).where(Roster.uid==100))
        r.region='R1';r.dealer='CA1'
        s.add(Missing(chat=-101,uid=100,start=datetime(2026,9,15,8),
                      end=datetime(2026,9,15,8,30),sent=datetime(2026,9,15,8,31),
                      alert_mid=55,person={'name':'Test SO'}))
    update=upd('Dealer: CA2\nOutlet Name: Correct Group Outlet',uid=100)
    asyncio.run(route(update,ctx()))
    with db.session() as s:
        r=s.scalar(select(Roster).where(Roster.uid==100))
        override=db.state(s,'roster_override:100')
        event=s.scalar(select(Event).where(Event.chat==-100,Event.uid==100))
        assert (r.region,r.dealer,r.source)==('R1','CA2','manual')
        assert override['from_dealer']=='CA1' and override['dealer']=='CA2'
        assert override['changed_by']=='automatic_valid_report'
        assert override['cleared_old_group_missing']==1
        assert s.scalar(select(Missing).where(Missing.chat==-101,Missing.uid==100)) is None
        assert [x.uid for x in members(s,s.get(Group,-101))]==[]
        assert [x.uid for x in members(s,s.get(Group,-100))]==[100]
        assert event is not None

def test_plain_message_does_not_auto_reconcile_dealer():
    with db.session() as s:
        s.get(Group,-100).test_uids=[]
        r=s.scalar(select(Roster).where(Roster.uid==100));r.dealer='CA1'
    asyncio.run(route(upd('hello CA2',uid=100),ctx()))
    with db.session() as s:
        r=s.scalar(select(Roster).where(Roster.uid==100))
        assert r.dealer=='CA1'
        assert db.state(s,'roster_override:100',{})=={}

def test_central_alert_routes_to_registered_category_topic(monkeypatch):
    monkeypatch.setattr('app.alerts.CENTRAL_ALERT_CHAT_ID',-5322681004)
    with db.session() as s:db.put(s,'central_topic:wrong_plan',{'topic_id':77})
    application=ctx().application
    asyncio.run(send_central_alert(application,'wrong-plan-topic-test','Wrong plan',category='wrong_plan'))
    assert application.bot.send_message.call_args.kwargs['message_thread_id']==77

def test_register_alert_topic_learns_migrated_central_forum_id(monkeypatch):
    monkeypatch.setattr('app.bot.CENTRAL_ALERT_CHAT_ID',-5322681004)
    migrated=-1009876543210
    u=run('/register_alert_topic | Morning Talk',chat_id=migrated,
          title='Group_SO_Controll',thread=123)
    result=u.effective_message.reply_text.call_args.args[0]
    assert f'Chat ID: {migrated}' in result
    assert 'Topic ID: 123' in result
    with db.session() as s:
        assert db.state(s,'central_alert_chat')['chat_id']==migrated
        assert db.state(s,'central_topic:morning_talk')['topic_id']==123

def test_central_alert_uses_learned_forum_id(monkeypatch):
    monkeypatch.setattr('app.alerts.CENTRAL_ALERT_CHAT_ID',-5322681004)
    migrated=-1009876543210
    with db.session() as s:
        db.put(s,'central_alert_chat',{'chat_id':migrated})
        db.put(s,'central_topic:morning_talk',{'topic_id':123})
    application=ctx().application
    asyncio.run(send_central_alert(application,'migrated-chat-test','Morning',category='morning_talk'))
    assert application.bot.send_message.call_args.args[0]==migrated
    assert application.bot.send_message.call_args.kwargs['message_thread_id']==123

def test_register_alert_topic_rejects_another_group(monkeypatch):
    monkeypatch.setattr('app.bot.CENTRAL_ALERT_CHAT_ID',-5322681004)
    with pytest.raises(ValueError,match='inside Group_SO_Controll'):
        run('/register_alert_topic | Morning Talk',chat_id=-1009876543210,
            title='Another Group',thread=123)

def test_group_not_morning_talk_reports_missing_registered_groups(monkeypatch):
    monkeypatch.setattr('app.bot.db.now',lambda:datetime(2026,11,5,9,0))
    with db.session() as s:
        s.add(Group(id=-200,title='R2-BTI6 Execution Excellent',code='R2-BTI6',test_uids=[],active=1))
        db.put(s,'morning_talk:-100:2026-11-05',{'received':True})
    # The command uses the Cambodia business date, not the Telegram message date.
    u=run('/group_not_Morning Talk',chat_id=5400514256,kind='private')
    result=u.effective_message.reply_text.call_args.args[0]
    assert 'Received: 1/2 groups' in result
    assert 'Not Submitted: 1 groups' in result
    assert 'R2-BTI6 — R2-BTI6 Execution Excellent' in result
    assert 'R1-CA2' not in result

def test_group_not_morning_talk_requires_global_admin():
    with pytest.raises(ValueError,match='global bot-management admin'):
        run('/group_not_morning_talk',uid=100)

def test_reason_acknowledgement_is_silent_when_bot_stopped():
    run('/stop_bot',chat_id=5400514256,kind='private')
    message=N(reply_text=AsyncMock(),chat_id=-100)
    asyncio.run(thank_for_reason(message))
    message.reply_text.assert_not_awaited()

def test_stop_ask_only_disables_missing_report_followup():
    u=run('/stop_ask',chat_id=5400514256,kind='private')
    assert 'Missing-report follow-up questions: OFF' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:
        assert question_enabled(s,'missing_report_followup') is False
        assert question_enabled(s,'wrong_plan') is True
        assert question_enabled(s,'over_10km') is True
    status=run('/ask_status',chat_id=5400514256,kind='private')
    assert 'Missing-report follow-up questions: OFF' in status.effective_message.reply_text.call_args.args[0]
    resumed=run('/start_ask',chat_id=5400514256,kind='private')
    assert 'Missing-report follow-up questions: ON' in resumed.effective_message.reply_text.call_args.args[0]

def test_stop_ask_requires_global_admin():
    with pytest.raises(ValueError,match='global bot-management admin'):
        run('/stop_ask',uid=100)

def test_central_alert_delivery_and_deduplication(monkeypatch):
    monkeypatch.setattr('app.alerts.CENTRAL_ALERT_CHAT_ID',-5322681004)
    application=ctx().application
    asyncio.run(send_central_alert(application,'test-key','Alert text'))
    asyncio.run(send_central_alert(application,'test-key','Alert text'))
    assert application.bot.send_message.await_count==1
    assert application.bot.send_message.call_args.args[0]==-5322681004

def test_setup_status_topic():
    with db.session() as s:s.delete(s.get(Group,-100))
    u=run('/setup_group | R1-CA2 | 100',thread=44)
    with db.session() as s:
        g=s.get(Group,-100);assert g.topic==44 and g.test_uids==[100]
    u=run('/status',thread=44);assert 'Monitoring SO/SS: 1' in u.effective_message.reply_text.call_args.args[0]
    run('/register_topic',thread=55)
    with db.session() as s:assert s.get(Group,-100).topic==55

def test_setup_rejects_private_and_bad_scope():
    with pytest.raises(ValueError):asyncio.run(command(upd('/setup_group | BAD | 100'),ctx()))
    with pytest.raises(ValueError):asyncio.run(command(upd('/setup_group | R1-CA2 | 100',kind='private'),ctx()))

def test_all_leave_variants():
    for cmd in ['/al | 100 | 2026-09-15','/al_morning | 100 | 2026-09-15','/al_afternoon | 100 | 2026-09-15']:
        run(cmd)
        with db.session() as s:assert db.state(s,'leave:-100:100')['start'].startswith('2026-09-15')

def test_leave_commands_are_case_insensitive_and_self_service():
    u=run('/AL_Morning',uid=100)
    assert 'morning alerts paused' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:
        leave=db.state(s,'leave:-100:100')
        assert leave['start'].endswith('T00:00:00') and leave['end'].endswith('T11:45:00')
    u=run('/AL_Afternoon',uid=100)
    assert 'afternoon alerts paused' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:
        leave=db.state(s,'leave:-100:100')
        assert leave['start'].endswith('T13:00:00')

def test_global_admin_can_set_multiday_leave_from_private_bot_chat():
    u=run('/AL | 100 | 2026-09-15 | 5day',chat_id=5400514256,kind='private')
    assert '15/09/2026 to 19/09/2026 (5 days)' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:
        leave=db.state(s,'leave:-100:100')
        assert leave['start']=='2026-09-15T00:00:00'
        assert leave['end']=='2026-09-20T00:00:00'

def test_self_leave_words_are_strict_and_case_insensitive():
    for value in ('AL','al','(AL)',' AL. ','ច្បាប់មួយថ្ងៃ\u200b','សុំច្បាប់មួយថ្ងៃ'):
        assert is_self_full_day_leave(value)
    assert not is_self_full_day_leave('Morning Talk\n1. Test Person AL')

def test_self_leave_message_records_today_for_sender():
    u=upd('ច្បាប់មួយថ្ងៃ',uid=100)
    asyncio.run(route(u,ctx()))
    assert 'full-day leave recorded' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:
        leave=db.state(s,'leave:-100:100')
        assert leave['start']=='2026-09-15T00:00:00'
        assert leave['end']=='2026-09-16T00:00:00'

def test_normal_user_cannot_pause_another_user():
    with pytest.raises(ValueError):run('/al | 999',uid=100)

def test_all_change_fields():
    for field,value in [('name','New Person'),('position','SO'),('dealer','CA2'),('region','R1')]:run(f'/change | 100 | {field} | {value}')
    with db.session() as s:
        r=s.scalar(select(Roster).where(Roster.uid==100));assert (r.name,r.position,r.dealer,r.region)==('New Person','SO','CA2','R1')

def test_change_dealer_from_canonical_and_compact_parser():
    assert parse('/change_dealer_from | 100 | Test SO | R1 | CA2 | CA6')==(
        'change_dealer_from',['100','Test SO','R1','CA2','CA6'])
    assert parse('/change_dealer_from_100 | Test SO | R1 | CA2 | _to_dealer_CA6')==(
        'change_dealer_from',['100','Test SO','R1','CA2','_to_dealer_CA6'])

def test_change_dealer_from_updates_roster_and_persistent_override(monkeypatch):
    monkeypatch.setattr('app.bot.WAREHOUSE_ENABLED',False)
    u=run('/change_dealer_from | 100 | Test SO | R1 | CA2 | CA6')
    assert 'From: R1-CA2' in u.effective_message.reply_text.call_args.args[0]
    assert 'To: R1-CA6' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:
        r=s.scalar(select(Roster).where(Roster.uid==100))
        override=db.state(s,'roster_override:100')
        assert (r.region,r.dealer,r.source)==('R1','CA6','manual')
        assert override['dealer']=='CA6' and override['from_dealer']=='CA2'

def test_change_dealer_from_does_not_fail_when_warehouse_refresh_is_unavailable(monkeypatch):
    monkeypatch.setattr('app.bot.WAREHOUSE_ENABLED',True)
    monkeypatch.setattr('app.bot.refresh_warehouse',lambda session: (_ for _ in ()).throw(RuntimeError('ETL failed')))
    u=run('/change_dealer_from | 100 | Test SO | R1 | CA2 | CA6')
    result=u.effective_message.reply_text.call_args.args[0]
    assert '✅ Dealer changed successfully' in result
    assert 'Power BI refresh has been queued' in result
    with db.session() as s:
        r=s.scalar(select(Roster).where(Roster.uid==100))
        assert (r.region,r.dealer)==('R1','CA6')

def test_change_dealer_from_rejects_wrong_identity(monkeypatch):
    monkeypatch.setattr('app.bot.WAREHOUSE_ENABLED',False)
    with pytest.raises(ValueError,match='Real Name does not match'):
        run('/change_dealer_from | 100 | Wrong Person | R1 | CA2 | CA6')

def test_update_plan_prompt_state():
    u=run('/update_plan')
    with db.session() as s:
        p=db.state(s,'upload:5400514256');assert p['chat']==-100 and p['prompt']==777 and p['expires']
    assert 'Reply to THIS message' in u.effective_message.reply_text.call_args.args[0]

def test_set_and_read_dealer_location():
    u=run('/set_dealer_location | R1-CA2 | 11.563299,104.912002')
    assert 'Dealer origin saved' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:assert db.state(s,'dealer_origin:R1-CA2')['lat']==11.563299
    u=run('/dealer_location | R1-CA2')
    assert 'maps.google.com' in u.effective_message.reply_text.call_args.args[0]

def test_add_and_update_dealer_location_with_roster_identity():
    u=run('/add_dealer_location | R1-CA2 | 100 | 1 | Test SO | 11.563299,104.912002')
    assert 'location added' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:
        origin=db.state(s,'dealer_origin:R1-CA2');assert origin['employee_id']=='1' and origin['telegram_id']==100
    u=run('/update_dealer_location | R1-CA2 | 100 | 1 | Test SO | 11.563209,104.912004')
    assert 'location updated' in u.effective_message.reply_text.call_args.args[0]
    with db.session() as s:assert db.state(s,'dealer_origin:R1-CA2')['lat']==11.563209

def test_update_list_so_prompt_state():
    u=run('/update_list_so')
    with db.session() as s:
        p=db.state(s,'roster_upload:5400514256');assert p['chat']==-100 and p['prompt']==777 and p['expires']
    assert 'new List_SO.xlsx' in u.effective_message.reply_text.call_args.args[0]

def test_update_location_prompt_state():
    u=run('/update_location')
    with db.session() as s:
        p=db.state(s,'location_upload:5400514256');assert p['chat']==-100 and p['prompt']==777 and p['expires']
    assert 'Location_All.xlsx' in u.effective_message.reply_text.call_args.args[0]

def test_export_commands_create_valid_xlsx():
    import openpyxl
    for cmd in ['/export 2026-09-15','/summary_by_so 2026-09-15','/export_raw_data 2026-09-15','/export_so_list']:
        u=run(cmd);call=u.effective_message.reply_document.call_args
        assert call.kwargs['filename'].endswith('.xlsx')
        assert openpyxl.load_workbook(call.args[0]).active.max_column>0

def test_currect_plan_command_and_hyphen_alias():
    import openpyxl
    from app.db import Plan,PlanFile
    from app.config import DATA
    assert parse('/currect_plan_W1_W2-Sep-26')==('currect_plan',[])
    with db.session() as s:
        content=(DATA/'Plan_SO.xlsx').read_bytes()
        s.add(Plan(batch='new',employee='1',day='2026-09-21',data={'region':'R1','dealer':'CA2','sale_name':'Test SO','district':'D','commune_code':'1','commune':'C','mark':'RWS'}))
        s.add(PlanFile(batch='new',filename="Plan_SO_WEEK3 , WEEK4_Sep'26.xlsx",content=content,uploaded_at=datetime(2026,9,15),admin=5400514256))
        db.put(s,'plan_latest',{'batch':'new','rows':1,'min_date':'2026-09-21','max_date':'2026-09-21'})
        db.put(s,'plan_dates',{'2026-09-01':'old','2026-09-21':'new'})
    u=run('/currect_plan');call=u.effective_message.reply_document.call_args
    assert call.kwargs['filename']=="Plan_SO_WEEK3 , WEEK4_Sep'26.xlsx"
    assert call.args[0].getvalue()==content

def test_reset_today():
    with db.session() as s:s.add(Missing(chat=-100,uid=100,start=db.now().replace(hour=8),end=db.now().replace(hour=8,minute=15),person={'name':'Test'}))
    run('/reset_today')
    with db.session() as s:assert s.scalar(select(Missing)) is None

def test_controller_cannot_manage_unassigned_group():
    with db.session() as s:s.add(Controller(uid=200,code='R2-SVR4',name='Other'))
    with pytest.raises(ValueError):asyncio.run(command(upd('/remove | 100',uid=200),ctx()))

def test_route_returns_friendly_validation_error():
    u=upd('/add controller | invalid | R1-CA2')
    asyncio.run(route(u,ctx()))
    assert u.effective_message.reply_text.call_args.args[0].startswith('❌')

def test_new_member_service_message_alerts_global_admin_once():
    application=ctx().application
    newcomer=N(id=7166364893,full_name='Li Reaksa',username='lireaksa',is_bot=False)
    actor=N(id=200,full_name='Sovannvong LOEM',username='sovannvong',is_bot=False)
    chat=N(id=-1004303041907,title='Test_SO',type='supergroup')
    at=datetime(2026,9,18,8,30,tzinfo=TZ)
    asyncio.run(notify_global_admins_new_member(application,chat,newcomer,actor,at))
    asyncio.run(notify_global_admins_new_member(application,chat,newcomer,actor,at))
    assert application.bot.send_message.await_count==1
    call=application.bot.send_message.call_args
    assert call.args[0]==5400514256
    assert 'Li Reaksa' in call.args[1] and '7166364893' in call.args[1]
    assert 'Added by a group member' in call.args[1]

def test_chat_member_join_alert_and_non_join_ignored():
    application=ctx().application
    newcomer=N(id=300,full_name='New SO',username=None,is_bot=False)
    actor=N(id=300,full_name='New SO',username=None,is_bot=False)
    chat=N(id=-100999,title='SO Group',type='supergroup')
    change=N(old_chat_member=N(status='left',is_member=False,user=newcomer),new_chat_member=N(status='member',is_member=True,user=newcomer),date=datetime(2026,9,18,9,tzinfo=TZ))
    update=N(chat_member=change,effective_chat=chat,effective_user=actor)
    asyncio.run(chat_member_update(update,N(application=application)))
    assert application.bot.send_message.await_count==1
    assert 'Joined by themselves' in application.bot.send_message.call_args.args[1]
    application.bot.send_message.reset_mock()
    change.old_chat_member.status='member';change.new_chat_member.status='administrator'
    asyncio.run(chat_member_update(update,N(application=application)))
    assert application.bot.send_message.await_count==0

def test_old_direct_reply_pin_does_not_complete_new_window():
    start,end=windows(datetime(2026,9,15).date())[0]
    with db.session() as s:
        s.add(Event(id=1,chat=-100,mid=1,uid=100,at=datetime(2026,9,15,8,5),kind='form',data={'text':'Dealer:CA2 Outlet Name:X','reply':2}))
        s.add(Event(id=2,chat=-100,mid=2,uid=100,at=datetime(2026,9,15,7,0),kind='pin',data={'lat':11,'lon':104}))
    app=ctx().application;asyncio.run(check_window(app,-100,start,end))
    assert app.bot.send_message.await_count==1


def test_setup_group_can_infer_unique_business_scope_from_title(monkeypatch):
    monkeypatch.setattr('app.bot.TEST_MODE',False)
    with db.session() as s:s.delete(s.get(Group,-100))
    run('/setup_group',title='R3-KKG3 Execution Excellent')
    with db.session() as s:
        g=s.get(Group,-100)
        assert g.code=='R3-KKG3' and g.active==1 and g.topic is None
    with pytest.raises(ValueError):run('/setup_group',title='Group_SO_Controll')
