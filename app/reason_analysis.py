"""Categorize missing-report explanations using deterministic Khmer and English rules."""
import re

OUTLET_FORM = re.compile(r"(?i)\b(?:dealer\s*:|outlet\s*(?:name|type)\s*:|posm\s*:|product\s*display\s*:|remark\s*issues\s*:)")
LINK_OR_LOCATION = re.compile(r"(?i)^\s*(?:https?://|\[\s*location\s*\])")
SUMMARY_VERSION = "rules-km-v1"

KHMER_SUMMARIES = {
    "report_evidence": "បានផ្ញើរបាយការណ៍ ឬទីតាំង ប៉ុន្តែមិនបានបញ្ជាក់មូលហេតុដែលខកខានរាយការណ៍។",
    "no_reason": "មិនបានបញ្ជាក់មូលហេតុដែលខកខានរាយការណ៍។",
    "connectivity": "កំពុងធ្វើការនៅតំបន់ដែលគ្មានសេវាអ៊ីនធឺណិត ឬសេវាទូរស័ព្ទ។",
    "field_work": "កំពុងដំឡើងបដា ឬចរចាជាមួយអតិថិជននៅហាង។",
    "travel_distance": "បានបញ្ជាក់ថាត្រូវធ្វើដំណើរផ្លូវឆ្ងាយរវាងទីតាំងហាង។",
    "dealer_meeting": "កំពុងចូលរួមប្រជុំជាមួយដេប៉ូ។",
    "illness": "បានបញ្ជាក់ថាមានជំងឺ ឬកំពុងឈឺ។",
    "review": "ត្រូវការពិនិត្យបន្ថែម ដើម្បីបញ្ជាក់មូលហេតុដែលខកខានរាយការណ៍។",
}


def _fallback_reason(text):
    value = " ".join(str(text or "").split())
    if not value or OUTLET_FORM.search(value) or LINK_OR_LOCATION.match(value):
        return {
            "is_reason": False,
            "summary": "Outlet report/evidence submitted, not a stated reason",
            "category": "report_evidence",
            "method": "rules",
        }
    if (len(value) < 9 and not re.search(r"[\u1780-\u17b3]", value)) or value.casefold() in (
        "yes", "ok", "done", "thanks", "បាន", "អត់", "សួស្តី", "សួស្ដី", "ជំរាបសួរ",
    ):
        return {
            "is_reason": False,
            "summary": "No explanation provided",
            "category": "no_reason",
            "method": "rules",
        }
    lower = value.casefold()
    if value == "ឈឺ" or any(x in value for x in ("ខ្ញុំឈឺ", "ខ្ញុំមានជំងឺ")):
        return {
            "is_reason": True,
            "summary": "The employee reports being ill.",
            "category": "illness",
            "method": "rules",
        }
    if any(x in lower for x in ("អត់សេវា", "អត់សេវ៉ា", "គ្មានសេវា", "គ្មានសេវ៉ា", "no signal", "no internet", "network")):
        return {
            "is_reason": True,
            "summary": "Working in an area without network coverage",
            "category": "connectivity",
            "method": "rules",
        }
    if any(x in lower for x in ("បាញ់banner", "បាញ់ banner", "ចរចា", "banner", "negotiat")):
        return {
            "is_reason": True,
            "summary": "Installing banners or negotiating with outlets",
            "category": "field_work",
            "method": "rules",
        }
    if any(x in lower for x in ("ផ្លូវឆ្ងាយ", "ផ្លូវ ឆ្ងាយ", "ឆ្ងាយ", "long distance", "far away", "far from", "traveling far")):
        return {
            "is_reason": True,
            "summary": "The employee mentions long-distance travel between outlet locations.",
            "category": "travel_distance",
            "method": "rules",
        }
    if any(x in lower for x in ("meeting with dealer", "meeting dealer", "ប្រជុំជាមួយដេប៉ូ", "ប្រជុំជាមួយ dealer")):
        return {
            "is_reason": True,
            "summary": "Attending a meeting with the dealer.",
            "category": "dealer_meeting",
            "method": "rules",
        }
    return {
        "is_reason": None,
        "summary": "Requires review of the employee explanation",
        "category": "review",
        "method": "rules",
    }


def fallback_reason(text):
    result = _fallback_reason(text)
    return {
        **result,
        "summary_en": result["summary"],
        "summary_km": KHMER_SUMMARIES.get(result["category"], KHMER_SUMMARIES["review"]),
        "summary_version": SUMMARY_VERSION,
        "ai_status": "rules",
    }


def analyze_reason(text):
    """Categorize a missed-report explanation using deterministic bilingual rules."""
    return fallback_reason(text)
