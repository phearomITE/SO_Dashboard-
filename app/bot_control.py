"""Persistent global pause switch for automatic bot behaviour.

The Telegram process, commands, ingestion, geocoding, warehouse refreshes and
API remain available while automation is paused. Only unsolicited alerts,
questions and acknowledgements are muted. Report forwarding stays active.
"""
from datetime import datetime,timedelta
from app.db import state,put,now

STATE_KEY='bot_automation_v1'

def get_bot_control(s):
    saved=state(s,STATE_KEY,{})
    paused_until=saved.get('paused_until')
    try:pause_active=bool(paused_until and datetime.fromisoformat(paused_until)>now())
    except (TypeError,ValueError):pause_active=False
    return {
        'enabled':bool(saved.get('enabled',True)) and not pause_active,
        'configured_enabled':bool(saved.get('enabled',True)),
        'paused_until':paused_until,
        'pause_active':pause_active,
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
    else:
        status='RUNNING' if value.get('enabled',True) else 'STOPPED'
        detail=('Automatic alerts, questions and acknowledgements are active.'
                if status=='RUNNING' else
                'Automatic alerts, questions and acknowledgements are paused until /start_bot is used.')
    return (f'Bot automation: {status}\n{detail}\n'
            'Telegram commands, report forwarding, data collection and dashboard updates stay active.')
