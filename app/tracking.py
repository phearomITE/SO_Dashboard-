import asyncio
import html
import logging
import random
import re
from datetime import datetime,timedelta
from pathlib import Path
from sqlalchemy import select
from app import db
from app.config import ADMINS,ROUTING_ALERT_KM
from app.alerts import send_central_alert
from app.question_settings import question_enabled
from app.bot_control import automation_enabled
from app.db import Event,Group,state,put
from app.geo import is_cambodia_coordinate,locate,plan_check,plan_locations,boundary_center,road_distance_km

log=logging.getLogger(__name__)

WRONG_PLAN_QUESTIONS=[
    'បងមិចថ្ងៃនិងបងចុះខុសPlan ទៅឃុំ {commune} ស្រុក {district}???',
    'បងថ្ងៃនេះក្នុងPlan ត្រូវចុះឃុំ {commune} ស្រុក {district} តើហេតុអ្វីបងទៅឃុំផ្សេង?',
    'បងសូមជួយបញ្ជាក់មូលហេតុ ដែលថ្ងៃនេះបងCheck-In មិនត្រូវតាមPlan ឃុំ {commune} ស្រុក {district} ផង🙏',
    'បងមានការប្ដូរPlan ឬមានបញ្ហាអ្វី ទើបបងមិនបានចុះឃុំ {commune} ស្រុក {district} តាមកាលវិភាគ?',
    'បងCheck-In ខុសពីឃុំ {commune} ស្រុក {district} ក្នុងPlan ថ្ងៃនេះ សូមប្រាប់មូលហេតុផង🙏',
]
OVER_10KM_QUESTIONS=[
    'បងមិចថ្ងៃនិងបងជិះទៅOutletឆ្ងាយៗមេសបង ឡើងលើស 10Km ហើយ?',
    'បងសូមជួយបញ្ជាក់មូលហេតុ ដែលCheck-In លើកនេះឆ្ងាយពីលើកមុនលើស 10Km ផង🙏',
    'បងមានបញ្ហាអ្វី ឬប្ដូរផ្លូវចុះOutlet ទើបចម្ងាយCheck-In លើស 10Km?',
    'បងហេតុអ្វីបានជាចុះOutlet បន្ទាប់ឆ្ងាយជាង 10Km ពីOutlet មុន? សូមជួយប្រាប់មូលហេតុផង🙏',
    'បងចម្ងាយពីCheck-In មុនមកCheck-In នេះលើស 10Km សូមបញ្ជាក់ថាបងបានទៅណា ឬមានការប្ដូរPlan អ្វីដែរ?',
]

PROVINCES={'01':'Banteay Meanchey','02':'Battambang','03':'Kampong Cham','04':'Kampong Chhnang','05':'Kampong Speu','06':'Kampong Thom','07':'Kampot','08':'Kandal','09':'Koh Kong','10':'Kratie','11':'Mondulkiri','12':'Phnom Penh','13':'Preah Vihear','14':'Prey Veng','15':'Pursat','16':'Ratanakiri','17':'Siem Reap','18':'Preah Sihanouk','19':'Stung Treng','20':'Svay Rieng','21':'Takeo','22':'Oddar Meanchey','23':'Kep','24':'Pailin','25':'Tboung Khmum'}

def location_text(geo):
    code=str(geo.get('commune_code') or '')
    return f'{geo.get("province") or PROVINCES.get(code.zfill(6)[:2],"")} / {geo.get("district","")} / {geo.get("commune","")} ({code})'

def planned_text(rows):
    if not rows:return 'Not scheduled'
    values=[]
    for row in rows:
        code=str(row.get('commune_code') or '')
        value=f'{PROVINCES.get(code.zfill(6)[:2],"")} / {row.get("district","")} / {row.get("commune","")} ({code})'
        if value not in values:values.append(value)
    return '; '.join(values)

KHMER_WEEKDAYS={0:'ថ្ងៃចន្ទ',1:'ថ្ងៃអង្គារ',2:'ថ្ងៃពុធ',3:'ថ្ងៃព្រហស្បតិ៍',4:'ថ្ងៃសុក្រ',5:'ថ្ងៃសៅរ៍',6:'ថ្ងៃអាទិត្យ'}

def plan_period_label(filename):
    stem=Path(str(filename or '')).stem
    return re.sub(r'^Plan[_ ]*SO[_ \-]*','',stem,flags=re.I).strip() or stem or 'Current Plan'

def plan_date_text(day,rows):
    when=KHMER_WEEKDAYS[day.weekday()]+' '+day.strftime('%d/%m/%Y')
    locations=[]
    for row in rows:
        value=f'នៅឃុំ {row.get("commune","")} ស្រុក {row.get("district","")}'
        if value not in locations:locations.append(value)
    return when+(' '+('; '.join(locations)) if locations else ' មិនមានកាលវិភាគ')

def telegram_link(chat_id,message_id):
    raw=str(chat_id)
    return f'https://t.me/c/{raw[4:]}/{int(message_id)}' if raw.startswith('-100') and message_id else ''

async def send_admin_alert(application,key,text,parse_mode=None,category=None):
    """Deliver to global admins and mirror to Group_SO_Controll."""
    with db.session() as s:
        if not automation_enabled(s):return
    for admin_id in sorted(ADMINS):
        unique=f'{key}:{admin_id}'
        with db.session() as s:
            if state(s,unique,{}).get('sent'):continue
        try:
            await application.bot.send_message(admin_id,text,disable_web_page_preview=True,parse_mode=parse_mode)
            with db.session() as s:put(s,unique,{'sent':True,'at':db.now().isoformat()})
        except Exception:
            log.exception('Operational admin alert failed admin=%s key=%s',admin_id,key)
    await send_central_alert(application,key,text,parse_mode=parse_mode,category=category)

async def send_group_question(application,event_id,category,questions,values=None):
    """Ask the SO/SS in the reporting group and persist an exact reply target."""
    field=f'{category}_question_mid'
    with db.session() as s:
        if not automation_enabled(s):return
        if not question_enabled(s,category):return
        event=s.get(Event,event_id)
        if not event or event.data.get(field):return
        group=s.get(Group,event.chat)
        person=dict(event.data.get('person') or {})
        question=random.choice(questions).format(**(values or {}))
        mention=f'@{person.get("username")}' if person.get('username') else person.get('name') or str(event.uid)
        chat_id=event.chat;reply_mid=event.mid;thread=group.topic if group else None
    kwargs={'reply_to_message_id':reply_mid,'allow_sending_without_reply':True}
    if thread is not None:kwargs['message_thread_id']=thread
    try:
        sent=await application.bot.send_message(chat_id,f'{mention}\n➡️ {question}',**kwargs)
    except Exception:
        log.exception('Follow-up question failed event=%s category=%s',event_id,category);return
    with db.session() as s:
        event=s.get(Event,event_id)
        if event and not event.data.get(field):
            event.data={**event.data,field:sent.message_id,f'{category}_question_at':db.now().isoformat()}

async def analyze_pin_event(application,event_id):
    """Check plan compliance and calculate sequential road distance for one pin."""
    analysis_lock=application.bot_data.setdefault('route_analysis_lock',asyncio.Lock())
    routing_lock=application.bot_data.setdefault('routing_lock',asyncio.Lock())
    routing_last=application.bot_data.setdefault('routing_last',[0.0])
    async with analysis_lock:
        with db.session() as s:
            event=s.get(Event,event_id)
            if not event or event.kind!='pin':return
            data=dict(event.data);person=dict(data.get('person') or {});saved_route=dict(data.get('route') or {})
            lat,lon=data.get('lat'),data.get('lon')
            if lat is None or lon is None:return
            if not is_cambodia_coordinate(lat,lon):
                event.data={**event.data,'analysis_status':'Quarantined: coordinate outside Cambodia'}
                return
            geo=data.get('geo') or locate(lat,lon)
            group=s.get(Group,event.chat)
            title=group.title if group else str(event.chat)
            status,plan_issue=plan_check(s,person.get('employee',''),event.at.date().isoformat(),geo,person.get('name',''))
            planned=plan_locations(s,person.get('employee',''),event.at.date().isoformat(),person.get('name',''))
            start=event.at.replace(hour=0,minute=0,second=0,microsecond=0);end=start+timedelta(days=1)
            pins=list(s.scalars(select(Event).where(Event.chat==event.chat,Event.uid==event.uid,Event.kind=='pin',Event.at>=start,Event.at<end)))
            pins.sort(key=lambda x:(x.at,x.mid))
            position=next((i for i,x in enumerate(pins) if x.id==event.id),len(pins)-1)
            previous=pins[position-1] if position>0 else None
            code=f'{person.get("region","")}-{person.get("dealer","")}'.upper().strip('-')
            origin=state(s,f'dealer_origin:{code}',{}) if code else {}
            plan_latest=state(s,'plan_latest',{})
            event_at=event.at;event_mid=event.mid;event_chat=event.chat
            previous_snapshot=(previous.at,previous.mid,dict(previous.data)) if previous else None

        route_kind='Dealer to First Check-in' if previous_snapshot is None else 'Previous Check-in to Current Check-in'
        route_from='';distance=None;route_issue='';route_geometry=[]
        if previous_snapshot:
            previous_at,previous_mid,previous_data=previous_snapshot
            start_lat,start_lon=previous_data.get('lat'),previous_data.get('lon')
            route_from=f'Check-in #{position}'
        elif origin.get('lat') is not None and origin.get('lon') is not None:
            start_lat,start_lon=origin['lat'],origin['lon'];route_from=f'Dealer {code}'
        else:
            start_lat=start_lon=None;route_issue='Dealer origin not configured'
        # A completed route is authoritative even when an older record or a
        # test fixture has no stored geometry.  Retrying analysis must not call
        # OSRM again, change a previously delivered distance, or duplicate an
        # alert. New pins always persist geometry when routing provides it.
        if saved_route.get('kind')==route_kind and saved_route.get('distance_km') is not None:
            distance=saved_route['distance_km'];route_issue=saved_route.get('issue','');route_geometry=saved_route.get('geometry') or []
        elif start_lat is not None and start_lon is not None:
            route_result=await road_distance_km(start_lat,start_lon,lat,lon,routing_lock,routing_last,include_geometry=True)
            distance,route_issue=route_result[:2]
            route_geometry=route_result[2] if len(route_result)>2 else []
        route_status=('Unavailable' if distance is None else ('Over limit' if previous_snapshot and distance>ROUTING_ALERT_KM else 'OK'))
        route={'kind':route_kind,'from':route_from,'sequence':position+1,'distance_km':distance,'threshold_km':ROUTING_ALERT_KM,'status':route_status,'issue':route_issue,'geometry':route_geometry}
        with db.session() as s:
            current=s.get(Event,event_id)
            if not current:return
            current.data={**current.data,'geo':geo,'plan_check':status,'plan_issue':plan_issue,'route':route}

        name=person.get('name') or str(event.uid);username=person.get('username') or ''
        position_label=str(person.get('position') or 'SO').upper()
        who=f'{name} | @{username}' if username else f'{name} | ID {event.uid}'
        map_url=f'https://maps.google.com/maps?q={lat},{lon}'
        message_url=telegram_link(event_chat,event_mid)
        # Never spam retroactive alerts for historical pins older than 45 minutes during startup backfill.
        if abs((db.now()-event_at).total_seconds())>2700 and event_at.date()<db.now().date():
            return
        if status=='Incorrect':
            center=boundary_center(planned[0].get('commune_code',''),planned[0].get('commune','')) if planned else None
            plan_map=f'https://maps.google.com/maps?q={center[0]:.6f},{center[1]:.6f}' if center else 'Unavailable'
            label=plan_period_label(plan_latest.get('filename',''))
            text=(f'🚨 Wrong Plan Check-In ( {label})\nDate: {event_at:%d/%m/%Y}\nGroup: {title}\n'
                  f'{position_label}: {who}\nEmployee ID: {person.get("employee","")}\nDealer: {code}\n'
                  f'Actual: {location_text(geo)}\nPlan: {planned_text(planned)}\n'
                  f'Plan Check: Incorrect\nDate Plan: {plan_date_text(event_at.date(),planned)}\nPlan Map: {plan_map}')
            if message_url:text+=f'\nTelegram: {message_url}'
            await send_admin_alert(application,f'plan_alert:{event_id}',text,category='wrong_plan')
            planned_first=planned[0] if planned else {}
            await send_group_question(application,event_id,'wrong_plan',WRONG_PLAN_QUESTIONS,
                                      {'commune':planned_first.get('commune') or 'មិនមានក្នុងPlan',
                                       'district':planned_first.get('district') or 'មិនមានក្នុងPlan'})
        if previous_snapshot is None:
            dealer_geo=locate(origin['lat'],origin['lon']) if origin.get('lat') is not None and origin.get('lon') is not None else {}
            def khmer_route_location(value):
                commune=html.escape(str(value.get('commune') or 'មិនស្គាល់'))
                district=html.escape(str(value.get('district') or 'មិនស្គាល់'))
                province=html.escape(str(value.get('province') or 'មិនស្គាល់'))
                return f'<b>ឃុំ {commune}</b> ស្រុក {district} ខេត្ត {province}'
            dealer_map=(f'https://maps.google.com/maps?q={origin["lat"]},{origin["lon"]}'
                        if origin.get('lat') is not None and origin.get('lon') is not None else 'Unavailable')
            checkin_map=f'https://maps.google.com/?q={lat},{lon}'
            distance_text=f'{distance:.2f} km' if distance is not None else 'Unavailable'
            text=(f'🚨 Dealer to First Check-In Road Distance\nDate: {event_at:%d/%m/%Y}\nGroup: {title}\n'
                  f'Time: {event_at:%I:%M %p}\n{position_label}: {html.escape(who)}\n'
                  f'Employee ID: {html.escape(str(person.get("employee", "")))}\nDealer: {html.escape(code)}\n'
                  f'Leg: Dealer → Check-in #1\nRoad Distance: {distance_text}\n'
                  f'From Dealer: {khmer_route_location(dealer_geo)}\nLink Map: {dealer_map}\n'
                  f'To First Check In: {khmer_route_location(geo)}\nLink Map: {checkin_map}')
            if distance is None:text+=f'\nRouting Status: {html.escape(route_issue or "Road route unavailable")}'
            if message_url:text+=f'\nTo Telegram: {message_url}'
            await send_admin_alert(application,f'dealer_first_alert:{event_id}',text,parse_mode='HTML',category='distant_dealer')
        if previous_snapshot and distance is not None and distance>ROUTING_ALERT_KM:
            previous_at,previous_mid,previous_data=previous_snapshot
            previous_link=telegram_link(event_chat,previous_mid)
            previous_geo=previous_data.get('geo') or locate(previous_data.get('lat'),previous_data.get('lon'))
            text=(f'🚨 Check-In Road Distance Over {ROUTING_ALERT_KM:g} km\nDate: {event_at:%d/%m/%Y}\nGroup: {title}\n'
                  f'{position_label}: {who}\nEmployee ID: {person.get("employee","")}\nDealer: {code}\n'
                  f'Leg: Check-in #{position} → Check-in #{position+1}\nRoad Distance: {distance:.2f} km\n'
                  f'From: {previous_at:%I:%M %p} | {location_text(previous_geo)}\n'
                  f'To: {event_at:%I:%M %p} | {location_text(geo)}')
            if previous_link:text+=f'\nFrom Telegram: {previous_link}'
            if message_url:text+=f'\nTo Telegram: {message_url}'
            await send_admin_alert(application,f'distance_alert:{event_id}',text,category='over_10km')
            await send_group_question(application,event_id,'over_10km',OVER_10KM_QUESTIONS)
