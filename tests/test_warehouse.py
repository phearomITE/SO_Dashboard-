from datetime import datetime
from pathlib import Path
from sqlalchemy import select,func
import pytest
from app import db
from app.config import DATA
from app.db import (Event,Group,Roster,BronzeLocation,SilverLocation,SilverVisit,Missing,
                    WarehouseRun,QualityIssue,Reconciliation,gold_view_statements)
from app.warehouse import read_locations,import_locations,refresh_warehouse

@pytest.fixture(autouse=True)
def database():
    db.connect('sqlite:///:memory:')
    with db.session() as s:
        s.add(Roster(employee='9001',uid=100,name='Test SO',username='test',region='R1',dealer='CA1',position='SO'))
        s.add(Group(id=-1004303041907,title='Test_SO',code='R1-CA1',test_uids=[100],active=1))
    yield
    db.engine.dispose()

def test_location_all_bronze_and_silver_import():
    rows=read_locations(DATA/'Location_All.xlsx')
    assert len(rows)==1652 and rows[0][1]['commune_code']=='050901'
    with db.session() as s:
        count,batch=import_locations(s,DATA/'Location_All.xlsx')
        assert count==1652
    with db.session() as s:
        assert s.scalar(select(func.count()).select_from(BronzeLocation))==1652
        assert s.scalar(select(func.count()).select_from(SilverLocation))==1652
        row=s.get(SilverLocation,'050901')
        assert row.province_name=='Kampong Speu' and row.province_name_kh and row.dealer=='CA1' and row.region=='R1'

def test_production_cleanup_removes_test_group_activity():
    with db.session() as s:
        s.add(Event(id=99,chat=-1004303041907,mid=999,uid=100,at=datetime(2026,9,21,8),kind='form',data={}))
        s.add(Missing(chat=-1004303041907,uid=100,start=datetime(2026,9,21,8),end=datetime(2026,9,21,8,15),person={}))
        s.add(SilverVisit(visit_id=99,source_event_id=99,chat_id=-1004303041907,group_name='Test_SO',message_id=999,
                          telegram_id=100,employee_id='9001',real_name='Test SO',username='test',region='R1',dealer='CA1',
                          position='SO',submitted_at=datetime(2026,9,21,8),report_date=datetime(2026,9,21).date(),report_time='08:00:00'))
    with db.session() as s:
        result=db.purge_test_data(s)
        assert result['events']==1 and result['missing']==1 and result['visits']==1 and result['groups']==1
    with db.session() as s:
        assert not s.scalar(select(func.count()).select_from(Event))
        assert not s.scalar(select(func.count()).select_from(Missing))
        assert not s.scalar(select(func.count()).select_from(SilverVisit))
        assert not s.scalar(select(func.count()).select_from(Group))

def test_incremental_visit_refresh_quality_and_reconciliation():
    with db.session() as s:
        import_locations(s,DATA/'Location_All.xlsx')
        person={'employee':'9001','uid':100,'name':'Test SO','username':'test','region':'R1','dealer':'CA1','position':'SO'}
        s.add(Event(id=1,chat=-1004303041907,mid=101,uid=100,at=datetime(2026,9,19,8,15),kind='form',data={'text':'Dealer: CA1\nOutlet Name: Demo Shop\nPhone: 012345678\nOutlet Type: RT','person':person}))
        s.add(Event(id=2,chat=-1004303041907,mid=102,uid=100,at=datetime(2026,9,19,8,16),kind='pin',data={'reply':101,'lat':11.5,'lon':104.5,'person':person,'geo':{'province':'Kampong Speu','district':'Odongk Maechay','commune':'Veang Chas','commune_code':'050901','geo_issue':''},'route':{'kind':'Dealer to First Check-in','distance_km':2.5,'status':'OK'},'wrong_plan_reason':'Changed route','over_10km_reason':'Road detour'}))
        run_id,status,metrics,inserted,updated,unchanged,rejected=refresh_warehouse(s)
        assert status=='Succeeded' and inserted==1 and metrics['silver_visits']==1
    with db.session() as s:
        row=s.scalar(select(SilverVisit));assert row.outlet_name=='Demo Shop' and row.actual_commune_kh and row.open_map
        assert row.wrong_plan_reason=='Changed route' and row.over_10km_reason=='Road detour'
        assert all(x.status=='Passed' for x in s.scalars(select(Reconciliation).where(Reconciliation.run_id==run_id)))
        _,status,_,inserted,updated,unchanged,_=refresh_warehouse(s)
        assert status=='Succeeded' and inserted==0 and updated==0 and unchanged==1

def test_missing_pin_is_recorded_as_data_quality_error():
    with db.session() as s:
        import_locations(s,DATA/'Location_All.xlsx')
        s.add(Event(id=10,chat=-1004303041907,mid=110,uid=100,at=datetime(2026,9,19,9),kind='form',data={'text':'Dealer: CA1\nOutlet Name: No GPS','person':{'employee':'9001','name':'Test SO','region':'R1','dealer':'CA1','position':'SO'}}))
        run_id,status,metrics,*_=refresh_warehouse(s)
        issues=list(s.scalars(select(QualityIssue).where(QualityIssue.run_id==run_id)))
        assert status=='Succeeded' and any(x.rule_code=='DQ_GPS_REQUIRED' and x.severity=='Error' for x in issues)

def test_gold_power_bi_views_have_relationship_keys_and_business_fields():
    ddl='\n'.join(gold_view_statements()).lower()
    for view in ['gold_dim_date_v1','gold_dim_employee_v1','gold_dim_location_v1','gold_dim_dealer_v1','gold_dim_group_v1',
                 'gold_fact_missing_report_v1','gold_fact_user_al_v1','gold_fact_plan_v1','gold_fact_wrong_plan_v1','gold_fact_road_exception_v1']:
        assert f'create view {view}' in ddl
    assert 'create view gold_fact_so_visit_v1' not in ddl
    assert 'create view gold_fact_live_location_v1' in ddl
    assert 'create view gold_fact_live_location_history_v1' in ddl
    assert 'create view gold_fact_live_route_v1' in ddl
    assert 'create view gold_powerbi_relationship_manifest_v1' in ddl
    assert ddl.count("coalesce(p.geo->>'commune_code','') as location_key") >= 2
    assert "coalesce(l.geo->>'commune_code','') as location_key" in ddl
    # Every live-map view rolls over automatically at Cambodia midnight.
    assert ddl.count("e.at::date=(current_timestamp at time zone 'asia/phnom_penh')::date") == 3
    assert "(current_timestamp at time zone 'asia/phnom_penh')-l.pin_at" in ddl
    assert "(current_timestamp at time zone 'asia/phnom_penh')-p.pin_at" in ddl
    for marker in ['as point_key','as pin_time','as full_address','as path_id','as point_order']:
        assert marker in ddl
    for marker in ['as route_point_key','as user_color_key','as road_point_order','as is_checkin',
                   'as marker_size','as checkin_number','as checkin_label']:
        assert marker in ddl
    assert 'raw_user_point_count::numeric/150' in ddl
    # The live route intentionally includes Dealer -> First Check-in so the
    # dashboard can draw the complete journey from the dealer origin.
    assert "<>'dealer to first check-in'" not in ddl
    assert 'json_typeof' in ddl and 'json_array_length' in ddl and 'json_array_elements' in ddl
    assert 'jsonb_typeof' not in ddl and 'jsonb_array_length' not in ddl and 'jsonb_array_elements' not in ddl
    for marker in ["'week'||least(5",'m.start::date as date_miss','as time_miss_submit',
                   'coalesce(m.reason,\'\') as reason_miss','v.report_date as date_wrong_plan',
                   'as time_wrong_plan','coalesce(v.wrong_plan_reason,\'\') as wrong_plan_reason',
                   'report_date as date_over_10km','as time_over_10km',
                   'actual_province as province,actual_district as district,actual_commune as commune',
                   'actual_commune_code as location_key',
                   "coalesce(over_10km_reason,'') as over_10km_reason",
                   "||' to '||"]:
        assert marker in ddl
    assert "route_leg='previous check-in to current check-in' and road_distance_km>10" in ddl
    assert "where upper(trim(position)) in ('so','ss')" in ddl
    assert "where upper(trim(v.position)) in ('so','ss')" in ddl
    assert "lower(trim(v.group_name))<>'test_so'" in ddl
    assert "m.chat<>-1004303041907" in ddl
    dealer_sql=next(x.lower() for x in gold_view_statements() if 'create view gold_dim_dealer_v1' in x.lower())
    assert dealer_sql.count("('r") == 65
    assert "('r4','ppt3')" in dealer_sql
    assert "('r5','ppt3')" not in dealer_sql

def test_warehouse_resolves_legacy_manual_employee_to_current_master():
    with db.session() as s:
        s.add(Event(id=20,chat=-1004303041907,mid=120,uid=100,at=datetime(2026,9,19,10),kind='form',data={
            'text':'Dealer: CA1\nOutlet Name: Legacy','person':{'employee':'manual:100','name':'Old Name','region':'R1','dealer':'CA1','position':'SO'}}))
        refresh_warehouse(s)
    with db.session() as s:
        row=s.scalar(select(SilverVisit).where(SilverVisit.source_event_id==20))
        assert row.employee_id=='9001' and row.real_name=='Test SO'
