"""Private dashboard orchestration for the supplied, unchanged GP calculation engine."""
from __future__ import annotations
import asyncio, json, os, sys, threading, uuid, zipfile
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from app.admin_api import current_admin

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/gp_reporting'
JOBS=BASE/'jobs'
sys.path.insert(0,str(ROOT/'app/gp_legacy'))
router=APIRouter(prefix='/api/v1/admin/gp',tags=['Report GP'])
_gate=threading.Lock()
_report_lock=threading.RLock()
_tasks={}
_cancel_events={}
_ACTIVE={'queued','running'}
from app.gp_preview import SHEETS as EXPECTED, keep_summary_sheets, workbook_preview, report_revision, edit_workbook
from app.gp_performance import cached_preview, scan_plan, reuse_artifacts, store_artifacts
TEMPLATES={'roster':'SO_List.xlsx','template':'Simple-Report GP.xlsx','staff':'R1-R8-Master List.xlsx'}

def _now():return datetime.now(ZoneInfo('Asia/Phnom_Penh')).isoformat(timespec='seconds')
def _save(job):
    directory=JOBS/job['id'];directory.mkdir(parents=True,exist_ok=True)
    temp=directory/'job.tmp';temp.write_text(json.dumps(job,ensure_ascii=False,default=str),encoding='utf-8');temp.replace(directory/'job.json')
def _load(key):
    try:
        uuid.UUID(key)
        return json.loads((JOBS/key/'job.json').read_text(encoding='utf-8'))
    except (ValueError,FileNotFoundError):raise HTTPException(404,'Report run not found.')
def _step(job,label,**details):
    job.update(status='running',stage=label,updated_at=_now(),**details)
    job['steps'].append({'at':_now(),'message':label});_save(job)
def _settings():
    from banner_tracker import settings as module
    module.ROOT=BASE
    class DashboardSettings(module.Settings):
        @property
        def gp_so_master_path(self):return self.report_gp_template_path.parent/'SO_List.xlsx'
    seed=module.Settings.load(require_telegram=False)
    cfg=json.loads((BASE/'connection.json').read_text(encoding='utf-8')) if (BASE/'connection.json').exists() else {}
    return DashboardSettings(**{**asdict(seed),'api_id':int(cfg.get('TELEGRAM_API_ID') or 0),
        'api_hash':cfg.get('TELEGRAM_API_HASH',''),'phone':cfg.get('TELEGRAM_PHONE',''),
        'session_path':BASE/'data/banner_tracker','database_path':BASE/'data/banner_tracking.db',
        'report_gp_template_path':BASE/'templates/Simple-Report GP.xlsx','staff_master_path':BASE/'templates/R1-R8-Master List.xlsx',
        'template_path':BASE/'templates/Simple-Report GP.xlsx','aliases_path':BASE/'config/banner_aliases.json',
        'export_dir':BASE/'exports','invalid_report_alert_mode':'off','timezone':'Asia/Phnom_Penh'})

def _validate(path):
    from openpyxl import load_workbook
    wb=load_workbook(path,read_only=True,data_only=False)
    try:
        if wb.sheetnames!=EXPECTED:raise ValueError('Generated workbook is missing the two required GP sheets.')
        report,dealer=wb['Report GP'],wb['Report with Dealer']
        for sheet in [report,dealer]:
            for col in (12,16,20,24,28,32,36,40):
                if [sheet.cell(9,col+i).value for i in range(4)]!=['WS','Retail','DS','ON']:
                    raise ValueError('Execution metric outlet-type columns failed validation.')
        codes=[dealer.cell(r,3).value for r in range(10,75)]
        if len(set(codes))!=65 or not all(codes):raise ValueError('Expected 65 unique dealer rows.')
        if report['K10'].value!='=IFERROR(J10/G10,0)':raise ValueError('Average visit formula failed validation.')
        if (report['H18'].value,report['I18'].value)!=(dealer['H75'].value,dealer['I75'].value):
            raise ValueError('Region/dealer location totals do not reconcile.')
        return {'sheets':len(wb.sheetnames),'dealers':65,'districts':report['H18'].value,'communes':report['I18'].value,'visits':report['J18'].value}
    finally:wb.close()

async def _run_local(job,settings):
    from banner_tracker.database import Database
    from banner_tracker.telegram_service import TelegramBannerTracker,local_day_range
    from banner_tracker.gp_so_roster import load_so_roster,headcounts
    from banner_tracker.gp_daily_export import export_report_gp_daily
    settings.ensure_directories();tracker=None
    try:
        _step(job,'Validating roster and reference files')
        roster=load_so_roster(settings.gp_so_master_path);regions,_=headcounts(roster)
        job['roster_total']=sum(x['SO']+x['SS'] for x in regions.values());_save(job)
        db=Database(settings.database_path)
        original_coverage=db.mark_track_range_complete
        completed_groups=set()
        def record_coverage(track,group_id,date_from,date_to):
            original_coverage(track,group_id,date_from,date_to)
            if str(track).upper()=='OUTLET_VISIT':
                completed_groups.add((date_from.date().isoformat(),int(group_id)))
                days=(date.fromisoformat(job['end'])-date.fromisoformat(job['start'])).days+1
                job.update(groups_completed=len(completed_groups),group_days_total=65*days,
                           progress=min(65,10+int(55*len(completed_groups)/(65*days))),updated_at=_now())
                _save(job)
        db.mark_track_range_complete=record_coverage
        if job['source'] in {'live','auto'}:
            plan=scan_plan(db,job['start'],job['end'],force=job['source']=='live')
            if any(pending for _,pending,_ in plan):
                if not settings.api_id or not settings.api_hash:raise ValueError('Telegram connection is not configured.')
                _step(job,'Connecting to Telegram for missing or refreshed data')
                tracker=TelegramBannerTracker(settings,db)
                await tracker._connect_with_session_lock_retry(timeout=30)
                if not await tracker.client.is_user_authorized():
                    raise ValueError('The saved Telegram session has expired. Reconnect outside the dashboard, or use saved complete scans.')
                discovery=await tracker.discover_groups()
                valid=[x for x in discovery if x.get('status')=='OK']
                if len(valid)!=65 or len(db.groups())!=65:raise ValueError('Expected 65 accessible Execution groups.')
                plan=scan_plan(db,job['start'],job['end'],force=job['source']=='live')
            for i,(day,pending,reused) in enumerate(plan):
                for group in db.groups():
                    if int(group['group_id']) not in {int(g['group_id']) for g in pending}:completed_groups.add((day,int(group['group_id'])))
                job['groups_reused']=job.get('groups_reused',0)+reused
                _step(job,f'{day}: refreshing {len(pending)} groups; reusing {reused} complete scans',progress=round(10+55*i/len(plan)))
                a,b=local_day_range(day,day,settings.timezone)
                parts=await asyncio.gather(*(tracker._history_group(g,a,b,force=True,track='OUTLET_VISIT',update_checkpoint=False) for g in pending)) if pending else []
                result={key:sum(part.get(key,0) for part in parts) for key in ['groups','messages','errors','skipped_groups']}
                result['reused_groups']=reused
                job['scan_results'].append({'date':day,**result});_save(job)
                if result['errors'] or result['groups']+reused!=65:raise ValueError(f'Incomplete scan for {day}; export stopped to prevent partial totals.')
            if tracker is not None and job['refresh_members']:
                _step(job,'Refreshing Telegram membership snapshots',progress=68)
                job['membership']=await tracker.refresh_group_members();_save(job)
        else:_step(job,'Using saved Telegram data; checking scan coverage',progress=65)
        _step(job,'Matching GPS, updating SO list and generating daily workbooks',progress=75)
        output=JOBS/job['id']/'outputs';output.mkdir(parents=True,exist_ok=True)
        paths=export_report_gp_daily(db,output,job['start'],job['end'],settings.report_gp_template_path,settings.staff_master_path,
            so_master_path=settings.gp_so_master_path,require_complete_al_scan=True,selected_date_mode=True,summary_only=True,
            allow_current_day_snapshot=job['end']==datetime.now(ZoneInfo('Asia/Phnom_Penh')).date().isoformat())
        _step(job,'Validating workbook formulas and dealer/region totals',progress=92)
        files=[]
        for path in paths:
            path=Path(path);keep_summary_sheets(path);files.append({'name':path.name,'bytes':path.stat().st_size,'validation':_validate(path)})
        with zipfile.ZipFile(JOBS/job['id']/'reports.zip','w',zipfile.ZIP_DEFLATED) as bundle:
            for item in files:bundle.write(output/item['name'],item['name'])
        job.update(status='running',stage='Saving private report cache',progress=98,files=files,updated_at=_now());_save(job)
    finally:
        if tracker is not None:await tracker.disconnect()

def _sqlite_copy(source,target):
    import sqlite3
    target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+'.copying')
    src=sqlite3.connect(source);dst=sqlite3.connect(temporary)
    try:src.backup(dst)
    finally:dst.close();src.close()
    temporary.replace(target)

async def _run(job):
    import time
    from app.gp_live_export import export_live_gp_reports
    started=time.perf_counter()
    settings=_settings();settings.ensure_directories()
    template_path=BASE/'templates'/TEMPLATES['template']
    _step(job,'Reading live SO check-ins and reports from database',progress=25)
    output=JOBS/job['id']/'outputs';output.mkdir(parents=True,exist_ok=True)
    paths=export_live_gp_reports(job['start'],job['end'],template_path,output)
    _step(job,'Validating workbook formulas and dealer/region totals',progress=85)
    files=[]
    for path in paths:
        path=Path(path);keep_summary_sheets(path);files.append({'name':path.name,'bytes':path.stat().st_size,'validation':_validate(path)})
    with zipfile.ZipFile(JOBS/job['id']/'reports.zip','w',zipfile.ZIP_DEFLATED) as bundle:
        for item in files:bundle.write(output/item['name'],item['name'])
    if _cancel_events[job['id']].is_set():raise asyncio.CancelledError()
    job.update(elapsed_seconds=round(time.perf_counter()-started,3),cache_hit=False,files=files,status='completed',stage='Reports ready',progress=100,completed_at=_now(),updated_at=_now());_save(job)

def _worker(job):
    async def entry():
        _tasks[job['id']]=(asyncio.get_running_loop(),asyncio.current_task())
        await _run(job)
    try:asyncio.run(entry())
    except asyncio.CancelledError:
        job.update(status='cancelled',stage='Run cancelled',updated_at=_now());_save(job)
    except Exception as exc:
        message=str(exc)
        cfg=json.loads((BASE/'connection.json').read_text()) if (BASE/'connection.json').exists() else {}
        for secret in [cfg.get('TELEGRAM_API_HASH'),cfg.get('TELEGRAM_PHONE')]:
            if secret:message=message.replace(str(secret),'[private]')
        job.update(status='failed',stage='Run stopped',error=message,updated_at=_now());_save(job)
    finally:_tasks.pop(job['id'],None);_cancel_events.pop(job['id'],None);_gate.release()

class RunRequest(BaseModel):
    start:date
    end:date
    source:str=Field(default='auto',pattern='^(auto|live|saved)$')
    refresh_members:bool=False
class Connection(BaseModel):
    api_id:int=Field(gt=0)
    api_hash:str=Field(default='',max_length=128)
    phone:str=Field(min_length=5,max_length=32)

@router.get('/status')
def status(_admin=Depends(current_admin)):
    s=_settings()
    saved_groups=0
    if s.database_path.exists():
        import sqlite3
        with sqlite3.connect(s.database_path) as con:
            saved_groups=con.execute("SELECT count(*) FROM telegram_groups WHERE is_active=1 AND source_kind='EXECUTION'").fetchone()[0]
    return {'saved_groups':saved_groups,'templates':{key:(BASE/'templates'/name).exists() for key,name in TEMPLATES.items()},'database_ready':s.database_path.exists(),
      'session_ready':s.session_path.with_suffix('.session').exists(),'connection_ready':bool(s.api_id and s.api_hash and s.phone),
      'phone_hint':('••••'+s.phone[-4:]) if s.phone else '', 'expected_groups':65,'today':datetime.now(ZoneInfo('Asia/Phnom_Penh')).date().isoformat()}
@router.put('/connection')
def connection(body:Connection,_admin=Depends(current_admin)):
    if _gate.locked():raise HTTPException(409,'Wait for the active run before changing the connection.')
    path=BASE/'connection.json';cfg=json.loads(path.read_text()) if path.exists() else {}
    if body.api_hash:cfg['TELEGRAM_API_HASH']=body.api_hash.strip()
    if not cfg.get('TELEGRAM_API_HASH'):raise HTTPException(422,'API hash is required for the first connection.')
    cfg.update(TELEGRAM_API_ID=str(body.api_id),TELEGRAM_PHONE=body.phone.strip());path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(cfg),encoding='utf-8')
    return {'saved':True}
@router.post('/templates/{kind}')
async def upload_template(kind:str,file:UploadFile=File(...),_admin=Depends(current_admin)):
    if kind not in TEMPLATES:raise HTTPException(404,'Unknown reference file.')
    if _gate.locked():raise HTTPException(409,'Wait for the active run before changing templates.')
    raw=await file.read(20*1024*1024+1)
    if len(raw)>20*1024*1024:raise HTTPException(413,'Maximum file size is 20 MB.')
    from io import BytesIO
    from openpyxl import load_workbook
    try:wb=load_workbook(BytesIO(raw),read_only=True);wb.close()
    except Exception:raise HTTPException(422,'Upload a valid .xlsx workbook.')
    target=BASE/'templates'/TEMPLATES[kind];target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        import shutil
        backup=BASE/'template_backups'/(_now().replace(':','-')+'_'+target.name);backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
    temp=target.with_suffix('.tmp');temp.write_bytes(raw);temp.replace(target)
    return {'saved':True,'name':target.name}
@router.get('/live-preview')
def live_preview(day:date|None=None,start:date|None=None,end:date|None=None,_admin=Depends(current_admin)):
    """Immediately render the Report GP & Report with Dealer template populated with live DB data."""
    import tempfile
    from app.gp_live_export import export_live_gp_summary
    today=datetime.now(ZoneInfo('Asia/Phnom_Penh')).date()
    win_start=start or day or today
    win_end=end or win_start
    if win_end<win_start:win_end=win_start
    template_path=BASE/'templates'/TEMPLATES['template']
    with tempfile.TemporaryDirectory(prefix='gp-preview-') as tmpdir:
        out=Path(tmpdir)/f'report_gp_{win_end.isoformat()}.xlsx'
        export_live_gp_summary(win_start.isoformat(),win_end.isoformat(),template_path,out)
        data=workbook_preview(out)
        data['start']=win_start.isoformat()
        data['end']=win_end.isoformat()
        data['updated_at']=_now()
        return data


@router.get('/live-download')
def live_download(day:date|None=None,start:date|None=None,end:date|None=None,_admin=Depends(current_admin)):
    """Download the live Report GP Excel workbook immediately without waiting for a background run."""
    import tempfile
    from app.gp_live_export import export_live_gp_summary
    today=datetime.now(ZoneInfo('Asia/Phnom_Penh')).date()
    win_start=start or day or today
    win_end=end or win_start
    if win_end<win_start:win_end=win_start
    template_path=BASE/'templates'/TEMPLATES['template']
    fname=f'report_gp_{win_start.isoformat()}.xlsx' if win_start==win_end else f'report_gp_{win_start.isoformat()}_to_{win_end.isoformat()}.xlsx'
    with tempfile.TemporaryDirectory(prefix='gp-dl-') as tmpdir:
        out=Path(tmpdir)/fname
        export_live_gp_summary(win_start.isoformat(),win_end.isoformat(),template_path,out)
        content=out.read_bytes()
    return Response(content=content,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':f'attachment; filename="{fname}"'})


@router.get('/runs')
def runs(_admin=Depends(current_admin)):
    JOBS.mkdir(parents=True,exist_ok=True);items=[]
    for path in JOBS.glob('*/job.json'):
        try:
            job=json.loads(path.read_text(encoding='utf-8'))
            if job['status'] in _ACTIVE and job['id'] not in _cancel_events:
                job.update(status='failed',stage='Interrupted by dashboard restart',error='Start a new run to continue.');_save(job)
            job['cancel_requested']=bool(_cancel_events.get(job['id']) and _cancel_events[job['id']].is_set())
            items.append(job)
        except (ValueError,KeyError,OSError):continue
    return {'items':sorted(items,key=lambda x:x['created_at'],reverse=True)[:50]}
@router.post('/runs',status_code=202)
def create_run(body:RunRequest,_admin=Depends(current_admin)):
    today=datetime.now(ZoneInfo('Asia/Phnom_Penh')).date()
    if body.end<body.start or (body.end-body.start).days>=31 or body.end>today or body.start<date(2026,8,18):
        raise HTTPException(422,'Choose an inclusive range of 1–31 days between 18 August 2026 and today.')
    if not _gate.acquire(blocking=False):raise HTTPException(409,'Another GP run is active. Wait for it to finish.')
    job={'id':str(uuid.uuid4()),'created_at':_now(),'updated_at':_now(),'start':body.start.isoformat(),'end':body.end.isoformat(),'source':body.source,
        'refresh_members':body.refresh_members,'status':'queued','stage':'Queued','progress':0,'steps':[],'files':[],'scan_results':[], 'provisional':body.end==today}
    try:
        _cancel_events[job['id']]=threading.Event();_save(job);threading.Thread(target=_worker,args=(job,),daemon=True).start()
    except Exception:_cancel_events.pop(job['id'],None);_gate.release();raise
    return _load(job['id'])
@router.get('/runs/{key}/download/{name}')
def download(key:str,name:str,_admin=Depends(current_admin)):
    job=_load(key)
    if job['status']!='completed':raise HTTPException(409,'Reports are not ready.')
    if name=='reports.zip':path=JOBS/key/name
    elif name in [x['name'] for x in job['files']]:path=JOBS/key/'outputs'/name
    else:raise HTTPException(404,'File not found.')
    return FileResponse(path,filename=name,media_type='application/zip' if name.endswith('.zip') else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

@router.post('/runs/{key}/cancel',status_code=202)
def cancel(key:str,_admin=Depends(current_admin)):
    _load(key)
    if key not in _cancel_events:raise HTTPException(409,'This run is no longer active.')
    _cancel_events[key].set()
    entry=_tasks.get(key)
    if entry is not None:
        loop,task=entry
        loop.call_soon_threadsafe(task.cancel)
    return {'requested':True}

@router.get('/runs/{key}/preview/{name}')
def preview(key:str,name:str,request:Request,_admin=Depends(current_admin)):
    job=_load(key)
    if job['status']!='completed':raise HTTPException(409,'Reports are not ready.')
    if name not in [x['name'] for x in job['files']]:raise HTTPException(404,'File not found.')
    with _report_lock:
        payload=cached_preview(JOBS/key/'outputs'/name,workbook_preview,getattr(request.app.state,'redis',None))
        return Response(content=json.dumps(payload,ensure_ascii=False,separators=(',',':')),media_type='application/json')

class CellEdit(BaseModel):
    sheet:str=Field(max_length=40)
    address:str=Field(max_length=6)
    value:str=Field(max_length=2000)
class ColumnVisibility(BaseModel):
    sheet:str=Field(max_length=40)
    hidden_columns:list[int]=Field(default_factory=list,max_length=44)
class WorkbookEdits(BaseModel):
    revision:str=Field(pattern='^[a-f0-9]{64}$')
    changes:list[CellEdit]=Field(default_factory=list,max_length=4200)
    visibility:list[ColumnVisibility]=Field(default_factory=list,max_length=2)

@router.patch('/runs/{key}/workbook/{name}')
def save_workbook(key:str,name:str,body:WorkbookEdits,_admin=Depends(current_admin)):
    import shutil
    with _report_lock:
        job=_load(key)
        if job['status']!='completed':raise HTTPException(409,'Reports are not ready.')
        if name not in [x['name'] for x in job['files']]:raise HTTPException(404,'File not found.')
        path=JOBS/key/'outputs'/name
        if report_revision(path)!=body.revision:
            raise HTTPException(409,'This workbook changed in another session. Reload it before saving your edits.')
        temporary=path.with_name(path.stem+'.editing.xlsx')
        changes=[x.model_dump() for x in body.changes]
        visibility=[x.model_dump() for x in body.visibility]
        if not changes and not visibility:raise HTTPException(422,'No report changes supplied.')
        try:
            edit_workbook(path,temporary,changes,visibility)
            result=workbook_preview(temporary)
            if result['formula_errors']:raise ValueError('Workbook contains unsupported formulas.')
        except ValueError as exc:
            temporary.unlink(missing_ok=True)
            raise HTTPException(422,str(exc))
        stamp=datetime.now().strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8]
        backup=JOBS/key/'edit_backups'/stamp
        backup.mkdir(parents=True)
        shutil.copy2(path,backup/name)
        (backup/'changes.json').write_text(json.dumps({'at':_now(),'changes':changes,'visibility':visibility},ensure_ascii=False),encoding='utf-8')
        temporary.replace(path)
        for item in job['files']:
            if item['name']==name:
                item.update(bytes=path.stat().st_size,edited_at=_now(),revision=result['revision'])
                item['validation']['manually_edited']=True
        job.update(updated_at=_now());_save(job)
        bundle=JOBS/key/'reports.editing.zip'
        with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
            for item in job['files']:archive.write(JOBS/key/'outputs'/item['name'],item['name'])
        bundle.replace(JOBS/key/'reports.zip')
        return result
