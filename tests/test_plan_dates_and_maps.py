import asyncio
from datetime import date,datetime
from types import SimpleNamespace as N
from unittest.mock import AsyncMock,Mock
import openpyxl
import pytest
from sqlalchemy import select
from app import db
from app.db import Roster,Group,Event,Missing
from app.imports import plan_date,read_plan
from app.geo import coordinates_from_text,online_address,reverse_online,road_distance_km,locate
from app.bot import activity,geocode_event
from app.config import TZ

@pytest.fixture(autouse=True)
def database():
    db.connect('sqlite:///:memory:')
    with db.session() as s:
        s.add(Roster(employee='9001',uid=100,name='Test SO',region='R1',dealer='CA2',position='SO'))
        s.add(Group(id=-100,title='Test_SO',code='R1-CA2',test_uids=[100],active=1))
    yield
    db.engine.dispose()

def test_plan_date_formats():
    assert plan_date(datetime(2026,9,15))=='2026-09-15'
    assert plan_date(date(2026,9,15))=='2026-09-15'
    for value in ['2026-09-15','15/09/2026','15-09-2026','15.09.2026','15 Sep 2026','September 15 2026','Tue, 15 Sep 2026','Date: 15/09/2026','=DATE(2026,9,15)']:
        assert plan_date(value)=='2026-09-15',value
    assert plan_date(46280,openpyxl.Workbook().epoch)=='2026-09-15'

def build_plan(path,dates):
    w=openpyxl.Workbook();s=w.active
    s.append(['Region','Dealer','EMPLOYEE ID','Sale Name','District','Com.Code','Commune',*dates])
    s.append(['R1','CA2',9001,'Test SO','District A',123,'Commune A',*(['RWS']*len(dates))])
    w.save(path)

def test_text_date_headers_import(tmp_path):
    p=tmp_path/'text_dates.xlsx';build_plan(p,['15/09/2026','16-09-2026','2026-09-17','18 Sep 2026'])
    rows,covered=read_plan(p,include_dates=True)
    assert covered=={'2026-09-15','2026-09-16','2026-09-17','2026-09-18'} and len(rows)==4

def test_formula_date_headers_import(tmp_path):
    p=tmp_path/'formula_dates.xlsx';build_plan(p,['=DATE(2026,9,15)','=H1+1'])
    rows,covered=read_plan(p,include_dates=True)
    assert covered=={'2026-09-15','2026-09-16'} and len(rows)==2

def test_day_number_plan_uses_period_and_weekday(tmp_path):
    p=tmp_path/'day_numbers.xlsx';w=openpyxl.Workbook();s=w.active
    s['C5']="WEEK1 , WEEK2_Sep'26"
    s.append([])
    s.append(['Region','Dealer','EMPLOYEE ID','Sale Name','District','Com.Code','Commune',3,4,31,1])
    s.append(['','','','','','','','Mon','Tue','Mon','Tue'])
    s.append(['R1','CA2',9001,'Test SO','District A',123,'Commune A','RWS','RWS','RWS','RWS'])
    w.save(p)
    rows,covered=read_plan(p,include_dates=True)
    assert covered=={'2026-08-03','2026-08-04','2026-08-31','2026-09-01'}
    assert len(rows)==4

def test_off_master_layout_resolves_commune_name_and_date_formatted_day_numbers(tmp_path):
    p=tmp_path/'off_master.xlsx';w=openpyxl.Workbook();s=w.active
    s['C5']="WEEK3 , WEEK4_Nov'26"
    headers=['Region','Dealer','EMPLOYEE ID','Sale Name','District','Com.Code',
             'COMMUNE NAME','Distance',datetime(1900,1,26),datetime(1900,1,27),
             datetime(1900,1,1)]
    for col,value in enumerate(headers,1):s.cell(7,col,value)
    for col,value in enumerate(['','','','','','','','','Mon','Tue','Sun'],1):s.cell(8,col,value)
    values=['R1','CA2',9001,'Test SO','District A',123,'Commune A',22,'RWS','RWS','RWS']
    for col,value in enumerate(values,1):s.cell(9,col,value)
    w.save(p)
    rows,covered=read_plan(p,include_dates=True)
    assert covered=={'2026-10-26','2026-10-27','2026-11-01'}
    assert [(row['day'],row['data']['commune']) for row in rows]==[
        ('2026-10-26','Commune A'),('2026-10-27','Commune A'),('2026-11-01','Commune A')]

def test_google_maps_coordinates():
    expected=(11.563299,104.912002)
    assert coordinates_from_text('https://maps.google.com/maps?q=11.563299,104.912002&ll=11.563299,104.912002&z=16')==expected
    assert coordinates_from_text('https://maps.google.com/maps?ll=11.563299,104.912002')==expected
    assert coordinates_from_text('11.563299, 104.912002')==expected
    assert coordinates_from_text('https://maps.google.com/maps?q=999,104') is None
    assert coordinates_from_text('Morning Talk\nDate: 09,10,2026\nBTB1') is None
    assert coordinates_from_text('Date: 13.10.2026') is None

def test_address_mapping_for_provided_coordinate():
    payload={'display_name':'test','address':{'village':'សង្កាត់អូរឬស្សីទី ៣','town':'ប្រាំពីរមករា','state':'រាជធានីភ្នំពេញ'}}
    geo=online_address(payload)
    assert geo['province']=='រាជធានីភ្នំពេញ'
    assert geo['district']=='ប្រាំពីរមករា'
    assert geo['commune']=='សង្កាត់អូរឬស្សីទី ៣'

def test_supplied_kml_matches_provided_coordinate():
    geo=locate(11.563299,104.912002)
    assert geo['geo_issue']==''
    assert geo['province']=='Phnom Penh'
    assert geo['district']=='ខណ្ឌ៧មករា'
    assert geo['commune']=='Veal Vong'
    assert geo['commune_code']=='120307'

def test_reverse_online_is_rate_limited_and_mapped(monkeypatch):
    import app.geo as geo
    response=Mock();response.json.return_value={'address':{'village':'Commune','town':'District','state':'Province'}};response.raise_for_status=Mock()
    client=AsyncMock();client.get.return_value=response
    cm=AsyncMock();cm.__aenter__.return_value=client
    monkeypatch.setattr(geo.httpx,'AsyncClient',Mock(return_value=cm))
    result=asyncio.run(reverse_online(11.5,104.9,asyncio.Lock(),[0.0]))
    assert result['commune']=='Commune' and client.get.await_count==1

def test_osrm_road_distance(monkeypatch):
    import app.geo as geo
    response=Mock();response.json.return_value={'code':'Ok','routes':[{'distance':12600.0}]};response.raise_for_status=Mock()
    client=AsyncMock();client.get.return_value=response
    cm=AsyncMock();cm.__aenter__.return_value=client
    monkeypatch.setattr(geo.httpx,'AsyncClient',Mock(return_value=cm))
    distance,issue=asyncio.run(road_distance_km(11.5,104.9,11.6,105.0,asyncio.Lock(),[0.0]))
    assert distance==12.6 and issue=='' and client.get.await_count==1

def test_osrm_road_geometry(monkeypatch):
    import app.geo as geo
    response=Mock();response.json.return_value={'code':'Ok','routes':[{'distance':12600.0,'geometry':{'coordinates':[[104.9,11.5],[105.0,11.6]]}}]};response.raise_for_status=Mock()
    client=AsyncMock();client.get.return_value=response
    cm=AsyncMock();cm.__aenter__.return_value=client
    monkeypatch.setattr(geo.httpx,'AsyncClient',Mock(return_value=cm))
    distance,issue,geometry=asyncio.run(road_distance_km(11.5,104.9,11.6,105.0,asyncio.Lock(),[0.0],include_geometry=True))
    assert distance==12.6 and issue=='' and geometry==[[104.9,11.5],[105.0,11.6]]

def make_update(text):
    msg=N(text=text,caption=None,chat_id=-100,message_id=9,message_thread_id=None,date=datetime(2026,9,15,10,18,tzinfo=TZ),reply_to_message=None,photo=[],document=None,location=None,media_group_id=None)
    return N(effective_message=msg,effective_user=N(id=100,full_name='Test SO',username='tester',is_bot=False))

def test_map_link_saved_as_pin_not_reason():
    with db.session() as s:s.add(Missing(chat=-100,uid=100,start=datetime(2026,9,15,10),end=datetime(2026,9,15,10,15),sent=datetime(2026,9,15,10,15),person={'name':'Test SO'}))
    app=N(bot_data={},create_task=Mock())
    context=N(application=app)
    asyncio.run(activity(make_update('https://maps.google.com/maps?q=11.563299,104.912002&ll=11.563299,104.912002&z=16'),context))
    with db.session() as s:
        event=s.scalar(select(Event));missing=s.scalar(select(Missing))
        assert event.kind=='pin' and event.data['lat']==11.563299 and event.data['lon']==104.912002
        assert missing.reason is None
    assert app.create_task.call_count==1
    app.create_task.call_args.args[0].close()

def test_geocode_event_persists_and_caches(monkeypatch):
    import app.bot as bot
    with db.session() as s:s.add(Event(chat=-100,mid=1,uid=100,at=datetime(2026,9,15,10),kind='pin',data={'lat':11.563299,'lon':104.912002}))
    monkeypatch.setattr(bot,'locate',lambda lat,lon:{'geo_issue':'No matching boundary'})
    remote=AsyncMock(return_value={'province':'Phnom Penh','district':'7 Makara','commune':'Ou Ruessei 3','geo_issue':''})
    monkeypatch.setattr(bot,'reverse_online',remote)
    app=N(bot_data={'geo_lock':asyncio.Lock(),'geo_last':[0.0]})
    asyncio.run(geocode_event(app,1))
    with db.session() as s:
        e=s.get(Event,1);assert e.data['geo']['commune']=='Ou Ruessei 3'
        assert db.state(s,'geo:11.563299:104.912002')['district']=='7 Makara'
