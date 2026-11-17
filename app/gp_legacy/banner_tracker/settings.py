from __future__ import annotations

import os
from datetime import date
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    path = path or ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _path(name: str, default: str) -> Path:
    value = Path(os.getenv(name, default))
    return value if value.is_absolute() else ROOT / value


@dataclass(frozen=True)
class Settings:
    api_id: int
    api_hash: str
    phone: str
    session_path: Path
    timezone: str
    expected_groups: int
    group_keyword: str
    product_display_group_name: str
    banner_mapping_group_name: str
    gps_window_minutes: int
    database_path: Path
    template_path: Path
    report_gp_template_path: Path
    staff_master_path: Path
    export_dir: Path
    aliases_path: Path
    invalid_report_alert_mode: str
    scrape_concurrency: int
    telegram_connect_timeout: int
    telegram_connection_retries: int
    telegram_request_retries: int
    telegram_retry_delay: int

    @classmethod
    def load(cls, require_telegram: bool = True) -> "Settings":
        load_dotenv()
        api_id_text = os.getenv("TELEGRAM_API_ID", "0")
        api_hash = os.getenv("TELEGRAM_API_HASH", "")
        phone = os.getenv("TELEGRAM_PHONE", "")
        if require_telegram and (not api_id_text.isdigit() or int(api_id_text) <= 0 or not api_hash):
            raise RuntimeError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first.")
        alert_mode = os.getenv("INVALID_REPORT_ALERT_MODE", "reply").strip().lower()
        if alert_mode not in {"reply", "log", "off"}:
            raise RuntimeError("INVALID_REPORT_ALERT_MODE must be reply, log, or off.")
        return cls(
            api_id=int(api_id_text) if api_id_text.isdigit() else 0,
            api_hash=api_hash,
            phone=phone,
            session_path=_path("TELEGRAM_SESSION", "data/banner_tracker"),
            timezone=os.getenv("TIMEZONE", "Asia/Phnom_Penh"),
            expected_groups=int(os.getenv("EXPECTED_GROUPS", "65")),
            group_keyword=os.getenv("GROUP_TITLE_KEYWORD", "Execution Excellent"),
            product_display_group_name=os.getenv(
                "PRODUCT_DISPLAY_GROUP_NAME", "Display Product"
            ).strip(),
            banner_mapping_group_name=os.getenv(
                "BANNER_MAPPING_GROUP_NAME", "Banner Mapping"
            ).strip(),
            gps_window_minutes=int(os.getenv("GPS_WINDOW_MINUTES", "10")),
            database_path=_path("DATABASE_PATH", "data/banner_tracking.db"),
            template_path=_path("TEMPLATE_PATH", "templates/All Banner Tracking in Aug'26.xlsx"),
            report_gp_template_path=_path(
                "REPORT_GP_TEMPLATE_PATH", "templates/Simple-Report GP.xlsx"
            ),
            staff_master_path=_path(
                "STAFF_MASTER_PATH", "templates/R1-R8-Master List.xlsx"
            ),
            export_dir=_path("EXPORT_DIR", "exports"),
            aliases_path=ROOT / "config" / "banner_aliases.json",
            invalid_report_alert_mode=alert_mode,
            scrape_concurrency=max(1, int(os.getenv("SCRAPE_CONCURRENCY", "2"))),
            telegram_connect_timeout=max(5, int(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "15"))),
            telegram_connection_retries=max(0, int(os.getenv("TELEGRAM_CONNECTION_RETRIES", "3"))),
            telegram_request_retries=max(0, int(os.getenv("TELEGRAM_REQUEST_RETRIES", "3"))),
            telegram_retry_delay=max(1, int(os.getenv("TELEGRAM_RETRY_DELAY", "3"))),
        )

    @property
    def gp_so_master_path(self) -> Path:
        return _path("GP_SO_MASTER_PATH", "templates/SO_List.xlsx")

    def ensure_directories(self) -> None:
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

    def banner_template_for_month(self, report_month: str) -> Path:
        """Return a month-specific Banner template when one is installed.

        ``report_month`` is YYYY-MM (or a date beginning with YYYY-MM).  An
        explicitly configured TEMPLATE_PATH remains the fallback, which keeps
        older deployments and tests backward compatible.
        """
        month = date.fromisoformat(f"{str(report_month)[:7]}-01")
        candidate = self.template_path.parent / (
            f"All Banner Tracking in {month.strftime('%b')}'{month.strftime('%y')}.xlsx"
        )
        return candidate if candidate.is_file() else self.template_path
