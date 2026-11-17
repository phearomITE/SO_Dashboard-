from __future__ import annotations

import hashlib
import unicodedata

# These five Telegram accounts are GUIDELINE / REFERENCE accounts for outputs
# that explicitly exclude training/guideline senders (especially the Combined
# Summary).  They are still scraped and retained in Banner Tracking.
#
# IMPORTANT: this is an output-filter list, not a global scrape filter and not
# an allow-list. Every Telegram sender remains eligible for source capture.
EXCLUDED_TELEGRAM_NAMES = (
    "CH Phy Roun GPS",
    "LOEM Sovannvong",
    "Dany.P",
    "Bo Pisey-CE",
    "Pheak HAV ( GPS )",
)

# One-time migration marker. Older releases skipped the five accounts before
# saving their messages but still advanced group checkpoints. The first
# continuation run after this fix replays 19-Aug-to-now once, then stores this
# marker so later runs remain fast and incremental.
BANNER_ALL_SENDERS_BACKFILL_STATE = "banner_all_senders_backfill_v1"

# Older rows may contain the account that clicked Forward instead of the
# original author displayed by Telegram. Replay once after the identity fix.
BANNER_FORWARDED_SENDER_BACKFILL_STATE = "banner_forwarded_sender_backfill_v1"

# Telegram can expose first/last names in a different order or with slightly
# different punctuation/spaces. These aliases still identify the same
# guideline account and therefore must also be excluded.
EXCLUDED_TELEGRAM_ALIASES = {
    "sovannvong loem": "LOEM Sovannvong",
    "loem sovannvong": "LOEM Sovannvong",
    "pheak hav (gps)": "Pheak HAV ( GPS )",
    "pheak hav gps": "Pheak HAV ( GPS )",
    # Historical Excel rows sometimes store this same guideline account by
    # username instead of the Telegram display name.
    "@pheak_gps": "Pheak HAV ( GPS )",
    "pheak_gps": "Pheak HAV ( GPS )",
    "ch phy roun gps": "CH Phy Roun GPS",
    "dany.p": "Dany.P",
    "bo pisey-ce": "Bo Pisey-CE",
}


def normalize_telegram_name(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = (
        text.replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )
    return " ".join(text.strip().split()).casefold()


def stable_telegram_id(
    sender_id: object = None,
    sender_name: object = None,
    sender_username: object = None,
    *,
    fallback_key: object = None,
) -> int:
    """Return a nonblank Telegram identity key suitable for every export.

    Real Telegram IDs are always preserved.  Privacy-hidden forwarded posts
    sometimes expose only ``from_name`` and no numeric Telegram user ID.  For
    those posts, derive a stable negative key from username/name so repeated
    reports from the same visible account keep the same Excel ID.  The fallback
    range is deliberately far outside normal Telegram user IDs while remaining
    exactly representable by Excel.
    """
    try:
        value = int(sender_id)
    except (TypeError, ValueError):
        value = 0
    if value:
        return value

    username = normalize_telegram_name(str(sender_username or "").lstrip("@"))
    name = normalize_telegram_name(sender_name)
    fallback = normalize_telegram_name(fallback_key)
    identity = username or name or fallback or "telegram-identity-unknown"
    digest = hashlib.sha256(identity.encode("utf-8")).digest()
    suffix = int.from_bytes(digest[:6], "big") % 900_000_000_000_000
    return -(8_000_000_000_000_000 + suffix)


EXCLUDED_TELEGRAM_NAME_KEYS = {
    normalize_telegram_name(name) for name in EXCLUDED_TELEGRAM_NAMES
}
EXCLUDED_TELEGRAM_NAME_KEYS.update(EXCLUDED_TELEGRAM_ALIASES.keys())


def excluded_telegram_name(value: object) -> str | None:
    """Return the canonical excluded guideline name, or None if not excluded."""
    key = normalize_telegram_name(value)
    if not key:
        return None
    if key in EXCLUDED_TELEGRAM_ALIASES:
        return EXCLUDED_TELEGRAM_ALIASES[key]
    for name in EXCLUDED_TELEGRAM_NAMES:
        if key == normalize_telegram_name(name):
            return name
    return None


def is_excluded_telegram_name(value: object) -> bool:
    return excluded_telegram_name(value) is not None


# Backward-compatible names for older imports.
# Their meaning is intentionally changed to "not excluded" so old callers do
# not accidentally turn the five guideline accounts into an allow-list.
def is_tracked_telegram_name(value: object) -> bool:
    """Return True for any nonblank Telegram name eligible for source capture."""
    return bool(" ".join(str(value or "").strip().split()))


def tracked_telegram_name(value: object) -> str | None:
    """Return the cleaned display name used for source capture."""
    text = " ".join(str(value or "").strip().split())
    return text or None


# Legacy constant alias. Do not use as an allow-list in new code.
TRACKED_TELEGRAM_NAMES = EXCLUDED_TELEGRAM_NAMES
