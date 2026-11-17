from __future__ import annotations

import hashlib
import re
from datetime import datetime, date, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from .database import Database
from .groups import region_for_dealer
from .parser import (
    BannerNormalizer,
    ParsedForm,
    extract_banner_text,
    has_banner_label,
    has_meaningful_banner_text,
    parse_form,
)
from .telegram_identity import BANNER_ALL_SENDERS_BACKFILL_STATE


EXPECTED_HEADERS = {
    "Region": "region",
    "Dealer": "dealer",
    "Outlet Name": "outlet_name",
    "Outlet Type": "outlet_type",
    "Phone": "phone",
    "Banner": "banner",
    "Latitude": "latitude",
    "Longitude": "longitude",
    "Has": "has",
    "Message date": "submitted_at",
    "Message ID": "message_id",
    "Sender": "sender",
    "Raw": "raw",
}


def resolve_manual_base_path(explicit_path: Path | None, manual_base_dir: Path) -> Path:
    """Resolve the user's historical workbook without requiring a rename.

    Priority is an explicitly supplied file, then ``base.xlsx``, then the only
    Excel workbook in ``manual_base``. If that folder is empty, the packaged
    Aug'26 workbook in ``templates`` (or the current Banner export) is used.
    This keeps a fresh project ZIP runnable without forcing the user to copy
    the same historical workbook into two folders.
    """
    if explicit_path:
        candidate = Path(explicit_path)
        if candidate.exists() and candidate.is_file():
            return candidate
        raise FileNotFoundError(f"Historical base Excel does not exist: {candidate.resolve()}")

    preferred = manual_base_dir / "base.xlsx"
    if preferred.exists():
        return preferred

    candidates = sorted(
        p for p in manual_base_dir.glob("*.xlsx")
        if p.is_file() and not p.name.startswith("~$")
    )
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        project_root = manual_base_dir.parent
        packaged_candidates = [
            project_root / "templates" / "All Banner Tracking in Aug'26.xlsx",
            project_root / "exports" / "All Banner Tracking in Aug'26.xlsx",
            project_root / "All Banner Tracking in Aug'26.xlsx",
        ]
        for packaged in packaged_candidates:
            if packaged.exists() and packaged.is_file():
                return packaged
        checked = ", ".join(str(path.resolve()) for path in packaged_candidates)
        raise FileNotFoundError(
            f"No historical base Excel found in {manual_base_dir.resolve()}, and no "
            f"packaged fallback workbook was found. Checked: {checked}."
        )
    names = ", ".join(p.name for p in candidates)
    raise RuntimeError(
        f"More than one historical Excel file was found in {manual_base_dir.resolve()}: {names}. "
        "Rename the intended file to base.xlsx or pass --file explicitly."
    )


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _parse_sender(value: str) -> tuple[str, str]:
    text = _text(value)
    if text.startswith("@"):
        return text, text[1:]
    return text, ""


def _to_datetime(v) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime.combine(v, time.min)
    text = _text(v).replace("Z", "+00:00")
    if not text:
        raise ValueError("Message date is empty")
    return datetime.fromisoformat(text)


def _header_map(ws) -> dict[str, int]:
    header_row = None
    mapping: dict[str, int] = {}
    for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=min(20, ws.max_row), values_only=True), 1):
        values = {_text(v): i for i, v in enumerate(row)}
        if "Dealer" in values and "Message ID" in values and "Message date" in values and "Raw" in values:
            header_row = row_idx
            mapping = values
            break
    if header_row is None:
        raise RuntimeError(
            f"Cannot find the Deployed_Telegram header row in sheet '{ws.title}'. "
            "Required columns include Dealer, Message date, Message ID and Raw."
        )
    mapping["__header_row__"] = header_row
    return mapping


def _fallback_form(row: dict[str, str]) -> ParsedForm | None:
    raw = row.get("raw", "")
    parsed = parse_form(raw)
    if parsed:
        return parsed
    # When Raw contains a Banner label, it is authoritative even when blank.
    # This prevents shifted spreadsheet cells such as "POSM" or "Banner Type"
    # from becoming false Banner quantities.
    banner = extract_banner_text(raw) if has_banner_label(raw) else _text(row.get("banner"))
    if not has_meaningful_banner_text(banner):
        return None
    outlet_name = _text(row.get("outlet_name"))
    outlet_type = _text(row.get("outlet_type"))
    if outlet_name.casefold() in {"phone", "telephone", "tel", "outlet type", "banner type"}:
        outlet_name = ""
    if re.match(r"(?i)^\s*(?:banner|bunner)(?:\s*type)?\s*[:=]?", outlet_type):
        outlet_type = ""
    return ParsedForm(
        dealer=_text(row.get("dealer")),
        outlet_name=outlet_name,
        phone=_text(row.get("phone")),
        outlet_type=outlet_type,
        banner_text=banner,
        product_display="",
        remark_issues="",
    )


def _synthetic_history_id(*parts: object) -> int:
    """Return a stable negative database key for a row lacking Telegram ID.

    Negative keys can never collide with real Telegram message IDs and are
    never used as Telegram continuation checkpoints.
    """
    payload = "\x1f".join(_text(part) for part in parts).encode("utf-8")
    value = int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")
    return -max(1, value & ((1 << 62) - 1))


def import_excel_base(
    db: Database,
    normalizer: BannerNormalizer,
    excel_path: Path,
    date_from: str,
    date_to: str,
    *,
    reset_existing: bool = False,
    timezone_name: str = "Asia/Phnom_Penh",
) -> dict[str, int | str]:
    """Import existing Deployed_Telegram rows as the durable scrape base.

    The Telegram Message ID is the database identity key, not the Excel row
    number. Historical IDs are not trusted as continuation IDs because the
    workbook may contain forwarded posts from a mapping group. Continuation
    starts from the fixed end-date boundary and later saves real per-group IDs.
    """
    if reset_existing:
        db.reset_tracking_data()

    wb = load_workbook(excel_path, read_only=True, data_only=True)
    if "Deployed_Telegram" not in wb.sheetnames:
        raise RuntimeError("Excel base must contain a 'Deployed_Telegram' sheet.")
    ws = wb["Deployed_Telegram"]
    headers = _header_map(ws)
    hrow = headers.pop("__header_row__")

    groups = {str(r["dealer"]).strip().upper(): dict(r) for r in db.groups()}
    if not groups:
        raise RuntimeError("No active Telegram groups found. Run discover before importing the base.")

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    imported = skipped = invalid = gps = synthetic_ids = 0

    for values in ws.iter_rows(min_row=hrow + 1, values_only=True):
        row = {}
        for name, idx in headers.items():
            row[name] = values[idx] if idx < len(values) else None
        dealer = _text(row.get("Dealer")).upper()
        if not dealer or dealer not in groups:
            continue
        try:
            submitted_at = _to_datetime(row.get("Message date"))
        except (TypeError, ValueError):
            invalid += 1
            continue
        if not (start <= submitted_at.date() <= end):
            continue
        raw = _text(row.get("Raw"))
        parsed = _fallback_form({
            "dealer": dealer,
            "outlet_name": _text(row.get("Outlet Name")),
            "phone": _text(row.get("Phone")),
            "outlet_type": _text(row.get("Outlet Type")),
            "banner": _text(row.get("Banner")),
            "raw": raw,
        })
        if parsed is None:
            skipped += 1
            continue

        raw_message_id = row.get("Message ID")
        try:
            message_id = int(float(raw_message_id))
            if message_id <= 0:
                raise ValueError
        except (TypeError, ValueError):
            message_id = _synthetic_history_id(
                dealer, submitted_at.isoformat(), parsed.outlet_name,
                parsed.phone, parsed.banner_text, raw,
            )
            synthetic_ids += 1

        group = groups[dealer]
        canonical_region = region_for_dealer(dealer) or str(group["region"])
        sender_name, sender_username = _parse_sender(_text(row.get("Sender")))
        items = normalizer.parse(parsed.banner_text or "")
        db.save_form(
            group_id=int(group["group_id"]),
            message_id=message_id,
            submitted_at=submitted_at,
            sender_id=None,
            sender_name=sender_name,
            sender_username=sender_username,
            # Dealer is authoritative. Never import Region=0, a blank formula
            # cache, or a stale Region from the historical workbook.
            region=canonical_region,
            dealer=dealer,
            form=parsed,
            items=items,
            raw_text=raw,
        )

        lat = row.get("Latitude")
        lon = row.get("Longitude")
        try:
            lat = float(lat) if lat not in (None, "") else None
            lon = float(lon) if lon not in (None, "") else None
        except (TypeError, ValueError):
            lat = lon = None
        if lat is not None and lon is not None:
            with db.connect() as con:
                con.execute(
                    """UPDATE banner_forms SET latitude=?, longitude=?, gps_status='GPS_MATCHED'
                       WHERE group_id=? AND message_id=?""",
                    (lat, lon, int(group["group_id"]), message_id),
                )
            gps += 1

        imported += 1
    # The supplied workbook is authoritative through the declared end date.
    # Its Message IDs can belong to the forwarding/mapping group, so NONE of
    # them are used to continue inside the 65 original Execution groups.
    # Every group starts from the same fixed local-time boundary. After the
    # first successful scrape, this date checkpoint is replaced by the newest
    # real Message ID observed in that exact group.
    zone = ZoneInfo(timezone_name)
    fallback_at = datetime.combine(end, time.max, zone)
    for group in groups.values():
        gid = int(group["group_id"])
        db.set_sync_checkpoint_exact(
            group_id=gid,
            last_message_id=0,
            last_message_date=fallback_at,
            history_from=datetime.combine(start, time.min, zone),
            history_to=fallback_at,
        )
    db.set_state("banner_base_through_date", end.isoformat())
    db.set_state("banner_base_workbook", Path(excel_path).name)
    # A base installed by this corrected release has not skipped any sender.
    # Its date checkpoints will collect every post-base message normally, so
    # it does not need the compatibility replay used for older databases.
    db.set_state(BANNER_ALL_SENDERS_BACKFILL_STATE, "complete")
    wb.close()

    return {
        "rows_imported_or_upserted": imported,
        "rows_skipped": skipped,
        "rows_invalid": invalid,
        "rows_without_message_id": synthetic_ids,
        "gps_rows": gps,
        "groups_with_message_id_checkpoints": 0,
        "groups_with_date_fallback": len(groups),
        "groups_with_checkpoints": len(groups),
        "continuation_starts": (end + timedelta(days=1)).isoformat(),
        "date_from": date_from,
        "date_to": date_to,
    }
