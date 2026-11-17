from datetime import datetime,timedelta
from html import escape
import random,logging
from sqlalchemy import select
from telegram import ForceReply
from app import db
from app.db import Group,Roster,Controller,Event,Missing,Leave,state,put
from app.config import TEST_MODE,TEST_GROUP_TITLE,WORK_DAYS,SETTLE,ALERT_INTERVAL_MINUTES
from app.rules import windows,is_reporter,normalize_position,person,link_visits
from app.concurrency import chat_lock,scheduler_lock
from app.alerts import send_central_alert
from app.bot_control import automation_enabled,get_bot_control
from app.question_settings import question_enabled

log=logging.getLogger(__name__)
QUESTIONS=[
 'បងមិចយូរទម្លាក់Report ម៉េស?មានបញ្ហាអ្វី?សូមផ្ដល់ចម្លើយផង🙏',
 'បងមានបញ្ហាអ្វីបានមិនទាន់ទម្លាក់Report?សូមបងឆ្លើយប្រាប់មូលហេតុផង🙏',
 'បងសូមជួយបញ្ជាក់មូលហេតុដែលខកខានទម្លាក់Report ផង🙏',
 'បងកំពុងជួបបញ្ហាអ្វីដែរ?សូមឆ្លើយប្រាប់មូលហេតុដែលយឺតទម្លាក់Report ផង🙏',
 'បងថ្ងៃនេះមានបញ្ហាអ្វីបានជាខកខានទម្លាក់Report? សូមជួយប្រាប់មូលហេតុផង🙏',
]

def enabled(g):return g.active and (not TEST_MODE or g.title==TEST_GROUP_TITLE)
def configured_alert_interval(s):
    try:value=int(state(s,'alert_interval_minutes',{}).get('minutes',ALERT_INTERVAL_MINUTES))
    except (TypeError,ValueError):value=ALERT_INTERVAL_MINUTES
    return value if 5<=value<=60 and value%5==0 else ALERT_INTERVAL_MINUTES
def members(s,g):
    result=[];seen=set()
    for r in s.scalars(select(Roster).where(Roster.active==1)):
        # Both field Sales Officers and Sales Supervisors must submit reports.
        if not is_reporter(r.position) or not r.uid:continue
        # A validated move is authoritative even if an old workbook row or a
        # duplicate active identity still contains the previous dealer.
        override=state(s,f'roster_override:{r.uid}',{})
        effective_region=(override.get('region') if override.get('active') else r.region) or ''
        effective_dealer=(override.get('dealer') if override.get('active') else r.dealer) or ''
        if g.test_uids:
            if r.uid not in g.test_uids:continue
        elif f'{effective_region}-{effective_dealer}'.upper()!=g.code:continue
        if state(s,f'excluded:{g.id}:{r.uid}').get('yes'):continue
        if r.uid in seen:continue
        seen.add(r.uid)
        result.append(r)
    return result

def paused(s,g,uid,start,end):
    if s.scalar(select(Leave.id).where(
            Leave.chat==g.id,Leave.uid==uid,Leave.start<end,Leave.end>start)):
        return True
    p=state(s,f'leave:{g.id}:{uid}')
    return bool(p and datetime.fromisoformat(p['start'])<end and datetime.fromisoformat(p['end'])>start)
def mention(uid,name,username=''):
    label='@'+username if username else name
    return f'<a href="tg://user?id={uid}">{escape(label)}</a>'
def controller_text(s,g):
    cs=list(s.scalars(select(Controller).where(Controller.code==g.code)))
    return ', '.join(f'{escape(c.name or str(c.uid))} | {mention(c.uid,c.name or str(c.uid),c.username or "")}' for c in cs) or 'Ra Phearom | @Phearom6996'

async def check_window(app,gid,start,end):
    with db.session() as s:
        if not automation_enabled(s):return
        g=s.get(Group,gid)
        if not g or not enabled(g):return
        es=list(s.scalars(select(Event).where(Event.chat==gid,Event.at>=start.replace(hour=0,minute=0),Event.at<end)))
        visits=link_visits(es)
        # A completed outlet for this deadline requires both pieces of evidence
        # inside the same half-open reporting window. A direct reply must not
        # pull an old GPS pin into a later window.
        submitted={
            v['event'].uid
            for v in visits
            if v['pin']
            and start <= v['event'].at < end
            and start <= v['pin'].at < end
        }
        missing=[]
        for r in members(s,g):
            if r.uid in submitted or paused(s,g,r.uid,start,end):continue
            m=s.scalar(select(Missing).where(Missing.chat==gid,Missing.uid==r.uid,Missing.start==start))
            if m is None:
                m=Missing(chat=gid,uid=r.uid,start=start,end=end,person=person(r));s.add(m);s.flush()
            if not m.alert_mid:missing.append(m)
        # One combined message matches the operating group's established format.
        # The same alert ID is stored on every row so a reply can resolve the
        # correct sender while the follow-up can mention everyone once.
        if missing:
            lines=[]
            for index,m in enumerate(missing,1):
                count=len(list(s.scalars(select(Missing.id).where(Missing.chat==gid,Missing.uid==m.uid,Missing.start>=start.replace(hour=0,minute=0),Missing.start<=start))))
                p=m.person
                lines.append(f'{index}. {escape(normalize_position(p.get("position") or "SO"))} | {escape(p["name"])} | {mention(m.uid,p["name"],p.get("username") or "")} , ខកខានទម្លាក់Report: {count}ដង')
            text=(f'➡️🔴 បងៗមិនទាន់បានទម្លាក់រូបភាពលក់\nDate: {start:%d/%m/%Y}\nGroup: {escape(g.title)}\n'
                  f'Time Submitted: {start:%I:%M %p} - {end:%I:%M %p}\n'
                  'Target បងៗចុះក្នុងមួយថ្ងៃចន្លោះពី 25 ទៅ 30 Outlets\n'
                  f'Controller:{controller_text(s,g)}\n'+'\n'.join(lines))
            try:
                msg=await app.bot.send_message(gid,text,parse_mode='HTML',message_thread_id=g.topic)
                for m in missing:m.alert_mid=msg.message_id;m.sent=db.now()
                s.commit()
                await send_central_alert(app,f'missing:{gid}:{start.isoformat()}',text,
                                         parse_mode='HTML',category='missing_report')
            except Exception:log.exception('Missing alert delivery failed chat=%s',gid)

async def followups(app,at):
    with db.session() as s:
        if not automation_enabled(s):return
        if not question_enabled(s,'missing_report_followup'):return
        items=[m for m in s.scalars(select(Missing).where(Missing.sent<=at-timedelta(minutes=5),Missing.sent>=at.replace(hour=0,minute=0,second=0,microsecond=0),Missing.reason.is_(None),Missing.question_mid.is_(None))) if not paused(s,s.get(Group,m.chat),m.uid,m.start,m.end)]
        groups={}
        for m in items:groups.setdefault((m.chat,m.alert_mid),[]).append(m)
        for (chat_id,alert_mid),group_items in groups.items():
            m=group_items[0]
            g=s.get(Group,m.chat)
            if not g or not enabled(g):continue
            text='\n'.join(mention(x.uid,x.person['name'],x.person.get('username') or '') for x in group_items)+f'\n{random.choice(QUESTIONS)}'
            try:
                msg=await app.bot.send_message(m.chat,text,parse_mode='HTML',message_thread_id=g.topic,
                                               reply_to_message_id=alert_mid,allow_sending_without_reply=True,
                                               reply_markup=ForceReply(selective=True))
                for x in group_items:x.question_mid=msg.message_id
                s.commit()
            except Exception:log.exception('Reason question delivery failed chat=%s',m.chat)

async def tick(context):
    app=context.application;at=db.now()
    if not app.running or not app.updater or not app.updater.running:return
    with db.session() as s:
        if not automation_enabled(s):return
    # Serialize scheduler runs only. Each group uses its own lock so a check in
    # one group cannot freeze commands or ingestion in the other 64 groups.
    async with scheduler_lock(app):
        if at.weekday() in WORK_DAYS:
            with db.session() as s:
                gs=[g.id for g in s.scalars(select(Group)) if enabled(g)]
                interval=configured_alert_interval(s)
            with db.session() as s:control=get_bot_control(s)
            pause_until=control.get('paused_until')
            try:pause_until=datetime.fromisoformat(pause_until) if pause_until else app.bot_data['started']
            except (TypeError,ValueError):pause_until=app.bot_data['started']
            started=max(app.bot_data['started'],app.bot_data.get('alert_interval_changed_at',app.bot_data['started']),pause_until)
            for start,end in windows(at.date(),interval):
                if end+timedelta(seconds=SETTLE)<=at and start>=started:
                    for gid in gs:
                        async with chat_lock(app,gid):
                            key=f'checked:{gid}:{start.isoformat()}'
                            with db.session() as s:done=state(s,key).get('done')
                            if done:continue
                            await check_window(app,gid,start,end)
                            with db.session() as s:
                                pending=s.scalar(select(Missing.id).where(Missing.chat==gid,Missing.start==start,Missing.alert_mid.is_(None)))
                                if pending is None:put(s,key,{'done':True})
        await followups(app,at)
