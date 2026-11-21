"""Dashboard SA Operations Overview analytics and Annual Leave reporting."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import db
from app.db import Controller, Event, Group, Leave, Missing, Roster, SilverVisit, State
from app.miss_report import EXCLUDED_CHAT_IDS, _is_test_group, _uid_str, _valid_geo


def _resolve_controller_label(region: str, dealer: str, ctrl_by_code: dict[str, list[str]]) -> str:
    names: list[str] = []
    for key in (f"{region}-{dealer}".upper(), dealer.upper(), region.upper()):
        for name in ctrl_by_code.get(key, []):
            if name not in names:
                names.append(name)
    return ", ".join(names) if names else "—"


def _classify_leave_span(leaves: list[Leave]) -> tuple[str, str, str, str, str]:
    """Return (leave_status, leave_type_label, leave_from, leave_to, sources)."""
    min_start = min(lv.start for lv in leaves)
    max_end = max(lv.end for lv in leaves)
    from_d = min_start.date()
    to_d = (max_end - timedelta(microseconds=1)).date()
    if to_d < from_d:
        to_d = from_d

    has_full = any(
        (lv.end - lv.start) >= timedelta(hours=20)
        or (lv.start.time() == datetime.min.time() and lv.end.time() == datetime.min.time())
        for lv in leaves
    )
    has_morning = any(lv.start.hour < 12 and lv.end.hour <= 13 and not ((lv.end - lv.start) >= timedelta(hours=20)) for lv in leaves)
    has_afternoon = any(lv.start.hour >= 12 and not ((lv.end - lv.start) >= timedelta(hours=20)) for lv in leaves)

    if has_full or (has_morning and has_afternoon) or to_d > from_d:
        leave_status = "AL Full Day"
        type_label = "Date range" if to_d > from_d else "Full day"
    elif has_morning:
        leave_status = "AL Morning"
        type_label = "Morning"
    elif has_afternoon:
        leave_status = "AL Afternoon"
        type_label = "Afternoon"
    else:
        leave_status = "AL Full Day"
        type_label = "Date range" if to_d > from_d else "Full day"

    sources = ", ".join(dict.fromkeys(str(lv.source or "Recorded AL").strip() for lv in leaves if str(lv.source or "").strip()))
    return leave_status, type_label, from_d.isoformat(), to_d.isoformat(), sources or "Recorded AL"


def _load_reference_maps(conn: Session, reg_filter: str | None, dlr_filter: str | None):
    groups_rows = conn.scalars(select(Group)).all()
    group_by_id: dict[int, Group] = {}
    group_by_code: dict[str, Group] = {}
    excluded_chats = set(EXCLUDED_CHAT_IDS)
    for g in groups_rows:
        group_by_id[int(g.id)] = g
        if _is_test_group(g.title, g.code):
            excluded_chats.add(int(g.id))
        elif g.active and g.code:
            group_by_code.setdefault(str(g.code).strip().upper(), g)

    ctrl_rows = conn.scalars(select(Controller)).all()
    ctrl_by_code: dict[str, list[str]] = defaultdict(list)
    all_controllers: set[str] = set()
    for c in ctrl_rows:
        label = str(c.name or c.username or c.uid or "").strip()
        code = str(c.code or "").strip().upper()
        if label and code and not _is_test_group("", code):
            if label not in ctrl_by_code[code]:
                ctrl_by_code[code].append(label)
            all_controllers.add(label)

    roster_rows = conn.scalars(select(Roster).where(Roster.active == 1)).all()
    roster_list: list[Roster] = []
    roster_by_emp: dict[str, Roster] = {}
    roster_by_uid: dict[str, Roster] = {}
    for r in roster_rows:
        pos = str(r.position or "SO").strip().upper()
        if pos not in ("SO", "SS"):
            continue
        r_reg = str(r.region or "").strip().upper()
        r_dlr = str(r.dealer or "").strip().upper()
        if reg_filter and r_reg != reg_filter:
            continue
        if dlr_filter and r_dlr != dlr_filter:
            continue
        roster_list.append(r)
        if r.employee:
            roster_by_emp[str(r.employee).strip()] = r
        uid_s = _uid_str(r.uid)
        if uid_s and uid_s not in roster_by_uid:
            roster_by_uid[uid_s] = r

    return group_by_id, group_by_code, excluded_chats, ctrl_by_code, all_controllers, roster_list, roster_by_emp, roster_by_uid


def build_performance_report(engine, start: date, end: date,
                             region: str | None = None, dealer: str | None = None) -> dict[str, Any]:
    """Compute Operations Overview & Performance Analyst series and tables."""
    start_s = start.isoformat()
    end_s = end.isoformat()
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
    reg_filter = region.strip().upper() if region else None
    dlr_filter = dealer.strip().upper() if dealer else None

    with Session(engine) as conn:
        (
            group_by_id,
            group_by_code,
            excluded_chats,
            ctrl_by_code,
            _,
            roster_list,
            roster_by_emp,
            roster_by_uid,
        ) = _load_reference_maps(conn, reg_filter, dlr_filter)

        visit_rows = conn.scalars(
            select(SilverVisit).where(
                SilverVisit.report_date >= start,
                SilverVisit.report_date <= end,
            )
        ).all()

        pin_rows = conn.scalars(
            select(Event).where(
                Event.kind == "pin",
                Event.at >= start_dt,
                Event.at < end_dt,
            )
        ).all()

        missing_rows = conn.scalars(
            select(Missing).where(
                Missing.start >= start_dt,
                Missing.start < end_dt,
                Missing.alert_mid.isnot(None),
            )
        ).all()

        leave_rows = conn.scalars(
            select(Leave).where(
                Leave.start < end_dt,
                Leave.end > start_dt,
            )
        ).all()

    def _person_key(emp_id: Any, uid: Any) -> str:
        e = str(emp_id or "").strip()
        if e and e in roster_by_emp:
            return f"emp:{e}"
        u = _uid_str(uid)
        if u and u in roster_by_uid:
            return f"emp:{roster_by_uid[u].employee}"
        if u:
            return f"uid:{u}"
        if e:
            return f"emp:{e}"
        return ""

    def _person_reg_dlr(pkey: str, fallback_reg: str = "", fallback_dlr: str = "", chat_id: int = 0) -> tuple[str, str]:
        emp_code = pkey[4:] if pkey.startswith("emp:") else ""
        r_obj = roster_by_emp.get(emp_code)
        if r_obj is None and pkey.startswith("uid:"):
            r_obj = roster_by_uid.get(pkey[4:])
        if r_obj:
            return str(r_obj.region or "").strip(), str(r_obj.dealer or "").strip()
        grp = group_by_id.get(chat_id)
        if grp and grp.code and "-" in str(grp.code):
            p1, p2 = str(grp.code).split("-", 1)
            return str(fallback_reg or p1).strip(), str(fallback_dlr or p2).strip()
        return str(fallback_reg or "").strip(), str(fallback_dlr or "").strip()

    # Collect visits from SilverVisit and live Event pins, reconciled per (pkey, day)
    silver_day_records: dict[tuple[str, date], list[tuple[str, str, float, int, str, str]]] = defaultdict(list)
    live_day_records: dict[tuple[str, date], list[tuple[str, str, float, int, str, str]]] = defaultdict(list)
    extra_people: dict[str, dict[str, Any]] = {}
    latest_chat_by_person: dict[str, int] = {}

    for v in visit_rows:
        pos = str(v.position or "SO").strip().upper()
        if pos not in ("SO", "SS"):
            continue
        chat_id = int(v.chat_id or 0)
        if chat_id in excluded_chats or _is_test_group(v.group_name):
            continue
        pkey = _person_key(v.employee_id, v.telegram_id)
        if not pkey or not v.report_date:
            continue
        reg, dlr = _person_reg_dlr(pkey, str(v.region or ""), str(v.dealer or ""), chat_id)
        if reg_filter and reg.upper() != reg_filter:
            continue
        if dlr_filter and dlr.upper() != dlr_filter:
            continue
        if pkey not in roster_by_emp and pkey not in extra_people and not pkey.startswith("emp:"):
            extra_people[pkey] = {
                "employee_id": str(v.employee_id or _uid_str(v.telegram_id) or ""),
                "telegram_id": int(v.telegram_id) if _uid_str(v.telegram_id) else None,
                "real_name": str(v.real_name or "").strip(),
                "position": pos,
                "region": reg,
                "dealer": dlr,
            }
        dist = str(v.actual_district or v.actual_district_kh or "").strip()
        comm = str(v.actual_commune_code or v.actual_commune or v.actual_commune_kh or "").strip()
        km = float(v.road_distance_km or 0.0)
        silver_day_records[(pkey, v.report_date)].append((dist, comm, km, chat_id, reg, dlr))
        if chat_id:
            latest_chat_by_person[pkey] = chat_id

    for ev in pin_rows:
        chat_id = int(ev.chat or 0)
        grp = group_by_id.get(chat_id)
        if chat_id in excluded_chats or (grp and _is_test_group(grp.title, grp.code)):
            continue
        if not _valid_geo(ev.data) or not ev.at:
            continue
        pjson = (ev.data or {}).get("person") or {}
        pos = str(pjson.get("position") or "SO").strip().upper()
        pkey = _person_key(pjson.get("employee"), ev.uid)
        if not pkey:
            continue
        emp_code = pkey[4:] if pkey.startswith("emp:") else ""
        if emp_code not in roster_by_emp and pos not in ("SO", "SS"):
            continue
        reg, dlr = _person_reg_dlr(pkey, str(pjson.get("region") or ""), str(pjson.get("dealer") or ""), chat_id)
        if reg_filter and reg.upper() != reg_filter:
            continue
        if dlr_filter and dlr.upper() != dlr_filter:
            continue
        if pkey not in roster_by_emp and pkey not in extra_people:
            extra_people[pkey] = {
                "employee_id": str(pjson.get("employee") or _uid_str(ev.uid) or ""),
                "telegram_id": int(ev.uid) if _uid_str(ev.uid) else None,
                "real_name": str(pjson.get("name") or f"Telegram {ev.uid}").strip(),
                "position": pos,
                "region": reg,
                "dealer": dlr,
            }
        geo = (ev.data or {}).get("geo") or {}
        route = (ev.data or {}).get("route") or {}
        dist = str(geo.get("district") or "").strip()
        comm = str(geo.get("commune_code") or geo.get("commune") or "").strip()
        try:
            km = float(route.get("distance_km") or 0.0)
        except (TypeError, ValueError):
            km = 0.0
        live_day_records[(pkey, ev.at.date())].append((dist, comm, km, chat_id, reg, dlr))
        if chat_id:
            latest_chat_by_person[pkey] = chat_id

    # Reconcile check-in metrics
    all_districts: set[str] = set()
    all_communes: set[str] = set()
    total_checkins = 0
    total_over_10km = 0
    active_sale_pkeys: set[str] = set()

    person_checkins: dict[str, int] = defaultdict(int)
    person_over_10km: dict[str, int] = defaultdict(int)
    person_districts: dict[str, set[str]] = defaultdict(set)
    person_communes: dict[str, set[str]] = defaultdict(set)

    reg_districts: dict[str, set[str]] = defaultdict(set)
    reg_communes: dict[str, set[str]] = defaultdict(set)
    reg_checkins: dict[str, int] = defaultdict(int)
    reg_over_10km: dict[str, int] = defaultdict(int)
    reg_active_sale: dict[str, set[str]] = defaultdict(set)

    dlr_districts: dict[tuple[str, str], set[str]] = defaultdict(set)
    dlr_communes: dict[tuple[str, str], set[str]] = defaultdict(set)
    dlr_checkins: dict[tuple[str, str], int] = defaultdict(int)
    dlr_over_10km: dict[tuple[str, str], int] = defaultdict(int)
    dlr_active_sale: dict[tuple[str, str], set[str]] = defaultdict(set)

    day_checkins: dict[date, int] = defaultdict(int)
    day_active_pkeys: dict[date, set[str]] = defaultdict(set)
    day_over_10km: dict[date, int] = defaultdict(int)
    checkin_cube: dict[tuple[str, str, str], int] = defaultdict(int)

    for key in set(silver_day_records.keys()) | set(live_day_records.keys()):
        pkey, d = key
        s_list = silver_day_records.get(key, [])
        l_list = live_day_records.get(key, [])
        chosen = l_list if len(l_list) > len(s_list) else s_list
        if not chosen:
            continue
        active_sale_pkeys.add(pkey)
        day_active_pkeys[d].add(pkey)
        for dist, comm, km, _, reg, dlr in chosen:
            total_checkins += 1
            day_checkins[d] += 1
            person_checkins[pkey] += 1
            r_up = reg.upper() or "—"
            d_up = dlr.upper() or "—"
            checkin_cube[(d.isoformat(), r_up, d_up)] += 1

            reg_checkins[r_up] += 1
            reg_active_sale[r_up].add(pkey)
            dlr_checkins[(r_up, d_up)] += 1
            dlr_active_sale[(r_up, d_up)].add(pkey)

            if dist:
                all_districts.add(dist)
                person_districts[pkey].add(dist)
                reg_districts[r_up].add(dist)
                dlr_districts[(r_up, d_up)].add(dist)
            if comm:
                all_communes.add(comm)
                person_communes[pkey].add(comm)
                reg_communes[r_up].add(comm)
                dlr_communes[(r_up, d_up)].add(comm)
            if km > 10:
                total_over_10km += 1
                day_over_10km[d] += 1
                person_over_10km[pkey] += 1
                reg_over_10km[r_up] += 1
                dlr_over_10km[(r_up, d_up)] += 1

    # Leaves for miss suppression + AL series
    leaves_by_uid: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    al_days_set_by_person: dict[str, set[date]] = defaultdict(set)
    al_pkeys_by_day: dict[date, set[str]] = defaultdict(set)
    al_cube_pkeys: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    reg_al_pkeys: dict[str, set[str]] = defaultdict(set)
    dlr_al_pkeys: dict[tuple[str, str], set[str]] = defaultdict(set)

    for lv in leave_rows:
        u = _uid_str(lv.uid)
        if u and lv.start and lv.end:
            leaves_by_uid[u].append((lv.start, lv.end))
        pkey = _person_key(lv.employee, lv.uid)
        if not pkey:
            continue
        reg, dlr = _person_reg_dlr(pkey, str(lv.region or ""), str(lv.dealer or ""), int(lv.chat or 0))
        if reg_filter and reg.upper() != reg_filter:
            continue
        if dlr_filter and dlr.upper() != dlr_filter:
            continue
        r_up = reg.upper() or "—"
        d_up = dlr.upper() or "—"
        lv_start_d = max(start, lv.start.date())
        lv_end_d = min(end, (lv.end - timedelta(microseconds=1)).date())
        if lv_end_d < lv_start_d:
            continue
        cur = lv_start_d
        while cur <= lv_end_d:
            al_days_set_by_person[pkey].add(cur)
            al_pkeys_by_day[cur].add(pkey)
            al_cube_pkeys[(cur.isoformat(), r_up, d_up)].add(pkey)
            reg_al_pkeys[r_up].add(pkey)
            dlr_al_pkeys[(r_up, d_up)].add(pkey)
            cur += timedelta(days=1)

    # Missing reports
    total_miss = 0
    person_misses: dict[str, int] = defaultdict(int)
    day_misses: dict[date, int] = defaultdict(int)
    miss_cube: dict[tuple[str, str, str], int] = defaultdict(int)
    reg_misses: dict[str, int] = defaultdict(int)
    dlr_misses: dict[tuple[str, str], int] = defaultdict(int)

    for m in missing_rows:
        chat_id = int(m.chat or 0)
        grp = group_by_id.get(chat_id)
        if chat_id in excluded_chats or (grp and _is_test_group(grp.title, grp.code)):
            continue
        u = _uid_str(m.uid)
        if u and any(ls < m.end and le > m.start for ls, le in leaves_by_uid.get(u, [])):
            continue
        pjson = m.person if isinstance(m.person, dict) else {}
        pkey = _person_key(pjson.get("employee"), m.uid)
        if not pkey:
            continue
        emp_code = pkey[4:] if pkey.startswith("emp:") else ""
        pos = str(pjson.get("position") or "SO").strip().upper()
        if emp_code not in roster_by_emp and pos not in ("SO", "SS"):
            continue
        reg, dlr = _person_reg_dlr(pkey, str(pjson.get("region") or ""), str(pjson.get("dealer") or ""), chat_id)
        if reg_filter and reg.upper() != reg_filter:
            continue
        if dlr_filter and dlr.upper() != dlr_filter:
            continue
        m_day = m.start.date()
        r_up = reg.upper() or "—"
        d_up = dlr.upper() or "—"
        total_miss += 1
        person_misses[pkey] += 1
        day_misses[m_day] += 1
        miss_cube[(m_day.isoformat(), r_up, d_up)] += 1
        reg_misses[r_up] += 1
        dlr_misses[(r_up, d_up)] += 1
        if chat_id and pkey not in latest_chat_by_person:
            latest_chat_by_person[pkey] = chat_id

    # Build roster-scoped region/dealer aggregations
    reg_roster_count: dict[str, int] = defaultdict(int)
    dlr_roster_count: dict[tuple[str, str], int] = defaultdict(int)
    all_regions: set[str] = set()
    all_dealers: set[tuple[str, str]] = set()
    for r in roster_list:
        r_up = str(r.region or "").strip().upper() or "—"
        d_up = str(r.dealer or "").strip().upper() or "—"
        reg_roster_count[r_up] += 1
        dlr_roster_count[(r_up, d_up)] += 1
        all_regions.add(r_up)
        all_dealers.add((r_up, d_up))
    for (_, r_up, d_up) in set(checkin_cube.keys()) | set(miss_cube.keys()) | set(al_cube_pkeys.keys()):
        if r_up and r_up != "—":
            all_regions.add(r_up)
        if r_up and d_up and r_up != "—" and d_up != "—":
            all_dealers.add((r_up, d_up))

    by_region = []
    for r_up in sorted(all_regions):
        rc = reg_roster_count.get(r_up, 0)
        act = len(reg_active_sale.get(r_up, set()))
        recorded_al = len(reg_al_pkeys.get(r_up, set()))
        by_region.append({
            "region": r_up,
            "roster_count": rc,
            "active_sale": act,
            "checkins": reg_checkins.get(r_up, 0),
            "miss_reports": reg_misses.get(r_up, 0),
            "districts": len(reg_districts.get(r_up, set())),
            "communes": len(reg_communes.get(r_up, set())),
            "over_10km": reg_over_10km.get(r_up, 0),
            "al_count": recorded_al if recorded_al else max(0, rc - act),
        })

    by_dealer = []
    for r_up, d_up in sorted(all_dealers):
        rc = dlr_roster_count.get((r_up, d_up), 0)
        act = len(dlr_active_sale.get((r_up, d_up), set()))
        recorded_al = len(dlr_al_pkeys.get((r_up, d_up), set()))
        by_dealer.append({
            "region": r_up,
            "dealer": d_up,
            "roster_count": rc,
            "active_sale": act,
            "checkins": dlr_checkins.get((r_up, d_up), 0),
            "miss_reports": dlr_misses.get((r_up, d_up), 0),
            "districts": len(dlr_districts.get((r_up, d_up), set())),
            "communes": len(dlr_communes.get((r_up, d_up), set())),
            "over_10km": dlr_over_10km.get((r_up, d_up), 0),
            "al_count": recorded_al if recorded_al else max(0, rc - act),
        })

    # Individual SO Summary
    ordered_pkeys = [f"emp:{r.employee}" for r in roster_list]
    known = set(ordered_pkeys)
    for extra_key in sorted(extra_people.keys()):
        if extra_key not in known:
            ordered_pkeys.append(extra_key)
            known.add(extra_key)

    individuals = []
    for pkey in ordered_pkeys:
        r_obj = roster_by_emp.get(pkey[4:]) if pkey.startswith("emp:") else None
        if r_obj is None and pkey.startswith("uid:"):
            r_obj = roster_by_uid.get(pkey[4:])
        meta = extra_people.get(pkey, {})

        emp_id = str(r_obj.employee or "") if r_obj else str(meta.get("employee_id") or "")
        uid_int = int(r_obj.uid) if (r_obj and r_obj.uid) else meta.get("telegram_id")
        name = str(r_obj.name or "") if r_obj else str(meta.get("real_name") or "—")
        pos = str(r_obj.position or "SO") if r_obj else str(meta.get("position") or "SO")
        reg = str(r_obj.region or "") if r_obj else str(meta.get("region") or "")
        dlr = str(r_obj.dealer or "") if r_obj else str(meta.get("dealer") or "")

        chat_id = latest_chat_by_person.get(pkey)
        grp = group_by_id.get(chat_id) if chat_id else None
        if grp is None and reg and dlr:
            grp = group_by_code.get(f"{reg}-{dlr}".upper())
        grp_name = (grp.title or grp.code) if grp and (grp.title or grp.code) else (
            f"{reg}-SO-{dlr}" if reg and dlr else "—"
        )
        ctrl = _resolve_controller_label(reg, dlr, ctrl_by_code)

        ci = person_checkins.get(pkey, 0)
        ms = person_misses.get(pkey, 0)
        ald = len(al_days_set_by_person.get(pkey, set()))
        attendance = "Present" if ci > 0 else ("AL" if ald > 0 else "No Check-in")

        individuals.append({
            "employee_id": emp_id,
            "telegram_id": uid_int,
            "real_name": name or "—",
            "position": pos,
            "region": reg,
            "dealer": dlr,
            "telegram_group": grp_name,
            "controller": ctrl,
            "checkins": ci,
            "miss_count": ms,
            "al_days": ald,
            "districts": len(person_districts.get(pkey, set())),
            "communes": len(person_communes.get(pkey, set())),
            "over_10km": person_over_10km.get(pkey, 0),
            "attendance": attendance,
        })

    individuals.sort(key=lambda x: (-x["checkins"], -x["miss_count"], x["real_name"].casefold(), str(x["employee_id"])))

    # Daily timeline
    timeline = []
    cur_d = start
    while cur_d <= end:
        al_cnt = len(al_pkeys_by_day.get(cur_d, set()))
        timeline.append({
            "date": cur_d.isoformat(),
            "label": cur_d.strftime("%b %d").replace(" 0", " "),
            "checkins": day_checkins.get(cur_d, 0),
            "miss_reports": day_misses.get(cur_d, 0),
            "al_count": al_cnt,
            "active_sale": len(day_active_pkeys.get(cur_d, set())),
            "over_10km": day_over_10km.get(cur_d, 0),
        })
        cur_d += timedelta(days=1)

    checkin_series = [
        {"date": d_s, "region": r_up, "dealer": d_up, "count": cnt}
        for (d_s, r_up, d_up), cnt in sorted(checkin_cube.items())
    ]
    miss_series = [
        {"date": d_s, "region": r_up, "dealer": d_up, "count": cnt}
        for (d_s, r_up, d_up), cnt in sorted(miss_cube.items())
    ]
    al_series = [
        {"date": d_s, "region": r_up, "dealer": d_up, "count": len(pkeys)}
        for (d_s, r_up, d_up), pkeys in sorted(al_cube_pkeys.items())
    ]

    total_al_days = sum(len(s) for s in al_days_set_by_person.values())
    headcount = len(roster_list)

    return {
        "start": start_s,
        "end": end_s,
        "totals": {
            "checkins": total_checkins,
            "active_sale": len(active_sale_pkeys),
            "districts": len(all_districts),
            "communes": len(all_communes),
            "over_10km": total_over_10km,
            "miss_reports": total_miss,
            "al_days": total_al_days,
        },
        "active_roster": headcount,
        "al_estimate": total_al_days if total_al_days else max(0, headcount - len(active_sale_pkeys)),
        "timeline": timeline,
        "checkin_series": checkin_series,
        "miss_series": miss_series,
        "al_series": al_series,
        "by_region": by_region,
        "by_dealer": by_dealer,
        "individuals": individuals,
    }


def build_al_report(engine, day: date, start: date | None = None, end: date | None = None,
                    region: str | None = None, dealer: str | None = None) -> dict[str, Any]:
    """Build Annual Leave view with controller, group, active & scheduled leave intervals."""
    win_start = start or day
    win_end = end or day
    if win_end < win_start:
        win_end = win_start
    win_start_dt = datetime.combine(win_start, datetime.min.time())
    win_end_dt = datetime.combine(win_end + timedelta(days=1), datetime.min.time())
    today = db.now().date()

    reg_filter = region.strip().upper() if region else None
    dlr_filter = dealer.strip().upper() if dealer else None

    with Session(engine) as conn:
        (
            group_by_id,
            group_by_code,
            excluded_chats,
            ctrl_by_code,
            all_controllers,
            roster_list,
            _,
            _,
        ) = _load_reference_maps(conn, reg_filter, dlr_filter)

        visit_rows = conn.scalars(
            select(SilverVisit).where(
                SilverVisit.report_date >= win_start,
                SilverVisit.report_date <= win_end,
            )
        ).all()

        recent_pins = conn.scalars(
            select(Event).where(
                Event.kind.in_(("pin", "form")),
                Event.at < win_end_dt,
            ).order_by(Event.at.desc(), Event.mid.desc())
        ).all()

        leave_rows = conn.scalars(
            select(Leave).where(Leave.end > win_start_dt).order_by(Leave.start.asc(), Leave.end.asc())
        ).all()

    checkin_uids: set[str] = set()
    checkin_emps: set[str] = set()
    latest_chat_by_uid: dict[str, int] = {}

    for v in visit_rows:
        chat_id = int(v.chat_id or 0)
        if chat_id in excluded_chats or _is_test_group(v.group_name):
            continue
        if v.employee_id:
            checkin_emps.add(str(v.employee_id).strip())
        u = _uid_str(v.telegram_id)
        if u:
            checkin_uids.add(u)
            if chat_id and u not in latest_chat_by_uid:
                latest_chat_by_uid[u] = chat_id

    for ev in recent_pins:
        chat_id = int(ev.chat or 0)
        grp = group_by_id.get(chat_id)
        if chat_id in excluded_chats or (grp and _is_test_group(grp.title, grp.code)):
            continue
        u = _uid_str(ev.uid)
        if not u:
            continue
        if chat_id and u not in latest_chat_by_uid:
            latest_chat_by_uid[u] = chat_id
        if ev.kind == "pin" and ev.at and ev.at >= win_start_dt and ev.at < win_end_dt and _valid_geo(ev.data):
            checkin_uids.add(u)

    leaves_by_emp: dict[str, list[Leave]] = defaultdict(list)
    leaves_by_uid: dict[str, list[Leave]] = defaultdict(list)
    for lv in leave_rows:
        if lv.employee:
            leaves_by_emp[str(lv.employee).strip()].append(lv)
        u = _uid_str(lv.uid)
        if u:
            leaves_by_uid[u].append(lv)

    items = []
    groups_seen: set[str] = set()
    for r in roster_list:
        emp_id = str(r.employee or "").strip()
        uid_s = _uid_str(r.uid)
        reg = str(r.region or "").strip()
        dlr = str(r.dealer or "").strip()

        chat_id = latest_chat_by_uid.get(uid_s) if uid_s else None
        grp = group_by_id.get(chat_id) if chat_id else None
        if grp is None and reg and dlr:
            grp = group_by_code.get(f"{reg}-{dlr}".upper())
        tg_group = (grp.code or grp.title) if grp and (grp.code or grp.title) else (
            f"{reg}-SO-{dlr}" if reg and dlr else "—"
        )
        if tg_group and tg_group != "—":
            groups_seen.add(tg_group)

        ctrl = _resolve_controller_label(reg, dlr, ctrl_by_code)

        # Combine employee + uid leave rows without duplicating by primary id
        person_leaves_map: dict[int, Leave] = {}
        for lv in leaves_by_emp.get(emp_id, []):
            person_leaves_map[int(lv.id)] = lv
        if uid_s:
            for lv in leaves_by_uid.get(uid_s, []):
                person_leaves_map[int(lv.id)] = lv
        person_leaves = sorted(person_leaves_map.values(), key=lambda x: (x.start, x.end))

        active_leaves = [lv for lv in person_leaves if lv.start < win_end_dt and lv.end > win_start_dt]
        future_leaves = [lv for lv in person_leaves if lv.start >= win_end_dt]

        has_checkin = (emp_id in checkin_emps) or (uid_s in checkin_uids)

        if active_leaves:
            leave_status, leave_type_label, leave_from, leave_to, sources = _classify_leave_span(active_leaves)
            status_kind = "on_leave"
            status_label = "On leave"
        elif future_leaves:
            # Group contiguous earliest future leave block
            block = [future_leaves[0]]
            block_end = future_leaves[0].end
            for nxt in future_leaves[1:]:
                if nxt.start <= block_end + timedelta(days=1):
                    block.append(nxt)
                    block_end = max(block_end, nxt.end)
                else:
                    break
            leave_status, leave_type_label, leave_from, leave_to, sources = _classify_leave_span(block)
            status_kind = "scheduled"
            status_label = "Scheduled"
        else:
            leave_status = ""
            leave_type_label = "—"
            leave_from = ""
            leave_to = ""
            sources = ""
            status_kind = "no_leave"
            status_label = "No leave"

        full_day_conflict = bool(status_kind == "on_leave" and leave_status == "AL Full Day" and has_checkin)
        if status_kind == "on_leave":
            attendance = leave_status if (full_day_conflict or not has_checkin) else f"Present + {leave_status}"
        elif has_checkin:
            attendance = "Present"
        else:
            attendance = "Scheduled" if win_start > today else "Not Reported"

        source_text = (sources or "Recorded AL") if status_kind != "no_leave" else (
            "Check-in activity" if has_checkin else
            ("Future date — not due" if win_start > today else "No check-in found")
        )

        items.append({
            "region": reg,
            "dealer": dlr,
            "employee_id": emp_id,
            "telegram_id": int(r.uid) if r.uid else None,
            "real_name": str(r.name or "").strip(),
            "position": str(r.position or "SO").strip(),
            "has_checkin": has_checkin,
            "leave_status": leave_status if status_kind == "on_leave" else "",
            "leave_type_label": leave_type_label,
            "leave_from": leave_from,
            "leave_to": leave_to,
            "status_kind": status_kind,
            "status_label": status_label,
            "full_day_conflict": full_day_conflict,
            "attendance": attendance,
            "source": source_text,
            "telegram_group": tg_group,
            "controller": ctrl,
        })

    items.sort(key=lambda x: (x["real_name"].casefold(), x["region"], x["dealer"], x["employee_id"]))

    return {
        "date": win_start.isoformat(),
        "start": win_start.isoformat(),
        "end": win_end.isoformat(),
        "definition": "AL comes from a saved Telegram/Morning Talk leave or dashboard correction. A future date without activity is Scheduled, not AL. Missing-report alerts are suppressed only by a saved leave interval covering that alert window.",
        "items": items,
        "al_count": sum(1 for x in items if x["status_kind"] == "on_leave"),
        "scheduled_count": sum(1 for x in items if x["status_kind"] == "scheduled"),
        "roster_total": len(items),
        "controllers": sorted(all_controllers, key=str.casefold),
        "groups": sorted(groups_seen, key=str.casefold),
    }


def cancel_employee_leave(session: Session, employee_id: str,
                          start: date | None = None, end: date | None = None,
                          actor: str = "admin") -> dict[str, Any] | None:
    """Cancel dashboard/recorded annual leave rows for an active SO/SS in the chosen window."""
    clean_id = str(employee_id or "").strip()
    person = session.scalar(select(Roster).where(Roster.employee == clean_id, Roster.active == 1))
    if person is None and clean_id.isdigit():
        person = session.scalar(select(Roster).where(Roster.uid == int(clean_id), Roster.active == 1))
    if person is None:
        return None

    today = db.now().date()
    win_start = start or today
    start_dt = datetime.combine(win_start, datetime.min.time())
    end_dt = datetime.combine((end or win_start) + timedelta(days=1), datetime.min.time()) if (start or end) else None

    query = select(Leave).where(Leave.employee == person.employee)
    if person.uid:
        query = select(Leave).where((Leave.employee == person.employee) | (Leave.uid == person.uid))
    if end_dt is not None:
        query = query.where(Leave.start < end_dt, Leave.end > start_dt)
    else:
        query = query.where(Leave.end > start_dt)

    rows = list(session.scalars(query).all())
    for row in rows:
        session.delete(row)

    # Clear dashboard AL override state markers in that range
    override_keys = session.scalars(select(State).where(State.key.like("dashboard_al_override:%"))).all()
    start_s = win_start.isoformat()
    end_s = (end or date(2099, 12, 31)).isoformat() if (start or end) else "2099-12-31"
    for st in override_keys:
        parts = str(st.key or "").split(":", 2)
        if len(parts) == 3 and parts[2] == person.employee and start_s <= parts[1] <= end_s:
            session.delete(st)

    return {
        "employee_id": str(person.employee),
        "cleared_rows": len(rows),
        "start": start_s,
        "end": end.isoformat() if end else None,
        "changed_by": actor,
    }
