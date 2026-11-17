"""Unique Product Display outlets within one selected export period."""
from decimal import Decimal, InvalidOperation
import re
import unicodedata


def product_display_outlet_key(row):
    name = unicodedata.normalize('NFKC', str(row.get('outlet_name') or ''))
    name = ' '.join(name.replace('\u200b', '').split()).casefold()
    phone = re.sub(r'\D', '', str(row.get('phone') or ''))
    def coordinate(value):
        try:
            number = Decimal(str(value).strip())
            return number.normalize() if number.is_finite() else None
        except (InvalidOperation, ValueError):
            return None
    return name, phone, coordinate(row.get('latitude')), coordinate(row.get('longitude'))


def unique_product_display_outlets(rows):
    """Keep the earliest eligible Yes per four-field key across the period.

    Sender, date, dealer and Telegram message ID do not create another outlet.
    Coordinates are numeric exact matches; no proximity threshold is applied.
    """
    seen = set()
    kept = []
    for row in rows:
        key = product_display_outlet_key(row)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept
