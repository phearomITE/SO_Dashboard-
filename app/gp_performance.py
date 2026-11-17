"""Bounded report caching and coverage planning; source files remain authoritative."""
import hashlib,json,shutil,threading,time,zlib
from collections import OrderedDict
from datetime import date,datetime,time as day_time,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_memory=OrderedDict()
_lock=threading.Lock()

def cached_preview(path,build,redis=None):
    stat=path.stat()
    identity=f'gp-preview-v3:{path}:{stat.st_mtime_ns}:{stat.st_size}'
    key='so-alert:gp:preview:'+hashlib.sha256(identity.encode()).hexdigest()
    if redis is not None:
        try:
            raw=redis.get(key)
            if raw:
                import base64
                return json.loads(zlib.decompress(base64.b64decode(raw)))
        except Exception:pass
    with _lock:
        entry=_memory.get(key)
        if entry and entry[0]>time.monotonic():
            _memory.move_to_end(key)
            return json.loads(zlib.decompress(entry[1]))
    result=build(path)
    packed=zlib.compress(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode(),3)
    with _lock:
        _memory[key]=(time.monotonic()+1800,packed)
        while len(_memory)>16:_memory.popitem(last=False)
    if redis is not None:
        try:
            import base64
            redis.setex(key,1800,base64.b64encode(packed).decode())
        except Exception:pass
    return result

def scan_plan(db,start,end,force=False,now=None):
    now=now or datetime.now(timezone.utc)
    zone=ZoneInfo('Asia/Phnom_Penh')
    groups=[dict(g) for g in db.groups()]
    with db.connect() as con:
        coverage={(int(row[0]),str(row[1])):str(row[2]) for row in con.execute(
            "SELECT group_id,report_date,completed_at FROM scrape_coverage WHERE track='OUTLET_VISIT' AND report_date BETWEEN ? AND ?",(start,end))}
    result=[]
    first,last=date.fromisoformat(start),date.fromisoformat(end)
    for offset in range((last-first).days+1):
        day=first+timedelta(days=offset)
        threshold=datetime.combine(day+timedelta(days=1),day_time.min,zone)
        if day==now.astimezone(zone).date():threshold=now-timedelta(seconds=60)
        pending=[]
        for group in groups:
            stamp=coverage.get((int(group['group_id']),day.isoformat()))
            try:complete=datetime.fromisoformat(stamp.replace('Z','+00:00'))>=threshold if stamp else False
            except (ValueError,TypeError):complete=False
            if force or not complete:pending.append(group)
        result.append((day.isoformat(),pending,len(groups)-len(pending)))
    return result

def source_fingerprint(settings):
    paths=[settings.database_path,Path(str(settings.database_path)+'-wal'),settings.gp_so_master_path,settings.staff_master_path,settings.report_gp_template_path,settings.aliases_path]
    reference=settings.database_path.parent.parent/'reference'
    paths.extend([reference/'Map_KML.kml',reference/'CambodiaCommuneBoundaries.geojson'])
    stamps=[]
    for path in paths:
        if path.exists():
            stat=path.stat();stamps.append((str(path.name),stat.st_size,stat.st_mtime_ns))
        else:stamps.append((str(path.name),None,None))
    return hashlib.sha256(json.dumps(['gp-summary-cache-v1',stamps]).encode()).hexdigest()

def artifact_key(job):return f"{job['start']}_{job['end']}"

def reuse_artifacts(base,job,settings):
    if job['source']=='live':return False
    directory=base/'report_cache'/artifact_key(job)
    manifest=directory/'manifest.json'
    try:
        data=json.loads(manifest.read_text())
        if data['fingerprint']!=source_fingerprint(settings):return False
        if job['source']=='auto' and data.get('source') not in {'auto','live'}:return False
        ttl=60 if job['end']==datetime.now(ZoneInfo('Asia/Phnom_Penh')).date().isoformat() else 43200
        if time.time()-data['created']>ttl:return False
        files=data['files']
        if not files or not all((directory/x['name']).is_file() for x in files):return False
        output=base/'jobs'/job['id']/'outputs';output.mkdir(parents=True,exist_ok=True)
        for item in files:shutil.copy2(directory/item['name'],output/item['name'])
        shutil.copy2(directory/'reports.zip',output.parent/'reports.zip')
        job.update(files=files,cache_hit=True)
        return True
    except (OSError,KeyError,ValueError):return False

def store_artifacts(base,job,settings):
    directory=base/'report_cache'/artifact_key(job);directory.mkdir(parents=True,exist_ok=True)
    for item in job['files']:shutil.copy2(base/'jobs'/job['id']/'outputs'/item['name'],directory/item['name'])
    shutil.copy2(base/'jobs'/job['id']/'reports.zip',directory/'reports.zip')
    temp=directory/'manifest.tmp'
    temp.write_text(json.dumps({'created':time.time(),'fingerprint':source_fingerprint(settings),'files':job['files'],'source':job['source']}))
    temp.replace(directory/'manifest.json')
