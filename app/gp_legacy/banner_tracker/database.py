from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from .parser import (
    BannerItem,
    BannerNormalizer,
    ParsedForm,
    has_meaningful_banner_text,
    parse_product_display,
    parse_form,
    product_display_status,
    validate_report,
)
from .outlet_visit import OutletVisit, normalize_outlet_type, parse_outlet_visit
from .groups import DEALER_TO_REGION
from .telegram_identity import (
    is_excluded_telegram_name,
    normalize_telegram_name,
    stable_telegram_id,
)


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS gp_sender_activity (
    group_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    submitted_at TEXT NOT NULL, sender_id INTEGER NOT NULL,
    sender_name TEXT NOT NULL DEFAULT '', sender_username TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    PRIMARY KEY(group_id,message_id)
);
CREATE TABLE IF NOT EXISTS telegram_group_members (
    group_id INTEGER NOT NULL,
    telegram_id INTEGER NOT NULL,
    telegram_name TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    PRIMARY KEY(group_id,telegram_id)
);
CREATE TABLE IF NOT EXISTS telegram_member_scans (
    group_id INTEGER PRIMARY KEY,
    attempted_at TEXT NOT NULL,
    status TEXT NOT NULL,
    retrieved_count INTEGER NOT NULL,
    reported_total INTEGER,
    detail TEXT NOT NULL DEFAULT '',
    last_success_at TEXT
);

CREATE TABLE IF NOT EXISTS team_talk_reports (
    group_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    report_date TEXT NOT NULL,
    dealer TEXT NOT NULL,
    raw_text TEXT NOT NULL,
    PRIMARY KEY(group_id,message_id)
);
CREATE INDEX IF NOT EXISTS idx_team_talk_date ON team_talk_reports(report_date);

CREATE TABLE IF NOT EXISTS telegram_groups (
    group_id INTEGER PRIMARY KEY,
    group_name TEXT NOT NULL,
    region TEXT NOT NULL,
    dealer TEXT NOT NULL,
    source_kind TEXT NOT NULL DEFAULT 'EXECUTION',
    is_active INTEGER NOT NULL DEFAULT 1,
    discovered_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_group_dealer ON telegram_groups(region, dealer);
CREATE INDEX IF NOT EXISTS idx_group_dealer_lookup ON telegram_groups(dealer);
CREATE TABLE IF NOT EXISTS banner_forms (
    group_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    sender_id INTEGER,
    sender_name TEXT,
    sender_username TEXT,
    region TEXT NOT NULL,
    dealer TEXT NOT NULL,
    form_dealer TEXT NOT NULL,
    outlet_name TEXT NOT NULL,
    phone TEXT,
    outlet_type TEXT,
    original_banner_text TEXT,
    latitude REAL,
    longitude REAL,
    gps_status TEXT NOT NULL DEFAULT 'GPS_MISSING',
    parse_status TEXT NOT NULL,
    raw_text TEXT,
    product_display TEXT,
    remark_issues TEXT,
    PRIMARY KEY(group_id, message_id)
);
CREATE TABLE IF NOT EXISTS banner_details (
    group_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    banner_name TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    original_token TEXT,
    status TEXT NOT NULL,
    PRIMARY KEY(group_id, message_id, banner_name, status),
    FOREIGN KEY(group_id, message_id) REFERENCES banner_forms(group_id, message_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS product_display_media (
    group_id INTEGER NOT NULL,
    form_message_id INTEGER NOT NULL,
    image_message_id INTEGER NOT NULL,
    PRIMARY KEY(group_id, form_message_id)
);
CREATE TABLE IF NOT EXISTS locations (
    group_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    sender_id INTEGER,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    reply_to_message_id INTEGER,
    matched_form_message_id INTEGER,
    PRIMARY KEY(group_id, message_id)
);
CREATE TABLE IF NOT EXISTS invalid_reports (
    group_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    sender_id INTEGER,
    sender_name TEXT,
    sender_username TEXT,
    region TEXT NOT NULL,
    dealer TEXT NOT NULL,
    error_codes TEXT NOT NULL,
    error_message TEXT NOT NULL,
    raw_text TEXT,
    alert_sent INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(group_id, message_id)
);
CREATE TABLE IF NOT EXISTS sync_checkpoints (
    group_id INTEGER PRIMARY KEY,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    last_message_date TEXT,
    history_from TEXT,
    history_to TEXT,
    history_complete INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(group_id) REFERENCES telegram_groups(group_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scrape_coverage (
    track TEXT NOT NULL,
    group_id INTEGER NOT NULL,
    report_date TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY(track,group_id,report_date)
);
CREATE TABLE IF NOT EXISTS outlet_visits (
    group_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    sender_id INTEGER,
    sender_name TEXT,
    sender_username TEXT,
    region TEXT NOT NULL,
    dealer TEXT NOT NULL,
    form_dealer TEXT,
    outlet_name TEXT,
    phone TEXT,
    outlet_type TEXT,
    emergency_outlet INTEGER NOT NULL DEFAULT 0,
    attacking_stock INTEGER NOT NULL DEFAULT 0,
    tour_program INTEGER NOT NULL DEFAULT 0,
    key_wholesale INTEGER NOT NULL DEFAULT 0,
    water INTEGER NOT NULL DEFAULT 0,
    c_cola INTEGER NOT NULL DEFAULT 0,
    ize_pet_300ml INTEGER NOT NULL DEFAULT 0,
    ize_pet_1500ml INTEGER NOT NULL DEFAULT 0,
    remark_issues TEXT,
    raw_values TEXT,
    field_statuses TEXT,
    parse_status TEXT NOT NULL,
    latitude REAL,
    longitude REAL,
    gps_status TEXT NOT NULL DEFAULT 'GPS_MISSING',
    raw_text TEXT,
    PRIMARY KEY(group_id,message_id)
);
CREATE INDEX IF NOT EXISTS idx_banner_forms_sender ON banner_forms(sender_id);
CREATE INDEX IF NOT EXISTS idx_outlet_visits_sender ON outlet_visits(sender_id);
CREATE INDEX IF NOT EXISTS idx_invalid_reports_sender ON invalid_reports(sender_id);
CREATE INDEX IF NOT EXISTS idx_banner_forms_report_date
    ON banner_forms(substr(submitted_at,1,10));
CREATE INDEX IF NOT EXISTS idx_banner_forms_dealer ON banner_forms(dealer);
CREATE INDEX IF NOT EXISTS idx_outlet_visits_report_date
    ON outlet_visits(substr(submitted_at,1,10));
CREATE INDEX IF NOT EXISTS idx_outlet_visits_dealer ON outlet_visits(dealer);
CREATE INDEX IF NOT EXISTS idx_invalid_reports_report_date
    ON invalid_reports(substr(submitted_at,1,10));
CREATE INDEX IF NOT EXISTS idx_invalid_reports_dealer ON invalid_reports(dealer);
CREATE INDEX IF NOT EXISTS idx_locations_report_date
    ON locations(substr(sent_at,1,10));
CREATE INDEX IF NOT EXISTS idx_locations_sender_time
    ON locations(group_id,sender_id,sent_at);
CREATE INDEX IF NOT EXISTS idx_locations_reply
    ON locations(group_id,reply_to_message_id);
CREATE INDEX IF NOT EXISTS idx_locations_matched
    ON locations(group_id,matched_form_message_id);
CREATE INDEX IF NOT EXISTS idx_scrape_coverage_track_date
    ON scrape_coverage(track,report_date);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)
            group_columns = {row[1] for row in con.execute("PRAGMA table_info(telegram_groups)")}
            if "source_kind" not in group_columns:
                con.execute(
                    "ALTER TABLE telegram_groups ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'EXECUTION'"
                )
            columns = {row[1] for row in con.execute("PRAGMA table_info(banner_forms)")}
            if "product_display" not in columns:
                con.execute("ALTER TABLE banner_forms ADD COLUMN product_display TEXT")
            if "remark_issues" not in columns:
                con.execute("ALTER TABLE banner_forms ADD COLUMN remark_issues TEXT")
            outlet_columns = {row[1] for row in con.execute("PRAGMA table_info(outlet_visits)")}
            if "remark_issues" not in outlet_columns:
                con.execute("ALTER TABLE outlet_visits ADD COLUMN remark_issues TEXT")
            outlet_type_marker = con.execute(
                "SELECT value FROM app_state WHERE key='outlet_type_normalization_v2'"
            ).fetchone()
            if not outlet_type_marker:
                for table in ("banner_forms", "outlet_visits"):
                    for row in con.execute(
                        f"SELECT rowid,outlet_type FROM {table} WHERE trim(COALESCE(outlet_type,''))<>''"
                    ).fetchall():
                        normalized = normalize_outlet_type(row["outlet_type"])
                        if normalized != row["outlet_type"]:
                            con.execute(
                                f"UPDATE {table} SET outlet_type=? WHERE rowid=?",
                                (normalized, row["rowid"]),
                            )
                con.execute(
                    """INSERT INTO app_state(key,value,updated_at) VALUES(?,?,?)""",
                    ("outlet_type_normalization_v2", "complete", datetime.now(timezone.utc).isoformat()),
                )
            # Repair the authoritative dealer-to-region map only once. Older
            # code rescanned four large tables for all 65 dealers every time a
            # notebook created Database(), making a simple export look frozen.
            region_marker = con.execute(
                "SELECT value FROM app_state WHERE key='region_map_repair_v2'"
            ).fetchone()
            if not region_marker:
                for table in ("telegram_groups", "banner_forms", "invalid_reports", "outlet_visits"):
                    for dealer, region in DEALER_TO_REGION.items():
                        con.execute(
                            f"UPDATE {table} SET region=? WHERE dealer=? COLLATE NOCASE AND region<>?",
                            (region, dealer, region),
                        )
                con.execute(
                    """INSERT INTO app_state(key,value,updated_at) VALUES(?,?,?)""",
                    ("region_map_repair_v2", "complete", datetime.now(timezone.utc).isoformat()),
                )
            # telegram_groups is tiny and can receive rows after the one-time
            # historical repair (for example after group discovery). Keep it
            # canonical on every Database initialization.
            for dealer, region in DEALER_TO_REGION.items():
                con.execute(
                    "UPDATE telegram_groups SET region=? "
                    "WHERE dealer=? COLLATE NOCASE AND region<>?",
                    (region, dealer, region),
                )
            self._migrate_checkpoint_coverage(con)
            # Source changes and media links require one Product Display-only
            # rescan. Preserve Banner and Outlet Visit coverage/checkpoints.
            product_presence_marker = con.execute(
                "SELECT value FROM app_state "
                "WHERE key='product_display_execution_media_rescan_v3'"
            ).fetchone()
            if not product_presence_marker:
                con.execute(
                    "DELETE FROM scrape_coverage WHERE track='PRODUCT_DISPLAY'"
                )
                con.execute(
                    """UPDATE sync_checkpoints SET history_complete=0
                       WHERE group_id IN (
                           SELECT group_id FROM telegram_groups
                           WHERE source_kind='PRODUCT_DISPLAY'
                       )"""
                )
                con.execute(
                    """INSERT INTO app_state(key,value,updated_at)
                       VALUES(?,?,?)""",
                    (
                        "product_display_execution_media_rescan_v3",
                        "complete",
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def save_gp_activity(self, *, group_id, message_id, submitted_at, sender_id,
                         sender_name='', sender_username='', kind='MESSAGE'):
        # Real Telegram users only; anonymous channel posts are not SOs.
        if not sender_id or int(sender_id) <= 0:
            return
        with self.connect() as con:
            con.execute("""INSERT INTO gp_sender_activity VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(group_id,message_id) DO UPDATE SET
                submitted_at=excluded.submitted_at,sender_id=excluded.sender_id,
                sender_name=excluded.sender_name,sender_username=excluded.sender_username,
                kind=excluded.kind""", (group_id,message_id,submitted_at.isoformat(),
                int(sender_id),sender_name,sender_username,kind))

    def upsert_group(self, group_id: int, group_name: str, region: str, dealer: str,
                     source_kind: str = "EXECUTION") -> None:
        source_kind = source_kind.strip().upper()
        if source_kind not in {"EXECUTION", "PRODUCT_DISPLAY", "BANNER_MAPPING"}:
            raise ValueError(f"Unsupported Telegram source kind: {source_kind}")
        with self.connect() as con:
            con.execute(
                """INSERT INTO telegram_groups(
                       group_id,group_name,region,dealer,source_kind,is_active,discovered_at
                   ) VALUES(?,?,?,?,?,1,?) ON CONFLICT(group_id) DO UPDATE SET
                   group_name=excluded.group_name,region=excluded.region,dealer=excluded.dealer,
                   source_kind=excluded.source_kind,is_active=1""",
                (group_id, group_name, region, dealer, source_kind,
                 datetime.now(timezone.utc).isoformat()),
            )

    def begin_group_discovery(self, source_kind: str = "EXECUTION") -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE telegram_groups SET is_active=0 WHERE source_kind=?",
                (source_kind.strip().upper(),),
            )

    def get_state(self, key: str) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
            return str(row[0]) if row else None

    def set_state(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO app_state(key,value,updated_at) VALUES(?,?,?)
                   ON CONFLICT(key) DO UPDATE SET
                   value=excluded.value,updated_at=excluded.updated_at""",
                (key, value, datetime.now(timezone.utc).isoformat()),
            )

    def track_row_count(self, track: str, start_date: str, end_date: str) -> int:
        """Count already saved rows for a notebook's selected reporting window."""
        track = str(track or "").strip().upper()
        if track == "PRODUCT_DISPLAY":
            table = "banner_forms"
            source_kind = "EXECUTION"
            extra = (
                "AND (product_display='Yes' OR "
                "lower(replace(replace(replace(raw_text,' ',''),'-',''),'\\','')) "
                "LIKE '%productdisplay%')"
            )
        elif track == "OUTLET_VISIT":
            table = "outlet_visits"
            source_kind = "EXECUTION"
            extra = ""
        elif track == "BANNER":
            table = "banner_forms"
            source_kind = "EXECUTION"
            extra = "AND trim(COALESCE(original_banner_text,''))<>''"
        else:
            raise ValueError("track must be PRODUCT_DISPLAY, OUTLET_VISIT, or BANNER")
        with self.connect() as con:
            row = con.execute(
                f"""SELECT COUNT(*) FROM {table} t
                    LEFT JOIN telegram_groups g ON g.group_id=t.group_id
                    WHERE (g.group_id IS NULL OR g.source_kind=?)
                      AND (g.group_id IS NULL OR g.is_active=1)
                      AND substr(t.submitted_at,1,10) BETWEEN ? AND ? {extra}""",
                (source_kind, start_date, end_date),
            ).fetchone()
        return int(row[0] or 0)

    @staticmethod
    def _track_source(track: str) -> tuple[str, str, str]:
        track = str(track or "").strip().upper()
        if track == "PRODUCT_DISPLAY":
            return "banner_forms", "EXECUTION", ""
        if track == "OUTLET_VISIT":
            return "outlet_visits", "EXECUTION", ""
        if track == "TEAM_TALK_V2":
            return "team_talk_reports", "EXECUTION", ""
        if track == "BANNER":
            return "banner_forms", "EXECUTION", ""
        raise ValueError("track must be PRODUCT_DISPLAY, OUTLET_VISIT, or BANNER")

    @staticmethod
    def _migrate_checkpoint_coverage(con: sqlite3.Connection) -> None:
        """Convert trustworthy legacy checkpoints into exact per-day coverage.

        Older databases stored one history interval per group.  They did not
        distinguish empty days, so looking only at the latest saved data row
        could incorrectly skip an incomplete day.  A completed checkpoint is
        authoritative because Telegram history was actually scanned for that
        entire group and interval.
        """
        marker = con.execute(
            "SELECT value FROM app_state WHERE key='scrape_coverage_migration_v1'"
        ).fetchone()
        if marker:
            return
        completed_at = datetime.now(timezone.utc).isoformat()
        coverage_rows: list[tuple[str, int, str, str]] = []
        checkpoints = con.execute(
            """SELECT s.group_id,s.history_from,s.history_to,g.source_kind
               FROM sync_checkpoints s
               JOIN telegram_groups g ON g.group_id=s.group_id
               WHERE s.history_complete=1 AND g.is_active=1
                 AND s.history_from IS NOT NULL AND s.history_to IS NOT NULL"""
        ).fetchall()
        for row in checkpoints:
            try:
                start = datetime.fromisoformat(str(row[1]))
                stop = datetime.fromisoformat(str(row[2]))
            except (TypeError, ValueError):
                continue
            # Scrape intervals normally end at midnight after the selected
            # final day. Imported-base checkpoints can instead end at 23:59:59.
            final_day = stop.date() - timedelta(days=1) if stop.time() == datetime.min.time() else stop.date()
            tracks = (
                ("PRODUCT_DISPLAY",)
                if str(row[3]).upper() == "PRODUCT_DISPLAY"
                else ("BANNER", "OUTLET_VISIT")
            )
            current = start.date()
            while current <= final_day:
                for track in tracks:
                    coverage_rows.append((track, int(row[0]), current.isoformat(), completed_at))
                current += timedelta(days=1)
        if coverage_rows:
            con.executemany(
                """INSERT OR IGNORE INTO scrape_coverage(
                       track,group_id,report_date,completed_at
                   ) VALUES(?,?,?,?)""",
                coverage_rows,
            )
        con.execute(
            """INSERT INTO app_state(key,value,updated_at) VALUES(?,?,?)""",
            ("scrape_coverage_migration_v1", "complete", completed_at),
        )

    def mark_track_range_complete(
        self, track: str, group_id: int, date_from: datetime, date_to: datetime,
    ) -> None:
        """Record every successfully checked local day, including empty days."""
        track = str(track or "").strip().upper()
        if track == "ALL":
            for child in ("BANNER", "OUTLET_VISIT"):
                self.mark_track_range_complete(child, group_id, date_from, date_to)
            return
        self._track_source(track)
        current = date_from.date()
        stop = date_to.date()
        completed_at = datetime.now(timezone.utc).isoformat()
        rows: list[tuple[str, int, str, str]] = []
        while current < stop:
            rows.append((track, int(group_id), current.isoformat(), completed_at))
            current += timedelta(days=1)
        if not rows:
            return
        with self.connect() as con:
            con.executemany(
                """INSERT INTO scrape_coverage(track,group_id,report_date,completed_at)
                   VALUES(?,?,?,?) ON CONFLICT(track,group_id,report_date) DO UPDATE SET
                   completed_at=excluded.completed_at""",
                rows,
            )

    def missing_track_ranges(
        self, track: str, start_date: str, end_date: str, *, force: bool = False,
    ) -> list[tuple[str, str]]:
        """Return only inclusive day ranges that have not been checked in every source group."""
        track = str(track or "").strip().upper()
        _, source_kind, _ = self._track_source(track)
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
        if start > end:
            raise ValueError("start_date cannot be after end_date")
        if force:
            return [(start.isoformat(), end.isoformat())]
        with self.connect() as con:
            group_ids = [
                int(row[0]) for row in con.execute(
                    """SELECT group_id FROM telegram_groups
                       WHERE source_kind=? AND is_active=1 ORDER BY group_id""",
                    (source_kind,),
                )
            ]
            covered = {
                str(row[0]): int(row[1])
                for row in con.execute(
                    """SELECT c.report_date,COUNT(DISTINCT c.group_id)
                       FROM scrape_coverage c
                       JOIN telegram_groups g ON g.group_id=c.group_id
                       WHERE c.track=? AND c.report_date BETWEEN ? AND ?
                         AND g.source_kind=? AND g.is_active=1
                         AND datetime(c.completed_at,'+7 hours') >= datetime(c.report_date,'+1 day')
                       GROUP BY c.report_date""",
                    (track, start_date, end_date, source_kind),
                )
            }
        expected = len(group_ids)
        missing: list[date] = []
        current = start
        while current <= end:
            day = current.isoformat()
            exact = bool(expected and covered.get(day, 0) >= expected)
            if not exact:
                missing.append(current)
            current += timedelta(days=1)
        if not missing:
            return []
        ranges: list[tuple[str, str]] = []
        range_start = previous = missing[0]
        for day in missing[1:]:
            if day != previous + timedelta(days=1):
                ranges.append((range_start.isoformat(), previous.isoformat()))
                range_start = day
            previous = day
        ranges.append((range_start.isoformat(), previous.isoformat()))
        return ranges

    def missing_track_ranges_with_overlap(
        self, track: str, start_date: str, end_date: str, *, force: bool = False,
    ) -> list[tuple[str, str]]:
        """Return missing ranges and re-read the preceding calendar day.

        The overlap refreshes reports that were edited or posted late on the
        previous day.
        It is bounded by each export's approved August start date. Database
        primary keys keep the repeated day idempotent.
        """
        ranges = self.missing_track_ranges(
            track, start_date, end_date, force=force,
        )
        if force:
            return ranges
        track_name = str(track or "").strip().upper()
        earliest = {
            "PRODUCT_DISPLAY": date(2026, 8, 1),
            "OUTLET_VISIT": date(2026, 8, 18),
            "BANNER": date(2026, 8, 19),
        }.get(track_name, date.fromisoformat(start_date))
        if ranges:
            first_from, first_to = ranges[0]
            overlap = max(
                earliest,
                date.fromisoformat(first_from) - timedelta(days=1),
            ).isoformat()
            ranges[0] = (overlap, first_to)

        # A day can be marked complete and then receive another report later
        # that afternoon. Always refresh the selected final day and the day
        # before it, even when the coverage table says every day is complete.
        selected_end = date.fromisoformat(end_date)
        refresh_from = max(earliest, selected_end - timedelta(days=1))
        ranges.append((refresh_from.isoformat(), selected_end.isoformat()))

        # Merge overlapping/adjacent missing and refresh ranges so Telegram is
        # never asked for the same day twice in one run.
        merged: list[tuple[date, date]] = []
        for range_from, range_to in sorted(
            (date.fromisoformat(a), date.fromisoformat(b)) for a, b in ranges
        ):
            if merged and range_from <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(merged[-1][1], range_to))
            else:
                merged.append((range_from, range_to))
        return [(a.isoformat(), b.isoformat()) for a, b in merged]

    def ensure_telegram_ids(self) -> dict[str, int]:
        """Backfill every source row with a deterministic, nonblank sender ID.

        The repair first reuses a known real ID by username, visible name, or
        matching group/message across source tables. Only privacy-hidden rows
        receive a stable negative fallback. This is local SQLite work and does
        not contact Telegram, so all exporters can call it safely and quickly.
        """
        identity_tables = ("banner_forms", "outlet_visits", "invalid_reports")
        with self.connect() as con:
            blank_counts = {
                table: int(con.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE sender_id IS NULL OR sender_id=0"
                ).fetchone()[0])
                for table in identity_tables
            }
            blank_counts["locations"] = int(con.execute(
                "SELECT COUNT(*) FROM locations WHERE sender_id IS NULL OR sender_id=0"
            ).fetchone()[0])
            rows_by_table = {
                table: [dict(row) for row in con.execute(
                    f"""SELECT group_id,message_id,sender_id,sender_name,sender_username
                        FROM {table}"""
                )]
                for table in identity_tables
            }

            ids_by_message: dict[tuple[int, int], int] = {}
            ids_by_username: dict[str, set[int]] = {}
            ids_by_name: dict[str, set[int]] = {}
            for rows in rows_by_table.values():
                for row in rows:
                    try:
                        known_id = int(row.get("sender_id") or 0)
                    except (TypeError, ValueError):
                        known_id = 0
                    # Positive values are real Telegram IDs. Negative values
                    # are privacy fallbacks and may be upgraded later.
                    if known_id <= 0:
                        continue
                    ids_by_message[(int(row["group_id"]), int(row["message_id"]))] = known_id
                    username = normalize_telegram_name(
                        str(row.get("sender_username") or "").lstrip("@")
                    )
                    name = normalize_telegram_name(row.get("sender_name"))
                    if username:
                        ids_by_username.setdefault(username, set()).add(known_id)
                    if name:
                        ids_by_name.setdefault(name, set()).add(known_id)

            def one(values: set[int] | None) -> int | None:
                return next(iter(values)) if values and len(values) == 1 else None

            updated = {table: 0 for table in (*identity_tables, "locations")}
            for table, rows in rows_by_table.items():
                for row in rows:
                    try:
                        current = int(row.get("sender_id") or 0)
                    except (TypeError, ValueError):
                        current = 0
                    key = (int(row["group_id"]), int(row["message_id"]))
                    username = normalize_telegram_name(
                        str(row.get("sender_username") or "").lstrip("@")
                    )
                    name = normalize_telegram_name(row.get("sender_name"))
                    recovered = (
                        ids_by_message.get(key)
                        or one(ids_by_username.get(username))
                        or one(ids_by_name.get(name))
                        or (current if current else stable_telegram_id(
                            sender_name=row.get("sender_name"),
                            sender_username=row.get("sender_username"),
                            fallback_key=f"message:{key[0]}:{key[1]}",
                        ))
                    )
                    if current != int(recovered):
                        con.execute(
                            f"UPDATE {table} SET sender_id=? WHERE group_id=? AND message_id=?",
                            (recovered, *key),
                        )
                        updated[table] += 1
                    ids_by_message[key] = int(recovered)

            locations = [dict(row) for row in con.execute(
                """SELECT group_id,message_id,sender_id,reply_to_message_id,
                          matched_form_message_id FROM locations
                   WHERE sender_id IS NULL OR sender_id<=0"""
            )]
            for row in locations:
                group_id = int(row["group_id"])
                related_ids = [row.get("matched_form_message_id"), row.get("reply_to_message_id")]
                recovered = next(
                    (ids_by_message[(group_id, int(message_id))]
                     for message_id in related_ids
                     if message_id and (group_id, int(message_id)) in ids_by_message),
                    None,
                )
                current = int(row.get("sender_id") or 0)
                recovered = recovered or (current if current else stable_telegram_id(
                    fallback_key=f"location:{group_id}:{int(row['message_id'])}"
                ))
                if current != int(recovered):
                    con.execute(
                        "UPDATE locations SET sender_id=? WHERE group_id=? AND message_id=?",
                        (recovered, group_id, int(row["message_id"])),
                    )
                    updated["locations"] += 1

            # Canonicalize profile text by real Telegram ID so the same person
            # cannot be exported as both "Cheat Un" and "UN Cheat".
            identity_counts: dict[int, dict[tuple[str, str], int]] = {}
            for table in identity_tables:
                for row in con.execute(
                    f"""SELECT sender_id,COALESCE(sender_name,''),COALESCE(sender_username,'')
                        FROM {table} WHERE sender_id>0"""
                ):
                    sender_id = int(row[0])
                    pair = (str(row[1]).strip(), str(row[2]).strip().lstrip("@"))
                    if not any(pair):
                        continue
                    counts = identity_counts.setdefault(sender_id, {})
                    counts[pair] = counts.get(pair, 0) + 1
            for sender_id, counts in identity_counts.items():
                name, username = max(
                    counts,
                    key=lambda pair: (
                        counts[pair], bool(pair[1]), bool(pair[0]),
                        len(pair[0]) + len(pair[1]), pair,
                    ),
                )
                for table in identity_tables:
                    cursor = con.execute(
                        f"""UPDATE {table}
                            SET sender_name=CASE WHEN ?<>'' THEN ? ELSE sender_name END,
                                sender_username=CASE WHEN ?<>'' THEN ? ELSE sender_username END
                            WHERE sender_id=? AND (
                                (?<>'' AND COALESCE(sender_name,'')<>?) OR
                                (?<>'' AND ltrim(COALESCE(sender_username,''),'@')<>?)
                            )""",
                        (name, name, username, username, sender_id,
                         name, name, username, username),
                    )
                    updated[table] += int(cursor.rowcount or 0)
        updated["total"] = sum(updated.values())
        return updated

    def reset_tracking_data(self) -> None:
        """Remove scraped/imported tracking data while preserving 65 group IDs.

        This is used once when the supplied 01--18 Aug workbook is installed as
        the authoritative base. Telegram authentication and discovered groups
        remain untouched.
        """
        with self.connect() as con:
            con.execute("DELETE FROM banner_details")
            con.execute("DELETE FROM banner_forms")
            con.execute("DELETE FROM locations")
            con.execute("DELETE FROM invalid_reports")
            con.execute("DELETE FROM outlet_visits")
            con.execute("DELETE FROM sync_checkpoints")
            con.execute("DELETE FROM scrape_coverage")
            con.execute("DELETE FROM app_state WHERE key LIKE 'banner_%'")

    def groups(self) -> list[sqlite3.Row]:
        """Return only the original 65 Execution groups.

        The central Display Product group is deliberately excluded so Banner
        and Outlet Visit scraping, base checkpoints, and exports retain their
        existing 65-group behavior.
        """
        with self.connect() as con:
            return con.execute(
                """SELECT * FROM telegram_groups
                   WHERE is_active=1 AND source_kind='EXECUTION'
                   ORDER BY region,dealer"""
            ).fetchall()

    def product_display_history_complete(self, group_id: int, date_from: datetime, date_to: datetime) -> bool:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo("Asia/Phnom_Penh")
        start = date_from.astimezone(zone).date()
        end = (date_to.astimezone(zone) - timedelta(microseconds=1)).date()
        with self.connect() as con:
            count = con.execute(
                """SELECT COUNT(*) FROM scrape_coverage WHERE track='PRODUCT_DISPLAY'
                   AND group_id=? AND report_date BETWEEN ? AND ?""",
                (group_id, start.isoformat(), end.isoformat()),
            ).fetchone()[0]
        return count == (end - start).days + 1

    def product_display_groups(self) -> list[sqlite3.Row]:
        """Product Display shares the 65 active dealer Execution sources."""
        return self.groups()

    def banner_mapping_groups(self) -> list[sqlite3.Row]:
        """Return the central historical Banner Mapping source."""
        with self.connect() as con:
            return con.execute(
                """SELECT * FROM telegram_groups
                   WHERE is_active=1 AND source_kind='BANNER_MAPPING'
                   ORDER BY group_name,group_id"""
            ).fetchall()

    def base_status(self) -> dict[str, object]:
        with self.connect() as con:
            forms = con.execute("SELECT COUNT(*) FROM banner_forms").fetchone()[0]
            details = con.execute("SELECT COUNT(*) FROM banner_details").fetchone()[0]
            groups = con.execute(
                """SELECT COUNT(*) FROM telegram_groups
                   WHERE is_active=1 AND source_kind='EXECUTION'"""
            ).fetchone()[0]
            dates = con.execute("SELECT MIN(substr(submitted_at,1,10)), MAX(substr(submitted_at,1,10)) FROM banner_forms").fetchone()
            checkpoint_join = (
                " FROM sync_checkpoints s JOIN telegram_groups g ON g.group_id=s.group_id "
                " WHERE g.source_kind='EXECUTION' AND g.is_active=1 AND "
            )
            checkpoints = con.execute(
                "SELECT COUNT(*)" + checkpoint_join +
                "(s.last_message_id>0 OR COALESCE(s.last_message_date,'')<>'')"
            ).fetchone()[0]
            id_checkpoints = con.execute(
                "SELECT COUNT(*)" + checkpoint_join + "s.last_message_id>0"
            ).fetchone()[0]
            date_checkpoints = con.execute(
                "SELECT COUNT(*)" + checkpoint_join +
                "s.last_message_id=0 AND COALESCE(s.last_message_date,'')<>''"
            ).fetchone()[0]
            rows = con.execute("""SELECT g.region,g.dealer,COALESCE(s.last_message_id,0) AS last_message_id,
                                        COALESCE(s.last_message_date,'') AS last_message_date
                                 FROM telegram_groups g LEFT JOIN sync_checkpoints s ON s.group_id=g.group_id
                                 WHERE g.is_active=1 AND g.source_kind='EXECUTION'
                                 ORDER BY g.region,g.dealer""").fetchall()
        return {
            "groups": groups, "forms": forms, "banner_details": details,
            "date_from": dates[0], "date_to": dates[1],
            "groups_with_checkpoints": checkpoints,
            "groups_with_message_id_checkpoints": id_checkpoints,
            "groups_with_date_fallback": date_checkpoints,
            "checkpoints": [dict(r) for r in rows],
        }

    def sync_checkpoint(self, group_id: int) -> sqlite3.Row | None:
        """Return the durable per-group Telegram synchronization checkpoint."""
        with self.connect() as con:
            return con.execute(
                "SELECT * FROM sync_checkpoints WHERE group_id=?", (group_id,)
            ).fetchone()

    def bootstrap_last_message_id(self, group_id: int) -> int:
        """Find the newest already-stored message for databases created before checkpoints."""
        with self.connect() as con:
            row = con.execute(
                """SELECT MAX(message_id) FROM (
                       SELECT message_id FROM banner_forms WHERE group_id=?
                       UNION ALL SELECT message_id FROM locations WHERE group_id=?
                       UNION ALL SELECT message_id FROM invalid_reports WHERE group_id=?
                   )""",
                (group_id, group_id, group_id),
            ).fetchone()
            return int(row[0] or 0)

    def save_sync_checkpoint(self, *, group_id: int, last_message_id: int,
                             last_message_date: datetime | None = None,
                             history_from: datetime | None = None,
                             history_to: datetime | None = None,
                             history_complete: bool | None = None) -> None:
        """Atomically save progress after one group finishes."""
        existing = self.sync_checkpoint(group_id)
        old_id = int(existing["last_message_id"] or 0) if existing else 0
        complete = int(history_complete if history_complete is not None else (
            bool(existing["history_complete"]) if existing else False
        ))
        with self.connect() as con:
            con.execute(
                """INSERT INTO sync_checkpoints(
                       group_id,last_message_id,last_message_date,history_from,history_to,
                       history_complete,updated_at
                   ) VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(group_id) DO UPDATE SET
                       last_message_id=MAX(sync_checkpoints.last_message_id,excluded.last_message_id),
                       last_message_date=COALESCE(excluded.last_message_date,sync_checkpoints.last_message_date),
                       history_from=COALESCE(excluded.history_from,sync_checkpoints.history_from),
                       history_to=COALESCE(excluded.history_to,sync_checkpoints.history_to),
                       history_complete=excluded.history_complete,
                       updated_at=excluded.updated_at""",
                (group_id, max(old_id, int(last_message_id)),
                 last_message_date.isoformat() if last_message_date else None,
                 history_from.isoformat() if history_from else None,
                 history_to.isoformat() if history_to else None,
                complete, datetime.now(timezone.utc).isoformat()),
            )

    def replace_sync_position(self, *, group_id: int, last_message_id: int,
                              last_message_date: datetime) -> None:
        """Replace an imported checkpoint after Telegram proves its ID invalid."""
        with self.connect() as con:
            con.execute(
                """UPDATE sync_checkpoints
                   SET last_message_id=?,last_message_date=?,updated_at=?
                   WHERE group_id=?""",
                (int(last_message_id), last_message_date.isoformat(),
                 datetime.now(timezone.utc).isoformat(), group_id),
            )

    def set_sync_checkpoint_exact(self, *, group_id: int, last_message_id: int,
                                  last_message_date: datetime,
                                  history_from: datetime,
                                  history_to: datetime) -> None:
        """Install an authoritative base boundary without MAX(old imported ID)."""
        with self.connect() as con:
            con.execute(
                """INSERT INTO sync_checkpoints(
                       group_id,last_message_id,last_message_date,history_from,history_to,
                       history_complete,updated_at
                   ) VALUES(?,?,?,?,?,1,?)
                   ON CONFLICT(group_id) DO UPDATE SET
                       last_message_id=excluded.last_message_id,
                       last_message_date=excluded.last_message_date,
                       history_from=excluded.history_from,
                       history_to=excluded.history_to,
                       history_complete=1,
                       updated_at=excluded.updated_at""",
                (group_id, int(last_message_id), last_message_date.isoformat(),
                 history_from.isoformat(), history_to.isoformat(),
                 datetime.now(timezone.utc).isoformat()),
            )

    def history_is_complete(self, group_id: int, date_from: datetime, date_to: datetime) -> bool:
        checkpoint = self.sync_checkpoint(group_id)
        return bool(
            checkpoint and checkpoint["history_complete"]
            and checkpoint["history_from"] == date_from.isoformat()
            and checkpoint["history_to"] == date_to.isoformat()
        )

    def save_form(self, *, group_id: int, message_id: int, submitted_at: datetime,
                  sender_id: int | None, sender_name: str, sender_username: str,
                  region: str, dealer: str, form: ParsedForm, items: list[BannerItem], raw_text: str,
                  format_review: bool = False) -> None:
        statuses = {x.status for x in items}
        parse_status = "EMPTY_BANNER" if not form.banner_text else (
            "NEED_REVIEW" if any(x not in {"OK", "SPECIAL"} for x in statuses) else "OK"
        )
        if format_review and parse_status == "OK":
            parse_status = "NEED_REVIEW"
        with self.connect() as con:
            con.execute(
                """INSERT INTO banner_forms(group_id,message_id,submitted_at,sender_id,sender_name,sender_username,
                   region,dealer,form_dealer,outlet_name,phone,outlet_type,original_banner_text,parse_status,raw_text,
                   product_display,remark_issues)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(group_id,message_id) DO UPDATE SET
                   submitted_at=excluded.submitted_at,sender_id=excluded.sender_id,sender_name=excluded.sender_name,
                   sender_username=excluded.sender_username,region=excluded.region,dealer=excluded.dealer,
                   form_dealer=excluded.form_dealer,outlet_name=excluded.outlet_name,phone=excluded.phone,
                   outlet_type=excluded.outlet_type,original_banner_text=excluded.original_banner_text,
                   parse_status=excluded.parse_status,raw_text=excluded.raw_text,
                   product_display=excluded.product_display,remark_issues=excluded.remark_issues""",
                (group_id, message_id, submitted_at.isoformat(), sender_id, sender_name, sender_username,
                 region, dealer, form.dealer, form.outlet_name, form.phone, form.outlet_type,
                 form.banner_text, parse_status, raw_text, form.product_display, form.remark_issues),
            )
            con.execute("DELETE FROM banner_details WHERE group_id=? AND message_id=?", (group_id, message_id))
            con.executemany(
                "INSERT INTO banner_details VALUES(?,?,?,?,?,?)",
                [(group_id, message_id, x.official_name, x.quantity, x.original_token, x.status) for x in items],
            )

    def save_outlet_visit(self, *, group_id: int, message_id: int, submitted_at: datetime,
                          sender_id: int | None, sender_name: str, sender_username: str,
                          region: str, dealer: str, visit: OutletVisit, raw_text: str) -> None:
        q = visit.quantities
        with self.connect() as con:
            con.execute(
                """INSERT INTO outlet_visits(
                   group_id,message_id,submitted_at,sender_id,sender_name,sender_username,
                   region,dealer,form_dealer,outlet_name,phone,outlet_type,
                   emergency_outlet,attacking_stock,tour_program,key_wholesale,water,c_cola,
                   ize_pet_300ml,ize_pet_1500ml,remark_issues,raw_values,field_statuses,parse_status,raw_text)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(group_id,message_id) DO UPDATE SET
                   submitted_at=excluded.submitted_at,sender_id=excluded.sender_id,
                   sender_name=excluded.sender_name,sender_username=excluded.sender_username,
                   region=excluded.region,dealer=excluded.dealer,form_dealer=excluded.form_dealer,
                   outlet_name=excluded.outlet_name,phone=excluded.phone,outlet_type=excluded.outlet_type,
                   emergency_outlet=excluded.emergency_outlet,attacking_stock=excluded.attacking_stock,
                   tour_program=excluded.tour_program,key_wholesale=excluded.key_wholesale,
                   water=excluded.water,c_cola=excluded.c_cola,
                   ize_pet_300ml=excluded.ize_pet_300ml,ize_pet_1500ml=excluded.ize_pet_1500ml,
                   remark_issues=excluded.remark_issues,
                   raw_values=excluded.raw_values,field_statuses=excluded.field_statuses,
                   parse_status=excluded.parse_status,raw_text=excluded.raw_text""",
                (group_id,message_id,submitted_at.isoformat(),sender_id,sender_name,sender_username,
                 region,dealer,visit.dealer,visit.outlet_name,visit.phone,visit.outlet_type,
                 q["emergency_outlet"],q["attacking_stock"],q["tour_program"],q["key_wholesale"],
                 q["water"],q["c_cola"],q["ize_pet_300ml"],q["ize_pet_1500ml"],
                 visit.remark_issues,
                 json.dumps(visit.raw_values,ensure_ascii=False),
                 json.dumps(visit.field_statuses,ensure_ascii=False),visit.parse_status,raw_text),
            )

    def rebuild_outlet_visits(self, start_date: str | None = None,
                              end_date: str | None = None) -> dict[str, int]:
        """Parse Outlet Visit only from the original 65 Execution groups."""
        # Rows created by very old builds may predate telegram_groups. Treat
        # those orphans as legacy Execution rows; an explicitly tagged central
        # Product Display group is still excluded.
        conditions = [
            "(g.group_id IS NULL OR g.source_kind='EXECUTION')",
            "(g.group_id IS NULL OR g.is_active=1)",
        ]
        params: list[str] = []
        if start_date:
            # submitted_at is stored in Cambodia local ISO format.  Comparing
            # its YYYY-MM-DD prefix avoids SQLite converting +07:00 to UTC and
            # moving early-morning reports to the previous day.
            conditions.append("substr(src.submitted_at,1,10) >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("substr(src.submitted_at,1,10) <= ?")
            params.append(end_date)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connect() as con:
            rows = con.execute(
                f"""SELECT src.group_id,src.message_id,src.submitted_at,src.sender_id,
                           src.sender_name,src.sender_username,src.region,src.dealer,src.raw_text
                    FROM invalid_reports src
                    LEFT JOIN telegram_groups g ON g.group_id=src.group_id{where}
                    UNION ALL
                    SELECT src.group_id,src.message_id,src.submitted_at,src.sender_id,
                           src.sender_name,src.sender_username,src.region,src.dealer,src.raw_text
                    FROM banner_forms src
                    LEFT JOIN telegram_groups g ON g.group_id=src.group_id{where}""",
                tuple(params + params),
            ).fetchall()
        seen = set(); checked = candidates = saved = 0
        for row in rows:
            key = (row["group_id"],row["message_id"])
            # Never rebuild/export an Outlet Visit from a guideline account.
            # The five guideline names are an EXCLUSION list; all other
            # Telegram accounts remain eligible for tracking.
            if is_excluded_telegram_name(row["sender_name"] or ""):
                continue
            if key in seen:
                continue
            seen.add(key); checked += 1
            parsed = parse_outlet_visit(row["raw_text"] or "")
            if not parsed.is_candidate or parsed.visit is None:
                continue
            candidates += 1
            submitted = datetime.fromisoformat(row["submitted_at"])
            self.save_outlet_visit(
                group_id=row["group_id"],message_id=row["message_id"],submitted_at=submitted,
                sender_id=row["sender_id"],sender_name=row["sender_name"] or "",
                sender_username=row["sender_username"] or "",region=row["region"],dealer=row["dealer"],
                visit=parsed.visit,raw_text=row["raw_text"] or "",
            )
            self.attach_existing_location_to_outlet(
                row["group_id"],row["message_id"],row["sender_id"],submitted,60
            )
            saved += 1
        return {"messages_checked":checked,"outlet_visit_candidates":candidates,"saved":saved}

    def attach_existing_location_to_outlet(self, group_id: int, message_id: int,
                                           sender_id: int | None, submitted_at: datetime,
                                           window_minutes: int) -> bool:
        lower = (submitted_at - timedelta(minutes=window_minutes)).isoformat()
        upper = (submitted_at + timedelta(minutes=window_minutes)).isoformat()
        with self.connect() as con:
            row = con.execute(
                """SELECT message_id,latitude,longitude FROM locations
                   WHERE group_id=? AND reply_to_message_id=?
                   ORDER BY ABS(strftime('%s',sent_at)-strftime('%s',?)) LIMIT 1""",
                (group_id,message_id,submitted_at.isoformat()),
            ).fetchone()
            if not row and sender_id is not None:
                row = con.execute(
                    """SELECT message_id,latitude,longitude FROM locations
                       WHERE group_id=? AND sender_id=? AND sent_at BETWEEN ? AND ?
                         AND matched_form_message_id IS NULL
                       ORDER BY ABS(strftime('%s',sent_at)-strftime('%s',?)) LIMIT 1""",
                    (group_id,sender_id,lower,upper,submitted_at.isoformat()),
                ).fetchone()
            if not row:
                return False
            con.execute("UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                        (message_id,group_id,row["message_id"]))
            con.execute(
                """UPDATE outlet_visits SET latitude=?,longitude=?,gps_status='GPS_MATCHED'
                   WHERE group_id=? AND message_id=?""",
                (row["latitude"],row["longitude"],group_id,message_id),
            )
            return True

    def rebuild_banner_details(self, normalizer: BannerNormalizer,
                               start_date: str | None = None,
                               end_date: str | None = None) -> dict[str, int]:
        """Reparse the authoritative Banner source and replace derived rows.

        Banner Mapping owns 01--18 Aug 2026; the 65 Execution groups own
        19-Aug onward.  Keeping that boundary here prevents duplicate sources
        from changing MTD totals between reruns.
        """
        predicates = [
            "(g.group_id IS NULL "
            "OR (substr(f.submitted_at,1,10)<='2026-08-18' AND g.source_kind='BANNER_MAPPING') "
            "OR (substr(f.submitted_at,1,10)>='2026-08-19' AND g.source_kind='EXECUTION'))"
        ]
        params: tuple[str, ...] = ()
        if start_date and end_date:
            predicates.append("substr(f.submitted_at,1,10) BETWEEN ? AND ?")
            params = (start_date, end_date)
        elif start_date:
            predicates.append("substr(f.submitted_at,1,10) >= ?")
            params = (start_date,)
        elif end_date:
            predicates.append("substr(f.submitted_at,1,10) <= ?")
            params = (end_date,)
        where = "WHERE " + " AND ".join(predicates)

        with self.connect() as con:
            rows = con.execute(
                f"""SELECT f.group_id,f.message_id,f.dealer,f.outlet_name,f.phone,
                           f.latitude,f.longitude,f.original_banner_text,f.raw_text
                      FROM banner_forms f
                      LEFT JOIN telegram_groups g ON g.group_id=f.group_id
                      {where}""", params
            ).fetchall()
            forms_with_banner = detail_rows = total_quantity = fields_repaired = 0
            for row in rows:
                reparsed = parse_form(row["raw_text"] or "")
                outlet_name = row["outlet_name"] or ""
                phone = row["phone"] or ""
                banner_text = row["original_banner_text"] or ""
                if reparsed is not None:
                    outlet_name = reparsed.outlet_name or outlet_name
                    phone = reparsed.phone or phone
                    banner_text = reparsed.banner_text or banner_text
                    changed = (
                        outlet_name != (row["outlet_name"] or "")
                        or phone != (row["phone"] or "")
                        or banner_text != (row["original_banner_text"] or "")
                    )
                    if changed:
                        con.execute(
                            """UPDATE banner_forms SET form_dealer=?,outlet_name=?,phone=?,
                                   outlet_type=?,original_banner_text=?,product_display=?,remark_issues=?
                               WHERE group_id=? AND message_id=?""",
                            (reparsed.dealer, outlet_name, phone, reparsed.outlet_type,
                             banner_text, reparsed.product_display, reparsed.remark_issues,
                             row["group_id"], row["message_id"]),
                        )
                        fields_repaired += 1
                items = normalizer.parse_for_record(
                    outlet_name, phone, row["latitude"], row["longitude"], banner_text,
                )
                forms_with_banner += bool(items)
                con.execute("DELETE FROM banner_details WHERE group_id=? AND message_id=?",
                            (row["group_id"], row["message_id"]))
                con.executemany(
                    "INSERT INTO banner_details VALUES(?,?,?,?,?,?)",
                    [(row["group_id"], row["message_id"], item.official_name, item.quantity,
                      item.original_token, item.status) for item in items],
                )
                # Revalidate stored source text with the current parser.  An
                # older parser may have written a stale invalid_reports row
                # for a spelling that is now recognized (for example
                # ``Outlet Nam:``).  Keeping that stale flag would continue to
                # exclude a repaired, valid form from the Excel export while
                # the warehouse still counted its Banner quantity.
                validation = validate_report(
                    row["raw_text"] or "", row["dealer"], normalizer
                )
                format_review = not validation.is_valid
                if validation.is_valid:
                    con.execute(
                        "DELETE FROM invalid_reports WHERE group_id=? AND message_id=?",
                        (row["group_id"], row["message_id"]),
                    )
                status = "EMPTY_BANNER" if not items else (
                    "NEED_REVIEW" if any(item.status not in {"OK", "SPECIAL"} for item in items) else "OK"
                )
                if format_review and status == "OK":
                    status = "NEED_REVIEW"
                con.execute("UPDATE banner_forms SET parse_status=? WHERE group_id=? AND message_id=?",
                            (status, row["group_id"], row["message_id"]))
                detail_rows += len(items)
                total_quantity += sum(item.quantity for item in items if item.status == "OK")
        return {"forms_checked": len(rows), "fields_repaired": fields_repaired,
                "forms_with_banner": int(forms_with_banner),
                "detail_rows": detail_rows, "total_banner_quantity": total_quantity}

    def rebuild_product_display(self, start_date: str | None = None,
                                end_date: str | None = None) -> dict[str, int]:
        """Normalize Product Display rows from the active dealer Execution sources."""
        predicates = [
            "g.source_kind='EXECUTION'",
            "(g.group_id IS NULL OR g.is_active=1)",
        ]
        params: tuple[str, ...] = ()
        if start_date and end_date:
            predicates.append("substr(f.submitted_at,1,10) BETWEEN ? AND ?")
            params = (start_date, end_date)
        elif start_date:
            predicates.append("substr(f.submitted_at,1,10) >= ?")
            params = (start_date,)
        elif end_date:
            predicates.append("substr(f.submitted_at,1,10) <= ?")
            params = (end_date,)
        where = "WHERE " + " AND ".join(predicates)
        counts = {"forms_checked": 0, "product_display_fields": 0,
                  "product_display_yes": 0,
                  "product_display_no": 0, "product_display_blank_or_other": 0}
        with self.connect() as con:
            rows = con.execute(
                f"""SELECT f.group_id,f.message_id,f.raw_text
                    FROM banner_forms f
                    LEFT JOIN telegram_groups g ON g.group_id=f.group_id
                    {where}""",
                params,
            ).fetchall()
            counts["forms_checked"] = len(rows)
            for row in rows:
                status = product_display_status(row["raw_text"] or "")
                stored = parse_product_display(row["raw_text"] or "")
                key = "product_display_yes" if status == "Yes" else (
                    "product_display_no" if status == "No" else "product_display_blank_or_other"
                )
                counts[key] += 1
                counts["product_display_fields"] += int(stored == "Yes")
                con.execute(
                    "UPDATE banner_forms SET product_display=? WHERE group_id=? AND message_id=?",
                    (stored, row["group_id"], row["message_id"]),
                )
        return counts

    def recover_forms_from_saved_sources(
        self,
        normalizer: BannerNormalizer,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, int]:
        """Recover Banner/Product rows already saved by another tracker.

        Older releases stopped after recognizing an Outlet Visit form.  The raw
        Telegram text was saved in ``outlet_visits`` but the same message's
        Banner Type and Product Display fields were not inserted into
        ``banner_forms``.  This idempotent repair reads those existing sources,
        validates them with the current tolerant parser, and saves every report
        containing a meaningful Banner Type or Product Display Yes.  Telegram
        is not contacted and no message is duplicated.
        """
        predicates: list[str] = []
        params: list[str] = []
        if start_date:
            predicates.append("substr(submitted_at,1,10) >= ?")
            params.append(start_date)
        if end_date:
            predicates.append("substr(submitted_at,1,10) <= ?")
            params.append(end_date)
        where = " WHERE " + " AND ".join(predicates) if predicates else ""

        with self.connect() as con:
            outlet_rows = con.execute(
                f"""SELECT group_id,message_id,submitted_at,sender_id,sender_name,sender_username,
                            region,dealer,raw_text,latitude,longitude FROM outlet_visits{where}""",
                tuple(params),
            ).fetchall()
            invalid_rows = con.execute(
                f"""SELECT group_id,message_id,submitted_at,sender_id,sender_name,sender_username,
                            region,dealer,raw_text,NULL AS latitude,NULL AS longitude
                     FROM invalid_reports{where}""",
                tuple(params),
            ).fetchall()
            existing = {
                (int(row[0]), int(row[1]))
                for row in con.execute("SELECT group_id,message_id FROM banner_forms")
            }

        seen: set[tuple[int, int]] = set()
        recovered_data: list[tuple[sqlite3.Row, ParsedForm, tuple[BannerItem, ...], bool]] = []
        checked = recovered = banner_rows = product_rows = 0
        for row in [*outlet_rows, *invalid_rows]:
            key = (int(row["group_id"]), int(row["message_id"]))
            if key in existing or key in seen:
                continue
            seen.add(key)
            # Recovery feeds Banner Tracking, so keep every sender here.
            # Output-specific exporters apply any guideline-account filter
            # required by Outlet/Product/Combined reports.
            checked += 1
            raw_text = row["raw_text"] or ""
            validation = validate_report(raw_text, row["dealer"], normalizer)
            form = validation.form
            if form is None:
                continue
            meaningful_banner = has_meaningful_banner_text(form.banner_text)
            product_yes = parse_product_display(raw_text) == "Yes"
            if not meaningful_banner and not product_yes:
                continue
            recovered_data.append((row, form, validation.items, not validation.is_valid))
            recovered += 1
            banner_rows += int(meaningful_banner)
            product_rows += int(product_yes)

        # One transaction is substantially faster than opening SQLite twice
        # for every recovered row (thousands of rows on 19--20 August).
        with self.connect() as con:
            for row, form, items, format_review in recovered_data:
                statuses = {item.status for item in items}
                parse_status = "EMPTY_BANNER" if not form.banner_text else (
                    "NEED_REVIEW" if any(status not in {"OK", "SPECIAL"} for status in statuses) else "OK"
                )
                if format_review and parse_status == "OK":
                    parse_status = "NEED_REVIEW"
                gps_status = "GPS_MATCHED" if row["latitude"] is not None and row["longitude"] is not None else "GPS_MISSING"
                con.execute(
                    """INSERT OR IGNORE INTO banner_forms(
                       group_id,message_id,submitted_at,sender_id,sender_name,sender_username,
                       region,dealer,form_dealer,outlet_name,phone,outlet_type,
                       original_banner_text,latitude,longitude,gps_status,parse_status,raw_text,
                       product_display,remark_issues)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (row["group_id"], row["message_id"], row["submitted_at"], row["sender_id"],
                     row["sender_name"] or "", row["sender_username"] or "", row["region"], row["dealer"],
                     form.dealer, form.outlet_name, form.phone, form.outlet_type, form.banner_text,
                     row["latitude"], row["longitude"], gps_status, parse_status, row["raw_text"] or "",
                     form.product_display, form.remark_issues),
                )
                con.executemany(
                    "INSERT OR REPLACE INTO banner_details VALUES(?,?,?,?,?,?)",
                    [(row["group_id"], row["message_id"], item.official_name, item.quantity,
                      item.original_token, item.status) for item in items],
                )
        return {
            "sources_checked": checked,
            "forms_recovered": recovered,
            "banner_reports_recovered": banner_rows,
            "product_display_yes_recovered": product_rows,
        }

    def save_invalid_report(self, *, group_id: int, message_id: int, submitted_at: datetime,
                            sender_id: int | None, sender_name: str, sender_username: str,
                            region: str, dealer: str, error_codes: list[str],
                            error_message: str, raw_text: str) -> bool:
        """Save a bad report and return True only while no reply alert was sent."""
        with self.connect() as con:
            con.execute(
                """INSERT INTO invalid_reports(group_id,message_id,submitted_at,sender_id,sender_name,
                   sender_username,region,dealer,error_codes,error_message,raw_text,alert_sent)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,0)
                   ON CONFLICT(group_id,message_id) DO UPDATE SET
                   submitted_at=excluded.submitted_at,sender_id=excluded.sender_id,
                   sender_name=excluded.sender_name,sender_username=excluded.sender_username,
                   region=excluded.region,dealer=excluded.dealer,error_codes=excluded.error_codes,
                   error_message=excluded.error_message,raw_text=excluded.raw_text""",
                (group_id, message_id, submitted_at.isoformat(), sender_id, sender_name, sender_username,
                 region, dealer, "|".join(error_codes), error_message, raw_text),
            )
            row = con.execute(
                "SELECT alert_sent FROM invalid_reports WHERE group_id=? AND message_id=?",
                (group_id, message_id),
            ).fetchone()
            return bool(row and not row["alert_sent"])

    def mark_invalid_alert_sent(self, group_id: int, message_id: int) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE invalid_reports SET alert_sent=1 WHERE group_id=? AND message_id=?",
                (group_id, message_id),
            )

    def save_location(self, *, group_id: int, message_id: int, sent_at: datetime, sender_id: int | None,
                      latitude: float, longitude: float, reply_to_message_id: int | None,
                      window_minutes: int, allow_unique_nearby: bool = False) -> int | None:
        matched = None
        with self.connect() as con:
            if reply_to_message_id:
                row = con.execute(
                    "SELECT message_id FROM banner_forms WHERE group_id=? AND message_id=?",
                    (group_id, reply_to_message_id),
                ).fetchone()
                matched = row[0] if row else None
            if matched is None and allow_unique_nearby:
                # Central Banner Mapping frequently forwards the form and map
                # through different outer senders. Match without sender only
                # when exactly one form exists in the preceding five minutes;
                # ambiguity stays unmatched rather than assigning a wrong GPS.
                lower = (sent_at - timedelta(minutes=5)).isoformat()
                candidates = con.execute(
                    """SELECT message_id FROM banner_forms
                       WHERE group_id=? AND submitted_at BETWEEN ? AND ?
                         AND message_id < ?
                       ORDER BY message_id DESC""",
                    (group_id, lower, sent_at.isoformat(), message_id),
                ).fetchall()
                if len(candidates) == 1:
                    matched = candidates[0][0]
            if matched is None and sender_id is not None:
                lower = (sent_at - timedelta(minutes=window_minutes)).isoformat()
                row = con.execute(
                    """SELECT message_id FROM banner_forms WHERE group_id=? AND sender_id=?
                       AND submitted_at BETWEEN ? AND ? AND message_id < ?
                       ORDER BY message_id DESC LIMIT 1""",
                    (group_id, sender_id, lower, sent_at.isoformat(), message_id),
                ).fetchone()
                matched = row[0] if row else None
            con.execute(
                """INSERT INTO locations VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(group_id,message_id) DO UPDATE SET
                   sent_at=excluded.sent_at,sender_id=excluded.sender_id,latitude=excluded.latitude,
                   longitude=excluded.longitude,reply_to_message_id=excluded.reply_to_message_id,
                   matched_form_message_id=excluded.matched_form_message_id""",
                (group_id, message_id, sent_at.isoformat(), sender_id, latitude, longitude, reply_to_message_id, matched),
            )
            if matched is not None:
                con.execute(
                    "UPDATE banner_forms SET latitude=?,longitude=?,gps_status='GPS_MATCHED' WHERE group_id=? AND message_id=?",
                    (latitude, longitude, group_id, matched),
                )
        return matched

    def attach_existing_location(self, group_id: int, form_message_id: int, sender_id: int | None,
                                 submitted_at: datetime, window_minutes: int) -> bool:
        if sender_id is None:
            return False
        lower = (submitted_at - timedelta(minutes=window_minutes)).isoformat()
        with self.connect() as con:
            row = con.execute(
                """SELECT message_id,latitude,longitude FROM locations WHERE group_id=? AND sender_id=?
                   AND sent_at BETWEEN ? AND ? AND message_id < ?
                   AND matched_form_message_id IS NULL ORDER BY message_id DESC LIMIT 1""",
                (group_id, sender_id, lower, submitted_at.isoformat(), form_message_id),
            ).fetchone()
            if not row:
                return False
            con.execute("UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                        (form_message_id, group_id, row["message_id"]))
            con.execute("UPDATE banner_forms SET latitude=?,longitude=?,gps_status='GPS_MATCHED' WHERE group_id=? AND message_id=?",
                        (row["latitude"], row["longitude"], group_id, form_message_id))
            return True

    def rebuild_outlet_visit_gps_links(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        same_sender_window_minutes: int = 60,
    ) -> dict[str, int]:
        """Recover Outlet Visit coordinates from Telegram location replies.

        Direct ``reply_to_message_id`` matches are authoritative even when a
        supervisor forwards/posts the map pin. Only remaining rows may use a
        same-group, same-sender location inside the configured time window.
        """
        where = ["(v.latitude IS NULL OR v.longitude IS NULL)"]
        params: list[object] = []
        if start_date:
            where.append("substr(v.submitted_at,1,10) >= ?")
            params.append(start_date)
        if end_date:
            where.append("substr(v.submitted_at,1,10) <= ?")
            params.append(end_date)
        predicate = " AND ".join(where)
        counts = {"checked": 0, "direct_reply": 0, "same_sender": 0, "still_missing": 0}

        with self.connect() as con:
            visits = con.execute(
                f"""SELECT v.group_id,v.message_id,v.sender_id,v.submitted_at
                    FROM outlet_visits v WHERE {predicate}
                    ORDER BY v.submitted_at,v.group_id,v.message_id""",
                tuple(params),
            ).fetchall()
            counts["checked"] = len(visits)

            for visit in visits:
                location = con.execute(
                    """SELECT message_id,latitude,longitude FROM locations
                       WHERE group_id=? AND reply_to_message_id=?
                       ORDER BY ABS(strftime('%s',sent_at)-strftime('%s',?)),message_id
                       LIMIT 1""",
                    (visit["group_id"], visit["message_id"], visit["submitted_at"]),
                ).fetchone()
                if not location:
                    continue
                con.execute(
                    "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                    (visit["message_id"], visit["group_id"], location["message_id"]),
                )
                con.execute(
                    """UPDATE outlet_visits SET latitude=?,longitude=?,gps_status='GPS_MATCHED_REPLY'
                       WHERE group_id=? AND message_id=?""",
                    (location["latitude"], location["longitude"],
                     visit["group_id"], visit["message_id"]),
                )
                counts["direct_reply"] += 1

            remaining = con.execute(
                f"""SELECT v.group_id,v.message_id,v.sender_id,v.submitted_at
                    FROM outlet_visits v WHERE {predicate} AND v.sender_id IS NOT NULL
                    ORDER BY v.submitted_at,v.group_id,v.message_id""",
                tuple(params),
            ).fetchall()
            seconds = max(1, int(same_sender_window_minutes)) * 60
            for visit in remaining:
                location = con.execute(
                    """SELECT message_id,latitude,longitude FROM locations
                       WHERE group_id=? AND sender_id=?
                         AND (matched_form_message_id IS NULL OR matched_form_message_id=?)
                         AND ABS(strftime('%s',sent_at)-strftime('%s',?)) <= ?
                       ORDER BY ABS(strftime('%s',sent_at)-strftime('%s',?)),message_id
                       LIMIT 1""",
                    (visit["group_id"], visit["sender_id"], visit["message_id"],
                     visit["submitted_at"], seconds, visit["submitted_at"]),
                ).fetchone()
                if not location:
                    continue
                con.execute(
                    "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                    (visit["message_id"], visit["group_id"], location["message_id"]),
                )
                con.execute(
                    """UPDATE outlet_visits SET latitude=?,longitude=?,gps_status='GPS_MATCHED_RECOVERED'
                       WHERE group_id=? AND message_id=?""",
                    (location["latitude"], location["longitude"],
                     visit["group_id"], visit["message_id"]),
                )
                counts["same_sender"] += 1

            count_where = ["(latitude IS NULL OR longitude IS NULL)"]
            count_params: list[object] = []
            if start_date:
                count_where.append("substr(submitted_at,1,10) >= ?")
                count_params.append(start_date)
            if end_date:
                count_where.append("substr(submitted_at,1,10) <= ?")
                count_params.append(end_date)
            counts["still_missing"] = int(con.execute(
                f"SELECT COUNT(*) FROM outlet_visits WHERE {' AND '.join(count_where)}",
                tuple(count_params),
            ).fetchone()[0])
        return counts

    def rebuild_gps_links(self, start_date: str | None = None, end_date: str | None = None,
                          same_sender_window_minutes: int = 60) -> dict[str, int]:
        """Recover high-confidence GPS links already stored in the database.

        Priority:
        1. A Telegram location directly replies to the form, even if another
           team member sent the location.
        2. Banner Mapping bundle sequence: the only map after a form/album and
           before the next form.
        3. Same group + same sender + nearest unused location within the
           configured recovery window.
        4. Different/forwarded sender only when exactly one missing form is in
           the preceding five-minute window in the same group.
        5. Exact duplicate identity (dealer + outlet + phone + local date) from
           another source when it has one unique coordinate pair.

        Coordinates are never copied from an unrelated sender merely to fill a
        blank cell.
        """
        where = ["(f.latitude IS NULL OR f.longitude IS NULL)"]
        params: list[object] = []
        if start_date:
            where.append("substr(f.submitted_at,1,10) >= ?")
            params.append(start_date)
        if end_date:
            where.append("substr(f.submitted_at,1,10) <= ?")
            params.append(end_date)
        predicate = " AND ".join(where)
        counts = {
            "checked": 0, "direct_reply": 0, "mapping_sequence": 0,
            "mapping_reverse_sequence": 0, "reverse_same_sender": 0, "same_sender": 0,
            "unique_nearby": 0, "identity_duplicate": 0, "still_missing": 0,
        }

        with self.connect() as con:
            forms = con.execute(
                f"""SELECT f.group_id,f.message_id,f.sender_id,f.submitted_at
                    FROM banner_forms f WHERE {predicate}
                    ORDER BY f.submitted_at,f.group_id,f.message_id""",
                tuple(params),
            ).fetchall()
            counts["checked"] = len(forms)

            # Direct reply is an exact relationship and overrides sender mismatch.
            for form in forms:
                location = con.execute(
                    """SELECT message_id,latitude,longitude FROM locations
                       WHERE group_id=? AND reply_to_message_id=?
                       ORDER BY ABS(strftime('%s',sent_at)-strftime('%s',?)),message_id LIMIT 1""",
                    (form["group_id"], form["message_id"], form["submitted_at"]),
                ).fetchone()
                if not location:
                    continue
                con.execute(
                    "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                    (form["message_id"], form["group_id"], location["message_id"]),
                )
                con.execute(
                    """UPDATE banner_forms SET latitude=?,longitude=?,gps_status='GPS_MATCHED_REPLY'
                       WHERE group_id=? AND message_id=?""",
                    (location["latitude"], location["longitude"], form["group_id"], form["message_id"]),
                )
                counts["direct_reply"] += 1

            # Banner Mapping is a chronological stream of forwarded bundles:
            # caption/form, album photos, then one map. A single unused map
            # between this form and the next form is a deterministic match,
            # even when the outer forwarded sender is different.
            mapping_groups = [row[0] for row in con.execute(
                """SELECT group_id FROM telegram_groups
                   WHERE source_kind='BANNER_MAPPING' AND is_active=1"""
            ).fetchall()]
            for group_id in mapping_groups:
                mapping_forms = con.execute(
                    f"""SELECT message_id,submitted_at FROM banner_forms f
                         WHERE group_id=? AND {predicate}
                         ORDER BY message_id""",
                    (group_id, *params),
                ).fetchall()
                all_group_forms = con.execute(
                    "SELECT message_id FROM banner_forms WHERE group_id=? ORDER BY message_id",
                    (group_id,),
                ).fetchall()
                next_form_by_id = {
                    int(row["message_id"]): (
                        int(all_group_forms[index + 1]["message_id"])
                        if index + 1 < len(all_group_forms) else None
                    )
                    for index, row in enumerate(all_group_forms)
                }
                for form in mapping_forms:
                    next_form_id = next_form_by_id.get(int(form["message_id"]))
                    if next_form_id is None:
                        locations = con.execute(
                            """SELECT message_id,latitude,longitude FROM locations
                               WHERE group_id=? AND matched_form_message_id IS NULL
                                 AND message_id>? AND message_id<=? ORDER BY message_id""",
                            (group_id, form["message_id"], int(form["message_id"]) + 20),
                        ).fetchall()
                    else:
                        locations = con.execute(
                            """SELECT message_id,latitude,longitude FROM locations
                               WHERE group_id=? AND matched_form_message_id IS NULL
                                 AND message_id>? AND message_id<? ORDER BY message_id""",
                            (group_id, form["message_id"], next_form_id),
                        ).fetchall()
                    if len(locations) != 1:
                        continue
                    location = locations[0]
                    con.execute(
                        "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                        (form["message_id"], group_id, location["message_id"]),
                    )
                    con.execute(
                        """UPDATE banner_forms SET latitude=?,longitude=?,
                               gps_status='GPS_MATCHED_MAPPING_SEQUENCE'
                           WHERE group_id=? AND message_id=?""",
                        (location["latitude"], location["longitude"],
                         group_id, form["message_id"]),
                    )
                    counts["mapping_sequence"] += 1

                # Some forwarded bundles arrive in the opposite order: the
                # map (or a forwarded map card) is stored immediately before
                # the caption/form.  Match the only unused location between
                # the previous form and this form.  The one-location rule is
                # important: it fills deterministic pairs without assigning a
                # random coordinate when several maps exist in that interval.
                missing_forms = con.execute(
                    f"""SELECT message_id,submitted_at FROM banner_forms f
                         WHERE group_id=? AND {predicate}
                         ORDER BY message_id""",
                    (group_id, *params),
                ).fetchall()
                all_form_ids = [int(row["message_id"]) for row in all_group_forms]
                previous_form_by_id = {
                    message_id: (all_form_ids[index - 1] if index else None)
                    for index, message_id in enumerate(all_form_ids)
                }
                for form in missing_forms:
                    form_id = int(form["message_id"])
                    previous_form_id = previous_form_by_id.get(form_id)
                    lower_id = previous_form_id if previous_form_id is not None else form_id - 20
                    locations = con.execute(
                        """SELECT message_id,latitude,longitude FROM locations
                           WHERE group_id=? AND matched_form_message_id IS NULL
                             AND message_id>? AND message_id<?
                           ORDER BY message_id""",
                        (group_id, lower_id, form_id),
                    ).fetchall()
                    if len(locations) != 1:
                        continue
                    location = locations[0]
                    con.execute(
                        "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                        (form_id, group_id, location["message_id"]),
                    )
                    con.execute(
                        """UPDATE banner_forms SET latitude=?,longitude=?,
                               gps_status='GPS_MATCHED_MAPPING_REVERSE_SEQUENCE'
                           WHERE group_id=? AND message_id=?""",
                        (location["latitude"], location["longitude"], group_id, form_id),
                    )
                    counts["mapping_reverse_sequence"] += 1

            # Execution groups also contain map-first bundles.  Recover only
            # an unused map from the same sender, sent during the five minutes
            # immediately before the form. This handles a native map followed
            # by a caption while avoiding cross-user assignments in busy
            # dealer groups.
            reverse_forms = con.execute(
                f"""SELECT f.group_id,f.message_id,f.sender_id,f.submitted_at
                    FROM banner_forms f WHERE {predicate} AND f.sender_id IS NOT NULL
                    ORDER BY f.submitted_at,f.group_id,f.message_id""",
                tuple(params),
            ).fetchall()
            for form in reverse_forms:
                # Strongest map-first signal: the immediately preceding native
                # location is from the same sender and has no reply target.
                # It may have been attached to an earlier form by an old
                # proximity-only build, so this exact relationship is allowed
                # to repair that stale link.
                exact_location = con.execute(
                    """SELECT message_id,latitude,longitude FROM locations
                       WHERE group_id=? AND sender_id=? AND message_id=?
                         AND reply_to_message_id IS NULL""",
                    (form["group_id"], form["sender_id"], int(form["message_id"]) - 1),
                ).fetchone()
                if exact_location:
                    con.execute(
                        "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                        (form["message_id"], form["group_id"], exact_location["message_id"]),
                    )
                    con.execute(
                        """UPDATE banner_forms SET latitude=?,longitude=?,
                               gps_status='GPS_MATCHED_REVERSE_SAME_SENDER'
                           WHERE group_id=? AND message_id=?""",
                        (exact_location["latitude"], exact_location["longitude"],
                         form["group_id"], form["message_id"]),
                    )
                    counts["reverse_same_sender"] += 1
                    continue
                location = con.execute(
                    """SELECT message_id,latitude,longitude FROM locations
                       WHERE group_id=? AND sender_id=?
                         AND matched_form_message_id IS NULL
                         AND sent_at<=?
                         AND strftime('%s',?)-strftime('%s',sent_at) BETWEEN 0 AND 300
                       ORDER BY sent_at DESC,message_id DESC LIMIT 1""",
                    (form["group_id"], form["sender_id"], form["submitted_at"],
                     form["submitted_at"]),
                ).fetchone()
                if not location:
                    continue
                # Do not cross another form by the same sender; in that case
                # the map belongs to the closer preceding report.
                intervening = con.execute(
                    """SELECT 1 FROM banner_forms
                       WHERE group_id=? AND sender_id=? AND message_id>?
                         AND message_id<? LIMIT 1""",
                    (form["group_id"], form["sender_id"], location["message_id"],
                     form["message_id"]),
                ).fetchone()
                if intervening:
                    continue
                con.execute(
                    "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                    (form["message_id"], form["group_id"], location["message_id"]),
                )
                con.execute(
                    """UPDATE banner_forms SET latitude=?,longitude=?,
                           gps_status='GPS_MATCHED_REVERSE_SAME_SENDER'
                       WHERE group_id=? AND message_id=?""",
                    (location["latitude"], location["longitude"],
                     form["group_id"], form["message_id"]),
                )
                counts["reverse_same_sender"] += 1

            remaining = con.execute(
                f"""SELECT f.group_id,f.message_id,f.sender_id,f.submitted_at
                    FROM banner_forms f WHERE {predicate} AND f.sender_id IS NOT NULL
                    ORDER BY f.submitted_at,f.group_id,f.message_id""",
                tuple(params),
            ).fetchall()
            seconds = max(1, int(same_sender_window_minutes)) * 60
            for form in remaining:
                location = con.execute(
                    """SELECT message_id,latitude,longitude FROM locations
                       WHERE group_id=? AND sender_id=? AND matched_form_message_id IS NULL
                         AND ABS(strftime('%s',sent_at)-strftime('%s',?)) <= ?
                       ORDER BY ABS(strftime('%s',sent_at)-strftime('%s',?)),message_id LIMIT 1""",
                    (form["group_id"], form["sender_id"], form["submitted_at"], seconds,
                     form["submitted_at"]),
                ).fetchone()
                if not location:
                    continue
                con.execute(
                    "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                    (form["message_id"], form["group_id"], location["message_id"]),
                )
                con.execute(
                    """UPDATE banner_forms SET latitude=?,longitude=?,gps_status='GPS_MATCHED_RECOVERED'
                       WHERE group_id=? AND message_id=?""",
                    (location["latitude"], location["longitude"], form["group_id"], form["message_id"]),
                )
                counts["same_sender"] += 1

            # Historical data may already contain a map whose forwarded outer
            # sender differs from the form sender, or whose reply points to an
            # album photo. Recover it only when the same group has exactly one
            # still-missing form immediately before that unused map.
            unused_locations = con.execute(
                """SELECT group_id,message_id,sent_at,latitude,longitude
                   FROM locations WHERE matched_form_message_id IS NULL
                   ORDER BY sent_at,group_id,message_id"""
            ).fetchall()
            for location in unused_locations:
                lower = (datetime.fromisoformat(location["sent_at"]) - timedelta(minutes=5)).isoformat()
                candidate_params: list[object] = [
                    location["group_id"], lower, location["sent_at"], location["message_id"],
                ]
                candidate_where = [
                    "group_id=?", "submitted_at BETWEEN ? AND ?", "message_id < ?",
                    "(latitude IS NULL OR longitude IS NULL)",
                ]
                if start_date:
                    candidate_where.append("substr(submitted_at,1,10) >= ?")
                    candidate_params.append(start_date)
                if end_date:
                    candidate_where.append("substr(submitted_at,1,10) <= ?")
                    candidate_params.append(end_date)
                candidates = con.execute(
                    f"""SELECT message_id FROM banner_forms
                         WHERE {' AND '.join(candidate_where)}
                         ORDER BY message_id DESC""",
                    tuple(candidate_params),
                ).fetchall()
                if len(candidates) != 1:
                    continue
                form_message_id = candidates[0]["message_id"]
                con.execute(
                    "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                    (form_message_id, location["group_id"], location["message_id"]),
                )
                con.execute(
                    """UPDATE banner_forms SET latitude=?,longitude=?,
                           gps_status='GPS_MATCHED_UNIQUE_NEARBY'
                       WHERE group_id=? AND message_id=?""",
                    (location["latitude"], location["longitude"],
                     location["group_id"], form_message_id),
                )
                counts["unique_nearby"] += 1

            # Banner Mapping and the historical/manual source can contain the
            # same report. Copy coordinates only for a unique exact identity;
            # this repairs a missing mapping row without using time proximity.
            all_forms = [dict(row) for row in con.execute(
                """SELECT group_id,message_id,submitted_at,dealer,outlet_name,phone,
                          latitude,longitude FROM banner_forms"""
            ).fetchall()]

            def identity_key(row: dict) -> tuple[str, str, str, str]:
                outlet = " ".join(str(row.get("outlet_name") or "").replace("\u00a0", " ").split()).casefold()
                phone = "".join(ch for ch in str(row.get("phone") or "") if ch.isdigit())
                return (
                    str(row.get("dealer") or "").replace(" ", "").upper(),
                    outlet, phone, str(row.get("submitted_at") or "")[:10],
                )

            coordinate_index: dict[tuple[str, str, str, str], set[tuple[float, float]]] = {}
            for row in all_forms:
                if row["latitude"] is None or row["longitude"] is None:
                    continue
                key = identity_key(row)
                if not key[0] or not key[1] or not key[2]:
                    continue
                coordinate_index.setdefault(key, set()).add(
                    (round(float(row["latitude"]), 7), round(float(row["longitude"]), 7))
                )
            for form in all_forms:
                if form["latitude"] is not None and form["longitude"] is not None:
                    continue
                form_date = str(form["submitted_at"] or "")[:10]
                if start_date and form_date < start_date:
                    continue
                if end_date and form_date > end_date:
                    continue
                coordinates = coordinate_index.get(identity_key(form), set())
                if len(coordinates) != 1:
                    continue
                latitude, longitude = next(iter(coordinates))
                con.execute(
                    """UPDATE banner_forms SET latitude=?,longitude=?,
                           gps_status='GPS_MATCHED_IDENTITY_DUPLICATE'
                       WHERE group_id=? AND message_id=?""",
                    (latitude, longitude, form["group_id"], form["message_id"]),
                )
                # Link the actual unmatched map in this source when its
                # coordinates equal the verified duplicate coordinates.
                location = con.execute(
                    """SELECT message_id FROM locations
                       WHERE group_id=? AND matched_form_message_id IS NULL
                         AND ABS(latitude-?) < 0.00002 AND ABS(longitude-?) < 0.00002
                       ORDER BY ABS(message_id-?) LIMIT 1""",
                    (form["group_id"], latitude, longitude, form["message_id"]),
                ).fetchone()
                if location:
                    con.execute(
                        "UPDATE locations SET matched_form_message_id=? WHERE group_id=? AND message_id=?",
                        (form["message_id"], form["group_id"], location["message_id"]),
                    )
                counts["identity_duplicate"] += 1

            count_params: list[object] = []
            count_where = ["(latitude IS NULL OR longitude IS NULL)"]
            if start_date:
                count_where.append("substr(submitted_at,1,10) >= ?")
                count_params.append(start_date)
            if end_date:
                count_where.append("substr(submitted_at,1,10) <= ?")
                count_params.append(end_date)
            counts["still_missing"] = con.execute(
                f"SELECT COUNT(*) FROM banner_forms WHERE {' AND '.join(count_where)}",
                tuple(count_params),
            ).fetchone()[0]
        return counts

    def form_message_exists(self, group_id: int, message_id: int) -> bool:
        """Return whether a Telegram message is already stored as a form."""
        with self.connect() as con:
            return con.execute(
                "SELECT 1 FROM banner_forms WHERE group_id=? AND message_id=?",
                (group_id, message_id),
            ).fetchone() is not None

    def rebuild_product_display_gps_links(
        self, start_date: str | None = None, end_date: str | None = None,
        same_sender_window_minutes: int = 60, max_message_gap: int = 40,
    ) -> dict[str, int]:
        """Recover missing Execution GPS without clearing existing shared links."""
        return self.rebuild_gps_links(start_date, end_date, same_sender_window_minutes)
