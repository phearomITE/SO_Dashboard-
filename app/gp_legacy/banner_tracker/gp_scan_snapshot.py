"""GP coverage: completed past days and explicitly labelled current-day snapshots."""
from __future__ import annotations

import csv
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ZONE = ZoneInfo('Asia/Phnom_Penh')


def local_now():
    return datetime.now(ZONE)


def scan_snapshot(db, start_date, end_date, *, now=None, track="TEAM_TALK_V2"):
    now = now or local_now()
    now = now.replace(tzinfo=ZONE) if now.tzinfo is None else now.astimezone(ZONE)
    start = datetime.fromisoformat(start_date).date()
    end = datetime.fromisoformat(end_date).date()
    if start > end:
        raise ValueError('Report start date must not be after end date.')
    with db.connect() as con:
        groups = list(con.execute("SELECT group_id,dealer FROM telegram_groups WHERE is_active=1 AND source_kind='EXECUTION' ORDER BY dealer"))
        stamps = {(r[0], r[1]): r[2] for r in con.execute(
            "SELECT group_id,report_date,completed_at FROM scrape_coverage WHERE track=? AND report_date BETWEEN ? AND ?", (track,start_date,end_date))}
    rows = []
    day = start
    while day <= end:
        boundary = datetime.combine(day, time.min, ZONE)
        for group in groups:
            stamp = stamps.get((group['group_id'], day.isoformat()))
            scanned = None
            try:
                scanned = datetime.fromisoformat(stamp) if stamp else None
                if scanned:
                    scanned = scanned.replace(tzinfo=timezone.utc) if scanned.tzinfo is None else scanned
                    scanned = scanned.astimezone(ZONE)
            except (ValueError, TypeError):
                pass
            status = 'NOT_SCANNED'
            if day > now.date():
                status = 'FUTURE_DATE'
            elif scanned and scanned > now + timedelta(minutes=5):
                status = 'INVALID_SCAN_TIMESTAMP'
            elif day == now.date():
                status = 'AS_OF_NOW' if scanned and scanned >= boundary else 'NOT_SCANNED_TODAY'
            elif scanned and scanned >= boundary + timedelta(days=1):
                status = 'COMPLETE_DAY'
            elif scanned:
                status = 'PARTIAL_PAST_DAY'
            rows.append({'Date': day.isoformat(), 'Dealer': group['dealer'], 'Group ID': group['group_id'],
                         'Status': status, 'Scanned At Local': scanned.isoformat() if scanned else '',
                         'Exported At Local': now.isoformat()})
        day += timedelta(days=1)
    return rows


def missing_snapshot_ranges(db, start_date, end_date, *, now=None):
    rows = scan_snapshot(db, start_date, end_date, now=now)
    available = {'AS_OF_NOW', 'COMPLETE_DAY'}
    missing = sorted({r['Date'] for r in rows if r['Status'] not in available})
    if not rows:
        return [(start_date,end_date)]
    ranges = []
    for day in missing:
        if ranges and datetime.fromisoformat(day).date() == datetime.fromisoformat(ranges[-1][1]).date() + timedelta(days=1):
            ranges[-1] = (ranges[-1][0],day)
        else:
            ranges.append((day,day))
    return ranges


def write_snapshot_audit(output, rows):
    target = Path(output).with_name(Path(output).stem + '_Scan_Coverage.csv')
    with target.open('w',encoding='utf-8-sig',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=['Date','Dealer','Group ID','Status','Scanned At Local','Exported At Local'])
        writer.writeheader()
        writer.writerows(rows)
    return target
