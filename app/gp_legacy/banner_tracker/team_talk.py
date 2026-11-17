"""Team Talk declarations, scoped to the written date and active dealer roster."""
from __future__ import annotations

import re
import csv
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from .groups import DEALER_TO_REGION
from .staff_directory import StaffMember, normalize_sales_role, normalize_telegram_id, staff_is_active

TEAM_TALK_TRACK = "TEAM_TALK_V2"


def clean_text(text):
    return unicodedata.normalize("NFKC", str(text)).replace("**", "").replace("\u200b", "")


def name_key(text):
    text = re.sub(r"\([^)]*\)", " ", clean_text(text))
    return "".join(c for c in text.casefold() if c.isalnum())


@dataclass(frozen=True)
class TeamTalk:
    report_date: str
    dealer: str
    names: tuple[str, ...]
    yes_names: tuple[str, ...] = ()


def parse_team_talk(raw_text, group_dealer):
    text = clean_text(raw_text)
    if not re.search(r"\b(?:morning|evening|team)\s+talk\b", text, re.I):
        return None
    match = re.search(r"\bdate\s*[:=]?\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b", text, re.I)
    if not match:
        return None
    try:
        report_date = datetime(int(match[3]), int(match[2]), int(match[1])).date().isoformat()
    except ValueError:
        return None
    dealer = str(group_dealer).strip().upper()
    explicit = re.search(r"(?im)^\s*([A-Z]{2,5}\d+)\s*[:(]", text)
    if explicit and explicit[1].upper() != dealer:
        return None
    # Only the attendance section can declare leave; activity prose cannot.
    section = re.search(r"(?im)^\s*\*?\s*(?:attain|attendent|attendant|attendance|attend|attained)\b[^\n]*\n", text)
    if not section:
        return None
    names = []
    yes_names = []
    pending = ""
    for line in text[section.end():].splitlines():
        line = line.strip().strip("*").strip()
        if re.search(r"(?i)^(?:\d+[.)]\s*)?(?:comp[ei]titor|focus|banner\s*:|poster\s*:|posm\b|thanks\b)", line):
            break
        if not line:
            continue
        numbered = re.match(r"^\d+\s*[.)]\s*(.*)$", line)
        if numbered:
            pending = numbered[1].strip()
        elif pending and re.fullmatch(r"(?i)\(?\s*(?:al|yes)\s*\)?[.!,:;\s]*", line):
            pending += " " + line
        else:
            pending = line
        status = re.search(r"(?i)(?<!\w)\(?\s*(al|yes)\s*\)?[.!,:;\s]*$", pending)
        if status:
            name = pending[:status.start()].strip(" :;,-")
            if name_key(name):
                (names if status[1].casefold() == "al" else yes_names).append(name)
            pending = ""
    return TeamTalk(report_date, dealer, tuple(names), tuple(yes_names))


def capture_team_talk(db, group, message_id, submitted_at, raw_text):
    talk = parse_team_talk(raw_text, group["dealer"])
    with db.connect() as con:
        if not talk and not con.execute(
            "SELECT 1 FROM team_talk_reports WHERE group_id=? AND message_id=?",
            (group["group_id"], message_id),
        ).fetchone():
            return False
        # Edits which remove AL or invalidate a form must remove stale leave.
        con.execute("DELETE FROM team_talk_reports WHERE group_id=? AND message_id=?", (group["group_id"], message_id))
        if talk:
            con.execute("INSERT INTO team_talk_reports VALUES (?,?,?,?,?,?)", (
                group["group_id"], message_id, submitted_at.isoformat(), talk.report_date, talk.dealer, raw_text,
            ))
    return talk is not None


def load_dealer_roster(path, directory):
    """Master Data is authoritative for assignment/status; Telegram IDs enrich it."""
    roster = {}
    inactive_names = set()
    by_employee = {m.employee_id: m for m in directory.values() if m.employee_id}
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            if sheet.title not in {"Master Data", "List_Name_ID"}:
                continue
            headers = None
            for values in sheet.iter_rows(values_only=True):
                candidate = {str(v or "").strip().casefold(): i for i, v in enumerate(values)}
                if headers is None:
                    if {"dealer", "name", "position"} <= candidate.keys():
                        headers = candidate
                    continue
                def get(label):
                    i = headers.get(label)
                    return values[i] if i is not None and i < len(values) else ""
                employee = normalize_telegram_id(get("id"))
                linked = by_employee.get(employee)
                member = StaffMember(employee, str(get("name") or ""), str(get("position") or ""),
                    normalize_telegram_id(get("telegram id")) or (linked.telegram_id if linked else ""),
                    normalize_sales_role(get("position")), str(get("region") or ""),
                    str(get("status") or get("employment status") or get("active status") or ""))
                dealers = set(re.findall(r"[A-Z]{2,5}\d+", str(get("dealer") or "").upper()))
                if not staff_is_active(member) or (linked and not staff_is_active(linked)):
                    inactive_names.update((d, name_key(member.name)) for d in dealers)
                    continue
                for dealer in dealers:
                    if dealer in DEALER_TO_REGION:
                        identity = employee or member.telegram_id or name_key(member.name)
                        roster[(dealer, identity)] = member
    finally:
        workbook.close()
    # Verified exceptions supplement an older master without changing targets
    # or manufacturing employee IDs. Inactive master entries remain excluded.
    overrides = Path(path).resolve().parent.parent / "config" / "gp_al_roster_overrides.csv"
    if overrides.is_file():
        with overrides.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                dealer = row.get("Dealer", "").strip().upper()
                telegram_id = normalize_telegram_id(row.get("Telegram ID"))
                role = row.get("Role", "").strip().upper()
                member = StaffMember(
                    row.get("Employee ID", "").strip(), row.get("Name", "").strip(),
                    {"SO": "Sales Officer", "SS": "Sales Supervisor"}.get(role, role),
                    telegram_id, role, DEALER_TO_REGION.get(dealer, ""), row.get("Status", ""),
                )
                if dealer not in DEALER_TO_REGION or not telegram_id.isdigit() or not member.name:
                    raise ValueError(f"Invalid GP AL roster override for {dealer}: {member.name}")
                if not staff_is_active(member) or (dealer, name_key(member.name)) in inactive_names:
                    continue
                existing = next((m for m in directory.values() if m.telegram_id == telegram_id), None)
                if existing and not staff_is_active(existing):
                    continue
                previous = [key for key, m in roster.items() if key[0] == dealer and
                            (m.telegram_id == telegram_id or name_key(m.name) == name_key(member.name))]
                identity = previous[0][1] if len(previous) == 1 else member.employee_id or f"telegram:{telegram_id}"
                roster.setdefault((dealer, identity), member)
    return roster


def resolve_al(db, path, directory, start_date, end_date, *, audit=None):
    roster = load_dealer_roster(path, directory)
    matches = {}
    for (dealer, identity), member in roster.items():
        matches.setdefault((dealer, name_key(member.name)), {})[identity] = member
    leave = {}
    with db.connect() as con:
        sources = con.execute("""SELECT t.* FROM team_talk_reports t
            JOIN telegram_groups g ON g.group_id=t.group_id
            WHERE t.report_date BETWEEN ? AND ? AND g.is_active=1
            AND g.source_kind='EXECUTION'""", (start_date, end_date)).fetchall()
    for source in sources:
        talk = parse_team_talk(source["raw_text"], source["dealer"])
        if not talk:
            continue
        for name in talk.names:
            candidates = matches.get((talk.dealer, name_key(name)), {})
            member = next(iter(candidates.values())) if len(candidates) == 1 else None
            status = ("COUNTED" if member and member.role in {"SO", "SS"} else
                      "MATCHED_OTHER_ROLE" if member else
                      "AMBIGUOUS" if candidates else "UNMATCHED")
            if audit is not None:
                chat = str(source["group_id"])
                chat = chat[4:] if chat.startswith("-100") else chat.lstrip("-")
                audit.append({"Date": talk.report_date, "Dealer": talk.dealer, "Name": name,
                    "Status": status, "Employee ID": member.employee_id if member else "",
                    "Telegram ID": member.telegram_id if member else "",
                    "Role": member.role if member else "", "Group ID": source["group_id"],
                    "Message ID": source["message_id"],
                    "Source": f"https://t.me/c/{chat}/{source['message_id']}"})
            if len(candidates) != 1:
                print(f"Team Talk AL not applied: {talk.report_date} {talk.dealer} {name!r}: {len(candidates)} active roster matches")
                continue
            identity, member = next(iter(candidates.items()))
            leave[(talk.report_date, talk.dealer, identity)] = member
    return leave


def write_al_audit(output, audit):
    target = Path(output).with_name(Path(output).stem + "_AL_Audit.csv")
    fields = ["Date", "Dealer", "Name", "Status", "Employee ID", "Telegram ID", "Role", "Group ID", "Message ID", "Source"]
    with target.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(audit)
    return target


def require_al_coverage(db, start_date, end_date):
    from .gp_scan_snapshot import missing_snapshot_ranges
    groups = list(db.groups())
    missing_dealers = sorted(set(DEALER_TO_REGION) - {g["dealer"] for g in groups})
    missing_ranges = missing_snapshot_ranges(db, start_date, end_date)
    if missing_dealers or len(groups) != len(DEALER_TO_REGION) or missing_ranges:
        raise RuntimeError(
            f"Report GP AL scan incomplete: missing dealers={missing_dealers}; "
            f"missing Team Talk scans={missing_ranges}. "
            f"Run python main.py scrape_outlet_visit --from {start_date} --to {end_date}, "
            "then export again. Today accepts a successful scan of every group and is labelled as of the scan time."
        )
