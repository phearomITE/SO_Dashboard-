"""Persistent global pause switch for automatic bot behaviour.

The Telegram process, commands, ingestion, geocoding, warehouse refreshes and
API remain available while automation is paused. Only unsolicited alerts,
questions and acknowledgements are muted. Report forwarding stays active.
"""
from datetime import datetime,timedelta
from app.db import state,put,now

STATE_KEY='bot_automation_v1'
WORKING_DAYS_KEY='working_days_v1'
KHMER_HOLIDAYS_KEY='khmer_holidays_v1'
WEEKDAY_KEYS=('mon','tue','wed','thu','fri','sat','sun')
DEFAULT_WORKING_DAYS={day:idx<5 for idx,day in enumerate(WEEKDAY_KEYS)}

# Official Cambodia Public Holidays & Khmer Lunar Calendar Holidays (https://khmer-lunar-calendar.com/)
FIXED_KHMER_HOLIDAYS: dict[tuple[int, int], str] = {
    (1, 1): "International New Year Day (ទិវាចូលឆ្នាំសកល)",
    (1, 7): "Victory over Genocide Day (ទិវាជ័យជម្នះលើរបបប្រល័យពូជសាសន៍)",
    (3, 8): "International Women's Day (ទិវានារីអន្តរជាតិ)",
    (4, 14): "Khmer New Year Day 1 (ពិធីបុណ្យចូលឆ្នាំថ្មីប្រពៃណីជាតិ - មហាសង្ក្រាន្ត)",
    (4, 15): "Khmer New Year Day 2 (ពិធីបុណ្យចូលឆ្នាំថ្មីប្រពៃណីជាតិ - វារៈវ័នបត)",
    (4, 16): "Khmer New Year Day 3 (ពិធីបុណ្យចូលឆ្នាំថ្មីប្រពៃណីជាតិ - វារៈឡើងស័ក)",
    (5, 1): "International Labor Day (ទិវាពលកម្មអន្តរជាតិ)",
    (5, 14): "King Norodom Sihamoni's Birthday (ព្រះរាជពិធីបុណ្យចម្រើនព្រះជន្មព្រះមហាក្សត្រ)",
    (6, 18): "Queen Mother's Birthday (ព្រះរាជពិធីបុណ្យចម្រើនព្រះជន្មសម្ដេចម៉ែ)",
    (9, 24): "Constitution Day (ទិវាប្រកាសរដ្ឋធម្មនុញ្ញ)",
    (10, 15): "Commemoration Day of King's Father (ទិវាប្រារព្ធពិធីគោរពព្រះវិញ្ញាណក្ខន្ធព្រះបរមរតនកោដ្ឋ)",
    (10, 29): "King's Coronation Day (ព្រះរាជពិធីគ្រងព្រះបរមរាជសម្បត្តិ)",
    (11, 9): "Independence Day (ទិវាបុណ្យឯករាជ្យជាតិ)",
    (12, 29): "Peace Day in Cambodia (ទិវាសន្តិភាពនៅកម្ពុជា)",
}

# Lunar-dependent Khmer Holidays by Year (Meak Bochea, Visak Bochea, Royal Ploughing, Pchum Ben, Water Festival)
LUNAR_KHMER_HOLIDAYS_BY_YEAR: dict[int, dict[tuple[int, int], str]] = {
    2025: {
        (2, 12): "Meak Bochea Day (ពិធីបុណ្យមាឃបូជា)",
        (5, 11): "Visak Bochea Day (ពិធីបុណ្យវិសាខបូជា)",
        (5, 15): "Royal Ploughing Ceremony (ព្រះរាជពិធីច្រត់ព្រះនង្គ័ល)",
        (9, 21): "Pchum Ben Day 1 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (9, 22): "Pchum Ben Day 2 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (9, 23): "Pchum Ben Day 3 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (11, 4): "Water Festival Day 1 (ព្រះរាជពិធីបុណ្យអុំទូក)",
        (11, 5): "Water Festival Day 2 (ព្រះរាជពិធីបុណ្យអុំទូក)",
        (11, 6): "Water Festival Day 3 (ព្រះរាជពិធីបុណ្យអុំទូក)",
    },
    2026: {
        (3, 3): "Meak Bochea Day (ពិធីបុណ្យមាឃបូជា)",
        (5, 31): "Visak Bochea Day (ពិធីបុណ្យវិសាខបូជា)",
        (6, 4): "Royal Ploughing Ceremony (ព្រះរាជពិធីច្រត់ព្រះនង្គ័ល)",
        (10, 10): "Pchum Ben Day 1 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (10, 11): "Pchum Ben Day 2 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (10, 12): "Pchum Ben Day 3 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (11, 24): "Water Festival Day 1 (ព្រះរាជពិធីបុណ្យអុំទូក)",
        (11, 25): "Water Festival Day 2 (ព្រះរាជពិធីបុណ្យអុំទូក)",
        (11, 26): "Water Festival Day 3 (ព្រះរាជពិធីបុណ្យអុំទូក)",
    },
    2027: {
        (2, 21): "Meak Bochea Day (ពិធីបុណ្យមាឃបូជា)",
        (5, 20): "Visak Bochea Day (ពិធីបុណ្យវិសាខបូជា)",
        (5, 24): "Royal Ploughing Ceremony (ព្រះរាជពិធីច្រត់ព្រះនង្គ័ល)",
        (9, 29): "Pchum Ben Day 1 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (9, 30): "Pchum Ben Day 2 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (10, 1): "Pchum Ben Day 3 (ពិធីបុណ្យភ្ជុំបិណ្ឌ)",
        (11, 13): "Water Festival Day 1 (ព្រះរាជពិធីបុណ្យអុំទូក)",
        (11, 14): "Water Festival Day 2 (ព្រះរាជពិធីបុណ្យអុំទូក)",
        (11, 15): "Water Festival Day 3 (ព្រះរាជពិធីបុណ្យអុំទូក)",
    },
}

def khmer_holiday_name(at=None) -> str | None:
    """Return the Cambodia public holiday name for the given Cambodia date, or None."""
    current = at or now()
    d = current.date() if isinstance(current, datetime) else current
    key = (d.month, d.day)
    if key in FIXED_KHMER_HOLIDAYS:
        return FIXED_KHMER_HOLIDAYS[key]
    year_map = LUNAR_KHMER_HOLIDAYS_BY_YEAR.get(d.year, {})
    return year_map.get(key)

def get_khmer_holidays_pause(s) -> bool:
    saved = state(s, KHMER_HOLIDAYS_KEY, {})
    return bool(saved.get("pause_on_holidays", True))

def set_khmer_holidays_pause(s, pause_on_holidays: bool, changed_by=None, changed_at=None) -> bool:
    payload = {"pause_on_holidays": bool(pause_on_holidays)}
    if changed_by is not None:
        payload["changed_by"] = int(changed_by)
    if changed_at is not None:
        payload["changed_at"] = changed_at.isoformat()
    put(s, KHMER_HOLIDAYS_KEY, payload)
    return bool(pause_on_holidays)

def is_weekend(at=None):
    """Return True on Saturday (5) or Sunday (6) in Cambodia time."""
    current=at or now()
    return current.weekday() in (5,6)

def get_working_days(s):
    saved=state(s,WORKING_DAYS_KEY,{})
    return {day:bool(saved.get(day,DEFAULT_WORKING_DAYS[day])) for day in WEEKDAY_KEYS}

def set_working_days(s,changes,changed_by=None,changed_at=None):
    values=get_working_days(s)
    for day in WEEKDAY_KEYS:
        if day in changes and changes[day] is not None:
            values[day]=bool(changes[day])
    payload=dict(values)
    if changed_by is not None:payload['changed_by']=int(changed_by)
    if changed_at is not None:payload['changed_at']=changed_at.isoformat()
    put(s,WORKING_DAYS_KEY,payload)
    return values

def is_working_day(s,at=None):
    current=at or now()
    day_key=WEEKDAY_KEYS[current.weekday()]
    return get_working_days(s).get(day_key,current.weekday()<5)

def get_bot_control(s):
    saved=state(s,STATE_KEY,{})
    paused_until=saved.get('paused_until')
    current=now()
    try:pause_active=bool(paused_until and datetime.fromisoformat(paused_until)>current)
    except (TypeError,ValueError):pause_active=False
    working_days=get_working_days(s)
    working_day_active=bool(working_days.get(WEEKDAY_KEYS[current.weekday()],current.weekday()<5))
    holiday_pause_enabled=get_khmer_holidays_pause(s)
    holiday_name=khmer_holiday_name(current)
    holiday_muted=bool(holiday_pause_enabled and holiday_name)
    weekend_muted=(not working_day_active) or holiday_muted
    return {
        'enabled':bool(saved.get('enabled',True)) and not pause_active and working_day_active and not holiday_muted,
        'configured_enabled':bool(saved.get('enabled',True)),
        'paused_until':paused_until,
        'pause_active':pause_active,
        'weekend_muted':weekend_muted,
        'working_day_active':working_day_active,
        'working_days':working_days,
        'khmer_holidays_pause':holiday_pause_enabled,
        'holiday_muted':holiday_muted,
        'holiday_name':holiday_name or '',
        'changed_by':saved.get('changed_by'),
        'changed_at':saved.get('changed_at'),
    }

def automation_enabled(s):
    return get_bot_control(s)['enabled']

def set_automation(s,enabled,changed_by,changed_at):
    value={'enabled':bool(enabled),'paused_until':None,'break_hours':None,
           'changed_by':int(changed_by),'changed_at':changed_at.isoformat()}
    put(s,STATE_KEY,value)
    return value

def set_temporary_break(s,hours,changed_by,changed_at):
    """Pause unsolicited bot output and automatically resume after N hours."""
    hours=int(hours)
    if hours not in (1,2):raise ValueError('Bot break must be 1 or 2 hours.')
    paused_until=changed_at+timedelta(hours=hours)
    value={'enabled':True,'paused_until':paused_until.isoformat(),'break_hours':hours,
           'changed_by':int(changed_by),'changed_at':changed_at.isoformat()}
    put(s,STATE_KEY,value)
    return get_bot_control(s)

def bot_status_text(value):
    if value.get('pause_active'):
        until=datetime.fromisoformat(value['paused_until'])
        status='BREAK'
        detail=f'Automatic alerts, questions and acknowledgements are paused until {until:%d/%m/%Y %I:%M %p}.'
    elif value.get('holiday_muted') and value.get('configured_enabled',True):
        status='HOLIDAY MUTED'
        detail=f'Cambodia Public Holiday ({value.get("holiday_name")}): automatic alerts, questions and acknowledgements are paused.'
    elif value.get('weekend_muted') and value.get('configured_enabled',True):
        status='WEEKEND MUTED'
        detail='Saturday & Sunday: automatic alerts, questions and acknowledgements are paused.'
    else:
        status='RUNNING' if value.get('enabled',True) else 'STOPPED'
        detail=('Automatic alerts, questions and acknowledgements are active (Monday–Friday).'
                if status=='RUNNING' else
                'Automatic alerts, questions and acknowledgements are paused until /start_bot is used.')
    return (f'Bot automation: {status}\n{detail}\n'
            'Telegram commands, report forwarding, data collection and dashboard updates stay active.')
