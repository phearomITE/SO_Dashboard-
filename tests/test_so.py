import asyncio
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace as N
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from app import db
from app.db import Roster,Group,Event,Missing,Controller,Plan
from app.rules import windows,photo_status,link_visits
from app.imports import read_roster,read_roster_controllers,read_plan,import_plan,import_roster,read_dealer_locations,import_dealer_locations,purge_excluded_roster
from app.exports import export_data,telegram_message_link
from app.bot import command,activity,parse,REASON_REPLY_MESSAGES
from app.service import check_window,followups,members
from app.config import DATA,TZ
from app.question_settings import update_question_settings

@pytest.fixture(autouse=True)
def database():
    db.connect('sqlite:///:memory:')
    with db.session() as s:
        s.add(Roster(employee='1',uid=100,name='Test SO',username='tester',region='R1',dealer='CA2',position='Sales Officer'))
        s.add(Group(id=-100,title='Test_SO',code='R1-CA2',test_uids=[100],active=1))
    yield
    db.engine.dispose()

def event(mid,kind,at='08:10',uid=100,**data):
    return Event(id=mid,mid=mid,chat=-100,uid=uid,at=datetime.fromisoformat('2026-09-15T'+at),kind=kind,data=data)

def update(text='',uid=5400514256,mid=1,reply=None,**extra):
    msg=N(text=text,caption=None,chat_id=-100,message_id=mid,message_thread_id=None,date=datetime(2026,9,15,8,10,tzinfo=TZ),reply_to_message=reply,reply_text=AsyncMock(),reply_document=AsyncMock(),photo=[],document=None,location=None,media_group_id=None,**extra)
    return N(effective_message=msg,effective_user=N(id=uid,full_name='Person',username='person',is_bot=False),effective_chat=N(id=-100,title='Test_SO',type='supergroup'))

def context():return N(application=N(bot=N(send_message=AsyncMock(return_value=N(message_id=900))),bot_data={}))

def test_windows():
    w=windows(datetime(2026,9,15).date())
    assert len(w)==30
    assert w[0][0].strftime('%H:%M')=='08:00'
    assert w[14][1].strftime('%H:%M')=='11:45'
    assert w[15][0].strftime('%H:%M')=='13:00'
    assert w[-1][1].strftime('%H:%M')=='16:45'

def test_missing_report_members_include_so_and_ss():
    with db.session() as s:
        s.add(Roster(employee='2',uid=200,name='Test SS',username='supervisor',region='R1',dealer='CA2',position='SS'))
        g=s.get(Group,-100);g.test_uids=[100,200]
    with db.session() as s:
        assert {r.uid for r in members(s,s.get(Group,-100))}=={100,200}

def test_timestamp_rule():
    dt=datetime(2026,9,15,9,40)
    assert photo_status('08:34 AM',dt)['gap']==66
    assert photo_status('08:34 AM',dt)['status']=='Review'
    assert photo_status('09:10 AM',dt)['status']=='OK'  # exactly 30 minutes
    assert photo_status('09:11 AM',dt)['status']=='OK'
    assert photo_status('10:00 AM',dt)['status']=='Review'  # future is invalid
    assert photo_status('No time',dt)['status']=='Review'
    assert photo_status('08:00 AM and 08:34 AM',dt)['status']=='Review'
    assert photo_status('14/09/2026 08:34 AM',dt)['gap']==1506

def test_pin_one_to_one():
    v=link_visits([event(1,'photo'),event(2,'photo',at='08:11'),event(3,'pin',at='08:12',lat=1,lon=2)])
    assert sum(x['pin'] is not None for x in v)==1

def test_album_form_and_pin():
    es=[event(1,'photo',album='A'),event(2,'photo',album='A'),event(3,'form',at='08:11',text='Dealer:CA2 Outlet Name:Shop'),event(4,'pin',at='08:12',reply=3,lat=1,lon=2)]
    v=link_visits(es)
    assert len(v)==1 and v[0]['pin'].mid==4 and len(v[0]['events'])==3

def test_other_sender_pin_not_counted():
    v=link_visits([event(1,'photo',reply=2),event(2,'pin',uid=999)])
    assert v[0]['pin'] is None

def test_inputs():
    rows=read_roster(DATA/'List_SO.xlsx')
    assert rows
    assert not any(x['position'].casefold() in ('head of sales','area sales manager') for x in rows)
    assert not any(x['uid'] in {477167813,424220108,831220255,7166364893} for x in rows)
    p=read_plan(DATA/'Plan_SO.xlsx')
    assert len(p)>1000
    assert any(x['employee']=='90026071' and x['day']=='2026-09-10' for x in p)
    assert any(x['employee']=='90024146' and x['data']['sale_name']=='Ra Phearom' and x['day']=='2026-09-11' and x['data']['commune_code']=='80809' for x in p)

def test_roster_controller_column_uses_official_65_group_codes():
    pairs=read_roster_controllers(DATA/'List_SO.xlsx')
    assert len({uid for uid,_ in pairs})==1
    assert len(pairs)==65
    assert (464829941,'R4-KCG4') in pairs
    assert (464829941,'R3-KCG4') not in pairs

def test_roster_controller_reader_ignores_short_blank_rows(tmp_path):
    import openpyxl
    path=tmp_path/'short-row.xlsx'
    workbook=openpyxl.Workbook();sheet=workbook.active
    sheet.append(['Region','Dealer','Controller'])
    sheet.append(['R1','CA2',464829941])
    sheet.append([])
    workbook.save(path)
    assert read_roster_controllers(path)=={(464829941,'R1-CA2')}

def test_dealer_location_sales_coverage_import():
    path=DATA/'Dealer Location With sales coverage.xlsx'
    rows=read_dealer_locations(path)
    assert len(rows)==68 and len({x['code'] for x in rows})==65
    with db.session() as s:
        source_rows,dealers=import_dealer_locations(s,path)
        assert (source_rows,dealers)==(68,65)
        ca2=db.state(s,'dealer_origin:R1-CA2')
        assert (ca2['lat'],ca2['lon'])==(11.537092,104.917502)
        # Duplicate coverage uses the normal sales row, not the WH_* row.
        sra5=db.state(s,'dealer_origin:R3-SRA5')
        assert sra5['remark']=='' and sra5['alternatives']==2

def test_roster_sync_deletes_old_management_roles():
    with db.session() as s:
        s.add(Roster(employee='manager-1',uid=999001,name='Manager',region='R1',dealer='R1',position='Head Of Sales',source='excel',active=0))
        s.add(Roster(employee='manager-2',uid=999002,name='Manager 2',region='R1',dealer='CA1',position='Area Sales Manager',source='excel',active=0))
    with db.session() as s:import_roster(s,DATA/'List_SO.xlsx')
    with db.session() as s:
        assert s.scalar(select(Roster).where(Roster.employee=='manager-1')) is None
        assert s.scalar(select(Roster).where(Roster.employee=='manager-2')) is None

def test_startup_cleanup_deletes_management_roles_without_roster_reimport():
    with db.session() as s:
        s.add_all([
            Roster(employee='manager-old-1',uid=999101,name='Old HOS',region='R1',dealer='R1',position='Head Of Sales',source='excel',active=0),
            Roster(employee='manager-old-2',uid=999102,name='Old ASM',region='R2',dealer='R2',position='Area Sales Manager',source='excel',active=0)])
    with db.session() as s:assert purge_excluded_roster(s)==2
    with db.session() as s:
        assert s.scalar(select(Roster).where(Roster.employee.in_(['manager-old-1','manager-old-2']))) is None

def test_failed_plan_does_not_replace(tmp_path):
    with db.session() as s:
        import_plan(s,DATA/'Plan_SO.xlsx',5400514256)
    with db.session() as s:
        before=db.state(s,'plan_dates').copy()
        assert len(before)==42  # Includes blank/off days in the dated header.
    bad=tmp_path/'bad.xlsx';bad.write_text('invalid')
    with pytest.raises(Exception):
        with db.session() as s:import_plan(s,bad,5400514256)
    with db.session() as s:assert db.state(s,'plan_dates')==before

def test_new_plan_replaces_all_active_date_mappings(monkeypatch,tmp_path):
    from app import imports
    first=tmp_path/'first.xlsx';first.write_bytes(b'first')
    second=tmp_path/'second.xlsx';second.write_bytes(b'second')
    rows1=[{'employee':'1','day':'2026-09-21','data':{'commune':'Old','commune_code':'1'}}]
    rows2=[{'employee':'1','day':'2026-10-01','data':{'commune':'New','commune_code':'2'}}]
    monkeypatch.setattr(imports,'read_plan',lambda path,include_dates=False:
                        (rows1,{'2026-09-21'}) if path==first else (rows2,{'2026-10-01'}))
    with db.session() as s:
        imports.import_plan(s,first,5400514256)
        imports.import_plan(s,second,5400514256)
    with db.session() as s:
        active=db.state(s,'plan_dates')
        assert active=={'2026-10-01':__import__('hashlib').sha256(b'second').hexdigest()}

def test_controller_syntax_and_remove():
    assert parse('/add controller| 1468225026 | R1-CA1,R1-CA2 |')==('add',['controller','1468225026','R1-CA1,R1-CA2'])
    c=context()
    asyncio.run(command(update('/add controller| 1468225026 | R1-CA1,R1-CA2 |'),c))
    with db.session() as s:assert len(list(s.scalars(select(Controller))))==2
    asyncio.run(command(update('/remove controller | 1468225026 |'),c))
    with db.session() as s:assert list(s.scalars(select(Controller)))==[]

def test_permissions():
    with pytest.raises(ValueError):asyncio.run(command(update('/add controller | 123 | R1-CA1',uid=100),context()))
    with pytest.raises(ValueError):asyncio.run(command(update('/update_plan',uid=100),context()))

def test_manual_add():
    asyncio.run(command(update('/add | 123 | 90099999 | Demo SO | R1 | CA2 | Sales Officer'),context()))
    with db.session() as s:
        r=s.scalar(select(Roster).where(Roster.uid==123))
        assert r.position=='SO' and r.employee=='90099999'

def test_remove_restore_persist():
    asyncio.run(command(update('/remove | 100'),context()))
    with db.session() as s:assert db.state(s,'excluded:-100:100')['yes']
    asyncio.run(command(update('/restore | 100'),context()))
    with db.session() as s:assert not db.state(s,'excluded:-100:100')['yes']

def test_alert_and_followup_reason():
    app=context().application
    start,end=windows(datetime(2026,9,15).date())[0]
    asyncio.run(check_window(app,-100,start,end))
    assert app.bot.send_message.await_count==1
    asyncio.run(check_window(app,-100,start,end))
    assert app.bot.send_message.await_count==1
    with db.session() as s:
        m=s.scalar(select(Missing));m.sent=datetime(2026,9,15,8,15,2)
    asyncio.run(followups(app,datetime(2026,9,15,8,20,1)))
    assert app.bot.send_message.await_count==1
    app.bot.send_message.return_value=N(message_id=901)
    asyncio.run(followups(app,datetime(2026,9,15,8,20,2)))
    assert app.bot.send_message.await_count==2

def test_stop_ask_suppresses_only_missing_followup_delivery():
    app=context().application
    start,end=windows(datetime(2026,9,15).date())[0]
    asyncio.run(check_window(app,-100,start,end))
    assert app.bot.send_message.await_count==1  # original red alert still sends
    with db.session() as s:
        m=s.scalar(select(Missing));m.sent=datetime(2026,9,15,8,15)
        update_question_settings(s,{'missing_report_followup':False},5400514256)
    asyncio.run(followups(app,datetime(2026,9,15,8,21)))
    assert app.bot.send_message.await_count==1
    with db.session() as s:assert s.scalar(select(Missing)).question_mid is None

def test_missing_alert_combines_all_group_users():
    with db.session() as s:
        s.add(Roster(employee='2',uid=101,name='Second SO',username='',region='R1',dealer='CA2',position='SO'))
        s.get(Group,-100).test_uids=[100,101]
    app=context().application
    start,end=windows(datetime(2026,9,15).date())[0]
    asyncio.run(check_window(app,-100,start,end))
    assert app.bot.send_message.await_count==1
    text=app.bot.send_message.call_args.args[1]
    assert '1. SO | Test SO' in text and '2. SO | Second SO' in text
    with db.session() as s:
        rows=list(s.scalars(select(Missing)))
        assert len(rows)==2 and rows[0].alert_mid==rows[1].alert_mid
        for row in rows:row.sent=datetime(2026,9,15,8,15)
    # Reply to the combined alert ID returned by check_window.
    asyncio.run(activity(update('មានបញ្ហារថយន្ត',uid=100,reply=N(message_id=900)),context()))
    with db.session() as s:assert s.scalar(select(Missing)).reason=='មានបញ្ហារថយន្ត'
    asyncio.run(followups(app,datetime(2026,9,15,8,30)))
    assert app.bot.send_message.await_count==2

def test_complete_outlet_no_alert():
    with db.session() as s:s.add_all([event(1,'form',text='Dealer:CA2 Outlet Name:X'),event(2,'pin',at='08:11',lat=11.5,lon=104.9)])
    app=context().application
    asyncio.run(check_window(app,-100,*windows(datetime(2026,9,15).date())[0]))
    assert app.bot.send_message.await_count==0

def test_half_open_windows():
    with db.session() as s:s.add_all([event(1,'form',at='08:15',text='Dealer:CA2 Outlet Name:X'),event(2,'pin',at='08:15',lat=11.5,lon=104.9)])
    app=context().application
    asyncio.run(check_window(app,-100,*windows(datetime(2026,9,15).date())[0]))
    assert app.bot.send_message.await_count==1

def test_leave():
    asyncio.run(command(update('/al_morning | 100 | 2026-09-15'),context()))
    app=context().application
    asyncio.run(check_window(app,-100,*windows(datetime(2026,9,15).date())[0]))
    assert app.bot.send_message.await_count==0

def test_exports():
    with db.session() as s:
        s.add_all([event(1,'form',text='Dealer:CA2 Outlet Name:X'),event(2,'pin',at='08:11',lat=11.5,lon=104.9)])
    import openpyxl
    with db.session() as s:
        b,n=export_data(s,'summary_by_so',datetime(2026,9,15).date(),[-100]);w=openpyxl.load_workbook(b)
        assert n==1 and w.active.cell(2,7).value==1 and w.active.cell(2,9).value=='Not checked'
        for cmd in ['export','export_raw_data']:
            b,n=export_data(s,cmd,datetime(2026,9,15).date(),[-100]);ws=openpyxl.load_workbook(b).active
            assert ws.max_column>=10
            if cmd=='export_raw_data':
                headers=[c.value for c in ws[1]]
                assert headers==['Region','Date','Dealer','Province','District','Commune','Lat','Lon','Real Name','Employee ID','Telegram ID','Position','First Report','Last Report','Plan Check In','Check in Over 10Km','Dealer First Check In (Distance Km)','Reason_Miss','Reason_Wrong_Plan','Reason_Over_10Km']

def test_multiple_outlets_same_so_export_all_raw_rows():
    with db.session() as s:
        s.add_all([
            event(101,'form',at='13:05',text='Dealer:CA2 Outlet Name:Outlet A'),
            event(102,'pin',at='13:06',reply=101,lat=11.56,lon=104.91),
            event(103,'form',at='13:20',text='Dealer:CA2 Outlet Name:Outlet B'),
            event(104,'pin',at='13:21',reply=103,lat=11.57,lon=104.92),
            event(105,'form',at='13:35',text='Dealer:CA2 Outlet Name:Outlet C'),
            event(106,'pin',at='13:36',reply=105,lat=11.58,lon=104.93),
        ])
    import openpyxl
    with db.session() as s:
        raw,n=export_data(s,'export_raw_data',datetime(2026,9,15).date(),[-100])
        assert n==3 and openpyxl.load_workbook(raw).active.max_row==4
        summary,n=export_data(s,'summary_by_so',datetime(2026,9,15).date(),[-100])
        ws=openpyxl.load_workbook(summary).active
        assert n==1 and ws.cell(2,7).value==3

def test_raw_export_bi_daily_metrics_and_exact_schema():
    with db.session() as s:
        s.add_all([event(201,'photo',at='14:25',text='Dealer:CA2 Outlet Name:X'),event(202,'pin',at='14:26',reply=201,lat=11.56,lon=104.91,route={'kind':'Dealer to First Check-in','distance_km':8.45,'status':'OK'})])
    import openpyxl
    with db.session() as s:
        b,n=export_data(s,'export_raw_data',datetime(2026,9,15).date(),[-100]);ws=openpyxl.load_workbook(b).active
        headers={c.value:c.column for c in ws[1]}
        assert n==1 and len(headers)==20
        assert ws.cell(2,headers['First Report']).value=='02:25 PM'
        assert ws.cell(2,headers['Last Report']).value=='02:25 PM'
        assert ws.cell(2,headers['Dealer First Check In (Distance Km)']).value==8.45
        assert ws.cell(2,headers['Check in Over 10Km']).value=='No'

def test_raw_export_identity_columns_and_no_extra_columns():
    assert telegram_message_link(-1004303041907,12345)=='https://t.me/c/4303041907/12345'
    assert telegram_message_link(-12345,12345)==''
    with db.session() as s:
        s.add_all([Event(id=301,chat=-1004303041907,mid=777,uid=100,at=datetime(2026,9,11,10),kind='photo',data={'text':'Dealer:CA2 Outlet Name:X','gap':5,'photo_time':'2026-09-11 09:55:00','person':{'employee':'1','name':'Test SO','region':'R1','dealer':'CA2','position':'SO'}}),Event(id=302,chat=-1004303041907,mid=778,uid=100,at=datetime(2026,9,11,10,1),kind='pin',data={'reply':777,'lat':11.56,'lon':104.91})])
        s.add(Group(id=-1004303041907,title='Test_SO_Link',code='R1-CA2',test_uids=[100],active=1))
    import openpyxl
    with db.session() as s:
        b,n=export_data(s,'export_raw_data',datetime(2026,9,11).date(),[-1004303041907]);ws=openpyxl.load_workbook(b).active
        headers={c.value:c.column for c in ws[1]}
        assert n==1 and len(headers)==20 and 'Open to Image' not in headers and 'Missing' not in headers
        assert ws.cell(2,headers['Employee ID']).value=='1'
        assert ws.cell(2,headers['Telegram ID']).value==100

def test_raw_export_does_not_mix_missing_fact_rows_into_visit_master():
    with db.session() as s:
        s.add(Missing(chat=-100,uid=100,start=datetime(2026,9,15,8),end=datetime(2026,9,15,8,15),reason='Traffic',person={'employee':'1','name':'Test SO','region':'R1','dealer':'CA2','position':'SO'}))
    import openpyxl
    with db.session() as s:
        b,n=export_data(s,'export_raw_data',datetime(2026,9,15).date(),[-100]);ws=openpyxl.load_workbook(b).active
        headers={c.value:c.column for c in ws[1]}
        assert n==0 and ws.max_row==1 and 'Missing' not in headers

def test_edit_dedup_and_original_time():
    a=update('Dealer:CA2 Outlet Name:X',uid=100,mid=55)
    asyncio.run(activity(a,context()));asyncio.run(activity(a,context()))
    with db.session() as s:
        es=list(s.scalars(select(Event)));assert len(es)==1 and es[0].at.hour==8

def test_24_hour_and_text_date():
    r=photo_status('Wed, 09 Sep 2026\n14:24',datetime(2026,9,9,14,25,4))
    assert r['gap']==1.07 and r['status']=='OK'
    assert photo_status('00:15',datetime(2026,9,15,1))['gap']==45

def test_plain_reason_same_sender_only():
    with db.session() as s:s.add(Missing(chat=-100,uid=100,start=datetime(2026,9,15,8),end=datetime(2026,9,15,8,15),sent=datetime(2026,9,15,8,15),alert_mid=900,person={'name':'Test SO'}))
    a=update('រថយន្តខូច',uid=100);a.effective_message.date=datetime(2026,9,15,8,19,tzinfo=TZ)
    asyncio.run(activity(a,context()))
    with db.session() as s:assert s.scalar(select(Missing)).reason=='រថយន្តខូច'
    a.effective_message.reply_text.assert_awaited_once()
    assert a.effective_message.reply_text.await_args.args[0] in REASON_REPLY_MESSAGES

def test_exception_question_replies_are_stored_in_separate_bi_columns():
    with db.session() as s:
        s.add_all([
            event(401,'form',at='08:10',text='Dealer:CA2 Outlet Name:X'),
            event(402,'pin',at='08:11',reply=401,lat=11.56,lon=104.91,
                  wrong_plan_question_mid=950,over_10km_question_mid=951,
                  wrong_plan_question_at='2026-09-15T08:12:00',over_10km_question_at='2026-09-15T08:13:00',
                  route={'kind':'Previous Check-in to Current Check-in','distance_km':12.5,'status':'Over limit'}),
        ])
    wrong=update('ប្ដូរ Plan បន្ទាន់',uid=100,mid=501,reply=N(message_id=950))
    over=update('Outlet ឆ្ងាយ',uid=100,mid=502,reply=N(message_id=951))
    asyncio.run(activity(wrong,context()))
    asyncio.run(activity(over,context()))
    wrong.effective_message.reply_text.assert_awaited_once()
    over.effective_message.reply_text.assert_awaited_once()
    assert wrong.effective_message.reply_text.await_args.args[0] in REASON_REPLY_MESSAGES
    assert over.effective_message.reply_text.await_args.args[0] in REASON_REPLY_MESSAGES
    with db.session() as s:
        pin=s.scalar(select(Event).where(Event.mid==402))
        assert pin.data['wrong_plan_reason']=='ប្ដូរ Plan បន្ទាន់'
        assert pin.data['over_10km_reason']=='Outlet ឆ្ងាយ'
        raw,n=export_data(s,'export_raw_data',datetime(2026,9,15).date(),[-100])
    import openpyxl
    ws=openpyxl.load_workbook(raw).active;headers={c.value:c.column for c in ws[1]}
    assert n==1
    assert ws.cell(2,headers['Reason_Wrong_Plan']).value=='ប្ដូរ Plan បន្ទាន់'
    assert ws.cell(2,headers['Reason_Over_10Km']).value=='Outlet ឆ្ងាយ'

def test_reason_reply_with_unmatched_thread_message_stops_followup():
    with db.session() as s:s.add(Missing(chat=-100,uid=100,start=datetime(2026,9,15,8),end=datetime(2026,9,15,8,15),sent=datetime(2026,9,15,8,15),alert_mid=900,person={'name':'Test SO'}))
    # Telegram may point at another message in the reply chain. The same SO's
    # simple reason must still resolve the latest pending alert.
    a=update('សុំយឺតបន្តិចដោយសារឡានខូច',uid=100,reply=N(message_id=899))
    a.effective_message.date=datetime(2026,9,15,8,16,tzinfo=TZ)
    asyncio.run(activity(a,context()))
    with db.session() as s:assert s.scalar(select(Missing)).reason=='សុំយឺតបន្តិចដោយសារឡានខូច'
    app=context().application
    asyncio.run(followups(app,datetime(2026,9,15,8,21)))
    assert app.bot.send_message.await_count==0

def test_geospatial_plan_correct_incorrect_and_unknown(tmp_path,monkeypatch):
    import json
    from app import geo
    from app.db import Plan
    monkeypatch.setattr(geo,'DATA',tmp_path);monkeypatch.setattr(geo,'_cache',None)
    folder=tmp_path/'boundaries';folder.mkdir()
    (folder/'test.geojson').write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{'commune_code':'010101','commune':'Commune A','district':'District A','province':'Province A'},'geometry':{'type':'Polygon','coordinates':[[[104,11],[105,11],[105,12],[104,12],[104,11]]]}}]}))
    loc=geo.locate(11.5,104.5)
    with db.session() as s:
        db.put(s,'plan_dates',{'2026-09-15':'batch'})
        s.add(Plan(batch='batch',employee='1',day='2026-09-15',data={'commune_code':'10101','commune':'Commune A','district':'District A'}));s.flush()
        assert geo.plan_check(s,'1','2026-09-15',loc)[0]=='Correct'
        assert geo.plan_check(s,'1','2026-09-15',{**loc,'commune_code':'999999'})[0]=='Incorrect'
        assert geo.plan_check(s,'1','2026-09-15',geo.locate(13,104.5))[0]=='Not checked'

def test_plan_check_exact_sale_name_fallback():
    from app.db import Plan
    from app import geo
    location={'commune_code':'080809','commune':'Mkak','district':'Angk Snuol','geo_issue':''}
    with db.session() as s:
        db.put(s,'plan_dates',{'2026-09-10':'batch'})
        s.add(Plan(batch='batch',employee='Vacant_CA1_ (1)',day='2026-09-10',data={'sale_name':'Phearom 619','commune_code':'080809','commune':'Mkak','district':'Angk Snuol'}));s.flush()
        assert geo.plan_check(s,'manual:7166364893','2026-09-10',location,'Phearom 619')[0]=='Correct'
        assert geo.plan_check(s,'manual:7166364893','2026-09-10',{**location,'commune_code':'120307'},'Phearom 619')[0]=='Incorrect'

def test_manual_identity_resolves_official_employee_for_plan():
    from app import geo
    with db.session() as s:
        s.add(Roster(employee='manual:7166364893',uid=7166364893,name='Phearom 619',region='R1',dealer='CA2',position='SO',source='manual',active=0))
        s.add(Roster(employee='90024146',uid=7166364893,name='Ra Phearom',region='R1',dealer='CA2',position='SO',source='excel',active=1))
        s.add(Plan(batch='batch',employee='90024146',day='2026-09-20',data={'sale_name':'Ra Phearom','commune':'Mkak','commune_code':'080809'}))
        db.put(s,'plan_dates',{'2026-09-20':'batch'})
    with db.session() as s:
        rows=geo.plan_locations(s,'manual:7166364893','2026-09-20','Phearom 619')
        assert len(rows)==1 and rows[0]['commune_code']=='080809'

def test_plan_check_existing_date_without_so_schedule_is_incorrect():
    from app.db import Plan
    from app import geo
    with db.session() as s:
        db.put(s,'plan_dates',{'2026-09-10':'batch'})
        s.add(Plan(batch='batch',employee='other',day='2026-09-10',data={'sale_name':'Other SO','commune_code':'080809','commune':'Mkak'}));s.flush()
        result=geo.plan_check(s,'manual:100','2026-09-10',{'commune_code':'080809','commune':'Mkak','geo_issue':''},'Phearom 619')
        assert result[0]=='Incorrect' and 'no scheduled commune' in result[1]

def test_plan_check_name_fallback_when_source_code_is_invalid():
    from app.db import Plan
    from app import geo
    with db.session() as s:
        db.put(s,'plan_dates',{'2026-09-10':'batch'})
        s.add(Plan(batch='batch',employee='1',day='2026-09-10',data={'commune_code':'Ta Khmau','commune':'Kampong Samnanh'}));s.flush()
        assert geo.plan_check(s,'1','2026-09-10',{'commune_code':'080201','commune':'Kampong Samnanh','geo_issue':''})[0]=='Correct'

def test_kml_polygon(tmp_path,monkeypatch):
    from app import geo
    monkeypatch.setattr(geo,'DATA',tmp_path);monkeypatch.setattr(geo,'_cache',None)
    folder=tmp_path/'boundaries';folder.mkdir()
    (folder/'test.kml').write_text('''<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>Commune A</name><ExtendedData><Data name="commune_code"><value>123456</value></Data></ExtendedData><Polygon><outerBoundaryIs><LinearRing><coordinates>104,11 105,11 105,12 104,12 104,11</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>''')
    assert geo.locate(11.5,104.5)['commune_code']=='123456'

def test_controller_export_scope():
    with db.session() as s:
        s.add(Controller(uid=200,code='R1-CA2',name='Controller'))
        s.add(Group(id=-200,title='Other',code='R2-SVR4',active=1))
        s.add(Missing(chat=-200,uid=333,start=datetime(2026,9,15,8),end=datetime(2026,9,15,8,15),person={'name':'Private Other'}))
    a=update('/export 2026-09-15',uid=200)
    asyncio.run(command(a,context()))
    assert a.effective_message.reply_document.call_args.kwargs['caption']=='0 rows'

def test_database_models_compile_for_postgres():
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects import postgresql
    for table in db.Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=postgresql.dialect()))

def test_application_build_without_network(monkeypatch):
    from app import bot
    monkeypatch.setattr(bot,'TOKEN','123456:offline-test-token')
    app=bot.create_bot()
    assert app.job_queue is not None and app.handlers
