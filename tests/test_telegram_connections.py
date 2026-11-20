from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import pytest
from app import db
from app import telegram_connections as setup

@pytest.fixture
def client(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    for model in [db.Group,db.Roster,db.Event,db.State]:model.__table__.create(engine)
    monkeypatch.setattr(db,'Session',sessionmaker(bind=engine,expire_on_commit=False))
    monkeypatch.setattr(setup,'_telegram_chat',lambda chat:{'id':chat,'type':'supergroup','title':'Reporting group','is_forum':True,'_can_read_reports':True})
    monkeypatch.setattr(setup,'central_chat_id',lambda s:int(db.state(s,'central_alert_chat',{}).get('chat_id') or 0))
    app=FastAPI();app.include_router(setup.router)
    with TestClient(app) as client:yield client
    engine.dispose()

def test_setup_preserves_reports_and_survives_requests(client):
    assert client.get('/api/v1/admin/telegram').json()['groups']==[]
    result=client.put('/api/v1/admin/telegram/groups',json={'chat_id':-100123,'code':'R3-KKG3'})
    assert result.status_code==200
    saved=client.get('/api/v1/admin/telegram').json()
    assert saved['groups'][0]['topic_id'] is None
    assert saved['groups'][0]['code']=='R3-KKG3'
    assert saved['location_count']==0  # registration never invents visits
    client.put('/api/v1/admin/telegram/groups',json={'chat_id':-100123,'code':'R3-KKG3','topic_id':12})
    assert len(client.get('/api/v1/admin/telegram').json()['groups'])==1
    with db.session() as s:assert s.get(db.Group,-100123).topic==12

@pytest.mark.parametrize('body',[{'chat_id':100,'code':'R3-KKG3'},{'chat_id':-100,'code':'R1-KKG3'},{'chat_id':-100,'code':'R3-KKG3','topic_id':0}])
def test_invalid_group_rejected(client,body):
    assert client.put('/api/v1/admin/telegram/groups',json=body).status_code==422
    assert client.get('/api/v1/admin/telegram').json()['groups']==[]

def test_central_topics_are_persisted_and_old_forum_topics_not_reused(client):
    assert client.put('/api/v1/admin/telegram/central',json={'chat_id':-1001,'topics':{'banner_report':7,'wrong_plan':8}}).status_code==200
    with db.session() as s:assert db.state(s,'central_topic:banner_report')['topic_id']==7
    client.put('/api/v1/admin/telegram/central',json={'chat_id':-1002,'topics':{'banner_report':12}})
    with db.session() as s:
        assert db.state(s,'central_alert_chat')['chat_id']==-1002
        assert db.state(s,'central_topic:banner_report')['topic_id']==12
        assert db.state(s,'central_topic:wrong_plan')['topic_id']==0

def test_failed_telegram_validation_does_not_save(client,monkeypatch):
    def inaccessible(chat):raise setup.HTTPException(400,'Bot cannot access group')
    monkeypatch.setattr(setup,'_telegram_chat',inaccessible)
    assert client.put('/api/v1/admin/telegram/groups',json={'chat_id':-1001,'code':'R3-KKG3'}).status_code==400
    assert client.get('/api/v1/admin/telegram').json()['groups']==[]


def test_privacy_mode_prevents_false_connected_state(client,monkeypatch):
    monkeypatch.setattr(setup,'_telegram_chat',lambda chat:{'type':'supergroup','_can_read_reports':False})
    result=client.put('/api/v1/admin/telegram/groups',json={'chat_id':-1001,'code':'R3-KKG3'})
    assert result.status_code==400 and 'Privacy Mode' in result.json()['detail']
    assert client.get('/api/v1/admin/telegram').json()['groups']==[]

def test_bot_must_be_member_and_must_receive_noncommand_messages(monkeypatch):
    calls={'getChat':{'type':'supergroup'},'getMe':{'id':123,'can_read_all_group_messages':False},'getChatMember':{'status':'member'}}
    monkeypatch.setattr(setup,'_telegram_call',lambda method,payload:calls[method])
    assert setup._telegram_chat(-100)['_can_read_reports'] is False
    calls['getChatMember']={'status':'administrator'}
    assert setup._telegram_chat(-100)['_can_read_reports'] is True
    calls['getChatMember']={'status':'left'}
    with pytest.raises(setup.HTTPException):setup._telegram_chat(-100)
