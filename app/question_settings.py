"""Persistent global switches for user-facing bot questions.

Operational alerts, event collection, analysis, and warehouse refreshes remain
active when a question type is disabled. Missing-report follow-up questions
can be controlled independently with /stop_ask and /start_ask.
"""
from app.db import state,put

STATE_KEY='question_settings_v1'
DEFAULTS={
    'wrong_plan':True,
    'over_10km':True,
    'reason_acknowledgement':True,
    'missing_report_followup':True,
}

def get_question_settings(s):
    saved=state(s,STATE_KEY,{})
    return {key:bool(saved.get(key,default)) for key,default in DEFAULTS.items()}

def question_enabled(s,key):
    return get_question_settings(s).get(key,True)

def update_question_settings(s,changes,changed_by=None):
    values=get_question_settings(s)
    values.update({key:bool(value) for key,value in changes.items() if key in DEFAULTS})
    if changed_by is not None:values['changed_by']=int(changed_by)
    put(s,STATE_KEY,values)
    return values

def status_text(values,group_count):
    flag=lambda key:'ON' if values.get(key,True) else 'OFF'
    return (
        f'Scope: All {group_count} registered groups (global admin via bot chat)\n'
        f'Wrong Plan questions: {flag("wrong_plan")}\n'
        f'Over-10 km questions: {flag("over_10km")}\n'
        f'Reason acknowledgements: {flag("reason_acknowledgement")}\n'
        f'Missing-report follow-up questions: {flag("missing_report_followup")}'
    )
