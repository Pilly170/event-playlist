import asyncio
from datetime import datetime, timedelta, timezone

import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.models.config import update_config
from app.models.requests import create_request, get_by_id
from app.services.crypto import TokenCipher
from app.services.notifications import EmailSettings
from app.spotify.token_store import save_tokens
from app.worker.request_timeout import run_timeout_tick, timeout_loop_forever


def _track_json(uri, name="A Song"):
    return {
        "uri": uri,
        "name": name,
        "explicit": False,
        "artists": [{"name": "An Artist"}],
        "album": {"images": []},
    }


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def _connected_cipher(conn):
    cipher = TokenCipher(key=Fernet.generate_key().decode())
    save_tokens(
        conn,
        cipher,
        access_token="valid-token",
        refresh_token="r",
        expires_in=3600,
        scope="s",
    )
    return cipher


def _create_pending(conn, **overrides):
    defaults = dict(
        spotify_track_uri="spotify:track:new",
        track_name="New Song",
        artist_name="New Artist",
        is_explicit=False,
        requestor_name="Alex",
        device_token="device-1",
        client_ip="1.2.3.4",
    )
    defaults.update(overrides)
    return create_request(conn, **defaults)


def _backdate(conn, request_id, *, minutes_ago):
    requested_at = (
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    ).isoformat()
    conn.execute(
        "UPDATE requests SET requested_at = ? WHERE id = ?",
        (requested_at, request_id),
    )
    conn.commit()


def _mock_spotify_client(*, now_playing_uri, playlist_uris, insert_response=None):
    insert_response = insert_response or {"snapshot_id": "snap-1"}

    async def handler(request):
        path = request.url.path
        if path.endswith("/me/player/currently-playing"):
            return httpx2.Response(
                200, json={"is_playing": True, "item": _track_json(now_playing_uri)}
            )
        if path.endswith("/tracks") and request.method == "GET":
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 100))
            page = playlist_uris[offset : offset + limit]
            return httpx2.Response(
                200,
                json={
                    "items": [{"track": _track_json(u)} for u in page],
                    "total": len(playlist_uris),
                    "limit": limit,
                    "offset": offset,
                },
            )
        if path.endswith("/tracks") and request.method == "POST":
            return httpx2.Response(200, json=insert_response)
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def _no_op_email_settings():
    # smtp_host blank => notifications module skips sending; keeps these tests
    # focused on the timeout/approval logic rather than email delivery, which
    # test_notifications.py already covers directly.
    return EmailSettings(
        smtp_host="",
        smtp_port=587,
        smtp_username="",
        smtp_password="",
        smtp_from_address="",
        notification_email="",
        domain="example.com",
    )


@pytest.mark.asyncio
async def test_tick_auto_approves_a_request_past_its_timeout(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(
        conn, default_playlist_id="playlist123", pending_request_timeout_minutes=15
    )
    created = _create_pending(conn, spotify_track_uri="spotify:track:new")
    _backdate(conn, created.id, minutes_ago=20)
    client = _mock_spotify_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=["spotify:track:current"],
    )

    await run_timeout_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        timeout_minutes=15,
        email_settings=_no_op_email_settings(),
    )

    updated = get_by_id(conn, created.id)
    assert updated.status == "added"
    assert updated.decided_by == "system"


@pytest.mark.asyncio
async def test_tick_leaves_a_request_under_its_timeout_alone(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(
        conn, default_playlist_id="playlist123", pending_request_timeout_minutes=15
    )
    created = _create_pending(conn, spotify_track_uri="spotify:track:new")
    _backdate(conn, created.id, minutes_ago=5)
    client = _mock_spotify_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=["spotify:track:current"],
    )

    await run_timeout_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        timeout_minutes=15,
        email_settings=_no_op_email_settings(),
    )

    updated = get_by_id(conn, created.id)
    assert updated.status == "pending"


@pytest.mark.asyncio
async def test_tick_marks_reminder_sent_without_auto_approving_early(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(
        conn, default_playlist_id="playlist123", pending_request_timeout_minutes=15
    )
    created = _create_pending(conn, spotify_track_uri="spotify:track:new")
    _backdate(conn, created.id, minutes_ago=14)  # 1 minute inside the 2-minute window
    client = _mock_spotify_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=["spotify:track:current"],
    )

    await run_timeout_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        timeout_minutes=15,
        email_settings=_no_op_email_settings(),
    )

    updated = get_by_id(conn, created.id)
    assert updated.status == "pending"
    assert updated.reminder_sent_at is not None


@pytest.mark.asyncio
async def test_tick_continues_after_one_request_fails_to_auto_approve(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    # No default_playlist_id configured => approve_request raises
    # PlaylistApprovalError for every request; this proves one failing request
    # doesn't crash the tick or block the DB from being left in a sane state.
    update_config(conn, pending_request_timeout_minutes=15)
    created = _create_pending(conn, spotify_track_uri="spotify:track:new")
    _backdate(conn, created.id, minutes_ago=20)
    client = _mock_spotify_client(now_playing_uri="x", playlist_uris=[])

    await run_timeout_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        timeout_minutes=15,
        email_settings=_no_op_email_settings(),
    )

    updated = get_by_id(conn, created.id)
    assert updated.status == "pending"  # left alone, will retry next tick


@pytest.mark.asyncio
async def test_loop_ticks_and_stops_promptly_when_signaled(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    conn.close()
    client = _mock_spotify_client(now_playing_uri="x", playlist_uris=[])
    stop_event = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.05)
        stop_event.set()

    asyncio.create_task(stop_soon())

    await asyncio.wait_for(
        timeout_loop_forever(
            str(tmp_path / "test.db"),
            cipher,
            client,
            client_id="id",
            client_secret="secret",
            email_settings=_no_op_email_settings(),
            stop_event=stop_event,
        ),
        timeout=2,
    )
