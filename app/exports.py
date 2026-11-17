from datetime import date,datetime,timedelta
from io import BytesIO
from sqlalchemy import select
from openpyxl import Workbook
from openpyxl.styles import Font,PatternFill,Alignment,Border,Side
from openpyxl.utils import get_column_letter
from app.db import Event,Missing,Roster,Group,Plan,PlanFile,Leave,state
from app.rules import link_visits,remark,is_reporter
from app.geo import locate,plan_check
from app.config import ROUTING_ALERT_KM

RAW=['Region','Date','Dealer','Province','District','Commune','Lat','Lon','Real Name','Employee ID','Telegram ID','Position','First Report','Last Report','Plan Check In','Check in Over 10Km','Dealer First Check In (Distance Km)','Reason_Miss','Reason_Wrong_Plan','Reason_Over_10Km']
SUMMARY=['Region','Dealer','Real Name','Position','First_Report','Last_Report','Total Outlet','Total Missing','Plan Check','Total District','Total Commune','Reason']
MISSING=['Region','Dealer','Real Name','Position','Date','Time Submit','Time Late','Reason','First_Report','Last_Report']

def fmt(t):return t.strftime('%I:%M %p') if t else ''
def telegram_message_link(chat_id,message_id):
    """Return a Telegram deep link for a private supergroup/channel message."""
    raw=str(chat_id)
    if not raw.startswith('-100') or not message_id:return ''
    return f'https://t.me/c/{raw[4:]}/{int(message_id)}'

def workbook(headers,rows,title):
    w=Workbook();s=w.active;s.title=title
    s.append(headers)
    for row in rows:
        # Prevent Telegram text from becoming an Excel formula.
        s.append([("'"+v if v.startswith(('=','+','-','@')) else v) if isinstance(v,str) else v for v in row])
    s.freeze_panes='D2';s.auto_filter.ref=s.dimensions
    for cell in s[1]:cell.fill=PatternFill('solid',fgColor='17365D');cell.font=Font(color='FFFFFF',bold=True);cell.alignment=Alignment(wrap_text=True)
    s.row_dimensions[1].height=32
    for i,h in enumerate(headers,1):s.column_dimensions[get_column_letter(i)].width=min(42,max(16,len(h)+3))
    for row in s.iter_rows(min_row=2):
        for c in row:
            c.alignment=Alignment(vertical='top',wrap_text=True)
            if isinstance(c.value,(date,datetime)):c.number_format='dd/mm/yyyy'
            if isinstance(c.value,str) and c.value.startswith(('https://t.me/','http://t.me/')):
                c.hyperlink=c.value;c.value='Open Image';c.style='Hyperlink'
    b=BytesIO();w.save(b);b.seek(0);return b

def al_summary_workbook(s,day):
    """Build an operational AL workbook for every active roster user.

    AL means a saved leave interval overlaps ``day``. NOT AL means only that
    no saved leave overlaps the date; it does not assert report attendance.
    """
    begin=datetime.combine(day,datetime.min.time());end=begin+timedelta(days=1)
    roster=sorted(s.scalars(select(Roster).where(Roster.active==1)),
                  key=lambda r:(str(r.region or ''),str(r.dealer or ''),
                                str(r.name or ''),str(r.employee or '')))
    leaves=list(s.scalars(select(Leave).where(Leave.start<end,Leave.end>begin)))
    by_uid={};by_employee={}
    for leave in leaves:
        if leave.uid is not None:by_uid.setdefault(leave.uid,[]).append(leave)
        if leave.employee:by_employee.setdefault(str(leave.employee).strip().casefold(),[]).append(leave)
    detail=[]
    for r in roster:
        matched=by_uid.get(r.uid,[]) if r.uid is not None else []
        if not matched and r.employee:
            matched=by_employee.get(str(r.employee).strip().casefold(),[])
        matched=sorted(matched,key=lambda x:(x.start,x.end,x.id or 0))
        status='AL' if matched else 'NOT AL'
        starts=', '.join(dict.fromkeys(x.start.strftime('%d/%m/%Y %I:%M %p') for x in matched))
        ends=', '.join(dict.fromkeys(x.end.strftime('%d/%m/%Y %I:%M %p') for x in matched))
        sources=', '.join(dict.fromkeys(str(x.source or '') for x in matched if x.source))
        detail.append([r.region or '',r.dealer or '',r.employee or '',r.uid or '',
                       r.name or '',r.username or '',r.position or '',status,
                       starts,ends,sources])

    counts={}
    def add_count(level,region,dealer,status):
        key=(level,region,dealer)
        value=counts.setdefault(key,{'total':0,'al':0})
        value['total']+=1
        if status=='AL':value['al']+=1
    for row in detail:
        region,dealer,status=row[0],row[1],row[7]
        add_count('Overall','ALL','ALL',status)
        add_count('Region',region,'ALL',status)
        add_count('Dealer',region,dealer,status)
    summary=[]
    order={'Overall':0,'Region':1,'Dealer':2}
    for (level,region,dealer),value in sorted(
            counts.items(),key=lambda item:(order[item[0][0]],item[0][1],item[0][2])):
        total=value['total'];al=value['al'];not_al=total-al
        summary.append([level,region,dealer,total,al,not_al,al/total if total else 0])

    wb=Workbook();ws=wb.active;ws.title='AL Summary'
    navy='17365D';blue='1F4E78';red='C00000';light_red='F4CCCC'
    grey='E7E6E6';light_grey='F3F4F6';white='FFFFFF';green='E2F0D9'
    thin=Side(style='thin',color='D9E2F3')
    ws.merge_cells('A1:G1');ws['A1']=f'USER AL SUMMARY — {day:%d %B %Y}'
    ws['A1'].fill=PatternFill('solid',fgColor=navy);ws['A1'].font=Font(color=white,bold=True,size=16)
    ws['A1'].alignment=Alignment(horizontal='center',vertical='center');ws.row_dimensions[1].height=34
    overall=counts.get(('Overall','ALL','ALL'),{'total':0,'al':0})
    kpis=[('Total Active Users',overall['total'],blue),('AL',overall['al'],red),
          ('Not AL',overall['total']-overall['al'],'7F8C8D')]
    for col,(label,value,color) in enumerate(kpis,1):
        ws.cell(3,col,label);ws.cell(4,col,value)
        for row in (3,4):
            c=ws.cell(row,col);c.fill=PatternFill('solid',fgColor=color)
            c.font=Font(color=white,bold=True,size=11 if row==3 else 16)
            c.alignment=Alignment(horizontal='center')
    ws.merge_cells('A6:G6');ws['A6']='NOT AL = no saved leave record for this date; it does not prove report submission.'
    ws['A6'].fill=PatternFill('solid',fgColor='FFF2CC');ws['A6'].font=Font(color='7F6000',italic=True)
    headers=['Level','Region','Dealer','Total Users','AL','Not AL','AL %']
    ws.append([]);ws.append(headers)
    header_row=8
    for row in summary:ws.append(row)
    for c in ws[header_row]:
        c.fill=PatternFill('solid',fgColor=navy);c.font=Font(color=white,bold=True)
        c.alignment=Alignment(horizontal='center')
    for row in ws.iter_rows(min_row=header_row+1,max_row=ws.max_row,min_col=1,max_col=7):
        fill=grey if row[0].value=='Region' else (light_grey if row[0].value=='Dealer' else green)
        for c in row:
            c.fill=PatternFill('solid',fgColor=fill);c.border=Border(bottom=thin)
            c.alignment=Alignment(vertical='center')
        row[4].fill=PatternFill('solid',fgColor=light_red);row[4].font=Font(color=red,bold=True)
        row[6].number_format='0.0%'
    ws.freeze_panes='D9';ws.auto_filter.ref=f'A{header_row}:G{ws.max_row}'
    for col,width in {'A':14,'B':12,'C':16,'D':14,'E':12,'F':12,'G':12}.items():
        ws.column_dimensions[col].width=width

    wd=wb.create_sheet('User Detail')
    detail_headers=['Region','Dealer','Employee ID','Telegram ID','Real Name','Username',
                    'Position','AL Status','Leave Start','Leave End','Source']
    wd.append(detail_headers)
    for row in detail:wd.append(row)
    for c in wd[1]:
        c.fill=PatternFill('solid',fgColor=navy);c.font=Font(color=white,bold=True)
        c.alignment=Alignment(horizontal='center',vertical='center',wrap_text=True)
    wd.row_dimensions[1].height=30
    for index,row in enumerate(wd.iter_rows(min_row=2),2):
        is_al=row[7].value=='AL'
        fill=light_red if is_al else (grey if index%2==0 else white)
        for c in row:
            c.fill=PatternFill('solid',fgColor=fill);c.border=Border(bottom=thin)
            c.alignment=Alignment(vertical='top',wrap_text=True)
        row[7].font=Font(color=red if is_al else '666666',bold=is_al)
    widths=[11,13,16,17,28,20,18,13,24,24,20]
    for idx,width in enumerate(widths,1):wd.column_dimensions[get_column_letter(idx)].width=width
    wd.freeze_panes='E2';wd.auto_filter.ref=wd.dimensions
    wd.sheet_view.showGridLines=False;ws.sheet_view.showGridLines=False
    out=BytesIO();wb.save(out);out.seek(0)
    return out,len(detail),overall['al']

def export_current_plan(s,allowed_codes=None):
    latest=state(s,'plan_latest',{})
    batch=latest.get('batch')
    if not batch:raise ValueError('No current plan has been imported. Use /update_plan first.')
    saved=s.scalar(select(PlanFile).where(PlanFile.batch==batch))
    if not saved:raise ValueError('Exact current plan file is unavailable. Upload the plan once more with /update_plan.')
    return BytesIO(saved.content),latest.get('rows',0),saved.filename

def export_data(s,kind,day,allowed_chats):
    begin=datetime.combine(day,datetime.min.time());end=begin+timedelta(days=1)
    events=list(s.scalars(select(Event).where(Event.chat.in_(allowed_chats),Event.at>=begin,Event.at<end)))
    misses=list(s.scalars(select(Missing).where(Missing.chat.in_(allowed_chats),Missing.start>=begin,Missing.start<end)))
    visits=link_visits(events)
    roster={r.uid:r for r in s.scalars(select(Roster)) if r.uid}
    groups={g.id:g for g in s.scalars(select(Group).where(Group.id.in_(allowed_chats)))}
    def pfor(e):
        base={k:getattr(roster[e.uid],k) for k in ['region','dealer','name','position','employee']} if e.uid in roster else {}
        return {**base,**(e.data.get('person') or {})}
    def times(uid):
        ts=[e.at for v in visits if v['event'].uid==uid for e in v['events']]
        return fmt(min(ts)) if ts else '',fmt(max(ts)) if ts else ''
    if kind=='export':
        rows=[]
        for m in misses:
            p=m.person;first,last=times(m.uid)
            rows.append([p.get('region',''),p.get('dealer',''),p.get('name',''),p.get('position','SO'),m.start,f'{fmt(m.start)} - {fmt(m.end)}',fmt(m.sent),m.reason or '',first,last])
        return workbook(MISSING,rows,'Missing'),len(rows)
    raw=[];details=[]
    # BI export grain is one logical report/check-in row. Daily SO metrics are
    # calculated once and repeated on each of that SO's rows so Power BI can
    # filter by the row's actual geography without losing day-level status.
    visit_groups={}
    for visit in visits:
        event=visit['event'];visit_groups.setdefault((event.chat,event.uid),[]).append(visit)
    daily={}
    for key,user_visits in visit_groups.items():
        report_times=[v['event'].at for v in user_visits]
        routes=[(v['pin'].data.get('route') or {}) for v in user_visits if v['pin']]
        first_route=next((r for r in routes if r.get('kind')=='Dealer to First Check-in'),{})
        daily[key]={
            'first':fmt(min(report_times)) if report_times else '',
            'last':fmt(max(report_times)) if report_times else '',
            'over':'Yes' if any(r.get('status')=='Over limit' or (r.get('kind')=='Previous Check-in to Current Check-in' and r.get('distance_km') is not None and float(r['distance_km'])>ROUTING_ALERT_KM) for r in routes) else 'No',
            'dealer_first':first_route.get('distance_km'),
        }
    miss_groups={}
    for missing in misses:miss_groups.setdefault((missing.chat,missing.uid),[]).append(missing)
    for v in visits:
        e=v['event'];p=pfor(e);pin=v['pin']
        if pin:
            local_geo=locate(pin.data['lat'],pin.data['lon'])
            geo=local_geo if not local_geo.get('geo_issue') else (pin.data.get('geo') or local_geo)
        else:geo={'geo_issue':'Missing pin'}
        check,issue=plan_check(s,p.get('employee',''),day.isoformat(),geo,p.get('name',''))
        key=(e.chat,e.uid);related=miss_groups.get(key,[])
        reason_miss='; '.join(dict.fromkeys(m.reason for m in related if m.reason))
        pin_data=pin.data if pin else {}
        rmk=remark(v['text']);issues='; '.join(x for x in [rmk,issue] if x)
        metric=daily[key]
        raw.append([p.get('region',''),day,p.get('dealer',''),geo.get('province',''),geo.get('district',''),geo.get('commune',''),pin.data['lat'] if pin else None,pin.data['lon'] if pin else None,p.get('name',''),p.get('employee',''),e.uid,p.get('position','SO'),metric['first'],metric['last'],'Yes' if check=='Correct' else 'No',metric['over'],metric['dealer_first'],reason_miss,pin_data.get('wrong_plan_reason',''),pin_data.get('over_10km_reason','')])
        details.append((e.uid,pin,geo,check,issues))
    if kind=='export_raw_data':
        raw.sort(key=lambda row:(str(row[0]),str(row[2]),str(row[8]),str(row[6]),str(row[7])))
        return workbook(RAW,raw,'Raw Data'),len(raw)
    # Include monitored people with zero reports as well as people with activity.
    people={e.uid:pfor(e) for e in events}
    people.update({m.uid:m.person for m in misses})
    for g in groups.values():
        for r in roster.values():
            scope=f'{r.region}-{r.dealer}'.upper()==g.code
            if is_reporter(r.position) and r.active and ((g.test_uids and r.uid in g.test_uids) or (not g.test_uids and scope)):
                people.setdefault(r.uid,{k:getattr(r,k) for k in ['region','dealer','name','position']})
    rows=[]
    for uid,p in sorted(people.items(),key=lambda kv:(kv[1].get('region',''),kv[1].get('dealer',''),kv[1].get('name',''))):
        d=[x for x in details if x[0]==uid];mm=[m for m in misses if m.uid==uid];first,last=times(uid)
        checks=[x[3] for x in d if x[1]]
        status='Incorrect' if 'Incorrect' in checks else ('Correct' if checks and all(x=='Correct' for x in checks) else 'Not checked')
        rows.append([p.get('region',''),p.get('dealer',''),p.get('name',''),p.get('position','SO'),first,last,sum(x[1] is not None for x in d),len(mm),status,len({x[2]['district'] for x in d if x[2].get('district')}),len({x[2].get('commune_code') or x[2]['commune'] for x in d if x[2].get('commune')}),'; '.join(dict.fromkeys(m.reason for m in mm if m.reason))])
    return workbook(SUMMARY,rows,'Summary by SO-SS'),len(rows)
