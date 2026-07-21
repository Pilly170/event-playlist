import asyncio
import contextlib
import logging
import sqlite3

import httpx2

from app.db import get_connection
from app.models.audit_log import write_audit_log
from app.models.config import AppConfig, get_config
from app.models.playlist_state import (
    get_active_entry_for_uri,
    mark_played,
    mark_removed,
)
from app.services.crypto import TokenCipher
from app.spotify.auth_manager import SpotifyNotConnectedError
from app.spotify.client import (
    delete_track_from_playlist,
    get_now_playing,
    get_playlist_track_uris,
    set_repeat_mode,
)

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 15


async def run_poll_tick(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    last_known_uri: str | None,
) -> str | None:
    """Runs one poll tick. Returns the currently-playing URI (or None) — pass this
    back in as `last_known_uri` on the next call."""
    config = get_config(conn)

    # Unconditional and idempotent on purpose: this is also what keeps the playlist
    # looping via Spotify's own repeat mechanism once it runs dry, rather than the
    # worker needing separate "restart playback" logic (SPEC.md §6.4).
    await set_repeat_mode(
        conn,
        cipher,
        client,
        client_id=client_id,
        client_secret=client_secret,
        enabled=config.playlist_repeat_enabled,
    )

    now_playing = await get_now_playing(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    current_uri = now_playing.track.uri if now_playing else None

    if last_known_uri is not None and current_uri != last_known_uri:
        await _handle_track_finished(
            conn,
            cipher,
            client,
            client_id=client_id,
            client_secret=client_secret,
            config=config,
            finished_uri=last_known_uri,
        )

    return current_uri


async def _handle_track_finished(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    config: AppConfig,
    finished_uri: str,
) -> None:
    entry = get_active_entry_for_uri(conn, finished_uri)
    if entry is None:
        return  # not a track the app inserted — never touch backbone tracks

    mark_played(conn, entry.id)

    if not config.default_playlist_id:
        return

    # Re-fetched fresh, immediately before removing — the position recorded at
    # insertion time is almost certainly stale by now (SPEC.md §5).
    track_uris = await get_playlist_track_uris(
        conn,
        cipher,
        client,
        client_id=client_id,
        client_secret=client_secret,
        playlist_id=config.default_playlist_id,
    )
    if finished_uri not in track_uris:
        return  # already gone somehow — nothing left to remove

    position = track_uris.index(finished_uri)
    await delete_track_from_playlist(
        conn,
        cipher,
        client,
        client_id=client_id,
        client_secret=client_secret,
        playlist_id=config.default_playlist_id,
        track_uri=finished_uri,
        position=position,
    )
    mark_removed(conn, entry.id)
    write_audit_log(
        conn,
        actor="system",
        action="playlist.track_removed",
        detail=f"track={finished_uri} position={position}",
    )


async def poll_forever(
    database_path: str,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    stop_event: asyncio.Event,
) -> None:
    """Runs run_poll_tick in a loop until stop_event is set. Started as a single
    in-process asyncio task from the app lifespan — SPEC.md §6.4/§2 require this to
    stay a single process, never multiple worker replicas."""
    last_known_uri = None
    while not stop_event.is_set():
        conn = get_connection(database_path)
        poll_interval = DEFAULT_POLL_INTERVAL_SECONDS
        try:
            config = get_config(conn)
            poll_interval = config.poll_interval_seconds
            last_known_uri = await run_poll_tick(
                conn,
                cipher,
                client,
                client_id=client_id,
                client_secret=client_secret,
                last_known_uri=last_known_uri,
            )
        except SpotifyNotConnectedError:
            pass  # nothing to do until an admin connects an account
        except Exception:
            logger.exception("Poller tick failed")
        finally:
            conn.close()

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
