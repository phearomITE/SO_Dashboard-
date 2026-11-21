"""Plan execution: one employee row, with commune-day targets counted once."""
from collections import defaultdict
from functools import lru_cache
from io import BytesIO
from sqlalchemy import text

def norm(value):return ' '.join(str(value or '').casefold().split())
def numeric(value):
    value=str(value or '').strip()
    return value.lstrip('0') if value.isdigit() else ''

def outlet_targets_from_rows(rows):
    """Use each assigned commune once; schedule dates never multiply the pool."""
    communes={}
    def count(value):
        try:return max(0,int(float(str(value or 0).replace(',',''))))
        except (TypeError,ValueError,OverflowError):return 0
    for row in rows:
        vals=row['values'];employee=str(vals.get('employee id','')).strip()
        if not employee:continue
        key=(employee,str(vals.get('region','')),str(vals.get('dealer','')),numeric(vals.get('com.code')) or (norm(vals.get('district')),norm(vals.get('commune'))))
        ws=count(vals.get('#ws/ds'));horeca=count(vals.get('#horeca outlet'))
        if key not in communes:
            communes[key]={'employee_id':employee,'name':str(vals.get('sale name','')),'region':key[1],'dealer':key[2],'ws_ds':ws,'horeca':horeca}
        else:
            communes[key]['ws_ds']=max(communes[key]['ws_ds'],ws)
            communes[key]['horeca']=max(communes[key]['horeca'],horeca)
    return [{**row,'total_outlets':row['ws_ds']+row['horeca']} for row in communes.values()]

@lru_cache(maxsize=4)
def workbook_outlet_targets(batch,content):
    from app.imports import read_plan
    _,_,rows,_=read_plan(BytesIO(content),include_dates=True,include_grid=True)
    return outlet_targets_from_rows(rows)

def summarize_actual_plan(roster,plans,visits,active_days,region=None,dealer=None,search='',outlet_targets=None):
    ids={};by_uid=defaultdict(list)
    for row in roster:
        if row.get('uid'):by_uid[str(row['uid'])].append(row)
    for rows in by_uid.values():
        chosen=min(rows,key=lambda x:(str(x['employee']).startswith('manual:'),x.get('source')!='excel',x['employee']))
        for row in rows:ids[row['employee']]=chosen['employee']
    canonical={row['employee']:row for row in roster if ids.get(row['employee'],row['employee'])==row['employee']}
    names=defaultdict(set)
    for row in canonical.values():names[(norm(row.get('name')),norm(row.get('region')),norm(row.get('dealer')))].add(row['employee'])
    def who(employee,name='',reg='',deal='',uid=None):
        if uid and str(uid) in by_uid:
            return ids[by_uid[str(uid)][0]['employee']]
        if employee in ids:return ids[employee]
        if employee in canonical:return employee
        options=names.get((norm(name),norm(reg),norm(deal)),set())
        return next(iter(options)) if len(options)==1 else employee
    output={};targets=defaultdict(dict);visited=defaultdict(set);completed=defaultdict(set)
    def ensure(employee,name='',reg='',deal=''):
        employee=str(employee or '')
        if not employee:return None
        if employee not in output:
            master=canonical.get(employee,{})
            output[employee]={'employee_id':employee,'name':master.get('name') or name or employee,'region':master.get('region') or reg,'dealer':master.get('dealer') or deal,'position':str(master.get('position') or '—'),'total_outlets':None,'total_checkins':0,'correct':0,'incorrect':0,'unassessed':0}
        return output[employee]
    for row in canonical.values():ensure(row['employee'],row.get('name'),row.get('region'),row.get('dealer'))
    for target in outlet_targets or []:
        employee=who(target['employee_id'],target.get('name'),target.get('region'),target.get('dealer'))
        item=ensure(employee,target.get('name'),target.get('region'),target.get('dealer'))
        if item is not None:item['total_outlets']=(item['total_outlets'] or 0)+target['total_outlets']
    for row in plans:
        data=row['data'];employee=who(row['employee'],data.get('sale_name'),data.get('region'),data.get('dealer'))
        if not ensure(employee,data.get('sale_name'),data.get('region'),data.get('dealer')):continue
        code=numeric(data.get('commune_code'));name=norm(data.get('commune'))
        if not(code or name):continue
        key=(str(row['day']),code or ('name:'+norm(data.get('district'))+':'+name))
        targets[employee][key]={'code':code,'name':name,'district':norm(data.get('district'))}
    for row in visits:
        employee=who(row.get('employee_id'),row.get('real_name'),row.get('region'),row.get('dealer'),row.get('telegram_id'))
        item=ensure(employee,row.get('real_name'),row.get('region'),row.get('dealer'))
        if item is None:continue
        if item['position']=='—' and row.get('position'):item['position']=row['position']
        count=int(row['checkins']);item['total_checkins']+=count;day=str(row['report_date'])
        code=numeric(row.get('actual_commune_code'));name=norm(row.get('actual_commune'));district=norm(row.get('actual_district'))
        if code or name:visited[employee].add((day,code or ('name:'+district+':'+name)))
        if not(code or name) or day not in active_days:
            item['unassessed']+=count;continue
        matches=[]
        for key,target in targets[employee].items():
            if key[0]!=day:continue
            same=(code==target['code']) if code and target['code'] else bool(name and name==target['name'] and (not district or not target['district'] or district==target['district']))
            if same:matches.append(key)
        item['correct' if matches else 'incorrect']+=count
        completed[employee].update(matches)
    result=[]
    for employee,item in output.items():
        if region and norm(item['region'])!=norm(region):continue
        if dealer and norm(item['dealer'])!=norm(dealer):continue
        if search and norm(search) not in norm(' '.join(str(item.get(k,'')) for k in ('employee_id','name','region','dealer'))):continue
        item.update(scheduled=len(targets[employee]),completed=len(completed[employee]),visited_communes=len(visited[employee]))
        item['commune_completion_percent']=round(100*item['completed']/item['scheduled'],1) if item['scheduled'] else None
        item['actual_plan_percent']=round(100*item['total_checkins']/item['total_outlets'],1) if item['total_outlets'] else None
        item['status']='No outlet target' if not item['total_outlets'] else 'Target reached' if item['total_checkins']>=item['total_outlets'] else 'In progress' if item['total_checkins'] else 'Not visited'
        result.append(item)
    result.sort(key=lambda x:(str(x['region']),str(x['dealer']),norm(x['name']),x['employee_id']))
    totals={key:sum(row[key] for row in result) for key in ('total_checkins','correct','incorrect','unassessed','scheduled','completed')}
    totals['total_outlets']=sum(row['total_outlets'] or 0 for row in result)
    totals['targeted_checkins']=sum(row['total_checkins'] for row in result if row['total_outlets'])
    totals.update(employees=len(result),actual_plan_percent=round(100*totals['targeted_checkins']/totals['total_outlets'],1) if totals['total_outlets'] else None)
    return {'items':result,'totals':totals,'definition':'Actual Plan % = recorded visits in the selected date range / Total Outlet × 100. Total Outlet is WS/DS + HORECA assigned in the workbook, counting each employee/commune once, including unscheduled communes. Repeat visits count and results may exceed 100%. No target displays —; the overall percentage includes only employees with positive outlet targets.'}

def actual_plan_report(engine,start,end,region=None,dealer=None,search=''):
    params={'start':start,'end':end,'start_text':start.isoformat(),'end_text':end.isoformat()}
    with engine.connect() as conn:
        roster=[dict(row) for row in conn.execute(text("SELECT employee,uid,name,region,dealer,position,source FROM so_roster_v1 WHERE active=1 AND upper(trim(position)) IN ('SO','SS')")).mappings()]
        mapping=conn.execute(text("SELECT data FROM so_states_v1 WHERE key='plan_dates'")).scalar() or {}
        days={day for day in mapping if start.isoformat()<=day<=end.isoformat()}
        batch=mapping[max(days)] if days else None
        workbook=conn.execute(text('SELECT filename,content FROM so_plan_files_v1 WHERE batch=:batch'),{'batch':batch}).mappings().first() if batch else None
        outlet_targets=workbook_outlet_targets(batch,bytes(workbook['content'])) if workbook else []
        plans=[dict(row) for row in conn.execute(text("SELECT p.employee,p.day,p.data FROM so_plans_v1 p JOIN so_states_v1 s ON s.key='plan_dates' AND p.batch=s.data->>p.day WHERE p.day BETWEEN :start_text AND :end_text"),params).mappings()]
        visits=[dict(row) for row in conn.execute(text("""SELECT employee_id,telegram_id,max(real_name) AS real_name,max(position) AS position,region,dealer,report_date,actual_commune_code,actual_commune,actual_district,count(DISTINCT visit_id) AS checkins
            FROM silver_so_visit_v1 WHERE report_date BETWEEN :start AND :end AND upper(trim(coalesce(position,'SO'))) NOT IN ('HEAD OF SALES','AREA SALES MANAGER')
            GROUP BY employee_id,telegram_id,region,dealer,report_date,actual_commune_code,actual_commune,actual_district"""),params).mappings()]
        try:
            raw_pins=[dict(row) for row in conn.execute(text("""SELECT
                    coalesce(r.employee,e.data->'person'->>'employee','') AS employee_id,
                    e.uid AS telegram_id,
                    max(coalesce(r.name,e.data->'person'->>'name','')) AS real_name,
                    max(coalesce(r.position,e.data->'person'->>'position','SO')) AS position,
                    coalesce(r.region,e.data->'person'->>'region',split_part(g.code,'-',1),'') AS region,
                    coalesce(r.dealer,e.data->'person'->>'dealer',substring(g.code from position('-' in g.code)+1),'') AS dealer,
                    e.at::date AS report_date,
                    coalesce(e.data->'geo'->>'commune_code','') AS actual_commune_code,
                    coalesce(e.data->'geo'->>'commune','') AS actual_commune,
                    coalesce(e.data->'geo'->>'district','') AS actual_district,
                    count(DISTINCT e.id) AS checkins
                FROM so_events_v1 e
                LEFT JOIN LATERAL (
                    SELECT x.employee,x.name,x.region,x.dealer,x.position FROM so_roster_v1 x
                    WHERE x.uid=e.uid AND x.active=1 ORDER BY x.source='excel' DESC,x.id LIMIT 1
                ) r ON true
                LEFT JOIN so_groups_v1 g ON g.id=e.chat
                WHERE e.kind='pin' AND e.at::date BETWEEN :start AND :end
                  AND (e.data->>'lat') ~ '^-?[0-9]+([.][0-9]+)?$'
                  AND (e.data->>'lon') ~ '^-?[0-9]+([.][0-9]+)?$'
                  AND (e.data->>'lat')::double precision BETWEEN 9.80 AND 14.75
                  AND (e.data->>'lon')::double precision BETWEEN 102.30 AND 107.70
                  AND e.chat<>-1004303041907 AND lower(trim(coalesce(g.title,'')))<>'test_so'
                  AND upper(trim(coalesce(r.position,e.data->'person'->>'position','SO'))) NOT IN ('HEAD OF SALES','AREA SALES MANAGER')
                GROUP BY 1,2,5,6,7,8,9,10"""),params).mappings()]
            if sum(int(r['checkins']) for r in raw_pins) > sum(int(v['checkins']) for v in visits):
                visits = raw_pins
        except Exception:
            pass
    return {'start':start.isoformat(),'end':end.isoformat(),'outlet_workbook':workbook['filename'] if workbook else None,**summarize_actual_plan(roster,plans,visits,days,region,dealer,search,outlet_targets=outlet_targets)}
