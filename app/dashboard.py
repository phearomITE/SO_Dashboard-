"""Local production dashboard for live sales check-ins and journeys."""
from contextlib import asynccontextmanager
from csv import reader
from io import BytesIO
from sqlalchemy import select
from app.imports import read_plan
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
import gc
import logging
import json
import mimetypes
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from app import db
from app.config import TOKEN
from app.diagnostics import database_message
import app.geo as geo_module
from app.geo import locate
from app.admin_api import router as admin_router

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "app" / "static" / "dashboard.html"
REACT_HTML = ROOT / "app" / "static" / "admin" / "index.html"
DEALERS = ROOT / "data" / "DEALERS_STATION_All_Location.csv"
CANONICAL_REGIONS = {
    region: dealers.split(",") for region, dealers in {
        "R1": "CA1,CA2,CA3,CA4,CA5,CA6,CA7,CA8,CA9,PTM6",
        "R2": "BTI6,CPH2,KDL1,KHT3,KRV5,KSV5,KTB1,PKB4,PMR5,SVA7,SVR4,TAK2",
        "R3": "CUK7,KKG3,KPS7,KPT2,KSM1,KSP3,KTR4,PNP6,SRA5,TPG2",
        "R4": "BTB1,BVL6,KCG4,KTL6,MRS5,PLN4,PPT3,PUS5,VVG1",
        "R5": "BMC2,CHK6,KRG7,KTH2,KTM8,PPR8,PUK7,SRP1",
        "R6": "BTY8,KCM1,MMT3,STT4,TKM2",
        "R7": "BKE7,KRT1,KSA5,MDK2,RTK4,SNL6,STR3",
        "R8": "AVG4,CKS1,PVH3,SMR5",
    }.items()
}
DEALER_REGION = {dealer: region for region, dealers in CANONICAL_REGIONS.items() for dealer in dealers}


@lru_cache(maxsize=4)
def _plan_outlet_targets(batch):
    """Read the actual master worksheet, including unmarked commune rows.

    Cached per immutable PlanFile batch. Do not infer targets from scheduled
    plan events; such events omit communes without a mark in the calendar.
    """
    if not batch:return []
    with db.Session() as session:
        workbook=session.scalar(select(db.PlanFile.content).where(db.PlanFile.batch==batch))
    if not workbook:return []
    _,_,grid_rows,_=read_plan(BytesIO(workbook),include_dates=True,include_grid=True)
    # One target for each employee/commune, not one for each repeated week.
    communes={}
    for row in grid_rows:
        vals=row['values'];employee=str(vals.get('employee id','')).strip()
        if not employee:continue
        region=str(vals.get('region',''));dealer=str(vals.get('dealer',''))
        commune_code=str(vals.get('com.code','')).strip()
        commune_key=commune_code or (str(vals.get('district','')).casefold(),str(vals.get('commune','')).casefold())
        key=(employee,region,dealer,commune_key)
        def parse_count(column):
            try:return max(0,int(float(str(vals.get(column,0) or 0).replace(',',''))))
            except (TypeError,ValueError):return 0
        ws_ds=parse_count('#ws/ds');horeca=parse_count('#horeca outlet')
        if key not in communes:communes[key]=[ws_ds,horeca]
        else:
            # Repeated calendar sheets may show the same assigned outlet pool.
            communes[key][0]=max(communes[key][0],ws_ds)
            communes[key][1]=max(communes[key][1],horeca)
    result={}
    for (employee,region,dealer,_), (ws_ds,horeca) in communes.items():
        target=result.setdefault(employee,{
            'employee_id':employee,'region':region,'dealer':dealer,
            'ws_ds':0,'horeca':0,'communes':0,'total_outlets':0})
        target['communes']+=1
        target['ws_ds']+=ws_ds
        target['horeca']+=horeca
        target['total_outlets']=target['ws_ds']+target['horeca']
    return list(result.values())


def _clean(value):
    return str(value or "").strip()


@lru_cache(maxsize=8)
def _load_dealers_cached(path,modified_ns):
    """Read the four-line-per-dealer CSV export and return normalized stations."""
    rows = list(reader(Path(path).open(encoding="utf-8-sig", newline="")))
    output = []
    for index, row in enumerate(rows):
        if index == 0 or len(row) < 2 or not _clean(row[0]).upper().startswith("POINT"):
            continue
        dealer = _clean(row[1]).upper()
        try:
            point = row[0].replace("POINT", "").replace("(", "").replace(")", "").split()
            longitude, latitude = float(point[0]), float(point[1])
        except (ValueError, IndexError):
            continue
        region = ""
        for follow in rows[index + 1:index + 4]:
            for cell in follow:
                value = _clean(cell)
                if value.lower().startswith("region:"):
                    region = value.split(":", 1)[1].strip().upper()
        if dealer and 9.8 <= latitude <= 14.75 and 102.3 <= longitude <= 107.7:
            region = DEALER_REGION.get(dealer, region)
            geo = locate(latitude, longitude)
            output.append({"dealer": dealer, "region": region, "dealer_key": f"{region}-{dealer}".strip("-"),
                           "latitude": latitude, "longitude": longitude,
                           "province": geo.get("province", ""), "district": geo.get("district", ""),
                           "commune": geo.get("commune", "")})
    unique = {}
    for row in output:
        if row["dealer"] in DEALER_REGION:
            unique.setdefault(row["dealer"], row)
    return sorted(unique.values(), key=lambda x: (x["region"], x["dealer"]))


def load_dealers(path=DEALERS):
    """Reload automatically after a global-admin dealer-master upload."""
    source=Path(path)
    return _load_dealers_cached(source,source.stat().st_mtime_ns) if source.exists() else []


@asynccontextmanager
async def lifespan(_app):
    for attempt in range(1, 4):
        try:
            db.connect()
            break
        except Exception as exc:
            logging.getLogger(__name__).error("%s (attempt %s/3)", database_message(exc, db.database_url()), attempt)
            if attempt == 3:
                raise
            time.sleep(2)
    with db.engine.begin() as connection:
        connection.execute(text("""CREATE TABLE IF NOT EXISTS dashboard_audit_v1 (
            id BIGSERIAL PRIMARY KEY,
            actor VARCHAR(100) NOT NULL,
            action VARCHAR(40) NOT NULL,
            entity VARCHAR(80) NOT NULL,
            entity_id VARCHAR(255) NOT NULL,
            before_data JSONB,
            after_data JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )"""))
    db.ReportImage.__table__.create(db.engine, checkfirst=True)
    from app.auth_store import AuthStore, PgAuthCache
    _app.state.auth_store = AuthStore(db.engine)
    _app.state.auth_store.initialize()
    _app.state.auth_kv = None
    _app.state.redis = None
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        try:
            import redis
            client = redis.Redis.from_url(redis_url, decode_responses=True, socket_connect_timeout=2,
                                          socket_timeout=2, health_check_interval=30)
            client.ping()
            _app.state.redis = client
            logging.getLogger(__name__).info("Dashboard Redis cache/rate limiter ready")
        except Exception:
            logging.getLogger(__name__).warning("Redis unavailable; using PostgreSQL auth store without Redis cache")
    if _app.state.redis is None:
        _app.state.auth_kv = PgAuthCache(db.engine)
        logging.getLogger(__name__).info("Dashboard authentication store using PostgreSQL")
    try:
        station_count = len(load_dealers())
        logging.getLogger(__name__).info("Dashboard dealer map cache ready (%s stations)", station_count)
    except Exception:
        logging.getLogger(__name__).exception("Dashboard dealer map cache warm-up failed")
    finally:
        if os.getenv("DATABASE_URL", "").strip() or os.getenv("RENDER"):
            geo_module._cache = None
            gc.collect()
    yield
    if getattr(_app.state, "redis", None) is not None:
        _app.state.redis.close()
    if db.engine is not None:
        db.engine.dispose()


app = FastAPI(title="SO Alert Live Check-In Dashboard", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs", redoc_url=None)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
from app.email_auth import router as email_auth_router, DashboardAuthMiddleware
app.add_middleware(DashboardAuthMiddleware)
app.include_router(email_auth_router)
app.include_router(admin_router)
from app.gp_reports import router as gp_router
app.include_router(gp_router)
app.mount("/assets", StaticFiles(directory=ROOT / "app" / "static" / "admin" / "assets", check_dir=False), name="admin-assets")
app.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")


@app.get("/media/reports/{filename}")
def report_image(filename: str):
    safe_name = Path(filename).name
    if not safe_name or safe_name != filename or Path(safe_name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
        raise HTTPException(404, "Image not found")
    disk_path = ROOT / "data" / "report_images" / safe_name
    if disk_path.is_file():
        return FileResponse(disk_path, headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})
    stored = db.load_report_image(db.engine, safe_name)
    if stored:
        content, media_type = stored
        return Response(content=content, media_type=media_type,
                        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})
    raise HTTPException(404, "Image not found")


@app.get("/", response_class=HTMLResponse)
def dashboard_page():
    return HTMLResponse(
        (REACT_HTML if REACT_HTML.exists() else HTML).read_text(encoding="utf-8"),
        headers={"Cache-Control":"no-store, no-cache, must-revalidate, max-age=0",
                 "Pragma":"no-cache", "Expires":"0"},
    )


@app.get("/health")
def health():
    try:
        with db.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ready", "database": True}
    except Exception as exc:
        raise HTTPException(503, "Database is not ready") from exc


@lru_cache(maxsize=512)
def _telegram_avatar(token: str, telegram_id: int):
    """Fetch and cache a Telegram profile image without exposing the bot token."""
    if not token:
        return None
    try:
        headers = {"User-Agent": "SO-Alert-Dashboard/1.0"}
        photos_url = "https://api.telegram.org/bot" + token + "/getUserProfilePhotos?" + urlencode({"user_id": telegram_id, "limit": 1})
        with urlopen(Request(photos_url, headers=headers), timeout=4) as response:
            photos_payload = json.loads(response.read(1_000_000).decode("utf-8"))
        photos = photos_payload.get("result", {}).get("photos", []) if photos_payload.get("ok") else []
        if not photos or not photos[0]:
            return None
        photo = max(photos[0], key=lambda item: int(item.get("width", 0)) * int(item.get("height", 0)))
        file_id = photo.get("file_id")
        if not file_id:
            return None
        file_url = "https://api.telegram.org/bot" + token + "/getFile?" + urlencode({"file_id": file_id})
        with urlopen(Request(file_url, headers=headers), timeout=4) as response:
            file_payload = json.loads(response.read(1_000_000).decode("utf-8"))
        file_path = str(file_payload.get("result", {}).get("file_path", "")) if file_payload.get("ok") else ""
        if not file_path or file_path.startswith("/") or ".." in file_path.split("/"):
            return None
        image_url = "https://api.telegram.org/file/bot" + token + "/" + file_path
        with urlopen(Request(image_url, headers=headers), timeout=5) as response:
            image = response.read(5 * 1024 * 1024 + 1)
        if not image or len(image) > 5 * 1024 * 1024:
            return None
        media_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
        return (image, media_type if media_type.startswith("image/") else "image/jpeg")
    except Exception:
        return None


@app.get("/api/v1/dashboard/telegram-avatar/{telegram_id}")
def dashboard_telegram_avatar(telegram_id: int):
    if telegram_id <= 0:
        return Response(status_code=404)
    exists = _query_rows("""SELECT 1 FROM so_roster_v1
        WHERE uid=:uid AND active=1 AND upper(trim(coalesce(position,'SO'))) IN ('SO','SS') LIMIT 1""", {"uid": telegram_id})
    if not exists:
        return Response(status_code=404)
    image = _telegram_avatar(TOKEN, telegram_id)
    if not image:
        return Response(status_code=404, headers={"Cache-Control": "public, max-age=900"})
    return Response(content=image[0], media_type=image[1], headers={
        "Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"
    })


def _query_rows(sql, params=None):
    with db.engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(sql), params or {}).mappings()]


def _redis_json_response(client, key):
    if client is None:
        return None
    try:
        cached = client.get(key)
        if cached:
            return Response(content=cached, media_type="application/json",
                            headers={"Cache-Control":"no-store", "X-Dashboard-Cache":"HIT"})
    except Exception:
        logging.getLogger(__name__).warning("Dashboard Redis cache read failed", exc_info=True)
    return None


def _redis_cache_json(client, key, payload, ttl):
    if client is None:
        return None
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        client.setex(key, max(1, int(ttl)), body)
        return Response(content=body, media_type="application/json",
                        headers={"Cache-Control":"no-store", "X-Dashboard-Cache":"MISS"})
    except Exception:
        logging.getLogger(__name__).warning("Dashboard Redis cache write failed", exc_info=True)
        return None


def historical_checkins(selected):
    return _query_rows("""
        WITH pins AS (
          SELECT e.id AS pin_id,e.uid AS telegram_id,e.chat AS chat_id,e.mid AS message_id,e.at AS pin_at,
                 (e.data->>'lat')::double precision AS latitude,(e.data->>'lon')::double precision AS longitude,
                 e.data->'geo' AS geo,e.data->'person' AS person,e.data->'route' AS route,
                 coalesce(e.data->>'plan_check','') AS plan_check,
                 coalesce(e.data->>'plan_issue','') AS plan_reason,
                 coalesce(e.data->>'pin_source','telegram_location') AS pin_source
          FROM so_events_v1 e LEFT JOIN so_groups_v1 gx ON gx.id=e.chat
          WHERE e.kind='pin' AND e.at>=:start AND e.at<:end
            AND (e.data->>'lat') ~ '^-?[0-9]+([.][0-9]+)?$'
            AND (e.data->>'lon') ~ '^-?[0-9]+([.][0-9]+)?$'
            AND (e.data->>'lat')::double precision BETWEEN 9.80 AND 14.75
            AND (e.data->>'lon')::double precision BETWEEN 102.30 AND 107.70
            AND e.chat<>-1004303041907 AND lower(trim(coalesce(gx.title,'')))<>'test_so')
        SELECT p.pin_id,p.pin_id::text AS point_key,p.pin_at::date AS date_key,p.pin_at,
               to_char(p.pin_at,'DD/MM/YYYY') AS pin_date,to_char(p.pin_at,'HH12:MI AM') AS pin_time,
               p.telegram_id,coalesce(r.employee,p.person->>'employee','') AS employee_id,
               coalesce(r.name,p.person->>'name','') AS real_name,
               coalesce(nullif(r.username,''),p.person->>'username','') AS username,
               coalesce(r.position,p.person->>'position','SO') AS position,
               coalesce(r.region,p.person->>'region',split_part(g.code,'-',1),'') AS region,
               coalesce(r.dealer,p.person->>'dealer',substring(g.code from position('-' in g.code)+1),'') AS dealer,
               concat_ws('-',coalesce(r.region,p.person->>'region',split_part(g.code,'-',1),''),coalesce(r.dealer,p.person->>'dealer',substring(g.code from position('-' in g.code)+1),'')) AS dealer_key,
               p.chat_id,g.title AS group_name,p.latitude,p.longitude,
               coalesce(p.geo->>'province','') AS province,coalesce(p.geo->>'district','') AS district,
               coalesce(p.geo->>'commune','') AS commune,coalesce(p.geo->>'commune_code','') AS commune_code,
               coalesce(p.geo->>'commune_code','') AS location_key,
               coalesce(p.plan_check,'') AS plan_check,coalesce(p.plan_reason,'') AS plan_reason,
               nullif(p.route->>'distance_km','')::double precision AS road_distance_km,
               coalesce(p.route->>'kind','') AS route_leg,
               concat_ws(' / ',nullif(p.geo->>'commune',''),nullif(p.geo->>'district',''),nullif(p.geo->>'province','')) AS full_address,
               p.pin_source,p.telegram_id::text||'-'||to_char(p.pin_at,'YYYYMMDD') AS path_id,
               row_number() OVER (PARTITION BY p.telegram_id,p.pin_at::date ORDER BY p.pin_at,p.message_id) AS point_order,
               CASE WHEN p.pin_at::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')::date
                          AND p.pin_at>=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')-interval '30 minutes' THEN 'Active'
                    WHEN p.pin_at::date=(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Phnom_Penh')::date THEN 'Inactive'
                    ELSE 'Historical' END AS activity_status,
               'https://maps.google.com/maps?q='||p.latitude||','||p.longitude AS open_map,
               CASE WHEN nullif(coalesce(r.username,p.person->>'username',''),'') IS NOT NULL
                    THEN 'https://t.me/'||replace(coalesce(r.username,p.person->>'username'),'@','')
                    ELSE 'tg://user?id='||p.telegram_id::text END AS telegram_user,
               CASE WHEN photo.mid IS NOT NULL AND p.chat_id<0
                    THEN 'https://t.me/c/'||substring(abs(p.chat_id)::text from 4)||'/'||photo.mid ELSE '' END AS open_image,
               CASE WHEN photo.mid IS NOT NULL THEN '/media/reports/'||p.chat_id::text||'_'||photo.mid||'.jpg'
                    ELSE '' END AS image_preview,
               CASE WHEN p.chat_id<0 THEN 'https://t.me/c/'||substring(abs(p.chat_id)::text from 4)||'/'||p.message_id ELSE '' END AS open_telegram
        FROM pins p
        LEFT JOIN LATERAL (
          SELECT x.employee,x.name,x.username,x.region,x.dealer,x.position FROM so_roster_v1 x
          WHERE x.uid=p.telegram_id AND x.active=1 AND upper(trim(x.position)) IN ('SO','SS')
          ORDER BY x.source='excel' DESC,x.id LIMIT 1) r ON true
        LEFT JOIN LATERAL (
          SELECT pe.mid FROM so_events_v1 pe
          WHERE pe.chat=p.chat_id AND pe.uid=p.telegram_id AND pe.kind='photo'
            AND pe.at BETWEEN p.pin_at-interval '30 minutes' AND p.pin_at+interval '30 minutes'
          ORDER BY abs(extract(epoch from (pe.at-p.pin_at))),pe.mid LIMIT 1) photo ON true
        LEFT JOIN so_groups_v1 g ON g.id=p.chat_id
        WHERE upper(trim(coalesce(r.position,p.person->>'position','SO'))) IN ('SO','SS')
        ORDER BY p.pin_at,p.telegram_id,p.message_id
    """,{"start":datetime.combine(selected,datetime.min.time()),"end":datetime.combine(selected+timedelta(days=1),datetime.min.time())})


def warehouse_checkins(selected):
    """Return persisted historical visits after the raw Telegram retention window.

    Silver is the durable BI grain (one logical visit/check-in). Raw pin events are
    still preferred for the current day because they arrive before the warehouse
    refresh finishes.
    """
    return _query_rows("""
        SELECT v.source_event_id AS pin_id,v.visit_id::text AS point_key,v.report_date AS date_key,
               v.submitted_at AS pin_at,to_char(v.report_date,'DD/MM/YYYY') AS pin_date,
               coalesce(nullif(v.report_time,''),to_char(v.submitted_at,'HH12:MI AM')) AS pin_time,
               v.telegram_id,v.employee_id,v.real_name,v.username,v.position,v.region,v.dealer,
               concat_ws('-',nullif(v.region,''),nullif(v.dealer,'')) AS dealer_key,
               v.chat_id,v.group_name,v.latitude,v.longitude,
               coalesce(nullif(v.actual_province,''),v.actual_province_kh,'') AS province,
               coalesce(nullif(v.actual_district,''),v.actual_district_kh,'') AS district,
               coalesce(nullif(v.actual_commune,''),v.actual_commune_kh,'') AS commune,
               coalesce(v.actual_commune_code,'') AS commune_code,
               coalesce(v.actual_commune_code,'') AS location_key,
               concat_ws(' / ',nullif(coalesce(v.actual_commune,v.actual_commune_kh),''),
                                     nullif(coalesce(v.actual_district,v.actual_district_kh),''),
                                     nullif(coalesce(v.actual_province,v.actual_province_kh),'')) AS full_address,
               CASE WHEN nullif(v.open_image,'') IS NOT NULL THEN 'explicit_coordinates'
                    ELSE 'telegram_location' END AS pin_source,
               v.telegram_id::text||'-'||to_char(v.report_date,'YYYYMMDD') AS path_id,
               row_number() OVER (PARTITION BY v.telegram_id,v.report_date
                                  ORDER BY v.submitted_at,v.message_id,v.visit_id) AS point_order,
               v.road_distance_km,v.route_leg,v.plan_check,v.plan_reason,v.open_image,
               CASE WHEN nullif(v.open_image,'') IS NOT NULL
                    THEN '/media/reports/'||v.chat_id::text||'_'||regexp_replace(v.open_image,'.*/','','g')||'.jpg'
                    ELSE '' END AS image_preview,
               'Historical' AS activity_status,
               coalesce(nullif(v.open_map,''),'https://maps.google.com/maps?q='||v.latitude||','||v.longitude) AS open_map,
               CASE WHEN nullif(v.username,'') IS NOT NULL THEN 'https://t.me/'||replace(v.username,'@','')
                    ELSE 'tg://user?id='||v.telegram_id::text END AS telegram_user,
               coalesce(v.open_telegram,'') AS open_telegram
        FROM silver_so_visit_v1 v
        WHERE v.report_date=:selected AND v.latitude BETWEEN 9.80 AND 14.75
          AND v.longitude BETWEEN 102.30 AND 107.70
          AND v.chat_id<>-1004303041907 AND lower(trim(coalesce(v.group_name,'')))<>'test_so'
          AND upper(trim(coalesce(v.position,'SO'))) IN ('SO','SS')
        ORDER BY v.submitted_at,v.telegram_id,v.message_id,v.visit_id
    """,{"selected":selected})


def available_dates():
    return _query_rows("""
        WITH dates AS (
          SELECT e.at::date AS date_key,count(*) AS raw_pins,0::bigint AS warehouse_visits
          FROM so_events_v1 e LEFT JOIN so_groups_v1 g ON g.id=e.chat
          WHERE e.kind='pin' AND e.chat<>-1004303041907
            AND lower(trim(coalesce(g.title,'')))<>'test_so'
          GROUP BY e.at::date
          UNION ALL
          SELECT v.report_date,0::bigint,count(*)
          FROM silver_so_visit_v1 v
          WHERE v.chat_id<>-1004303041907 AND lower(trim(coalesce(v.group_name,'')))<>'test_so'
            AND v.latitude BETWEEN 9.80 AND 14.75 AND v.longitude BETWEEN 102.30 AND 107.70
            AND upper(trim(coalesce(v.position,'SO'))) IN ('SO','SS')
          GROUP BY v.report_date)
        SELECT date_key,sum(raw_pins)::bigint AS raw_pins,
               sum(warehouse_visits)::bigint AS warehouse_visits,
               greatest(sum(raw_pins),sum(warehouse_visits))::bigint AS available_rows
        FROM dates GROUP BY date_key ORDER BY date_key DESC
    """)


def historical_routes(selected, telegram_id):
    return _query_rows("""
        WITH route_events AS (
          SELECT e.id AS pin_id,e.uid AS telegram_id,e.chat AS chat_id,e.mid AS message_id,e.at AS pin_at,
                 e.data->'geo' AS geo,e.data->'person' AS person,e.data->'route' AS route
          FROM so_events_v1 e LEFT JOIN so_groups_v1 gx ON gx.id=e.chat
          WHERE e.kind='pin' AND e.at>=:start AND e.at<:end AND e.uid=:telegram_id
            AND json_array_length(CASE WHEN json_typeof(e.data->'route'->'geometry')='array' THEN e.data->'route'->'geometry' ELSE '[]'::json END)>1
            AND e.chat<>-1004303041907 AND lower(trim(coalesce(gx.title,'')))<>'test_so'),
        raw_points AS (
          SELECT e.*,p.coordinate,p.road_point_order,count(*) OVER (PARTITION BY e.pin_id) AS route_point_count
          FROM route_events e CROSS JOIN LATERAL json_array_elements(e.route->'geometry') WITH ORDINALITY AS p(coordinate,road_point_order)),
        ranked AS (
          SELECT r.*,row_number() OVER (PARTITION BY r.telegram_id,r.pin_at::date ORDER BY r.pin_at,r.message_id,r.road_point_order) AS raw_user_point_order,
                 count(*) OVER (PARTITION BY r.telegram_id,r.pin_at::date) AS raw_user_point_count FROM raw_points r),
        sampled AS (
          SELECT * FROM ranked WHERE road_point_order IN (1,route_point_count)
             OR mod(raw_user_point_order-1,greatest(1,ceil(raw_user_point_count::numeric/60)::integer))=0),
        ordered AS (
          SELECT s.*,row_number() OVER (PARTITION BY s.telegram_id,s.pin_at::date ORDER BY s.pin_at,s.message_id,s.road_point_order) AS map_point_order FROM sampled s)
        SELECT p.pin_id,p.pin_at::date AS date_key,p.pin_at,to_char(p.pin_at,'HH12:MI AM') AS pin_time,p.telegram_id,
               coalesce(r.employee,p.person->>'employee','') AS employee_id,coalesce(r.name,p.person->>'name','') AS real_name,
               coalesce(nullif(r.username,''),p.person->>'username','') AS username,
               coalesce(r.region,p.person->>'region',split_part(g.code,'-',1),'') AS region,
               coalesce(r.dealer,p.person->>'dealer',substring(g.code from position('-' in g.code)+1),'') AS dealer,
               (p.coordinate->>1)::double precision AS latitude,(p.coordinate->>0)::double precision AS longitude,
               p.telegram_id::text||'-'||to_char(p.pin_at,'YYYYMMDD') AS path_id,
               coalesce(r.employee,p.person->>'employee',p.telegram_id::text) AS user_color_key,
               coalesce((p.route->>'sequence')::integer,1) AS route_segment,p.road_point_order::integer AS road_point_order,
               p.map_point_order AS point_order,(p.road_point_order=p.route_point_count) AS is_checkin,
               nullif(p.route->>'distance_km','')::double precision AS road_distance_km,
               coalesce(p.route->>'kind','') AS route_leg
        FROM ordered p
        LEFT JOIN LATERAL (
          SELECT x.employee,x.name,x.username,x.region,x.dealer FROM so_roster_v1 x
          WHERE x.uid=p.telegram_id AND x.active=1 AND upper(trim(x.position)) IN ('SO','SS')
          ORDER BY x.source='excel' DESC,x.id LIMIT 1) r ON true
        LEFT JOIN so_groups_v1 g ON g.id=p.chat_id
        ORDER BY path_id,point_order
    """,{"start":datetime.combine(selected,datetime.min.time()),"end":datetime.combine(selected+timedelta(days=1),datetime.min.time()),"telegram_id":telegram_id})


@app.get("/api/v1/dashboard/snapshot")
def snapshot(day: date | None = Query(default=None)):
    # Use Cambodia local time and Redis before any database reads. Live snapshots
    # have a short TTL; historical snapshots are stable and can be reused longer.
    current = datetime.now(ZoneInfo("Asia/Phnom_Penh")).date()
    selected = day or current
    redis_client = getattr(app.state, "redis", None)
    cache_key = f"so-alert:dashboard:snapshot:v3:{selected.isoformat()}"
    cached = _redis_json_response(redis_client, cache_key)
    if cached is not None:
        return cached
    if selected == current:
        checkins=historical_checkins(selected)
        data_source="raw_live_events"
        if not checkins:
            checkins=warehouse_checkins(selected)
            data_source="silver_warehouse"
    else:
        checkins=warehouse_checkins(selected)
        data_source="silver_warehouse"
        if not checkins:
            checkins=historical_checkins(selected)
            data_source="raw_event_fallback"
    routes=[]  # Loaded on demand for one selected employee.
    totals = _query_rows("""
        SELECT count(DISTINCT telegram_id) FILTER (WHERE active=1) AS employees_total
        FROM gold_dim_employee_v1
    """)[0]
    ops = _query_rows("""SELECT
        (SELECT count(DISTINCT employee_id) FROM silver_so_visit_v1
         WHERE report_date=:selected AND upper(trim(coalesce(position,'SO'))) IN ('SO','SS')) AS active_sale,
        (SELECT count(*) FROM silver_so_visit_v1
         WHERE report_date=:selected AND upper(trim(coalesce(position,'SO'))) IN ('SO','SS')) AS checkin_count,
        (SELECT count(DISTINCT uid) FROM so_missing_v1
         WHERE start::date=:selected AND alert_mid IS NOT NULL) AS missing_count,
        (SELECT count(*) FROM silver_so_visit_v1
         WHERE report_date=:selected AND coalesce(road_distance_km,0)>10
           AND upper(trim(coalesce(position,'SO'))) IN ('SO','SS')) AS over_10km_count,
        (SELECT count(DISTINCT r.employee) FROM so_roster_v1 r
         WHERE r.active=1
           AND EXISTS(SELECT 1 FROM so_leave_v1 l
             WHERE l.employee=r.employee AND l.uid=r.uid
               AND l.start<(:selected + interval '1 day') AND l."end">:selected)) AS al_count
    """, {"selected": selected})[0]
    leaves = _query_rows("""SELECT r.employee AS employee_id,r.uid AS telegram_id,r.name AS real_name,
               r.region,r.dealer,r.position,l.source,l.start AS leave_start,l."end" AS leave_end
        FROM so_roster_v1 r JOIN so_leave_v1 l ON l.employee=r.employee AND l.uid=r.uid
        WHERE r.active=1
          AND l.start<(:selected + interval '1 day') AND l."end">:selected
        ORDER BY r.region,r.dealer,r.name,r.employee,l.recorded_at DESC,l.id DESC
    """, {"selected": selected})
    missing_rows = _query_rows("""SELECT DISTINCT ON (m.uid)
               m.uid AS telegram_id,
               coalesce(r.employee,nullif(m.person->>'employee',''),'') AS employee_id,
               coalesce(nullif(m.person->>'name',''),r.name,'') AS real_name,
               coalesce(nullif(m.person->>'region',''),r.region,split_part(g.code,'-',1),'') AS region,
               coalesce(nullif(m.person->>'dealer',''),r.dealer,substring(g.code from position('-' in g.code)+1),'') AS dealer,
               coalesce(nullif(m.person->>'position',''),r.position,'SO') AS position,
               m.start AS missing_start, m."end" AS missing_end,
               m.reason AS summary_reason, m.reason_at AS reason_at
        FROM so_missing_v1 m
        LEFT JOIN LATERAL (
          SELECT x.employee,x.name,x.region,x.dealer,x.position FROM so_roster_v1 x
          WHERE x.uid=m.uid AND x.active=1 AND upper(trim(coalesce(x.position,'SO'))) IN ('SO','SS')
          ORDER BY x.source='excel' DESC,x.id LIMIT 1) r ON true
        LEFT JOIN so_groups_v1 g ON g.id=m.chat
        WHERE m.start>=:start AND m.start<:end AND m.alert_mid IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM so_leave_v1 l
            WHERE l.uid=m.uid AND l.start<m."end" AND l."end">m.start)
          AND m.chat<>-1004303041907 AND lower(trim(coalesce(g.title,'')))<>'test_so'
          AND upper(trim(coalesce(nullif(m.person->>'position',''),r.position,'SO'))) IN ('SO','SS')
        ORDER BY m.uid,m.start DESC,m.id DESC
    """, {"start":datetime.combine(selected,datetime.min.time()),"end":datetime.combine(selected+timedelta(days=1),datetime.min.time())})
    for row in missing_rows:
        for key in ("missing_start","missing_end","reason_at"):
            if row.get(key) is not None:row[key]=row[key].isoformat()
    plan_version = _query_rows("SELECT data ->> CAST(:day AS text) AS batch FROM so_states_v1 WHERE key='plan_dates'", {"day": selected.isoformat()})
    active_plan_batch = plan_version[0].get("batch") if plan_version else None
    plan_communes = _query_rows("""SELECT p.employee AS employee_id,
               coalesce(p.data->>'sale_name','') AS real_name,
               coalesce(p.data->>'region','') AS region,
               coalesce(p.data->>'dealer','') AS dealer,
               coalesce(p.data->>'district','') AS planned_district,
               coalesce(p.data->>'commune','') AS planned_commune,
               coalesce(p.data->>'commune_code','') AS planned_commune_code
        FROM so_plans_v1 p
        WHERE p.batch=:batch AND p.day=:day
        ORDER BY p.employee,p.data->>'commune'
    """, {"batch": active_plan_batch, "day": selected.isoformat()}) if active_plan_batch else []
    # Original workbook is the source of truth, including unscheduled rows.
    plan_targets=_plan_outlet_targets(active_plan_batch)
    # Business coverage targets are configurable because the administrative
    # master can temporarily contain staging/duplicate rows during uploads.
    coverage_master = {
        "provinces": int(os.getenv("DASHBOARD_MASTER_PROVINCES", "25")),
        "districts": int(os.getenv("DASHBOARD_MASTER_DISTRICTS", "210")),
        "communes": int(os.getenv("DASHBOARD_MASTER_COMMUNES", "1651")),
    }
    last_refresh = max((row["pin_at"] for row in checkins if row.get("pin_at")), default=None)
    warehouse=_query_rows("""
        SELECT id AS run_id,status,completed_at,rejected,metrics
        FROM etl_run_v1 ORDER BY id DESC LIMIT 1
    """)
    warehouse=warehouse[0] if warehouse else {}
    for row in checkins:
        for key in ("pin_at", "date_key"):
            if row.get(key) is not None:
                row[key] = row[key].isoformat()
    for row in routes:
        for key in ("pin_at", "date_key"):
            if row.get(key) is not None:
                row[key] = row[key].isoformat()
    for row in leaves:
        for key in ("leave_start", "leave_end"):
            if row.get(key) is not None:
                row[key] = row[key].isoformat()
    if warehouse.get("completed_at") is not None:warehouse["completed_at"]=warehouse["completed_at"].isoformat()
    payload = {"date": selected.isoformat(), "current_business_date": current.isoformat(), "is_live": selected==current,
            "data_source":data_source,
            "checkins": checkins, "routes": routes, "leaves": leaves, "missing_rows": missing_rows, "plan_communes": plan_communes, "plan_targets": plan_targets, "dealers": load_dealers(),
            "employees_total": int(totals.get("employees_total") or 0),
            "active_sale": int(ops.get("active_sale") or 0),
            "checkin_count": int(ops.get("checkin_count") or 0),
            "missing_count": int(ops.get("missing_count") or 0),
            "over_10km_count": int(ops.get("over_10km_count") or 0),
            "al_count": int(ops.get("al_count") or 0),
            "coverage_master": coverage_master,
            "last_refresh": last_refresh.isoformat() if last_refresh else None,"warehouse":warehouse,
            "refresh_seconds": max(60, int(os.getenv("DASHBOARD_REFRESH_SECONDS", "300")))}
    ttl_env = "DASHBOARD_LIVE_SNAPSHOT_TTL_SECONDS" if selected==current else "DASHBOARD_HISTORY_SNAPSHOT_TTL_SECONDS"
    default_ttl = "5" if selected==current else "60"
    cached_response = _redis_cache_json(redis_client, cache_key, payload, os.getenv(ttl_env, default_ttl))
    return cached_response if cached_response is not None else payload


@app.get("/api/v1/dashboard/routes")
def dashboard_employee_routes(day: date = Query(...), telegram_id: int = Query(..., gt=0)):
    current = datetime.now(ZoneInfo("Asia/Phnom_Penh")).date()
    redis_client = getattr(app.state, "redis", None)
    cache_key = f"so-alert:dashboard:route:v1:{day.isoformat()}:{telegram_id}"
    cached = _redis_json_response(redis_client, cache_key)
    if cached is not None:
        return cached
    routes = historical_routes(day, telegram_id)
    for row in routes:
        for key in ("pin_at", "date_key"):
            if row.get(key) is not None:
                row[key] = row[key].isoformat()
    payload = {"date": day.isoformat(), "telegram_id": telegram_id, "routes": routes}
    ttl = os.getenv("DASHBOARD_LIVE_SNAPSHOT_TTL_SECONDS", "5") if day == current else os.getenv("DASHBOARD_HISTORY_SNAPSHOT_TTL_SECONDS", "60")
    cached_response = _redis_cache_json(redis_client, cache_key, payload, ttl)
    return cached_response if cached_response is not None else payload


@app.get("/api/v1/dashboard/available-dates")
def dashboard_available_dates():
    rows=available_dates()
    for row in rows:
        if row.get("date_key") is not None:row["date_key"]=row["date_key"].isoformat()
    return {"dates":rows}


