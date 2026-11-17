from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# ============================================================
# OUTLET VISIT METRICS
# ============================================================

METRICS = [
    (
        "emergency_outlet",
        "Emergency Outlet",
        [
            "Emergency Outlet",
            "EmergencyOutlet",
            # Common field-report typo.  The extractor already tolerates a
            # missing colon, so this covers both ``Emergency Outle : 1`` and
            # ``Emergency Outle 1`` (plus case/spacing variants).
            "Emergency Outle",
        ],
    ),
    (
        "attacking_stock",
        "Attacking Stock",
        [
            "Attacking Stock",
            "AttackingStock",
        ],
    ),
    (
        "tour_program",
        "Tour Program",
        [
            "Tour Program",
            "TourProgram",
        ],
    ),
    (
        "key_wholesale",
        "Key Wholesale",
        [
            "Key Wholesale",
            "KeyWholesale",
        ],
    ),
    (
        "water",
        "Water",
        [
            "Water",
        ],
    ),
    (
        "c_cola",
        "C-Cola",
        [
            "C-Cola",
            "C Cola",
            "C.Cola",
            "CCola",
        ],
    ),
    (
        "ize_pet_300ml",
        "Ize PET 300mL",
        [
            "Ize PET 300mL",
            "IzePET300mL",
            "IZE PET 300 ml",
        ],
    ),
    (
        "ize_pet_1500ml",
        "Ize PET 1500mL",
        [
            "Ize PET 1500mL",
            "IzePET1500mL",
            "IZE PET 1500 ml",
            "Ize PET 1.5L",
        ],
    ),
]


# ============================================================
# BASE FIELDS
# ============================================================

BASE_FIELDS = [
    (
        "dealer",
        [
            "Dealer",
        ],
    ),
    (
        "outlet_name",
        [
            "Outlet Name",
            "OutletName",
        ],
    ),
    (
        "phone",
        [
            "Phone",
            "Telephone",
            "Tel",
        ],
    ),
    (
        "outlet_type",
        [
            "Outlet Type",
            "OutletType",
        ],
    ),
]


# ============================================================
# NORMALIZE TEXT
# ============================================================

def _normalize(text: str) -> str:
    """
    Normalize Unicode and remove invisible characters.
    """

    if not text:
        return ""

    return (
        unicodedata
        .normalize("NFKC", str(text))
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\ufeff", "")
    )


def normalize_outlet_type(value: str) -> str:
    """Normalize human-entered Outlet Type values to four report categories.

    Punctuation, slashes, dots, whitespace, and letter case are ignored. This
    makes examples such as ``W/S``, ``w.s``, ``R/T``, ``R.t``, ``D/s`` and
    ``o/N`` deterministic while preserving an unknown value for review.
    """
    cleaned = _normalize(value).strip()
    key = "".join(character for character in cleaned.casefold() if character.isalnum())
    aliases = {
        "ws": "WS",
        "wholesale": "WS",
        "wholesales": "WS",
        "rt": "Retail",
        "rt24h": "Retail",
        "re": "Retail",
        "reat": "Retail",
        "retail": "Retail",
        "retial": "Retail",
        "retailer": "Retail",
        "ds": "DS",
        "da": "DS",
        "drinkshop": "DS",
        "directsale": "DS",
        "directsales": "DS",
        "on": "ON",
        "gton": "ON",
        "horeca": "ON",
        "horica": "ON",
        "localeat": "ON",
        "outlet24h": "ON",
        "sportclub": "ON",
        "sportsclub": "ON",
        "ontrade": "ON",
        "outletnetwork": "ON",
    }
    return aliases.get(key, cleaned)


# ============================================================
# ALIAS PATTERN
# ============================================================

def _alias_pattern(alias: str) -> str:
    """
    Convert alias into flexible regex.

    Examples:

        C-Cola
        C Cola
        C.Cola
        CCola

    can all be matched.
    """

    parts = []

    for character in alias:

        if character.isalnum():
            parts.append(re.escape(character))

        else:
            parts.append(r"(?:[ \t._-]|\\)*")

    return "".join(parts)


# ============================================================
# EXTRACT FIELD
# ============================================================

def _extract_line(
    text: str,
    aliases: list[str],
) -> tuple[bool, str]:
    """
    Extract field value from one line.

    Supports:

        Outlet: No
        Outlet : No
        Outlet=No
        Outlet - No
        Outlet\\:No
        Outlet\\:NO
        Outlet:No
    """

    names = "|".join(
        _alias_pattern(alias)
        for alias in sorted(
            aliases,
            key=len,
            reverse=True,
        )
    )

    match = re.search(
        rf"(?im)"
        rf"^[ \t]*"
        rf"\*?[ \t]*"
        rf"(?:{names})"
        rf"[ \t\\]*"
        rf"(?::|=|-)?"
        rf"[ \t\\]*"
        rf"(.*?)[ \t]*$",
        _normalize(text),
    )

    if not match:
        return False, ""

    value = match.group(1).strip()

    return True, value



# ============================================================
# REMARK ISSUES
# ============================================================

_REMARK_LABEL_RE = re.compile(
    r"(?im)^\s*\*?\s*(?:remark|remake)\s*(?:issues?|issue|iusse|issuee|isue)"
    r"\s*(?:(?::|=|-)\s*(.*?))?\s*$"
)


def _extract_remark_issues(text: str) -> str:
    """Extract *Remark Issues from an Outlet Visit message.

    Supports examples such as:
        *Remark Issues: CBB CB Exprez melon, Dazz, Wurkz Ize, .CW លក់យឺត.អត់មាន C.cola
        Remark Issues: ...
        *Remark issue: ...

    If the label is present with no value, returns an empty string.
    """
    normalized = _normalize(text)
    matches = list(_REMARK_LABEL_RE.finditer(normalized))
    if not matches:
        return ""

    values = []
    for match in matches:
        value = (match.group(1) or "").strip()
        if value:
            values.append(value)

    # Keep the original text, including Khmer, but normalize whitespace.
    return " | ".join(dict.fromkeys(values))


# ============================================================
# CLEAN BINARY VALUE
# ============================================================

def _clean_binary_value(value: str) -> str:
    r"""
    Normalize a binary value before converting it to 0/1.

    Supported ZERO forms:
        No / NO / no
        N / n
        O / o
        0 / 0.0 / 0.00 / 0.000
        False / FALSE
        None / Nil / Null
        NA / N/A
        -
        អត់

    Wrapped ZERO forms:
        Outlet: No
        Outlet: NO
        Outlet: o
        Outlet: O
        Outlet\\:No
        Outlet\\:NO
        Outlet\\:o
        Outlet \\: No
        Outlet \\ : No
        Outlet\\:No
        Outlet + multiple backslashes + :No
        Outlet + multiple backslashes + :NO
        Outlet + multiple backslashes + :o

    Empty Outlet wrappers are also ZERO:
        Outlet:
        Outlet :
        Outlet\\:
        Outlet \\:
        Outlet \ :
        Outlet\\:
        Outlet + multiple backslashes + :

    The function intentionally accepts any number of backslashes
    before the colon.
    """

    if value is None:
        return ""

    value = _normalize(value).strip()

    if not value:
        return ""

    # --------------------------------------------------------
    # Normalize ANY number of backslashes before ':'.
    #
    #   \:
    #   \\:
    #   \\\:
    #   \ :
    #   \\ :
    #
    # all become:
    #
    #   :
    # --------------------------------------------------------
    value = re.sub(r"\\+\s*:", ":", value)

    # Collapse repeated backslashes that are not part of a colon.
    # Use a callable replacement so the replacement is always a
    # literal backslash and never a regex replacement escape.
    value = re.sub(
        r"\\{2,}",
        lambda _m: "\\",
        value,
    ).strip()

    # --------------------------------------------------------
    # Remove one or more Outlet wrappers.
    #
    # This handles:
    #   Outlet:No
    #   Outlet: NO
    #   Outlet: o
    #   Outlet:Outlet:No
    #
    # and the escaped forms after the colon normalization above.
    # --------------------------------------------------------
    outlet_pattern = re.compile(
        r"""
        ^\s*
        Outlet
        \s*
        (?:
            :
            |
            =
            |
            -
        )
        \s*
        (.*?)
        \s*$
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for _ in range(5):
        before = value

        match = outlet_pattern.match(value)
        if match:
            value = match.group(1).strip()
        else:
            break

        if value == before:
            break

        value = re.sub(r"\\+\s*:", ":", value).strip()
        value = value.strip("\\").strip()

    # Final cleanup.
    value = value.strip().strip("\\").strip()

    return value


def _binary_quantity(
    value: str,
) -> tuple[int, str]:
    """
    Convert a raw binary value into 0/1.

    ZERO:
        No, NO, no
        N, n
        O, o
        0, 0.0, 0.00, 0.000
        False
        None, Nil, Null
        NA, N/A
        -
        អត់
        Outlet: No
        Outlet\\:No
        Outlet + multiple backslashes + :No
        Outlet: NO
        Outlet + multiple backslashes + :NO
        Outlet: o
        Outlet + multiple backslashes + :o
        Outlet:
        Outlet + multiple backslashes + :

    ONE:
        Yes, YES, yes
        Y, y
        True, TRUE
        1, 1.0, 1.00
        មាន

    POSITIVE NUMBERS:
        any number > 0 -> 1

    NEGATIVE / ZERO NUMBERS:
        any number <= 0 -> 0

    OTHER NON-EMPTY TEXT:
        -> 1
    """

    cleaned = _clean_binary_value(value)

    if not cleaned:
        return 0, "BLANK"

    normalized = cleaned.casefold().strip()
    normalized = normalized.strip("\\").strip()

    if not normalized:
        return 0, "BLANK"

    zero_values = {
        "0",
        "0.0",
        "0.00",
        "no",
        "n",
        "o",
        "false",
        "none",
        "nil",
        "null",
        "na",
        "n/a",
        "-",
        "អត់",
    }

    if normalized in zero_values:
        return 0, "OK"

    one_values = {
        "1",
        "1.0",
        "1.00",
        "yes",
        "y",
        "true",
        "មាន",
    }

    if normalized in one_values:
        return 1, "OK"

    numeric_match = re.fullmatch(
        r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)",
        normalized,
    )

    if numeric_match:
        try:
            number = float(normalized)
            return (0, "OK") if number <= 0 else (1, "OK")
        except (ValueError, OverflowError):
            pass

    # --------------------------------------------------------
    # Defensive final wrapper check.
    #
    # This makes it impossible for an Outlet: negative value to
    # fall through to the "other text -> 1" rule.
    # --------------------------------------------------------
    safety_value = _normalize(value).strip()
    safety_value = re.sub(r"\\+\s*:", ":", safety_value)
    safety_value = re.sub(
        r"\\{2,}",
        lambda _m: "\\",
        safety_value,
    ).strip()

    outlet_pattern = re.compile(
        r"""
        ^\s*
        Outlet
        \s*
        (?:
            :
            |
            =
            |
            -
        )
        \s*
        (.*?)
        \s*$
        """,
        flags=re.IGNORECASE | re.VERBOSE,
    )

    for _ in range(5):
        match = outlet_pattern.match(safety_value)
        if not match:
            break

        safety_value = match.group(1).strip()
        safety_value = re.sub(
            r"\\+\s*:",
            ":",
            safety_value,
        ).strip().strip("\\").strip()

    if not safety_value:
        return 0, "BLANK"

    remaining = safety_value.casefold().strip()

    if remaining in zero_values:
        return 0, "OK"

    if remaining in one_values:
        return 1, "OK"

    numeric_match = re.fullmatch(
        r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)",
        remaining,
    )

    if numeric_match:
        try:
            number = float(remaining)
            return (0, "OK") if number <= 0 else (1, "OK")
        except (ValueError, OverflowError):
            pass

    # Any other non-empty text -> 1.
    return 1, "OK"
# ============================================================
# DATA CLASSES
# ============================================================

@dataclass(frozen=True)
class OutletVisit:

    dealer: str
    outlet_name: str
    phone: str
    outlet_type: str

    raw_values: dict[str, str]
    quantities: dict[str, int]
    field_statuses: dict[str, str]
    remark_issues: str

    parse_status: str


@dataclass(frozen=True)
class OutletVisitResult:

    is_candidate: bool
    visit: OutletVisit | None


# ============================================================
# PARSE OUTLET VISIT
# ============================================================

def parse_outlet_visit(
    text: str,
) -> OutletVisitResult:

    text = _normalize(text)

    metric_raw: dict[str, str] = {}
    metric_quantities: dict[str, int] = {}
    metric_statuses: dict[str, str] = {}

    present = 0

    # ========================================================
    # PARSE 8 METRICS
    # ========================================================

    for key, _label, aliases in METRICS:

        found, value = _extract_line(
            text,
            aliases,
        )

        if found:

            present += 1

            metric_raw[key] = value

            quantity, field_status = (
                _binary_quantity(value)
            )

            metric_quantities[key] = quantity

            metric_statuses[key] = field_status

        else:

            metric_raw[key] = ""

            metric_quantities[key] = 0

            metric_statuses[key] = "MISSING_FIELD"

    # ========================================================
    # CANDIDATE DETECTION
    # ========================================================

    # At least 3 unique metric labels.
    if present < 3:

        return OutletVisitResult(
            is_candidate=False,
            visit=None,
        )

    # ========================================================
    # BASE FIELDS
    # ========================================================

    base: dict[str, str] = {}

    missing_base: list[str] = []

    for key, aliases in BASE_FIELDS:

        found, value = _extract_line(
            text,
            aliases,
        )

        base[key] = value

        # Required fields
        if key in {
            "dealer",
            "outlet_type",
        }:

            if not found or not value:
                missing_base.append(key)

    # ========================================================
    # PARSE STATUS
    # ========================================================

    status = "OK"

    # Not all 8 metrics found.
    if present < len(METRICS):
        status = "NEED_REVIEW"

    # Required base field missing.
    if missing_base:
        status = "NEED_REVIEW"

    # ========================================================
    # IMPORTANT:
    #
    # Metric values NEVER create NEED_REVIEW.
    #
    # Valid:
    #
    #   0
    #   No
    #   NO
    #   o
    #   O
    #   Outlet: No
    #   Outlet\\:No
    #   5
    #   Gold
    #
    # Only missing metric labels or required base fields
    # create NEED_REVIEW.
    # ========================================================

    # ========================================================
    # NORMALIZE DEALER
    # ========================================================

    dealer = (
        base["dealer"]
        .upper()
        .replace(" ", "")
    )

    # ========================================================
    # BUILD RESULT
    # ========================================================

    visit = OutletVisit(
        dealer=dealer,
        outlet_name=base["outlet_name"],
        phone=base["phone"],
        outlet_type=normalize_outlet_type(base["outlet_type"]),
        raw_values=metric_raw,
        quantities=metric_quantities,
        field_statuses=metric_statuses,
        remark_issues=_extract_remark_issues(text),
        parse_status=status,
    )

    return OutletVisitResult(
        is_candidate=True,
        visit=visit,
    )
