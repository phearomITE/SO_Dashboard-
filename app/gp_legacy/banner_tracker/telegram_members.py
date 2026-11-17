"""Current Telegram group membership snapshots for the GP directory sheet."""
from __future__ import annotations

import asyncio
import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill

SHEET_NAME = 'All_Sale_From_Telegram'
HEADERS = ['No', 'Date', 'Region', 'Dealer', 'Telegram ID', 'Telegram Name', 'Username', 'Telegram Group']


def save_member_snapshot(db, group_id, users, captured_at, *, status, reported_total=None, detail=''):
    people = {}
    for user in users:
        identity = int(getattr(user, 'id', 0) or 0)
        if identity <= 0:
            continue
        name = ' '.join(filter(None, [str(getattr(user,'first_name','') or '').strip(),
                                     str(getattr(user,'last_name','') or '').strip()]))
        name = name or ('Deleted Account' if getattr(user,'deleted',False) else 'Unnamed Account')
        username = str(getattr(user,'username','') or '').strip().lstrip('@')
        people[identity] = (group_id,identity,name,'@'+username if username else '',captured_at)
    with db.connect() as con:
        previous = con.execute('SELECT last_success_at FROM telegram_member_scans WHERE group_id=?',(group_id,)).fetchone()
        last_success = previous[0] if previous else None
        replace = status == 'COMPLETE' or (status == 'VISIBLE_ONLY' and not last_success)
        if replace:
            con.execute('DELETE FROM telegram_group_members WHERE group_id=?',(group_id,))
        if status == 'COMPLETE':
            last_success = captured_at
        # On first partial fetch show only what Telegram returned and flag it.
        # Later failed/partial fetches retain the last complete snapshot.
        if replace:
            con.executemany('''INSERT INTO telegram_group_members VALUES (?,?,?,?,?)
                ON CONFLICT(group_id,telegram_id) DO UPDATE SET
                telegram_name=excluded.telegram_name,username=excluded.username,captured_at=excluded.captured_at''', people.values())
        con.execute('''INSERT INTO telegram_member_scans VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(group_id) DO UPDATE SET attempted_at=excluded.attempted_at,
            status=excluded.status,retrieved_count=excluded.retrieved_count,
            reported_total=excluded.reported_total,detail=excluded.detail,
            last_success_at=excluded.last_success_at''',
            (group_id,captured_at,status,len(people),reported_total,detail,last_success))
    return len(people)


async def refresh_members(tracker):
    groups = [dict(row) for row in tracker.db.groups()]
    result = {'groups':len(groups),'complete_groups':0,'members':0,'issues':0}
    for group in groups:
        users = []
        reported = None
        status = 'ERROR'
        detail = ''
        for attempt in range(3):
            try:
                users = await tracker.client.get_participants(int(group['group_id']), limit=None)
                reported = getattr(users,'total',None)
                if reported is not None:
                    reported = int(reported)
                unique = {int(u.id) for u in users if int(getattr(u,'id',0) or 0)>0}
                status = 'COMPLETE' if reported is not None and len(unique)==reported else 'VISIBLE_ONLY'
                detail = ''
                if status != 'COMPLETE':
                    detail = f'Telegram returned {len(unique)} unique accounts; reported total={reported}. Membership may be restricted or changed during refresh.'
                break
            except Exception as exc:
                detail = f'{type(exc).__name__}: {exc}'
                transient = isinstance(exc,(OSError,asyncio.TimeoutError)) or type(exc).__name__ in {
                    'RpcCallFailError','ServerError','TimedOutError','FloodWaitError',
                }
                delay = int(getattr(exc,'seconds',2**attempt))
                if not transient or attempt==2 or delay>60:
                    break
                await asyncio.sleep(max(1,delay))
        stamp = datetime.now(ZoneInfo(tracker.settings.timezone)).isoformat()
        count = save_member_snapshot(tracker.db,int(group['group_id']),users,stamp,
                                     status=status,reported_total=reported,detail=detail)
        result['members'] += count
        if status == 'COMPLETE':
            result['complete_groups'] += 1
        else:
            result['issues'] += 1
        print(f"MEMBERS {group['region']}-{group['dealer']}: {count} accounts, {status}")
    return result


def add_members_sheet(workbook, db, output):
    sheet = workbook.create_sheet(SHEET_NAME)
    sheet.append(HEADERS)
    with db.connect() as con:
        people = con.execute('''SELECT m.*,g.region,g.dealer,g.group_name FROM telegram_group_members m
            JOIN telegram_groups g ON g.group_id=m.group_id
            WHERE g.is_active=1 AND g.source_kind='EXECUTION'
            ORDER BY g.region,g.dealer,lower(m.telegram_name),m.telegram_id''').fetchall()
        audit = [dict(row) for row in con.execute('''SELECT g.region,g.dealer,g.group_name,g.group_id,
            s.attempted_at,COALESCE(s.status,'NOT_REFRESHED') AS status,
            s.retrieved_count,s.reported_total,s.last_success_at,COALESCE(s.detail,'') AS detail
            FROM telegram_groups g LEFT JOIN telegram_member_scans s ON s.group_id=g.group_id
            WHERE g.is_active=1 AND g.source_kind='EXECUTION' ORDER BY g.region,g.dealer''')]
    for index, person in enumerate(people,1):
        captured = datetime.fromisoformat(person['captured_at']).date()
        sheet.append([index,captured,person['region'],person['dealer'],str(person['telegram_id']),
                      person['telegram_name'],person['username'],person['group_name']])
        sheet.cell(index+1,2).number_format='dd/mm/yyyy'
        sheet.cell(index+1,5).number_format='@'
        for column in (5,6,7,8):
            sheet.cell(index+1,column).data_type='s'  # Names must never become Excel formulas.
        chat = str(person['group_id'])
        chat = chat[4:] if chat.startswith('-100') else chat.lstrip('-')
        if str(person['group_id']).startswith('-100'):
            sheet.cell(index+1,8).hyperlink=f'https://t.me/c/{chat}'
            sheet.cell(index+1,8).style='Hyperlink'
    sheet.freeze_panes='A2'
    sheet.auto_filter.ref=f'A1:H{max(1,sheet.max_row)}'
    sheet.sheet_view.showGridLines=True
    sheet.row_dimensions[1].height=25
    for cell in sheet[1]:
        cell.fill=PatternFill('solid',fgColor='1B395D')
        cell.font=Font(name='Calibri',size=11,color='FFFFFF',bold=True)
        cell.alignment=Alignment(horizontal='left',vertical='center')
    for column,width in zip('ABCDEFGH',[8,14,12,12,21,30,26,42]):
        sheet.column_dimensions[column].width=width
    issues = [row for row in audit if row['status']!='COMPLETE']
    sheet['A1'].comment=Comment(
        'Current visible Telegram membership, one account per dealer group. Date is the membership capture date, '
        'not historical membership on the GP report date. Accounts may include admins/bots; this directory does not change SO/SS targets or AL. '
        f'{len(people)} rows; {len(issues)} groups need membership review. '
        'A failed refresh retains its last complete snapshot. See the companion Member_Refresh_Audit.csv. '
        'Refresh via the notebook or python main.py refresh_gp_members.', 'Banner Tracker')
    target=Path(output).with_name(Path(output).stem+'_Member_Refresh_Audit.csv')
    fields=['region','dealer','group_name','group_id','attempted_at','status','retrieved_count','reported_total','last_success_at','detail']
    with target.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(audit)
    print(f'{SHEET_NAME}: {len(people)} membership rows; {len(issues)} groups need refresh/review. Audit: {target}')
