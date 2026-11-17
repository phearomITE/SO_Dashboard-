"""Independent Team Talk Yes attendance and the requested Total - Active fallback."""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from .groups import DEALER_TO_REGION, EXPECTED_DEALERS
from .team_talk import TEAM_TALK_TRACK, load_dealer_roster, name_key, parse_team_talk


def reconcile_attendance(db, path, directory, start_date, end_date, leave,
                         region_headcounts, dealer_headcounts):
    roster = load_dealer_roster(path, directory)
    matches = defaultdict(dict)
    for (dealer, identity), member in roster.items():
        matches[(dealer, name_key(member.name))][identity] = member
    active = defaultdict(set)
    forms = set()
    unmatched = set()
    audit = []
    # An explicit full-day AL takes priority over Yes in another Talk.
    explicit_days = {(day, identity) for day, dealer, identity in leave}
    with db.connect() as con:
        sources = con.execute("""SELECT t.* FROM team_talk_reports t
            JOIN telegram_groups g ON g.group_id=t.group_id
            WHERE t.report_date BETWEEN ? AND ? AND g.is_active=1
            AND g.source_kind='EXECUTION' ORDER BY t.group_id,t.message_id""",
            (start_date, end_date)).fetchall()
    from .gp_scan_snapshot import scan_snapshot
    covered = {(r['Dealer'], r['Date']) for r in scan_snapshot(db, start_date, end_date)
               if r['Status'] in {'COMPLETE_DAY', 'AS_OF_NOW'}}
    for source in sources:
        talk = parse_team_talk(source['raw_text'], source['dealer'])
        if not talk:
            continue
        forms.add((talk.dealer, talk.report_date))
        for name in talk.yes_names:
            candidates = matches.get((talk.dealer, name_key(name)), {})
            member = next(iter(candidates.values())) if len(candidates) == 1 else None
            identity = next(iter(candidates)) if member else ''
            status = 'ACTIVE' if member and member.role in {'SO', 'SS'} else (
                'OTHER_ROLE' if member else 'AMBIGUOUS' if candidates else 'UNMATCHED')
            if not member:
                if re.search(r'(?i)\(\s*(?:ASS|SA|SP|ASM|HOS|assistant\s+sales?)\s*\)', name):
                    status = 'OTHER_ROLE'
            if member and (talk.report_date, identity) in explicit_days:
                status = 'EXPLICIT_AL_OVERRIDES_YES'
            if status == 'ACTIVE':
                active[(talk.dealer, talk.report_date)].add(identity)
            elif status in {'UNMATCHED', 'AMBIGUOUS'}:
                # Never turn an unresolved Yes into an inferred AL.
                unmatched.add((talk.dealer, talk.report_date))
            chat = str(source['group_id'])
            chat = chat[4:] if chat.startswith('-100') else chat.lstrip('-')
            audit.append({'Date': talk.report_date, 'Dealer': talk.dealer, 'Name': name,
                'Status': status, 'Employee ID': member.employee_id if member else '',
                'Telegram ID': member.telegram_id if member else '',
                'Role': member.role if member else '',
                'Source': f"https://t.me/c/{chat}/{source['message_id']}"})
    days = []
    day = date.fromisoformat(start_date)
    while day <= date.fromisoformat(end_date):
        days.append(day.isoformat())
        day += timedelta(days=1)

    def eligible(dealers, day):
        if any((d, day) not in covered for d in dealers):
            return 'NOT_SCANNED'
        if any((d, day) not in forms for d in dealers):
            return 'MISSING_TEAM_TALK'
        if any((d, day) in unmatched for d in dealers):
            return 'UNMATCHED_YES'
        return 'APPLIED'

    rows = []
    results = {'Region': {}, 'Dealer': {}}
    inferred_details = set()
    for scope, counts, groups in (
        ('Region', region_headcounts, EXPECTED_DEALERS),
        ('Dealer', dealer_headcounts, {d: [d] for d in DEALER_TO_REGION}),
    ):
        for key, dealers in groups.items():
            planned = counts.get(key)
            total = (planned.get('SO', 0) + planned.get('SS', 0)) if planned else None
            values = []
            candidate_details = set()
            for day in days:
                people = set().union(*(active[(d, day)] for d in dealers))
                status = eligible(dealers, day)
                if total is None:
                    status = 'MISSING_HEADCOUNT'
                elif len(people) > total:
                    status = 'ACTIVE_EXCEEDS_TOTAL'
                al = total - len(people) if status == 'APPLIED' else None
                rows.append({'Date': day, 'Scope': scope, 'Key': key, 'Total Sales': total,
                             'Active Yes': len(people), 'AL': al, 'Status': status})
                values.append(al)
                if scope == 'Dealer' and status == 'APPLIED':
                    roster_total = len({identity for (dealer, identity), member in roster.items()
                                        if dealer == key and member.role in {'SO', 'SS'}})
                    if roster_total != total:
                        # A verified override may identify a new employee without
                        # establishing which old master row they replaced.
                        continue
                    for (dealer, identity), member in roster.items():
                        if dealer == key and member.role in {'SO', 'SS'} and identity not in people:
                            if member.telegram_id.isdigit():
                                candidate_details.add((day, key, member.telegram_id))
            # A range uses average daily headcount only when every day is known.
            if values and all(value is not None for value in values):
                results[scope][key] = sum(values) / len(values)
                inferred_details.update(candidate_details)
    return results, inferred_details, rows, audit


def write_attendance_audits(output, rows, audit):
    active_field = 'Active Reports' if rows and 'Active Reports' in rows[0] else 'Active Yes'
    for suffix, values, fields in (
        ('Attendance_Reconciliation', rows, ['Date', 'Scope', 'Key', 'Total Sales', active_field, 'AL'] + (['Pending'] if rows and 'Pending' in rows[0] else []) + ['Status']),
        ('Attendance_Audit', audit, ['Date', 'Dealer', 'Name', 'Status', 'Employee ID', 'Telegram ID', 'Role', 'Source']),
    ):
        target = Path(output).with_name(Path(output).stem + '_' + suffix + '.csv')
        with target.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)
