import asyncio,logging,re,tempfile,os,shutil,random,time
from pathlib import Path
from datetime import datetime,timedelta
from sqlalchemy import select,delete
from telegram import Update,ReplyKeyboardMarkup,KeyboardButton
from telegram.ext import Application,MessageHandler,ChatMemberHandler,filters
from app import db
from app.config import (ADMINS,TOKEN,TZ,DATA,TEST_MODE,WAREHOUSE_ENABLED,
                        WAREHOUSE_INTERVAL_MINUTES,WAREHOUSE_INITIAL_DELAY_SECONDS,ALERT_INTERVAL_MINUTES,
                        CENTRAL_ALERT_CHAT_ID,AUTO_RECONCILE_DEALER)
from app.db import Roster,Group,Controller,Event,Missing,Leave,state,put
from app.reason_analysis import analyze_reason, fallback_reason
from app.rules import (person,is_so,is_form,is_morning_talk,windows,normalize_position,
                       morning_talk_date,morning_talk_full_day_leave_entries,
                       normalized_person_name,normalized_employee_id)
from app.imports import import_roster,import_plan,import_dealer_locations,excluded_position,DEALER_REGIONS
from app.exports import export_data,workbook,export_current_plan,al_summary_workbook
from app.service import members,enabled,check_window,tick
from app.ocr import scan
from app.geo import coordinates_from_text,is_cambodia_coordinate,locate,reverse_online
from app.tracking import analyze_pin_event
from app.warehouse import import_locations,refresh_warehouse,latest_status
from app.concurrency import chat_lock
from app.alerts import (send_central_alert,send_central_document,
                        copy_central_messages,TOPIC_CATEGORIES)
from app.question_settings import get_question_settings,question_enabled,update_question_settings,status_text
from app.bot_control import automation_enabled,get_bot_control,set_automation,set_temporary_break,bot_status_text

log=logging.getLogger(__name__)

REASON_REPLY_MESSAGES=[
    'សូមអរគុណច្រើនចំពោះការឆ្លើយតបបង🙏♥️',
    'សូមអរគុណបងដែលបានជួយបញ្ជាក់មូលហេតុ🙏',
    'ទទួលបានមូលហេតុរបស់បងហើយ សូមអរគុណ🙏♥️',
    'អរគុណបងសម្រាប់ការឆ្លើយតប និងការបញ្ជាក់ព័ត៌មាន🙏',
    'បានទទួលចម្លើយរបស់បងហើយ អរគុណច្រើនបង🙏',
]

async def thank_for_reason(message):
    """Confirm that a user's reason was accepted and stored."""
    with db.session() as s:
        if not automation_enabled(s):return
        if not question_enabled(s,'reason_acknowledgement'):return
    try:
        await message.reply_text(random.choice(REASON_REPLY_MESSAGES))
    except Exception:
        # A temporary Telegram delivery error must not roll back the reason.
        log.exception('Reason acknowledgement delivery failed chat=%s',getattr(message,'chat_id',None))

def _warehouse_refresh_once():
    """Run one complete warehouse transaction in a worker thread."""
    with db.session() as s:return refresh_warehouse(s)

async def auto_refresh_warehouse(application,trigger):
    """Serialize automatic ETL runs so 65 active groups cannot overlap them."""
    if not WAREHOUSE_ENABLED:return None
    lock=application.bot_data.setdefault('warehouse_refresh_lock',asyncio.Lock())
    async with lock:
        try:
            result=await asyncio.to_thread(_warehouse_refresh_once)
            log.info('Automatic warehouse refresh trigger=%s run=%s status=%s',trigger,result[0],result[1])
            return result
        except Exception:
            # The operational command has already committed successfully. ETL
            # failure must be visible in logs but must not roll back the upload.
            log.exception('Automatic warehouse refresh failed trigger=%s',trigger)
            return None

def warehouse_caption(result):
    if not result:return 'Power BI warehouse refresh: failed; use /warehouse_status and check logs/bot.log.'
    run_id,status,metrics,*_=result
    return f'Power BI warehouse refresh #{run_id}: {status}. Silver visits: {metrics["silver_visits"]}.'

def schedule_warehouse_refresh(application,trigger):
    """Queue ETL without blocking an operational Telegram message."""
    create_task=getattr(application,'create_task',None)
    if WAREHOUSE_ENABLED and callable(create_task):
        create_task(auto_refresh_warehouse(application,trigger))
ALL_COMMANDS={x:x for x in ['start','myid','chatid','export','export_so_list','remove','restore','al','al_summary','al_morning','al_afternoon','add','change','change_dealer_from','pin','status','alert_now','reset_today','register_topic','register_alert_topic','alert_topics','setup_group','sync_so_list','update_list_so','update_plan','update_location','update_location_master','update_dealer_master','warehouse_refresh','warehouse_status','summary_by_so','export_raw_data','currect_plan','set_dealer_location','dealer_location','add_dealer_location','update_dealer_location','change_alert_interval','change_alert_interval_minutes_15','change_alert_interval_minutes_20','change_alert_interval_minutes_30','alert_interval','questions','stop_bot','start_bot','bot_status','break_bot_1h','break_bot_2h','group_not_morning','group_not_morning_talk','stop_ask','start_ask','ask_status']}
HELP='''SO Alert Bot
/myid • /chatid • /status • /pin
/setup_group | R1-CA2 | TEST_USER_ID1,TEST_USER_ID2
/register_topic  (run inside the reporting topic)
/register_alert_topic | CATEGORY  (run inside each Group_SO_Controll topic)
/alert_topics  (show registered central category topics)
/add controller | TELEGRAM_ID | R1-CA1,R1-CA2
/remove controller | TELEGRAM_ID
/add | TELEGRAM_ID | Employee ID | Real Name | R1 | CA2 | SO
/remove | TELEGRAM_ID
/restore | TELEGRAM_ID
/al [TELEGRAM_ID] [YYYY-MM-DD] (self-service full day)
/al | 4day (self-service 4 calendar days from today)
/al | TELEGRAM_ID | 5day (controller/admin multi-day leave)
/al | TELEGRAM_ID | YYYY-MM-DD | 5day (multi-day leave from date)
/al_morning [TELEGRAM_ID] [YYYY-MM-DD] (self-service morning)
/al_afternoon [TELEGRAM_ID] [YYYY-MM-DD] (self-service afternoon)
/AL_Summary_YYYY-MM-DD (send all-user AL summary workbook to User_AL topic)
/change | TELEGRAM_ID | name | New Name
/change | TELEGRAM_ID | dealer | CA2
/change | TELEGRAM_ID | region | R1
/change | TELEGRAM_ID | position | SO
/change_dealer_from | TELEGRAM_ID | Real Name | R1 | CA2 | CA6
Compact form: /change_dealer_from_TELEGRAM_ID | Real Name | R1 | CA2 | _to_dealer_CA6
/add_dealer_location | R1-CA2 | TELEGRAM_ID | Employee ID | Real Name | 11.563299,104.912002
/update_dealer_location | R1-CA2 | TELEGRAM_ID | Employee ID | Real Name | 11.563209,104.912004
/set_dealer_location | R1-CA2 | LAT,LON
/dealer_location | R1-CA2
/update_plan then reply to the prompt with an Excel document
/update_list_so then reply to the prompt with an Excel document
/update_location then reply to the prompt with Location_All.xlsx
/update_location_master (alias of /update_location)
/update_dealer_master then reply with DEALERS STATION All Location.csv
/warehouse_refresh (run incremental Bronze–Silver–Gold processing)
/warehouse_status (show ETL, quality and reconciliation totals)
/change_ALERT_INTERVAL_MINUTES_15
/change_ALERT_INTERVAL_MINUTES_20
/change_ALERT_INTERVAL_MINUTES_30
/change_alert_interval | MINUTES (5-60, step 5)
/alert_interval (show current interval)
/questions | off_Wrong Plan
/questions | on_Wrong Plan
/questions | off_Over 10Km
/questions | on_Over 10Km
/questions | off_Reason Acknowledgement
/questions | on_Reason Acknowledgement
/questions | off  (turn off the three optional responses)
/questions | on   (turn on the three optional responses)
/questions | status
/stop_bot (pause all automatic alerts, questions and acknowledgements)
/start_bot (resume automatic bot behaviour)
/bot_status (show whether automation is running or stopped)
/break_bot_1h (pause automatic bot behaviour for 1 hour)
/break_bot_2h (pause automatic bot behaviour for 2 hours)
/group_not_Morning Talk (list groups missing today's Morning Talk)
/group_not_morning_talk (compact alias)
/stop_ask (stop only missing-report follow-up questions)
/start_ask (resume missing-report follow-up questions)
/ask_status (show missing-report follow-up question status)
/register_alert_topic | Banner Report  (run inside the Banner_Report topic)
/sync_so_list (reload bundled List_SO.xlsx)
/export [YYYY-MM-DD]
/export_raw_data [YYYY-MM-DD]
/summary_by_so [YYYY-MM-DD]
/export_so_list
/currect_plan (export the latest successfully uploaded plan)
/alert_now (check latest completed window)
/reset_today (clear today's missing alerts for this group)
Global admins manage setup, controller access, plan, roster, and resets.
Controllers manage only their assigned groups. Send text reasons as replies to the alert or follow-up question.'''

def parse(text):
    dated_summary=re.fullmatch(
        r'\s*/al_summary_(\d{4}-\d{2}-\d{2})(?:@\w+)?\s*',str(text or ''),re.I)
    if dated_summary:return 'al_summary',[dated_summary.group(1)]
    m=re.match(r'^\s*/([\w]+)(?:@\w+)?(.*)$',text,re.S)
    if not m:return '',[]
    cmd=m[1].lower();payload=m[2].strip()
    # Compact transfer command example:
    # /change_dealer_from_7166364893 | Ra Phearom | R1 | CA2 | _to_dealer_CA6
    compact=re.fullmatch(r'change_dealer_from_(\d+)',cmd)
    compact_uid=compact.group(1) if compact else None
    if compact:cmd='change_dealer_from'
    if cmd in ('currect_plan_w1_w2_sep_26','currect_plan_w1_w2'):
        if cmd.endswith('sep_26') or payload.lower()=='-sep-26':cmd='currect_plan';payload=''
    parts=[x.strip() for x in payload.split('|') if x.strip()] if '|' in payload else payload.split()
    if compact_uid:parts.insert(0,compact_uid)
    return cmd,parts

def dealer_transfer_target(value,current_region):
    """Normalize CA6, R1-CA6, or _to_dealer_CA6 to a validated pair."""
    raw=str(value or '').strip().upper()
    raw=re.sub(r'^_?TO_DEALER_','',raw)
    if '-' in raw:
        target_region,target_dealer=raw.split('-',1)
    else:
        target_dealer=raw
        target_region=DEALER_REGIONS.get(target_dealer,current_region)
    code=valid_code(f'{target_region}-{target_dealer}')
    expected_region=DEALER_REGIONS.get(target_dealer)
    if expected_region and expected_region!=target_region:
        raise ValueError(f'{target_dealer} belongs to {expected_region}, not {target_region}.')
    return code.split('-',1)

def can_manage(s,uid,g):
    return uid in ADMINS or bool(g and s.scalar(select(Controller.id).where(Controller.uid==uid,Controller.code==g.code)))
def admin(uid):
    if uid not in ADMINS:raise ValueError('Only a global bot-management admin may run this command.')

def member_is_present(chat_member):
    """True when a ChatMember state means the user is currently in the chat."""
    status=getattr(chat_member,'status','')
    return status in ('member','administrator','creator','owner') or (status=='restricted' and bool(getattr(chat_member,'is_member',False)))

async def notify_global_admins_new_member(application,chat,new_user,actor,at):
    """Notify every global admin once when a user joins or is added to a group."""
    if not chat or getattr(chat,'type','') not in ('group','supergroup') or not new_user:return
    with db.session() as s:
        if not automation_enabled(s):return
    at=at.astimezone(TZ).replace(tzinfo=None) if getattr(at,'tzinfo',None) else at
    actor_id=getattr(actor,'id',None)
    joined_self=actor_id==new_user.id
    action='Joined by themselves' if joined_self else 'Added by a group member'
    actor_name=getattr(actor,'full_name','Unknown') if actor else 'Unknown'
    actor_username=f'@{actor.username}' if actor and getattr(actor,'username',None) else 'None'
    username=f'@{new_user.username}' if getattr(new_user,'username',None) else 'None'
    kind='Bot' if getattr(new_user,'is_bot',False) else 'User'
    text=(f'🚨 New {kind} in Telegram Group\n'
          f'Group: {getattr(chat,"title",None) or "Unknown"}\n'
          f'Group ID: {chat.id}\n'
          f'New User: {new_user.full_name}\n'
          f'Telegram ID: {new_user.id}\n'
          f'Username: {username}\n'
          f'Action: {action}\n'
          f'Added/Joined By: {actor_name}\n'
          f'Actor ID: {actor_id or "Unknown"}\n'
          f'Actor Username: {actor_username}\n'
          f'Time: {at:%d/%m/%Y %I:%M %p}')
    for admin_id in sorted(ADMINS):
        key=f'new_member_alert:{chat.id}:{new_user.id}:{admin_id}'
        with db.session() as s:
            previous=state(s,key,{})
            try:last=datetime.fromisoformat(previous.get('at',''))
            except (TypeError,ValueError):last=None
            # Telegram can emit both a service message and chat_member update.
            if last and abs((at-last).total_seconds())<120:continue
        try:
            await application.bot.send_message(admin_id,text)
            with db.session() as s:put(s,key,{'at':at.isoformat()})
        except Exception:
            log.exception('New-member admin alert failed admin=%s chat=%s user=%s',admin_id,chat.id,new_user.id)
    await send_central_alert(application,f'new_member:{chat.id}:{new_user.id}:{at:%Y%m%d%H%M}',
                             text,category='new_user_join')

async def chat_member_update(update,context):
    change=update.chat_member
    if not change:return
    old_present=member_is_present(change.old_chat_member)
    new_present=member_is_present(change.new_chat_member)
    if old_present or not new_present:return
    await notify_global_admins_new_member(context.application,update.effective_chat,change.new_chat_member.user,update.effective_user,change.date)
def valid_code(c):
    c=c.upper().strip()
    if not re.fullmatch(r'R[1-8]-[A-Z0-9]+',c):raise ValueError('Group must be Region-Dealer, for example R1-CA2.')
    return c

def alert_topic_category(value):
    aliases={
        'morning talk':'morning_talk','morning_talk':'morning_talk',
        'wrong plan':'wrong_plan','wrong_plan':'wrong_plan',
        'over 10km':'over_10km','over_10km':'over_10km','over10km':'over_10km',
        'user al':'user_al','user_al':'user_al','al':'user_al',
        'distant dealer':'distant_dealer','distant_dealer':'distant_dealer',
        'distand dealer':'distant_dealer','distand_dealer':'distant_dealer',
        'user in wrong group':'user_in_wrong_group','user_in_wrong_group':'user_in_wrong_group',
        'new user join':'new_user_join','new_user_join':'new_user_join',
        'missing report':'missing_report','missing_report':'missing_report',
        'banner report':'banner_report','banner_report':'banner_report','banner':'banner_report',
    }
    key=' '.join(str(value or '').strip().casefold().replace('-',' ').split())
    key=key.replace('_',' ')
    category=aliases.get(key)
    if not category or category not in TOPIC_CATEGORIES:
        raise ValueError('Unknown category. Use Morning Talk, Wrong Plan, Over 10Km, User AL, Distant Dealer, User In Wrong Group, New User Join, Missing Report, or Banner Report.')
    return category

def banner_type_value(text):
    """Return a non-empty Banner Type value without OCR or AI inference."""
    # Keep whitespace horizontal: ``\s`` crosses newlines and can mistake the
    # next caption field (often ``PROGRAM:``) for a Banner Type value.
    match=re.search(r'(?im)^[ \t]*\*?[ \t]*banner[ \t]*type[ \t]*[:：][ \t]*([^\r\n]*)',
                    str(text or '').replace('\u200b',''))
    if not match:return ''
    value=match.group(1).strip().strip('.,;|-_* ')
    compact=re.sub(r'[\s._-]+','',value).casefold()
    if compact in {'','0','no','none','na','n/a','nil'}:return ''
    return value

async def forward_banner_album(application,album_key):
    """Wait for all Telegram updates in an album, then copy it exactly once."""
    await asyncio.sleep(1.5)
    albums=application.bot_data.setdefault('banner_albums',{})
    album=albums.pop(album_key,None)
    if not album:return
    if not banner_type_value(album.get('text','')):return
    await copy_central_messages(
        application,
        f'banner_report:{album["chat_id"]}:{album["media_group_id"]}',
        album['chat_id'],album['message_ids'],'banner_report')

def queue_banner_forward(application,msg,text):
    """Queue an album or copy a single qualifying report/photo message."""
    media_group_id=getattr(msg,'media_group_id',None)
    if media_group_id:
        key=(int(msg.chat_id),str(media_group_id))
        albums=application.bot_data.setdefault('banner_albums',{})
        album=albums.setdefault(key,{
            'chat_id':int(msg.chat_id),'media_group_id':str(media_group_id),
            'message_ids':[],'text':'','task_started':False,
        })
        album['message_ids'].append(int(msg.message_id))
        if text:album['text']=text
        if not album['task_started']:
            album['task_started']=True
            application.create_task(forward_banner_album(application,key))
        return
    if banner_type_value(text) and (msg.photo or
            (msg.document and (msg.document.mime_type or '').startswith('image/'))):
        application.create_task(copy_central_messages(
            application,f'banner_report:{msg.chat_id}:{msg.message_id}',
            msg.chat_id,[msg.message_id],'banner_report'))

def is_central_alert_chat(chat):
    """Accept the configured ID or the exact central-group title after migration."""
    if int(getattr(chat,'id',0) or 0)==CENTRAL_ALERT_CHAT_ID:return True
    title=re.sub(r'[^a-z0-9]+','',str(getattr(chat,'title','') or '').casefold())
    return title=='groupsocontroll'

def resolve_member(s,g,uid):
    candidates=list(s.scalars(select(Roster).where(Roster.uid==int(uid),Roster.active==1)))
    candidates.sort(key=lambda row:(row.source!='excel',str(row.employee or '').startswith('manual:')))
    r=candidates[0] if candidates else None
    if not r:raise ValueError('Telegram ID is not in List_SO. Use /add first.')
    if g and ((g.test_uids and r.uid not in g.test_uids) or (not g.test_uids and f'{r.region}-{r.dealer}'.upper()!=g.code)):
        raise ValueError('User is outside this registered group scope.')
    return r

def leave_duration(value):
    """Parse 4day/4days/4 day as a safe, bounded calendar-day count."""
    match=re.fullmatch(r'\s*(\d{1,3})\s*days?\s*',str(value or ''),re.I)
    if not match:return None
    days=int(match.group(1))
    if not 1<=days<=365:raise ValueError('Leave duration must be between 1day and 365days.')
    return days

def is_self_full_day_leave(text):
    """Recognize an SO/SS explicitly declaring their own one-day leave.

    Keep this deliberately strict so an AL token inside a Morning Talk roster,
    outlet form, or ordinary sentence cannot pause the sender accidentally.
    """
    value=str(text or '').replace('\u200b','').replace('\ufeff','').strip()
    value=re.sub(r'^[\s*_.:;,-]+|[\s*_.:;,-]+$','',value)
    if re.fullmatch(r'\(?\s*al\s*\)?',value,re.I):return True
    compact=re.sub(r'\s+','',value)
    return compact in {'ច្បាប់មួយថ្ងៃ','ឈប់មួយថ្ងៃ','សុំច្បាប់មួយថ្ងៃ'}

def leave_groups(s,r,current_group=None):
    """Return every registered group in which this roster user is monitored."""
    result=[]
    for group in s.scalars(select(Group).where(Group.active==1)):
        if group.test_uids:
            belongs=r.uid in group.test_uids
        else:
            belongs=f'{r.region}-{r.dealer}'.upper()==str(group.code or '').upper()
        if belongs:result.append(group)
    if current_group and current_group not in result:result.append(current_group)
    return result

def save_leave(s,r,start,end,groups,source='command',source_message_id=None,recorded_by=None):
    for group in groups:
        put(s,f'leave:{group.id}:{r.uid}',{
            'start':start.isoformat(),'end':end.isoformat(),'source':source,
            'source_message_id':source_message_id,'recorded_by':recorded_by,
            'recorded_at':db.now().isoformat(),
        })
        row=s.scalar(select(Leave).where(
            Leave.chat==group.id,Leave.uid==r.uid,Leave.start==start,Leave.end==end))
        if row is None:
            row=Leave(chat=group.id,uid=r.uid,employee=r.employee,name=r.name,
                      region=r.region,dealer=r.dealer,position=normalize_position(r.position),
                      start=start,end=end,source=source,
                      source_message_id=source_message_id,recorded_by=recorded_by,
                      recorded_at=db.now())
            s.add(row)
        else:
            row.employee=r.employee;row.name=r.name;row.region=r.region;row.dealer=r.dealer
            row.position=normalize_position(r.position);row.source=source
            row.source_message_id=source_message_id;row.recorded_by=recorded_by
            row.recorded_at=db.now()
        # AL is not a missing report. Remove any same-period rows so BI totals
        # and delayed follow-up questions cannot continue counting this user.
        s.execute(delete(Missing).where(
            Missing.chat==group.id,Missing.uid==r.uid,
            Missing.start<end,Missing.end>start,
        ))

def leave_alert_text(r,start,end,groups,source,recorded_by=None):
    period=('Full day' if start.time()==datetime.min.time() and
            end.time()==datetime.min.time() else
            ('Morning' if end.hour==11 else 'Afternoon'))
    return (f'🔕 User AL Recorded\nDate: {start:%d/%m/%Y}\nPeriod: {period}\n'
            f'Real Name: {r.name}\nTelegram ID: {r.uid}\nEmployee ID: {r.employee}\n'
            f'Position: {normalize_position(r.position)}\n'
            f'Group: {", ".join(sorted({g.code for g in groups}))}\n'
            f'Source: {source}\nRecorded By: {recorded_by or r.uid}')

async def command(update,context):
    msg=update.effective_message;uid=update.effective_user.id;chat=update.effective_chat
    cmd,args=parse(msg.text or '')
    if cmd not in ALL_COMMANDS:
        await msg.reply_text('Unknown command. Send /start for the command list.');return
    if cmd=='start':await msg.reply_text(HELP);return
    if cmd=='myid':await msg.reply_text(f'Telegram ID: {uid}');return
    if cmd=='chatid':await msg.reply_text(f'Chat ID: {chat.id}\nTopic ID: {msg.message_thread_id or "None"}');return
    if cmd=='pin':
        if chat.type=='private':await msg.reply_text('Send your location in the registered reporting group using Telegram Attach → Location.');return
        await msg.reply_text('សូមបងផ្ញើទីតាំង GPS ដោយប្រើ Attach → Location ហើយ Reply ទៅ Report របស់បង🙏');return
    with db.session() as s:
        g=s.get(Group,chat.id)
        if cmd in ('register_alert_topic','alert_topics'):
            admin(uid)
            if cmd=='register_alert_topic':
                if not is_central_alert_chat(chat):
                    raise ValueError('Run this command inside Group_SO_Controll.')
                if len(args)!=1:
                    raise ValueError('Use /register_alert_topic | Morning Talk')
                category=alert_topic_category(args[0])
                topic_id=int(msg.message_thread_id or 0)
                put(s,'central_alert_chat',{
                    'chat_id':int(chat.id),'title':chat.title or 'Group_SO_Controll',
                    'registered_by':uid,'registered_at':db.now().isoformat(),
                })
                put(s,f'central_topic:{category}',{
                    'topic_id':topic_id,'title':args[0].strip(),'registered_by':uid,
                    'registered_at':db.now().isoformat(),
                })
                await msg.reply_text(f'✅ Central alert topic registered\nCategory: {category}\nChat ID: {chat.id}\nTopic ID: {topic_id or "General"}')
                return
            labels=[
                ('morning_talk','Morning Talk'),('wrong_plan','Wrong Plan'),
                ('over_10km','Over 10Km'),('user_al','User AL'),
                ('distant_dealer','Distant Dealer'),
                ('user_in_wrong_group','User In Wrong Group'),
                ('new_user_join','New User Join'),('missing_report','Missing Report'),
                ('banner_report','Banner Report'),
            ]
            central=state(s,'central_alert_chat',{})
            lines=['📌 Central alert topic routing',
                   f'Central Chat ID: {central.get("chat_id",CENTRAL_ALERT_CHAT_ID or "Not registered")}']
            for category,label in labels:
                value=state(s,f'central_topic:{category}',{})
                lines.append(f'{label}: {value.get("topic_id","Not registered")}')
            await msg.reply_text('\n'.join(lines));return
        if cmd in ('stop_bot','start_bot','bot_status','break_bot_1h','break_bot_2h'):
            admin(uid)
            if cmd=='bot_status':
                value=get_bot_control(s)
            elif cmd.startswith('break_bot_'):
                now=db.now();hours=1 if cmd=='break_bot_1h' else 2
                value=set_temporary_break(s,hours,uid,now)
                # Never replay reporting windows that elapsed during the break.
                resume_at=now+timedelta(hours=hours)
                context.application.bot_data['started']=resume_at
                context.application.bot_data['alert_interval_changed_at']=resume_at
            else:
                now=db.now();enabled_now=cmd=='start_bot'
                value=set_automation(s,enabled_now,uid,now)
                if enabled_now:
                    # Resume from now and never alert windows that elapsed
                    # while automatic bot behaviour was paused.
                    context.application.bot_data['started']=now
                    context.application.bot_data['alert_interval_changed_at']=now
            await msg.reply_text(bot_status_text(value));return
        if cmd in ('group_not_morning','group_not_morning_talk'):
            admin(uid)
            today=db.now().date()
            groups=sorted(s.scalars(select(Group).where(Group.active==1)),
                          key=lambda row:(str(row.code or ''),row.id))
            received=[];missing=[]
            for group in groups:
                receipt=state(s,f'morning_talk:{group.id}:{today.isoformat()}',{})
                (received if receipt.get('received') else missing).append(group)
            lines=[
                '📋 Morning Talk Submission Status',
                f'Date: {today:%d/%m/%Y}',
                f'Received: {len(received)}/{len(groups)} groups',
                f'Not Submitted: {len(missing)} groups',
            ]
            if missing:
                current_region=None
                for group in missing:
                    code=str(group.code or 'UNASSIGNED').upper()
                    region=code.split('-',1)[0] if '-' in code else 'OTHER'
                    if region!=current_region:
                        lines.extend(['',f'{region}:'])
                        current_region=region
                    lines.append(f'• {code} — {group.title or "Unnamed group"}')
            else:
                lines.extend(['','✅ All registered groups submitted Morning Talk today.'])
            await msg.reply_text('\n'.join(lines));return
        if cmd in ('stop_ask','start_ask','ask_status'):
            admin(uid)
            if cmd=='ask_status':
                values=get_question_settings(s)
            else:
                values=update_question_settings(
                    s,{'missing_report_followup':cmd=='start_ask'},uid
                )
            status='ON' if values.get('missing_report_followup',True) else 'OFF'
            await msg.reply_text(
                f'Missing-report follow-up questions: {status}\n'
                'Missing-report alerts, commands, data collection, warehouse and Power BI remain working.'
            );return
        if cmd=='questions':
            admin(uid)
            action=' '.join(args).strip().casefold().replace('_',' ')
            action=re.sub(r'\s+',' ',action)
            aliases={
                'off wrong plan':{'wrong_plan':False},
                'on wrong plan':{'wrong_plan':True},
                'off over 10km':{'over_10km':False},
                'off over 10 km':{'over_10km':False},
                'on over 10km':{'over_10km':True},
                'on over 10 km':{'over_10km':True},
                'off reason acknowledgement':{'reason_acknowledgement':False},
                'on reason acknowledgement':{'reason_acknowledgement':True},
                'off':{'wrong_plan':False,'over_10km':False,'reason_acknowledgement':False},
                'on':{'wrong_plan':True,'over_10km':True,'reason_acknowledgement':True},
            }
            if not action:
                raise ValueError('Use /questions | status, on, off, or a specific question type.')
            if action=='status':
                values=get_question_settings(s)
            elif action in aliases:
                values=update_question_settings(s,aliases[action],uid)
            else:
                raise ValueError(
                    'Use /questions | off_Wrong Plan, off_Over 10Km, '
                    'off_Reason Acknowledgement, on, off, or status.'
                )
            group_count=len(list(s.scalars(select(Group).where(Group.active==1))))
            await msg.reply_text(status_text(values,group_count));return
        if cmd=='alert_interval':
            current=int(state(s,'alert_interval_minutes',{}).get('minutes',ALERT_INTERVAL_MINUTES))
            await msg.reply_text(f'Current missing-report alert interval: {current} minutes.');return
        if cmd.startswith('change_alert_interval'):
            admin(uid)
            aliases={'change_alert_interval_minutes_15':15,'change_alert_interval_minutes_20':20,'change_alert_interval_minutes_30':30}
            if cmd in aliases:minutes=aliases[cmd]
            else:
                if not args:raise ValueError('Use /change_alert_interval | 15')
                minutes=int(args[0])
            if minutes<5 or minutes>60 or minutes%5:raise ValueError('Alert interval must be 5-60 minutes in steps of 5.')
            changed_at=db.now();put(s,'alert_interval_minutes',{'minutes':minutes,'changed_by':uid,'changed_at':changed_at.isoformat()})
            context.application.bot_data['alert_interval_changed_at']=changed_at
            await msg.reply_text(f'✅ Missing-report alert interval changed to {minutes} minutes. New schedule starts now; old windows will not be alerted retroactively.');return
        if cmd=='setup_group':
            admin(uid)
            if chat.type=='private':raise ValueError('Run /setup_group inside Test_SO or the reporting group.')
            if not args:
                matches=set(re.findall(r'(?<![A-Z0-9])(R[1-8])\s*[-_]\s*([A-Z0-9]+)(?![A-Z0-9])',str(chat.title or '').upper()))
                codes=[f'{region}-{dealer}' for region,dealer in matches if DEALER_REGIONS.get(dealer)==region]
                if len(codes)!=1:raise ValueError('Use /setup_group | R1-CA2 inside the reporting group. Its Region-Dealer code could not be inferred uniquely.')
                args=[codes[0]]
            c=valid_code(args[0]);test_ids=[int(x.strip()) for x in args[1].split(',') if x.strip()] if len(args)>1 else []
            if TEST_MODE and not test_ids:raise ValueError('Test mode requires explicit test Telegram IDs after the group code.')
            for tid in test_ids:
                if not s.scalar(select(Roster.id).where(Roster.uid==tid)):raise ValueError(f'{tid} is not in roster. Use /add first.')
            if not g:g=Group(id=chat.id);s.add(g)
            g.title=chat.title;g.code=c;g.test_uids=test_ids;g.active=1;g.topic=msg.message_thread_id
            await msg.reply_text(f'Registered {chat.title}: {c}. Test users: {test_ids or "all SO in group scope"}. Alerts enabled: {bool(enabled(g))}.');return
        if cmd in ('add','remove') and args and args[0].lower()=='controller':
            admin(uid)
            if len(args)<2:raise ValueError(f'Use /{cmd} controller | TELEGRAM_ID |'+(' R1-CA1,R1-CA2' if cmd=='add' else ''))
            tid=int(args[1])
            if tid<=0:raise ValueError('Telegram ID must be positive.')
            if cmd=='remove':
                n=s.execute(delete(Controller).where(Controller.uid==tid)).rowcount
                await msg.reply_text(f'Removed {n} controller group assignment(s) for {tid}. Global admin access is configured separately.');return
            if len(args)!=3:raise ValueError('Use /add controller | TELEGRAM_ID | R1-CA1,R1-CA2')
            codes=[valid_code(x) for x in args[2].split(',')]
            r=s.scalar(select(Roster).where(Roster.uid==tid));profile=state(s,f'profile:{tid}')
            for c in codes:
                existing=s.scalar(select(Controller).where(Controller.uid==tid,Controller.code==c))
                if not existing:s.add(Controller(uid=tid,code=c,name=profile.get('name') or (r.name if r else str(tid)),username=profile.get('username') or (r.username if r else '')))
            await msg.reply_text(f'Controller {tid} assigned to {", ".join(codes)}.');return
        if cmd=='update_plan':
            admin(uid)
            prompt=await msg.reply_text('Reply to THIS message with the new Plan_SO.xlsx document. The plan is validated before replacement; this request expires in 15 minutes.')
            put(s,f'upload:{uid}',{'chat':chat.id,'prompt':prompt.message_id,'expires':(db.now()+timedelta(minutes=15)).isoformat()});return
        if cmd=='update_list_so':
            admin(uid)
            prompt=await msg.reply_text('Reply to THIS message with the new List_SO.xlsx document. The roster is validated before replacement; this request expires in 15 minutes.')
            put(s,f'roster_upload:{uid}',{'chat':chat.id,'prompt':prompt.message_id,'expires':(db.now()+timedelta(minutes=15)).isoformat()});return
        if cmd in ('update_location','update_location_master'):
            admin(uid)
            prompt=await msg.reply_text('Reply to THIS message with the new Location_All.xlsx document. It is validated before Bronze/Silver loading; this request expires in 15 minutes.')
            put(s,f'location_upload:{uid}',{'chat':chat.id,'prompt':prompt.message_id,'expires':(db.now()+timedelta(minutes=15)).isoformat()});return
        if cmd=='update_dealer_master':
            admin(uid)
            prompt=await msg.reply_text('Reply to THIS message with DEALERS STATION All Location.csv. All 65 dealer codes and Cambodia coordinates are validated before replacement; this request expires in 15 minutes.')
            put(s,f'dealer_master_upload:{uid}',{'chat':chat.id,'prompt':prompt.message_id,'expires':(db.now()+timedelta(minutes=15)).isoformat()});return
        if cmd=='warehouse_refresh':
            admin(uid)
            run_id,status,metrics,inserted,updated,unchanged,rejected=refresh_warehouse(s)
            await msg.reply_text(f'Warehouse refresh #{run_id}: {status}\nInserted: {inserted}\nUpdated: {updated}\nUnchanged: {unchanged}\nDQ errors: {rejected}\nLogical visits: {metrics["logical_visits"]}\nSilver visits: {metrics["silver_visits"]}\nLocations: {metrics["silver_locations"]}\nQuality issues: {metrics["quality_issues"]}');return
        if cmd=='warehouse_status':
            admin(uid);info=latest_status(s)
            if not info:raise ValueError('Warehouse has not run yet. Use /warehouse_refresh.')
            await msg.reply_text('Latest warehouse status\n'+'\n'.join(f'{k}: {v}' for k,v in info.items()));return
        if cmd=='sync_so_list':
            admin(uid);count,so,controllers,controller_groups=import_roster(s,DATA/'List_SO.xlsx')
            result=refresh_warehouse(s) if WAREHOUSE_ENABLED else None
            await msg.reply_text(f'Synced {count} roster rows; {so} monitored SO/SS roles; {controllers} controller(s) covering {controller_groups} group(s). Removals and manual users preserved.\n{warehouse_caption(result)}');return
        if cmd in ('add_dealer_location','update_dealer_location'):
            admin(uid)
            if len(args)!=5:raise ValueError(f'Use /{cmd} | R1-CA2 | TELEGRAM_ID | Employee ID | Real Name | LAT,LON')
            c=valid_code(args[0]);tid=int(args[1]);employee=str(args[2]).strip();name=str(args[3]).strip();coordinates=coordinates_from_text(args[4])
            if tid<=0 or not employee or not name:raise ValueError('Telegram ID, Employee ID and Real Name are required.')
            if not coordinates:raise ValueError('Provide valid dealer latitude,longitude or a Google Maps link.')
            roster=s.scalar(select(Roster).where(Roster.uid==tid,Roster.employee==employee))
            if not roster:raise ValueError('Telegram ID and Employee ID do not match a roster user. Use /add or /update_list_so first.')
            if ' '.join(roster.name.casefold().split())!=' '.join(name.casefold().split()):raise ValueError(f'Real Name does not match roster: {roster.name}.')
            key=f'dealer_origin:{c}';origin=state(s,key,{})
            if cmd=='add_dealer_location' and origin:raise ValueError(f'Dealer location already exists for {c}. Use /update_dealer_location.')
            if cmd=='update_dealer_location' and not origin:raise ValueError(f'No dealer location exists for {c}. Use /add_dealer_location first.')
            lat,lon=coordinates
            put(s,key,{'lat':lat,'lon':lon,'telegram_id':tid,'employee_id':employee,'real_name':roster.name,'updated_by':uid,'updated_at':db.now().isoformat()})
            action='added' if cmd=='add_dealer_location' else 'updated'
            await msg.reply_text(f'Dealer location {action}: {c}\nEmployee ID: {employee}\nReal Name: {roster.name}\nLatitude: {lat}\nLongitude: {lon}');return
        if cmd in ('set_dealer_location','dealer_location'):
            admin(uid)
            if cmd=='set_dealer_location':
                if len(args)<2:raise ValueError('Use /set_dealer_location | R1-CA2 | LAT,LON')
                c=valid_code(args[0]);coordinates=coordinates_from_text(' '.join(args[1:]))
                if not coordinates:raise ValueError('Provide valid dealer latitude,longitude or a Google Maps link.')
                lat,lon=coordinates;put(s,f'dealer_origin:{c}',{'lat':lat,'lon':lon,'updated_by':uid,'updated_at':db.now().isoformat()})
                await msg.reply_text(f'Dealer origin saved: {c}\nLatitude: {lat}\nLongitude: {lon}\nFirst check-in distance can now be calculated.');return
            c=valid_code(args[0]) if args else (g.code if g else '')
            if not c:raise ValueError('Use /dealer_location | R1-CA2')
            origin=state(s,f'dealer_origin:{c}',{})
            if not origin:raise ValueError(f'No dealer origin saved for {c}.')
            await msg.reply_text(f'Dealer origin: {c}\nLatitude: {origin["lat"]}\nLongitude: {origin["lon"]}\nMap: https://maps.google.com/maps?q={origin["lat"]},{origin["lon"]}');return
        if cmd=='change_dealer_from':
            admin(uid)
            if len(args)!=5:
                raise ValueError('Use /change_dealer_from | TELEGRAM_ID | Real Name | R1 | CA2 | CA6')
            target_uid=int(args[0]);expected_name=' '.join(args[1].casefold().split())
            current_region=args[2].strip().upper();current_dealer=args[3].strip().upper()
            if not re.fullmatch(r'R[1-8]',current_region):raise ValueError('Current region must be R1 to R8.')
            valid_code(f'{current_region}-{current_dealer}')
            r=resolve_member(s,None,target_uid)
            actual_name=' '.join(str(r.name or '').casefold().split())
            if actual_name!=expected_name:raise ValueError(f'Real Name does not match roster: {r.name}.')
            if str(r.region or '').upper()!=current_region or str(r.dealer or '').upper()!=current_dealer:
                raise ValueError(f'Current assignment does not match. Database has {r.region}-{r.dealer}.')
            target_region,target_dealer=dealer_transfer_target(args[4],current_region)
            if (target_region,target_dealer)==(current_region,current_dealer):raise ValueError('Target dealer is the same as the current dealer.')
            changed_at=db.now()
            r.region=target_region;r.dealer=target_dealer;r.source='manual';s.flush()
            put(s,f'roster_override:{target_uid}',{
                'telegram_id':target_uid,'real_name':r.name,
                'from_region':current_region,'from_dealer':current_dealer,
                'region':target_region,'dealer':target_dealer,
                'changed_by':uid,'changed_at':changed_at.isoformat(),'active':True,
            })
            # The operational roster transfer is authoritative and must not be
            # rolled back because an unrelated warehouse refresh fails. Commit
            # it first, then queue ETL independently just like master uploads.
            s.commit()
            schedule_warehouse_refresh(context.application,'change_dealer_from')
            await msg.reply_text(
                f'✅ Dealer changed successfully\nReal Name: {r.name}\nTelegram ID: {target_uid}\n'
                f'From: {current_region}-{current_dealer}\nTo: {target_region}-{target_dealer}\n'
                'Power BI refresh has been queued. Historical facts are preserved.'
            );return
        if cmd=='add':
            if len(args)!=6:raise ValueError('Use /add | Telegram ID | Employee ID | Real Name | Region | Dealer | SO')
            tid=int(args[0]);employee=str(args[1]).strip();c=valid_code(f'{args[3]}-{args[4]}');position=normalize_position(args[5])
            if tid<=0:raise ValueError('Telegram ID must be positive.')
            if not employee:raise ValueError('Employee ID is required for plan matching.')
            if position not in ('SO','SS'):raise ValueError('Position must be SO/SS or a supported Sales Officer/Sales Supervisor title.')
            if not can_manage(s,uid,g):raise ValueError('You do not manage this group.')
            if uid not in ADMINS and (not g or g.code!=c):raise ValueError('Cannot add a user outside your group scope.')
            if s.scalar(select(Roster.id).where(Roster.uid==tid)):raise ValueError('Telegram ID already exists. Use /change or /restore.')
            if s.scalar(select(Roster.id).where(Roster.employee==employee)):raise ValueError('Employee ID already exists. Use /change or sync the official roster.')
            s.add(Roster(employee=employee,uid=tid,name=args[2],region=args[3].upper(),dealer=args[4].upper(),position=position,source='manual'))
            s.flush();result=refresh_warehouse(s) if WAREHOUSE_ENABLED else None
            await msg.reply_text(f'Added {args[2]} ({tid}), Employee ID {employee}, Position {position}. In test mode, include this Telegram ID in /setup_group.\n{warehouse_caption(result)}');return
        if cmd in ('export','summary_by_so','export_raw_data','export_so_list','currect_plan'):
            if uid in ADMINS:allowed=[x.id for x in s.scalars(select(Group))]
            else:
                codes=list(s.scalars(select(Controller.code).where(Controller.uid==uid)))
                allowed=[x.id for x in s.scalars(select(Group).where(Group.code.in_(codes)))]
                if not allowed:raise ValueError('Only a controller or global admin may export.')
            if cmd=='currect_plan':
                admin(uid)
                b,n,filename=export_current_plan(s)
                suffix=''
            elif cmd=='export_so_list':
                codes={x.code for x in s.scalars(select(Group).where(Group.id.in_(allowed)))}
                rows=[[r.region,r.dealer,r.employee,r.uid,r.name,r.username,r.position,r.active] for r in s.scalars(select(Roster)) if not excluded_position(r.position) and (uid in ADMINS or f'{r.region}-{r.dealer}'.upper() in codes)]
                b=workbook(['Region','Dealer','Employee ID','Telegram ID','Real Name','Username','Position','Active'],rows,'SO List');n=len(rows);suffix='';filename=f'{cmd}.xlsx'
            else:
                day=datetime.strptime(args[0],'%Y-%m-%d').date() if args else db.now().date()
                b,n=export_data(s,cmd,day,allowed);suffix=f'_{day}';filename=f'{cmd}{suffix}.xlsx'
            await msg.reply_document(b,filename=filename,caption=f'{n} rows');return
        if cmd=='al_summary':
            admin(uid)
            if len(args)!=1 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',args[0]):
                raise ValueError('Use /AL_Summary_YYYY-MM-DD, for example /AL_Summary_2026-11-08')
            try:day=datetime.strptime(args[0],'%Y-%m-%d').date()
            except ValueError:raise ValueError('Invalid date. Use a real date in YYYY-MM-DD format.')
            output,total,al_count=al_summary_workbook(s,day)
            filename=f'AL_Summary_{day.isoformat()}.xlsx'
            s.commit()
            delivered=await send_central_document(
                context.application,
                f'al_summary:{day.isoformat()}:{msg.chat_id}:{msg.message_id}',
                output,filename,
                caption=(f'📊 User AL Summary — {day:%d/%m/%Y}\n'
                         f'Total active users: {total}\nAL: {al_count}\nNot AL: {total-al_count}'),
                category='user_al')
            if delivered:
                await msg.reply_text(f'✅ {filename} sent to the User_AL topic.')
            else:
                raise ValueError('File was not sent. Check /alert_topics, User AL registration, bot automation status, and logs.')
            return
        if cmd in ('al','al_morning','al_afternoon'):
            # With no arguments, every rostered SO/SS can pause only their own
            # alerts. Controllers/admins may still specify another Telegram ID.
            target_uid=uid;day=db.now().date();duration=1;position=0
            if args and re.fullmatch(r'\d+',args[0]):
                target_uid=int(args[0]);position=1
                if target_uid!=uid and uid not in ADMINS and (not g or not can_manage(s,uid,g)):
                    raise ValueError('You may pause only your own alerts. Use the command without another Telegram ID.')
            elif args and target_uid!=uid:
                raise ValueError('Telegram ID must be numeric.')
            if position<len(args) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',args[position]):
                day=datetime.strptime(args[position],'%Y-%m-%d').date();position+=1
            if position<len(args):
                parsed=leave_duration(args[position])
                if parsed is None:raise ValueError('Use a date or duration such as 4day or 5days.')
                duration=parsed;position+=1
            if position!=len(args):raise ValueError('Too many values. Use /AL | TELEGRAM_ID | YYYY-MM-DD | 4day')
            if duration>1 and cmd!='al':raise ValueError('Multi-day duration is supported only by /AL (full day).')
            r=resolve_member(s,g if g and uid not in ADMINS else None,target_uid)
            a=datetime.combine(day,datetime.min.time());b=a+timedelta(days=duration)
            if cmd=='al_morning':b=a.replace(hour=11,minute=45)
            elif cmd=='al_afternoon':a=a.replace(hour=13)
            groups=leave_groups(s,r,g)
            if not groups:raise ValueError('No registered group matches this user. Run /setup_group for the dealer first.')
            save_leave(s,r,a,b,groups,source=f'/{cmd}',source_message_id=msg.message_id,recorded_by=uid)
            label={'al':'full day','al_morning':'morning','al_afternoon':'afternoon'}[cmd]
            scope=', '.join(sorted({x.code for x in groups}))
            date_text=f'{day:%d/%m/%Y}' if duration==1 else f'{day:%d/%m/%Y} to {(b-timedelta(days=1)):%d/%m/%Y} ({duration} days)'
            s.commit()
            await send_central_alert(
                context.application,f'user_al:command:{msg.chat_id}:{msg.message_id}:{r.uid}',
                leave_alert_text(r,a,b,groups,f'/{cmd}',uid),category='user_al')
            await msg.reply_text(f'{r.name}: {label} alerts paused for {date_text}. Scope: {scope}. Other reporting periods remain active.');return
        if not g:raise ValueError('Group is not registered. A global admin must run /setup_group first.')
        if not can_manage(s,uid,g):raise ValueError('You do not manage this group.')
        if cmd=='register_topic':
            admin(uid);g.topic=msg.message_thread_id;await msg.reply_text(f'Reporting topic set to {g.topic or "main group"}.');return
        if cmd=='status':
            users=members(s,g);plan=state(s,'plan_latest')
            await msg.reply_text(f'Group: {g.title}\nScope: {g.code}\nMonitoring SO/SS: {len(users)}\nTest mode: {TEST_MODE}\nAlerts enabled: {bool(enabled(g))}\nWindows: 08:00–11:45 / 13:00–16:45\nPlan: {plan.get("min_date","missing")} to {plan.get("max_date","missing")}\nBoundary files: {len(list((DATA/"boundaries").glob("*.kml")))+len(list((DATA/"boundaries").glob("*.geojson")))}');return
        if cmd=='reset_today':
            admin(uid);start=db.now().replace(hour=0,minute=0,second=0,microsecond=0)
            n=s.execute(delete(Missing).where(Missing.chat==chat.id,Missing.start>=start)).rowcount
            await msg.reply_text(f'Cleared {n} missing rows today. Reports, pins, plans and roster preserved; completed windows will not resend automatically.');return
        if cmd=='alert_now':
            at=db.now();ws=[(a,b) for a,b in windows(at.date()) if b<=at]
            if not ws:raise ValueError('No completed SO reporting window today.')
            if not enabled(g):raise ValueError('Alerts are disabled for this group under the current test configuration.')
            s.commit();await check_window(context.application,g.id,*ws[-1]);await msg.reply_text('Checked latest completed window. Existing delivered alerts are not duplicated.');return
        if cmd in ('remove','restore','change'):
            if not args:raise ValueError('Provide the Telegram ID. Send /start for command examples.')
            r=resolve_member(s,g,args[0])
            if cmd in ('remove','restore'):
                put(s,f'excluded:{g.id}:{r.uid}',{'yes':cmd=='remove'});await msg.reply_text(f'{r.name}: {cmd} completed.');return
            if len(args)!=3 or args[1].lower() not in ('name','region','dealer','position'):raise ValueError('Use /change | ID | name/region/dealer/position | value')
            field=args[1].lower()
            if field in ('region','dealer'):admin(uid)
            setattr(r,field,normalize_position(args[2]) if field=='position' else args[2]);r.source='manual';s.flush()
            result=refresh_warehouse(s) if WAREHOUSE_ENABLED else None
            await msg.reply_text(f'Updated {r.name}: {field}.\n{warehouse_caption(result)}');return

async def plan_upload(update,context):
    msg=update.effective_message;uid=update.effective_user.id
    admin(uid)
    with db.session() as s:
        pending=state(s,f'upload:{uid}')
        if not pending or pending['chat']!=msg.chat_id or datetime.fromisoformat(pending['expires'])<db.now():raise ValueError('Send /update_plan first, then reply to its prompt with the workbook.')
        if not msg.reply_to_message or msg.reply_to_message.message_id!=pending['prompt']:raise ValueError('Reply directly to the /update_plan upload prompt.')
        if not msg.document.file_name.lower().endswith('.xlsx'):raise ValueError('Upload an .xlsx workbook.')
        if (msg.document.file_size or 0)>20_000_000:raise ValueError('Plan upload exceeds 20 MB.')
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'Plan_SO.xlsx'
        f=await msg.document.get_file();await f.download_to_drive(path)
        # Parse in worker thread so the bot remains responsive to polling.
        def save():
            with db.session() as s:
                n=import_plan(s,path,uid,msg.document.file_name);latest=state(s,'plan_latest',{})
                put(s,f'upload:{uid}',{});return n,latest
        n,latest=await asyncio.to_thread(save)
    result=await auto_refresh_warehouse(context.application,'update_plan')
    await msg.reply_text(f'Plan updated: {n} scheduled commune/date rows.\nActive dates: {latest.get("min_date")} to {latest.get("max_date")}\nActive file: {latest.get("filename")}\nThis upload is now the only active plan; previous versions are archived for audit.\n{warehouse_caption(result)}')

async def roster_upload(update,context):
    msg=update.effective_message;uid=update.effective_user.id
    admin(uid)
    with db.session() as s:
        pending=state(s,f'roster_upload:{uid}')
        if not pending or pending['chat']!=msg.chat_id or datetime.fromisoformat(pending['expires'])<db.now():raise ValueError('Send /update_list_so first, then reply to its prompt with the workbook.')
        if not msg.reply_to_message or msg.reply_to_message.message_id!=pending['prompt']:raise ValueError('Reply directly to the /update_list_so upload prompt.')
        if not msg.document.file_name.lower().endswith('.xlsx'):raise ValueError('Upload an .xlsx workbook.')
        if (msg.document.file_size or 0)>20_000_000:raise ValueError('Roster upload exceeds 20 MB.')
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'List_SO.xlsx'
        f=await msg.document.get_file();await f.download_to_drive(path)
        def save():
            with db.session() as s:
                count,so,controllers,controller_groups=import_roster(s,path)
                shutil.copy2(path,DATA/'List_SO.xlsx')
                put(s,'bundled_roster',{'sha256':__import__('hashlib').sha256(path.read_bytes()).hexdigest(),'rows':count,'so':so,'controllers':controllers,'controller_groups':controller_groups})
                put(s,f'roster_upload:{uid}',{})
                return count,so,controllers,controller_groups
        count,so,controllers,controller_groups=await asyncio.to_thread(save)
    result=await auto_refresh_warehouse(context.application,'update_list_so')
    await msg.reply_text(f'SO list updated: {count} roster rows; {so} monitored SO/SS roles; {controllers} controller(s) covering {controller_groups} group(s). Manual users and removals preserved.\n{warehouse_caption(result)}')

async def location_upload(update,context):
    msg=update.effective_message;uid=update.effective_user.id
    admin(uid)
    with db.session() as s:
        pending=state(s,f'location_upload:{uid}')
        if not pending or pending['chat']!=msg.chat_id or datetime.fromisoformat(pending['expires'])<db.now():raise ValueError('Send /update_location first, then reply to its prompt with the workbook.')
        if not msg.reply_to_message or msg.reply_to_message.message_id!=pending['prompt']:raise ValueError('Reply directly to the /update_location upload prompt.')
        if not msg.document.file_name.lower().endswith('.xlsx'):raise ValueError('Upload an .xlsx workbook.')
        if (msg.document.file_size or 0)>20_000_000:raise ValueError('Location upload exceeds 20 MB.')
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'Location_All.xlsx';f=await msg.document.get_file();await f.download_to_drive(path)
        def save():
            with db.session() as s:
                count,_=import_locations(s,path,msg.document.file_name)
                destination=DATA/'Location_All.xlsx';temporary=DATA/'Location_All.xlsx.tmp'
                shutil.copy2(path,temporary);temporary.replace(destination)
                put(s,'bundled_location',{'sha256':__import__('hashlib').sha256(path.read_bytes()).hexdigest(),'rows':count})
                put(s,f'location_upload:{uid}',{})
                return count
        count=await asyncio.to_thread(save)
    result=await auto_refresh_warehouse(context.application,'update_location')
    await msg.reply_text(f'Location master updated: {count} communes.\n{warehouse_caption(result)}')

async def dealer_master_upload(update,context):
    msg=update.effective_message;uid=update.effective_user.id
    admin(uid)
    with db.session() as s:
        pending=state(s,f'dealer_master_upload:{uid}')
        if not pending or pending['chat']!=msg.chat_id or datetime.fromisoformat(pending['expires'])<db.now():raise ValueError('Send /update_dealer_master first, then reply to its prompt with the CSV.')
        if not msg.reply_to_message or msg.reply_to_message.message_id!=pending['prompt']:raise ValueError('Reply directly to the /update_dealer_master upload prompt.')
        if not msg.document.file_name.lower().endswith('.csv'):raise ValueError('Upload a .csv file named DEALERS STATION All Location.csv.')
        if (msg.document.file_size or 0)>10_000_000:raise ValueError('Dealer master upload exceeds 10 MB.')
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/'DEALERS_STATION_All_Location.csv'
        f=await msg.document.get_file();await f.download_to_drive(path)
        def save():
            with db.session() as s:
                source_rows,dealers=import_dealer_locations(s,path)
                destination=DATA/'DEALERS_STATION_All_Location.csv';temporary=DATA/'DEALERS_STATION_All_Location.csv.tmp'
                shutil.copy2(path,temporary);temporary.replace(destination)
                put(s,'bundled_dealer_locations',{'sha256':__import__('hashlib').sha256(path.read_bytes()).hexdigest(),'source_rows':source_rows,'dealers':dealers,'filename':msg.document.file_name})
                put(s,f'dealer_master_upload:{uid}',{})
                return source_rows,dealers
        source_rows,dealers=await asyncio.to_thread(save)
    result=await auto_refresh_warehouse(context.application,'update_dealer_master')
    await msg.reply_text(f'Dealer station master updated: {dealers}/65 dealers from {source_rows} source station rows. New check-ins and warehouse models now use this master.\n{warehouse_caption(result)}')

async def excel_upload(update,context):
    """Dispatch a replied workbook to the matching active upload prompt."""
    msg=update.effective_message;uid=update.effective_user.id
    reply_id=msg.reply_to_message.message_id if msg.reply_to_message else None
    with db.session() as s:
        roster=state(s,f'roster_upload:{uid}',{});plan=state(s,f'upload:{uid}',{});location=state(s,f'location_upload:{uid}',{});dealer=state(s,f'dealer_master_upload:{uid}',{})
    if dealer and dealer.get('prompt')==reply_id:return await dealer_master_upload(update,context)
    if location and location.get('prompt')==reply_id:return await location_upload(update,context)
    if roster and roster.get('prompt')==reply_id:return await roster_upload(update,context)
    if plan and plan.get('prompt')==reply_id:return await plan_upload(update,context)
    raise ValueError('Reply directly to an active /update_plan, /update_list_so, /update_location or /update_dealer_master upload prompt.')

async def warehouse_job(context):
    if not WAREHOUSE_ENABLED:return
    await auto_refresh_warehouse(context.application,'scheduled')

def auto_reconcile_reporter_group(s,user_id,group,text,message_id,at):
    """Move one roster identity to the registered dealer proven by a form.

    Telegram ID is the identity key. A plain message, pin, Morning Talk, test
    group, or unregistered group can never change the roster assignment.
    """
    if not AUTO_RECONCILE_DEALER or not group or group.test_uids or not is_form(text):return None
    try:target_region,target_dealer=valid_code(group.code).split('-',1)
    except (ValueError,AttributeError):return None
    candidates=list(s.scalars(select(Roster).where(Roster.uid==int(user_id),Roster.active==1)))
    candidates.sort(key=lambda row:(row.source!='excel',str(row.employee or '').startswith('manual:')))
    r=candidates[0] if candidates else None
    if not r or normalize_position(r.position) not in ('SO','SS'):return None
    old_region=str(r.region or '').upper();old_dealer=str(r.dealer or '').upper()
    if (old_region,old_dealer)==(target_region,target_dealer):return None
    # Apply the move to every active identity for this Telegram account. This
    # prevents a legacy/manual duplicate from remaining in the old group.
    for identity in candidates:
        identity.region=target_region;identity.dealer=target_dealer;identity.source='manual'
    s.flush()
    old_code=f'{old_region}-{old_dealer}'.upper()
    old_group_ids=[item.id for item in s.scalars(select(Group))
                   if str(item.code or '').upper()==old_code]
    day_start=at.replace(hour=0,minute=0,second=0,microsecond=0)
    cleared_missing=0
    if old_group_ids:
        # The master assignment was wrong, so same-day misses attributed to
        # the old dealer are invalid. Removing them also stops old follow-ups.
        cleared_missing=s.execute(delete(Missing).where(
            Missing.uid==r.uid,Missing.chat.in_(old_group_ids),Missing.start>=day_start
        )).rowcount or 0
    audit={
        'telegram_id':r.uid,'employee_id':r.employee,'real_name':r.name,
        'from_region':old_region,'from_dealer':old_dealer,
        'region':target_region,'dealer':target_dealer,
        'changed_by':'automatic_valid_report','changed_at':at.isoformat(),
        'evidence_chat_id':group.id,'evidence_message_id':message_id,'active':True,
        'cleared_old_group_missing':cleared_missing,
    }
    put(s,f'roster_override:{r.uid}',audit)
    put(s,f'roster_auto_reconcile:{group.id}:{message_id}:{r.uid}',audit)
    return {'roster':r,'audit':audit,'group':group}

async def activity(update,context):
    msg=update.effective_message;u=update.effective_user
    if not msg or not u or u.is_bot:return
    text=msg.text or msg.caption or ''
    at=msg.date.astimezone(TZ).replace(tzinfo=None)
    reconciliation=None
    with db.session() as s:
        candidate_group=s.get(Group,msg.chat_id)
        if candidate_group and enabled(candidate_group) and (
                candidate_group.topic is None or msg.message_thread_id==candidate_group.topic):
            reconciliation=auto_reconcile_reporter_group(
                s,u.id,candidate_group,text,msg.message_id,at)
    if reconciliation:
        r=reconciliation['roster'];audit=reconciliation['audit'];group=reconciliation['group']
        schedule_warehouse_refresh(context.application,'auto_reconcile_dealer')
        text_alert=(
            '🔄 User Dealer Assignment Auto-Corrected\n'
            f'Date: {at:%d/%m/%Y %I:%M %p}\nReal Name: {r.name}\n'
            f'Telegram ID: {r.uid}\nEmployee ID: {r.employee}\n'
            f'From Master: {audit["from_region"]}-{audit["from_dealer"]}\n'
            f'To Actual Group: {audit["region"]}-{audit["dealer"]}\n'
            f'Group: {group.title or group.code}\nEvidence: valid outlet report\n'
            f'Old-group missing records cleared today: {audit["cleared_old_group_missing"]}\n'
            'Persistent roster override saved; historical facts were not changed.'
        )
        await send_central_alert(
            context.application,f'auto_reconcile:{group.id}:{msg.message_id}:{r.uid}',
            text_alert,category='user_in_wrong_group')
    # Morning Talk is an operational attendance report. Mirror it once from
    # every registered reporting group, even when its sender is a supervisor
    # or assistant who is not part of the missing-report roster.
    with db.session() as s:
        morning_group=s.get(Group,msg.chat_id)
        morning_allowed=bool(morning_group and enabled(morning_group) and
                             (morning_group.topic is None or msg.message_thread_id==morning_group.topic))
    if morning_allowed and is_morning_talk(text):
        at=msg.date.astimezone(TZ).replace(tzinfo=None)
        business_day=morning_talk_date(text,at.date())
        username=f'@{u.username}' if u.username else 'No username'
        with db.session() as s:
            roster_members=members(s,morning_group)
            all_active=list(s.scalars(select(Roster).where(Roster.active==1)))
            all_reporters=[r for r in all_active
                           if normalize_position(r.position) in ('SO','SS')]
            matched=[];unmatched=[]
            matched_meta={}
            detected_leave_entries=morning_talk_full_day_leave_entries(text)
            for leave_entry in detected_leave_entries:
                supplied_employee=leave_entry['employee_id']
                supplied_name=leave_entry['name']
                # An explicit Employee ID is authoritative and can identify
                # any active position (including SA). Never fall back to a
                # similar name when the supplied ID is unknown or duplicated.
                if supplied_employee:
                    employee_key=normalized_employee_id(supplied_employee)
                    candidates=[r for r in all_active
                                if normalized_employee_id(r.employee)==employee_key]
                    match_method='employee_id'
                else:
                    candidates=[]
                    match_method='name_fallback'
                key=normalized_person_name(supplied_name)
                if not supplied_employee:
                    candidates=[r for r in roster_members if normalized_person_name(r.name)==key]
                if not supplied_employee and len(candidates)!=1:
                    token_key=' '.join(sorted(key.split()))
                    candidates=[r for r in roster_members
                                if ' '.join(sorted(normalized_person_name(r.name).split()))==token_key]
                # If the master dealer is stale, a globally unique real name
                # may still be put on AL. Suppress both old and actual groups;
                # only a later valid outlet report performs the dealer move.
                if not supplied_employee and len(candidates)!=1:
                    candidates=[r for r in all_reporters if normalized_person_name(r.name)==key]
                if not supplied_employee and len(candidates)!=1:
                    token_key=' '.join(sorted(key.split()))
                    candidates=[r for r in all_reporters
                                if ' '.join(sorted(normalized_person_name(r.name).split()))==token_key]
                # Final safe fallback: token-equivalent names within the reporting
                # dealer, including 'Soth Narin' vs 'NARIN Soth'. Never select
                # a fuzzy substring or a nonunique employee.
                if not supplied_employee and len(candidates)!=1:
                    scoped=[r for r in all_active if
                            f'{r.region}-{r.dealer}'.upper()==morning_group.code.upper()]
                    tokens=sorted(key.split())
                    candidates=[r for r in scoped if
                                sorted(normalized_person_name(r.name).split())==tokens]
                if len(candidates)==1 and candidates[0].uid not in {x.uid for x in matched}:
                    r=candidates[0]
                    start=datetime.combine(business_day,datetime.min.time())
                    leave_scope=leave_groups(s,r,morning_group)
                    save_leave(s,r,start,start+timedelta(days=1),leave_scope,
                               source='Morning Talk',source_message_id=msg.message_id,
                               recorded_by=u.id)
                    r._morning_leave_scope=leave_scope
                    matched.append(r)
                    matched_meta[r.uid]={
                        'match_method':match_method,
                        'supplied_employee_id':supplied_employee,
                        'supplied_name':supplied_name,
                    }
                else:
                    label=(f'ID {supplied_employee}' +
                           (f' — {supplied_name}' if supplied_name else ''))
                    unmatched.append(label if supplied_employee else supplied_name)
            put(s,f'morning_talk:{morning_group.id}:{at.date().isoformat()}',{
                'received':True,'received_at':at.isoformat(),'message_id':msg.message_id,
                'sender_id':u.id,'sender_name':u.full_name,'username':u.username or '',
                'group_code':morning_group.code,'group_title':morning_group.title or '',
                'business_date':business_day.isoformat(),
                'al_matched':[{'uid':r.uid,'employee':r.employee,'name':r.name,
                               **matched_meta.get(r.uid,{})} for r in matched],
                'al_unmatched':unmatched,
            })
        report=text.strip()
        # Telegram text messages are limited to 4096 characters.
        if len(report)>3300:report=report[:3297]+'...'
        al_summary=', '.join(f'{r.name} (ID: {r.employee})' for r in matched) or 'None'
        al_warning=(f'\n⚠️ AL identity not matched uniquely in the active roster: '
                    f'{", ".join(unmatched)}' if unmatched else '')
        central_text=(
            '🌅 Morning Talk Report Received\n'
            f'Date Received: {at:%d/%m/%Y}\n'
            f'Business Date: {business_day:%d/%m/%Y}\n'
            f'Time Received: {at:%I:%M %p}\n'
            f'Group: {morning_group.title or morning_group.code}\n'
            f'Group Code: {morning_group.code}\n'
            f'Sent By: {u.full_name}\n'
            f'Telegram: {username}\n'
            f'Telegram ID: {u.id}\n'
            f'Full-day AL detected: {len(detected_leave_entries)} — {al_summary}{al_warning}\n'
            f'Roster-matched AL: {len(matched)}; unmatched: {len(unmatched)}\n\n{report}'
        )
        await send_central_alert(context.application,
                                 f'morning_talk:{msg.chat_id}:{msg.message_id}',central_text,
                                 category='morning_talk')
        for r in matched:
            start=datetime.combine(business_day,datetime.min.time())
            leave_scope=getattr(r,'_morning_leave_scope',[morning_group])
            await send_central_alert(
                context.application,
                f'user_al:morning_talk:{msg.chat_id}:{msg.message_id}:{r.uid}',
                leave_alert_text(r,start,start+timedelta(days=1),leave_scope,
                                 'Morning Talk',u.id),category='user_al')
    # Copy qualifying banner reports with their original photos/caption. This
    # is deliberately rule-based: no OCR, LLM, image recognition, or AI agent.
    # Queue before roster validation so valid reports from supervisors or new
    # field accounts are not silently missed; the source group must still be a
    # registered and enabled reporting group/topic.
    with db.session() as s:
        banner_group=s.get(Group,msg.chat_id)
        banner_allowed=bool(banner_group and enabled(banner_group) and
                            (banner_group.topic is None or
                             msg.message_thread_id==banner_group.topic))
    if banner_allowed and (msg.photo or
            (msg.document and (msg.document.mime_type or '').startswith('image/'))):
        queue_banner_forward(context.application,msg,text)
    with db.session() as s:
        put(s,f'profile:{u.id}',{'name':u.full_name,'username':u.username or ''})
        for c in s.scalars(select(Controller).where(Controller.uid==u.id)):
            c.name=u.full_name;c.username=u.username or ''
        g=s.get(Group,msg.chat_id)
        if not g or not enabled(g):
            # Log configuration failures without disclosing employee report text.
            if msg.location or is_form(text):
                warnings=context.application.bot_data.setdefault('collection_warnings',{})
                last=warnings.get(msg.chat_id,0)
                if time.monotonic()-last>300:
                    log.warning('Outlet collection skipped chat=%s: %s',msg.chat_id,
                                'group is not registered; use /setup_group' if not g else 'group is disabled by configuration')
                    warnings[msg.chat_id]=time.monotonic()
            return
        if g.topic is not None and msg.message_thread_id!=g.topic:return
        candidates=list(s.scalars(select(Roster).where(Roster.uid==u.id,Roster.active==1)))
        candidates.sort(key=lambda row:(row.source!='excel',str(row.employee or '').startswith('manual:')))
        r=candidates[0] if candidates else None
        auto_join=None
        if not r and is_form(text):
            # A valid outlet report is sufficient evidence to register a new
            # field reporter in this known Region-Dealer group. Use the
            # established manual:<telegram_id> identity until the official
            # List_SO workbook supplies the employee ID.
            if g.test_uids and u.id not in g.test_uids:return
            if TEST_MODE and u.id not in (g.test_uids or []):return
            try:
                target_region,target_dealer=valid_code(g.code).split('-',1)
            except (ValueError,AttributeError):
                return
            if DEALER_REGIONS.get(target_dealer)!=target_region:
                return
            provisional_id=f'manual:{u.id}'
            r=s.scalar(select(Roster).where(
                Roster.employee==provisional_id,Roster.uid==u.id))
            if r:
                r.active=1;r.name=u.full_name;r.username=u.username or ''
                r.region=target_region;r.dealer=target_dealer
                r.position='SO';r.source='manual'
            else:
                # The employee column is unique. If a stale row already owns
                # this synthetic ID, do not overwrite another Telegram user.
                collision=s.scalar(select(Roster.id).where(Roster.employee==provisional_id))
                if collision:return
                r=Roster(employee=provisional_id,uid=u.id,name=u.full_name,
                         username=u.username or '',region=target_region,
                         dealer=target_dealer,position='SO',active=1,source='manual')
                s.add(r)
            s.flush()
            auto_join={'group':g,'region':target_region,'dealer':target_dealer,
                       'employee':provisional_id,'name':u.full_name,'username':u.username or ''}
        if not r or r.uid not in {x.uid for x in members(s,g)}:return
        r.username=u.username or ''
        at=msg.date.astimezone(TZ).replace(tzinfo=None)
        reply=msg.reply_to_message.message_id if msg.reply_to_message else None
        text_coordinates=coordinates_from_text(text)
        if is_self_full_day_leave(text) and not msg.photo and not msg.document and not text_coordinates:
            start=datetime.combine(at.date(),datetime.min.time());end=start+timedelta(days=1)
            save_leave(s,r,start,end,[g],source='Standalone AL message',
                       source_message_id=msg.message_id,recorded_by=u.id)
            al_text=leave_alert_text(r,start,end,[g],'Standalone AL message',u.id)
            s.commit()
            await send_central_alert(
                context.application,f'user_al:standalone:{msg.chat_id}:{msg.message_id}:{r.uid}',
                al_text,category='user_al')
            if automation_enabled(s):
                await msg.reply_text(f'✅ {r.name}: full-day leave recorded for {at:%d/%m/%Y}. Missing-report alerts are paused for today.')
            return
        if text.strip() and reply and not text_coordinates and not is_form(text) and not msg.photo and not msg.document:
            # Exception questions are replies to the SO's original pin. A reply
            # to the bot question is exact; a reply to the pin resolves the
            # newest still-unanswered exception for that pin.
            day_start=at.replace(hour=0,minute=0,second=0,microsecond=0)
            pin_events=list(s.scalars(select(Event).where(Event.chat==msg.chat_id,Event.uid==u.id,
                                                          Event.kind=='pin',Event.at>=day_start)))
            matches=[]
            for pin in pin_events:
                data=dict(pin.data)
                for category in ('wrong_plan','over_10km'):
                    if data.get(f'{category}_reason'):continue
                    question_mid=data.get(f'{category}_question_mid')
                    if reply==question_mid:matches.append((2,data.get(f'{category}_question_at',''),pin,category))
                    elif reply==pin.mid and question_mid:matches.append((1,data.get(f'{category}_question_at',''),pin,category))
            if matches:
                _,_,pin,category=max(matches,key=lambda item:(item[0],item[1]))
                pin.data={**pin.data,f'{category}_reason':text[:8000],f'{category}_reason_at':at.isoformat()}
                schedule_warehouse_refresh(context.application,f'{category}_reason')
                await thank_for_reason(msg)
                return
        if text.strip() and reply:
            mm=list(s.scalars(select(Missing).where(Missing.chat==msg.chat_id,Missing.uid==u.id)))
            targets=[m for m in mm if reply in (m.alert_mid,m.question_mid)]
            if targets and not is_form(text) and not msg.photo and not msg.document:
                analysis=await asyncio.to_thread(analyze_reason,text)
                put(s,f'reason_review:{msg.chat_id}:{msg.message_id}',{
                    'date':at.date().isoformat(),'recorded_at':at.isoformat(),
                    'chat':msg.chat_id,'telegram_id':u.id,'employee_id':r.employee,
                    'name':r.name,'region':r.region,'dealer':r.dealer,
                    'reply_to':reply,'message':text[:4000],**analysis})
                if analysis['is_reason']:
                    for m in targets:m.reason=analysis['summary'];m.reason_at=at
                    schedule_warehouse_refresh(context.application,'missing_reason')
                    await thank_for_reason(msg)
                return
        if text.strip() and not text_coordinates and not is_form(text) and not msg.photo and not msg.document:
            # A simple typed reason answers the latest unresolved alert for this
            # same SO/group within 30 minutes. This fallback also applies when
            # Telegram's reply points at another message in the alert thread
            # instead of the exact stored alert/question message ID.
            candidates=list(s.scalars(select(Missing).where(Missing.chat==msg.chat_id,Missing.uid==u.id,Missing.reason.is_(None),Missing.sent<=at,Missing.sent>=at-timedelta(minutes=30)).order_by(Missing.sent.desc())))
            if candidates:
                analysis=await asyncio.to_thread(analyze_reason,text)
                put(s,f'reason_review:{msg.chat_id}:{msg.message_id}',{
                    'date':at.date().isoformat(),'recorded_at':at.isoformat(),
                    'chat':msg.chat_id,'telegram_id':u.id,'employee_id':r.employee,
                    'name':r.name,'region':r.region,'dealer':r.dealer,
                    'reply_to':reply,'message':text[:4000],**analysis})
                if analysis['is_reason']:
                    candidates[0].reason=analysis['summary'];candidates[0].reason_at=at
                    schedule_warehouse_refresh(context.application,'missing_reason_fallback')
                    await thank_for_reason(msg)
                return
        kind='pin' if msg.location or text_coordinates else ('photo' if msg.photo or (msg.document and (msg.document.mime_type or '').startswith('image/')) else ('form' if is_form(text) else None))
        if not kind:return
        e=s.scalar(select(Event).where(Event.chat==msg.chat_id,Event.mid==msg.message_id))
        data=dict(e.data) if e else {}
        data.update({'text':text,'reply':reply,'album':msg.media_group_id,'person':person(r)})
        if kind=='pin':
            lat,lon=(msg.location.latitude,msg.location.longitude) if msg.location else text_coordinates
            data.update(lat=lat,lon=lon,map_link=f'https://maps.google.com/maps?q={lat},{lon}',
                        pin_source='telegram_location' if msg.location else 'explicit_coordinates')
        if kind=='photo':
            media=msg.photo[-1] if msg.photo else msg.document
            data.update(telegram_file_id=getattr(media,'file_id',''),
                        telegram_file_unique_id=getattr(media,'file_unique_id',''))
            data.setdefault('status','Review');data.setdefault('ocr_issue','OCR pending')
        if e:e.data=data;e.kind=kind
        else:e=Event(chat=msg.chat_id,mid=msg.message_id,uid=u.id,at=at,kind=kind,data=data);s.add(e)
        s.flush();eid=e.id
    if auto_join:
        joined=auto_join
        await send_central_alert(
            context.application,
            f'new_user_report:{msg.chat_id}:{msg.message_id}:{u.id}',
            '🆕 New Join Automatically Added from Report\n'
            f'Real Name: {joined["name"]}\nTelegram ID: {u.id}\n'
            f'Temporary Employee ID: {joined["employee"]}\n'
            f'Group: {joined["group"].title or joined["group"].code}\n'
            f'Assignment: {joined["region"]}-{joined["dealer"]}\n'
            'Status Master: New Join\n'
            'The first valid report was saved. Replace the temporary ID when the official employee ID is available.',
            category='new_user_join')
    if kind=='photo' and not data.get('ocr_done'):
        # Save event first; OCR must never block reporting-window ingestion.
        context.application.create_task(ocr_event(context,eid,msg,at))
    elif kind=='pin' and not data.get('geo'):
        context.application.create_task(geocode_event(context.application,eid))
    elif WAREHOUSE_ENABLED:
        schedule_warehouse_refresh(context.application,f'activity:{kind}')

async def geocode_event(application,eid):
    with db.session() as s:
        event=s.get(Event,eid)
        if not event or event.kind!='pin':return
        existing=event.data.get('geo') or {}
        if existing and not existing.get('geo_issue'):
            ready=True
        else:ready=False
        data=dict(event.data);lat=data['lat'];lon=data['lon']
        cached=state(s,f'geo:{lat:.6f}:{lon:.6f}')
    if ready:
        await analyze_pin_event(application,eid);return
    # Always retry the local boundary first. This upgrades records previously
    # resolved by the online fallback as soon as a KML is installed.
    geo=locate(lat,lon)
    if not is_cambodia_coordinate(lat,lon):
        with db.session() as s:
            event=s.get(Event,eid)
            if event:event.data={**event.data,'geo':geo,'analysis_status':'Quarantined: coordinate outside Cambodia'}
        await auto_refresh_warehouse(application,'quarantined_pin')
        return
    if geo.get('geo_issue'):
        if cached:geo=cached
        else:
            geo=await reverse_online(lat,lon,application.bot_data['geo_lock'],application.bot_data['geo_last'])
    with db.session() as s:put(s,f'geo:{lat:.6f}:{lon:.6f}',geo)
    with db.session() as s:
        event=s.get(Event,eid)
        if event:event.data={**event.data,'geo':geo}
    await analyze_pin_event(application,eid)
    await auto_refresh_warehouse(application,'pin_analysis')

async def backfill_geo(app):
    with db.session() as s:ids=[e.id for e in s.scalars(select(Event).where(Event.kind=='pin')) if not e.data.get('geo') or e.data.get('geo',{}).get('geo_issue')]
    for eid in ids:await geocode_event(app,eid)

async def backfill_geo_job(context):
    """Run startup backfill only after Telegram Application has started."""
    await backfill_geo(context.application)

async def ocr_event(context,eid,msg,at):
    async with context.application.bot_data['ocr_sem']:
        filename=f'{msg.chat_id}_{msg.message_id}.jpg'
        image_bytes=b''
        try:
            photo=msg.photo[-1] if msg.photo else msg.document
            # Keep a local scratch copy for OCR and persist bytes in PostgreSQL.
            image_dir=DATA/'report_images';image_dir.mkdir(parents=True,exist_ok=True)
            path=image_dir/filename
            f=await photo.get_file();await f.download_to_drive(path)
            if path.exists():image_bytes=path.read_bytes()
            result=await asyncio.to_thread(scan,path,at)
        except Exception as exc:
            log.exception('Photo OCR failed event=%s',eid)
            result={'status':'Review','ocr_issue':f'Photo download failed: {type(exc).__name__}'}
        with db.session() as s:
            if image_bytes:db.save_report_image(s,filename,msg.chat_id,msg.message_id,image_bytes)
            e=s.get(Event,eid)
            if e:e.data={**e.data,**result,'ocr_done':True}
        await auto_refresh_warehouse(context.application,'photo_ocr')

async def route(update,context):
    if not update.effective_message or not update.effective_user:return
    msg=update.effective_message
    command_text=(msg.text or '').lstrip()
    command_name=parse(command_text)[0] if command_text.startswith('/') else ''
    started=time.monotonic()
    if command_name:
            log.info('Command received command=%s chat=%s user=%s update=%s',command_name,update.effective_chat.id,update.effective_user.id,getattr(update,'update_id',None))
    # Preserve message order inside one group, but never make one slow group
    # block commands and reports from the other 64 production groups.
    async with chat_lock(context.application,update.effective_chat.id):
        try:
            if getattr(msg,'new_chat_members',None):
                for new_user in msg.new_chat_members:
                    await notify_global_admins_new_member(context.application,update.effective_chat,new_user,update.effective_user,msg.date)
                return
            with db.session() as s:
                u=update.effective_user
                put(s,f'profile:{u.id}',{'name':u.full_name,'username':u.username or ''})
            if (msg.text or '').lstrip().startswith('/'):
                await command(update,context)
            elif msg.document and (msg.document.file_name or '').lower().endswith(('.xlsx','.csv')):
                await excel_upload(update,context)
            else:await activity(update,context)
        except (ValueError,TypeError,IndexError) as e:
            await update.effective_message.reply_text(f'❌ {e}')
        finally:
            if command_name:
                log.info('Command finished command=%s chat=%s user=%s seconds=%.3f',command_name,update.effective_chat.id,update.effective_user.id,time.monotonic()-started)

async def error(update,context):
    log.error('Bot operation failed',exc_info=context.error)
    if isinstance(update,Update) and update.effective_message:
        try:await update.effective_message.reply_text('Operation failed. Check logs/bot.log for details; no successful result is assumed.')
        except Exception:pass

async def post_init(app):
    app.bot_data['chat_locks']={};app.bot_data['scheduler_lock']=asyncio.Lock();app.bot_data['ocr_sem']=asyncio.Semaphore(2)
    app.bot_data['warehouse_refresh_lock']=asyncio.Lock()
    app.bot_data['geo_lock']=asyncio.Lock();app.bot_data['geo_last']=[0.0]
    app.bot_data['routing_lock']=asyncio.Lock();app.bot_data['routing_last']=[0.0]
    app.bot_data['route_analysis_lock']=asyncio.Lock()
    # Start at the next complete reporting window. Never invent misses during downtime.
    app.bot_data['started']=db.now()
    app.job_queue.run_repeating(tick,interval=5,first=5,job_kwargs={'max_instances':1,'coalesce':True,'misfire_grace_time':30})
    if WAREHOUSE_ENABLED:
        # The refresh handles a large historical event set. Run it in the
        # existing worker-thread path only after Telegram polling has started.
        app.job_queue.run_repeating(warehouse_job,interval=WAREHOUSE_INTERVAL_MINUTES*60,
                                    first=WAREHOUSE_INITIAL_DELAY_SECONDS,
                                    job_kwargs={'max_instances':1,'coalesce':True,'misfire_grace_time':max(600,WAREHOUSE_INTERVAL_MINUTES*60)})
    # A JobQueue task starts after Application.start(), avoiding PTB's warning
    # about un-awaited tasks created from post_init.
    app.job_queue.run_once(backfill_geo_job,when=1,job_kwargs={'misfire_grace_time':600})

async def post_shutdown(app):db.release_lock()

def create_bot():
    # Concurrent update workers are required for 65 groups. Per-chat locks in
    # route() retain ordering for each group while allowing other groups and
    # admin commands to continue immediately.
    app=(Application.builder().token(TOKEN).concurrent_updates(128)
         .post_init(post_init).post_shutdown(post_shutdown)
         .connect_timeout(30).read_timeout(30).write_timeout(60).pool_timeout(30)
         .connection_pool_size(128).build())
    app.add_handler(ChatMemberHandler(chat_member_update,ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ALL,route));app.add_error_handler(error)
    return app
