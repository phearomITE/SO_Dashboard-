"""Read-only coverage diagnostics. A scan is a snapshot, not proof of completeness."""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def audit_outlet_coverage(db, start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError('--from must not be after --to')
    zone = ZoneInfo('Asia/Phnom_Penh')
    result = []
    with db.connect() as con:
        groups = list(con.execute("SELECT group_id,dealer FROM telegram_groups WHERE source_kind='EXECUTION' AND is_active=1 ORDER BY dealer"))
        coverage = {(r[0], r[1]): r[2] for r in con.execute(
            "SELECT group_id,report_date,completed_at FROM scrape_coverage WHERE track='OUTLET_VISIT' AND report_date BETWEEN ? AND ?", (start,end))}
        counts = {(r[0],r[1]): (r[2],r[3]) for r in con.execute(
            "SELECT group_id,substr(submitted_at,1,10),count(*),sum(latitude IS NULL OR longitude IS NULL) FROM outlet_visits WHERE substr(submitted_at,1,10) BETWEEN ? AND ? GROUP BY group_id,substr(submitted_at,1,10)", (start,end))}
    current = first
    while current <= last:
        day = current.isoformat()
        boundary = datetime.combine(current+timedelta(days=1), time.min, zone)
        for gid, dealer in groups:
            stamp = coverage.get((gid,day))
            try:
                scanned = datetime.fromisoformat(stamp).astimezone(zone) if stamp else None
            except (ValueError, TypeError):
                scanned = None
            status = 'NOT_SCANNED' if scanned is None else ('PARTIAL_DAY' if scanned < boundary else 'SCANNED_AFTER_DAY_END')
            reports, missing = counts.get((gid,day),(0,0))
            result.append({'dealer':dealer,'date':day,'scan_status':status,
                           'last_scan_local':scanned.isoformat() if scanned else None,
                           'saved_reports':reports,'missing_gps':missing})
        current += timedelta(days=1)
    return result


def warn_outlet_coverage(db, start, end):
    rows = audit_outlet_coverage(db,start,end)
    pending = [r for r in rows if r['scan_status'] != 'SCANNED_AFTER_DAY_END']
    if pending:
        dealers = ', '.join(sorted({r['dealer'] for r in pending}))
        print(f'WARNING: {len(pending)} group-days have no full-day scan. Dealers: {dealers}')
        print(f'Refresh Telegram first: python main.py scrape_outlet_visit --from {start} --to {end}')
        print('Export uses saved data only; partial-day scans may miss later reports.')
    if not rows:
        print('WARNING: No active Execution groups registered; coverage cannot be verified.')
