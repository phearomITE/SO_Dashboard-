"""Authenticated, database-backed administration API for the SO dashboard.

PostgreSQL remains the source of truth. Redis is deliberately optional: it is
used only for short-lived read caching and login throttling, never as the only
copy of operational data.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import tempfile
import time
from io import BytesIO
from urllib.request import urlopen
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, text

from app.report_dates import reporting_period
from app import db
from app.config import DATA
from app.db import Event, Group, Leave, Missing, Plan, PlanFile, Roster, State, state, put
from app.imports import DEALER_REGIONS, import_plan, read_plan, read_roster, import_roster

router = APIRouter(prefix="/api/v1", tags=["admin"])
bearer = HTTPBearer(auto_error=False)
MAX_PLAN_BYTES = 20 * 1024 * 1024
POSITIONS = {"SO", "SS", "SA", "SL", "ASM", "HOS"}
_PLAN_GRID_CACHE: dict[str, dict[str, Any]] = {}


def current_admin(request: Request) -> str:
    user = getattr(request.state, "dashboard_user", None)
    if isinstance(user, dict) and user.get("email"):
        return user["email"]
    return os.getenv("DASHBOARD_ADMIN_NAME", "SO Operations Admin").strip() or "SO Operations Admin"


def _redis(request: Request):
    return getattr(request.app.state, "redis", None)


def _cache_get(request: Request, key: str):
    client = _redis(request)
    if not client:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _cache_set(request: Request, key: str, value: Any, ttl: int = 20):
    client = _redis(request)
    if not client:
        return
    try:
        client.setex(key, ttl, json.dumps(value, default=str, separators=(",", ":")))
    except Exception:
        pass


def invalidate_cache(request: Request):
    client = _redis(request)
    if not client:
        return
    try:
        keys = list(client.scan_iter("so-dashboard:v*:*", count=200))
        if keys:
            client.delete(*keys)
    except Exception:
        pass


def _audit(conn, actor: str, action: str, entity: str, entity_id: str,
           before: Any = None, after: Any = None):
    conn.execute(text("""
        INSERT INTO dashboard_audit_v1 (actor,action,entity,entity_id,before_data,after_data)
        VALUES (:actor,:action,:entity,:entity_id,CAST(:before AS jsonb),CAST(:after AS jsonb))
    """), {"actor": actor, "action": action, "entity": entity, "entity_id": entity_id,
           "before": json.dumps(before, default=str) if before is not None else None,
           "after": json.dumps(after, default=str) if after is not None else None})


class RosterBody(BaseModel):
    employee_id: str = Field(min_length=1, max_length=80)
    telegram_id: int | None = Field(default=None, gt=0)
    real_name: str = Field(min_length=1, max_length=255)
    username: str = Field(default="", max_length=255)
    region: str = Field(pattern=r"^R[1-8]$")
    dealer: str = Field(min_length=2, max_length=20)
    position: str = Field(default="SO", max_length=80)
    active: bool = True


class LocationBody(BaseModel):
    latitude: float = Field(ge=9.8, le=14.75)
    longitude: float = Field(ge=102.3, le=107.7)


class SettingsBody(BaseModel):
    automation_enabled: bool | None = None
    alert_interval_minutes: int | None = Field(default=None, ge=5, le=60)
    wrong_plan: bool | None = None
    over_10km: bool | None = None
    reason_acknowledgement: bool | None = None
    missing_report_followup: bool | None = None


class AttendanceOverride(BaseModel):
    day: date
    attendance: str = Field(pattern=r"^(AL Full Day|AL Morning|AL Afternoon|Present|Not Reported|Clear dashboard AL)$")


class AttendanceRangeOverride(BaseModel):
    start: date
    end: date
    employee_ids: list[str] = Field(min_length=1, max_length=100)
    attendance: str = Field(pattern=r"^(AL Full Day|AL Morning|AL Afternoon|Clear dashboard AL)$")


class MissReasonBody(BaseModel):
    start: date
    end: date
    reason: str = Field(default="", max_length=4000)


def _roster_out(row: Roster) -> dict:
    return {"employee_id": str(row.employee or ""), "telegram_id": row.uid,
            "real_name": row.name or "", "username": row.username or "",
            "region": row.region or "", "dealer": row.dealer or "",
            "position": row.position or "", "active": bool(row.active), "source": row.source or ""}


def _roster_api_out(row: dict) -> dict:
    item = dict(row)
    item["employee_id"] = str(item.pop("employee", "") or "")
    item["telegram_id"] = item.pop("uid", None)
    item["real_name"] = item.pop("name", "") or ""
    item["position"] = item.get("position") or ""
    item["region"] = item.get("region") or ""
    item["dealer"] = item.get("dealer") or ""
    item["active"] = bool(item.get("active"))
    item["source"] = item.get("source") or ""
    item["telegram_group"] = item.pop("telegram_group", "") or ""
    item["last_report_at"] = item.pop("last_report_at", None)
    item["reported_region"] = item.pop("reported_region", "") or ""
    item["reported_dealer"] = item.pop("reported_dealer", "") or ""
    return item


def _master_employee_ids() -> set[str] | None:
    """Read exact membership in the latest official List_SO workbook."""
    with db.Session() as session:
        saved = state(session, "roster_master_employee_ids", {})
    if "employee_ids" in saved:
        return {str(value) for value in saved.get("employee_ids", [])}
    # Older databases predate the membership snapshot. Seed the classification
    # from the bundled/latest roster file without changing database records.
    try:
        return {str(row["employee"]) for row in read_roster(DATA / "List_SO.xlsx")}
    except (OSError, ValueError, KeyError):
        return None


def _master_status(employee_id: str, source: str, master_ids: set[str] | None) -> str:
    if master_ids is not None:
        return "In Master" if employee_id in master_ids else "New Join"
    return "In Master" if source == "excel" else "New Join"


def _check_dealer(region: str, dealer: str):
    if DEALER_REGIONS.get(dealer.upper()) != region.upper():
        raise HTTPException(422, f"Dealer {dealer} is not assigned to {region}.")


@router.get("/admin/roster")
def get_roster(request: Request, _admin: str = Depends(current_admin)):
    cached = _cache_get(request, "so-dashboard:v2:roster")
    if cached is not None:
        return {"items": cached}
    master_ids = _master_employee_ids()
    with db.engine.connect() as conn:
        rows = conn.execute(text("""SELECT r.employee,r.uid,r.name,r.username,r.region,r.dealer,
                    r.position,r.active,r.source,coalesce(g.code,latest.group_name,'') AS telegram_group,
                    latest.submitted_at AS last_report_at,
                    CASE WHEN g.code ~ '^R[1-8]-' THEN split_part(g.code,'-',1) ELSE '' END AS reported_region,
                    CASE WHEN g.code ~ '^R[1-8]-' THEN substring(g.code from position('-' in g.code)+1) ELSE '' END AS reported_dealer
                    FROM so_roster_v1 r
                    LEFT JOIN LATERAL (
                      SELECT e.chat AS chat_id,gx.title AS group_name,e.at AS submitted_at
                      FROM so_events_v1 e LEFT JOIN so_groups_v1 gx ON gx.id=e.chat
                      WHERE e.uid=r.uid AND e.kind='form'
                      ORDER BY e.at DESC,e.mid DESC LIMIT 1
                    ) latest ON TRUE
                    LEFT JOIN so_groups_v1 g ON g.id=latest.chat_id
                    ORDER BY r.region,r.dealer,r.name""")).mappings().all()
        items = [_roster_api_out(dict(row)) for row in rows]
    with db.Session() as session:
        removed = set(state(session, "roster_dashboard_deleted_ids", {}).get("employee_ids", []))
    items = [item for item in items if item["employee_id"] not in removed]
    for item in items:
        item["status_master"] = _master_status(item["employee_id"], item["source"], master_ids)
    # New Join rows can be created from a valid Telegram report. Keep this
    # read cache short so those automatic roster additions appear promptly.
    _cache_set(request, "so-dashboard:v2:roster", items, 5)
    return {"items": items}


@router.post("/admin/roster/upload")
async def upload_roster_master(request: Request, file: UploadFile = File(...), admin: str = Depends(current_admin)):
    """Replace official master membership using the existing List_SO.xlsx import format.

    The same importer as /update_list_so preserves report history and manual
    transfers. Any invalid workbook rolls back the entire transaction.
    """
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(415, "Upload an .xlsx Excel workbook in List_SO format.")
    content = await file.read(MAX_PLAN_BYTES + 1)
    await file.close()
    if not content or len(content) > MAX_PLAN_BYTES:
        raise HTTPException(413, "Master Sale Excel must be between 1 byte and 20 MB.")
    if not zipfile.is_zipfile(BytesIO(content)):
        raise HTTPException(422, "This is not a valid Excel workbook.")
    try:
        with tempfile.TemporaryDirectory(prefix="so-roster-") as tmp:
            path = Path(tmp) / "List_SO.xlsx"
            path.write_bytes(content)
            with db.Session.begin() as session:
                count, reporters, controllers, controller_groups = import_roster(session, path)
                digest = hashlib.sha256(content).hexdigest()
                put(session, "bundled_roster", {"sha256": digest, "rows": count, "so": reporters,
                                                "controllers": controllers, "controller_groups": controller_groups})
                _audit(session.connection(), admin, "upload", "roster_master", filename,
                       after={"rows": count, "reporters": reporters, "controllers": controllers})
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        raise HTTPException(422, f"Master Sale unchanged: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, "Master Sale import failed. Existing records were retained.") from exc
    # Persist the official workbook to the shared /app/data volume, matching
    # the Telegram /update_list_so workflow, so restart uses the new master.
    try:
        staged = DATA / "List_SO.dashboard-new.xlsx"
        staged.write_bytes(content)
        os.replace(staged, DATA / "List_SO.xlsx")
    except OSError as exc:
        raise HTTPException(500, "Master imported, but the workbook could not be saved for restart. Check the data volume permissions.") from exc
    invalidate_cache(request)
    return {"filename": filename, "rows": count, "reporters": reporters,
            "controllers": controllers, "controller_groups": controller_groups}


@router.delete("/admin/roster/{employee_id}")
def delete_roster_master(employee_id: str, request: Request, admin: str = Depends(current_admin)):
    """Soft-delete: hide from master, deactivate reporting, preserve history and audit."""
    employee_id = employee_id.strip()
    with db.Session.begin() as session:
        record = session.scalar(select(Roster).where(Roster.employee == employee_id))
        if record is None:
            raise HTTPException(404, "Employee was not found.")
        before = _roster_out(record)
        removed = set(state(session, "roster_dashboard_deleted_ids", {}).get("employee_ids", []))
        removed.add(employee_id)
        put(session, "roster_dashboard_deleted_ids", {"employee_ids": sorted(removed)})
        master = set(state(session, "roster_master_employee_ids", {}).get("employee_ids", []))
        master.discard(employee_id)
        put(session, "roster_master_employee_ids", {"employee_ids": sorted(master)})
        record.active = False
        _audit(session.connection(), admin, "delete", "roster_master", employee_id,
               before=before, after={"active": False, "hidden": True, "history_retained": True})
    invalidate_cache(request)
    return {"employee_id": employee_id, "deleted": True, "history_retained": True}


@router.post("/admin/roster", status_code=201)
def add_roster(body: RosterBody, request: Request, admin: str = Depends(current_admin)):
    employee = body.employee_id.strip()
    dealer = body.dealer.strip().upper()
    _check_dealer(body.region, dealer)
    position = body.position.strip().upper()
    if position not in POSITIONS:
        raise HTTPException(422, "Position must be SO, SS, SA, SL, ASM, or HOS.")
    with db.engine.begin() as conn:
        exists = conn.execute(text("SELECT 1 FROM so_roster_v1 WHERE employee=:employee"), {"employee": employee}).first()
        if exists:
            raise HTTPException(409, "Employee ID already exists.")
        record = {"employee_id": employee, "telegram_id": body.telegram_id, "real_name": body.real_name.strip(),
                  "username": body.username.strip().lstrip("@"), "region": body.region,
                  "dealer": dealer, "position": position, "active": int(body.active), "source": "dashboard"}
        conn.execute(text("""INSERT INTO so_roster_v1 (employee,uid,name,username,region,dealer,position,active,source)
                    VALUES (:employee_id,:telegram_id,:real_name,:username,:region,:dealer,:position,:active,:source)"""), record)
        _audit(conn, admin, "create", "roster", employee, after=record)
    invalidate_cache(request)
    record["status_master"] = _master_status(employee, record["source"], _master_employee_ids())
    return record


@router.patch("/admin/roster/{employee_id}")
def edit_roster(employee_id: str, body: RosterBody, request: Request, admin: str = Depends(current_admin)):
    dealer = body.dealer.strip().upper()
    _check_dealer(body.region, dealer)
    position = body.position.strip().upper()
    if position not in POSITIONS:
        raise HTTPException(422, "Position must be SO, SS, SA, SL, ASM, or HOS.")
    if body.employee_id.strip() != employee_id.strip():
        raise HTTPException(422, "Employee ID is the record key and cannot be changed; create a new row instead.")
    with db.engine.begin() as conn:
        result = conn.execute(text("SELECT employee,uid,name,username,region,dealer,position,active,source FROM so_roster_v1 WHERE employee=:employee"), {"employee": employee_id}).mappings().first()
        if not result:
            raise HTTPException(404, "Employee not found.")
        before = dict(result)
        after = {"employee_id": employee_id.strip(), "telegram_id": body.telegram_id,
                 "real_name": body.real_name.strip(), "username": body.username.strip().lstrip("@"),
                 "region": body.region, "dealer": dealer, "position": position,
                 "active": int(body.active), "source": before.get("source") or "dashboard"}
        conn.execute(text("""UPDATE so_roster_v1 SET uid=:telegram_id,name=:real_name,username=:username,
                    region=:region,dealer=:dealer,position=:position,active=:active WHERE employee=:employee_id"""), after)
        if (before.get("region"), before.get("dealer")) != (body.region, dealer) and body.telegram_id:
            # Keep every active identity for this Telegram account aligned with
            # the admin's assignment, matching the bot's valid-report reconcile.
            conn.execute(text("""UPDATE so_roster_v1 SET region=:region,dealer=:dealer,source='manual'
                        WHERE uid=:uid AND active=1"""),
                         {"region": body.region, "dealer": dealer, "uid": body.telegram_id})
            override = {"telegram_id": body.telegram_id, "employee_id": employee_id,
                        "region": body.region, "dealer": dealer, "changed_by": "dashboard_admin",
                        "changed_at": db.now().isoformat(), "active": True}
            conn.execute(text("""INSERT INTO so_states_v1 (key,data) VALUES (:key,CAST(:data AS jsonb))
                        ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data"""),
                         {"key": f"roster_override:{body.telegram_id}", "data": json.dumps(override)})
        _audit(conn, admin, "update", "roster", employee_id, before=before, after=after)
    invalidate_cache(request)
    return after


@router.get("/admin/plans")
def get_active_plan(request: Request, _admin: str = Depends(current_admin)):
    cached = _cache_get(request, "so-dashboard:v2:plan")
    if cached is not None:
        return cached
    with db.Session() as session:
        latest = state(session, "plan_latest", {})
        batch = latest.get("batch")
        workbook = session.scalar(select(PlanFile.content).where(PlanFile.batch == batch)) if batch else None
    grid=_PLAN_GRID_CACHE.get(batch or "") if batch else None
    if grid is None:
        items=[];dates=[];columns=[]
        if workbook:
            _,covered_dates,items,columns=read_plan(BytesIO(workbook),include_dates=True,include_grid=True)
            dates=sorted(covered_dates)
        grid={"items":items,"dates":dates,"columns":columns}
        if batch:
            _PLAN_GRID_CACHE.clear()
            _PLAN_GRID_CACHE[batch]=grid
    items=grid["items"];dates=grid["dates"];columns=grid["columns"]
    payload = {"latest": latest, "items": items, "dates": dates, "columns": columns}
    _cache_set(request, "so-dashboard:v2:plan", payload, 30)
    return payload


@router.post("/admin/plans/upload")
async def upload_plan(request: Request, file: UploadFile = File(...), admin: str = Depends(current_admin)):
    filename = Path(file.filename or "").name
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(415, "Upload an .xlsx workbook.")
    content = await file.read(MAX_PLAN_BYTES + 1)
    await file.close()
    if not content or len(content) > MAX_PLAN_BYTES:
        raise HTTPException(413, "Plan workbook must be between 1 byte and 20 MB.")
    if not zipfile.is_zipfile(__import__("io").BytesIO(content)):
        raise HTTPException(422, "The uploaded file is not a valid Excel workbook.")
    admin_ids = [x.strip() for x in os.getenv("GLOBAL_ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    admin_id = int(os.getenv("DASHBOARD_ADMIN_ID", admin_ids[0] if admin_ids else "0"))
    try:
        with tempfile.TemporaryDirectory(prefix="so-plan-") as tmp:
            path = Path(tmp) / "upload.xlsx"
            path.write_bytes(content)
            with db.Session.begin() as session:
                count = import_plan(session, path, admin_id, filename)
                latest = state(session, "plan_latest", {})
                result = {"rows": count, "latest": latest}
                _audit(session.connection(), admin, "upload", "plan", str(latest.get("batch", "")),
                       after={"filename": filename, "rows": count, "latest": latest})
    except (ValueError, OSError) as exc:
        raise HTTPException(422, f"Plan was not changed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(500, "Plan could not be committed. Existing plan was retained.") from exc
    invalidate_cache(request)
    return result


@router.get("/admin/performance")
def performance(request: Request, start: date, end: date, region: str | None = None,
                dealer: str | None = None, _admin: str = Depends(current_admin)):
    if end < start or (end - start).days > 366:
        raise HTTPException(422, "Date range must be ordered and no longer than 366 days.")
    cache_key = f"so-dashboard:v1:performance:{start}:{end}:{region or '*'}:{dealer or '*'}"
    cached = _cache_get(request, cache_key)
    if cached is not None:
        return cached
    params: dict[str, Any] = {"start": start, "end": end,
                              "region": region.upper() if region else None,
                              "dealer": dealer.upper() if dealer else None}
    visit_predicate = "report_date BETWEEN :start AND :end AND upper(trim(coalesce(position,'SO'))) IN ('SO','SS')"
    if region: visit_predicate += " AND upper(region)=:region"
    if dealer: visit_predicate += " AND upper(dealer)=:dealer"
    roster_scope = "r.active=1 AND upper(trim(r.position)) IN ('SO','SS') AND (:region IS NULL OR upper(r.region)=:region) AND (:dealer IS NULL OR upper(r.dealer)=:dealer)"
    with db.engine.connect() as conn:
        totals = dict(conn.execute(text(f"""SELECT count(*) AS checkins,
                    count(DISTINCT employee_id) AS active_sale,
                    count(DISTINCT nullif(actual_district,'')) AS districts,
                    count(DISTINCT nullif(actual_commune_code,'')) AS communes,
                    count(*) FILTER (WHERE coalesce(road_distance_km,0)>10) AS over_10km
                    FROM silver_so_visit_v1 WHERE {visit_predicate}"""), params).mappings().one())
        grouped_sql = f"""SELECT r.region,count(DISTINCT r.employee) AS roster_count,
                    count(DISTINCT v.employee_id) AS active_sale,count(DISTINCT v.visit_id) AS checkins,
                    count(DISTINCT nullif(v.actual_district,'')) AS districts,
                    count(DISTINCT nullif(v.actual_commune_code,'')) AS communes,
                    count(DISTINCT v.visit_id) FILTER (WHERE coalesce(v.road_distance_km,0)>10) AS over_10km,
                    greatest(count(DISTINCT r.employee)-count(DISTINCT v.employee_id),0) AS al_count
                    FROM so_roster_v1 r LEFT JOIN silver_so_visit_v1 v ON v.employee_id=r.employee
                    AND v.report_date BETWEEN :start AND :end AND upper(trim(coalesce(v.position,'SO'))) IN ('SO','SS')
                    WHERE {roster_scope} GROUP BY r.region ORDER BY r.region"""
        by_region = [dict(r) for r in conn.execute(text(grouped_sql), params).mappings().all()]
        by_dealer = [dict(r) for r in conn.execute(text(grouped_sql.replace("SELECT r.region,", "SELECT r.region,r.dealer,").replace("GROUP BY r.region ORDER", "GROUP BY r.region,r.dealer ORDER")), params).mappings().all()]
        individuals = [dict(r) for r in conn.execute(text(f"""SELECT r.region,r.dealer,r.employee AS employee_id,
                    r.name AS real_name,r.position,count(DISTINCT v.visit_id) AS checkins,
                    count(DISTINCT nullif(v.actual_district,'')) AS districts,
                    count(DISTINCT nullif(v.actual_commune_code,'')) AS communes,
                    count(DISTINCT v.visit_id) FILTER (WHERE coalesce(v.road_distance_km,0)>10) AS over_10km,
                    CASE WHEN count(v.visit_id)>0 THEN 'Present' ELSE 'AL' END AS attendance
                    FROM so_roster_v1 r LEFT JOIN silver_so_visit_v1 v ON v.employee_id=r.employee
                    AND v.report_date BETWEEN :start AND :end AND upper(trim(coalesce(v.position,'SO'))) IN ('SO','SS')
                    WHERE {roster_scope} GROUP BY r.region,r.dealer,r.employee,r.name,r.position
                    ORDER BY r.region,r.dealer,r.name"""), params).mappings().all()]
        headcount = conn.execute(text(f"SELECT count(*) FROM so_roster_v1 r WHERE {roster_scope}"), params).scalar_one()
    payload = {"start": start.isoformat(), "end": end.isoformat(), "totals": totals,
               "active_roster": int(headcount or 0), "al_estimate": max(0, int(headcount or 0)-int(totals.get("active_sale") or 0)),
               "by_region": by_region, "by_dealer": by_dealer, "individuals": individuals}
    _cache_set(request, cache_key, payload, 20)
    return payload


@router.get("/admin/reasons")
def recent_reason_summary(request: Request, day: date | None = None, region: str | None = None,
                          dealer: str | None = None, start: date | None = None, end: date | None = None,
                          _admin: str = Depends(current_admin)):
    """Explicit user replies; not every outlet photo or form is an explanation."""
    start,end=reporting_period(day,start,end)
    cache_key=f"so-dashboard:v3:reasons:{start}:{end}:{region or '*'}:{dealer or '*'}"
    cached=_cache_get(request,cache_key)
    if cached is not None:return cached
    clauses=["key LIKE 'reason_review:%'", "data->>'date' BETWEEN :start AND :end"]
    params={'start':start.isoformat(),'end':end.isoformat()}
    if region:clauses.append("upper(data->>'region')=:region");params['region']=region.upper()
    if dealer:clauses.append("upper(data->>'dealer')=:dealer");params['dealer']=dealer.upper()
    sql="SELECT data FROM so_states_v1 WHERE "+' AND '.join(clauses)+" ORDER BY data->>'recorded_at' DESC"
    with db.engine.connect() as conn:
        rows=[r['data'] for r in conn.execute(text(sql),params).mappings().all()]
    # Reads do not silently rewrite classifications. Historical pending replies
    # are reprocessed by the authenticated Hermes background endpoint instead.
    reasons=[x for x in rows if x.get('is_reason') is True]
    no_reason=[x for x in rows if x.get('is_reason') is False]
    review=[x for x in rows if x.get('is_reason') is None]
    payload={'date':start.isoformat(),'start':start.isoformat(),'end':end.isoformat(),'received':len(rows),'with_reason':len(reasons),
            'not_reason':len(no_reason),'needs_review':len(review),'items':rows}
    _cache_set(request,cache_key,payload,10)
    return payload


@router.get("/admin/al")
def al_summary(request: Request, day: date, region: str | None = None, dealer: str | None = None,
               _admin: str = Depends(current_admin)):
    cache_key=f"so-dashboard:v3:al:{day}:{region or '*'}:{dealer or '*'}"
    cached=_cache_get(request,cache_key)
    if cached is not None:return cached
    day_start = datetime.combine(day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    params: dict[str, Any] = {"day": day, "day_start": day_start, "day_end": day_end,
                              "morning_start": day_start + timedelta(hours=8),
                              "noon": day_start + timedelta(hours=12),
                              "afternoon_start": day_start + timedelta(hours=13),
                              "afternoon_end": day_start + timedelta(hours=17),
                              "today": db.now().date()}
    clauses = ["r.active=1", "upper(trim(r.position)) IN ('SO','SS')"]
    if region:
        clauses.append("upper(r.region)=:region"); params["region"] = region.upper()
    if dealer:
        clauses.append("upper(r.dealer)=:dealer"); params["dealer"] = dealer.upper()
    with db.engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(text(f"""SELECT r.region,r.dealer,r.employee AS employee_id,r.uid AS telegram_id,
                    r.name AS real_name,r.position,
                    EXISTS(SELECT 1 FROM silver_so_visit_v1 v WHERE v.employee_id=r.employee
                           AND v.report_date=:day) AS has_checkin,
                    coalesce(al.full_day,false) AS full_day,coalesce(al.morning,false) AS morning_al,
                    coalesce(al.afternoon,false) AS afternoon_al,coalesce(al.sources,'') AS leave_sources,
                    coalesce(al.leave_from,'') AS leave_from,coalesce(al.leave_to,'') AS leave_to,
                    coalesce(nullif(g.code,''),nullif(g.title,''),
                             nullif(concat_ws('-',nullif(r.region,''),nullif(r.dealer,'')),''),'—') AS telegram_group
                    FROM so_roster_v1 r
                    LEFT JOIN LATERAL (
                      SELECT bool_or(l.start<=:day_start AND l.end>=:day_end) AS full_day,
                             bool_or(l.start<:noon AND l.end>:morning_start) AS morning,
                             bool_or(l.start<:afternoon_end AND l.end>:afternoon_start) AS afternoon,
                             string_agg(DISTINCT l.source, ', ') AS sources,
                             to_char(min(l.start)::date, 'YYYY-MM-DD') AS leave_from,
                             to_char(max((l.end - interval '1 microsecond')::date), 'YYYY-MM-DD') AS leave_to
                      FROM so_leave_v1 l WHERE l.employee=r.employee AND l.uid=r.uid
                        AND l.start<:day_end AND l.end>:day_start
                    ) al ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT e.chat AS chat_id FROM so_events_v1 e
                      LEFT JOIN so_groups_v1 gx ON gx.id=e.chat
                      WHERE e.uid=r.uid AND e.kind='pin'
                        AND e.chat<>-1004303041907
                        AND lower(trim(coalesce(gx.title,'')))<>'test_so'
                      ORDER BY CASE WHEN e.at>=:day_start AND e.at<:day_end THEN 0 ELSE 1 END,
                               CASE WHEN e.at>=:day_start AND e.at<:day_end THEN e.at END DESC,
                               e.at DESC,e.mid DESC LIMIT 1
                    ) latest ON TRUE
                    LEFT JOIN so_groups_v1 g ON g.id=latest.chat_id
                    WHERE {' AND '.join(clauses)} ORDER BY r.region,r.dealer,r.name"""), params).mappings().all()]
        scheduled_count = conn.execute(text(f"""SELECT count(DISTINCT r.employee)
                    FROM so_roster_v1 r
                    JOIN so_leave_v1 l ON l.employee=r.employee AND l.uid=r.uid
                    WHERE {' AND '.join(clauses)} AND l.start>=:day_end"""), params).scalar_one()
    for row in rows:
        if row["full_day"] or (row["morning_al"] and row["afternoon_al"]):
            leave_status = "AL Full Day"
        elif row["morning_al"]:
            leave_status = "AL Morning"
        elif row["afternoon_al"]:
            leave_status = "AL Afternoon"
        else:
            leave_status = ""
        row["leave_status"] = leave_status
        row["leave_from"] = row.get("leave_from") or ""
        row["leave_to"] = row.get("leave_to") or ""
        row["full_day_conflict"] = bool(
            (row["full_day"] or (row["morning_al"] and row["afternoon_al"])) and row["has_checkin"]
        )
        row["attendance"] = (
            leave_status if row["full_day_conflict"] or not row["has_checkin"] else
            (f"Present + {leave_status}" if leave_status else "Present")
        )
        if not row["attendance"]:
            row["attendance"] = "Scheduled" if day > params["today"] else "Not Reported"
        row["source"] = (row["leave_sources"] or "Recorded AL") if leave_status else (
            "Check-in activity" if row["has_checkin"] else
            "Future date — not due" if day > params["today"] else "No check-in found")
        row["telegram_group"] = row["telegram_group"] or "—"
        for key in ("full_day", "morning_al", "afternoon_al"):
            row.pop(key, None)
    payload={"date": day.isoformat(),
            "definition": "AL comes from a saved Telegram/Morning Talk leave or dashboard correction. A future date without activity is Scheduled, not AL. Missing-report alerts are suppressed only by a saved leave interval covering that alert window.",
            "items": rows, "al_count": sum(bool(x["leave_status"]) for x in rows),
            "scheduled_count": int(scheduled_count or 0),
            "roster_total": len(rows)}
    _cache_set(request,cache_key,payload,5)
    return payload


@router.get("/admin/first-check-alerts")
def first_check_alerts(request: Request, _admin: str = Depends(current_admin)):
    """Dashboard alerts for SO/SS users whose first location check is late or missing."""
    cache_key="so-dashboard:v3:first-check-alerts"
    cached=_cache_get(request,cache_key)
    if cached is not None:return cached
    now = db.now()
    day_start = datetime.combine(now.date(), datetime.min.time())
    deadline = day_start + timedelta(hours=9)
    if now < deadline:
        return {"date": now.date().isoformat(), "deadline": "09:00 AM", "deadline_passed": False,
                "count": 0, "items": []}

    day_end = day_start + timedelta(days=1)
    rows = []
    with db.engine.connect() as conn:
        rows = [dict(row) for row in conn.execute(text("""
            WITH active_roster AS (
              SELECT r.employee AS employee_id,r.uid AS telegram_id,r.name AS real_name,
                     r.region,r.dealer,r.position
              FROM so_roster_v1 r
              WHERE r.active=1 AND upper(trim(coalesce(r.position,'SO'))) IN ('SO','SS')
            ), first_pins AS (
              SELECT DISTINCT ON (e.uid) e.uid,e.at AS first_check_at,
                     coalesce(nullif(g.code,''),nullif(g.title,'')) AS telegram_group
              FROM so_events_v1 e LEFT JOIN so_groups_v1 g ON g.id=e.chat
              WHERE e.kind='pin' AND e.at>=:day_start AND e.at<=:now AND e.at<:day_end
                AND e.chat<>-1004303041907 AND lower(trim(coalesce(g.title,'')))<>'test_so'
                AND (e.data->>'lat') ~ '^-?[0-9]+([.][0-9]+)?$'
                AND (e.data->>'lon') ~ '^-?[0-9]+([.][0-9]+)?$'
                AND (e.data->>'lat')::double precision BETWEEN 9.80 AND 14.75
                AND (e.data->>'lon')::double precision BETWEEN 102.30 AND 107.70
              ORDER BY e.uid,e.at,e.mid
            )
            SELECT r.employee_id,r.telegram_id,r.real_name,r.region,r.dealer,r.position,
                   coalesce(nullif(f.telegram_group,''),
                            nullif(concat_ws('-',nullif(r.region,''),nullif(r.dealer,'')),''),'—') AS telegram_group,
                   f.first_check_at,
                   CASE WHEN f.first_check_at IS NULL THEN 'missing' ELSE 'late' END AS alert_type
            FROM active_roster r LEFT JOIN first_pins f ON f.uid=r.telegram_id
            WHERE (f.first_check_at IS NULL OR f.first_check_at>:deadline)
              AND NOT EXISTS (
                SELECT 1 FROM so_leave_v1 l
                WHERE l.employee=r.employee_id AND l.uid=r.telegram_id
                  AND l.start<=:deadline AND l."end">:deadline
              )
            ORDER BY r.region,r.dealer,r.real_name,r.employee_id
        """), {"day_start": day_start, "day_end": day_end, "deadline": deadline, "now": now}).mappings().all()]
    items = []
    for row in rows:
        first_check = row.pop("first_check_at", None)
        row["first_check_time"] = first_check.strftime("%I:%M %p") if first_check else ""
        row["minutes_late"] = max(0, int((first_check - deadline).total_seconds() // 60)) if first_check else None
        items.append(row)
    payload={"date": now.date().isoformat(), "deadline": "09:00 AM", "deadline_passed": True,
            "count": len(items), "items": items}
    _cache_set(request,cache_key,payload,10)
    return payload


@router.get("/admin/al/range")
def al_range_summary(request: Request, start: date, end: date,
                     region: str | None = None, dealer: str | None = None,
                     _admin: str = Depends(current_admin)):
    """List saved leave intervals that overlap the requested date range."""
    if end < start or (end - start).days >= 90:
        raise HTTPException(422, "AL date range must be ordered and no longer than 90 days.")
    start_at = datetime.combine(start, datetime.min.time())
    end_at = datetime.combine(end + timedelta(days=1), datetime.min.time())
    query = select(
        Leave.employee.label("employee_id"),
        func.coalesce(Roster.name, Leave.name, "").label("real_name"),
        Roster.region.label("region"), Roster.dealer.label("dealer"),
        Group.code.label("telegram_group"), Leave.start.label("leave_start"),
        Leave.end.label("leave_end"), Leave.source.label("source"),
    ).join(Roster, Roster.employee == Leave.employee).outerjoin(Group, Group.id == Leave.chat).where(
        Roster.active == 1, func.upper(func.trim(Roster.position)).in_(("SO", "SS")),
        Leave.start < end_at, Leave.end > start_at,
    )
    if region:
        query = query.where(func.upper(Roster.region) == region.upper())
    if dealer:
        query = query.where(func.upper(Roster.dealer) == dealer.upper())
    query = query.order_by(Roster.region, Roster.dealer, Roster.name, Leave.start, Leave.end)

    items = []
    with db.Session() as session:
        records = session.execute(query).mappings().all()
    for record in records:
        leave_start = record["leave_start"]
        leave_end = record["leave_end"]
        if leave_start.time() == datetime.min.time() and leave_end.time() == datetime.min.time():
            leave_type = "AL Full Day"
        elif leave_start.hour < 12 and leave_end.hour <= 12:
            leave_type = "AL Morning"
        elif leave_start.hour >= 12:
            leave_type = "AL Afternoon"
        else:
            leave_type = "AL Custom"
        first_day = max(leave_start.date(), start)
        last_day = min((leave_end - timedelta(microseconds=1)).date(), end)
        if last_day < first_day:
            continue
        items.append({
            "employee_id": str(record["employee_id"] or ""),
            "real_name": record["real_name"] or "",
            "telegram_group": record["telegram_group"] or "—",
            "region": record["region"] or "", "dealer": record["dealer"] or "",
            "leave_type": leave_type, "from_date": first_day.isoformat(),
            "to_date": last_day.isoformat(), "days": (last_day - first_day).days + 1,
            "source": record["source"] or "Recorded AL",
            "_merge_key": (str(record["employee_id"] or ""), record["telegram_group"] or "—",
                           leave_type, record["source"] or "Recorded AL",
                           leave_start.time(), leave_end.time()),
        })
    # Dashboard range assignments are stored as one alert interval per day.
    # Combine adjacent entries here so the report displays a single clear span.
    merged = []
    for item in items:
        if merged and merged[-1]["_merge_key"] == item["_merge_key"] and (
                date.fromisoformat(item["from_date"]) <=
                date.fromisoformat(merged[-1]["to_date"]) + timedelta(days=1)):
            merged[-1]["to_date"] = max(merged[-1]["to_date"], item["to_date"])
            merged[-1]["days"] = (date.fromisoformat(merged[-1]["to_date"]) -
                                  date.fromisoformat(merged[-1]["from_date"])).days + 1
        else:
            merged.append(item)
    for item in merged:
        item.pop("_merge_key", None)
    return {"from_date": start.isoformat(), "to_date": end.isoformat(),
            "count": len(merged), "items": merged}


@router.put("/admin/al/range")
def override_attendance_range_route(body: AttendanceRangeOverride, request: Request,
                                    admin: str = Depends(current_admin)):
    return _override_attendance_range_impl(body, request, admin)


@router.put("/admin/al/{employee_id}")
def override_attendance(employee_id: str, body: AttendanceOverride, request: Request,
                        admin: str = Depends(current_admin)):
    day_start = datetime.combine(body.day, datetime.min.time())
    day_end = day_start + timedelta(days=1)
    with db.Session.begin() as session:
        person = session.scalar(select(Roster).where(Roster.employee == employee_id, Roster.active == 1))
        if not person:
            raise HTTPException(404, "Active employee not found.")
        if not person.uid:
            raise HTTPException(409, "This employee has no Telegram ID; bot alerts cannot be suppressed for this roster row.")
        # Prefer the employee's most recent valid form/reporting group. The
        # spreadsheet assignment can lag behind reality (for example, the SO
        # is rostered to CA1 but has started submitting reports in CA2).
        latest_report_chat = session.scalar(
            select(Event.chat).where(Event.uid == person.uid, Event.kind == "form",
                                     Event.at < day_end)
            .order_by(Event.at.desc(), Event.id.desc()).limit(1)
        )
        group = session.get(Group, latest_report_chat) if latest_report_chat else None
        if not group or not group.active:
            group = session.scalar(select(Group).where(Group.active == 1,
                                  Group.code == f"{person.region}-{person.dealer}"))
        group_id = group.id if group else 0
        key = f"dashboard_al_override:{body.day.isoformat()}:{employee_id}"
        before = state(session, key, {})
        after = {"attendance": body.attendance, "changed_by": admin, "changed_at": db.now().isoformat()}
        session.execute(delete(Leave).where(Leave.employee == employee_id, Leave.uid == person.uid,
                            Leave.source == "Dashboard admin correction", Leave.start < day_end,
                            Leave.end > day_start))
        if body.attendance.startswith("AL "):
            if body.attendance == "AL Full Day":
                leave_start, leave_end = day_start, day_end
            elif body.attendance == "AL Morning":
                leave_start, leave_end = day_start + timedelta(hours=8), day_start + timedelta(hours=12)
            else:
                leave_start, leave_end = day_start + timedelta(hours=13), day_start + timedelta(hours=17)
            already_covered = session.scalar(select(Leave.id).where(
                Leave.uid == person.uid,
                Leave.start <= leave_start, Leave.end >= leave_end).limit(1))
            if not already_covered:
                session.add(Leave(chat=group_id, uid=person.uid, employee=person.employee,
                                  name=person.name, region=person.region, dealer=person.dealer,
                                  position=person.position, start=leave_start, end=leave_end,
                                  source="Dashboard admin correction", recorded_by=int(os.getenv("DASHBOARD_ADMIN_ID", "0")),
                                  recorded_at=db.now()))
            session.execute(delete(Missing).where(Missing.uid == person.uid,
                                Missing.start < leave_end, Missing.end > leave_start))
        if body.attendance == "Clear dashboard AL":
            after = {"attendance": "Cleared dashboard AL", "changed_by": admin,
                     "changed_at": db.now().isoformat()}
        put(session, key, after)
        _audit(session.connection(), admin, "override", "attendance", f"{body.day}:{employee_id}", before=before, after=after)
    invalidate_cache(request)
    return {"employee_id": employee_id, "date": body.day.isoformat(), **after}


def _override_attendance_range_impl(body: AttendanceRangeOverride, request: Request, admin: str):
    """Apply an admin AL interval to selected users, one alert window per day."""
    if body.end < body.start or (body.end - body.start).days >= 90:
        raise HTTPException(422, "AL date range must be ordered and no longer than 90 days.")
    employee_ids = list(dict.fromkeys(str(item).strip() for item in body.employee_ids if str(item).strip()))
    if not employee_ids or len(employee_ids) > 100:
        raise HTTPException(422, "Select between 1 and 100 active employees.")
    day_count = (body.end - body.start).days + 1
    days = [body.start + timedelta(days=offset) for offset in range(day_count)]
    range_end = datetime.combine(body.end + timedelta(days=1), datetime.min.time())
    recorded_at = db.now()

    with db.Session.begin() as session:
        people = {person.employee: person for person in session.scalars(
            select(Roster).where(Roster.employee.in_(employee_ids), Roster.active == 1))}
        missing_people = [employee_id for employee_id in employee_ids if employee_id not in people]
        if missing_people:
            raise HTTPException(404, f"Active employee not found: {', '.join(missing_people[:10])}.")

        assignments = []
        for employee_id in employee_ids:
            person = people[employee_id]
            if not person.uid:
                raise HTTPException(409, f"{employee_id} has no Telegram ID; bot alerts cannot be suppressed.")
            latest_report_chat = session.scalar(
                select(Event.chat).where(Event.uid == person.uid, Event.kind == "form",
                                         Event.at < range_end)
                .order_by(Event.at.desc(), Event.id.desc()).limit(1)
            )
            group = session.get(Group, latest_report_chat) if latest_report_chat else None
            if not group or not group.active:
                group = session.scalar(select(Group).where(
                    Group.active == 1, Group.code == f"{person.region}-{person.dealer}"))
            group_id = group.id if group else 0
            assignments.append((person, group_id))

        for person, group_id in assignments:
            previous = []
            for day in days:
                day_start = datetime.combine(day, datetime.min.time())
                day_end = day_start + timedelta(days=1)
                key = f"dashboard_al_override:{day.isoformat()}:{person.employee}"
                prior = state(session, key, {})
                if prior:
                    previous.append({"date": day.isoformat(), **prior})
                after = {"attendance": "Cleared dashboard AL" if body.attendance == "Clear dashboard AL" else body.attendance,
                         "changed_by": admin,
                         "changed_at": recorded_at.isoformat(), "range_start": body.start.isoformat(),
                         "range_end": body.end.isoformat()}

                # Replace only dashboard-created AL for this employee/day.
                # Telegram and Morning Talk records remain authoritative.
                session.execute(delete(Leave).where(
                    Leave.employee == person.employee, Leave.uid == person.uid,
                    Leave.source == "Dashboard admin correction",
                    Leave.start < day_end, Leave.end > day_start))
                if body.attendance != "Clear dashboard AL":
                    if body.attendance == "AL Full Day":
                        leave_start, leave_end = day_start, day_end
                    elif body.attendance == "AL Morning":
                        leave_start, leave_end = day_start + timedelta(hours=8), day_start + timedelta(hours=12)
                    else:
                        leave_start, leave_end = day_start + timedelta(hours=13), day_start + timedelta(hours=17)
                    already_covered = session.scalar(select(Leave.id).where(
                        Leave.uid == person.uid,
                        Leave.start <= leave_start, Leave.end >= leave_end).limit(1))
                    if not already_covered:
                        session.add(Leave(chat=group_id, uid=person.uid, employee=person.employee,
                                          name=person.name, region=person.region, dealer=person.dealer,
                                          position=person.position, start=leave_start, end=leave_end,
                                          source="Dashboard admin correction",
                                          recorded_by=int(os.getenv("DASHBOARD_ADMIN_ID", "0")),
                                          recorded_at=recorded_at))
                    session.execute(delete(Missing).where(
                        Missing.uid == person.uid,
                        Missing.start < leave_end, Missing.end > leave_start))
                put(session, key, after)
            _audit(session.connection(), admin, "override_range", "attendance",
                   f"{body.start}:{body.end}:{person.employee}",
                   before={"days": previous},
                   after={"attendance": body.attendance, "start": body.start.isoformat(),
                          "end": body.end.isoformat(), "days": day_count})

    invalidate_cache(request)
    return {"employee_ids": employee_ids, "from_date": body.start.isoformat(),
            "to_date": body.end.isoformat(), "days": day_count,
            "attendance": body.attendance}


@router.get("/admin/tunnel")
def tunnel_status(_admin: str = Depends(current_admin)):
    inspector = os.getenv("NGROK_API_URL", "http://ngrok:4040").rstrip("/")
    try:
        with urlopen(inspector + "/api/tunnels", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        tunnels = [t for t in payload.get("tunnels", []) if str(t.get("public_url", "")).startswith("https://")]
        if tunnels:
            return {"active": True, "public_url": tunnels[0]["public_url"], "message": "Tunnel is live. Open it and authenticate."}
        return {"active": False, "public_url": "", "message": "ngrok is running but has not published an HTTPS tunnel yet."}
    except Exception:
        return {"active": False, "public_url": "", "message": "Public tunnel is not running. Start RUN_NGROK_DASHBOARD.bat to create the link."}


@router.get("/admin/dealers")
def dealers(request: Request, _admin: str = Depends(current_admin)):
    with db.Session() as session:
        rows = load_dealers_with_origins(session)
    return {"items": rows}


def load_dealers_with_origins(session):
    from app.dashboard import load_dealers
    rows = load_dealers()
    for row in rows:
        origin = state(session, f"dealer_origin:{row['dealer_key']}", {})
        if origin:
            row["latitude"] = float(origin["lat"]); row["longitude"] = float(origin["lon"])
            row["origin_source"] = "dashboard_override"
        else:
            row["origin_source"] = "master_file"
    return rows


@router.put("/admin/dealers/{dealer_key}/origin")
def update_dealer_origin(dealer_key: str, body: LocationBody, request: Request,
                         admin: str = Depends(current_admin)):
    match = re.fullmatch(r"(R[1-8])-([A-Z0-9]+)", dealer_key.upper())
    if not match or DEALER_REGIONS.get(match.group(2)) != match.group(1):
        raise HTTPException(422, "Dealer must be a valid Region-Dealer key such as R1-CA1.")
    key = f"dealer_origin:{dealer_key.upper()}"
    now = datetime.utcnow().isoformat()
    with db.engine.begin() as conn:
        existing = conn.execute(text("SELECT data FROM so_states_v1 WHERE key=:key"), {"key": key}).scalar()
        before = existing or None
        after = {"lat": body.latitude, "lon": body.longitude, "updated_by": "dashboard", "updated_at": now}
        conn.execute(text("""INSERT INTO so_states_v1 (key,data) VALUES (:key,CAST(:data AS jsonb))
                      ON CONFLICT (key) DO UPDATE SET data=EXCLUDED.data"""),
                     {"key": key, "data": json.dumps(after)})
        _audit(conn, admin, "update", "dealer_origin", dealer_key.upper(), before=before, after=after)
    invalidate_cache(request)
    return {"dealer_key": dealer_key.upper(), **after}


@router.get("/admin/settings")
def get_settings(_admin: str = Depends(current_admin)):
    from app.bot_control import get_bot_control
    from app.config import ALERT_INTERVAL_MINUTES
    from app.question_settings import get_question_settings
    with db.Session() as session:
        return {"automation": get_bot_control(session),
                "alert_interval_minutes": int(state(session, "alert_interval_minutes", {}).get("minutes", ALERT_INTERVAL_MINUTES)),
                "questions": get_question_settings(session)}


@router.patch("/admin/settings")
def update_settings(body: SettingsBody, request: Request, admin: str = Depends(current_admin)):
    from app.bot_control import set_automation
    from app.config import ALERT_INTERVAL_MINUTES
    from app.question_settings import get_question_settings, update_question_settings
    changed = body.model_dump(exclude_none=True)
    now = db.now()
    with db.Session.begin() as session:
        before = {"automation": state(session, "bot_automation_v1", {}),
                  "alert_interval_minutes": state(session, "alert_interval_minutes", {}).get("minutes", ALERT_INTERVAL_MINUTES),
                  "questions": get_question_settings(session)}
        if body.automation_enabled is not None:
            set_automation(session, body.automation_enabled, int(os.getenv("DASHBOARD_ADMIN_ID", "0")), now)
        if body.alert_interval_minutes is not None:
            put(session, "alert_interval_minutes", {"minutes": body.alert_interval_minutes,
                "changed_by": int(os.getenv("DASHBOARD_ADMIN_ID", "0")), "changed_at": now.isoformat()})
        questions = {k: v for k, v in changed.items() if k in {"wrong_plan", "over_10km", "reason_acknowledgement", "missing_report_followup"}}
        if questions:
            update_question_settings(session, questions, changed_by=int(os.getenv("DASHBOARD_ADMIN_ID", "0")))
        after = {"automation": state(session, "bot_automation_v1", {}),
                 "alert_interval_minutes": state(session, "alert_interval_minutes", {}).get("minutes", ALERT_INTERVAL_MINUTES),
                 "questions": get_question_settings(session)}
        _audit(session.connection(), admin, "update", "settings", "global", before=before, after=after)
    invalidate_cache(request)
    return after


@router.get("/admin/audit")
def get_audit(limit: int = 100, _admin: str = Depends(current_admin)):
    limit = min(max(limit, 1), 500)
    with db.engine.connect() as conn:
        rows = conn.execute(text("""SELECT id,actor,action,entity,entity_id,before_data,after_data,created_at
                    FROM dashboard_audit_v1 ORDER BY id DESC LIMIT :limit"""), {"limit": limit}).mappings().all()
    return {"items": [dict(row) for row in rows]}


@router.get('/admin/reasons/users')
def reason_summary_users(request:Request,day:date|None=None,region:str|None=None,dealer:str|None=None,
                         language:str=Query(default='km',pattern='^(km|en|both)$'),q:str=Query(default='',max_length=100),start:date|None=None,end:date|None=None,
                         _admin:str=Depends(current_admin)):
    from app.reason_exports import user_reason_report
    start,end=reporting_period(day,start,end)
    key=f"so-dashboard:v3:reasons:users:{start}:{end}:{region or '*'}:{dealer or '*'}:{language}:{q}"
    cached=_cache_get(request,key)
    if cached is not None:return cached
    payload=user_reason_report(db.engine,start,region,dealer,language,q,end=end)
    _cache_set(request,key,payload,10)
    return payload

@router.get('/admin/reasons/users/export')
def export_reason_summary_users(request:Request,day:date|None=None,region:str|None=None,dealer:str|None=None,
                                language:str=Query(default='km',pattern='^(km|en|both)$'),q:str=Query(default='',max_length=100),start:date|None=None,end:date|None=None,
                                _admin:str=Depends(current_admin)):
    from fastapi.responses import Response
    from app.reason_exports import user_reason_report,employee_reason_xlsx
    start,end=reporting_period(day,start,end)
    report=user_reason_report(db.engine,start,region,dealer,language,q,end=end)
    try:content=employee_reason_xlsx(report)
    except ValueError as exc:raise HTTPException(422,str(exc))
    return Response(content,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition':f'attachment; filename="Reason_Summary_By_Employee_{start}_{end}.xlsx"','Cache-Control':'no-store'})


@router.get('/admin/plans/actual')
def actual_plan_summary(request:Request,start:date,end:date,region:str|None=None,dealer:str|None=None,
                        q:str=Query(default='',max_length=100),_admin:str=Depends(current_admin)):
    from app.actual_plan import actual_plan_report
    start,end=reporting_period(None,start,end)
    key=f"so-dashboard:v5:actual-plan:{start}:{end}:{region or '*'}:{dealer or '*'}:{q}"
    cached=_cache_get(request,key)
    if cached is not None:return cached
    payload=actual_plan_report(db.engine,start,end,region,dealer,q)
    _cache_set(request,key,payload,20)
    return payload


@router.get("/admin/miss-report")
def get_miss_report(request: Request, day: date | None = None, start: date | None = None,
                    end: date | None = None, region: str | None = None, dealer: str | None = None,
                    controller: str | None = None,
                    language: str = Query(default="km", pattern="^(km|en|both)$"),
                    q: str = Query(default="", max_length=100),
                    _admin: str = Depends(current_admin)):
    from app.miss_report import miss_report_data
    start, end = reporting_period(day, start, end)
    key = f"so-dashboard:v3:miss-report:{start}:{end}:{region or '*'}:{dealer or '*'}:{controller or '*'}:{language}:{q}"
    cached = _cache_get(request, key)
    if cached is not None:
        return cached
    payload = miss_report_data(
        db.engine, start, end, region=region, dealer=dealer,
        controller=controller, q=q, language=language,
    )
    _cache_set(request, key, payload, 10)
    return payload


@router.put("/admin/miss-report/{employee_id}/reason")
def update_miss_report_reason(employee_id: str, body: MissReasonBody, request: Request,
                              admin: str = Depends(current_admin)):
    from app.miss_report import save_miss_reason
    start, end = reporting_period(None, body.start, body.end)
    with db.Session.begin() as session:
        before = state(session, f"miss_reason_override:{start.isoformat()}:{end.isoformat()}:{employee_id}", {})
        after = save_miss_reason(session, employee_id, start, end, body.reason, admin)
        _audit(session.connection(), admin, "update", "miss_reason",
               f"{start}:{end}:{employee_id}", before=before, after=after)
    invalidate_cache(request)
    return after


@router.delete("/admin/miss-report/{employee_id}/reason")
def clear_miss_report_reason(employee_id: str, start: date, end: date, request: Request,
                             admin: str = Depends(current_admin)):
    from app.miss_report import delete_miss_reason
    start, end = reporting_period(None, start, end)
    with db.Session.begin() as session:
        before = state(session, f"miss_reason_override:{start.isoformat()}:{end.isoformat()}:{employee_id}", {})
        after = delete_miss_reason(session, employee_id, start, end, admin)
        _audit(session.connection(), admin, "delete", "miss_reason",
               f"{start}:{end}:{employee_id}", before=before, after=after)
    invalidate_cache(request)
    return after
