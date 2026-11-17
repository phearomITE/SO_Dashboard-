"""One deterministic Banner installation identity for Excel and Gold."""
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo
import re
from .outlet_gps import coordinates, distance_m


def local_datetime(value):
    if value in (None, ""):
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(ZoneInfo("Asia/Phnom_Penh"))
    return parsed.replace(tzinfo=None)


def _banner_duplicate_key(form: dict, signature: tuple) -> tuple:
    """Same installation identity; sender is intentionally excluded."""
    def coordinate(value):
        try:
            return round(float(value), 5)
        except (TypeError, ValueError):
            return None
    submitted = local_datetime(form.get("submitted_at"))
    local_report_date = submitted.date().isoformat() if submitted else ""
    return (
        local_report_date,
        str(form.get("dealer") or "").strip().upper(),
        re.sub(r"[^\w]+", "", str(form.get("outlet_name") or "").casefold()),
        re.sub(r"\D+", "", str(form.get("phone") or "")),
        coordinate(form.get("latitude")), coordinate(form.get("longitude")),
        signature,
    )


def _banner_nearby_key(form: dict, signature: tuple) -> tuple:
    """Identity fields used before applying the 20 metre GPS tolerance."""
    submitted = local_datetime(form.get("submitted_at"))
    local_report_date = submitted.date().isoformat() if submitted else ""
    return (
        local_report_date,
        str(form.get("dealer") or "").strip().upper(),
        re.sub(r"[^\w]+", "", str(form.get("outlet_name") or "").casefold()),
        signature,
    )


def _nearby_duplicate_distance(form: dict, kept: dict) -> float | None:
    """Return GPS distance for duplicate candidates within 20 metres."""
    point = coordinates(form.get("latitude"), form.get("longitude"))
    kept_point = coordinates(kept.get("latitude"), kept.get("longitude"))
    if point is None or kept_point is None:
        return None
    metres = distance_m(point, kept_point)
    return metres if metres <= 20.0 else None



def select_banner_reports(forms, details):
    """Return canonical forms and duplicate decisions, comparing only kept rows.

    Blank outlet/phone and Unicode normalization, five-decimal GPS fallback,
    20-metre proximity, and earliest-message ordering are identical for all
    consumers. Rejected tokens do not change an installation's identity.
    """
    signatures = defaultdict(list)
    for detail in details:
        if str(detail["status"]) in {"OK", "SPECIAL"}:
            key = (int(detail["group_id"]), int(detail["message_id"]))
            signatures[key].append((str(detail["banner_name"]), int(detail["quantity"] or 0), str(detail["status"])))
    eligible = [dict(f) for f in forms if signatures.get((int(f["group_id"]), int(f["message_id"])))]
    eligible.sort(key=lambda f: (local_datetime(f.get("submitted_at")) or datetime.min,
                                str(f.get("region") or ""), str(f.get("dealer") or ""),
                                int(f["group_id"]), int(f["message_id"])))
    exact = {}
    nearby = defaultdict(list)
    kept_forms, duplicates = [], []
    for form in eligible:
        key = (int(form["group_id"]), int(form["message_id"]))
        signature = tuple(sorted(signatures[key]))
        business = _banner_duplicate_key(form, signature)
        near = _banner_nearby_key(form, signature)
        kept = exact.get(business)
        metres = None
        if kept is None:
            for candidate in nearby[near]:
                metres = _nearby_duplicate_distance(form, candidate)
                if metres is not None:
                    kept = candidate
                    break
        if kept is not None:
            duplicates.append({"form": form, "kept": kept, "business_key": business,
                               "signature": signature, "distance_m": metres})
            continue
        exact[business] = form
        nearby[near].append(form)
        kept_forms.append(form)
    return kept_forms, duplicates
