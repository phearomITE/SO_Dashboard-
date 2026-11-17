from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from zoneinfo import ZoneInfo


REPORT_PREFIXES = {
    "banner": "All Banner Tracking",
    "outlet_visit": "Outlet Visit",
    "product_display": "Product Display",
    "combined_summary": "Combined Tracking Summary",
    "report_gp": "Report GP",
}


def month_start(report_month: str) -> date:
    """Parse a strict YYYY-MM reporting month and return its first day."""
    try:
        parsed = datetime.strptime(str(report_month).strip(), "%Y-%m").date()
    except (TypeError, ValueError) as exc:
        raise ValueError("REPORT_MONTH must use YYYY-MM, for example '2026-09'.") from exc
    return parsed.replace(day=1)


def month_end(report_month: str) -> date:
    start = month_start(report_month)
    return date(start.year, start.month, monthrange(start.year, start.month)[1])


def resolve_reporting_range(
    report_month: str,
    mode: str = "month_to_date",
    selected_day: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    *,
    earliest: str | None = None,
    timezone_name: str = "Asia/Phnom_Penh",
    today: date | str | None = None,
) -> tuple[str, str]:
    """Resolve one reporting month into a safe inclusive date range.

    ``month_to_date`` is automatic: a past month uses its full month, while the
    current month ends on today's Cambodia date.  Future months are rejected so
    they cannot be accidentally marked complete before reports exist.
    """
    first = month_start(report_month)
    last = month_end(report_month)
    if today is None:
        local_today = datetime.now(ZoneInfo(timezone_name)).date()
    elif isinstance(today, date):
        local_today = today
    else:
        local_today = date.fromisoformat(str(today))

    normalized_mode = str(mode or "month_to_date").strip().casefold().replace("-", "_")
    if normalized_mode in {"month_to_date", "mtd", "month"}:
        if first > local_today:
            raise ValueError(
                f"REPORT_MONTH {report_month} is in the future for {timezone_name}."
            )
        start = first
        end = min(last, local_today)
    elif normalized_mode == "single_day":
        if not selected_day:
            raise ValueError("SELECTED_DAY is required when DATE_MODE='single_day'.")
        start = end = date.fromisoformat(str(selected_day))
    elif normalized_mode == "range":
        if not date_from or not date_to:
            raise ValueError("DATE_FROM and DATE_TO are required when DATE_MODE='range'.")
        start = date.fromisoformat(str(date_from))
        end = date.fromisoformat(str(date_to))
    else:
        raise ValueError(
            "DATE_MODE must be 'month_to_date', 'single_day', or 'range'."
        )

    if start > end:
        raise ValueError("DATE_FROM cannot be after DATE_TO.")
    if (start.year, start.month) != (first.year, first.month) or (
        end.year,
        end.month,
    ) != (first.year, first.month):
        raise ValueError(f"Selected dates must stay inside REPORT_MONTH {report_month}.")

    if earliest:
        minimum = date.fromisoformat(earliest)
        if last < minimum:
            raise ValueError(f"The earliest allowed date is {minimum.isoformat()}.")
        if normalized_mode in {"month_to_date", "mtd", "month"}:
            start = max(start, minimum)
        elif start < minimum:
            raise ValueError(f"The earliest allowed date is {minimum.isoformat()}.")

    if start > end:
        raise ValueError("The selected month has no reportable dates yet.")
    return start.isoformat(), end.isoformat()


def resolve_monthly_export_range(
    report_month: str,
    scrape_to: str,
    *,
    earliest: str | None = None,
) -> tuple[str, str]:
    """Return the cumulative monthly date window used by Excel exports.

    DATE_MODE controls which Telegram days are scraped. Monthly workbooks are
    rebuilt separately from all saved rows between month day 1 (or a track's
    approved earliest date) and the selected scrape end date.
    """
    first = month_start(report_month)
    end = date.fromisoformat(str(scrape_to))
    if (end.year, end.month) != (first.year, first.month):
        raise ValueError(f"Export end date must stay inside REPORT_MONTH {report_month}.")

    start = first
    if earliest:
        start = max(start, date.fromisoformat(earliest))
    if start > end:
        raise ValueError("The selected month has no exportable dates yet.")
    return start.isoformat(), end.isoformat()


def monthly_filename(report_kind: str, report_month: str) -> str:
    """Return a stable monthly filename such as Outlet Visit in Sep'26.xlsx."""
    key = str(report_kind).strip().casefold().replace(" ", "_").replace("-", "_")
    if key not in REPORT_PREFIXES:
        raise ValueError(f"Unknown report kind: {report_kind!r}")
    start = month_start(report_month)
    label = f"{start:%b}'{start:%y}"
    return f"{REPORT_PREFIXES[key]} in {label}.xlsx"


def monthly_filenames(report_month: str) -> dict[str, str]:
    return {
        "Banner": monthly_filename("banner", report_month),
        "Outlet Visit": monthly_filename("outlet_visit", report_month),
        "Product Display": monthly_filename("product_display", report_month),
        "Combined Summary": monthly_filename("combined_summary", report_month),
        "Report GP": monthly_filename("report_gp", report_month),
    }
