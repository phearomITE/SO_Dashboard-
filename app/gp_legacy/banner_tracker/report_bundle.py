from __future__ import annotations

from pathlib import Path

from .database import Database
from .combined_summary_exporter import export_combined_summary
from .exporter import export_excel
from .outlet_visit_exporter import export_outlet_visit
from .parser import BannerNormalizer
from .product_display_exporter import export_product_display
from .report_gp_exporter import export_report_gp
from .reporting_period import month_start, monthly_filename, monthly_filenames
from .settings import Settings


BANNER_FILENAME = monthly_filename("banner", "2026-08")
OUTLET_VISIT_FILENAME = monthly_filename("outlet_visit", "2026-08")
PRODUCT_DISPLAY_FILENAME = monthly_filename("product_display", "2026-08")
COMBINED_SUMMARY_FILENAME = monthly_filename("combined_summary", "2026-08")
REPORT_GP_FILENAME = monthly_filename("report_gp", "2026-08")

# Each project has its own approved reporting window.  Keep these dates in one
# place so Jupyter, the Windows batch commands, and every Excel export apply the
# same rules.
PRODUCT_DISPLAY_START_DATE = "2026-08-01"
OUTLET_VISIT_START_DATE = "2026-08-18"
BANNER_TRACKING_START_DATE = "2026-08-19"
# Telegram continuation/rebuild starts on 19-Aug because 01--18 Aug is the
# installed historical base.  The Combined Summary is different: it reads the
# finished Banner workbook, so it must include that historical base from
# 01-Aug as well as the newly appended rows.
COMBINED_BANNER_START_DATE = "2026-08-01"
LEGACY_SOURCE_REPAIR_STATE = "legacy_multitrack_source_repair_v3"


def export_four_reports(
    db: Database,
    settings: Settings,
    start_date: str,
    end_date: str,
) -> dict[str, Path]:
    """Recover current data and maintain five stable Excel outputs.

    Filenames never contain timestamps. If Excel has one of the files open,
    exporting stops with a clear error rather than creating duplicate copies.
    """
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    normalizer = BannerNormalizer(settings.aliases_path)

    if start_date[:7] != end_date[:7]:
        raise ValueError("All five exports must stay inside one reporting month.")

    # DATE_MODE may select only one scrape day, but these filenames represent
    # cumulative monthly reports. Never replace an MTD file with a daily-only
    # export; reuse every saved row from month day 1 through end_date.
    report_start = month_start(start_date[:7]).isoformat()

    # The August cutoffs preserve the installed historical base. For September
    # and every later month, max() naturally resolves all trackers to the first
    # selected day, so no August-only dates leak into the new month.
    banner_start = max(report_start, BANNER_TRACKING_START_DATE)
    combined_banner_start = max(report_start, COMBINED_BANNER_START_DATE)
    outlet_start = max(report_start, OUTLET_VISIT_START_DATE)
    product_start = max(report_start, PRODUCT_DISPLAY_START_DATE)

    print(
        "Tracking windows: "
        f"Banner {banner_start} -> {end_date}; "
        f"Outlet Visit {outlet_start} -> {end_date}; "
        f"Product Display {product_start} -> {end_date}"
    )

    ensure_ids = getattr(db, "ensure_telegram_ids", None)
    if callable(ensure_ids):
        id_recovery = ensure_ids()
        if id_recovery["total"]:
            print(f"Telegram IDs repaired for all exports: {id_recovery}")

    # Old releases sometimes saved one multi-purpose caption to only one table.
    # Repair that historical condition once; current scraping writes every
    # track directly, so reparsing 20k+ saved captions on every export wastes
    # minutes and cannot add new data.
    get_state = getattr(db, "get_state", lambda _key: None)
    set_state = getattr(db, "set_state", lambda _key, _value: None)
    if get_state(LEGACY_SOURCE_REPAIR_STATE) == "complete":
        recovery = {"skipped_completed_migration": 1, "forms_recovered": 0}
        outlet_rebuild = {"skipped_completed_migration": 1, "saved": 0}
    else:
        recovery = db.recover_forms_from_saved_sources(normalizer, banner_start, end_date) or {
            "forms_recovered": 0
        }
        outlet_rebuild = db.rebuild_outlet_visits(outlet_start, end_date) or {}
        set_state(LEGACY_SOURCE_REPAIR_STATE, "complete")
        if recovery["forms_recovered"]:
            print(f"Recovered saved multi-track messages: {recovery}")
    db.rebuild_banner_details(normalizer, banner_start, end_date)
    db.rebuild_product_display(product_start, end_date)
    db.rebuild_gps_links(banner_start, end_date, same_sender_window_minutes=60)
    if outlet_rebuild.get("saved"):
        print(f"Recovered legacy Outlet Visit rows: {outlet_rebuild}")

    names = monthly_filenames(report_start[:7])
    outputs = {label: settings.export_dir / filename for label, filename in names.items()}

    try:
        template_resolver = getattr(settings, "banner_template_for_month", None)
        banner_template = (
            template_resolver(report_start[:7])
            if callable(template_resolver) else settings.template_path
        )
        export_excel(db, banner_template, outputs["Banner"], product_start, end_date)
        staff_master_path = getattr(
            settings,
            "staff_master_path",
            settings.template_path.parent / "R1-R8-Master List.xlsx",
        )
        report_gp_template_path = getattr(
            settings,
            "report_gp_template_path",
            settings.template_path.parent / "Simple-Report GP.xlsx",
        )
        export_outlet_visit(
            db, outputs["Outlet Visit"], outlet_start, end_date, staff_master_path
        )
        export_product_display(
            db, settings.template_path, outputs["Product Display"], product_start, end_date,
            staff_master_path,
        )
        export_report_gp(
            db,
            outputs["Report GP"],
            outlet_start,
            end_date,
            report_gp_template_path,
            staff_master_path,
            **({"so_master_path": settings.gp_so_master_path}
               if hasattr(settings, "gp_so_master_path") else {}),
        )
        export_combined_summary(
            db,
            outputs["Combined Summary"],
            product_start,
            end_date,
            banner_start_date=combined_banner_start,
            outlet_start_date=outlet_start,
            product_start_date=product_start,
            banner_workbook=outputs["Banner"],
            outlet_workbook=outputs["Outlet Visit"],
            product_workbook=outputs["Product Display"],
        )
    except PermissionError as exc:
        raise PermissionError(
            "One of the five report files is open in Microsoft Excel. "
            "Close Banner, Outlet Visit, Product Display, Combined Summary, and Report GP workbooks, then rerun "
            "the export cell. No timestamped duplicate was created."
        ) from exc

    missing = [str(path) for path in outputs.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"Five-file export did not finish; missing: {missing}")
    return outputs


def export_three_reports(
    db: Database,
    settings: Settings,
    start_date: str,
    end_date: str,
) -> dict[str, Path]:
    """Backward-compatible name retained for older notebooks and batch files."""
    return export_four_reports(db, settings, start_date, end_date)


def export_five_reports(
    db: Database,
    settings: Settings,
    start_date: str,
    end_date: str,
) -> dict[str, Path]:
    """Current descriptive name; older entry points remain compatible."""
    return export_four_reports(db, settings, start_date, end_date)
