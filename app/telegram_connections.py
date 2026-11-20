"""Persistent reporting-group and central-topic setup for new deployments."""
import json
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from app import db
from app.config import TOKEN
from app.db import Group, Roster, Event, put
from app.admin_api import current_admin
from app.alerts import TOPIC_CATEGORIES, central_chat_id, central_topic_id
from app.imports import DEALER_REGIONS

router=APIRouter(prefix="/api/v1/admin/telegram",tags=["Telegram connections"])

class ReportingConnection(BaseModel):
    chat_id:int=Field(lt=0)
    code:str
    topic_id:int|None=Field(default=None,gt=0)
    @field_validator('code')
    @classmethod
    def valid_scope(cls,value):
        value=value.strip().upper()
        region,sep,dealer=value.partition('-')
        if not sep or DEALER_REGIONS.get(dealer)!=region:
            raise ValueError('Choose a valid Region-Dealer code.')
        return value

class CentralConnection(BaseModel):
    chat_id:int=Field(lt=0)
    topics:dict[str,int]=Field(default_factory=dict,max_length=20)
    @field_validator('topics')
    @classmethod
    def valid_topics(cls,value):
        if any(k not in TOPIC_CATEGORIES or v<0 for k,v in value.items()):
            raise ValueError('Use known topic categories and nonnegative topic IDs.')
        return value

def _telegram_call(method,payload):
    if not TOKEN:raise HTTPException(503,'The hosted bot token is not configured.')
    request=URLRequest('https://api.telegram.org/bot'+TOKEN+'/'+method,
                       data=json.dumps(payload).encode(),
                       headers={'Content-Type':'application/json'})
    try:
        with urlopen(request,timeout=12) as response:result=json.load(response)
    except (HTTPError,URLError,TimeoutError,OSError,ValueError):
        raise HTTPException(400,'Telegram could not access this group. Check the Chat ID and add the bot to the group.') from None
    if not result.get('ok'):raise HTTPException(400,'Telegram could not validate the bot connection.')
    return result.get('result',{})

def _telegram_chat(chat_id):
    chat=_telegram_call('getChat',{'chat_id':chat_id})
    if chat.get('type') not in ('group','supergroup'):
        raise HTTPException(400,'Choose a Telegram group that this bot can access.')
    bot=_telegram_call('getMe',{})
    member=_telegram_call('getChatMember',{'chat_id':chat_id,'user_id':bot['id']})
    if member.get('status') in ('left','kicked'):
        raise HTTPException(400,'Add the bot to this Telegram group first.')
    chat['_can_send_reports']=member.get('status') in ('administrator','creator') or (member.get('can_send_messages',False) if member.get('status')=='restricted' else chat.get('permissions',{}).get('can_send_messages',True))
    chat['_can_read_reports']=member.get('status') in ('administrator','creator') or bool(bot.get('can_read_all_group_messages'))
    return chat

@router.get('')
def connections(_admin:str=Depends(current_admin)):
    with db.session() as s:
        groups=[{'chat_id':g.id,'title':g.title,'code':g.code,'topic_id':g.topic,'active':bool(g.active)}
                for g in s.scalars(select(Group).order_by(Group.code,Group.id))]
        topics={key:central_topic_id(s,key) for key in sorted(TOPIC_CATEGORIES)}
        return {'groups':groups,'central_chat_id':central_chat_id(s),'topics':topics,
                'roster_count':s.scalar(select(func.count()).select_from(Roster).where(Roster.active==1)),
                'location_count':s.scalar(select(func.count()).select_from(Event).where(Event.kind=='pin')),
                'scopes':sorted(f'{region}-{dealer}' for dealer,region in DEALER_REGIONS.items())}

@router.put('/groups')
def connect_group(body:ReportingConnection,admin:str=Depends(current_admin)):
    chat=_telegram_chat(body.chat_id)
    if not chat.get('_can_read_reports'):
        raise HTTPException(400,'Make the bot an administrator in this reporting group, or disable its Privacy Mode in BotFather, so it can receive outlet reports and locations.')
    with db.session() as s:
        g=s.get(Group,body.chat_id)
        if not g:
            g=Group(id=body.chat_id,test_uids=[]);s.add(g)
        g.title=chat.get('title','');g.code=body.code;g.topic=body.topic_id;g.active=1
        put(s,f'group_connection:{body.chat_id}',{'registered_by':admin,'registered_at':db.now().isoformat()})
    return {'saved':True,'chat_id':body.chat_id,'code':body.code}

@router.put('/central')
def connect_central(body:CentralConnection,admin:str=Depends(current_admin)):
    chat=_telegram_chat(body.chat_id)
    if chat.get('_can_send_reports') is False:
        raise HTTPException(400,'Allow the bot to send messages in the central alert group, or make it an administrator.')
    if any(body.topics.values()) and not chat.get('is_forum'):
        raise HTTPException(400,'Topic IDs require a Telegram forum group.')
    with db.session() as s:
        old_chat=central_chat_id(s)
        # Never reuse topic IDs belonging to a different forum.
        if old_chat!=body.chat_id:
            for key in TOPIC_CATEGORIES:put(s,f'central_topic:{key}',{'topic_id':0})
        stamp={'registered_by':admin,'registered_at':db.now().isoformat()}
        put(s,'central_alert_chat',{'chat_id':body.chat_id,'title':chat.get('title',''),**stamp})
        for key,topic in body.topics.items():put(s,f'central_topic:{key}',{'topic_id':topic,**stamp})
    return {'saved':True,'chat_id':body.chat_id}
