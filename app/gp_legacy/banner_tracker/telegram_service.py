from __future__ import annotations

import asyncio
import re
import sqlite3
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.network.connection.tcpabridged import ConnectionTcpAbridged

try:  # Unit-test Telegram stubs intentionally expose no telethon.utils module.
    from telethon.utils import get_peer_id as _telethon_get_peer_id
except ImportError:  # pragma: no cover - exercised by the lightweight test stub
    _telethon_get_peer_id = None

from .database import Database
from .groups import EXPECTED_REGION_DEALERS, region_for_dealer
from .parser import (
    BannerNormalizer,
    ParsedForm,
    has_meaningful_banner_text,
    parse_product_display,
    parse_product_display_form,
    validate_report,
)
from .outlet_visit import parse_outlet_visit
from .settings import Settings
from .telegram_identity import (
    BANNER_ALL_SENDERS_BACKFILL_STATE,
    BANNER_FORWARDED_SENDER_BACKFILL_STATE,
    stable_telegram_id,
)


GROUP_NAME_RE = re.compile(r"(?i)\b(R[1-8])\s*[-_ ]\s*([A-Z]{2,5}\d+)\b")


def _peer_id(peer) -> int | None:
    """Get Telethon's numeric peer ID without requiring an entity lookup."""
    if peer is None:
        return None
    if _telethon_get_peer_id is not None:
        try:
            return int(_telethon_get_peer_id(peer))
        except (TypeError, ValueError):
            pass
    for attribute, transform in (
        ("user_id", lambda value: value),
        ("chat_id", lambda value: -value),
        ("channel_id", lambda value: -(1_000_000_000_000 + value)),
        ("id", lambda value: value),
    ):
        value = getattr(peer, attribute, None)
        if value is not None:
            try:
                return int(transform(int(value)))
            except (TypeError, ValueError):
                return None
    try:
        return int(peer)
    except (TypeError, ValueError):
        return None


class TelegramBannerTracker:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.normalizer = BannerNormalizer(settings.aliases_path)

        # Use Telegram's compact TCP transport and explicit timeouts/retries.
        # This prevents discover/rescrape from appearing frozen forever when
        # Windows, an ISP, or a firewall cannot reach Telegram.
        self.client = self._build_client()
        self._sender_cache: dict[int, tuple[int | None, str, str]] = {}
        self._scrape_semaphore = asyncio.Semaphore(max(1, settings.scrape_concurrency))

    def _build_client(self):
        """Create one Telethon client for the configured persistent session."""
        return TelegramClient(
            str(self.settings.session_path),
            self.settings.api_id,
            self.settings.api_hash,
            connection=ConnectionTcpAbridged,
            timeout=self.settings.telegram_connect_timeout,
            request_retries=self.settings.telegram_request_retries,
            connection_retries=self.settings.telegram_connection_retries,
            retry_delay=self.settings.telegram_retry_delay,
            auto_reconnect=True,
        )

    async def refresh_group_members(self) -> dict:
        """Refresh current membership of the active dealer Execution groups."""
        from .telegram_members import refresh_members
        return await refresh_members(self)

    async def disconnect(self) -> None:
        """Disconnect Telegram and close Telethon's SQLite session handle."""
        try:
            if self.client.is_connected():
                await self.client.disconnect()
        finally:
            # Telethon normally closes the session during disconnect.  Calling
            # close as a final safeguard releases a handle left by a cancelled
            # Jupyter cell before the next Run All.
            session = getattr(self.client, "session", None)
            close = getattr(session, "close", None)
            if callable(close):
                try:
                    close()
                except sqlite3.OperationalError:
                    # A failed commit can leave Telethon's private connection
                    # open. Closing the handle without deleting the session
                    # preserves authorization and releases this process's lock.
                    connection = getattr(session, "_conn", None)
                    if connection is not None:
                        connection.close()
                        try:
                            session._conn = None
                        except Exception:
                            pass

    @staticmethod
    def _is_session_lock_error(exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).casefold()

    @staticmethod
    def _is_transient_telegram_error(exc: BaseException) -> bool:
        """Recognize retryable Telegram/Telethon startup failures.

        Telethon 1.44 can turn repeated server-side RPC failures into the
        generic ``ValueError: Request was unsuccessful N time(s)``.  Treat
        only that exact ValueError shape (plus known temporary RPC/network
        errors) as retryable; ordinary programming ValueErrors still fail
        immediately.
        """
        message = str(exc).casefold()
        class_name = type(exc).__name__.casefold()
        if isinstance(exc, ValueError):
            return "request was unsuccessful" in message
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return True
        return (
            "rpcmcgetfail" in class_name
            or "servererror" in class_name
            or "telegram is having internal issues" in message
            or "server closed the connection" in message
            or "connection reset" in message
        )

    async def _reset_client_after_failed_connect(self) -> None:
        """Close a failed client and create a clean client for the next try."""
        try:
            await self.disconnect()
        except Exception:
            pass
        self.client = self._build_client()

    async def _connect_with_session_lock_retry(self, timeout: int) -> None:
        """Retry session locks and temporary Telegram startup failures."""
        attempts = max(2, min(5, self.settings.telegram_connection_retries + 1))
        for attempt in range(1, attempts + 1):
            try:
                await asyncio.wait_for(self.client.connect(), timeout=timeout)
                return
            except sqlite3.OperationalError as exc:
                if not self._is_session_lock_error(exc):
                    raise
                try:
                    await self.disconnect()
                except Exception:
                    pass
                if attempt >= attempts:
                    raise RuntimeError(
                        "Telegram session database is locked after retrying. "
                        "Another Jupyter kernel or Python process is using "
                        f"{self.settings.session_path}. Close the other running notebook/kernel "
                        "once, then rerun the Connect cell. Do not delete the .session file."
                    ) from exc
                print(f"Telegram session is briefly locked; retrying ({attempt}/{attempts - 1})...")
                await asyncio.sleep(self.settings.telegram_retry_delay)
                self.client = self._build_client()
            except Exception as exc:
                if not self._is_transient_telegram_error(exc):
                    raise
                await self._reset_client_after_failed_connect()
                if attempt >= attempts:
                    raise RuntimeError(
                        "Telegram is temporarily unavailable after "
                        f"{attempts} connection attempts. The project and login "
                        "session are not damaged. Wait 1-2 minutes, then rerun "
                        "only the TELEGRAM CONNECTION cell. If it continues, "
                        "check the Internet/VPN/firewall path to Telegram. "
                        f"Last Telegram error: {type(exc).__name__}: {exc}"
                    ) from exc
                print(
                    "Telegram returned a temporary server/network error; "
                    f"reconnecting ({attempt}/{attempts - 1})..."
                )
                await asyncio.sleep(self.settings.telegram_retry_delay)

    async def start(self, code_callback=None, password_callback=None) -> None:
        print("\n" + "=" * 70)
        print("TELEGRAM CONNECTION")
        print("=" * 70)
        print(f"Python session : {self.settings.session_path}")
        print(f"Connect timeout: {self.settings.telegram_connect_timeout}s")
        print(f"Conn retries   : {self.settings.telegram_connection_retries}")
        print(f"Request retries: {self.settings.telegram_request_retries}")
        print("Connecting to Telegram...")

        try:
            if self.client.is_connected():
                print("Reusing the Telegram connection already open in this notebook.")
            else:
                # Connect first so network failures are reported separately
                # from authentication/login failures.
                await self._connect_with_session_lock_retry(
                    timeout=max(5, self.settings.telegram_connect_timeout *
                                max(1, self.settings.telegram_connection_retries + 1) +
                                self.settings.telegram_retry_delay *
                                max(0, self.settings.telegram_connection_retries))
                )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "Telegram connection timed out.\n\n"
                "Python/Telethon is installed correctly, but this computer "
                "did not establish a Telegram connection within the configured "
                "retry window.\n\n"
                "Run these Windows tests:\n"
                "  Test-NetConnection 149.154.167.50 -Port 443\n"
                "  Test-NetConnection 149.154.167.91 -Port 443\n"
                "If TcpTestSucceeded is False, fix the network/firewall/VPN/proxy "
                "path to Telegram before running the scraper."
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"Telegram network connection failed: {exc}\n\n"
                "Check Internet/firewall/VPN/proxy access to Telegram."
            ) from exc

        if not self.client.is_connected():
            raise RuntimeError("Telethon returned without an active Telegram connection.")

        print("✓ Telegram network connection established.")

        try:
            if await self.client.is_user_authorized():
                me = await self.client.get_me()
                name = " ".join(
                    x for x in [getattr(me, "first_name", ""), getattr(me, "last_name", "")]
                    if x
                ).strip() if me else "Unknown"
                username = getattr(me, "username", None) if me else None
                print(f"✓ Telegram session authorized: {name or 'Unknown'}" +
                      (f" (@{username})" if username else ""))
                return

            print("Telegram session is not authorized; starting login...")
            kwargs = {"phone": self.settings.phone or None}
            if code_callback is not None:
                kwargs["code_callback"] = code_callback
            if password_callback is not None:
                kwargs["password"] = password_callback

            await self.client.start(**kwargs)
            print("✓ Telegram login successful.")
        except Exception:
            # Never leave a half-open connection after authentication failure.
            await self.disconnect()
            raise

    async def discover_groups(self) -> list[dict]:
        """Discover only the original 65 Execution groups."""
        results: list[dict] = []
        seen: dict[tuple[str, str], int] = {}
        self.db.begin_group_discovery()
        keyword = self.settings.group_keyword.casefold()
        async for dialog in self.client.iter_dialogs():
            if not dialog.is_group or keyword not in dialog.name.casefold():
                continue
            match = GROUP_NAME_RE.search(dialog.name)
            if not match:
                results.append({"group_id": int(dialog.id), "name": dialog.name, "status": "NAME_REVIEW"})
                continue
            region, dealer = match.group(1).upper(), match.group(2).upper()
            if (region, dealer) not in EXPECTED_REGION_DEALERS:
                results.append({"group_id": int(dialog.id), "name": dialog.name, "region": region,
                                "dealer": dealer, "status": "UNEXPECTED_DEALER"})
                continue
            key = (region, dealer)
            if key in seen:
                results.append({"group_id": int(dialog.id), "name": dialog.name, "region": region,
                                "dealer": dealer, "status": "DUPLICATE_GROUP"})
                continue
            seen[key] = int(dialog.id)
            self.db.upsert_group(int(dialog.id), dialog.name, region, dealer)
            results.append({"group_id": int(dialog.id), "name": dialog.name, "region": region, "dealer": dealer, "status": "OK"})
        return results

    async def discover_product_display_group(self) -> list[dict]:
        """Discover the 65 dealer Execution sources used by Product Display."""
        return await self.discover_groups()

    async def discover_banner_mapping_group(self) -> list[dict]:
        """Discover the exact central group used for Banner data on 01--18 Aug."""
        results: list[dict] = []
        target = " ".join(self.settings.banner_mapping_group_name.casefold().split())
        self.db.begin_group_discovery("BANNER_MAPPING")
        async for dialog in self.client.iter_dialogs():
            if not dialog.is_group:
                continue
            if " ".join(str(dialog.name or "").casefold().split()) != target:
                continue
            self.db.upsert_group(
                int(dialog.id), dialog.name, "BM", "MAPPING",
                source_kind="BANNER_MAPPING",
            )
            results.append({"group_id": int(dialog.id), "name": dialog.name, "status": "OK"})
        if not results:
            results.append({"group_id": 0, "name": self.settings.banner_mapping_group_name,
                            "status": "NOT_FOUND"})
        return results

    async def _sender(self, message) -> tuple[int | None, str, str]:
        """
        Resolve the Telegram account that sent a message.

        Telegram history messages do not always have a fully populated
        ``message.sender`` object in the local cache.  In that situation the
        old implementation could save only sender_id and leave name/username
        blank.  This resolver uses several safe fallbacks:

        1. message.sender
        2. message.get_sender()
        3. client.get_entity(sender_id)
        4. message.post_author for anonymous/channel-style posts

        The returned values are always normalized strings so the database and
        Excel exporters receive the same identity fields for every form.
        """

        sender_id = getattr(message, "sender_id", None)
        sender = getattr(message, "sender", None)

        if sender_id is not None and int(sender_id) in self._sender_cache:
            return self._sender_cache[int(sender_id)]

        # 1) Local Telethon cache.
        if sender is None and sender_id is not None:
            try:
                sender = await message.get_sender()
            except Exception:
                sender = None

        # 2) Explicit entity lookup.  This is the important fallback for
        # historical messages where the sender entity was not cached.
        if sender is None and sender_id is not None:
            try:
                sender = await self.client.get_entity(sender_id)
            except Exception:
                sender = None

        if sender is not None:
            resolved_id = getattr(sender, "id", None) or sender_id

            first_name = getattr(sender, "first_name", None) or ""
            last_name = getattr(sender, "last_name", None) or ""
            title = getattr(sender, "title", None) or ""

            name = " ".join(
                part.strip()
                for part in (first_name, last_name)
                if str(part).strip()
            ).strip()

            # Channels/anonymous senders may expose title rather than
            # first_name/last_name.
            if not name:
                name = str(title).strip()

            username = getattr(sender, "username", None) or ""
            username = str(username).strip().lstrip("@")

            result = (resolved_id, name, username)
            if sender_id is not None:
                self._sender_cache[int(sender_id)] = result
            return result

        # 3) Anonymous admin / channel-style posts can expose a display
        # author even when Telegram does not expose a User entity.
        post_author = getattr(message, "post_author", None) or ""
        post_author = str(post_author).strip()

        # Keep sender_id whenever Telegram supplied it.  If no profile entity
        # exists, the ID is still useful for GPS matching and auditing.
        result = (sender_id, post_author, "")
        if sender_id is not None:
            self._sender_cache[int(sender_id)] = result
        return result

    async def _product_display_sender(self, message) -> tuple[int | None, str, str]:
        """Prefer the original author shown on a forwarded Product Display post.

        The central group contains forwarded reports. Counting the account that
        clicked Forward would assign many dealers to one admin. Telethon keeps
        the original public sender in ``fwd_from`` when Telegram exposes it;
        privacy-hidden forwards fall back to their visible original name.
        """
        forwarded = getattr(message, "fwd_from", None)
        original_peer = getattr(forwarded, "from_id", None) if forwarded else None
        peer_id = None
        if original_peer is not None:
            try:
                peer_id = _peer_id(original_peer)
            except (TypeError, ValueError):
                peer_id = None
            try:
                entity = await self.client.get_entity(original_peer)
            except Exception:
                entity = None
            if entity is not None:
                sender_id = peer_id or getattr(entity, "id", None)
                name = " ".join(
                    str(part).strip()
                    for part in (
                        getattr(entity, "first_name", None),
                        getattr(entity, "last_name", None),
                    )
                    if str(part or "").strip()
                ).strip()
                if not name:
                    name = str(getattr(entity, "title", None) or "").strip()
                username = str(getattr(entity, "username", None) or "").strip().lstrip("@")
                return sender_id, name, username

        original_name = str(
            getattr(forwarded, "from_name", None) or ""
        ).strip() if forwarded else ""
        if original_name:
            return stable_telegram_id(
                peer_id, original_name, "",
                fallback_key=f"product-display:{getattr(message, 'id', '')}",
            ), original_name, ""
        if peer_id:
            return peer_id, "", ""
        sender_id, name, username = await self._sender(message)
        return stable_telegram_id(
            sender_id, name, username,
            fallback_key=f"product-display:{getattr(message, 'id', '')}",
        ), name, username

    async def _report_sender(self, message) -> tuple[int | None, str, str]:
        """Return the original forwarded author, otherwise the direct sender.

        This applies to every report source. Telegram's visible ``Forwarded
        from`` identity is the business reporter; the account that forwards it
        into another group must not replace that identity in Banner Tracking.
        """
        return await self._product_display_sender(message)

    @staticmethod
    def _product_display_sender_id_quick(message) -> int:
        """Resolve a GPS identity without a network profile request."""
        forwarded = getattr(message, "fwd_from", None)
        original_peer = getattr(forwarded, "from_id", None) if forwarded else None
        peer_id = None
        if original_peer is not None:
            try:
                peer_id = _peer_id(original_peer)
            except (TypeError, ValueError):
                peer_id = None
        original_name = str(
            getattr(forwarded, "from_name", None) or ""
        ).strip() if forwarded else ""
        return stable_telegram_id(
            peer_id or getattr(message, "sender_id", None),
            original_name,
            "",
            fallback_key=f"product-display:{getattr(message, 'id', '')}",
        )

    async def process_product_display_message(self, message, group: dict) -> str:
        """Collect display reports with the shared Execution parser."""
        if group.get("source_kind", "EXECUTION") == "EXECUTION":
            # Use the shared parser so a Product Display rerun preserves Banner
            # quantities and dealer identity stored on this same Telegram form.
            status = await self.process_message(message, group, track="BANNER")
            if status.startswith("GPS_"):
                return "PRODUCT_DISPLAY_" + status
            if "FORM_SAVED" in status and parse_product_display(message.raw_text or "") == "Yes":
                return "PRODUCT_DISPLAY_SAVED"
            return status
        group_id = int(group["group_id"])
        sent_at = message.date
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=ZoneInfo("UTC"))
        sent_at = sent_at.astimezone(ZoneInfo(self.settings.timezone))
        if message.geo:
            sender_id = self._product_display_sender_id_quick(message)
            reply_id = await self._product_display_form_reply_id(message, group_id)
            matched = self.db.save_location(
                group_id=group_id, message_id=message.id, sent_at=sent_at,
                sender_id=sender_id, latitude=float(message.geo.lat),
                longitude=float(message.geo.long), reply_to_message_id=reply_id,
                window_minutes=self.settings.gps_window_minutes,
            )
            return f"PRODUCT_DISPLAY_GPS_{'MATCHED' if matched else 'SAVED'}"

        raw_text = message.raw_text or ""
        if parse_product_display(raw_text) != "Yes":
            return "IGNORED"

        parsed = parse_product_display_form(raw_text)
        if parsed is None:
            return "PRODUCT_DISPLAY_REVIEW"
        dealer = parsed.dealer.strip().upper()
        region = region_for_dealer(dealer)
        if not region:
            return "PRODUCT_DISPLAY_REVIEW"

        # Entity/profile lookup is the slowest part of a history run. Do it
        # only after the message is confirmed to be a Product Display form.
        sender_id, sender_name, sender_username = await self._product_display_sender(message)

        validation = validate_report(raw_text, dealer, self.normalizer)
        source_form = validation.form or parsed
        product_form = ParsedForm(
            dealer=dealer,
            outlet_name=source_form.outlet_name,
            phone=source_form.phone,
            outlet_type=source_form.outlet_type,
            # Central Display Product posts must never leak into Banner export.
            banner_text="",
            product_display="Yes",
            remark_issues=source_form.remark_issues,
        )
        self.db.save_form(
            group_id=group_id, message_id=message.id, submitted_at=sent_at,
            sender_id=sender_id, sender_name=sender_name,
            sender_username=sender_username, region=region, dealer=dealer,
            form=product_form, items=[], raw_text=raw_text,
            format_review=not validation.is_valid,
        )
        self.db.attach_existing_location(
            group_id, message.id, sender_id, sent_at,
            self.settings.gps_window_minutes,
        )
        return "PRODUCT_DISPLAY_SAVED"

    async def _product_display_form_reply_id(self, message, group_id: int, form_ids: set[int] | None = None) -> int | None:
        """Follow a Telegram reply chain from a forwarded map back to its form.

        Field teams frequently reply to one of the forwarded photos, so the
        map's direct ``reply_to_msg_id`` is not itself a form.  Follow at most
        five parent messages and return the first stored form message.  If the
        chain cannot be resolved, return the direct reply so database recovery
        can still pair the bundle chronologically.
        """
        direct_reply = (
            getattr(message, "reply_to_msg_id", None)
            or getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
        )
        reply_id = direct_reply
        current = message
        seen: set[int] = set()
        for _ in range(5):
            if not reply_id or int(reply_id) in seen:
                break
            reply_id = int(reply_id)
            seen.add(reply_id)
            if (reply_id in form_ids if form_ids is not None else self.db.form_message_exists(group_id, reply_id)):
                return reply_id
            try:
                parent = await current.get_reply_message()
                if parent is None or int(getattr(parent, "id", -1)) != reply_id:
                    parent = await self.client.get_messages(group_id, ids=reply_id)
            except Exception:
                break
            if parent is None:
                break
            current = parent
            reply_id = (
                getattr(parent, "reply_to_msg_id", None)
                or getattr(getattr(parent, "reply_to", None), "reply_to_msg_id", None)
            )
        return int(direct_reply) if direct_reply else None

    @staticmethod
    def _invalid_alert_text(sender_name: str, sender_username: str, group: dict,
                            errors: tuple[str, ...]) -> str:
        identity = sender_name or "Telegram user"
        if sender_username:
            identity += f" (@{sender_username.lstrip('@')})"
        problem_lines = "\n".join(f"• {error}" for error in errors)
        return (
            f"⚠️ REPORT FORMAT INCORRECT\n"
            f"User: {identity}\n"
            f"Group: {group['region']}-{group['dealer']}\n\n"
            f"Problems:\n{problem_lines}\n\n"
            "Please edit or resend using this format:\n"
            f"Dealer: {group['dealer']}\n"
            "Outlet Name:\nPhone:\nOutlet Type:\n"
            "Banner Type: CB Lite1,Dazz2\n"
            "POSM:\nPROGRAM:\n*Product Display:\n*Remark Issues:"
        )

    async def process_message(
        self,
        message,
        group: dict,
        allow_group_alert: bool = False,
        track: str = "ALL",
    ) -> str:
        """Process one Execution-group message for the requested tracker only.

        ``ALL`` preserves the normal live/continuation behavior. ``BANNER``
        and ``OUTLET_VISIT`` are used by the separate date-range notebooks so
        rerunning one report does not write rows for the other report.
        """
        track = str(track or "ALL").strip().upper()
        if track not in {"ALL", "BANNER", "OUTLET_VISIT"}:
            raise ValueError(
                "track must be 'ALL', 'BANNER', or 'OUTLET_VISIT'"
            )
        collect_banner = track in {"ALL", "BANNER"}
        collect_outlet = track in {"ALL", "OUTLET_VISIT"}
        group_id = int(group["group_id"])
        sent_at = message.date
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=ZoneInfo("UTC"))
        sent_at = sent_at.astimezone(ZoneInfo(self.settings.timezone))

        # Attendance is independent of the metric parser (short captions and
        # standalone pins must not disappear). Use the actual posting account.
        if collect_outlet and group.get("source_kind", "EXECUTION") == "EXECUTION" and not getattr(message, "action", None):
            activity_id, activity_name, activity_username = await self._sender(message)
            raw = getattr(message, "raw_text", "") or ""
            is_report = bool(re.search(r"(?im)^\s*[.*\s]*outlet\s*name\s*:", raw))
            self.db.save_gp_activity(group_id=group_id, message_id=message.id,
                submitted_at=sent_at, sender_id=activity_id, sender_name=activity_name,
                sender_username=activity_username,
                kind="LOCATION" if message.geo else "REPORT" if is_report else "MESSAGE")

        if message.geo:
            # GPS matching only needs the sender ID already present on the
            # message; avoid an extra Telegram profile request.
            sender_id = self._product_display_sender_id_quick(message)
            reply_id = (
                getattr(message, "reply_to_msg_id", None)
                or getattr(getattr(message, "reply_to", None), "reply_to_msg_id", None)
            )
            mapping_source = group.get("source_kind") == "BANNER_MAPPING"
            if reply_id and not self.db.form_message_exists(group_id, reply_id):
                # A pin can reply to a photo inside an album instead of the
                # caption/form. This happens in both Banner Mapping and the 65
                # Execution groups, so follow the reply chain for every source.
                reply_id = await self._product_display_form_reply_id(message, group_id)
            matched = self.db.save_location(
                group_id=group_id, message_id=message.id, sent_at=sent_at, sender_id=sender_id,
                latitude=float(message.geo.lat), longitude=float(message.geo.long),
                reply_to_message_id=reply_id, window_minutes=self.settings.gps_window_minutes,
                # If Telegram has no usable reply relation, match only when
                # exactly one preceding form exists in the safe nearby window.
                # Ambiguous cases remain GPS_MISSING instead of being guessed.
                allow_unique_nearby=True,
            )
            return f"GPS_{'MATCHED' if matched else 'SAVED'}"

        raw_text = message.raw_text or ""
        if collect_outlet and group.get("source_kind", "EXECUTION") == "EXECUTION":
            from .team_talk import capture_team_talk
            if capture_team_talk(self.db, group, message.id, sent_at, raw_text):
                return "TEAM_TALK_SAVED"
        saved_tracks: list[str] = []
        outlet_result = parse_outlet_visit(raw_text) if collect_outlet else None
        mapping_source = group.get("source_kind") == "BANNER_MAPPING"
        parsed_dealer = ""
        if mapping_source:
            match = re.search(r"(?im)^\s*\*?\s*dealer\s*[:=\-]?\s*([A-Z]{2,5}\d+)\b", raw_text)
            parsed_dealer = match.group(1).upper() if match else ""
        expected_dealer = parsed_dealer or group["dealer"]
        validation = validate_report(raw_text, expected_dealer, self.normalizer) if collect_banner else None
        outlet_candidate = bool(
            outlet_result is not None
            and outlet_result.is_candidate
            and outlet_result.visit is not None
        )
        banner_candidate = bool(validation is not None and validation.is_report_candidate)
        if not outlet_candidate and not banner_candidate:
            return "IGNORED"

        # Keep every Telegram sender in the source database, but avoid a
        # network profile lookup for photos/chat messages that are not forms.
        sender_id, sender_name, sender_username = await self._report_sender(message)
        report_region = region_for_dealer(expected_dealer) if mapping_source else group["region"]
        report_dealer = expected_dealer if mapping_source else group["dealer"]
        if (
            outlet_candidate
        ):
            assert outlet_result is not None and outlet_result.visit is not None
            visit = outlet_result.visit
            self.db.save_outlet_visit(
                group_id=group_id, message_id=message.id, submitted_at=sent_at,
                sender_id=sender_id, sender_name=sender_name, sender_username=sender_username,
                region=report_region, dealer=report_dealer, visit=visit, raw_text=raw_text,
            )
            self.db.attach_existing_location_to_outlet(
                group_id, message.id, sender_id, sent_at, self.settings.gps_window_minutes
            )
            # One Telegram form may contain Outlet Visit, Banner Type, and
            # Product Display fields.  Do not return here: older code did so
            # and lost the Banner/Product data for 19--20 August.
            saved_tracks.append("OUTLET_VISIT_SAVED")

        if not collect_banner:
            return "+".join(saved_tracks) if saved_tracks else "IGNORED"

        assert validation is not None
        if not validation.is_report_candidate:
            return "+".join(saved_tracks) if saved_tracks else "IGNORED"
        # Sender profile data is requested only for an actual form candidate,
        # not for every photo, video, chat message, or ignored post.
        if not validation.is_valid:
            alert_text = self._invalid_alert_text(sender_name, sender_username, group, validation.errors)
            alert_needed = self.db.save_invalid_report(
                group_id=group_id, message_id=message.id, submitted_at=sent_at,
                sender_id=sender_id, sender_name=sender_name, sender_username=sender_username,
                region=report_region, dealer=report_dealer,
                error_codes=list(validation.error_codes), error_message="; ".join(validation.errors),
                raw_text=raw_text,
            )
            mode = self.settings.invalid_report_alert_mode
            if allow_group_alert and alert_needed and mode == "reply":
                await message.reply(alert_text, link_preview=False)
                self.db.mark_invalid_alert_sent(group_id, message.id)
            elif mode == "log":
                print(alert_text)
            # Keep the alert/review record, but also preserve usable Banner or
            # Product Display data from a report whose surrounding form format
            # is imperfect.  The saved form remains marked NEED_REVIEW.
            form = validation.form
            if form is not None and (
                has_meaningful_banner_text(form.banner_text)
                or parse_product_display(raw_text) == "Yes"
            ):
                self.db.save_form(
                    group_id=group_id, message_id=message.id, submitted_at=sent_at,
                    sender_id=sender_id, sender_name=sender_name, sender_username=sender_username,
                    region=report_region, dealer=report_dealer, form=form,
                    items=list(validation.items), raw_text=raw_text, format_review=True,
                )
                self.db.attach_existing_location(
                    group_id, message.id, sender_id, sent_at, self.settings.gps_window_minutes
                )
                saved_tracks.append("FORM_SAVED")
            saved_tracks.append("INVALID_REPORT")
            return "+".join(saved_tracks)

        form = validation.form
        items = list(validation.items)
        assert form is not None
        self.db.save_form(
            group_id=group_id, message_id=message.id, submitted_at=sent_at,
            sender_id=sender_id, sender_name=sender_name, sender_username=sender_username,
            region=report_region, dealer=report_dealer, form=form, items=items, raw_text=raw_text,
        )
        self.db.attach_existing_location(group_id, message.id, sender_id, sent_at, self.settings.gps_window_minutes)
        saved_tracks.append("FORM_SAVED")
        return "+".join(saved_tracks)

    @staticmethod
    def _add_counts(total: dict[str, int], part: dict[str, int]) -> None:
        for key, value in part.items():
            total[key] = total.get(key, 0) + value

    @staticmethod
    def _count_message_status(result: dict[str, int], status: str) -> None:
        """Count every track saved by one multi-purpose Telegram message."""
        counted = False
        if "FORM_SAVED" in status:
            result["forms"] += 1
            counted = True
        if "OUTLET_VISIT_SAVED" in status:
            result["outlet_visits"] += 1
            counted = True
        if status == "TEAM_TALK_SAVED":
            result["team_talks"] = result.get("team_talks", 0) + 1
            counted = True
        if status.startswith("GPS_"):
            result["locations"] += 1
            counted = True
        if not counted:
            result["ignored"] += 1

    async def _history_group(
        self,
        group: dict,
        date_from: datetime,
        date_to: datetime,
        force: bool = False,
        track: str = "ALL",
        update_checkpoint: bool = True,
    ) -> dict[str, int]:
        result = {"groups": 1, "messages": 0, "forms": 0, "outlet_visits": 0, "locations": 0,
                  "ignored": 0, "errors": 0, "skipped_groups": 0}
        group_id = int(group["group_id"])
        if (
            update_checkpoint
            and not force
            and self.db.history_is_complete(group_id, date_from, date_to)
        ):
            result["skipped_groups"] = 1
            return result
        newest_id = self.db.bootstrap_last_message_id(group_id)
        newest_date = None
        try:
            async with self._scrape_semaphore:
                # iter_messages reads metadata/text only. It never downloads media.
                pending_messages = []
                async for message in self.client.iter_messages(
                    group_id, offset_date=date_to, wait_time=0
                ):
                    if message.date >= date_to:
                        continue
                    if message.date < date_from:
                        break
                    newest_id = max(newest_id, int(message.id))
                    if newest_date is None or message.date > newest_date:
                        newest_date = message.date
                    pending_messages.append(message)
                # Telethon history is newest-first. Process oldest-first so a
                # map can attach only to the form bundle that precedes it.
                for message in reversed(pending_messages):
                    result["messages"] += 1
                    status = await self.process_message(message, group, track=track)
                    self._count_message_status(result, status)
                await self._save_report_images(group_id, pending_messages)
            if update_checkpoint:
                self.db.save_sync_checkpoint(
                    group_id=group_id, last_message_id=newest_id,
                    last_message_date=newest_date, history_from=date_from,
                    history_to=date_to, history_complete=True,
                )
            self.db.mark_track_range_complete(track, group_id, date_from, date_to)
            if track in {"ALL", "OUTLET_VISIT"}:
                from .team_talk import TEAM_TALK_TRACK
                self.db.mark_track_range_complete(TEAM_TALK_TRACK, group_id, date_from, date_to)
            print(f"DONE {group['region']}-{group['dealer']}: {result['messages']} messages")
        except FloodWaitError as exc:
            result["errors"] += 1
            print(f"FLOOD WAIT {group['region']}-{group['dealer']}: retry after {exc.seconds}s")
            await asyncio.sleep(exc.seconds + 1)
        except Exception as exc:
            result["errors"] += 1
            print(f"ERROR {group['region']}-{group['dealer']}: {exc}")
        return result

    async def backfill(
        self,
        date_from: datetime,
        date_to: datetime,
        force: bool = False,
        track: str = "ALL",
        update_checkpoint: bool = True,
    ) -> dict[str, int]:
        """Scrape a complete history range.

        ``force=True`` deliberately ignores the matching checkpoint. This is
        used for the Aug'26 re-scrape requested from the supplied template/base
        data. Writes remain idempotent because forms use (group_id, message_id)
        as the database key.
        """
        groups = [dict(row) for row in self.db.groups()]
        parts = await asyncio.gather(*(
            self._history_group(
                group,
                date_from,
                date_to,
                force=force,
                track=track,
                update_checkpoint=update_checkpoint,
            )
            for group in groups
        ))
        total = {"groups": 0, "messages": 0, "forms": 0, "outlet_visits": 0, "locations": 0,
                 "ignored": 0, "errors": 0, "skipped_groups": 0}
        for part in parts:
            self._add_counts(total, part)
        if track in {"ALL", "OUTLET_VISIT"}:
            from .gp_so_roster import sync_so_list
            path = getattr(self.settings, 'gp_so_master_path', None)
            if path is not None and path.is_file():
                zone = ZoneInfo(self.settings.timezone)
                result = sync_so_list(self.db, path,
                    date_from.astimezone(zone).date().isoformat(),
                    (date_to - timedelta(microseconds=1)).astimezone(zone).date().isoformat())
                print(f"SO List sync: {result}")
        return total

    async def backfill_banner_sources(
        self, date_from: datetime, date_to: datetime, force: bool = False,
    ) -> dict[str, dict[str, int]]:
        """Route Banner dates: Mapping through 18-Aug, execution from 19-Aug."""
        zone = ZoneInfo(self.settings.timezone)
        boundary = datetime(2026, 8, 19, tzinfo=zone)
        result: dict[str, dict[str, int]] = {}
        if date_from < boundary:
            mapping_to = min(date_to, boundary)
            groups = [dict(r) for r in self.db.banner_mapping_groups()]
            if not groups:
                raise RuntimeError("Banner Mapping group is not configured. Run discover first.")
            parts = [await self._history_group(g, date_from, mapping_to, force=force,
                                               track="BANNER") for g in groups]
            result["banner_mapping"] = parts[0]
        if date_to > boundary:
            execution_from = max(date_from, boundary)
            parts = await asyncio.gather(*(self._history_group(
                dict(g), execution_from, date_to, force=force, track="BANNER"
            ) for g in self.db.groups()))
            total = {"groups": 0, "messages": 0, "forms": 0, "outlet_visits": 0,
                     "locations": 0, "ignored": 0, "errors": 0, "skipped_groups": 0}
            for part in parts:
                self._add_counts(total, part)
            result["execution_65_groups"] = total
        return result

    async def _save_report_images(self, group_id: int, messages: list) -> None:
        """Link verified photos by caption, album or explicit reply relation.

        No nearest-message guess: unrelated group photos must not become outlet
        evidence. A photo in the same album opens that album directly in Telegram.
        """
        with self.db.connect() as con:
            form_ids = {int(r[0]) for r in con.execute(
                """SELECT message_id FROM banner_forms WHERE group_id=?
                   UNION SELECT message_id FROM outlet_visits WHERE group_id=?""",
                (group_id,group_id),
            )}
        albums = {}
        for msg in messages:
            album = getattr(msg, "grouped_id", None)
            if album and int(msg.id) in form_ids:
                albums[album] = int(msg.id)
        for msg in sorted(messages, key=lambda m: int(m.id)):
            if not getattr(msg, "photo", None):
                continue
            form_id = int(msg.id) if int(msg.id) in form_ids else albums.get(getattr(msg, "grouped_id", None))
            if not form_id:
                form_id = await self._product_display_form_reply_id(msg, group_id, form_ids=form_ids)
            if form_id not in form_ids:
                continue
            with self.db.connect() as con:
                con.execute(
                    """INSERT INTO product_display_media(group_id,form_message_id,image_message_id)
                       VALUES(?,?,?) ON CONFLICT(group_id,form_message_id) DO UPDATE SET
                       image_message_id=MIN(product_display_media.image_message_id,excluded.image_message_id)""",
                    (group_id, form_id, int(msg.id)),
                )

    async def _save_product_display_images(self, group_id: int, messages: list) -> None:
        # Keep the original method name for existing notebook integrations.
        await self._save_report_images(group_id, messages)

    async def _history_product_display_group(
        self, group: dict, date_from: datetime, date_to: datetime,
        force: bool = False, update_checkpoint: bool = True,
    ) -> dict[str, int]:
        result = {
            "groups": 1, "messages": 0, "product_display_forms": 0,
            "locations": 0, "review": 0, "ignored": 0, "errors": 0,
            "skipped_groups": 0,
        }
        group_id = int(group["group_id"])
        if not force and self.db.product_display_history_complete(group_id, date_from, date_to):
            result["skipped_groups"] = 1
            return result
        newest_id = self.db.bootstrap_last_message_id(group_id)
        newest_date = None
        try:
            async with self._scrape_semaphore:
                pending_messages = []
                async for message in self.client.iter_messages(
                    group_id, offset_date=date_to, wait_time=0
                ):
                    if message.date >= date_to:
                        continue
                    if message.date < date_from:
                        break
                    newest_id = max(newest_id, int(message.id))
                    if newest_date is None or message.date > newest_date:
                        newest_date = message.date
                    pending_messages.append(message)
                # Process oldest first so report, album and GPS replies resolve.
                for message in reversed(pending_messages):
                    result["messages"] += 1
                    status = await self.process_product_display_message(message, group)
                    self._count_product_display_status(result, status)
                await self._save_product_display_images(group_id, pending_messages)
            # Product Display coverage is independent. Do not advance shared
            # Banner/Outlet Visit Message-ID checkpoints during a display run.
            self.db.mark_track_range_complete(
                "PRODUCT_DISPLAY", group_id, date_from, date_to
            )
            print(
                f"DONE {group['group_name']}: {result['messages']} messages; "
                f"Product Display fields={result['product_display_forms']}"
            )
        except FloodWaitError as exc:
            result["errors"] += 1
            print(f"FLOOD WAIT {group['group_name']}: retry after {exc.seconds}s")
            await asyncio.sleep(exc.seconds + 1)
        except Exception as exc:
            result["errors"] += 1
            print(f"ERROR {group['group_name']}: {exc}")
        return result

    async def backfill_product_display(
        self, date_from: datetime, date_to: datetime, force: bool = False,
        update_checkpoint: bool = True,
    ) -> dict[str, int]:
        """Scrape Product Display from all 65 dealer Execution groups."""
        groups = [dict(row) for row in self.db.product_display_groups()]
        if not groups:
            raise RuntimeError(
                "The 65 Execution groups are not configured. "
                "Run discover first."
            )
        from .groups import EXPECTED_REGION_DEALERS
        actual = {(g["region"], g["dealer"]) for g in groups}
        if actual != EXPECTED_REGION_DEALERS or len(groups) != 65:
            missing = sorted(EXPECTED_REGION_DEALERS - actual)
            raise RuntimeError(f"Product Display requires all 65 Execution groups; missing: {missing}")
        parts = await asyncio.gather(*(
            self._history_product_display_group(
                group,
                date_from,
                date_to,
                force=force,
                update_checkpoint=update_checkpoint,
            )
            for group in groups
        ))
        total = {
            "groups": 0, "messages": 0, "product_display_forms": 0,
            "locations": 0, "review": 0, "ignored": 0, "errors": 0,
            "skipped_groups": 0,
        }
        for part in parts:
            self._add_counts(total, part)
        return total

    @staticmethod
    def _count_product_display_status(result: dict[str, int], status: str) -> None:
        if status == "PRODUCT_DISPLAY_SAVED":
            result["product_display_forms"] += 1
        elif status.startswith("PRODUCT_DISPLAY_GPS_"):
            result["locations"] += 1
        elif status == "PRODUCT_DISPLAY_REVIEW":
            result["review"] += 1
        else:
            result["ignored"] += 1

    async def _incremental_product_display_group(self, group: dict) -> dict[str, int]:
        result = {
            "groups": 1, "messages": 0, "product_display_forms": 0,
            "locations": 0, "review": 0, "ignored": 0, "errors": 0,
        }
        group_id = int(group["group_id"])
        checkpoint = self.db.sync_checkpoint(group_id)
        if checkpoint is None:
            raise RuntimeError(
                f"No Product Display checkpoint for {group['group_name']}. "
                "Run the Product Display history backfill first."
            )
        last_id = int(checkpoint["last_message_id"] or 0)
        fallback_date = None
        if checkpoint["last_message_date"]:
            try:
                fallback_date = datetime.fromisoformat(checkpoint["last_message_date"])
            except ValueError:
                fallback_date = None
        newest_id = last_id
        newest_date = fallback_date
        try:
            async with self._scrape_semaphore:
                if last_id:
                    iterator = self.client.iter_messages(
                        group_id, min_id=last_id, reverse=True
                    )
                    async for message in iterator:
                        newest_id = max(newest_id, int(message.id))
                        newest_date = message.date
                        result["messages"] += 1
                        status = await self.process_product_display_message(message, group)
                        self._count_product_display_status(result, status)
                else:
                    pending = []
                    async for message in self.client.iter_messages(group_id):
                        message_date = message.date
                        if message_date.tzinfo is None:
                            message_date = message_date.replace(tzinfo=ZoneInfo("UTC"))
                        comparable = fallback_date
                        if comparable and comparable.tzinfo is None:
                            comparable = comparable.replace(tzinfo=ZoneInfo("UTC"))
                        if comparable and message_date <= comparable.astimezone(message_date.tzinfo):
                            break
                        pending.append(message)
                    for message in reversed(pending):
                        newest_id = max(newest_id, int(message.id))
                        newest_date = message.date
                        result["messages"] += 1
                        status = await self.process_product_display_message(message, group)
                        self._count_product_display_status(result, status)
            self.db.save_sync_checkpoint(
                group_id=group_id, last_message_id=newest_id,
                last_message_date=newest_date or datetime.now(ZoneInfo("UTC")),
            )
            print(
                f"DONE {group['group_name']}: {result['messages']} new messages; "
                f"Product Display fields={result['product_display_forms']}; "
                f"checkpoint={newest_id or 'DATE'}"
            )
        except FloodWaitError as exc:
            result["errors"] += 1
            print(f"FLOOD WAIT {group['group_name']}: retry after {exc.seconds}s")
            await asyncio.sleep(exc.seconds + 1)
        except Exception as exc:
            result["errors"] += 1
            print(f"ERROR {group['group_name']}: {exc}")
        return result

    async def sync_product_display_new_messages(
        self, date_from: datetime, date_to: datetime,
    ) -> dict[str, int]:
        """Re-read only the previous local day plus today (idempotent overlap)."""
        groups = [dict(row) for row in self.db.product_display_groups()]
        if not groups:
            raise RuntimeError(
                "The 65 Execution groups are not configured. "
                "Run discover first."
            )
        overlap_from = max(date_from, date_to - timedelta(days=2))
        parts = []
        for group in groups:
            parts.append(await self._history_product_display_group(
                group, overlap_from, date_to, force=True
            ))
        total = {
            "groups": 0, "messages": 0, "product_display_forms": 0,
            "locations": 0, "review": 0, "ignored": 0, "errors": 0,
            "skipped_groups": 0,
        }
        for part in parts:
            self._add_counts(total, part)
        return total

    async def _incremental_group(self, group: dict) -> dict[str, int]:
        result = {"groups": 1, "messages": 0, "forms": 0, "outlet_visits": 0, "locations": 0,
                  "ignored": 0, "errors": 0}
        group_id = int(group["group_id"])
        checkpoint = self.db.sync_checkpoint(group_id)
        last_id = int(checkpoint["last_message_id"] or 0) if checkpoint else 0
        fallback_date = None
        if checkpoint and checkpoint["last_message_date"]:
            try:
                fallback_date = datetime.fromisoformat(checkpoint["last_message_date"])
                if fallback_date.tzinfo is None:
                    fallback_date = fallback_date.replace(tzinfo=ZoneInfo(self.settings.timezone))
            except ValueError:
                fallback_date = None
        if not last_id and fallback_date is None:
            raise RuntimeError(
                f"No continuation checkpoint for {group['region']}-{group['dealer']}. "
                "Import the historical base before continuing."
            )
        newest_id = last_id
        newest_date = fallback_date
        used_date_fallback = not bool(last_id)
        media_messages = []
        try:
            async with self._scrape_semaphore:
                if last_id:
                    # min_id asks Telegram only for newer messages. It reads
                    # metadata/text and never downloads photos or videos.
                    iterator = self.client.iter_messages(group_id, min_id=last_id, reverse=True)
                    async for message in iterator:
                        newest_id = max(newest_id, int(message.id))
                        newest_date = message.date
                        result["messages"] += 1
                        status = await self.process_message(message, group)
                        self._count_message_status(result, status)
                        media_messages.append(message)
                else:
                    # First continuation after installing the authoritative
                    # workbook. Telegram returns newest first; stop exactly at
                    # the saved 18-Aug local-time boundary, then process only
                    # 19-Aug-and-newer messages oldest-to-newest.
                    pending = []
                    async for message in self.client.iter_messages(group_id):
                        message_date = message.date
                        if message_date.tzinfo is None:
                            message_date = message_date.replace(tzinfo=ZoneInfo("UTC"))
                        comparable_fallback = fallback_date
                        if comparable_fallback and comparable_fallback.tzinfo is None:
                            comparable_fallback = comparable_fallback.replace(
                                tzinfo=ZoneInfo(self.settings.timezone)
                            )
                        if comparable_fallback and message_date <= comparable_fallback.astimezone(message_date.tzinfo):
                            break
                        pending.append(message)
                    for message in reversed(pending):
                        newest_id = max(newest_id, int(message.id))
                        newest_date = message.date
                        result["messages"] += 1
                        status = await self.process_message(message, group)
                        self._count_message_status(result, status)
                        media_messages.append(message)
                    if not pending:
                        newest_date = datetime.now(ZoneInfo("UTC"))
                await self._save_report_images(group_id, media_messages)
            if used_date_fallback:
                self.db.replace_sync_position(
                    group_id=group_id, last_message_id=newest_id,
                    last_message_date=newest_date or datetime.now(ZoneInfo("UTC")),
                )
            else:
                self.db.save_sync_checkpoint(
                    group_id=group_id, last_message_id=newest_id,
                    last_message_date=newest_date,
                )
            print(
                f"DONE {group['region']}-{group['dealer']}: "
                f"{result['messages']} new messages; checkpoint={newest_id or 'DATE'}"
            )
        except FloodWaitError as exc:
            result["errors"] += 1
            print(f"FLOOD WAIT {group['region']}-{group['dealer']}: retry after {exc.seconds}s")
            await asyncio.sleep(exc.seconds + 1)
        except Exception as exc:
            result["errors"] += 1
            print(f"ERROR {group['region']}-{group['dealer']}: {exc}")
        return result

    async def sync_new_messages(self) -> dict[str, int]:
        """Fetch only messages newer than each group's durable checkpoint.

        Databases created by older releases may have skipped five guideline
        senders while still advancing their per-group Telegram checkpoints.
        Replay 19-Aug-to-now exactly once for those installations. Database
        primary keys keep the replay idempotent; the durable marker prevents
        any repeat on tomorrow's normal run.
        """
        groups = [dict(row) for row in self.db.groups()]
        migration_messages = 0
        replay_markers = (
            BANNER_ALL_SENDERS_BACKFILL_STATE,
            BANNER_FORWARDED_SENDER_BACKFILL_STATE,
        )
        pending_markers = [
            marker for marker in replay_markers
            if self.db.get_state(marker) != "complete"
        ]
        if pending_markers:
            today = datetime.now(ZoneInfo(self.settings.timezone)).date().isoformat()
            migration_start, migration_end = local_day_range(
                "2026-08-19", today, self.settings.timezone
            )
            print(
                "ONE-TIME BANNER SENDER/FORWARD RECOVERY: replaying 2026-08-19 through "
                f"{today}. Existing Telegram message IDs will be updated, not duplicated."
            )
            migration = await self.backfill(
                migration_start, migration_end, force=True
            )
            migration_messages = int(migration.get("messages", 0))
            if int(migration.get("errors", 0)):
                return {
                    "groups": len(groups), "messages": 0, "forms": 0,
                    "outlet_visits": 0, "locations": 0, "ignored": 0,
                    "errors": int(migration["errors"]),
                    "migration_messages": migration_messages,
                }
            for marker in pending_markers:
                self.db.set_state(marker, "complete")
            print("ONE-TIME BANNER SENDER/FORWARD RECOVERY: complete.")

        zone = ZoneInfo(self.settings.timezone)
        today_local = datetime.now(zone).date()
        overlap_from = datetime.combine(today_local - timedelta(days=1), time.min, zone)
        overlap_to = datetime.combine(today_local + timedelta(days=1), time.min, zone)
        parts = await asyncio.gather(*(self._history_group(
            group, overlap_from, overlap_to, force=True, track="ALL"
        ) for group in groups))
        total = {"groups": 0, "messages": 0, "forms": 0, "outlet_visits": 0, "locations": 0,
                 "ignored": 0, "errors": 0, "migration_messages": migration_messages}
        for part in parts:
            self._add_counts(total, part)
        from .gp_so_roster import sync_so_list
        path = getattr(self.settings, 'gp_so_master_path', None)
        if path is not None and path.is_file():
            result = sync_so_list(self.db, path, overlap_from.date().isoformat(),
                (overlap_to - timedelta(microseconds=1)).date().isoformat())
            print(f"SO List sync: {result}")
        return total

    async def listen(self) -> None:
        groups = [dict(x) for x in self.db.groups()]
        by_id = {int(x["group_id"]): x for x in groups}
        if not groups:
            raise RuntimeError("No configured groups. Run discover first.")

        @self.client.on(events.MessageEdited(chats=list(by_id), incoming=True))
        async def team_talk_edit_handler(event):
            group = by_id.get(int(event.chat_id))
            if group and group.get("source_kind", "EXECUTION") == "EXECUTION":
                from .team_talk import capture_team_talk
                capture_team_talk(
                    self.db, group, event.message.id, event.message.date,
                    event.message.raw_text or "",
                )

        @self.client.on(events.NewMessage(chats=list(by_id), incoming=True))
        async def handler(event):
            group = by_id.get(int(event.chat_id))
            if group:
                if group.get("source_kind") == "PRODUCT_DISPLAY":
                    status = await self.process_product_display_message(event.message, group)
                    label = group["group_name"]
                else:
                    status = await self.process_message(
                        event.message, group, allow_group_alert=True
                    )
                    await self._save_report_images(int(event.chat_id), [event.message])
                    label = f"{group['region']}-{group['dealer']}"
                if status != "IGNORED":
                    print(f"{label} message={event.id} {status}")

        print(
            f"Listening to {len(self.db.groups())} Execution groups plus "
            f"{len(self.db.product_display_groups())} Product Display group. "
            "Press Ctrl+C to stop."
        )
        await self.client.run_until_disconnected()


def local_day_range(start: str, end: str, timezone_name: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone_name)
    start_day = datetime.strptime(start, "%Y-%m-%d").date()
    end_day = datetime.strptime(end, "%Y-%m-%d").date()
    return datetime.combine(start_day, time.min, zone), datetime.combine(end_day + timedelta(days=1), time.min, zone)
