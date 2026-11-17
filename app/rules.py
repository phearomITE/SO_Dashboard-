from datetime import datetime,timedelta
import re,unicodedata

def windows(day,interval_minutes=15):
    interval_minutes=int(interval_minutes)
    if interval_minutes<5 or interval_minutes>60 or interval_minutes%5:
        raise ValueError('Alert interval must be 5-60 minutes in steps of 5.')
    result=[]
    for start,end in [('08:00','11:45'),('13:00','16:45')]:
        t=datetime.combine(day,datetime.strptime(start,'%H:%M').time())
        stop=datetime.combine(day,datetime.strptime(end,'%H:%M').time())
        while t<stop:
            next_time=min(t+timedelta(minutes=interval_minutes),stop)
            result.append((t,next_time));t=next_time
    return result

def normalize_position(position):
    value=' '.join(str(position or '').strip().replace('.','').split())
    lowered=value.casefold()
    if value.upper()=='SO' or 'sales officer' in lowered:return 'SO'
    if value.upper()=='SS' or 'sales supervisor' in lowered:return 'SS'
    return value
def is_so(position):return normalize_position(position)=='SO'
def is_ss(position):return normalize_position(position)=='SS'
def is_reporter(position):return normalize_position(position) in ('SO','SS')
def code(region,dealer):return f'{region}-{dealer}'.upper().strip()
def person(r):return {k:getattr(r,k) for k in ['employee','uid','name','username','region','dealer','position']}
def is_form(text):return bool(re.search(r'Outlet\s*Name\s*[:៖]',text,re.I) and re.search(r'Dealer\s*[:៖]',text,re.I))
def is_morning_talk(text):
    """Recognize Morning Talk headers despite Telegram/Markdown decoration."""
    cleaned=str(text or '').replace('\u200b','').replace('\ufeff','')
    return bool(re.search(r'(?im)^\s*(?:[*_`~]+\s*)*morning\s+talk\b',cleaned))

def _visible_text(value):
    """Normalize copied Telegram text while preserving Khmer letters."""
    value=unicodedata.normalize('NFKC',str(value or ''))
    return ''.join(ch for ch in value if unicodedata.category(ch)!='Cf')

def normalized_person_name(value):
    """Return a stable key for a person's name in attendance and roster data."""
    value=_visible_text(value)
    value=re.sub(r'[*_`~]','',value)
    value=re.sub(r'\([^)]*\)',' ',value)
    value=re.sub(r'[^\w\u1780-\u17ff]+',' ',value,flags=re.UNICODE)
    return ' '.join(value.casefold().split())

def normalized_employee_id(value):
    """Return a stable Employee ID key without changing leading zeroes."""
    return re.sub(r'[^0-9A-Za-z]+','',_visible_text(value)).casefold()

def morning_talk_date(text,fallback=None):
    """Read Date dd.mm.yyyy, dd/mm/yyyy, or dd-mm-yyyy from Morning Talk."""
    cleaned=_visible_text(text)
    match=re.search(r'(?im)^\s*(?:[*_`~]+\s*)*date\s*[:៖-]?\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(20\d{2})\b',cleaned)
    if match:
        try:return datetime(int(match[3]),int(match[2]),int(match[1])).date()
        except ValueError:pass
    return fallback

def morning_talk_full_day_leave_entries(text):
    """Extract terminal AL rows, preferring an explicit Employee ID.

    Supported examples include ``ID:90021882-Eol Phearun (SA): AL`` and
    legacy name-only rows such as ``Young Vanntha: AL``.
    """
    cleaned=_visible_text(text)
    if not is_morning_talk(cleaned):return []
    entries=[]
    for raw in cleaned.splitlines():
        line=re.sub(r'[*_`~]','',raw).strip()
        if re.search(r'(?i)competitor|compititor|focus\s+(?:new\s+)?product|\bposm\b',line):break
        numbered=re.match(r'^\s*\d+\s*[.)-]?\s*(.+?)\s*$',line)
        if not numbered:continue
        value=numbered.group(1).strip()
        # Only an explicit terminal leave marker counts; never classify a bare "No"
        # or a half-day entry as full-day leave.
        if re.search(r'(?i)\bhalf[ -]?(?:morning|afternoon|day)\b|\b(?:morning|afternoon)\s+(?:AL|A/L)\b',value):
            continue
        match=re.match(r'^(.*?)\s*(?:[:៖=-]\s*)?(?:No\s*\(\s*A\s*/?\s*L\s*\)|\(?\s*A\s*/?\s*L\s*\)?)\s*[.!。]*\s*$',value,re.I)
        if not match:continue
        identity=re.sub(r'\s*\([^)]*\)\s*$','',match.group(1)).strip(' :៖=-')
        employee_id='';name=identity
        id_match=re.match(
            r'(?i)^(?:employee\s*)?id\s*[:：#]?\s*([0-9A-Za-z]+)'
            r'(?:\s*[-–—]\s*(.*?))?\s*$',identity)
        if id_match:
            employee_id=id_match.group(1).strip()
            name=(id_match.group(2) or '').strip(' :៖=-')
        if normalized_employee_id(employee_id) or normalized_person_name(name):
            entries.append({'employee_id':employee_id,'name':name})
    return entries

def morning_talk_full_day_leave_names(text):
    """Compatibility helper returning names from explicit terminal AL rows."""
    return [entry['name'] for entry in morning_talk_full_day_leave_entries(text)]
def remark(text):
    m=re.search(r'Remark\s*Issues?\s*[:៖]\s*(.*)',text,re.I|re.S)
    return m.group(1).strip() if m else ''

def link_visits(events):
    """One logical report/album + one GPS per outlet; never reuse a GPS.

    Direct replies have priority. Otherwise nearest same-sender pin within five
    minutes is paired one-to-one. Photos in an album collapse to one report.
    Nearby uncaptioned photos are evidence for a form, not extra outlets.
    """
    reports=[]; pins=[]; albums={}
    for e in sorted(events,key=lambda e:(e.at,e.mid)):
        if e.kind=='pin':pins.append(e);continue
        if e.kind not in ('photo','form'):continue
        album=e.data.get('album')
        key=(e.chat,e.uid,album) if album else (e.chat,e.uid,e.mid)
        if key in albums:
            r=albums[key];r['events'].append(e)
            if is_form(e.data.get('text','')):r['text']=e.data['text']
        else:
            r={'event':e,'events':[e],'text':e.data.get('text',''),'pin':None,'match':''}
            albums[key]=r;reports.append(r)
    # Fold uncaptioned photos into a nearby textual outlet form.
    folded=[]
    for r in reports:
        e=r['event']
        if e.kind=='photo' and not is_form(r['text']):
            candidates=[f for f in reports if f is not r and is_form(f['text']) and f['event'].uid==e.uid and f['event'].chat==e.chat and abs((f['event'].at-e.at).total_seconds())<=120]
            if len(candidates)==1:
                candidates[0]['events'].extend(r['events']);folded.append(r)
    reports=[r for r in reports if r not in folded]
    used=set()
    for r in reports:
        e=r['event']; mids={x.mid for x in r['events']};replies={x.data.get('reply') for x in r['events']}
        direct=[p for p in pins if p.id not in used and p.uid==e.uid and p.chat==e.chat and (p.mid in replies or p.data.get('reply') in mids) and p.at.date()==e.at.date()]
        if direct:
            p=min(direct,key=lambda p:abs((p.at-e.at).total_seconds()));r.update(pin=p,match='Reply');used.add(p.id)
    for r in reports:
        if r['pin']:continue
        e=r['event']
        near=[p for p in pins if p.id not in used and p.uid==e.uid and p.chat==e.chat and p.at.date()==e.at.date() and min(abs((p.at-x.at).total_seconds()) for x in r['events'])<=300]
        if near:
            p=min(near,key=lambda p:abs((p.at-e.at).total_seconds()));r.update(pin=p,match='Same sender within 5 minutes');used.add(p.id)
    return reports

def photo_status(text,submitted):
    """Business rule: >30 minutes Review; <=30 minutes OK."""
    matches=re.findall(r'(?<!\d)(\d{1,2})\s*[:.]\s*(\d{2})\s*([AP])\s*\.?M\.?',text.upper())
    times=set()
    for h,m,ap in matches:
        h,m=int(h),int(m)
        if 1<=h<=12 and m<60:times.add((h%12+(12 if ap=='P' else 0),m))
    if not times:
        for h,m in re.findall(r'(?<![\d:.])(\d{1,2})\s*:\s*(\d{2})(?!\d)',text):
            h,m=int(h),int(m)
            if h<24 and m<60:times.add((h,m))
    if len(times)!=1:return {'status':'Review','gap':None,'photo_time':'','ocr_issue':'Unreadable or ambiguous timestamp'}
    h,m=next(iter(times)); dt=submitted.replace(hour=h,minute=m,second=0,microsecond=0)
    # If date is present, use it rather than silently assuming submission day.
    dm=re.search(r'(?<!\d)(\d{1,2})[/.-](\d{1,2})[/.-](20\d{2})(?!\d)',text)
    if dm:
        try:dt=dt.replace(year=int(dm[3]),month=int(dm[2]),day=int(dm[1]))
        except ValueError:return {'status':'Review','gap':None,'photo_time':'','ocr_issue':'Invalid photo date'}
    textual=re.search(r'(?<!\d)(\d{1,2})\s+([A-Za-z]{3,9})\s+(20\d{2})(?!\d)',text)
    if not dm and textual:
        for fmt in ('%d %b %Y','%d %B %Y'):
            try:
                parsed=datetime.strptime(textual.group(0),fmt);dt=dt.replace(year=parsed.year,month=parsed.month,day=parsed.day);dm=True;break
            except ValueError:pass
    gap=round((submitted-dt).total_seconds()/60,2)
    status='Review' if gap>30 or gap<0 else 'OK'
    return {'status':status,'gap':gap,'photo_time':dt.isoformat(' '),'ocr_issue':'Future/ambiguous date' if gap<0 else ('' if dm else 'Photo date not read; submission date assumed')}
