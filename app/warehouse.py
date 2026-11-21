"""Small, auditable Bronze-Silver-Gold pipeline for SO Alert Bot + Power BI."""
from datetime import datetime
from pathlib import Path
import hashlib,json,re
import openpyxl
from sqlalchemy import select,delete,func
from app import db
from app.db import (BronzeLocation,SilverLocation,SilverVisit,WarehouseRun,
                    QualityIssue,Reconciliation,Event,Missing,Roster,Group)
from app.rules import link_visits,remark
from app.geo import is_cambodia_coordinate,locate,plan_check
from app.exports import telegram_message_link

LOCATION_COLUMNS=['source_commune_code','province_code','province_name','province_name_kh',
                  'district_code','district_name','district_name_kh','commune_code',
                  'commune_name','commune_name_kh','commune_district_kh','dealer','region']

def code(value,width):
    text=str(int(value)) if isinstance(value,(int,float)) else str(value or '').strip()
    return text.zfill(width) if text.isdigit() else text

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()

def read_locations(path):
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)
    try:
        sheet=book.active;rows=[];seen=set()
        for row_number,row in enumerate(sheet.iter_rows(min_row=2,max_col=13,values_only=True),2):
            if not any(v is not None and str(v).strip() for v in row):continue
            values=dict(zip(LOCATION_COLUMNS,row))
            values.update(source_commune_code=code(values['source_commune_code'],6),province_code=code(values['province_code'],2),
                          district_code=code(values['district_code'],4),commune_code=code(values['commune_code'],6))
            for key in LOCATION_COLUMNS:values[key]=str(values[key] or '').strip()
            required=['province_code','province_name','district_code','district_name','commune_code','commune_name','dealer','region']
            missing=[x for x in required if not values[x]]
            if missing:raise ValueError(f'Location row {row_number} is missing: {", ".join(missing)}')
            if values['commune_code'] in seen:raise ValueError(f'Duplicate commune code {values["commune_code"]} at row {row_number}.')
            if not re.fullmatch(r'R[1-8]',values['region']):raise ValueError(f'Invalid region at row {row_number}: {values["region"]}')
            seen.add(values['commune_code']);rows.append((row_number,values))
        if not rows:raise ValueError('Location workbook has no data rows.')
        return rows
    finally:book.close()

def import_locations(s,path,source_name=None):
    """Bronze-load the workbook and upsert its standardized Silver dimension."""
    rows=read_locations(path);batch=hashlib.sha256(Path(path).read_bytes()).hexdigest();now=db.now()
    if not s.scalar(select(BronzeLocation.id).where(BronzeLocation.batch==batch).limit(1)):
        s.add_all([BronzeLocation(batch=batch,source_file=Path(source_name or path).name,source_row=n,
                                  source_hash=digest(data),payload=data,loaded_at=now) for n,data in rows])
    active=set()
    for _,data in rows:
        active.add(data['commune_code']);item=s.get(SilverLocation,data['commune_code'])
        values={k:data[k] for k in LOCATION_COLUMNS if k!='source_commune_code'}
        values.update(source_hash=digest(data),batch=batch,updated_at=now)
        if item:
            for k,v in values.items():setattr(item,k,v)
        else:s.add(SilverLocation(**values))
    for item in s.scalars(select(SilverLocation)):
        if item.commune_code not in active:s.delete(item)
    db.put(s,'location_latest',{'batch':batch,'filename':Path(source_name or path).name,'rows':len(rows),'loaded_at':now.isoformat()})
    return len(rows),batch

FORM_LABELS={
    'outlet_name':['Outlet Name'], 'phone':['Phone'], 'outlet_type':['Outlet Type'],
    'banner_type':['Banner Type','BannerType','Banner-Type','Banner_Type','Banner','Bunner Type','Banner Ty'],
    'posm':['POSM'], 'program':['PROGRAM','Program'], 'emergency_outlet':['Emergency Outlet'],
    'attacking_stock':['Attacking Stock','Attacking Stock Outlet'],
    'tour_program_outlet':['Tour Program Outlet','Tour Program'], 'key_wholesale':['Key Wholesale'],
    'water':['Water'], 'c_cola':['C-Cola','C Cola'], 'ize_pet_300ml':['Ize PET 300mL'],
    'ize_pet_1500ml':['Ize PET 1500mL'], 'product_display':['Product Display']}

def field(text,labels):
    labels='|'.join(re.escape(x) for x in labels)
    match=re.search(rf'(?im)^\s*(?:{labels})\s*[:៖,=\-][ \t]*([^\n]*)',str(text or ''))
    return match.group(1).strip() if match else ''

def refresh_warehouse(s,mode='incremental'):
    """Idempotent hash-upsert of logical bot visits plus DQ/reconciliation."""
    run=WarehouseRun(started_at=db.now(),mode=mode,status='Running');s.add(run);s.flush()
    inserted=updated=unchanged=rejected=0
    try:
        events=list(s.scalars(select(Event).order_by(Event.chat,Event.at,Event.mid)))
        groups={g.id:g.title for g in s.scalars(select(Group))}
        roster={r.uid:r for r in s.scalars(select(Roster)) if r.uid}
        locations={x.commune_code:x for x in s.scalars(select(SilverLocation))}
        misses=list(s.scalars(select(Missing)))
        logical=[]
        by_chat={}
        for event in events:by_chat.setdefault(event.chat,[]).append(event)
        for chat,items in by_chat.items():logical.extend(link_visits(items))
        active_source_ids=set()
        for visit in logical:
            event=visit['event'];active_source_ids.add(event.id);pin=visit['pin'];text=visit['text'] or ''
            if pin and not is_cambodia_coordinate(pin.data.get('lat'),pin.data.get('lon')):pin=None
            person=dict(event.data.get('person') or {})
            if event.uid in roster:
                # Current roster identity wins over legacy/manual identity
                # snapshots so every Gold fact joins to Dim Employee.
                r=roster[event.uid]
                person={**person,**{k:getattr(r,k) for k in ['employee','uid','name','username','region','dealer','position']}}
            geo={'geo_issue':'Missing pin'};lat=lon=None
            if pin:
                lat,lon=pin.data.get('lat'),pin.data.get('lon');geo=pin.data.get('geo') or locate(lat,lon)
            normalized_code=code(geo.get('commune_code'),6) if geo.get('commune_code') else ''
            location=locations.get(normalized_code)
            check,plan_issue=plan_check(s,person.get('employee',''),event.at.date().isoformat(),geo,person.get('name',''))
            photos=[x for x in visit['events'] if x.kind=='photo'];photo_data=[x.data for x in photos]
            ocr=next((x for x in photo_data if x.get('status')=='Review'),photo_data[0] if photo_data else {})
            gap=ocr.get('gap');time_status='Review' if gap is None or gap>30 or gap<0 else 'OK'
            related=[m for m in misses if m.chat==event.chat and m.uid==event.uid and m.start<=event.at<m.end]
            reason='; '.join(dict.fromkeys(m.reason for m in related if m.reason));pin_data=pin.data if pin else {};route=(pin_data.get('route') or {})
            parsed={key:field(text,labels) for key,labels in FORM_LABELS.items()}
            image_mid=min((x.mid for x in photos),default=None)
            payload=dict(source_event_id=event.id,chat_id=event.chat,group_name=groups.get(event.chat,str(event.chat)),message_id=event.mid,
                telegram_id=event.uid,employee_id=str(person.get('employee','')),real_name=person.get('name',''),username=person.get('username',''),
                region=person.get('region',''),dealer=person.get('dealer',''),position=person.get('position','SO'),submitted_at=event.at,
                report_date=event.at.date(),report_time=event.at.strftime('%H:%M:%S'),**parsed,latitude=lat,longitude=lon,
                actual_province=geo.get('province',''),actual_province_kh=location.province_name_kh if location else '',
                actual_district=geo.get('district',''),actual_district_kh=location.district_name_kh if location else '',
                actual_commune=geo.get('commune',''),actual_commune_kh=location.commune_name_kh if location else '',actual_commune_code=normalized_code,
                plan_check=check,plan_reason=plan_issue,route_leg=route.get('kind',''),road_distance_km=route.get('distance_km'),
                route_status=route.get('status',''),photo_time=str(ocr.get('photo_time','')),delay_minutes=gap,time_status=time_status,
                missing_reason=reason,wrong_plan_reason=pin_data.get('wrong_plan_reason',''),
                over_10km_reason=pin_data.get('over_10km_reason',''),
                reason_status='Answered' if reason else ('Pending' if related else 'Not required'),
                remark_issue=remark(text),gps_match=visit['match'],ocr_issue=ocr.get('ocr_issue','No photo timestamp'),
                open_map=f'https://maps.google.com/maps?q={lat},{lon}' if pin else '',open_image=telegram_message_link(event.chat,image_mid),
                open_telegram=telegram_message_link(event.chat,event.mid),raw_text=text,processed_at=db.now())
            signature=digest({k:v for k,v in payload.items() if k!='processed_at'});payload['source_hash']=signature
            target=s.scalar(select(SilverVisit).where(SilverVisit.source_event_id==event.id))
            if not target:s.add(SilverVisit(**payload));inserted+=1
            elif target.source_hash!=signature:
                for k,v in payload.items():setattr(target,k,v)
                updated+=1
            else:unchanged+=1
        # Logical visits can disappear after an edit/folding change; remove stale derived rows only.
        for target in s.scalars(select(SilverVisit)):
            if target.source_event_id not in active_source_ids:s.delete(target)
        s.flush()
        s.execute(delete(QualityIssue).where(QualityIssue.run_id==run.id))
        def issue(entity_id,rule,severity,detail):
            nonlocal rejected
            if severity=='Error':rejected+=1
            s.add(QualityIssue(run_id=run.id,entity_type='SO Visit',entity_id=str(entity_id),rule_code=rule,severity=severity,detail=detail,detected_at=db.now()))
        for row in s.scalars(select(SilverVisit)):
            if not row.employee_id:issue(row.source_event_id,'DQ_EMPLOYEE_REQUIRED','Error','Employee ID is missing.')
            if row.latitude is None or row.longitude is None:issue(row.source_event_id,'DQ_GPS_REQUIRED','Error','Visit has no matched GPS pin.')
            elif not (-90<=row.latitude<=90 and -180<=row.longitude<=180):issue(row.source_event_id,'DQ_GPS_RANGE','Error','Latitude/longitude is outside the valid range.')
            elif not is_cambodia_coordinate(row.latitude,row.longitude):issue(row.source_event_id,'DQ_GPS_COUNTRY','Error','Latitude/longitude is outside Cambodia and was quarantined.')
            if row.actual_commune_code and row.actual_commune_code not in locations:issue(row.source_event_id,'DQ_LOCATION_LOOKUP','Warning','Commune code is absent from Location_All.')
            if row.plan_check=='Incorrect':issue(row.source_event_id,'DQ_PLAN_INCORRECT','Warning',row.plan_reason or 'Wrong plan commune.')
            if row.route_status=='Unavailable':issue(row.source_event_id,'DQ_ROUTE_UNAVAILABLE','Warning','Road route could not be calculated.')
        total_visits=s.scalar(select(func.count()).select_from(SilverVisit)) or 0
        latest=db.state(s,'location_latest',{});bronze_count=s.scalar(select(func.count()).select_from(BronzeLocation).where(BronzeLocation.batch==latest.get('batch'))) or 0
        silver_location_count=s.scalar(select(func.count()).select_from(SilverLocation)) or 0
        checks=[('Logical visits to Silver visits',len(logical),total_visits),('Location Bronze to Silver',bronze_count,silver_location_count)]
        s.execute(delete(Reconciliation).where(Reconciliation.run_id==run.id))
        for name,source,target in checks:
            difference=target-source;s.add(Reconciliation(run_id=run.id,check_name=name,source_count=source,target_count=target,difference=difference,status='Passed' if difference==0 else 'Failed',checked_at=db.now()))
        metrics={'raw_events':len(events),'logical_visits':len(logical),'silver_visits':total_visits,'bronze_locations':bronze_count,
                 'silver_locations':silver_location_count,'quality_issues':s.scalar(select(func.count()).select_from(QualityIssue).where(QualityIssue.run_id==run.id)) or 0}
        run.completed_at=db.now();run.status='Succeeded' if all(a==b for _,a,b in checks) else 'Reconciliation Failed'
        run.inserted=inserted;run.updated=updated;run.unchanged=unchanged;run.rejected=rejected;run.metrics=metrics
        db.put(s,'warehouse_latest',{'run_id':run.id,'status':run.status,'completed_at':run.completed_at.isoformat(),**metrics})
        return run.id,run.status,metrics,inserted,updated,unchanged,rejected
    except Exception as exc:
        run.completed_at=db.now();run.status='Failed';run.error=f'{type(exc).__name__}: {exc}'
        raise

def latest_status(s):
    return db.state(s,'warehouse_latest',{})
