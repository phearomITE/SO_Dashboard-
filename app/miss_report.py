"""Miss Report aggregation and admin reason override helpers for Dashboard SA parity."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import db
from app.db import Controller, Event, Group, Leave, Missing, Roster, SilverVisit, State


EXCLUDED_CHAT_IDS = {-1004303041907}


def _is_test_group(title: str | None, code: str | None = None) -> bool:
    norm_title = " ".join(str(title or "").strip().casefold().split())
    norm_code = str(code or "").strip().upper()
    return norm_title == "test_so" or norm_code == "R9" or norm_code.startswith("R9-")


def _uid_str(value: Any) -> str:
    try:
        iv = int(value)
        return str(iv) if iv > 0 else ""
    except (TypeError, ValueError):
        return ""


def _valid_geo(data: dict[str, Any] | None) -> bool:
    if not isinstance(data, dict):
        return False
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return False
    return 9.80 <= lat <= 14.75 and 102.30 <= lon <= 107.70


def _pick_reason_text(entry: dict[str, Any], language: str) -> str:
    msg = str(entry.get("message") or "").strip()
    km = str(entry.get("summary_km") or "").strip()
    en = str(entry.get("summary_en") or entry.get("summary") or "").strip()
    if language == "en":
        return en or msg or km
    if language == "both":
        primary = km or msg
        secondary = en or msg
        if primary and secondary and primary.casefold() != secondary.casefold():
            return f"{primary}\n{secondary}"
        return primary or secondary
    return km or msg or en


def _dedupe_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        for part in str(raw or "").splitlines():
            clean = " ".join(part.split()).strip()
            if not clean:
                continue
            key = clean.casefold()
            if key not in seen:
                seen.add(key)
                out.append(clean)
    return out


def _match_override(overrides: list[tuple[str, dict[str, Any]]],
                    emp_id: str, uid: str,
                    start_s: str, end_s: str) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_rank = (-1, "")
    for key, data in overrides:
        if not isinstance(data, dict):
            continue
        ov_emp = str(data.get("employee_id") or "").strip()
        ov_uid = _uid_str(data.get("telegram_id"))
        if not ((emp_id and ov_emp == emp_id) or (uid and ov_uid == uid) or key.endswith(f":{emp_id}")):
            continue
        ov_start = str(data.get("start") or "")
        ov_end = str(data.get("end") or "")
        if not ov_start or not ov_end or ov_end < start_s or ov_start > end_s:
            continue
        exact = 1 if (ov_start == start_s and ov_end == end_s) else 0
        updated_at = str(data.get("updated_at") or "")
        rank = (exact, updated_at)
        if rank > best_rank:
            best_rank = rank
            best = data
    return best


def miss_report_data(engine, start: date, end: date,
                     region: str | None = None, dealer: str | None = None,
                     controller: str | None = None, q: str = "",
                     language: str = "km") -> dict[str, Any]:
    """Aggregate SO roster, visits, missed alerts, written replies, and overrides."""
    start_s = start.isoformat()
    end_s = end.isoformat()
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
    reg_filter = region.strip().upper() if region else None
    dlr_filter = dealer.strip().upper() if dealer else None
    ctrl_filter = controller.strip().casefold() if controller else None
    search_q = " ".join(str(q or "").split()).strip().casefold()

    with Session(engine) as conn:
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

        if engine.dialect.name == "postgresql":
            reply_rows = [
                row["data"]
                for row in conn.execute(
                    text(
                        "SELECT data FROM so_states_v1 "
                        "WHERE key LIKE 'reason_review:%' "
                        "AND data->>'date' BETWEEN :start AND :end"
                    ),
                    {"start": start_s, "end": end_s},
                ).mappings().all()
            ]
        else:
            reply_rows = [
                s.data
                for s in conn.scalars(
                    select(State).where(State.key.like("reason_review:%"))
                ).all()
                if isinstance(s.data, dict)
                and start_s <= str(s.data.get("date") or "") <= end_s
            ]

        override_rows = [
            (str(s.key), s.data)
            for s in conn.scalars(
                select(State).where(State.key.like("miss_reason_override:%"))
            ).all()
            if isinstance(s.data, dict)
        ]

    # Map person keys to roster / synthetic identity keys
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

    # Count silver visits per (person_key, day)
    silver_by_person_day: dict[tuple[str, date], int] = defaultdict(int)
    latest_chat_by_person: dict[str, int] = {}
    for v in visit_rows:
        pos = str(v.position or "SO").strip().upper()
        if pos not in ("SO", "SS"):
            continue
        chat_id = int(v.chat_id or 0)
        if chat_id in excluded_chats or _is_test_group(v.group_name):
            continue
        pkey = _person_key(v.employee_id, v.telegram_id)
        if not pkey:
            continue
        if v.report_date:
            silver_by_person_day[(pkey, v.report_date)] += 1
        if chat_id and pkey not in latest_chat_by_person:
            latest_chat_by_person[pkey] = chat_id

    # Count live pins per (person_key, day)
    pins_by_person_day: dict[tuple[str, date], int] = defaultdict(int)
    for ev in pin_rows:
        chat_id = int(ev.chat or 0)
        grp = group_by_id.get(chat_id)
        if chat_id in excluded_chats or (grp and _is_test_group(grp.title, grp.code)):
            continue
        if not _valid_geo(ev.data):
            continue
        person_json = (ev.data or {}).get("person") or {}
        pos = str(person_json.get("position") or "SO").strip().upper()
        pkey = _person_key(person_json.get("employee"), ev.uid)
        if not pkey:
            continue
        if pkey in latest_chat_by_person or chat_id:
            latest_chat_by_person[pkey] = chat_id
        # Only enforce roster or SO/SS position when not in roster
        emp_code = pkey[4:] if pkey.startswith("emp:") else ""
        if emp_code not in roster_by_emp and pos not in ("SO", "SS"):
            continue
        if ev.at:
            pins_by_person_day[(pkey, ev.at.date())] += 1

    visits_by_person: dict[str, int] = defaultdict(int)
    for key in set(silver_by_person_day.keys()) | set(pins_by_person_day.keys()):
        pkey, _ = key
        visits_by_person[pkey] += max(silver_by_person_day[key], pins_by_person_day[key])

    # Build leave interval index by uid for excluding suppressed missing alerts
    leaves_by_uid: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for lv in leave_rows:
        u = _uid_str(lv.uid)
        if u and lv.start and lv.end:
            leaves_by_uid[u].append((lv.start, lv.end))

    misses_by_person: dict[str, int] = defaultdict(int)
    missing_reasons_by_person: dict[str, list[str]] = defaultdict(list)
    for m in missing_rows:
        chat_id = int(m.chat or 0)
        grp = group_by_id.get(chat_id)
        if chat_id in excluded_chats or (grp and _is_test_group(grp.title, grp.code)):
            continue
        u = _uid_str(m.uid)
        if u and any(ls < m.end and le > m.start for ls, le in leaves_by_uid.get(u, [])):
            continue
        person_json = m.person if isinstance(m.person, dict) else {}
        pkey = _person_key(person_json.get("employee"), m.uid)
        if not pkey:
            continue
        emp_code = pkey[4:] if pkey.startswith("emp:") else ""
        pos = str(person_json.get("position") or "SO").strip().upper()
        if emp_code not in roster_by_emp and pos not in ("SO", "SS"):
            continue
        if reg_filter:
            r_obj = roster_by_emp.get(emp_code)
            p_reg = str((r_obj.region if r_obj else person_json.get("region")) or "").strip().upper()
            if p_reg != reg_filter:
                continue
        if dlr_filter:
            r_obj = roster_by_emp.get(emp_code)
            p_dlr = str((r_obj.dealer if r_obj else person_json.get("dealer")) or "").strip().upper()
            if p_dlr != dlr_filter:
                continue
        misses_by_person[pkey] += 1
        if m.reason and str(m.reason).strip():
            missing_reasons_by_person[pkey].append(str(m.reason).strip())
        if chat_id and pkey not in latest_chat_by_person:
            latest_chat_by_person[pkey] = chat_id

    # Written reason_review entries grouped by person key
    reviews_by_person: dict[str, list[str]] = defaultdict(list)
    review_entry_counts: dict[str, int] = defaultdict(int)
    for entry in sorted(reply_rows, key=lambda x: str(x.get("recorded_at") or "")):
        if not isinstance(entry, dict):
            continue
        if entry.get("cleared_by_admin") is True or entry.get("is_reason") is False:
            continue
        if reg_filter and str(entry.get("region") or "").strip().upper() != reg_filter:
            continue
        if dlr_filter and str(entry.get("dealer") or "").strip().upper() != dlr_filter:
            continue
        pkey = _person_key(entry.get("employee_id"), entry.get("telegram_id"))
        if not pkey:
            continue
        txt = _pick_reason_text(entry, language)
        if txt:
            reviews_by_person[pkey].append(txt)
            review_entry_counts[pkey] += 1

    # Build consolidated SO items (roster first, plus any extra keys from activity if matching filters)
    ordered_pkeys: list[str] = [f"emp:{r.employee}" for r in roster_list]
    known_set = set(ordered_pkeys)
    for extra in sorted(set(misses_by_person.keys()) | set(reviews_by_person.keys())):
        if extra not in known_set:
            ordered_pkeys.append(extra)
            known_set.add(extra)

    items: list[dict[str, Any]] = []
    for pkey in ordered_pkeys:
        r = roster_by_emp.get(pkey[4:]) if pkey.startswith("emp:") else None
        if r is None and pkey.startswith("uid:"):
            r = roster_by_uid.get(pkey[4:])

        emp_id = str(r.employee or "").strip() if r else (pkey[4:] if pkey.startswith("emp:") else "")
        uid_s = _uid_str(r.uid) if r else (pkey[4:] if pkey.startswith("uid:") else "")
        name = str(r.name or "").strip() if r else ""
        if not name and uid_s:
            name = f"Telegram {uid_s}"
        reg = str(r.region or "").strip() if r else ""
        dlr = str(r.dealer or "").strip() if r else ""
        pos = str(r.position or "SO").strip() if r else "SO"

        chat_id = latest_chat_by_person.get(pkey)
        grp = group_by_id.get(chat_id) if chat_id else None
        if grp is None and reg and dlr:
            grp = group_by_code.get(f"{reg}-{dlr}".upper())
        group_name = (grp.title or grp.code) if grp and (grp.title or grp.code) else (
            f"{reg}-{dlr}" if reg and dlr else "—"
        )

        # Resolve controller label(s)
        c_names: list[str] = []
        for code_key in (f"{reg}-{dlr}".upper(), dlr.upper(), reg.upper()):
            for cname in ctrl_by_code.get(code_key, []):
                if cname not in c_names:
                    c_names.append(cname)
        ctrl_label = ", ".join(c_names) if c_names else "—"

        if ctrl_filter and ctrl_filter not in ctrl_label.casefold():
            continue

        total_visit = int(visits_by_person.get(pkey, 0))
        total_miss = int(misses_by_person.get(pkey, 0))

        override = _match_override(override_rows, emp_id or uid_s, uid_s, start_s, end_s)
        override_used = False
        if override is not None:
            if override.get("cleared"):
                written_lines: list[str] = []
            else:
                written_lines = _dedupe_lines([str(override.get("reason") or "")])
                override_used = bool(written_lines)
        else:
            raw_lines = reviews_by_person.get(pkey) or missing_reasons_by_person.get(pkey) or []
            written_lines = _dedupe_lines(raw_lines)

        has_written = bool(written_lines)
        entry_count = review_entry_counts.get(pkey, 0) or len(missing_reasons_by_person.get(pkey, []))
        if has_written:
            if override_used and total_miss > 0:
                written_count = total_miss
            elif total_miss > 0:
                written_count = min(total_miss, max(entry_count, 1))
            else:
                written_count = max(entry_count, len(written_lines))
        else:
            written_count = 0
        not_written_count = max(0, total_miss - written_count)

        miss_status = "Written" if has_written else ("Awaiting reason" if total_miss > 0 else "No miss")
        summary_reason = "\n".join(written_lines) if has_written else ("Awaiting reason" if total_miss > 0 else "—")

        item = {
            "employee_id": emp_id or uid_s,
            "telegram_id": int(uid_s) if uid_s.isdigit() else None,
            "name": name or "—",
            "position": pos,
            "region": reg or "—",
            "dealer": dlr or "—",
            "group": group_name,
            "controller": ctrl_label,
            "total_visit": total_visit,
            "total_miss": total_miss,
            "miss_report_status": miss_status,
            "has_written_reason": has_written,
            "written_reasons": written_lines,
            "written_count": written_count,
            "not_written_count": not_written_count,
            "summary_reason": summary_reason,
            "has_override": override_used,
        }
        if search_q:
            haystack = " ".join(
                str(item[k] or "")
                for k in ("name", "employee_id", "telegram_id", "region", "dealer", "group", "controller", "summary_reason")
            ).casefold()
            if search_q not in haystack:
                continue
        items.append(item)

    items.sort(key=lambda x: (-x["total_miss"], x["total_visit"], x["name"].casefold(), str(x["employee_id"])))

    summary = {
        "total_so": len(items),
        "so_miss_report": sum(1 for x in items if x["total_miss"] > 0),
        "total_visit": sum(x["total_visit"] for x in items),
        "total_miss": sum(x["total_miss"] for x in items),
        "written_reasons": sum(x["written_count"] for x in items),
        "not_written_reasons": sum(x["not_written_count"] for x in items),
        "written_so_count": sum(1 for x in items if x["has_written_reason"]),
        "not_written_so_count": sum(1 for x in items if x["not_written_count"] > 0),
    }

    return {
        "start": start_s,
        "end": end_s,
        "language": language,
        "controllers": sorted(all_controllers, key=str.casefold),
        "summary": summary,
        "items": items,
    }


def _find_roster_person(session, employee_id: str) -> Roster | None:
    clean = str(employee_id or "").strip()
    person = session.scalar(select(Roster).where(Roster.employee == clean, Roster.active == 1))
    if person is None and clean.isdigit():
        person = session.scalar(select(Roster).where(Roster.uid == int(clean), Roster.active == 1))
    return person


def save_miss_reason(session, employee_id: str, start: date, end: date,
                     reason: str, actor: str) -> dict[str, Any]:
    """Save an admin written reason override for an SO and update matching Missing rows."""
    clean_id = str(employee_id or "").strip()
    person = _find_roster_person(session, clean_id)
    target_emp = str(person.employee) if person else clean_id
    target_uid = int(person.uid) if person and person.uid else (int(clean_id) if clean_id.isdigit() else None)
    clean_reason = "\n".join(line.strip() for line in str(reason or "").splitlines() if line.strip())
    now_dt = db.now()

    key = f"miss_reason_override:{start.isoformat()}:{end.isoformat()}:{target_emp}"
    payload = {
        "employee_id": target_emp,
        "telegram_id": target_uid,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "reason": clean_reason,
        "cleared": not bool(clean_reason),
        "updated_by": actor,
        "updated_at": now_dt.isoformat(),
    }
    db.put(session, key, payload)

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
    if target_uid:
        matching_missing = session.scalars(
            select(Missing).where(
                Missing.uid == target_uid,
                Missing.start >= start_dt,
                Missing.start < end_dt,
            )
        ).all()
        for row in matching_missing:
            row.reason = clean_reason or None
            row.reason_at = now_dt if clean_reason else None

    return payload


def delete_miss_reason(session, employee_id: str, start: date, end: date,
                       actor: str) -> dict[str, Any]:
    """Clear written reasons and admin overrides for an SO across the selected range."""
    clean_id = str(employee_id or "").strip()
    person = _find_roster_person(session, clean_id)
    target_emp = str(person.employee) if person else clean_id
    target_uid = int(person.uid) if person and person.uid else (int(clean_id) if clean_id.isdigit() else None)
    start_s = start.isoformat()
    end_s = end.isoformat()
    now_dt = db.now()

    # Delete previous overlapping overrides for this SO, then write an explicit cleared tombstone
    all_overrides = session.scalars(select(State).where(State.key.like("miss_reason_override:%"))).all()
    for st in all_overrides:
        data = st.data if isinstance(st.data, dict) else {}
        ov_emp = str(data.get("employee_id") or "").strip()
        ov_uid = _uid_str(data.get("telegram_id"))
        if not (ov_emp == target_emp or (target_uid and ov_uid == str(target_uid))):
            continue
        ov_start = str(data.get("start") or "")
        ov_end = str(data.get("end") or "")
        if ov_start and ov_end and ov_end >= start_s and ov_start <= end_s:
            session.delete(st)

    session.flush()
    clear_key = f"miss_reason_override:{start_s}:{end_s}:{target_emp}"
    clear_payload = {
        "employee_id": target_emp,
        "telegram_id": target_uid,
        "start": start_s,
        "end": end_s,
        "reason": "",
        "cleared": True,
        "updated_by": actor,
        "updated_at": now_dt.isoformat(),
    }
    db.put(session, clear_key, clear_payload)

    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())
    if target_uid:
        matching_missing = session.scalars(
            select(Missing).where(
                Missing.uid == target_uid,
                Missing.start >= start_dt,
                Missing.start < end_dt,
            )
        ).all()
        for row in matching_missing:
            row.reason = None
            row.reason_at = None

    review_states = session.scalars(select(State).where(State.key.like("reason_review:%"))).all()
    for st in review_states:
        data = st.data if isinstance(st.data, dict) else None
        if not data:
            continue
        day_val = str(data.get("date") or "")
        if not (start_s <= day_val <= end_s):
            continue
        st_emp = str(data.get("employee_id") or "").strip()
        st_uid = _uid_str(data.get("telegram_id"))
        if st_emp == target_emp or (target_uid and st_uid == str(target_uid)):
            updated = dict(data)
            updated["cleared_by_admin"] = True
            updated["cleared_at"] = now_dt.isoformat()
            updated["cleared_by"] = actor
            st.data = updated
            flag_modified(st, "data")

    return clear_payload
