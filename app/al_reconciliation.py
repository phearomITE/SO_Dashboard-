"""One-time reconciliation for explicit historical AL reports shared by admin.

Only validated Employee IDs, dealer codes and dates are accepted. Not a general
source of annual leave and not a replacement for incoming Telegram messages.
"""
import csv
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import select
from app.db import Group, Roster, state, put
from app.bot import save_leave

def apply_reconciliation(s,csv_path):
    path=Path(csv_path)
    if not path.is_file():return 0
    key='al_reconcile:'+path.name
    if state(s,key,{}).get('complete'):return 0
    rows=list(csv.DictReader(path.open(encoding='utf-8-sig',newline='')))
    validated=[]
    for row in rows:
        dt=datetime.strptime(row['business_date'],'%Y-%m-%d')
        groups=list(s.scalars(select(Group).where(Group.code==row['group'],Group.active==1)))
        users=list(s.scalars(select(Roster).where(Roster.employee==row['employee_id'],Roster.active==1)))
        if len(groups)!=1 or len(users)!=1:continue
        roster=users[0]
        if f'{roster.region}-{roster.dealer}'!=row['group']:continue
        if sorted(roster.name.casefold().split())!=sorted(row['name'].casefold().split()):continue
        if not roster.uid:continue
        validated.append((roster,groups[0],dt))
    for roster,group,dt in validated:
        save_leave(s,roster,dt,dt+timedelta(days=1),[group],
                   source='Morning Talk reconciliation (verified Telegram export)',recorded_by=0)
    # Keep unmatched CSV rows reviewable by retrying after future roster setup.
    if len(validated)==len(rows):put(s,key,{'complete':True,'matched':len(validated)})
    return len(validated)
