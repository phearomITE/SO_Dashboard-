"""Employee-level reason reporting and the styled Excel template export."""
from collections import defaultdict
from copy import copy
from datetime import date
from io import BytesIO
from pathlib import Path
import json,re,math
from sqlalchemy import text

TEMPLATE=Path(__file__).parent/'templates/reason_summary_by_employee.xlsx'
HEADERS=['Name','Employee ID','Region','Dealer','Total Outlets','Reason Summary']

def _uid(value):
    try:return str(int(value)) if int(value)>0 else ''
    except (ValueError,TypeError):return ''

def aggregate_users(replies,visits,language='km',search=''):
    groups={}
    ids=defaultdict(set)
    for source in [*replies,*visits]:
        uid=_uid(source.get('telegram_id'));employee=str(source.get('employee_id') or '').strip()
        if uid and employee:ids[employee].add(uid)
    def person_uid(source):
        uid=_uid(source.get('telegram_id'));employee=str(source.get('employee_id') or '').strip()
        return uid or (next(iter(ids[employee])) if employee and len(ids[employee])==1 else '')
    uid_counts=defaultdict(int);employee_counts=defaultdict(int)
    for row in visits:
        count=int(row.get('total_outlets') or 0);uid=person_uid(row)
        if uid:uid_counts[uid]+=count
        employee=str(row.get('employee_id') or '').strip()
        if employee:employee_counts[employee]+=count
    for i,reply in enumerate(sorted(replies,key=lambda x:str(x.get('recorded_at') or ''))):
        uid=person_uid(reply);employee=str(reply.get('employee_id') or '').strip()
        identity=('telegram',uid) if uid else ('employee',employee) if employee else ('unmatched',str(reply.get('chat')),str(reply.get('reply_to')),i)
        group=groups.setdefault(identity,{'name':'','employee_id':'','region':set(),'dealer':set(),'uid':uid,'replies':[]})
        if reply.get('name'):group['name']=str(reply['name'])
        if employee and (not group['employee_id'] or not employee.startswith('manual:')):group['employee_id']=employee
        if reply.get('region'):group['region'].add(str(reply['region']))
        if reply.get('dealer'):group['dealer'].add(str(reply['dealer']))
        group['replies'].append(reply)
    output=[]
    for group in groups.values():
        parts=[];seen=set();positive=False;review=False
        for reply in group['replies']:
            if reply.get('is_reason') is False:continue
            positive|=reply.get('is_reason') is True
            pending=reply.get('is_reason') is None;review|=pending
            en=reply.get('summary_en') or reply.get('summary') or reply.get('message') or 'No summary available.'
            km=reply.get('summary_km') or ('មិនទាន់សង្ខេប៖ '+str(reply.get('message') or en))
            value=km if language=='km' else en if language=='en' else km+'\n'+en
            if pending:value=('ត្រូវពិនិត្យ៖ ' if language!='en' else 'Needs review: ')+value
            normalized=' '.join(value.split()).casefold()
            if normalized not in seen:parts.append(value);seen.add(normalized)
        if not parts:parts=[('មិនបានបញ្ជាក់មូលហេតុដែលខកខានរាយការណ៍។' if language=='km' else 'No stated reason for the missed report.' if language=='en' else 'មិនបានបញ្ជាក់មូលហេតុដែលខកខានរាយការណ៍។\nNo stated reason for the missed report.')]
        row={'name':group['name'] or '—','employee_id':group['employee_id'] or '—',
             'region':', '.join(sorted(group['region'])) or '—','dealer':', '.join(sorted(group['dealer'])) or '—',
             'total_outlets':uid_counts.get(group['uid'],0) if group['uid'] else employee_counts.get(group['employee_id'],0) if group['employee_id'] else None,
             'summary':'\n'.join(parts),'reply_count':len(group['replies']),'has_reason':positive,'needs_review':review}
        if not search or search.casefold() in (' '.join(str(row[k]) for k in ('name','employee_id','region','dealer'))).casefold():output.append(row)
    return sorted(output,key=lambda x:(x['region'],x['dealer'],x['name'],x['employee_id']))

def user_reason_report(engine,day,region=None,dealer=None,language='km',search='',end=None):
    end=end or day
    clauses=["key LIKE 'reason_review:%'","data->>'date' BETWEEN :day AND :end"]
    visit_clauses=["report_date BETWEEN :report_day AND :report_end","upper(trim(coalesce(position,'SO'))) IN ('SO','SS')"]
    params={'day':day.isoformat(),'end':end.isoformat(),'report_day':day,'report_end':end}
    for key,value in [('region',region),('dealer',dealer)]:
        if value:
            params[key]=value.upper();clauses.append(f"upper(data->>'{key}')=:{key}");visit_clauses.append(f'upper({key})=:{key}')
    with engine.connect() as conn:
        replies=[r['data'] for r in conn.execute(text('SELECT data FROM so_states_v1 WHERE '+' AND '.join(clauses)),params).mappings()]
        visits=[dict(r) for r in conn.execute(text('SELECT telegram_id,employee_id,count(DISTINCT visit_id) AS total_outlets FROM silver_so_visit_v1 WHERE '+' AND '.join(visit_clauses)+' GROUP BY telegram_id,employee_id'),params).mappings()]
    rows=aggregate_users(replies,visits,language,search)
    return {'date':day.isoformat(),'start':day.isoformat(),'end':end.isoformat(),'region':region or 'All Regions','dealer':dealer or 'All Dealers','language':language,
            'items':rows,'employees':len(rows),'total_outlets':sum(r['total_outlets'] or 0 for r in rows),
            'outlet_definition':'Recorded outlet check-ins in the selected date range. Repeated visits count separately; totals include only employees with replies in this export.'}

def employee_reason_xlsx(report):
    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill
    from openpyxl.worksheet.page import PageMargins
    if not TEMPLATE.exists():raise RuntimeError('Employee summary Excel template is not installed.')
    wb=load_workbook(TEMPLATE);sheet=wb['Employee Summary']
    prototype=[copy(sheet.cell(8,c)._style) for c in range(1,7)]
    sheet.delete_rows(8,max(sheet.max_row-7,1))
    sheet['B3']=date.fromisoformat(report['date']) if report.get('end',report['date'])==report['date'] else f"{report['date']} to {report['end']}";sheet['B3'].number_format='dd mmm yyyy'
    sheet['E3']={'km':'Khmer','en':'English','both':'Khmer + English'}[report['language']]
    sheet['B4']=report['region'];sheet['E4']=report['dealer'];sheet['B5']=report['employees']
    for r,item in enumerate(report['items'],8):
        values=[item['name'],item['employee_id'],item['region'],item['dealer'],item['total_outlets'],item['summary']]
        for c,value in enumerate(values,1):
            if isinstance(value,str) and len(value)>32767:raise ValueError('An employee summary exceeds Excel cell limits. Narrow the filters before exporting.')
            cell=sheet.cell(r,c,value);cell._style=copy(prototype[c-1])
            if r%2:cell.fill=PatternFill('solid',fgColor='FFFFFF')
            if isinstance(value,str):cell.data_type='s'
        lines=sum(max(1,math.ceil(len(line)/65)) for line in str(item['summary']).split('\n'))
        name_lines=max(1,math.ceil(len(item['name'])/26))
        sheet.row_dimensions[r].height=min(409,max(36,max(lines,name_lines)*18+12))
    last=7+len(report['items'])
    sheet['E5']=report['total_outlets']
    sheet.freeze_panes='C8';sheet.auto_filter.ref=f'A7:F{max(8,last)}'
    sheet.print_title_rows='1:7';sheet.print_options.horizontalCentered=True
    sheet.sheet_properties.pageSetUpPr.fitToPage=True
    sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A4;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    sheet.page_margins=PageMargins(left=.25,right=.25,top=.35,bottom=.35,header=.15,footer=.15)
    sheet.print_area=f'A1:F{max(8,last)}'
    sheet.oddFooter.center.text='Page &P of &N'
    output=BytesIO();wb.save(output);wb.close();return output.getvalue()
