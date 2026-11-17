from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .outlet_visit import normalize_outlet_type


FIELD_NAMES = {
    "dealer": ["dealer"],
    # Older/mobile templates shorten "Outlet Name" to "Outlet", and field
    # users occasionally type "Outlet Nam" (missing the final e).
    "outlet_name": ["outlet name", "outlet nam", "outlet  name", "outlet"],
    "phone": ["phone", "telephone", "tel"],
    "outlet_type": ["outlet type", "type"],
}

# Historical users wrote Banner labels in many ways. The label is recognized
# case-insensitively, with optional "Type", punctuation and the common
# misspelling "Bunner". It must start a line so remark text is not mistaken for
# a Banner field.
# Banner field label:
# - case-insensitive: Banner Type / banner type / BANNER TYPE / mixed case
# - accepts optional Type/Ty, including BannerType, Banner-Type, Banner_Type
# - accepts common separators: :, =, -, comma, or whitespace
# - accepts the historical "Bunner" spelling and close/fuzzy spellings
# - must start a line so text inside POSM/Remark/etc. is not treated as a field
BANNER_LABEL_RE = re.compile(
    r"(?im)^\s*\*?\s*(?:banner|bunner)"
    r"(?:\s*[-_./\\:：=,;|]+\s*|\s+)"
    r"(?:type|ty)?"
)


_NON_BANNER_VALUES = {"mapping", "report", "summary", "tracking", "photo", "photos"}


def _similar_word(value: str, target: str, threshold: float) -> bool:
    return SequenceMatcher(None, value.casefold(), target).ratio() >= threshold


def _find_banner_field(text: str) -> tuple[int, int, str] | None:
    """Find the Banner/Banner Type field with deliberately broad tolerance.

    The Telegram forms are human-entered, so this accepts case, spacing,
    punctuation, and common spelling variations without requiring one exact
    label.  Examples include::

        Banner Type: water 1m
        banner type , water 1m
        BANNER-TYPE = water 1m
        Banner_Type: water 1m
        BannerType water 1m
        Banner Ty - water 1m
        Bunner Type: water 1m
        Baner Tye: water 1m
        Banner: water 1m
        Banner - water 1m
        Banner, water 1m

    Only a line beginning with the label is considered.  This prevents
    ``POSM: Banner ...`` and ``Remark Issues: ... banner ...`` from becoming
    Banner fields.  The returned value is restricted to that same line, so a
    following Telegram form field can never be swallowed.
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\u00a0", " ").replace("\u200b", "")
    offset = 0

    # Separators users commonly type between label words / label and value.
    # Include ASCII and full-width punctuation plus the long dash variants.
    separator_chars = r"[\s\-_./\\:：=,;|，、；：]+"
    value_delimiter = r"[:：=\-–—,，、;；|]+"

    for line in normalized.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        leading = re.match(r"^\s*(?:(?:[-–—•·▪▫]+|#{1,6})\s*|(?:\d+[.)-])\s*)?\*?\s*", body)
        cursor = leading.end() if leading else 0

        word_match = re.match(r"[A-Za-z]+", body[cursor:])
        if not word_match:
            offset += len(line)
            continue

        first_word = word_match.group()
        first_key = first_word.casefold()
        exact_banner = first_key in {"banner", "bunner"}
        combined_banner_type = first_key in {"bannertype", "bunnertype"}
        fuzzy_banner = _similar_word(first_word, "banner", 0.70)
        if not (exact_banner or combined_banner_type or fuzzy_banner):
            offset += len(line)
            continue
        cursor += word_match.end()

        has_type = combined_banner_type

        # IMPORTANT: do not greedily consume punctuation after Banner until
        # we know the next word is Type/Ty.  This fixes "Banner - water" and
        # "Banner, water", which were previously losing the value.
        if not combined_banner_type:
            between = re.match(separator_chars, body[cursor:])
            between_end = cursor + (between.end() if between else 0)
            second = re.match(r"[A-Za-z]+", body[between_end:])
            if second:
                second_word = second.group()
                if (
                    second_word.casefold() in {"type", "ty"}
                    or _similar_word(second_word, "type", 0.65)
                ):
                    has_type = True
                    cursor = between_end + second.end()

        # Optional punctuation/spaces after the complete label.  A plain
        # "Banner water" is also valid, so spaces alone are allowed when the
        # label itself is already known.
        cursor += len(body[cursor:]) - len(body[cursor:].lstrip(" \t"))
        delimiter = re.match(value_delimiter, body[cursor:])
        has_delimiter = bool(delimiter)
        if delimiter:
            cursor += delimiter.end()
            cursor += len(body[cursor:]) - len(body[cursor:].lstrip(" \t"))

        value = clean_space(body[cursor:])

        # Fuzzy Banner spellings need a field-like signal. Exact Banner/Bunner
        # labels may stand alone because older forms commonly use "Banner:".
        if fuzzy_banner and not exact_banner and not (has_type or has_delimiter):
            offset += len(line)
            continue

        # "Banner Mapping", "Banner Report", etc. are headings, not fields.
        if not has_type and not has_delimiter and value.casefold() in _NON_BANNER_VALUES:
            offset += len(line)
            continue

        return offset, offset + cursor, value

    return None


STANDARD_BOUNDARIES = (
    "dealer|outlet(?:\\s*nam(?:e)?)?|phone|telephone|tel|(?:outlet\\s*)?type|"
    "(?:banner|bunner)(?:\\s*type)?|posm|program|"
    "\\*?(?:remark|remake)\\s*(?:issues?|iusse|issuee|isue)"
)
FIELD_BOUNDARY = (
    rf"(?:{STANDARD_BOUNDARIES})\s*(?::|=|-)|"
    r"\*?\s*product[\s\-_./\\]*disp(?:lay|aly)\b\s*(?::|=|-|\\:)?"
)


@dataclass(frozen=True)
class BannerItem:
    official_name: str
    quantity: int
    original_token: str
    status: str = "OK"


@dataclass(frozen=True)
class ParsedForm:
    dealer: str
    outlet_name: str
    phone: str
    outlet_type: str
    banner_text: str
    product_display: str = ""
    remark_issues: str = ""


@dataclass(frozen=True)
class ValidationResult:
    is_report_candidate: bool
    is_valid: bool
    errors: tuple[str, ...]
    error_codes: tuple[str, ...]
    form: ParsedForm | None
    items: tuple[BannerItem, ...]


FORMAT_FIELDS = [
    ("Dealer", r"(?im)^\s*\.?[ \t]*dealer\s*:"),
    ("Outlet Name", r"(?im)^\s*outlet(?:\s*nam(?:e)?)?\s*:"),
    ("Phone", r"(?im)^\s*(?:phone|telephone|tel)\s*(?::|=|-)"),
    ("Outlet Type", r"(?im)^\s*(?:outlet\s*)?type\s*:"),
    ("Banner Type", r"(?im)^\s*(?:banner|bunner)(?:\s*type)?\s*(?::|=|-)"),
    ("POSM", r"(?im)^\s*posm\s*:"),
    ("PROGRAM", r"(?im)^\s*program\s*:"),
    ("Product Display", r"(?im)^\s*\*?\s*product[\s\-_./\\]*disp(?:lay|aly)\b.*$"),
    # The remark field may be an empty label with no colon, e.g. "*Remark issue".
    ("Remark Issues", r"(?im)^\s*\*?\s*(?:remark|remake)\s*(?:issues?|iusse|issuee|isue)\s*(?:(?::|=|-)\s*.*)?$"),
]


def clean_space(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    value = value.replace("\u00a0", " ").replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip(" ,;:/")


def is_non_banner_text(value: str) -> bool:
    """True only for verified promotional text that must count as zero."""
    compact = re.sub(
        r"[^A-Z0-9]+", "",
        unicodedata.normalize("NFKC", value or "").upper()
        .replace("\u00a0", " ").replace("\u200b", ""),
    )
    return compact in {
        "WURKZSPECIALVOLLEYBALLCUP",
        "WURKZSPECIALVOLLEYBALLCUP1",
    }


def _extract(text: str, aliases: list[str]) -> str:
    # Telegram captions frequently contain NBSP/zero-width characters copied
    # from mobile templates. Normalize the source before matching any label.
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    names = "|".join(re.escape(x).replace(r"\ ", r"\s*") for x in aliases)
    match = re.search(
        rf"(?ims)^\s*(?:{names})\s*(?::|=|-)\s*(.*?)"
        rf"(?=^\s*(?:{FIELD_BOUNDARY})|\Z)",
        text,
    )
    return clean_space(match.group(1)) if match else ""


def _extract_line(text: str, aliases: list[str]) -> str:
    """Extract only the value on the label's own line."""
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    names = "|".join(re.escape(x).replace(r"\ ", r"[ \t]*") for x in aliases)
    # MDK2 uses '.Dealer:' in its photo captions. Treat the leading dot as
    # label punctuation, while retaining strict line-based field extraction.
    prefix = r"(?:\.[ \t]*)?" if "dealer" in aliases else ""
    match = re.search(
        rf"(?im)^[ \t]*{prefix}(?:{names})[ \t]*(?::|=|-)[ \t]*(.*)$",
        text,
    )
    return clean_space(match.group(1)) if match else ""


def _product_display_field(text: str) -> tuple[bool, str]:
    """Return ``(field_present, raw_value)`` for a Product Display line.

    Product Display is a *field-presence* metric.  A form counts when its line
    starts with a recognizable Product Display label, regardless of whether the
    user entered Yes, No, a blank value, or another note.  Mixed case, bullets,
    missing spaces, escaped/full-width punctuation, and the common ``Dispaly``
    typo are tolerated.
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.replace("\u00a0", " ").replace("\u200b", "")

    for raw_line in normalized.splitlines():
        line = re.sub(
            r"^\s*(?:(?:[-–—•·▪▫]+|#{1,6})\s*|(?:\d+[.)-])\s*)?\*?\s*",
            "",
            raw_line,
        )
        first = re.match(r"(?i)product", line)
        if not first:
            continue
        cursor = first.end()
        between = re.match(r"[\s\-_./\\]*", line[cursor:])
        cursor += between.end() if between else 0
        second = re.match(r"[A-Za-z]+", line[cursor:])
        if not second or not (
            second.group().casefold() == "display"
            or _similar_word(second.group(), "display", 0.70)
        ):
            continue
        cursor += second.end()
        remainder = re.sub(r"^[\s\\:：=\-–—,;|]+", "", line[cursor:]).strip()
        return True, remainder
    return False, ""


def has_product_display_field(text: str) -> bool:
    """Return True when the report contains a Product Display field line."""
    return _product_display_field(text)[0]


def product_display_status(text: str) -> str:
    """Return the entered Yes/No value for audit; blank/other stays blank.

    Ingestion preserves field presence separately via parse_product_display.
    The Product Display export uses explicit_product_display_yes to count only
    captions that explicitly say Yes, without losing No/blank audit records.
    """
    present, remainder = _product_display_field(text)
    if not present or not remainder:
        return ""
    yes_values = {"yes", "y", "true", "ok", "1", "✓", "✔", "មាន"}
    no_values = {"no", "n", "false", "0", "none", "nil", "មិនមាន"}
    value = re.split(r"[\s,;.!?]+", remainder, maxsplit=1)[0].casefold()
    if value in yes_values:
        return "Yes"
    if value in no_values:
        return "No"
    return ""


def parse_product_display(text: str) -> str:
    """Normalize any Product Display field occurrence to one counted record."""
    return "Yes" if has_product_display_field(text) else ""


def explicit_product_display_yes(text: str) -> bool:
    """Export eligibility: the caption must explicitly say Yes (any case).

    Stored field-presence flags are not evidence of a positive response.
    Keep ingestion/audit parsing separate so No and blank captions remain saved.
    """
    present, value = _product_display_field(text)
    return present and value.strip().rstrip('.,;!').strip().casefold() == 'yes'


def has_banner_label(text: str) -> bool:
    return _find_banner_field(text) is not None


def extract_banner_text(text: str) -> str:
    """Extract everything after a tolerant Banner label up to the next field."""
    field = _find_banner_field(text)
    return field[2] if field else ""


def has_meaningful_banner_text(value: str) -> bool:
    """Return False for an empty/non-reportable Banner field value.

    These values stay in the database for audit and Product Display tracking,
    but must not appear as Banner rows in the Raw export.
    """
    normalized = unicodedata.normalize("NFKC", value or "")
    normalized = normalized.replace("\u00a0", " ").replace("\u200b", "").strip()
    empty_markers = {
        "", "0", "00", "0.0", "0.00", "n/a", "na", "none", "nil", "null",
        "new banner", "no banner", "not have", "no have", "rt", "ds", "d.s",
        "posm", "program", "programme", "banner type", "banner",
    }
    if normalized.casefold() in empty_markers:
        return False

    # POSM/outlet/dealer values accidentally entered in Banner Type are not
    # banner records and must not appear in the Raw Banner export.
    non_banner_value = re.sub(r"[\s._-]+", "", normalized.casefold())
    if (
        re.fullmatch(r"(?:tshirt|poster)\d*", non_banner_value)
        or re.fullmatch(r"(?:អាវយឺត|មួកប៉ារ៉េ)\d*", non_banner_value)
        or re.fullmatch(r"dealer:?\s*[a-z]{2,5}\d+", normalized.casefold())
    ):
        return False

    # Historical forms sometimes list three categories even when none exists:
    # "Beer:0 / Soft Drink:0 / Water:0" or
    # "Beer... / Soft Drink: New Banner / Water:.........".
    components = [part.strip() for part in re.split(r"[/|;,]+", normalized) if part.strip()]
    category_pattern = re.compile(r"(?i)^\s*(?:beer|soft\s*drink|water)\s*(?::|=|-)?\s*(.*)$")
    if components and all(category_pattern.match(part) for part in components):
        component_values = [category_pattern.match(part).group(1).strip() for part in components]

        def component_has_value(component: str) -> bool:
            cleaned = component.strip().casefold()
            if cleaned in empty_markers:
                return False
            remaining = "".join(character for character in cleaned if character.isalnum())
            if not remaining:
                return False
            if remaining.isdigit() and int(remaining) == 0:
                return False
            return True

        return any(component_has_value(component) for component in component_values)

    # Periods, ellipses, spaces, and other punctuation are placeholders, not
    # Banner data. At least one letter or digit must remain.
    substantive = "".join(character for character in normalized if character.isalnum())
    if not substantive:
        return False
    if substantive.isdigit() and int(substantive) == 0:
        return False
    return True


def parse_form(text: str) -> ParsedForm | None:
    dealer = _extract_line(text, FIELD_NAMES["dealer"])
    outlet_name = _extract_line(text, FIELD_NAMES["outlet_name"])
    banner_text = extract_banner_text(text)
    # A blank Outlet Name makes the surrounding form incomplete, but it must
    # not discard a meaningful Banner Type. validate_report() will mark the
    # row NEED_REVIEW and process_message() can still preserve its banner
    # quantities. This is common in edited photo captions such as the 19-Aug
    # CKS1 report: "c-sport2/Exprezz 2/wurkz 2".
    if not dealer or not has_banner_label(text):
        return None
    return ParsedForm(
        dealer=dealer.upper().replace(" ", ""),
        outlet_name=outlet_name,
        phone=_extract_line(text, FIELD_NAMES["phone"]),
        outlet_type=normalize_outlet_type(_extract_line(text, FIELD_NAMES["outlet_type"])),
        banner_text=banner_text,
        product_display=parse_product_display(text),
        remark_issues=_extract(text, [
            "remark issues", "remark issue", "*remark issues", "*remark issue",
            "remake issues", "remake issue", "remake iusse", "remark iusse",
        ]),
    )


def parse_product_display_form(text: str) -> ParsedForm | None:
    """Parse a central Product Display report without requiring Banner Type.

    The dedicated ``Display Product`` group is an independent source. Some
    forwarded reports contain only Dealer, Outlet, and Product Display fields,
    so Product Display collection must not depend on a Banner label.
    """
    dealer = _extract_line(text, FIELD_NAMES["dealer"])
    outlet_name = _extract_line(text, FIELD_NAMES["outlet_name"])
    product_display = parse_product_display(text)
    if not dealer or not outlet_name or product_display != "Yes":
        return None
    return ParsedForm(
        dealer=dealer.upper().replace(" ", ""),
        outlet_name=outlet_name,
        phone=_extract_line(text, FIELD_NAMES["phone"]),
        outlet_type=normalize_outlet_type(_extract_line(text, FIELD_NAMES["outlet_type"])),
        banner_text="",
        product_display=product_display,
        remark_issues=_extract(text, [
            "remark issues", "remark issue", "*remark issues", "*remark issue",
            "remake issues", "remake issue", "remake iusse", "remark iusse",
        ]),
    )


def product_display_count(value: object) -> int:
    normalized = clean_space("" if value is None else str(value)).casefold()
    # Stored history normally contains ``Yes``.  New Product Display detail
    # exports use numeric 1, while old workbooks may contain text "1".  Accept
    # all three representations so Combined exports remain backward compatible.
    return 1 if normalized in {"yes", "1", "true"} else 0


def normalized_product_display(value: str) -> str:
    return "Yes" if product_display_count(value) else ""


class BannerNormalizer:
    def __init__(self, aliases_path: Path):
        raw = json.loads(aliases_path.read_text(encoding="utf-8"))
        pairs: list[tuple[str, str]] = []
        for official, aliases in raw.items():
            for alias in set([official, *aliases]):
                pairs.append((self._key(alias), official))
        self.aliases = sorted(pairs, key=lambda item: len(item[0]), reverse=True)
        override_path = aliases_path.with_name("banner_identity_overrides.json")
        self.identity_overrides = (
            json.loads(override_path.read_text(encoding="utf-8"))
            if override_path.is_file() else []
        )

    @staticmethod
    def _identity_name(value: object) -> str:
        return clean_space(str(value or "")).casefold()

    @staticmethod
    def _identity_phone(value: object) -> str:
        text = clean_space(str(value or "")).upper()
        digits = re.sub(r"\D", "", text)
        return digits or re.sub(r"[^A-Z0-9]", "", text)

    def record_override(
        self, outlet_name: object, phone: object,
        latitude: object, longitude: object,
    ) -> dict | None:
        name_key = self._identity_name(outlet_name)
        phone_key = self._identity_phone(phone)
        try:
            lat = float(latitude)
            lon = float(longitude)
        except (TypeError, ValueError):
            return None
        for value in self.identity_overrides:
            if not isinstance(value, dict):
                continue
            if self._identity_name(value.get("outlet_name")) != name_key:
                continue
            if self._identity_phone(value.get("phone")) != phone_key:
                continue
            if abs(float(value.get("latitude")) - lat) > 0.00002:
                continue
            if abs(float(value.get("longitude")) - lon) > 0.00002:
                continue
            return value
        return None

    def display_text_for_record(
        self, outlet_name: object, phone: object, latitude: object,
        longitude: object, banner_text: str,
    ) -> str:
        override = self.record_override(outlet_name, phone, latitude, longitude)
        if override and clean_space(str(override.get("banner_text") or "")):
            return str(override["banner_text"])
        return banner_text or ""

    def parse_for_record(
        self, outlet_name: object, phone: object, latitude: object,
        longitude: object, banner_text: str,
    ) -> list[BannerItem]:
        """Parse one report, applying only approved message-specific corrections.

        Some historical SMR5 captions contain the same generic text
        (for example ``Soft Drink 1``) but have different verified banners.
        Verified Outlet Name + Phone + Latitude + Longitude distinguish them.
        """
        override = self.record_override(outlet_name, phone, latitude, longitude)
        items = override.get("items") if override else None
        if isinstance(items, dict):
            return [
                BannerItem(str(name), int(quantity), banner_text or str(name))
                for name, quantity in items.items()
                if int(quantity) > 0
            ]
        return self.parse(banner_text)

    @staticmethod
    def _key(value: str) -> str:
        value = unicodedata.normalize("NFKC", value).upper()
        return re.sub(r"[^A-Z0-9.]", "", value)

    def parse(self, banner_text: str) -> list[BannerItem]:
        if not clean_space(banner_text):
            return []
        if is_non_banner_text(banner_text):
            return []
        # New mobile shorthand: a leading quantity followed by "CB Water"
        # means Water only. Keep this narrow because the historical suffix
        # form "CB Water 1m" intentionally maps to CBC + Water.
        cb_water_prefix = re.fullmatch(
            r"(?is)\s*0*(\d+)\s*CB\s+WATER\s*", banner_text
        )
        if cb_water_prefix:
            quantity = int(cb_water_prefix.group(1))
            return [BannerItem("Water", quantity, banner_text)] if quantity > 0 else []
        # CCL is an explicit short code for C-Cola. Unlike ordinary repeated
        # brand text, every repeated CCL entry is a separately reported banner
        # and must be summed: CCL:1=CCL:1 and CCL:1/CCL:1 both mean C-Cola=2.
        ccl_values = re.findall(r"(?i)(?<![A-Z])CCL\s*[:=]?\s*(\d+)", banner_text)
        ccl_remainder = re.sub(
            r"(?i)(?<![A-Z])CCL\s*[:=]?\s*\d+", "", banner_text
        )
        if ccl_values and not re.sub(r"[\s=/,;|]+", "", ccl_remainder):
            quantity = sum(int(value) for value in ccl_values)
            return [BannerItem("C-Cola", quantity, banner_text)] if quantity > 0 else []
        # Historical free-text category entry: both categories explicitly
        # report zero, so it must not create the normal Soft Drink default.
        if re.fullmatch(
            r"(?is)\s*soft\s*drink\s*(?:[:=]\s*0\s*)?&\s*beer\s*[:=]\s*0\s*",
            banner_text,
        ):
            return []

        # Khmer field shorthand used for a new Water banner.
        khmer_water = re.fullmatch(r"(?is)\s*new\s*ទឹកសុទ្ធ\s*[:=]?\s*(\d+)\s*", banner_text)
        if khmer_water:
            quantity = int(khmer_water.group(1))
            return [BannerItem("Water", quantity, banner_text)] if quantity > 0 else []
        # The comma in MDK2's 'CB4,4:1' belongs to the brand, not a
        # separator or a quantity of 4/41. Protect it before token splitting.
        banner_text = re.sub(r"(?i)\bCB\s*4\s*,\s*4\b", "CB 4.4", banner_text)
        banner_text = re.sub(r"(?i)\bCB\s+Spot\b", "C-Sport", banner_text)
        # Historical forms sometimes use a comma as the decimal separator
        # (0,8m). Protect it before commas are treated as list separators.
        banner_text = re.sub(r"(?<=\d),(?=\d\s*(?:m|cm|mm)\b)", ".", banner_text, flags=re.I)
        # Currency/size noise can appear between a brand and its real count:
        # ``CBC 3333$=1`` means one CBC banner, not 33,331 banners.
        banner_text = re.sub(r"\b\d{3,}\s*\$\s*=\s*(\d+)\b", r"\1", banner_text)
        # Verified field typo: "Wurkz Ize" means the single brand Wurkz Ice,
        # not separate Wurkz and IZE banners. Keep this phrase-level correction
        # narrow so legitimate standalone Wurkz and IZE entries are unchanged.
        banner_text = re.sub(r"(?i)\bwurkz\s+ize\b", "Wurkz Ice", banner_text)
        # Keep punctuation inside aliases (C.Cola, CB 4.4), but split normal
        # list separators. Ampersand and the word "and" also mean two banners.
        tokens = [clean_space(x) for x in re.split(r"[,;/|\n&]+|\bAND\b", banner_text, flags=re.I) if clean_space(x)]
        composite_mode = len(re.findall(
            r"(?i)(?:^|[/|])\s*(?:beer|soft\s*drink|water)\b", banner_text
        )) >= 2
        found: list[BannerItem] = []
        for token in tokens:
            # Promotional event wording is not an installed beverage banner.
            # Verified examples: "Wurkz Special Volleyball cup" and the same
            # phrase followed by 1. They must contribute zero, not Wurkz=1.
            if re.fullmatch(
                r"(?i)\s*wurkz\s+special\s+volleyball\s+cup"
                r"(?:\s*[:=x]?\s*1)?\s*",
                token,
            ):
                continue
            # Valid size continuation from text such as
            # "1(CB 4.4, 1m x 3m)"; it is not a second banner or an error.
            if re.fullmatch(
                r"(?i)\s*\d+(?:[.,]\d+)?\s*(?:m\s*)?x\s*\d+(?:[.,]\d+)?\s*m\s*\)?\s*",
                token,
            ):
                continue

            # Special event banners are tracked separately from the 15 official
            # beverage brands. 3m/4m is a size; a number after ':' is quantity.
            special = re.fullmatch(
                r"(?i)\s*banner\s*(concert|theat(?:er|re))\s*"
                r"(?:\d+(?:[.,]\d+)?\s*m)?\s*(?:(?:[:=x])\s*(\d+))?\s*",
                token,
            )
            if special:
                name = "Banner Concert" if special.group(1).casefold() == "concert" else "Banner Theater"
                found.append(BannerItem(name, int(special.group(2) or "1"), token, "SPECIAL"))
                continue

            # Old category-style reports must not turn text such as
            # "Water: Vikinkz" into Water=1. Only a positive number is valid.
            if composite_mode:
                category = re.fullmatch(
                    r"(?i)\s*(beer|soft\s*drink|water)\s*[:.]*\s*(.*?)\s*", token
                )
                if category:
                    label = re.sub(r"\s+", " ", category.group(1).casefold())
                    value = clean_space(category.group(2))
                    placeholder = re.sub(r"[\s.……_-]+", "", value.casefold())
                    if placeholder in {"", "0", "00", "newbanner", "n/a", "na", "none", "nil", "null"}:
                        continue
                    number = re.fullmatch(r"0*(\d+)(?:\.0+)?", value)
                    if not number or int(number.group(1)) <= 0:
                        found.append(BannerItem("Unrecognized", 0, token, "NEED_REVIEW_COMPOSITE_VALUE"))
                        continue
                    quantity = int(number.group(1))
                    if label == "beer":
                        found.append(BannerItem("CB Wedding", quantity, token))
                    elif label == "water":
                        found.append(BannerItem("Water", quantity, token))
                    elif quantity == 2:
                        found.extend([
                            BannerItem("C-ED", 1, token),
                            BannerItem("Dazz Zero", 1, token),
                        ])
                    else:
                        found.append(BannerItem("Unrecognized", 0, token, "NEED_REVIEW_SOFT_DRINK_QUANTITY"))
                    continue

            generic = clean_space(token).casefold()
            # A comma is sometimes inserted between one brand and its quantity:
            # "CB4.4, 1" / "CBC 4.4, 1". Apply the standalone number to the
            # preceding recognized banner instead of creating NEED_REVIEW.
            standalone_quantity = re.fullmatch(r"0*(\d+)", generic)
            if standalone_quantity and found and found[-1].status == "OK":
                quantity = int(standalone_quantity.group(1))
                if quantity > 0:
                    previous = found[-1]
                    found[-1] = BannerItem(
                        previous.official_name, quantity,
                        previous.original_token + " + " + token, previous.status,
                    )
                    continue
            # Historical one-letter entry: only an exact standalone I means IZE.
            # Do not add I as a general alias because it would match ordinary words.
            if self._key(token) == "I":
                found.append(BannerItem("IZE", 1, token))
                continue
            if re.fullmatch(r"(?:yes|ds|rt|1|0*1\s*new)", generic):
                found.append(BannerItem("Unrecognized", 0, token, "NEED_REVIEW_GENERIC_VALUE"))
                continue

            # Business composite:
            # - "Soft Drink" / "Soft Drink2" represents C-ED + Dazz Zero.
            # - The supplied Banner Type reference explicitly contains
            #   "Soft Drink1" => IZE, so keep that historical case.
            soft_match = re.fullmatch(r"(?i)\s*soft\s*drink(?:\s*[x=:]?\s*(\d+))?\s*", token)
            if soft_match:
                total = int(soft_match.group(1) or "2")
                if total == 1:
                    found.append(BannerItem("IZE", 1, token))
                elif total == 2:
                    found.extend([
                        BannerItem("C-ED", 1, token),
                        BannerItem("Dazz Zero", 1, token),
                    ])
                else:
                    found.append(BannerItem("Unrecognized", 0, token, "REVIEW_SOFT_DRINK_QUANTITY"))
                continue

            # One historical source row contains exactly "New 1" and is
            # mapped to Dazz in the supplied reference cases. Keep the rule
            # narrow so generic values such as "1 New" remain NEED_REVIEW.
            if re.fullmatch(r"(?i)\s*new\s+1\s*", token):
                found.append(BannerItem("Dazz", 1, token))
                continue
            # Verified historical shorthand: one New 2 report represents two
            # different installed banners, not quantity two of one brand.
            if re.fullmatch(r"(?i)\s*new\s*2\s*", token):
                found.extend([
                    BannerItem("CB Wedding", 1, token),
                    BannerItem("Wurkz Ice", 1, token),
                ])
                continue
            # Keep this narrow: a standalone W1 is Wurkz=1. Do not make W a
            # general alias because it occurs in many unrelated words.
            if re.fullmatch(r"(?i)\s*w\s*1\s*", token):
                found.append(BannerItem("Wurkz", 1, token))
                continue

            compact = self._key(token)
            prefix_quantity = None
            prefix = re.match(r"^(\d+)(?=[A-Z])", compact)
            if prefix:
                # In "2(Lite, Wurkz: 0.8m x 2m)", 2 describes the two
                # listed brands/layout, not CB Lite quantity.
                prefix_quantity = None if re.match(r"^\s*\d+\s*\(", token) else int(prefix.group(1))
                compact = compact[prefix.end():]
            # Find every recognized brand, not only the brand at the beginning.
            # This handles C.Cola1.Exprezz1, BeerCambodia1IZE and similar data.
            matches: list[tuple[int, str, str]] = []
            cursor = 0
            while cursor < len(compact):
                match = next(
                    ((alias, official) for alias, official in self.aliases
                     if compact.startswith(alias, cursor)),
                    None,
                )
                if match:
                    alias, official = match
                    matches.append((cursor, alias, official))
                    cursor += len(alias)
                else:
                    cursor += 1

            if not matches:
                found.append(BannerItem("Unrecognized", 0, token, "REVIEW_UNKNOWN_BANNER"))
                continue

            for index, (start, alias, official) in enumerate(matches):
                end = matches[index + 1][0] if index + 1 < len(matches) else len(compact)
                suffix = compact[start + len(alias):end]
                # Package sizes are descriptors, not quantities. Because _key()
                # removes spaces, "CB Cola 330 1" becomes a suffix of "3301".
                # Preserve only the final report quantity: C-Cola=1.
                package_quantity = re.fullmatch(r"(?:250|300|330|350|500|1500)(\d+)", suffix)
                if package_quantity:
                    suffix = package_quantity.group(1)
                elif re.fullmatch(r"(?:250|300|330|350|500|1500)", suffix):
                    suffix = ""
                # Field shorthand ``1 m 2`` means a 1m x 2m banner size,
                # therefore the report quantity is one, not two.
                elif re.fullmatch(r"1M2", suffix):
                    suffix = ""
                # In the observed combined text ``Water 1 2 Sport ...``, the
                # first 1 is a stray size/count marker and the final 2 is the
                # intended Water quantity. Keep this exception Water-specific.
                elif official == "Water" and suffix == "12":
                    suffix = "2"
                # ``ED 08`` and ``Dazz 08`` are common 0.8m shorthand with
                # the decimal omitted. They describe size and mean one banner.
                elif official in {"C-ED", "Dazz"} and suffix == "08":
                    suffix = ""
                # 0.8/0,8/1.8m are dimensions and mean one banner, not 8 or 18.
                suffix = re.sub(r"\d+[.,]\d+(?:M|CM|MM)?", "", suffix)
                # 250ml, layout dimensions such as 2m, and 8h are descriptors,
                # not quantities.
                suffix = re.sub(r"\d+(?:ML|CM|MM|M|L|H)", "", suffix)
                numbers = re.findall(r"\d+", suffix)
                quantity = prefix_quantity if index == 0 and prefix_quantity is not None else (int(numbers[-1]) if numbers else 1)
                if quantity <= 0:
                    found.append(BannerItem(official, 0, token, "REVIEW_ZERO_QUANTITY"))
                else:
                    found.append(BannerItem(official, quantity, token))

        combined: dict[tuple[str, str], BannerItem] = {}
        for item in found:
            key = (item.official_name, item.status)
            old = combined.get(key)
            if old and item.status in {"OK", "SPECIAL"}:
                # Repeated same Banner Type in one form is a duplicate entry, not extra stock.
                combined[key] = BannerItem(
                    item.official_name,
                    max(old.quantity, item.quantity),
                    old.original_token + " + " + item.original_token,
                    item.status,
                )
            else:
                combined[key] = item
        return list(combined.values())


def validate_report(text: str, expected_dealer: str, normalizer: BannerNormalizer) -> ValidationResult:
    text = text or ""
    positions = [(label, (m.start() if (m := re.search(pattern, text)) else -1))
                 for label, pattern in FORMAT_FIELDS]
    banner_field = _find_banner_field(text)
    if banner_field:
        positions = [
            (label, banner_field[0] if label == "Banner Type" and position < 0 else position)
            for label, position in positions
        ]
    present_count = sum(position >= 0 for _, position in positions)
    candidate = present_count >= 2 or bool(re.search(r"(?im)^\s*\.?[ \t]*dealer\s*:", text)) or has_banner_label(text)
    if not candidate:
        return ValidationResult(False, False, (), (), None, ())

    errors: list[str] = []
    codes: list[str] = []
    missing = [label for label, position in positions if position < 0]
    if missing:
        errors.append("Missing field label: " + ", ".join(missing))
        codes.append("MISSING_LABEL")

    # POSM and PROGRAM are commonly swapped in field reports; both orders are valid.
    order_labels = {"Dealer", "Outlet Name", "Phone", "Outlet Type", "Banner Type",
                    "Product Display", "Remark Issues"}
    found_positions = [position for label, position in positions
                       if position >= 0 and label in order_labels]
    if found_positions != sorted(found_positions):
        errors.append("Fields are not in the required order")
        codes.append("WRONG_ORDER")

    form = parse_form(text)
    if form is None:
        if not missing:
            errors.append("Dealer or Outlet Name value is blank")
            codes.append("MISSING_REQUIRED_VALUE")
        return ValidationResult(True, False, tuple(errors), tuple(codes), None, ())

    required_values = []
    if not form.dealer: required_values.append("Dealer")
    if not form.outlet_name: required_values.append("Outlet Name")
    if not form.outlet_type: required_values.append("Outlet Type")
    # Banner Type may be blank for a valid Product Display report.
    if not form.banner_text and not product_display_count(form.product_display):
        required_values.append("Banner Type or Product Display: Yes")
    if required_values:
        errors.append("Required value is blank: " + ", ".join(required_values))
        codes.append("MISSING_REQUIRED_VALUE")

    if form.dealer != expected_dealer.upper():
        errors.append(f"Dealer must be {expected_dealer.upper()} for this group (received {form.dealer})")
        codes.append("DEALER_MISMATCH")

    items = tuple(normalizer.parse(form.banner_text))
    bad_items = [item.original_token for item in items if item.status not in {"OK", "SPECIAL"}]
    if bad_items:
        errors.append("Unknown banner or invalid quantity: " + ", ".join(bad_items))
        codes.append("INVALID_BANNER")

    return ValidationResult(True, not errors, tuple(errors), tuple(codes), form, items)
