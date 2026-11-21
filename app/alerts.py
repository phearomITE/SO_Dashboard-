import asyncio
import logging
from app import db
from app.config import CENTRAL_ALERT_CHAT_ID,CENTRAL_ALERT_TOPIC_ID,CENTRAL_ALERT_TOPIC_IDS
from app.db import state,put
from app.bot_control import automation_enabled

log=logging.getLogger(__name__)

TOPIC_CATEGORIES={
    'morning_talk','wrong_plan','over_10km','user_al','distant_dealer',
    'user_in_wrong_group','new_user_join','missing_report','banner_report','parasol',
}

def central_chat_id(s):
    """Resolve the learned forum chat ID, falling back to .env."""
    runtime=state(s,'central_alert_chat',{})
    return int(runtime.get('chat_id') or CENTRAL_ALERT_CHAT_ID or 0)

def central_topic_id(s,category=None):
    """Resolve a registered runtime topic, then .env, then default topic."""
    category=str(category or '').strip().lower()
    if category in TOPIC_CATEGORIES:
        runtime=state(s,f'central_topic:{category}',{})
        if runtime.get('topic_id') is not None:return int(runtime['topic_id'] or 0)
        configured=int(CENTRAL_ALERT_TOPIC_IDS.get(category,0) or 0)
        if configured:return configured
    return 0 if category == 'parasol' else CENTRAL_ALERT_TOPIC_ID

async def send_central_alert(application,key,text,parse_mode=None,category=None):
    """Mirror an operational alert once to Group_SO_Controll."""
    unique=f'central_alert:{key}'
    with db.session() as s:
        if not automation_enabled(s):return None
        if state(s,unique,{}).get('sent'):return None
        chat_id=central_chat_id(s)
        if not chat_id:return None
        topic_id=central_topic_id(s,category)
    kwargs={'disable_web_page_preview':True}
    if parse_mode:kwargs['parse_mode']=parse_mode
    if topic_id:kwargs['message_thread_id']=topic_id
    try:
        message=await application.bot.send_message(chat_id,text,**kwargs)
        with db.session() as s:
            put(s,unique,{'sent':True,'at':db.now().isoformat(),'message_id':message.message_id,
                          'chat_id':chat_id,'category':category or 'default','topic_id':topic_id or 0})
        return message
    except Exception:
        log.exception('Central alert delivery failed chat=%s key=%s',chat_id,key)
        return None

async def send_central_document(application,key,document,filename,caption='',category=None):
    """Send one generated file to its registered central alert topic."""
    unique=f'central_alert:{key}'
    with db.session() as s:
        if not automation_enabled(s):return None
        if state(s,unique,{}).get('sent'):return None
        chat_id=central_chat_id(s)
        if not chat_id:return None
        topic_id=central_topic_id(s,category)
    kwargs={'document':document,'filename':filename,'caption':caption}
    if topic_id:kwargs['message_thread_id']=topic_id
    try:
        message=await application.bot.send_document(chat_id=chat_id,**kwargs)
        with db.session() as s:
            put(s,unique,{'sent':True,'at':db.now().isoformat(),
                          'message_id':message.message_id,'chat_id':chat_id,
                          'category':category or 'default','topic_id':topic_id or 0,
                          'filename':filename})
        return message
    except Exception:
        log.exception('Central document delivery failed chat=%s key=%s file=%s',chat_id,key,filename)
        return None

async def copy_central_messages(application,key,source_chat_id,message_ids,category):
    """Serialize banner forwards so concurrent duplicate updates cannot race."""
    if category in {'banner_report', 'parasol'}:
        lock=application.bot_data.setdefault('banner_forward_lock',asyncio.Lock())
        async with lock:
            return await _copy_central_messages(application,key,source_chat_id,message_ids,category)
    return await _copy_central_messages(application,key,source_chat_id,message_ids,category)

async def _copy_central_messages(application,key,source_chat_id,message_ids,category):
    """Forward original Telegram messages to a central topic exactly once.

    True forwarding preserves Telegram's ``Forwarded from`` attribution, the
    complete caption, photo order, and media-group structure. No extra heading
    or sender-information message is created, and no report text is parsed,
    shortened, regenerated, copied, or re-uploaded here.
    """
    unique=f'central_alert:{key}'
    message_ids=sorted({int(mid) for mid in message_ids if mid})
    if not message_ids:return None
    with db.session() as s:
        if not automation_enabled(s):return None
        if state(s,unique,{}).get('sent'):return None
        chat_id=central_chat_id(s)
        if not chat_id:return None
        topic_id=central_topic_id(s,category)
    if category == 'parasol' and not topic_id:
        log.warning('Parasol forwarding skipped: no Parasol topic is configured')
        return None
    kwargs={}
    if topic_id:kwargs['message_thread_id']=topic_id
    try:
        delivered=await application.bot.forward_messages(
            chat_id=chat_id,from_chat_id=int(source_chat_id),
            message_ids=message_ids,**kwargs)
        delivered_ids=[getattr(item,'message_id',None) for item in delivered]
        if len(delivered_ids) != len(message_ids):
            log.warning('Telegram forwarded %s of %s messages for %s', len(delivered_ids), len(message_ids), key)
            if not delivered_ids:
                return None
        with db.session() as s:
            put(s,unique,{
                'sent':True,'at':db.now().isoformat(),'message_ids':delivered_ids,
                'source_chat_id':int(source_chat_id),'source_message_ids':message_ids,
                'chat_id':chat_id,'category':category,'topic_id':topic_id or 0,
                'delivery_mode':'forwarded',
            })
        return delivered
    except Exception:
        log.exception('Central message forward failed chat=%s key=%s source=%s messages=%s',
                      chat_id,key,source_chat_id,message_ids)
        return None
