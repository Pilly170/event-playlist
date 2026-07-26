import asyncio
import contextlib
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx2

from app.db import get_connection
from app.models.config import get_config
from app.models.requests import (
    list_pending_needing_reminder,
    list_pending_requested_before,
    mark_reminder_sent,
)
from app.services.crypto import TokenCipher
from app.services.notifications import EmailSettings, send_pending_request_reminder
from app.services.playlist_ops import approve_request

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_SECONDS = 15
REMINDER_LEAD_MINUTES = 2


async def run_timeout_tick(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    timeout_minutes: int,
    email_settings: EmailSettings,
) -> None:
    """Runs one tick: sends the one-time reminder for any pending request within
    REMINDER_LEAD_MINUTES of its own timeout, then auto-approves any pending
    request that's actually past its timeout. Reads fresh from the DB every call —
    no state carried between ticks, same accepted tradeoff as the Spotify poller's
    last_known_uri (a restart can only delay one tick's worth of work, never lose
    or duplicate it, since every check re-derives from requested_at/reminder_sent_at
    on the row itself)."""
    now = datetime.now(timezone.utc)

    if timeout_minutes > REMINDER_LEAD_MINUTES:
        reminder_cutoff = now - timedelta(
            minutes=timeout_minutes - REMINDER_LEAD_MINUTES
        )
        for request in list_pending_needing_reminder(conn, reminder_cutoff):
            try:
                # send_pending_request_reminder itself never raises (see
                # app/services/notifications.py) — marking as sent regardless of
                # whether delivery actually succeeded is deliberate: this is a
                # best-effort courtesy notification, not the safety net itself,
                # and a single-attempt policy avoids resending forever against a
                # persistently broken SMTP config. The try/except here guards
                # against everything else in the loop body (e.g. mark_reminder_sent,
                # a DB write) so one bad reminder can't abort the tick and skip a
                # different, unrelated request's auto-approval below.
                #
                # Runs the blocking SMTP call in a worker thread — smtplib's
                # socket I/O would otherwise block this single event loop, which
                # also serves all public/admin HTTP traffic and the Spotify
                # poller.
                await asyncio.to_thread(
                    send_pending_request_reminder, request, email_settings
                )
                mark_reminder_sent(conn, request.id)
            except Exception:
                logger.exception("Reminder send failed for request %s", request.id)

    approval_cutoff = now - timedelta(minutes=timeout_minutes)
    for request in list_pending_requested_before(conn, approval_cutoff):
        try:
            await approve_request(
                conn,
                cipher,
                client,
                client_id=client_id,
                client_secret=client_secret,
                request_id=request.id,
                admin_username="system",
            )
        except Exception:
            # One request that can't be auto-approved right now (no default
            # playlist configured, Spotify temporarily unreachable, etc.) must not
            # block the rest of the tick — it simply gets retried next tick.
            logger.exception("Auto-approval failed for request %s", request.id)


async def timeout_loop_forever(
    database_path: str,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    email_settings: EmailSettings,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        conn = get_connection(database_path)
        interval = DEFAULT_TICK_INTERVAL_SECONDS
        try:
            config = get_config(conn)
            interval = config.poll_interval_seconds
            await run_timeout_tick(
                conn,
                cipher,
                client,
                client_id=client_id,
                client_secret=client_secret,
                timeout_minutes=config.pending_request_timeout_minutes,
                email_settings=email_settings,
            )
        except Exception:
            logger.exception("Request-timeout tick failed")
        finally:
            conn.close()

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
