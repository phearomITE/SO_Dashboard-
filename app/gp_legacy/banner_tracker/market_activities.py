from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .groups import normalized_dealer, region_for_dealer


MARKET_GROUP_NAME = "Nationwide_P&SE Team Activities, Market"
MARKET_HEADERS = [
    "N.", "Sender_Name", "Timestamp", "Text", "Dealer", "Outlet Name",
    "Latitude", "Longitude", "Product Display",
]


@dataclass
class MarketActivityRecord:
    message_id: int
    submitted_at: datetime
    sender_id: int | None
    sender_name: str
    dealer: str
    region: str
    outlet_name: str
    products_display: str
    raw_text: str
    latitude: float | None = None
    longitude: float | None = None


def _normalized_line(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00a0", " ").replace("\\:", ":")
    return re.sub(r"\s+", " ", value).strip()


def _field(text: str, labels: Iterable[str]) -> str:
    alternatives = "|".join(
        re.escape(label).replace(r"\ ", r"[ \t_-]*") for label in labels
    )
    pattern = re.compile(
        rf"(?im)^[ \t]*[+\-*•]*[ \t]*(?:{alternatives})"
        rf"[ \t]*[:：=\-]?[ \t]*(.*?)[ \t]*$"
    )
    match = pattern.search(text or "")
    return _normalized_line(match.group(1)) if match else ""


def normalize_products_display(value: str) -> str:
    cleaned = _normalized_line(value).strip(" .,:;+-")
    key = cleaned.casefold()
    if key in {"yes", "y", "1", "true", "មាន"}:
        return "Yes"
    if key in {"no", "n", "0", "false", "គ្មាន"}:
        return "No"
    return cleaned


def parse_market_activity(
    text: str,
    *,
    message_id: int = 0,
    submitted_at: datetime | None = None,
    sender_id: int | None = None,
    sender_name: str = "",
) -> MarketActivityRecord | None:
    raw_text = str(text or "").strip()
    dealer = normalized_dealer(_field(raw_text, ["Dealer"]))
    if not dealer or not region_for_dealer(dealer):
        return None
    # Require an outlet label so unrelated messages containing a dealer code
    # are never exported as Market Activity forms.
    outlet_name = _field(raw_text, ["Outlet name", "Outlet na", "Outlet Name"])
    if not outlet_name:
        return None
    display = _field(
        raw_text,
        ["Products display", "Product display", "Productsdisplay", "Productdisplay"],
    )
    return MarketActivityRecord(
        message_id=int(message_id),
        submitted_at=submitted_at or datetime.now(ZoneInfo("Asia/Phnom_Penh")),
        sender_id=int(sender_id) if sender_id is not None else None,
        sender_name=_normalized_line(sender_name),
        dealer=dealer,
        region=region_for_dealer(dealer),
        outlet_name=outlet_name,
        products_display=normalize_products_display(display),
        raw_text=raw_text,
    )


def telegram_coordinates(message: Any) -> tuple[float, float] | None:
    geo = getattr(message, "geo", None)
    if geo is None:
        geo = getattr(getattr(message, "media", None), "geo", None)
    lat = getattr(geo, "lat", None)
    lon = getattr(geo, "long", None)
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


def _reply_id(message: Any) -> int | None:
    value = getattr(message, "reply_to_msg_id", None)
    if value is None:
        value = getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
    return int(value) if value is not None else None


def pair_market_locations(
    records: list[MarketActivityRecord],
    locations: list[dict[str, Any]],
    window_minutes: int = 15,
) -> None:
    by_message = {record.message_id: record for record in records}
    unused = set(range(len(locations)))

    # Strongest match: the Telegram location directly replies to the form.
    for index, location in enumerate(locations):
        record = by_message.get(location.get("reply_to_message_id"))
        if record is None:
            continue
        record.latitude, record.longitude = location["latitude"], location["longitude"]
        unused.discard(index)

    # Next: nearest same-sender pin inside the configured time window.
    window = timedelta(minutes=max(1, int(window_minutes)))
    for record in records:
        if record.latitude is not None or record.sender_id is None:
            continue
        candidates = [
            (abs(location["sent_at"] - record.submitted_at), index, location)
            for index, location in enumerate(locations)
            if index in unused
            and location.get("sender_id") == record.sender_id
            and abs(location["sent_at"] - record.submitted_at) <= window
        ]
        if not candidates:
            continue
        _delta, index, location = min(candidates, key=lambda item: item[0])
        record.latitude, record.longitude = location["latitude"], location["longitude"]
        unused.discard(index)

    # Forwarded bundles can have different outer senders. Match only one
    # unambiguous pin located after this form and before the next form.
    ordered = sorted(records, key=lambda item: item.message_id)
    for position, record in enumerate(ordered):
        if record.latitude is not None:
            continue
        next_id = ordered[position + 1].message_id if position + 1 < len(ordered) else 2**63
        candidates = [
            (index, location) for index, location in enumerate(locations)
            if index in unused and record.message_id < location["message_id"] < next_id
        ]
        if len(candidates) == 1:
            index, location = candidates[0]
            record.latitude, record.longitude = location["latitude"], location["longitude"]
            unused.discard(index)


async def find_exact_market_group(client: Any, group_name: str = MARKET_GROUP_NAME) -> Any:
    target = " ".join(str(group_name).casefold().split())
    matches = []
    async for dialog in client.iter_dialogs():
        if getattr(dialog, "is_group", False) and " ".join(
            str(getattr(dialog, "name", "")).casefold().split()
        ) == target:
            matches.append(dialog)
    if not matches:
        raise RuntimeError(f"Telegram group not found: {group_name}")
    if len(matches) > 1:
        raise RuntimeError(f"More than one exact Telegram group found: {group_name}")
    return matches[0]


async def scrape_market_activities(
    client: Any,
    group_id: int,
    date_from: datetime,
    date_to: datetime,
    *,
    timezone_name: str = "Asia/Phnom_Penh",
    gps_window_minutes: int = 15,
) -> list[MarketActivityRecord]:
    messages = []
    async for message in client.iter_messages(group_id, offset_date=date_to, wait_time=0):
        if message.date >= date_to:
            continue
        if message.date < date_from:
            break
        messages.append(message)

    timezone = ZoneInfo(timezone_name)
    records: list[MarketActivityRecord] = []
    locations: list[dict[str, Any]] = []
    for message in reversed(messages):
        sent_at = message.date.astimezone(timezone)
        coordinates = telegram_coordinates(message)
        if coordinates is not None:
            locations.append({
                "message_id": int(message.id), "sent_at": sent_at,
                "sender_id": int(message.sender_id) if message.sender_id is not None else None,
                "reply_to_message_id": _reply_id(message),
                "latitude": coordinates[0], "longitude": coordinates[1],
            })
            continue
        sender_name = ""
        try:
            sender = await message.get_sender()
            sender_name = _normalized_line(
                " ".join(filter(None, [
                    getattr(sender, "first_name", None),
                    getattr(sender, "last_name", None),
                ]))
                or getattr(sender, "title", None)
                or getattr(sender, "username", None)
                or ""
            )
        except Exception:
            sender_name = ""
        record = parse_market_activity(
            getattr(message, "raw_text", None) or getattr(message, "message", ""),
            message_id=int(message.id), submitted_at=sent_at,
            sender_id=getattr(message, "sender_id", None),
            sender_name=sender_name,
        )
        if record is not None:
            records.append(record)
    pair_market_locations(records, locations, gps_window_minutes)
    return sorted(records, key=lambda item: (item.submitted_at, item.message_id))


def export_market_activities(
    records: Iterable[MarketActivityRecord], output: Path
) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Market Activities"
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A2"
    sheet.append(MARKET_HEADERS)
    for number, record in enumerate(records, 1):
        sheet.append([
            number, record.sender_name, record.submitted_at.replace(tzinfo=None),
            record.raw_text, record.dealer, record.outlet_name,
            record.latitude, record.longitude, record.products_display,
        ])
    header_fill = PatternFill("solid", fgColor="17365D")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    widths = [7, 22, 19, 70, 12, 28, 14, 14, 18]
    for column, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    for row in sheet.iter_rows(min_row=2):
        row[2].number_format = "dd-mm-yy h:mm"
        row[3].alignment = Alignment(vertical="top", wrap_text=True)
        row[6].number_format = "0.000000"
        row[7].number_format = "0.000000"
        sheet.row_dimensions[row[0].row].height = 60
    sheet.auto_filter.ref = f"A1:I{max(1, sheet.max_row)}"
    workbook.save(output)
    return output


LOCATION_PIN_HEADERS = [
    'Date', 'Telegram Group', 'Region', 'Dealer', 'Telegram Name', 'Telegram ID',
    'Province', 'District', 'Com_Code', 'Commune', 'Lat', 'Lon',
]
LOCATION_PIN_TRACKING_VERSION = 'PINS_GEOGRAPHY_GPS_V3'


@dataclass(frozen=True)
class LocationPinRecord:
    group_id: int
    message_id: int
    submitted_at: datetime
    group_name: str
    region: str
    dealer: str
    sender_name: str
    sender_id: int
    latitude: float | None = None
    longitude: float | None = None


def location_pin_rows(records, *, unique_users_per_day=False, geography=None):
    """Pin identity and reference geography; optional first user/group/day pin."""
    from .pin_geography import default_pin_geography
    geography = geography if geography is not None else default_pin_geography()
    rows, seen_messages, seen_days = [], set(), set()
    for record in sorted(records, key=lambda r: (r.submitted_at, r.group_id, r.message_id)):
        key = (record.group_id, record.message_id)
        if key in seen_messages:
            continue
        seen_messages.add(key)
        day = record.submitted_at.date()
        daily_key = (day, record.group_id, record.sender_id)
        if unique_users_per_day and daily_key in seen_days:
            continue
        seen_days.add(daily_key)
        rows.append([day, record.group_name, record.region, record.dealer,
                     record.sender_name, str(record.sender_id),
                     *geography.resolve(record.latitude, record.longitude).values(),
                     record.latitude, record.longitude])
    return rows


async def scrape_location_pin_users(client, groups, date_from, date_to, *,
                                    timezone_name='Asia/Phnom_Penh', progress=print):
    """Read every location message in [start,end); never infer from a form.

    Standalone/static/live Telegram locations qualify. Map URLs, photos and
    text do not. Attribute pins to the posting account, not forwarded authors
    or the author of a replied-to form. A scan failure aborts before export.
    """
    import math
    zone = ZoneInfo(timezone_name)
    if date_from.tzinfo is None or date_to.tzinfo is None or date_to <= date_from:
        raise ValueError('Pin scan requires timezone-aware start < end (end exclusive)')
    groups = [dict(g) for g in groups]
    if not groups:
        raise ValueError('No Execution groups selected for location pin tracking')
    if len({int(g['group_id']) for g in groups}) != len(groups):
        raise ValueError('Duplicate Telegram groups in pin scan')
    records, seen, names = [], set(), {}
    for group in groups:
        group_id = int(group['group_id'])
        if group.get('source_kind', 'EXECUTION') != 'EXECUTION':
            raise ValueError('Location pin mode requires dealer Execution groups')
        dealer = str(group['dealer']).strip().upper()
        region = region_for_dealer(dealer)
        if not region:
            raise ValueError(f'Unknown dealer in location scan: {dealer}')
        group_name = str(group.get('group_name') or group.get('name') or group_id)
        count = 0
        try:
            async for message in client.iter_messages(group_id, offset_date=date_to, wait_time=0):
                stamp = getattr(message, 'date', None)
                if stamp is None:
                    continue
                # Telegram normally returns UTC-aware timestamps.
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=ZoneInfo('UTC'))
                if stamp >= date_to:
                    continue
                if stamp < date_from:
                    break
                coordinates = telegram_coordinates(message)
                if coordinates is None:
                    continue
                lat, lon = coordinates
                if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                sender_id = getattr(message, 'sender_id', None)
                if sender_id is None or int(sender_id) <= 0 or getattr(message, 'action', None):
                    continue
                sender_id = int(sender_id)
                key = (group_id, int(message.id))
                if key in seen:
                    continue
                seen.add(key)
                name = names.get(sender_id, '')
                if not name:
                    sender = getattr(message, 'sender', None)
                    if sender is None:
                        try:
                            sender = await message.get_sender()
                        except Exception:
                            # Preserve pin/ID even when the profile is unavailable.
                            sender = None
                    name = _normalized_line(' '.join(filter(None, [
                        getattr(sender, 'first_name', None), getattr(sender, 'last_name', None),
                    ])) or getattr(sender, 'username', '') or '')
                    if name:
                        names[sender_id] = name
                records.append(LocationPinRecord(group_id, int(message.id), stamp.astimezone(zone),
                    group_name, region, dealer, name, sender_id, lat, lon))
                count += 1
        except Exception as exc:
            raise RuntimeError(f'Location pin scan failed for {group_name} ({dealer}); no new export was created. Rerun the selected range.') from exc
        if progress is not None:
            progress(f'PIN SCAN {group_name}: {count} location messages')
    # Fill an earlier blank when another pin resolves the same account's profile.
    from dataclasses import replace
    records = [replace(r, sender_name=names.get(r.sender_id, '')) if not r.sender_name else r for r in records]
    return sorted(records, key=lambda r: (r.submitted_at, r.group_id, r.message_id))


def export_location_pin_users(records, output, *, unique_users_per_day=False, geography=None):
    """Export pin users and four reference fields; preserve ID/code text."""
    import os
    import tempfile
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = location_pin_rows(records, unique_users_per_day=unique_users_per_day, geography=geography)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Location Pin Users'
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = 'A2'
    sheet.append(LOCATION_PIN_HEADERS)
    for row in rows:
        sheet.append(row)
        # User-provided names/group titles must remain literal Excel text.
        for column in range(2, 11):
            sheet.cell(sheet.max_row, column).data_type = 's'
        sheet.cell(sheet.max_row, 1).number_format = 'dd/mm/yyyy'
        sheet.cell(sheet.max_row, 6).number_format = '@'
        sheet.cell(sheet.max_row, 9).number_format = '@'
        sheet.cell(sheet.max_row, 11).number_format = '0.000000'
        sheet.cell(sheet.max_row, 12).number_format = '0.000000'
    for cell in sheet[1]:
        cell.fill = PatternFill('solid', fgColor='17365D')
        cell.font = Font(color='FFFFFF', bold=True)
        cell.alignment = Alignment(vertical='center', wrap_text=True)
    sheet.row_dimensions[1].height = 30
    for column, width in enumerate([14, 48, 12, 14, 35, 24, 24, 26, 16, 28, 16, 16], 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.auto_filter.ref = f'A1:L{max(1,sheet.max_row)}'
    fd, temporary = tempfile.mkstemp(dir=output.parent, suffix='.xlsx')
    os.close(fd)
    try:
        workbook.save(temporary)
        workbook.close()
        try:
            os.replace(temporary, output)
        except PermissionError as exc:
            raise PermissionError(f'Close {output.name} in Excel, then rerun the export cell.') from exc
    finally:
        workbook.close()
        Path(temporary).unlink(missing_ok=True)
    return output
