"""Export one GP workbook per selected calendar date, inclusive."""
from datetime import date, timedelta
from pathlib import Path

from .report_gp_exporter import export_report_gp

GP_CURRENT_DAY_SNAPSHOT_VERSION = "PENDING_V1"
GP_SELECTED_DATE_EXPORT_VERSION = "EXACT_DATES_XLSX_V2_ACTIVITY"


def export_report_gp_daily(db, export_dir, start_date, end_date, template_path,
                           staff_master_path, *, so_master_path,
                           require_complete_al_scan=True, output_path=None,
                           allow_current_day_snapshot=False, selected_date_mode=True, summary_only=False):
    start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    if end < start:
        raise ValueError('GP end date must be on or after start date')
    from .gp_so_roster import load_so_roster
    load_so_roster(so_master_path)
    # Validate the entire selection before producing any workbook.
    from .gp_so_roster import require_report_coverage
    require_report_coverage(db, start_date, end_date, allow_current_day=allow_current_day_snapshot, selected_date_mode=selected_date_mode)
    from .gp_so_roster import sync_so_list
    sync_so_list(db, so_master_path, start_date, end_date)
    if selected_date_mode:
        archive_gp_sidecars(db, export_dir)
    count = (end - start).days + 1
    paths = []
    for offset in range(count):
        day = (start + timedelta(days=offset)).isoformat()
        if output_path is not None:
            base = Path(output_path)
            if base.suffix.lower() != '.xlsx':
                raise ValueError('GP output must have an .xlsx extension')
            output = base if count == 1 else base.with_name(f'{base.stem}_{day}.xlsx')
        else:
            output = Path(export_dir) / f'report_gp_{day}.xlsx'
        paths.append(export_report_gp(
            db, output, day, day, template_path, staff_master_path,
            so_master_path=so_master_path,
            require_complete_al_scan=require_complete_al_scan,
            allow_current_day_snapshot=allow_current_day_snapshot,
            selected_date_mode=selected_date_mode,summary_only=summary_only))
    return paths


def archive_gp_sidecars(db, export_dir):
    """Relocate only recognized old GP audit files, without deleting other exports."""
    import hashlib
    import re
    import shutil
    directory = Path(export_dir)
    destination = db.path.parent / 'gp_export_audits'
    pattern = re.compile(r'^report_gp_\d{4}-\d{2}-\d{2}_(AL_Audit|Attendance_Audit|Attendance_Reconciliation|Member_Refresh_Audit|Scan_Coverage|SO_Roster_Audit)\.csv$')
    if not directory.is_dir():
        return
    for source in directory.iterdir():
        if source.is_symlink() or not source.is_file() or not pattern.fullmatch(source.name):
            continue
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / source.name
        if target.exists() and target.read_bytes() != source.read_bytes():
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:12]
            target = target.with_name(target.stem + '.previous-' + digest + '.csv')
        shutil.move(str(source), str(target))
