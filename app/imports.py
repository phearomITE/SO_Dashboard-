from datetime import date, datetime, timedelta
from pathlib import Path
import csv
import hashlib
import re
import openpyxl
import warnings
from contextlib import contextmanager

@contextmanager
def readonly_excel_warnings():
    # Ignore only unsupported formatting-extension warnings during read-only
    # extraction. Never save the input workbook; all other warnings stay visible.
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore",message=r"(?:Unknown|Conditional Formatting) extension is not supported and will be removed",category=UserWarning,module=r"openpyxl\.worksheet\._reader")
        yield

def read_only_excel(fn):
    from functools import wraps
    @wraps(fn)
    def wrapped(*args,**kwargs):
        with readonly_excel_warnings():return fn(*args,**kwargs)
    return wrapped

from sqlalchemy import select,delete
from app.db import Roster,Controller,Plan,PlanFile,put,state
from app.rules import is_reporter,normalize_position

def ident(v):return str(int(v)) if isinstance(v,(int,float)) else str(v or '').strip()

EXCLUDED_POSITIONS={'head of sales','area sales manager'}
REMOVED_TELEGRAM_IDS={477167813,424220108,831220255,7166364893}

# The dealer code is the stable business key.  Using this map prevents a typo
# in the Region cell from creating controller access to a non-existent group.
DEALER_REGIONS={
    **dict.fromkeys('CA1 CA2 CA3 CA4 CA5 CA6 CA7 CA8 CA9 PTM6'.split(),'R1'),
    **dict.fromkeys('BTI6 CPH2 KDL1 KHT3 KRV5 KSV5 KTB1 PKB4 PMR5 SVA7 SVR4 TAK2'.split(),'R2'),
    **dict.fromkeys('CUK7 KKG3 KPS7 KPT2 KSM1 KSP3 KTR4 PNP6 SRA5 TPG2'.split(),'R3'),
    **dict.fromkeys('BTB1 BVL6 KCG4 KTL6 MRS5 PLN4 PPT3 PUS5 VVG1'.split(),'R4'),
    **dict.fromkeys('BMC2 CHK6 KRG7 KTH2 KTM8 PPR8 PUK7 SRP1'.split(),'R5'),
    **dict.fromkeys('BTY8 KCM1 MMT3 STT4 TKM2'.split(),'R6'),
    **dict.fromkeys('BKE7 KRT1 KSA5 MDK2 RTK4 SNL6 STR3'.split(),'R7'),
    **dict.fromkeys('AVG4 CKS1 PVH3 SMR5'.split(),'R8'),
}

def excluded_position(value):
    return ' '.join(str(value or '').strip().replace('.','').casefold().split()) in EXCLUDED_POSITIONS

def purge_excluded_roster(s):
    """Delete legacy management rows even when the source-file hash is unchanged."""
    removed=0
    for row in list(s.scalars(select(Roster))):
        if excluded_position(row.position):
            s.delete(row);removed+=1
    return removed

def purge_removed_users(s):
    """Permanently remove blocked users and their operational history."""
    from app.db import Event,Missing,Leave,SilverVisit,Group,State
    employees={str(x) for x in s.scalars(select(Roster.employee).where(Roster.uid.in_(REMOVED_TELEGRAM_IDS))) if x}
    event_ids={str(x) for x in s.scalars(select(Event.id).where(Event.uid.in_(REMOVED_TELEGRAM_IDS)))}
    counts={
        'events':s.execute(delete(Event).where(Event.uid.in_(REMOVED_TELEGRAM_IDS))).rowcount or 0,
        'missing':s.execute(delete(Missing).where(Missing.uid.in_(REMOVED_TELEGRAM_IDS))).rowcount or 0,
        'leave':s.execute(delete(Leave).where(Leave.uid.in_(REMOVED_TELEGRAM_IDS))).rowcount or 0,
        'visits':s.execute(delete(SilverVisit).where(SilverVisit.telegram_id.in_(REMOVED_TELEGRAM_IDS))).rowcount or 0,
        'controllers':s.execute(delete(Controller).where(Controller.uid.in_(REMOVED_TELEGRAM_IDS))).rowcount or 0,
    }
    counts['plans']=s.execute(delete(Plan).where(Plan.employee.in_(employees))).rowcount or 0 if employees else 0
    counts['roster']=s.execute(delete(Roster).where(Roster.uid.in_(REMOVED_TELEGRAM_IDS))).rowcount or 0
    for group in s.scalars(select(Group)):
        if group.test_uids:
            group.test_uids=[uid for uid in group.test_uids if uid not in REMOVED_TELEGRAM_IDS]
    for row in list(s.scalars(select(State))):
        key=str(row.key or '')
        if any(f':{uid}' in key or key.endswith(str(uid)) for uid in REMOVED_TELEGRAM_IDS):s.delete(row)
    counts['source_event_ids']=len(event_ids)
    return counts

def plan_date(value, epoch=None):
    """Return an ISO date from native, text, serial, or simple Excel formula headers."""
    if isinstance(value, datetime):return value.date().isoformat()
    if isinstance(value, date):return value.isoformat()
    if isinstance(value, (int,float)) and 20_000 <= float(value) <= 80_000:
        try:return openpyxl.utils.datetime.from_excel(value, epoch=epoch).date().isoformat()
        except (TypeError,ValueError,OverflowError):return None
    text=str(value or '').strip()
    if not text:return None
    text=re.sub(r'^date\s*[:=]\s*','',text,flags=re.I).strip()
    # Formula workbooks frequently use =DATE(...) or =previous_header+1.
    match=re.fullmatch(r'=DATE\s*\(\s*(\d{4})\s*,\s*(\d{1,2})\s*,\s*(\d{1,2})\s*\)',text,re.I)
    if match:
        try:return date(*map(int,match.groups())).isoformat()
        except ValueError:return None
    text=re.sub(r'^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?:day)?\s*[,\-]?\s*','',text,flags=re.I).strip()
    for fmt in ('%Y-%m-%d','%d/%m/%Y','%d-%m-%Y','%d.%m.%Y','%m/%d/%Y','%d %b %Y','%d %B %Y','%b %d %Y','%B %d %Y'):
        try:return datetime.strptime(text,fmt).date().isoformat()
        except ValueError:pass
    return None

def plan_period(rows):
    """Find the month/year label used by day-number schedule templates."""
    months='Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()
    for row in rows:
        for value in row:
            text=str(value or '').strip()
            # Examples from operational files: WEEK1 , WEEK2_Sep'26, Sep 2026.
            # Month may follow an underscore, as in WEEK2_Sep'26.
            match=re.search(r'(?i)('+'|'.join(months)+r')(?:tember|ober|ember|uary|ruary|ch|il|e|y|ust)?\s*[\'’\-/ ]*\s*(\d{2,4})\b',text)
            if match:
                month=next(i for i,name in enumerate(months,1) if name.lower()==match.group(1)[:3].lower())
                year=int(match.group(2));year=year+2000 if year<100 else year
                return year,month
    return None

def plan_day_number(value):
    """Read a schedule day stored as a number or date-formatted Excel serial."""
    if isinstance(value, datetime) and value.year == 1900 and value.month == 1:
        return value.day
    if isinstance(value, date) and value.year == 1900 and value.month == 1:
        return value.day
    if isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= int(value) <= 31:
        return int(value)
    return None

def plan_table_value(value):
    if value is None:return ''
    if isinstance(value,(datetime,date)):return value.isoformat()
    if isinstance(value,(int,float)) and not isinstance(value,bool):return value
    return str(value).strip()

def canonical_plan_header(value):
    label=' '.join(str(value or '').strip().casefold().replace('_',' ').split())
    aliases={
        'employee id':'employee id','employee code':'employee id','emp id':'employee id',
        'commune':'commune','commune name':'commune',
        'com.code':'com.code','com. code':'com.code','com code':'com.code',
        'commune code':'com.code','commune/code':'com.code',
        'sale name':'sale name','sales name':'sale name',
        'distance from dealer':'dis. from dealer','dis from dealer':'dis. from dealer',
        'dis. from dealer':'dis. from dealer','dis.from dealer':'dis. from dealer',
    }
    return aliases.get(label,label)
@read_only_excel
def read_roster(path):
    w=openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
        result=[]
        for row in w.active.iter_rows(min_row=2,max_col=14,values_only=True):
            uid,_,region,dealer,_,_,username,employee,name,position,*_=row
            if not name or not employee:continue
            if uid and int(uid) in REMOVED_TELEGRAM_IDS:continue
            # Management roles are not monitored as field reporters and must
            # not be loaded into the active bot roster.
            if excluded_position(position):continue
            dealer=str(dealer or '').strip().upper()
            region=DEALER_REGIONS.get(dealer,str(region or '').strip().upper())
            result.append(dict(employee=ident(employee),uid=int(uid) if uid else None,name=str(name).strip(),username=str(username or '').lstrip('@').strip(),region=region,dealer=dealer,position=normalize_position(position)))
        if not result:raise ValueError('Roster has no employee rows.')
        if len({x['employee'] for x in result})!=len(result):raise ValueError('Duplicate employee IDs in roster.')
        return result
    finally:w.close()

@read_only_excel
def read_roster_controllers(path):
    """Return Controller-column access as unique (Telegram ID, group code) pairs."""
    w=openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
        sheet=w.active
        headers=[' '.join(str(v or '').strip().casefold().split()) for v in next(sheet.iter_rows(min_row=1,max_row=1,values_only=True))]
        required=('region','dealer','controller')
        if not all(name in headers for name in required):return set()
        ri,di,ci=(headers.index(name) for name in required)
        required_index=max(ri,di,ci)
        result=set()
        for row in sheet.iter_rows(min_row=2,values_only=True):
            # Excel tables can retain formatted/blank trailing rows. openpyxl
            # may return those rows as a short tuple, so skip them before
            # indexing the Region, Dealer and Controller columns.
            if len(row)<=required_index:continue
            dealer=str(row[di] or '').strip().upper()
            region=DEALER_REGIONS.get(dealer,str(row[ri] or '').strip().upper())
            if not dealer or not re.fullmatch(r'R[1-8]',region):continue
            raw=str(row[ci] or '').strip()
            for token in re.findall(r'\d+',raw):
                uid=int(token)
                if uid>0:result.add((uid,f'{region}-{dealer}'))
        return result
    finally:w.close()

def sync_roster_controllers(s,path):
    """Synchronize only workbook-owned controller access; preserve manual assignments."""
    desired=read_roster_controllers(path)
    previous={tuple(x) for x in state(s,'roster_controller_assignments',{}).get('pairs',[])}
    for uid,code in previous-desired:
        s.execute(delete(Controller).where(Controller.uid==uid,Controller.code==code))
    for uid,code in sorted(desired):
        existing=s.scalar(select(Controller).where(Controller.uid==uid,Controller.code==code))
        if not existing:s.add(Controller(uid=uid,code=code,name=str(uid),username=''))
    put(s,'roster_controller_assignments',{'pairs':[list(x) for x in sorted(desired)]})
    return len({uid for uid,_ in desired}),len(desired)

def import_roster(s,path):
    rows=read_roster(path)
    # Dashboard-deleted users remain excluded on future uploads until restored.
    removed=set(state(s,'roster_dashboard_deleted_ids',{}).get('employee_ids',[]))
    rows=[data for data in rows if data['employee'] not in removed]
    if not rows:raise ValueError('Workbook contains no non-deleted eligible employees.')
    for data in rows:
        r=s.scalar(select(Roster).where(Roster.employee==data['employee']))
        if r:
            for k,v in data.items():
                if k=='username' and not v:continue
                setattr(r,k,v)
        else:s.add(Roster(**data))
        # An SO may first be registered manually as manual:<telegram_id> and
        # later appear in the official workbook with the real Employee ID.
        # Retire that duplicate so Telegram activity uses the official record.
        if data.get('uid'):
            for duplicate in s.scalars(select(Roster).where(Roster.uid==data['uid'],Roster.employee!=data['employee'])):
                if duplicate.source=='manual' or str(duplicate.employee or '').startswith('manual:'):
                    duplicate.active=0
    # Preserve manual rows and permanent removals; retire employees absent in new official roster.
    keep={x['employee'] for x in rows}
    # Keep an exact employee-ID snapshot of the latest official workbook so
    # the dashboard can distinguish workbook members from new joins even when
    # an existing employee's operational source becomes "manual" after edits.
    put(s,'roster_master_employee_ids',{'employee_ids':sorted(keep)})
    for r in s.scalars(select(Roster).where(Roster.source=='excel')):
        if r.employee not in keep:r.active=0
    # A validated dealer transfer is an operational override. Re-apply it
    # after every official workbook import so /update_list_so cannot silently
    # move the user back to the old dealer. Historical facts remain unchanged.
    for r in s.scalars(select(Roster).where(Roster.active==1,Roster.uid.is_not(None))):
        override=state(s,f'roster_override:{r.uid}',{})
        if override.get('active') and override.get('region') and override.get('dealer'):
            r.region=str(override['region']).strip().upper()
            r.dealer=str(override['dealer']).strip().upper()
            r.source='manual'
    # Management records imported by an older bot version must be removed,
    # not merely marked inactive, so they cannot appear in roster exports.
    purge_excluded_roster(s)
    controller_count,controller_groups=sync_roster_controllers(s,path)
    return len(rows),sum(is_reporter(x['position']) for x in rows),controller_count,controller_groups

@read_only_excel
def read_dealer_locations(path):
    """Read dealer road-routing origins from the sales-coverage workbook."""
    w=openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
        sheet=w.active;rows=[]
        headers=[str(v or '').strip().casefold() for v in next(sheet.iter_rows(min_row=1,max_row=1,values_only=True))]
        required={'region code','dealer code','latitude','longitude'}
        if not required.issubset(headers):raise ValueError('Dealer location headers must include REGION CODE, DEALER CODE, Latitude and Longitude.')
        index={name:headers.index(name) for name in required}
        remark=headers.index('remark') if 'remark' in headers else None
        for source_row,row in enumerate(sheet.iter_rows(min_row=2,values_only=True),2):
            region=str(row[index['region code']] or '').strip().upper()
            dealer=str(row[index['dealer code']] or '').strip().upper()
            if not region and not dealer:continue
            # The location export contains legacy region assignments for some
            # stations. Dealer code is the stable key used by all 65 groups.
            region=DEALER_REGIONS.get(dealer,region)
            try:lat=float(row[index['latitude']]);lon=float(row[index['longitude']])
            except (TypeError,ValueError):raise ValueError(f'Invalid dealer coordinates on row {source_row}.')
            if not (-90<=lat<=90 and -180<=lon<=180):raise ValueError(f'Out-of-range dealer coordinates on row {source_row}.')
            if not re.fullmatch(r'R[1-8]',region) or not re.fullmatch(r'[A-Z0-9]+',dealer):raise ValueError(f'Invalid region/dealer code on row {source_row}.')
            rows.append({'code':f'{region}-{dealer}','lat':lat,'lon':lon,'remark':str(row[remark] or '').strip() if remark is not None else '','source_row':source_row})
        if not rows:raise ValueError('Dealer location workbook has no usable rows.')
        return rows
    finally:w.close()

def read_dealer_station_csv(path):
    """Read the GIS CSV export containing one POINT block per dealer.

    The supplied export uses four physical rows per station. Region values in
    older exports are corrected from the canonical 65-dealer mapping.
    """
    with Path(path).open('r',encoding='utf-8-sig',newline='') as source:
        raw=list(csv.reader(source))
    rows=[]
    for index,row in enumerate(raw):
        if index==0 or len(row)<2 or not str(row[0] or '').strip().upper().startswith('POINT'):continue
        dealer=str(row[1] or '').strip().upper()
        match=re.search(r'POINT\s*\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)',str(row[0]),re.I)
        if not dealer or not match:continue
        lon=float(match.group(1));lat=float(match.group(2));region=''
        for follow in raw[index+1:index+4]:
            for cell in follow:
                value=str(cell or '').strip()
                if value.casefold().startswith('region:'):region=value.split(':',1)[1].strip().upper()
        region=DEALER_REGIONS.get(dealer,region)
        if dealer not in DEALER_REGIONS:continue
        if not (9.80<=lat<=14.75 and 102.30<=lon<=107.70):
            raise ValueError(f'Dealer {dealer} coordinate is outside Cambodia.')
        rows.append({'code':f'{region}-{dealer}','lat':lat,'lon':lon,'remark':'','source_row':index+1})
    expected={f'{region}-{dealer}' for dealer,region in DEALER_REGIONS.items()}
    found={row['code'] for row in rows}
    missing=sorted(expected-found)
    if missing:raise ValueError(f'Dealer station CSV is incomplete: {len(found)}/65 dealers. Missing: {", ".join(missing)}')
    return rows

def import_dealer_locations(s,path):
    """Load one primary routing origin per dealer; non-WH sales coverage wins."""
    rows=read_dealer_station_csv(path) if Path(path).suffix.casefold()=='.csv' else read_dealer_locations(path)
    groups={}
    for row in rows:groups.setdefault(row['code'],[]).append(row)
    for code,options in groups.items():
        ordered=sorted(options,key=lambda x:(str(x['remark']).upper().startswith('WH_'),x['source_row']))
        primary=ordered[0]
        put(s,f'dealer_origin:{code}',{**primary,'source_file':Path(path).name,'alternatives':len(options)})
        put(s,f'dealer_origins:{code}',{'locations':ordered,'source_file':Path(path).name})
    return len(rows),len(groups)

@read_only_excel
def read_plan(path,include_dates=False,include_grid=False):
    w=openpyxl.load_workbook(path,read_only=True,data_only=True)
    formulas=openpyxl.load_workbook(path,read_only=True,data_only=False)
    try:
        result=[];grid_rows=[];grid_columns=[];covered_dates=set()
        formula_sheets={s.title:s for s in formulas}
        for sheet in w:
            # The supplied workbook has 16,339 formatted columns. Read only the first 100.
            it=sheet.iter_rows(max_col=100,values_only=True)
            fit=formula_sheets[sheet.title].iter_rows(max_col=100,values_only=True)
            header=None; dates={};carry={};health_columns=[]
            preamble=[]
            for row_number,(row,frow) in enumerate(zip(it,fit),1):
                if header is None:
                    preamble.append(row)
                    labels=[canonical_plan_header(v) for v in row]
                    if 'employee id' not in labels or 'commune' not in labels:continue
                    header={v:i for i,v in enumerate(labels) if v}
                    health_columns=[i for i,label in enumerate(labels) if label=='market health']
                    # Operational templates sometimes store only day numbers in
                    # date-formatted cells. openpyxl then reports 1900 dates;
                    # those are placeholders, not valid schedule dates. Resolve
                    # them from the workbook period and weekday row below.
                    dates={i:d for i,(v,fv) in enumerate(zip(row,frow))
                           if (d:=plan_date(v,sheet.parent.epoch) or plan_date(fv,sheet.parent.epoch))
                           and 2000 <= int(d[:4]) <= 2100}
                    # Resolve simple relative date formulas such as =J7+1.
                    for i,value in enumerate(frow):
                        m=re.fullmatch(r'=\$?([A-Z]{1,3})\$?(\d+)\s*\+\s*(\d+)',str(value or '').strip(),re.I)
                        if not m:continue
                        source=openpyxl.utils.column_index_from_string(m.group(1))-1
                        if source in dates:
                            dates[i]=(datetime.fromisoformat(dates[source])+timedelta(days=int(m.group(3)))).date().isoformat()
                    # Some business plan files put only day-of-month numbers in
                    # the header and keep the period in a title like
                    # WEEK1 , WEEK2_Sep'26. Build consecutive dates, including
                    # month rollover (30 Sep -> 1 Oct), from that period.
                    if not dates and (period:=plan_period(preamble)):
                        year,month=period;previous=None
                        schedule_start=max(header[k] for k in ('employee id','commune'))+1
                        day_candidates=[(i,day_number) for i,v in enumerate(row)
                                        if i>=schedule_start and (day_number:=plan_day_number(v))]
                        weekday_labels={'mon','monday','tue','tuesday','wed','wednesday',
                                        'thu','thursday','fri','friday','sat','saturday','sun','sunday'}
                        numbered=[(i,day_number) for i,day_number in day_candidates
                                  if str(sheet.cell(row_number+1,i+1).value or '').strip().casefold() in weekday_labels]
                        if not numbered:numbered=day_candidates
                        schedule_columns={i for i,_ in numbered}
                        # The period title can name the ending month. Use the
                        # weekday row under the first day to distinguish it from
                        # a title naming the starting month.
                        if numbered:
                            first_col,first_day=numbered[0]
                            weekday=str(sheet.cell(row_number+1,first_col+1).value or '')[:3].lower()
                            previous_month=12 if month==1 else month-1
                            previous_year=year-1 if month==1 else year
                            try:
                                current=date(year,month,first_day).strftime('%a').lower()
                                prior=date(previous_year,previous_month,first_day).strftime('%a').lower()
                                if weekday and prior==weekday and current!=weekday:year,month=previous_year,previous_month
                            except ValueError:pass
                        for i,value in enumerate(row):
                            if i not in schedule_columns:continue
                            day=plan_day_number(value)
                            if day is None:continue
                            if previous is not None and day < previous:
                                month+=1
                                if month==13:month=1;year+=1
                            try:dates[i]=date(year,month,day).isoformat()
                            except ValueError:continue
                            previous=day
                    if not dates:raise ValueError('Plan date headers were not recognized. Supported: real Excel dates, DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD, English dates, Excel serial dates, =DATE(...), and previous-date + 1 formulas.')
                    covered_dates.update(dates.values())
                    base_columns=[
                        ('region','Region'),('dealer','Dealer'),('employee id','Employee ID'),
                        ('sale name','Sale Name'),('district','District'),('com.code','Com.Code'),
                        ('commune','Commune Name'),('dis. from dealer','Dis. From Dealer'),
                        ('population','Population'),('grade','Grade'),('#market','#Market'),
                        ('#factory','#Factory'),('key location','Key Location'),
                    ]
                    # Keep the core plan columns visible and in a predictable
                    # order even when a workbook uses a slightly different
                    # spelling or omits a field. Missing source values render
                    # blank; available fields are still read from the upload.
                    sheet_columns=[{'key':key,'label':label,'type':'data'} for key,label in base_columns]
                    for health_number,col in enumerate(health_columns,1):
                        period=str(sheet.cell(row_number-1,col+1).value or '').strip() if row_number>1 else ''
                        period=re.sub(r'\s+',' ',period)
                        label=f'Market Health ({period})' if period else f'Market Health {health_number}'
                        sheet_columns.append({'key':f'market_health_{health_number}','label':label,'type':'data'})
                    extra_columns=[
                        ('#ws/ds','#WS/DS'),('#horeca outlet','#HORECA Outlet'),
                    ]
                    sheet_columns.extend({'key':key,'label':label,'type':'data'} for key,label in extra_columns)
                    sheet_columns.extend({'key':day,'label':day[-2:],'weekday':datetime.fromisoformat(day).strftime('%a'),'full_date':day,'type':'schedule'} for day in sorted(set(dates.values())))
                    summary_columns=[
                        ('emergency outlet','Emergency Outlet'),('tour outlet','Tour Outlet'),
                        ('hub water','Hub Water'),('key ws','Key WS'),('24h outlet','24H Outlet'),
                        ('village outlet','Village Outlet'),
                    ]
                    sheet_columns.extend({'key':key,'label':label,'type':'data'} for key,label in summary_columns if key in header)
                    if not grid_columns:grid_columns=sheet_columns
                    continue
                def get(key):return row[header[key]] if key in header else None
                # Do not carry a commune across rows; only merged employee identity fields.
                for k in ['region','dealer','employee id','sale name']:
                    v=get(k)
                    if v is not None:carry[k]=v
                employee=ident(carry.get('employee id'))
                commune=get('commune')
                if not employee or not commune:continue
                values={}
                for column in sheet_columns:
                    if column['type']!='data':continue
                    key=column['key']
                    if key.startswith('market_health_'):
                        health_index=int(key.rsplit('_',1)[-1])-1
                        cell_index=health_columns[health_index] if health_index<len(health_columns) else None
                    else:cell_index=header.get(key)
                    values[key]=plan_table_value(row[cell_index]) if cell_index is not None and cell_index<len(row) else ''
                values['region']=str(carry.get('region','')).strip()
                values['dealer']=str(carry.get('dealer','')).strip()
                values['employee id']=employee
                values['sale name']=str(carry.get('sale name','')).strip()
                values['district']=plan_table_value(get('district'))
                values['com.code']=ident(get('com.code'))
                values['commune']=plan_table_value(commune)
                values['dis. from dealer']=plan_table_value(get('dis. from dealer'))
                grid_rows.append({'source_sheet':sheet.title,'source_row':row_number,'values':values,
                                  'schedule':{day:plan_table_value(row[col]) for col,day in dates.items()}})
                for col,day in dates.items():
                    mark=str(row[col] or '').strip()
                    if mark.lower() in ('','0','no','off','-','al'):continue
                    result.append(dict(employee=employee,day=day,data={'region':values['region'],'dealer':values['dealer'],'sale_name':values['sale name'],'district':values['district'],'commune':values['commune'],'commune_code':ident(get('com.code')),'mark':mark,'excel_values':values,'source_sheet':sheet.title,'source_row':row_number}))
        if not result:raise ValueError('No scheduled commune visits found. Existing plan is unchanged.')
        if include_grid:return result,covered_dates,grid_rows,grid_columns
        return (result,covered_dates) if include_dates else result
    finally:w.close();formulas.close()

def import_plan(s,path,admin,source_name=None):
    rows,covered_dates,grid_rows,grid_columns=read_plan(path,include_dates=True,include_grid=True);batch=hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if not s.scalar(select(Plan.id).where(Plan.batch==batch).limit(1)):
        s.add_all([Plan(batch=batch,**r) for r in rows])
    if not s.scalar(select(PlanFile.id).where(PlanFile.batch==batch).limit(1)):
        filename=Path(source_name or path).name
        s.add(PlanFile(batch=batch,filename=filename,content=Path(path).read_bytes(),uploaded_at=datetime.now(),admin=admin))
    # A successful upload becomes the one authoritative active plan. Historical
    # batches remain archived for audit, but missing dates must not inherit an
    # older workbook silently.
    versions={day:batch for day in covered_dates}
    put(s,'plan_dates',versions)
    put(s,'plan_latest',{'batch':batch,'admin':admin,'uploaded_at':datetime.now().isoformat(),'rows':len(rows),'commune_rows':len(grid_rows),'min_date':min(covered_dates),'max_date':max(covered_dates),'filename':Path(source_name or path).name})
    return len(rows)
