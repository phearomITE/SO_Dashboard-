"""Authoritative SO-list staffing and GP Total-minus-Active attendance."""
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
import re

from openpyxl import load_workbook

from .groups import DEALER_TO_REGION, EXPECTED_DEALERS
from .staff_directory import StaffMember, normalize_telegram_id
from .team_talk import name_key, parse_team_talk
from .gp_scan_snapshot import local_now


def load_so_roster(path, *, audit=None):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'GP SO master missing: {path}. Install SO_List.xlsx or set GP_SO_MASTER_PATH.')
    workbook = load_workbook(path, read_only=True, data_only=True)
    roster = {}
    identities = {}
    try:
        sheet = workbook['SO List'] if 'SO List' in workbook.sheetnames else workbook.active
        rows = sheet.iter_rows(values_only=True)
        headers = {str(v or '').strip().casefold(): i for i, v in enumerate(next(rows, ()))}
        required = {'region', 'dealer', 'employee id', 'telegram id', 'real name', 'position', 'active'}
        if not required <= headers.keys():
            raise ValueError(f'GP SO master missing columns: {sorted(required - headers.keys())}')
        for number, values in enumerate(rows, 2):
            def get(key):
                i = headers[key]
                return values[i] if i < len(values) else None
            if not any(value is not None and str(value).strip() for value in values):
                continue
            entry = {
                'Source Row': number, 'Region': str(get('region') or '').strip(),
                'Dealer': str(get('dealer') or '').strip(),
                'Employee ID': normalize_telegram_id(get('employee id')),
                'Telegram ID': normalize_telegram_id(get('telegram id')),
                'Real Name': str(get('real name') or '').strip(),
                'Position': str(get('position') or '').strip(), 'Active': get('active'),
                'Eligibility': 'ELIGIBLE',
            }
            if audit is not None:
                audit.append(entry)
            # Active=0 rows are historical entries, even if they repeat an active ID.
            flag = str(get('active') if get('active') is not None else '').strip().casefold()
            if flag in {'0', '0.0', 'false', 'no', 'inactive', ''}:
                entry['Eligibility'] = 'EXCLUDED_INACTIVE'
                continue
            if flag not in {'1', '1.0', 'true', 'yes', 'active'}:
                raise ValueError(f'GP SO master row {number}: invalid Active flag {flag!r}')
            role = str(get('position') or '').strip().upper()
            if role not in {'SO', 'SS'}:
                entry['Eligibility'] = 'EXCLUDED_ROLE'
                continue
            dealer = str(get('dealer') or '').strip().upper()
            region = str(get('region') or '').strip().upper()
            identity = normalize_telegram_id(get('telegram id'))
            name = str(get('real name') or '').strip()
            if dealer not in DEALER_TO_REGION or region not in EXPECTED_DEALERS or not identity.isdigit() or int(identity) <= 0 or not name:
                raise ValueError(f'GP SO master row {number}: invalid Region, Dealer, Telegram ID or Real Name')
            member = StaffMember(normalize_telegram_id(get('employee id')), name, role,
                                 identity, role, region, 'Active')
            previous = identities.get(identity)
            if previous and (previous.region, previous.role, name_key(previous.name)) != (region, role, name_key(name)):
                raise ValueError(f'GP SO master row {number}: conflicting active assignment for Telegram ID {identity}')
            identities[identity] = member
            if (dealer, identity) in roster:
                entry['Eligibility'] = 'DUPLICATE_ASSIGNMENT'
            roster[(dealer, identity)] = member
    finally:
        workbook.close()
    if not roster:
        raise ValueError('GP SO master contains no active SO/SS accounts')
    return roster


def headcounts(roster):
    regions = {r: {'SO': 0, 'SS': 0} for r in EXPECTED_DEALERS}
    dealers = {d: {'SO': 0, 'SS': 0} for d in DEALER_TO_REGION}
    seen = set()
    for (dealer, identity), member in roster.items():
        dealers[dealer][member.role] += 1
        if identity not in seen:
            regions[member.region][member.role] += 1
            seen.add(identity)
    return regions, dealers


def require_report_coverage(db, start_date, end_date, *, allow_current_day=False, selected_date_mode=False):
    """Require all dealer scans; selected-date mode uses data available at scan time."""
    from datetime import datetime, time, timezone
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('Asia/Phnom_Penh')
    now = local_now().astimezone(zone)
    day, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if day > end:
        raise ValueError('GP end date must be on or after start date')
    with db.connect() as con:
        groups = list(con.execute("SELECT group_id,dealer FROM telegram_groups WHERE is_active=1 AND source_kind='EXECUTION'"))
        scans = {(r[0], r[1]): r[2] for r in con.execute(
            "SELECT group_id,report_date,completed_at FROM scrape_coverage WHERE track='OUTLET_VISIT' AND report_date BETWEEN ? AND ?", (start_date,end_date))}
    missing = []
    if len(groups) != 65 or {g['dealer'] for g in groups} != set(DEALER_TO_REGION):
        raise RuntimeError('GP report-based AL requires all 65 Execution groups')
    while day <= end:
        if day > now.date():
            raise RuntimeError('GP cannot export a future reporting date')
        if day == now.date() and not (allow_current_day or selected_date_mode):
            raise RuntimeError('GP full-day AL is available only after the reporting day has ended in Cambodia. Enable the current-day snapshot option for an as-of report with Pending attendance.')
        boundary = datetime.combine(day if selected_date_mode or day == now.date() else day + timedelta(days=1), time.min, zone)
        for group in groups:
            stamp = scans.get((group['group_id'], day.isoformat()))
            try:
                scanned = datetime.fromisoformat(stamp) if stamp else None
                if scanned and scanned.tzinfo is None:
                    scanned = scanned.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                scanned = None
            if not scanned or not boundary <= scanned <= now + timedelta(minutes=5):
                missing.append((day.isoformat(), group['dealer']))
        day += timedelta(days=1)
    if missing:
        reason = 'GP selected-date scan coverage incomplete' if selected_date_mode else 'GP full-day AL cannot be inferred'
        raise RuntimeError(f'{reason}: {len(missing)} dealer-days need an Outlet Visit scan. Run scrape_outlet_visit for {start_date} through {end_date}.')


def reconcile_so_attendance(db, roster, start_date, end_date, *, allow_current_day=False, selected_date_mode=False):
    """Any authored message or location proves Active for that Cambodia date.

    Match immutable Telegram IDs to the SO roster. GPS, outlet counts, name
    spelling and Team Talk Yes/AL never determine daily attendance. Reporting
    in another Execution group still proves the employee worked that day.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    require_report_coverage(db, start_date, end_date, allow_current_day=allow_current_day, selected_date_mode=selected_date_mode)
    today = local_now().date().isoformat()
    zone = ZoneInfo('Asia/Phnom_Penh')
    first_report = {}
    with db.connect() as con:
        sources = attendance_sources(con)
    for source in sources:
        stamp = datetime.fromisoformat(source['submitted_at'])
        stamp = stamp.replace(tzinfo=zone) if stamp.tzinfo is None else stamp.astimezone(zone)
        day = stamp.date().isoformat()
        if not start_date <= day <= end_date:
            continue
        identity = normalize_telegram_id(source['sender_id'])
        first_report.setdefault((day, identity), source)
    days = []
    day = date.fromisoformat(start_date)
    while day <= date.fromisoformat(end_date):
        days.append(day.isoformat())
        day += timedelta(days=1)
    results = {'Region': {}, 'Dealer': {}}
    rows, audit, details = [], [], set()
    for day in days:
        for (dealer, identity), member in sorted(roster.items()):
            report = first_report.get((day, identity))
            source_link = ''
            if report:
                chat = str(report['group_id'])
                chat = chat[4:] if chat.startswith('-100') else chat.lstrip('-')
                source_link = f"https://t.me/c/{chat}/{report['message_id']}"
            elif selected_date_mode or day < today:
                details.add((day, dealer, identity))
            audit.append({'Date': day, 'Dealer': dealer, 'Name': member.name,
                          'Status': ('ACTIVE_REPORT' if report else 'AL_NO_REPORT_AT_SCAN' if selected_date_mode else 'PENDING_NO_REPORT_YET' if day == today else 'AL_NO_REPORT_FULL_DAY'),
                          'Employee ID': member.employee_id, 'Telegram ID': identity,
                          'Role': member.role, 'Source': source_link})
    scopes = [('Dealer', d, {(dealer, i) for dealer, i in roster if dealer == d}) for d in DEALER_TO_REGION]
    scopes += [('Region', r, {key for key, member in roster.items() if member.region == r}) for r in EXPECTED_DEALERS]
    for scope, key, assignments in scopes:
        total = len({i for d, i in assignments})
        values = []
        for day in days:
            people = {i for d, i in assignments if (day, i) in first_report}
            al = total - len(people) if selected_date_mode else None if day == today else total - len(people)
            pending = total - len(people) if day == today and not selected_date_mode else 0
            rows.append({'Date': day, 'Scope': scope, 'Key': key, 'Total Sales': total,
                         'Active Reports': len(people), 'AL': al, 'Pending': pending,
                         'Status': 'APPLIED_SELECTED_DATE' if selected_date_mode else 'APPLIED_SNAPSHOT' if day == today else 'APPLIED_REPORT_SENDERS'})
            values.append(al)
        results[scope][key] = sum(values) / len(values) if all(v is not None for v in values) else None
    return results, details, rows, audit


def report_local_day(value):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    zone = ZoneInfo('Asia/Phnom_Penh')
    stamp = datetime.fromisoformat(str(value))
    return (stamp.replace(tzinfo=zone) if stamp.tzinfo is None else stamp.astimezone(zone)).date().isoformat()


def append_attendance_records(pin_rows, image_links, roster, audit):
    """One distinct roster assignment/day, with no fabricated visit or GPS."""
    from datetime import datetime
    for item in audit:
        member = roster[(item['Dealer'], item['Telegram ID'])]
        pin_rows.append([
            datetime.fromisoformat(item['Date']), member.region, item['Dealer'],
            member.name, member.telegram_id, None, None, None, None,
            None, None, None, None, None, None,
            attendance_label(item['Status']),
            'Attendance', member.employee_id,
        ])
        image_links.append('')


def validate_pin_attendance(pin_rows, roster, reconciliation, start_date, end_date):
    """Fail export if any roster identity/day is missing or totals disagree."""
    days = []
    day = date.fromisoformat(start_date)
    while day <= date.fromisoformat(end_date):
        days.append(day.isoformat()); day += timedelta(days=1)
    wanted = {(d, dealer, i) for d in days for dealer, i in roster}
    actual = {}
    for row in pin_rows:
        if row[16] != 'Attendance':
            if row[15] == 'AL':
                raise RuntimeError('A submitted outlet report must never be labelled AL')
            continue
        key = (row[0].date().isoformat(), row[2], row[4])
        if key in actual or key not in wanted or row[15] not in {'Active', 'AL', 'Pending'}:
            raise RuntimeError(f'Invalid or duplicate GP attendance record: {key}')
        if any(row[c] is not None for c in (5, 6, 7, 8, 9, 10, 11, 12, 13, 14)):
            raise RuntimeError('Attendance-only rows must not fabricate outlet data')
        actual[key] = row
    if set(actual) != wanted:
        raise RuntimeError('Pin Detail does not contain every roster assignment/day')
    for item in reconciliation:
        candidates = {i: row for (d, dealer, i), row in actual.items()
                      if d == item['Date'] and
                      (dealer == item['Key'] if item['Scope'] == 'Dealer' else row[1] == item['Key'])}
        active = sum(row[15] == 'Active' for row in candidates.values())
        al = sum(row[15] == 'AL' for row in candidates.values())
        pending = sum(row[15] == 'Pending' for row in candidates.values())
        if (len(candidates), active, al, pending) != (item['Total Sales'], item['Active Reports'], item['AL'] or 0, item.get('Pending', 0)):
            raise RuntimeError(f'Pin Detail attendance does not reconcile: {item}')


def write_roster_audit(output, rows):
    import csv
    target = Path(output).with_name(Path(output).stem + '_SO_Roster_Audit.csv')
    with target.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['Source Row', 'Region', 'Dealer', 'Employee ID',
            'Telegram ID', 'Real Name', 'Position', 'Active', 'Eligibility'])
        writer.writeheader(); writer.writerows(rows)
    return target


def attendance_label(status):
    return ('Active' if status == 'ACTIVE_REPORT' else
            'Pending' if status == 'PENDING_NO_REPORT_YET' else 'AL')


def attendance_sources(con):
    """Activity plus legacy forms/pins, always restricted to Execution groups."""
    return con.execute("""SELECT a.* FROM (
        SELECT group_id,message_id,submitted_at,sender_id,sender_name,sender_username,'REPORT' kind FROM outlet_visits
        UNION ALL SELECT group_id,message_id,sent_at,sender_id,'','','LOCATION' FROM locations
        UNION ALL SELECT group_id,message_id,submitted_at,sender_id,sender_name,sender_username,kind FROM gp_sender_activity
    ) a JOIN telegram_groups g ON g.group_id=a.group_id
    WHERE g.is_active=1 AND g.source_kind='EXECUTION' AND a.sender_id>0
    ORDER BY a.submitted_at,a.group_id,a.message_id""").fetchall()


def sync_so_list(db, path, start_date=None, end_date=None):
    """Append missing report/pin senders by ID; preserve every existing row.

    Membership and casual chat alone never enroll people as sales staff.
    Status is enrollment provenance, not a multi-day attendance value.
    """
    from copy import copy
    import os, tempfile, shutil
    path = Path(path)
    with db.connect() as con:
        sources = con.execute("""SELECT a.*,g.region,g.dealer FROM (
            SELECT group_id,message_id,submitted_at,sender_id,sender_name,sender_username,'REPORT' kind FROM outlet_visits
            UNION ALL SELECT group_id,message_id,sent_at,sender_id,'','','LOCATION' FROM locations
            UNION ALL SELECT group_id,message_id,submitted_at,sender_id,sender_name,sender_username,kind FROM gp_sender_activity WHERE kind IN ('REPORT','LOCATION')
        ) a JOIN telegram_groups g ON g.group_id=a.group_id
        WHERE g.is_active=1 AND g.source_kind='EXECUTION' AND a.sender_id>0
        ORDER BY a.submitted_at,a.group_id,a.message_id""").fetchall()
        profiles = {str(x['telegram_id']): (x['telegram_name'],x['username']) for x in con.execute(
            'SELECT * FROM telegram_group_members ORDER BY captured_at')}
    w = load_workbook(path)
    temp = None
    try:
        sheet = w['SO List'] if 'SO List' in w.sheetnames else w.active
        headers = {str(c.value or '').strip().casefold(): c.column for c in sheet[1]}
        required = {'region','dealer','employee id','telegram id','real name','position','active'}
        if not required <= headers.keys():
            raise ValueError('SO List is missing required headers: '+str(sorted(required-headers.keys())))
        changed = False
        for name in ['Telegram Name','Status']:
            if name.casefold() not in headers:
                col = sheet.max_column+1
                sheet.cell(1,col,name)._style = copy(sheet.cell(1,col-1)._style)
                headers[name.casefold()] = col; changed = True
        existing = {}
        for row in range(2,sheet.max_row+1):
            identity = normalize_telegram_id(sheet.cell(row,headers['telegram id']).value)
            existing.setdefault(identity,[]).append(row)
        added = 0
        for source in sources:
            day = report_local_day(source['submitted_at'])
            if (start_date and day < start_date) or (end_date and day > end_date):
                continue
            identity = normalize_telegram_id(source['sender_id'])
            name,user = profiles.get(identity, ('',''))
            name = source['sender_name'] or name
            user = source['sender_username'] or user
            if identity in existing:
                if name:
                    for row in existing[identity]:
                        cell = sheet.cell(row,headers['telegram name'])
                        if cell.value != name: cell.value = name; changed = True
                continue
            # Need a resolved human profile; pins without a profile wait for refresh.
            if not name or source['dealer'] not in DEALER_TO_REGION:
                continue
            row = sheet.max_row+1
            values = {'region':source['region'],'dealer':source['dealer'],
                'employee id':'','telegram id':identity,'telegram name':name,
                'real name':name,'username':user,'position':'SO','active':1,
                'status':'Auto-added'}
            for key,col in headers.items():
                cell = sheet.cell(row,col)
                if row>2: cell._style = copy(sheet.cell(row-1,col)._style)
                if key in values: cell.value = values[key]
            existing[identity] = [row];added += 1;changed = True
        if changed:
            from openpyxl.utils import get_column_letter
            ref=f'A1:{get_column_letter(sheet.max_column)}{sheet.max_row}'
            if sheet.auto_filter.ref: sheet.auto_filter.ref=ref
            for table in sheet.tables.values():
                # Retain existing table columns, expanding rows only.
                from openpyxl.utils.cell import range_boundaries
                left,top,right,_=range_boundaries(table.ref)
                table.ref=f'{get_column_letter(left)}{top}:{get_column_letter(right)}{sheet.max_row}'
                if table.autoFilter: table.autoFilter.ref=table.ref
            backup = db.path.parent/'so_list_backups';backup.mkdir(parents=True,exist_ok=True)
            import hashlib
            digest=hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            target=backup/(path.stem+'-'+digest+path.suffix)
            if not target.exists(): shutil.copy2(path,target)
            fd,temp = tempfile.mkstemp(dir=path.parent,suffix='.xlsx');os.close(fd)
            w.save(temp)
            try:
                os.replace(temp,path)
            except PermissionError as exc:
                raise PermissionError(f'Close {path.name} in Excel, then rerun. SO List was not replaced.') from exc
            temp=None
        return {'added':added,'updated':changed}
    finally:
        w.close()
        if temp and Path(temp).exists(): Path(temp).unlink()
