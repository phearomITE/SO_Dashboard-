from contextlib import contextmanager
from datetime import datetime
import os
from sqlalchemy import create_engine, String, Integer, BigInteger, DateTime, Date, Float, Boolean, Text, JSON, LargeBinary, UniqueConstraint, text, select, delete
from sqlalchemy.orm import DeclarativeBase, mapped_column, sessionmaker
from sqlalchemy.engine import URL
from app.config import TZ

class Base(DeclarativeBase): pass
class Roster(Base):
    __tablename__='so_roster_v1'
    id=mapped_column(Integer,primary_key=True)
    employee=mapped_column(String(80),unique=True)
    uid=mapped_column(BigInteger,index=True)
    name=mapped_column(String(255))
    username=mapped_column(String(255),default='')
    region=mapped_column(String(20))
    dealer=mapped_column(String(100))
    position=mapped_column(String(80))
    active=mapped_column(Integer,default=1)
    source=mapped_column(String(20),default='excel')
class Group(Base):
    __tablename__='so_groups_v1'
    id=mapped_column(BigInteger,primary_key=True)
    title=mapped_column(String(255))
    code=mapped_column(String(80))
    topic=mapped_column(BigInteger,nullable=True)
    test_uids=mapped_column(JSON,default=list)
    active=mapped_column(Integer,default=1)
class Controller(Base):
    __tablename__='so_controllers_v1'
    __table_args__=(UniqueConstraint('uid','code'),)
    id=mapped_column(Integer,primary_key=True)
    uid=mapped_column(BigInteger)
    code=mapped_column(String(80))
    name=mapped_column(String(255),default='')
    username=mapped_column(String(255),default='')
class State(Base):
    __tablename__='so_states_v1'
    key=mapped_column(String(200),primary_key=True)
    data=mapped_column(JSON,default=dict)
class Event(Base):
    __tablename__='so_events_v1'
    __table_args__=(UniqueConstraint('chat','mid'),)
    id=mapped_column(Integer,primary_key=True)
    chat=mapped_column(BigInteger,index=True)
    mid=mapped_column(BigInteger)
    uid=mapped_column(BigInteger,index=True)
    at=mapped_column(DateTime,index=True)
    kind=mapped_column(String(20))
    data=mapped_column(JSON,default=dict)
class Missing(Base):
    __tablename__='so_missing_v1'
    __table_args__=(UniqueConstraint('chat','uid','start'),)
    id=mapped_column(Integer,primary_key=True)
    chat=mapped_column(BigInteger,index=True)
    uid=mapped_column(BigInteger,index=True)
    start=mapped_column(DateTime,index=True)
    end=mapped_column(DateTime)
    sent=mapped_column(DateTime,nullable=True)
    alert_mid=mapped_column(BigInteger,nullable=True)
    question_mid=mapped_column(BigInteger,nullable=True)
    reason=mapped_column(Text,nullable=True)
    reason_at=mapped_column(DateTime,nullable=True)
    person=mapped_column(JSON,default=dict)
class Leave(Base):
    __tablename__='so_leave_v1'
    __table_args__=(UniqueConstraint('chat','uid','start','end'),)
    id=mapped_column(Integer,primary_key=True)
    chat=mapped_column(BigInteger,index=True)
    uid=mapped_column(BigInteger,index=True)
    employee=mapped_column(String(80),index=True)
    name=mapped_column(String(255))
    region=mapped_column(String(20),index=True)
    dealer=mapped_column(String(100),index=True)
    position=mapped_column(String(80))
    start=mapped_column(DateTime,index=True)
    end=mapped_column(DateTime,index=True)
    source=mapped_column(String(100),index=True)
    source_message_id=mapped_column(BigInteger,nullable=True)
    recorded_by=mapped_column(BigInteger,nullable=True)
    recorded_at=mapped_column(DateTime,index=True)
class Plan(Base):
    __tablename__='so_plans_v1'
    id=mapped_column(Integer,primary_key=True)
    batch=mapped_column(String(80),index=True)
    employee=mapped_column(String(80),index=True)
    day=mapped_column(String(10),index=True)
    data=mapped_column(JSON)
class PlanFile(Base):
    __tablename__='so_plan_files_v1'
    id=mapped_column(Integer,primary_key=True)
    batch=mapped_column(String(80),unique=True,index=True)
    filename=mapped_column(String(255))
    content=mapped_column(LargeBinary)
    uploaded_at=mapped_column(DateTime)
    admin=mapped_column(BigInteger)

# Small Data Engineering layer. Existing bot tables remain the immutable/raw
# source. These tables hold imported source rows, standardized dimensions,
# Power BI-ready visits, quality exceptions and reconciliation results.
class BronzeLocation(Base):
    __tablename__='bronze_location_all_v1'
    __table_args__=(UniqueConstraint('batch','source_row'),)
    id=mapped_column(Integer,primary_key=True)
    batch=mapped_column(String(64),index=True)
    source_file=mapped_column(String(255))
    source_row=mapped_column(Integer)
    source_hash=mapped_column(String(64),index=True)
    payload=mapped_column(JSON)
    loaded_at=mapped_column(DateTime,index=True)
class SilverLocation(Base):
    __tablename__='silver_location_v1'
    commune_code=mapped_column(String(6),primary_key=True)
    province_code=mapped_column(String(2),index=True)
    province_name=mapped_column(String(255))
    province_name_kh=mapped_column(String(255))
    district_code=mapped_column(String(4),index=True)
    district_name=mapped_column(String(255))
    district_name_kh=mapped_column(String(255))
    commune_name=mapped_column(String(255))
    commune_name_kh=mapped_column(String(255))
    commune_district_kh=mapped_column(String(500))
    dealer=mapped_column(String(100),index=True)
    region=mapped_column(String(20),index=True)
    source_hash=mapped_column(String(64))
    batch=mapped_column(String(64),index=True)
    updated_at=mapped_column(DateTime)
class SilverVisit(Base):
    __tablename__='silver_so_visit_v1'
    visit_id=mapped_column(Integer,primary_key=True)
    source_event_id=mapped_column(Integer,unique=True,index=True)
    chat_id=mapped_column(BigInteger,index=True)
    group_name=mapped_column(String(255))
    message_id=mapped_column(BigInteger)
    telegram_id=mapped_column(BigInteger,index=True)
    employee_id=mapped_column(String(80),index=True)
    real_name=mapped_column(String(255),index=True)
    username=mapped_column(String(255))
    region=mapped_column(String(20),index=True)
    dealer=mapped_column(String(100),index=True)
    position=mapped_column(String(80))
    submitted_at=mapped_column(DateTime,index=True)
    report_date=mapped_column(Date,index=True)
    report_time=mapped_column(String(8))
    outlet_name=mapped_column(String(500))
    phone=mapped_column(String(100))
    outlet_type=mapped_column(String(100))
    banner_type=mapped_column(Text)
    posm=mapped_column(Text)
    program=mapped_column(Text)
    emergency_outlet=mapped_column(Text)
    attacking_stock=mapped_column(Text)
    tour_program_outlet=mapped_column(Text)
    key_wholesale=mapped_column(Text)
    water=mapped_column(Text)
    c_cola=mapped_column(Text)
    ize_pet_300ml=mapped_column(Text)
    ize_pet_1500ml=mapped_column(Text)
    product_display=mapped_column(Text)
    latitude=mapped_column(Float)
    longitude=mapped_column(Float)
    actual_province=mapped_column(String(255))
    actual_province_kh=mapped_column(String(255))
    actual_district=mapped_column(String(255))
    actual_district_kh=mapped_column(String(255))
    actual_commune=mapped_column(String(255))
    actual_commune_kh=mapped_column(String(255))
    actual_commune_code=mapped_column(String(6),index=True)
    plan_check=mapped_column(String(40),index=True)
    plan_reason=mapped_column(Text)
    route_leg=mapped_column(String(100))
    road_distance_km=mapped_column(Float)
    route_status=mapped_column(String(40),index=True)
    photo_time=mapped_column(String(40))
    delay_minutes=mapped_column(Float)
    time_status=mapped_column(String(40),index=True)
    missing_reason=mapped_column(Text)
    wrong_plan_reason=mapped_column(Text)
    over_10km_reason=mapped_column(Text)
    reason_status=mapped_column(String(40))
    remark_issue=mapped_column(Text)
    gps_match=mapped_column(String(100))
    ocr_issue=mapped_column(Text)
    open_map=mapped_column(Text)
    open_image=mapped_column(Text)
    open_telegram=mapped_column(Text)
    raw_text=mapped_column(Text)
    source_hash=mapped_column(String(64),index=True)
    processed_at=mapped_column(DateTime)
class WarehouseRun(Base):
    __tablename__='etl_run_v1'
    id=mapped_column(Integer,primary_key=True)
    started_at=mapped_column(DateTime,index=True)
    completed_at=mapped_column(DateTime,nullable=True)
    mode=mapped_column(String(30),default='incremental')
    status=mapped_column(String(30),index=True)
    inserted=mapped_column(Integer,default=0)
    updated=mapped_column(Integer,default=0)
    unchanged=mapped_column(Integer,default=0)
    rejected=mapped_column(Integer,default=0)
    metrics=mapped_column(JSON,default=dict)
    error=mapped_column(Text,nullable=True)
class QualityIssue(Base):
    __tablename__='dq_issue_v1'
    id=mapped_column(Integer,primary_key=True)
    run_id=mapped_column(Integer,index=True)
    entity_type=mapped_column(String(40),index=True)
    entity_id=mapped_column(String(100),index=True)
    rule_code=mapped_column(String(80),index=True)
    severity=mapped_column(String(20),index=True)
    detail=mapped_column(Text)
    detected_at=mapped_column(DateTime,index=True)
class Reconciliation(Base):
    __tablename__='reconciliation_v1'
    id=mapped_column(Integer,primary_key=True)
    run_id=mapped_column(Integer,index=True)
    check_name=mapped_column(String(100),index=True)
    source_count=mapped_column(Integer)
    target_count=mapped_column(Integer)
    difference=mapped_column(Integer)
    status=mapped_column(String(20),index=True)
    checked_at=mapped_column(DateTime,index=True)
class ReportImage(Base):
    __tablename__='so_report_images_v1'
    filename=mapped_column(String(120),primary_key=True)
    chat_id=mapped_column(BigInteger,index=True)
    message_id=mapped_column(BigInteger,index=True)
    content_type=mapped_column(String(60),default='image/jpeg')
    data=mapped_column(LargeBinary)
    size_bytes=mapped_column(Integer)
    created_at=mapped_column(DateTime,index=True)

engine=None
Session=None
lock_connection=None

def now(): return datetime.now(TZ).replace(tzinfo=None)
_ALREADY_ENCODED=None
def _normalize_database_url(raw):
    import re, urllib.parse
    global _ALREADY_ENCODED
    if _ALREADY_ENCODED is None:_ALREADY_ENCODED=re.compile(r'^(?:[A-Za-z0-9._~-]|%[0-9A-Fa-f]{2})*$')
    value=(raw or '').strip()
    if value.startswith('postgres://'):value = 'postgresql+psycopg2://'+value[len('postgres://'):]
    elif value.startswith('postgresql://'):value = 'postgresql+psycopg2://'+value[len('postgresql://'):]
    m = re.match(r'^(.*?://)([^:@/?#]+):(.*)@([^@]+)$', value)
    host = ''
    if m:
        scheme, user, pwd, host_part = m.groups()
        if not _ALREADY_ENCODED.match(pwd):
            pwd = urllib.parse.quote(pwd, safe='')
        value = scheme + user + ':' + pwd + '@' + host_part
        host = host_part.split('/', 1)[0].split('?', 1)[0].split(':', 1)[0].strip().lower()
    elif '://' in value:
        after = value.split('://', 1)[1].split('@')[-1]
        host = after.split('/', 1)[0].split('?', 1)[0].split(':', 1)[0].strip().lower()
    if value.startswith('postgresql') and host not in {'', 'localhost', '127.0.0.1', 'postgres', 'host.docker.internal'}:
        query = value.split('?', 1)[1] if '?' in value else ''
        if 'sslmode=' not in query.lower():
            value += ('&' if '?' in value else '?') + 'sslmode=require'
    return value

def database_url():
    env_url=_normalize_database_url(os.getenv('DATABASE_URL'))
    return env_url or URL.create('postgresql+psycopg2',username=os.getenv('DB_USER','postgres'),password=os.getenv('DB_PASSWORD',''),host=os.getenv('DB_HOST') or '127.0.0.1',port=int(os.getenv('DB_PORT','5435')),database=os.getenv('DB_NAME','SA_Alert'))

def new_engine(url=None):
    from sqlalchemy.engine import make_url
    target=make_url(_normalize_database_url(url) if isinstance(url,str) else (url or database_url()))
    external=bool((os.getenv('DATABASE_URL') or '').strip())
    if url is None and external and os.getenv('DATABASE_PASSWORD'):
        target=target.set(password=os.getenv('DATABASE_PASSWORD'))
    options={'connect_timeout':max(3,int(os.getenv('DB_CONNECT_TIMEOUT','10')))} if target.get_backend_name()=='postgresql' else {}
    pool_options={}
    if target.get_backend_name()=='postgresql':
        pool_size=int(os.getenv('DB_POOL_SIZE','3' if external else '20'))
        max_overflow=int(os.getenv('DB_MAX_OVERFLOW','3' if external else '40'))
        pool_options={'pool_size':pool_size,'max_overflow':max_overflow,'pool_timeout':30,'pool_recycle':1800}
    return create_engine(target,pool_pre_ping=True,connect_args=options,**pool_options)

def save_report_image(s,filename,chat_id,message_id,content,content_type='image/jpeg'):
    """Persist downloaded report image bytes in PostgreSQL for durable cloud serving."""
    if not content:return
    existing=s.get(ReportImage,filename)
    if existing:
        existing.chat_id=chat_id;existing.message_id=message_id
        existing.content_type=content_type;existing.data=content
        existing.size_bytes=len(content);existing.created_at=now()
    else:
        s.add(ReportImage(filename=filename,chat_id=chat_id,message_id=message_id,
                          content_type=content_type,data=content,size_bytes=len(content),created_at=now()))

def load_report_image(target_engine,filename):
    """Fetch stored report image bytes and media type from PostgreSQL."""
    if target_engine is None:return None
    with target_engine.connect() as conn:
        row=conn.execute(select(ReportImage.data,ReportImage.content_type).where(ReportImage.filename==filename)).first()
    return (bytes(row[0]),row[1] or 'image/jpeg') if row and row[0] else None

def probe_connection():
    # Diagnostics must not create tables or modify data.
    from sqlalchemy import text
    probe=new_engine()
    try:
        with probe.connect() as c:return c.execute(text('SELECT 1')).scalar()==1
    finally:probe.dispose()

def connect(url=None):
    global engine,Session
    candidate=new_engine(url)
    try:
        if candidate.dialect.name=='postgresql':
            with candidate.begin() as connection:
                connection.execute(text('SELECT pg_advisory_xact_lock(835501520)'))
                Base.metadata.create_all(connection)
                connection.execute(text('ALTER TABLE silver_so_visit_v1 ADD COLUMN IF NOT EXISTS wrong_plan_reason TEXT'))
                connection.execute(text('ALTER TABLE silver_so_visit_v1 ADD COLUMN IF NOT EXISTS over_10km_reason TEXT'))
                install_gold_views(connection)
        else:
            Base.metadata.create_all(candidate)
    except Exception:
        candidate.dispose();raise
    engine=candidate
    Session=sessionmaker(engine,expire_on_commit=False)

def purge_test_data(s,extra_chat_ids=None):
    """Permanently remove Test_SO/R9 activity before production processing.

    The known chat ID remains useful after an earlier cleanup deleted the group
    registration but left facts behind.  Silver group names provide a second
    recovery path for legacy databases.
    """
    from app.config import EXCLUDED_TEST_CHAT_IDS
    chat_ids=set(EXCLUDED_TEST_CHAT_IDS)
    chat_ids.update(int(x) for x in (extra_chat_ids or []) if x is not None)
    for group in s.scalars(select(Group)):
        title=' '.join(str(group.title or '').strip().casefold().split())
        code=str(group.code or '').strip().upper()
        if title=='test_so' or code.startswith('R9-') or code=='R9':chat_ids.add(group.id)
    for visit in s.scalars(select(SilverVisit)):
        title=' '.join(str(visit.group_name or '').strip().casefold().split())
        if title=='test_so' or visit.chat_id in chat_ids:chat_ids.add(visit.chat_id)
    if not chat_ids:return {'chat_ids':0,'events':0,'missing':0,'visits':0,'groups':0,'states':0,'quality':0}

    event_ids=[x for x in s.scalars(select(Event.id).where(Event.chat.in_(chat_ids)))]
    visit_event_ids=[x for x in s.scalars(select(SilverVisit.source_event_id).where(SilverVisit.chat_id.in_(chat_ids)))]
    affected={str(x) for x in event_ids+visit_event_ids if x is not None}
    quality=0
    if affected:
        quality=s.execute(delete(QualityIssue).where(QualityIssue.entity_id.in_(affected))).rowcount or 0
    missing=s.execute(delete(Missing).where(Missing.chat.in_(chat_ids))).rowcount or 0
    s.execute(delete(Leave).where(Leave.chat.in_(chat_ids)))
    visits=s.execute(delete(SilverVisit).where(SilverVisit.chat_id.in_(chat_ids))).rowcount or 0
    events=s.execute(delete(Event).where(Event.chat.in_(chat_ids))).rowcount or 0
    states=0
    prefixes=tuple(f'{name}:{chat}:' for chat in chat_ids for name in ('checked','excluded','leave','new_member_alert'))
    for row in list(s.scalars(select(State))):
        if str(row.key or '').startswith(prefixes):s.delete(row);states+=1
    groups=s.execute(delete(Group).where(Group.id.in_(chat_ids))).rowcount or 0
    # Test-only R9 controller mappings must not survive into production.
    s.execute(delete(Controller).where((Controller.code=='R9') | (Controller.code.like('R9-%'))))
    return {'chat_ids':len(chat_ids),'events':events,'missing':missing,'visits':visits,
            'groups':groups,'states':states,'quality':quality}

def gold_view_statements():
    """Return the PostgreSQL Gold star-schema DDL used by Power BI."""
    return [
        '''DROP VIEW IF EXISTS gold_powerbi_relationship_manifest_v1,gold_fact_live_route_v1,gold_fact_live_location_history_v1,gold_fact_live_location_v1,gold_fact_road_exception_v1,gold_fact_wrong_plan_v1,gold_fact_missing_report_v1,gold_fact_user_al_v1,
             gold_fact_plan_v1,gold_fact_so_visit_v1,gold_dim_group_v1,gold_dim_dealer_v1,
             gold_dim_location_v1,gold_dim_employee_v1,gold_dim_date_v1 CASCADE''',
        '''CREATE VIEW gold_dim_employee_v1 AS
           SELECT DISTINCT ON (employee)
                  employee AS employee_id,uid AS telegram_id,name AS real_name,username,region,dealer,
                  concat_ws('-',region,dealer) AS dealer_key,position,active,source
           FROM so_roster_v1
           WHERE upper(trim(position)) IN ('SO','SS') AND employee<>''
           ORDER BY employee,active DESC,(source='excel') DESC,id DESC''',
        '''CREATE VIEW gold_dim_location_v1 AS
           SELECT commune_code AS location_key,commune_code,province_code,province_name,province_name_kh,
                  district_code,district_name,district_name_kh,commune_name,commune_name_kh,
                  commune_district_kh,dealer,region,concat_ws('-',region,dealer) AS dealer_key,
                  batch,source_hash,updated_at
           FROM silver_location_v1''',
        '''CREATE VIEW gold_dim_dealer_v1 AS
           SELECT region||'-'||dealer AS dealer_key,region,dealer
           FROM (VALUES
             ('R1','CA1'),('R1','CA2'),('R1','CA3'),('R1','CA4'),('R1','CA5'),
             ('R1','CA6'),('R1','CA7'),('R1','CA8'),('R1','CA9'),('R1','PTM6'),
             ('R2','BTI6'),('R2','CPH2'),('R2','KDL1'),('R2','KHT3'),('R2','KRV5'),
             ('R2','KSV5'),('R2','KTB1'),('R2','PKB4'),('R2','PMR5'),('R2','SVA7'),
             ('R2','SVR4'),('R2','TAK2'),
             ('R3','CUK7'),('R3','KKG3'),('R3','KPS7'),('R3','KPT2'),('R3','KSM1'),
             ('R3','KSP3'),('R3','KTR4'),('R3','PNP6'),('R3','SRA5'),('R3','TPG2'),
             ('R4','BTB1'),('R4','BVL6'),('R4','KCG4'),('R4','KTL6'),('R4','MRS5'),
             ('R4','PLN4'),('R4','PPT3'),('R4','PUS5'),('R4','VVG1'),
             ('R5','BMC2'),('R5','CHK6'),('R5','KRG7'),('R5','KTH2'),('R5','KTM8'),
             ('R5','PPR8'),('R5','PUK7'),('R5','SRP1'),
             ('R6','BTY8'),('R6','KCM1'),('R6','MMT3'),('R6','STT4'),('R6','TKM2'),
             ('R7','BKE7'),('R7','KRT1'),('R7','KSA5'),('R7','MDK2'),('R7','RTK4'),
             ('R7','SNL6'),('R7','STR3'),
             ('R8','AVG4'),('R8','CKS1'),('R8','PVH3'),('R8','SMR5')
           ) AS canonical_dealers(region,dealer)''',
        '''CREATE VIEW gold_dim_group_v1 AS
           SELECT id AS chat_id,title AS group_name,code AS group_code,split_part(code,'-',1) AS region,
                  substring(code from position('-' in code)+1) AS dealer,code AS dealer_key,topic,active
           FROM so_groups_v1
WHERE lower(trim(title))<>'test_so' AND upper(trim(code)) NOT LIKE 'R9-%'
           ''',
        '''CREATE VIEW gold_fact_missing_report_v1 AS
           SELECT coalesce(nullif(m.person->>'region',''),r.region,split_part(g.code,'-',1),'') AS region,
                  coalesce(nullif(m.person->>'dealer',''),r.dealer,substring(g.code from position('-' in g.code)+1),'') AS dealer,
                  concat_ws('-',
                    coalesce(nullif(m.person->>'region',''),r.region,split_part(g.code,'-',1),''),
                    coalesce(nullif(m.person->>'dealer',''),r.dealer,substring(g.code from position('-' in g.code)+1),'')
                  ) AS dealer_key,
                  m.start::date AS date_miss,
                  'Time Submitted: '||to_char(m.start,'HH12:MI AM')||' - '||to_char(m.end,'HH12:MI AM') AS time_miss_submit,
                  1::integer AS missing_count,
                  coalesce(nullif(m.person->>'name',''),r.name,'') AS real_name,
                  coalesce(r.employee,nullif(m.person->>'employee',''),'') AS employee_id,
                  m.uid AS telegram_id,
                  coalesce(nullif(m.person->>'position',''),r.position,'SO') AS position,
                  coalesce(nullif(m.person->>'username',''),r.username,'') AS username,
                  coalesce(m.reason,'') AS reason_miss
           FROM so_missing_v1 m
           LEFT JOIN LATERAL (
             SELECT x.employee,x.name,x.username,x.region,x.dealer,x.position
             FROM so_roster_v1 x WHERE x.uid=m.uid AND upper(trim(x.position)) IN ('SO','SS')
             ORDER BY x.active DESC,x.id LIMIT 1) r ON true
           LEFT JOIN so_groups_v1 g ON g.id=m.chat
           WHERE upper(trim(coalesce(nullif(m.person->>'position',''),r.position,'SO'))) IN ('SO','SS')
             AND m.chat<>-1004303041907
             AND lower(trim(coalesce(g.title,'')))<>'test_so'
AND upper(trim(coalesce(g.code,''))) NOT LIKE 'R9-%'
           ''',
        '''CREATE VIEW gold_fact_user_al_v1 AS
           SELECT l.id AS leave_id,l.start::date AS date_al,
                  to_char(l.start,'HH12:MI AM') AS time_start,
                  to_char(l.end,'HH12:MI AM') AS time_end,
                  l.start,l.end,l.uid AS telegram_id,l.employee AS employee_id,
                  l.name AS real_name,l.position,l.region,l.dealer,
                  concat_ws('-',l.region,l.dealer) AS dealer_key,
                  g.title AS group_name,l.chat AS chat_id,
                  l.source,l.source_message_id,l.recorded_by,l.recorded_at
           FROM so_leave_v1 l LEFT JOIN so_groups_v1 g ON g.id=l.chat
           WHERE l.chat<>-1004303041907
             AND lower(trim(coalesce(g.title,'')))<>'test_so'
             AND upper(trim(coalesce(g.code,''))) NOT LIKE 'R9-%' ''',
        '''CREATE VIEW gold_fact_plan_v1 AS
           SELECT p.id,p.batch,p.employee AS employee_id,p.employee AS employee_key,p.day::date AS plan_date,
                  p.day::date AS date_key,r.uid AS telegram_id,
                  coalesce(nullif(p.data->>'region',''),r.region,'') AS region,
                  coalesce(nullif(p.data->>'dealer',''),r.dealer,'') AS dealer,
                  concat_ws('-',coalesce(nullif(p.data->>'region',''),r.region,''),coalesce(nullif(p.data->>'dealer',''),r.dealer,'')) AS dealer_key,
                  coalesce(nullif(p.data->>'sale_name',''),r.name,'') AS real_name,r.position,
                  p.data->>'district' AS district,p.data->>'commune' AS commune,
                  p.data->>'commune_code' AS commune_code,p.data->>'commune_code' AS location_key,
                  l.province_name AS province,l.province_name_kh AS province_kh,l.district_name_kh AS district_kh,
                  l.commune_name_kh AS commune_kh,p.data->>'mark' AS plan_mark,1::integer AS planned_visit_count
           FROM so_plans_v1 p LEFT JOIN so_roster_v1 r ON r.employee=p.employee
           LEFT JOIN silver_location_v1 l ON l.commune_code=p.data->>'commune_code'
           WHERE p.batch=(SELECT data->>'batch' FROM so_states_v1 WHERE key='plan_latest')
             AND upper(trim(coalesce(r.position,'SO'))) IN ('SO','SS')''',
        '''CREATE VIEW gold_fact_wrong_plan_v1 AS
           SELECT v.region,v.dealer,concat_ws('-',v.region,v.dealer) AS dealer_key,
                  v.report_date AS date_wrong_plan,
                  to_char(v.submitted_at,'HH12:MI AM') AS time_wrong_plan,
                  v.employee_id,v.telegram_id,v.position,v.real_name,v.username,
                  v.longitude,v.latitude,v.actual_province AS province,
                  v.actual_district AS district,v.actual_commune AS commune,
                  v.actual_commune_code AS location_key,
                  1::integer AS visit_count,coalesce(v.wrong_plan_reason,'') AS wrong_plan_reason,
                  CASE WHEN v.plan_check='Correct' THEN 1 ELSE 0 END AS plan_correct_count,
                  CASE WHEN v.plan_check='Incorrect' THEN 1 ELSE 0 END AS plan_incorrect_count,
                  v.open_telegram,v.open_map,v.open_image
           FROM silver_so_visit_v1 v
           WHERE v.plan_check='Incorrect' AND upper(trim(v.position)) IN ('SO','SS')
AND v.chat_id<>-1004303041907 AND lower(trim(v.group_name))<>'test_so'
           ''',
        '''CREATE VIEW gold_fact_road_exception_v1 AS
           WITH sequenced AS (
             SELECT v.*,
                    lag(v.actual_commune) OVER w AS from_commune,
                    lag(v.actual_district) OVER w AS from_district,
                    lag(v.actual_province) OVER w AS from_province
             FROM silver_so_visit_v1 v
             WHERE upper(trim(v.position)) IN ('SO','SS')
               AND v.chat_id<>-1004303041907 AND lower(trim(v.group_name))<>'test_so'
               AND v.latitude IS NOT NULL AND v.longitude IS NOT NULL
             WINDOW w AS (PARTITION BY v.chat_id,v.telegram_id,v.report_date ORDER BY v.submitted_at,v.message_id))
           SELECT region,dealer,concat_ws('-',region,dealer) AS dealer_key,
                  employee_id,real_name,username,position,
                  to_char(submitted_at,'HH12:MI AM') AS time_over_10km,
                  report_date AS date_over_10km,
                  actual_province AS province,actual_district AS district,actual_commune AS commune,
                  actual_commune_code AS location_key,
                  1::integer AS over_10km_count,
                  road_distance_km,
                  concat_ws(' / ',nullif(from_commune,''),nullif(from_district,''),nullif(from_province,''))
                    ||' to '||
                  concat_ws(' / ',nullif(actual_commune,''),nullif(actual_district,''),nullif(actual_province,'')) AS route_leg,
                  open_map,open_telegram,latitude,longitude,
                  coalesce(over_10km_reason,'') AS over_10km_reason,open_image,1::integer AS visit_count
           FROM sequenced
           WHERE route_leg='Previous Check-in to Current Check-in' AND road_distance_km>10''',
        '''CREATE VIEW gold_fact_live_location_v1 AS
           WITH latest AS (
             SELECT DISTINCT ON (e.uid)
                    e.id AS pin_id,e.uid AS telegram_id,e.chat AS chat_id,e.mid AS message_id,e.at AS pin_at,
                    (e.data->>'lat')::double precision AS latitude,
                    (e.data->>'lon')::double precision AS longitude,
                    e.data->'geo' AS geo,e.data->'person' AS person,
                    coalesce(e.data->>'pin_source','telegram_location') AS pin_source
             FROM so_events_v1 e
             LEFT JOIN so_groups_v1 gx ON gx.id=e.chat
             WHERE e.kind='pin'
               -- Events are stored as naive Asia/Phnom_Penh local timestamps.
               -- Evaluate the date at query time so this view rolls over
               -- automatically at Cambodia midnight without rebuilding it.
               AND e.at::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')::date
               AND (e.data->>'lat') ~ '^-?[0-9]+([.][0-9]+)?$'
               AND (e.data->>'lon') ~ '^-?[0-9]+([.][0-9]+)?$'
               AND (e.data->>'lat')::double precision BETWEEN 9.80 AND 14.75
               AND (e.data->>'lon')::double precision BETWEEN 102.30 AND 107.70
               AND e.chat<>-1004303041907
               AND lower(trim(coalesce(gx.title,'')))<>'test_so'
             ORDER BY e.uid,e.at DESC,e.mid DESC)
           SELECT l.pin_id,l.pin_id::text AS point_key,l.pin_at::date AS date_key,l.pin_at,
                  to_char(l.pin_at,'DD/MM/YYYY') AS pin_date,
                  to_char(l.pin_at,'HH12:MI AM') AS pin_time,l.telegram_id,
                  coalesce(r.employee,l.person->>'employee','') AS employee_id,
                  coalesce(r.name,l.person->>'name','') AS real_name,
                  coalesce(nullif(r.username,''),l.person->>'username','') AS username,
                  coalesce(r.position,l.person->>'position','SO') AS position,
                  coalesce(r.region,l.person->>'region',split_part(g.code,'-',1),'') AS region,
                  coalesce(r.dealer,l.person->>'dealer',substring(g.code from position('-' in g.code)+1),'') AS dealer,
                  concat_ws('-',coalesce(r.region,l.person->>'region',split_part(g.code,'-',1),''),
                                coalesce(r.dealer,l.person->>'dealer',substring(g.code from position('-' in g.code)+1),'')) AS dealer_key,
                  l.chat_id,g.title AS group_name,l.latitude,l.longitude,
                  coalesce(l.geo->>'province','') AS province,coalesce(l.geo->>'district','') AS district,
                  coalesce(l.geo->>'commune','') AS commune,coalesce(l.geo->>'commune_code','') AS commune_code,
                  coalesce(l.geo->>'commune_code','') AS location_key,
                  l.pin_source,
                  greatest(0,floor(extract(epoch from ((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')-l.pin_at))/60))::integer AS minutes_since_pin,
                  CASE WHEN l.pin_at>=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')-interval '30 minutes' THEN 'Active'
                       WHEN l.pin_at>=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')-interval '2 hours' THEN 'Recent'
                       ELSE 'Inactive' END AS activity_status,
                  concat_ws(' / ',nullif(l.geo->>'commune',''),nullif(l.geo->>'district',''),nullif(l.geo->>'province','')) AS full_address,
                  'https://maps.google.com/maps?q='||l.latitude||','||l.longitude AS open_map,
                  CASE WHEN l.chat_id<0 THEN 'https://t.me/c/'||substring(abs(l.chat_id)::text from 4)||'/'||l.message_id ELSE '' END AS open_telegram
           FROM latest l
           LEFT JOIN LATERAL (
             SELECT x.employee,x.name,x.username,x.region,x.dealer,x.position
             FROM so_roster_v1 x WHERE x.uid=l.telegram_id AND x.active=1 AND upper(trim(x.position)) IN ('SO','SS')
             ORDER BY x.source='excel' DESC,x.id LIMIT 1) r ON true
           LEFT JOIN so_groups_v1 g ON g.id=l.chat_id
           WHERE upper(trim(coalesce(r.position,l.person->>'position','SO'))) IN ('SO','SS')''',
        '''CREATE VIEW gold_fact_live_location_history_v1 AS
           WITH pins AS (
             SELECT e.id AS pin_id,e.uid AS telegram_id,e.chat AS chat_id,e.mid AS message_id,e.at AS pin_at,
                    (e.data->>'lat')::double precision AS latitude,
                    (e.data->>'lon')::double precision AS longitude,
                    e.data->'geo' AS geo,e.data->'person' AS person,
                    coalesce(e.data->>'pin_source','telegram_location') AS pin_source
             FROM so_events_v1 e
             LEFT JOIN so_groups_v1 gx ON gx.id=e.chat
             WHERE e.kind='pin'
               AND e.at::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')::date
               AND (e.data->>'lat') ~ '^-?[0-9]+([.][0-9]+)?$'
               AND (e.data->>'lon') ~ '^-?[0-9]+([.][0-9]+)?$'
               AND (e.data->>'lat')::double precision BETWEEN 9.80 AND 14.75
               AND (e.data->>'lon')::double precision BETWEEN 102.30 AND 107.70
               AND e.chat<>-1004303041907
               AND lower(trim(coalesce(gx.title,'')))<>'test_so')
           SELECT p.pin_id,p.pin_id::text AS point_key,p.pin_at::date AS date_key,p.pin_at,
                  to_char(p.pin_at,'DD/MM/YYYY') AS pin_date,to_char(p.pin_at,'HH12:MI AM') AS pin_time,
                  p.telegram_id,coalesce(r.employee,p.person->>'employee','') AS employee_id,
                  coalesce(r.name,p.person->>'name','') AS real_name,
                  coalesce(nullif(r.username,''),p.person->>'username','') AS username,
                  coalesce(r.position,p.person->>'position','SO') AS position,
                  coalesce(r.region,p.person->>'region',split_part(g.code,'-',1),'') AS region,
                  coalesce(r.dealer,p.person->>'dealer',substring(g.code from position('-' in g.code)+1),'') AS dealer,
                  concat_ws('-',coalesce(r.region,p.person->>'region',split_part(g.code,'-',1),''),
                                coalesce(r.dealer,p.person->>'dealer',substring(g.code from position('-' in g.code)+1),'')) AS dealer_key,
                  p.chat_id,g.title AS group_name,p.latitude,p.longitude,
                  coalesce(p.geo->>'province','') AS province,coalesce(p.geo->>'district','') AS district,
                  coalesce(p.geo->>'commune','') AS commune,coalesce(p.geo->>'commune_code','') AS commune_code,
                  coalesce(p.geo->>'commune_code','') AS location_key,
                  concat_ws(' / ',nullif(p.geo->>'commune',''),nullif(p.geo->>'district',''),nullif(p.geo->>'province','')) AS full_address,
                  p.pin_source,
                  p.telegram_id::text||'-'||to_char(p.pin_at,'YYYYMMDD') AS path_id,
                  row_number() OVER (PARTITION BY p.telegram_id,p.pin_at::date ORDER BY p.pin_at,p.message_id) AS point_order,
                  greatest(0,floor(extract(epoch from ((CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')-p.pin_at))/60))::integer AS minutes_since_pin,
                  CASE WHEN p.pin_at>=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')-interval '30 minutes' THEN 'Active'
                       WHEN p.pin_at>=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')-interval '2 hours' THEN 'Recent'
                       ELSE 'Inactive' END AS activity_status,
                  'https://maps.google.com/maps?q='||p.latitude||','||p.longitude AS open_map,
                  CASE WHEN p.chat_id<0 THEN 'https://t.me/c/'||substring(abs(p.chat_id)::text from 4)||'/'||p.message_id ELSE '' END AS open_telegram
           FROM pins p
           LEFT JOIN LATERAL (
             SELECT x.employee,x.name,x.username,x.region,x.dealer,x.position
             FROM so_roster_v1 x WHERE x.uid=p.telegram_id AND x.active=1 AND upper(trim(x.position)) IN ('SO','SS')
             ORDER BY x.source='excel' DESC,x.id LIMIT 1) r ON true
           LEFT JOIN so_groups_v1 g ON g.id=p.chat_id
           WHERE upper(trim(coalesce(r.position,p.person->>'position','SO'))) IN ('SO','SS')''',
        '''CREATE VIEW gold_fact_live_route_v1 AS
           WITH route_events AS (
             SELECT e.id AS pin_id,e.uid AS telegram_id,e.chat AS chat_id,e.mid AS message_id,e.at AS pin_at,
                    e.data->'geo' AS geo,e.data->'person' AS person,e.data->'route' AS route
             FROM so_events_v1 e
             LEFT JOIN so_groups_v1 gx ON gx.id=e.chat
             WHERE e.kind='pin'
               AND e.at::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')::date
               AND json_array_length(CASE WHEN json_typeof(e.data->'route'->'geometry')='array'
                                           THEN e.data->'route'->'geometry' ELSE '[]'::json END)>1
               AND e.chat<>-1004303041907
               AND lower(trim(coalesce(gx.title,'')))<>'test_so'),
           raw_points AS (
             SELECT e.*,p.coordinate,p.road_point_order,
                    count(*) OVER (PARTITION BY e.pin_id) AS route_point_count
             FROM route_events e
             CROSS JOIN LATERAL json_array_elements(e.route->'geometry') WITH ORDINALITY AS p(coordinate,road_point_order)),
           eligible AS (
             -- Keep the complete journey: dealer -> first check-in, followed
             -- by every check-in-to-check-in road leg.
             SELECT r.*,
                    row_number() OVER (
                      PARTITION BY r.telegram_id,r.pin_at::date
                      ORDER BY r.pin_at,r.message_id,r.road_point_order) AS raw_user_point_order,
                    count(*) OVER (PARTITION BY r.telegram_id,r.pin_at::date) AS raw_user_point_count
             FROM raw_points r),
           sampled AS (
             -- Azure Maps silently drops paths when all full OSRM geometries
             -- exceed its visual point budget.  Keep every real check-in and
             -- both ends of every leg, while sampling the remaining geometry
             -- to roughly 150 road points per employee/day.
             SELECT e.*
             FROM eligible e
             WHERE e.road_point_order IN (1,e.route_point_count)
                OR mod(e.raw_user_point_order-1,
                       greatest(1,ceil(e.raw_user_point_count::numeric/150)::integer))=0),
           ordered AS (
             SELECT s.*,
                    row_number() OVER (
                      PARTITION BY s.telegram_id,s.pin_at::date
                      ORDER BY s.pin_at,s.message_id,s.road_point_order) AS map_point_order
             FROM sampled s)
           SELECT p.pin_id,p.pin_id::text||'-'||p.road_point_order::text AS route_point_key,
                  p.pin_at::date AS date_key,p.pin_at,to_char(p.pin_at,'HH12:MI AM') AS pin_time,
                  p.telegram_id,coalesce(r.employee,p.person->>'employee','') AS employee_id,
                  coalesce(r.name,p.person->>'name','') AS real_name,
                  coalesce(nullif(r.username,''),p.person->>'username','') AS username,
                  coalesce(r.position,p.person->>'position','SO') AS position,
                  coalesce(r.region,p.person->>'region',split_part(g.code,'-',1),'') AS region,
                  coalesce(r.dealer,p.person->>'dealer',substring(g.code from position('-' in g.code)+1),'') AS dealer,
                  concat_ws('-',coalesce(r.region,p.person->>'region',split_part(g.code,'-',1),''),
                                coalesce(r.dealer,p.person->>'dealer',substring(g.code from position('-' in g.code)+1),'')) AS dealer_key,
                  p.chat_id,g.title AS group_name,
                  (p.coordinate->>1)::double precision AS latitude,
                  (p.coordinate->>0)::double precision AS longitude,
                  coalesce(p.geo->>'province','') AS province,coalesce(p.geo->>'district','') AS district,
                  coalesce(p.geo->>'commune','') AS commune,
                  coalesce(p.geo->>'commune_code','') AS location_key,
                  concat_ws(' / ',nullif(p.geo->>'commune',''),nullif(p.geo->>'district',''),nullif(p.geo->>'province','')) AS full_address,
                  p.telegram_id::text||'-'||to_char(p.pin_at,'YYYYMMDD') AS path_id,
                  coalesce(r.employee,p.person->>'employee',p.telegram_id::text) AS user_color_key,
                  coalesce((p.route->>'sequence')::integer,1) AS route_segment,
                  p.road_point_order::integer AS road_point_order,
                  p.map_point_order AS point_order,
                  (p.road_point_order=p.route_point_count) AS is_checkin,
                  CASE WHEN p.road_point_order=p.route_point_count THEN 8 ELSE 0 END AS marker_size,
                  CASE WHEN p.road_point_order=p.route_point_count
                       THEN coalesce((p.route->>'sequence')::integer,1) END AS checkin_number,
                  CASE WHEN p.road_point_order=p.route_point_count
                       THEN 'Check-in #'||coalesce(p.route->>'sequence','1') ELSE '' END AS checkin_label,
                  nullif(p.route->>'distance_km','')::double precision AS road_distance_km,
                  coalesce(p.route->>'kind','') AS route_leg,
                  'https://maps.google.com/maps?q='||(p.coordinate->>1)||','||(p.coordinate->>0) AS open_map,
                  CASE WHEN p.chat_id<0 THEN 'https://t.me/c/'||substring(abs(p.chat_id)::text from 4)||'/'||p.message_id ELSE '' END AS open_telegram
           FROM ordered p
           LEFT JOIN LATERAL (
             SELECT x.employee,x.name,x.username,x.region,x.dealer,x.position
             FROM so_roster_v1 x WHERE x.uid=p.telegram_id AND x.active=1 AND upper(trim(x.position)) IN ('SO','SS')
             ORDER BY x.source='excel' DESC,x.id LIMIT 1) r ON true
           LEFT JOIN so_groups_v1 g ON g.id=p.chat_id
           WHERE upper(trim(coalesce(r.position,p.person->>'position','SO'))) IN ('SO','SS')''',
        '''CREATE VIEW gold_dim_date_v1 AS
           WITH all_dates AS (
             SELECT report_date AS d FROM silver_so_visit_v1
             WHERE chat_id<>-1004303041907 AND lower(trim(group_name))<>'test_so'
             UNION SELECT m.start::date FROM so_missing_v1 m
                   LEFT JOIN so_groups_v1 g ON g.id=m.chat
                   WHERE m.chat<>-1004303041907 AND lower(trim(coalesce(g.title,'')))<>'test_so'
             UNION SELECT l.start::date FROM so_leave_v1 l
                   LEFT JOIN so_groups_v1 g ON g.id=l.chat
                   WHERE l.chat<>-1004303041907 AND lower(trim(coalesce(g.title,'')))<>'test_so'
             UNION SELECT day::date FROM so_plans_v1
                   WHERE day ~ '^\\d{4}-\\d{2}-\\d{2}$'
                     AND batch=(SELECT data->>'batch' FROM so_states_v1 WHERE key='plan_latest')
             UNION SELECT e.at::date FROM so_events_v1 e
                   WHERE e.kind='pin'
                     AND (e.data->>'lat') ~ '^-?[0-9]+([.][0-9]+)?$'
                     AND (e.data->>'lon') ~ '^-?[0-9]+([.][0-9]+)?$'
                     AND (e.data->>'lat')::double precision BETWEEN 9.80 AND 14.75
                     AND (e.data->>'lon')::double precision BETWEEN 102.30 AND 107.70),
           bounds AS (SELECT coalesce(min(d),current_date) AS first_date,coalesce(max(d),current_date) AS last_date FROM all_dates)
           SELECT d::date AS date_key,to_char(d,'YYYYMMDD')::integer AS date_id,
                  extract(year from d)::integer AS year,extract(quarter from d)::integer AS quarter,
                  extract(month from d)::integer AS month_no,to_char(d,'FMMonth') AS month,
                  to_char(d,'Mon') AS month_short,to_char(d,'YYYY-MM') AS year_month,
                  extract(week from d)::integer AS week_no,
                  'Week'||least(5,((extract(day from d)::integer-1)/7)+1)::text AS week,
                  extract(day from d)::integer AS day,
                  trim(to_char(d,'Day')) AS day_name,extract(isodow from d)::integer AS day_of_week,
                  CASE WHEN extract(isodow from d) IN (6,7) THEN true ELSE false END AS is_weekend
           FROM bounds CROSS JOIN LATERAL generate_series(first_date,last_date,interval '1 day') d''',
        '''CREATE VIEW gold_powerbi_relationship_manifest_v1 AS
           SELECT * FROM (VALUES
             ('gold_dim_date_v1','date_key','gold_fact_plan_v1','date_key'),
             ('gold_dim_date_v1','date_key','gold_fact_missing_report_v1','date_miss'),
             ('gold_dim_date_v1','date_key','gold_fact_user_al_v1','date_al'),
             ('gold_dim_date_v1','date_key','gold_fact_wrong_plan_v1','date_wrong_plan'),
             ('gold_dim_date_v1','date_key','gold_fact_road_exception_v1','date_over_10km'),
             ('gold_dim_date_v1','date_key','gold_fact_live_location_v1','date_key'),
             ('gold_dim_date_v1','date_key','gold_fact_live_location_history_v1','date_key'),
             ('gold_dim_date_v1','date_key','gold_fact_live_route_v1','date_key'),
             ('gold_dim_employee_v1','employee_id','gold_fact_plan_v1','employee_id'),
             ('gold_dim_employee_v1','employee_id','gold_fact_missing_report_v1','employee_id'),
             ('gold_dim_employee_v1','employee_id','gold_fact_user_al_v1','employee_id'),
             ('gold_dim_employee_v1','employee_id','gold_fact_wrong_plan_v1','employee_id'),
             ('gold_dim_employee_v1','employee_id','gold_fact_road_exception_v1','employee_id'),
             ('gold_dim_employee_v1','employee_id','gold_fact_live_location_v1','employee_id'),
             ('gold_dim_employee_v1','employee_id','gold_fact_live_location_history_v1','employee_id'),
             ('gold_dim_employee_v1','employee_id','gold_fact_live_route_v1','employee_id'),
             ('gold_dim_dealer_v1','dealer_key','gold_fact_plan_v1','dealer_key'),
             ('gold_dim_dealer_v1','dealer_key','gold_fact_missing_report_v1','dealer_key'),
             ('gold_dim_dealer_v1','dealer_key','gold_fact_user_al_v1','dealer_key'),
             ('gold_dim_dealer_v1','dealer_key','gold_fact_wrong_plan_v1','dealer_key'),
             ('gold_dim_dealer_v1','dealer_key','gold_fact_road_exception_v1','dealer_key'),
             ('gold_dim_dealer_v1','dealer_key','gold_fact_live_location_v1','dealer_key'),
             ('gold_dim_dealer_v1','dealer_key','gold_fact_live_location_history_v1','dealer_key'),
             ('gold_dim_dealer_v1','dealer_key','gold_fact_live_route_v1','dealer_key'),
             ('gold_dim_location_v1','location_key','gold_fact_plan_v1','location_key'),
             ('gold_dim_location_v1','location_key','gold_fact_wrong_plan_v1','location_key'),
             ('gold_dim_location_v1','location_key','gold_fact_road_exception_v1','location_key'),
             ('gold_dim_location_v1','location_key','gold_fact_live_location_v1','location_key'),
             ('gold_dim_location_v1','location_key','gold_fact_live_location_history_v1','location_key'),
             ('gold_dim_location_v1','location_key','gold_fact_live_route_v1','location_key')
           ) AS r(one_table,one_column,many_table,many_column)'''
    ]

def install_gold_views(connection=None):
    """Install a relationship-ready Power BI star schema on PostgreSQL."""
    if connection is not None:
        if connection.dialect.name!='postgresql':return
        for sql in gold_view_statements():connection.execute(text(sql))
        return
    if engine is None or engine.dialect.name!='postgresql':return
    with engine.begin() as conn:
        conn.execute(text('SELECT pg_advisory_xact_lock(835501520)'))
        for sql in gold_view_statements():conn.execute(text(sql))

def acquire_lock():
    global lock_connection
    from sqlalchemy import text
    if engine.dialect.name != 'postgresql': return
    lock_connection=engine.connect().execution_options(isolation_level="AUTOCOMMIT")
    if not lock_connection.execute(text('SELECT pg_try_advisory_lock(835501519)')).scalar():
        lock_connection.close(); raise RuntimeError('Another SO bot is already running against this database. Stop it first.')

def release_lock():
    global lock_connection
    if lock_connection is not None:
        lock_connection.close();lock_connection=None

@contextmanager
def session():
    s=Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback();raise
    finally:s.close()

def state(s,key,default=None):
    row=s.get(State,key)
    return row.data if row else (default if default is not None else {})
def put(s,key,data):
    row=s.get(State,key)
    if row:row.data=data
    else:s.add(State(key=key,data=data))
