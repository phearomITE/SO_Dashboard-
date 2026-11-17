from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook


@dataclass(frozen=True)
class StaffMember:
    employee_id: str
    name: str
    position: str
    telegram_id: str
    role: str
    region: str = ""
    status: str = ""


def normalize_telegram_id(value) -> str:
    """Return an Excel/SQLite Telegram ID as a stable integer-like string."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        return str(int(float(text)))
    except (TypeError, ValueError, OverflowError):
        return text


def normalize_sales_role(position: str) -> str:
    """Map Sales Officer variants to SO and Sales Supervisor variants to SS."""
    cleaned = " ".join(str(position or "").replace(".", " ").split()).casefold()
    if "sales supervisor" in cleaned:
        return "SS"
    if "sales officer" in cleaned:
        return "SO"
    return ""


def normalize_staff_region(value) -> str:
    """Return a supported R1--R8 staff region or an empty string."""
    cleaned = str(value or "").strip().upper().replace(" ", "")
    return cleaned if cleaned in {f"R{number}" for number in range(1, 9)} else ""


def staff_is_active(member: StaffMember) -> bool:
    """Treat blank/Active status as active and common departure labels as inactive."""
    status = " ".join(str(member.status or "").strip().casefold().split())
    combined = f"{member.name} {member.position} {status}".casefold()
    inactive_markers = (
        "goodbye", "good bye", "goodbey", "inactive", "resigned",
        "resign", "terminated", "left company", "no longer active",
    )
    return not any(marker in combined for marker in inactive_markers)


def load_staff_directory(path: Path | str | None) -> dict[str, StaffMember]:
    """Load the uploaded ``List_Name_ID`` mapping keyed by Telegram ID.

    Missing files intentionally return an empty mapping so scraping and exports
    still run. The ID column in output remains populated from Telegram itself;
    only the friendly staff Name/Role mapping is unavailable in that case.
    """
    if not path:
        return {}
    source = Path(path)
    if not source.is_file():
        return {}
    workbook = load_workbook(source, read_only=True, data_only=True)
    try:
        if "List_Name_ID" not in workbook.sheetnames:
            return {}
        sheet = workbook["List_Name_ID"]
        headers = {
            str(cell.value or "").strip().casefold(): index
            for index, cell in enumerate(sheet[1], 1)
        }
        employee_col = headers.get("id")
        name_col = headers.get("name")
        position_col = headers.get("position")
        telegram_col = headers.get("telegram id")
        region_col = headers.get("region")
        status_col = (
            headers.get("status")
            or headers.get("employment status")
            or headers.get("active status")
        )
        if not telegram_col:
            return {}
        result: dict[str, StaffMember] = {}
        for values in sheet.iter_rows(min_row=2, values_only=True):
            def value_at(column: int | None):
                if not column or column > len(values):
                    return ""
                return values[column - 1]

            telegram_id = normalize_telegram_id(value_at(telegram_col))
            if not telegram_id:
                continue
            employee_id = normalize_telegram_id(value_at(employee_col))
            name = str(value_at(name_col) or "").strip()
            position = str(value_at(position_col) or "").strip()
            region = normalize_staff_region(value_at(region_col))
            status = str(value_at(status_col) or "").strip()
            result[telegram_id] = StaffMember(
                employee_id=employee_id,
                name=name,
                position=position,
                telegram_id=telegram_id,
                role=normalize_sales_role(position),
                region=region,
                status=status,
            )
        return result
    finally:
        workbook.close()


def staff_name(directory: dict[str, StaffMember], telegram_id) -> str:
    member = directory.get(normalize_telegram_id(telegram_id))
    return member.name if member else ""
