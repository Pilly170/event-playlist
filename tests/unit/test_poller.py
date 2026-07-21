import asyncio
import json

import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.models.audit_log import list_audit_log
from app.models.config import update_config
from app.models.playlist_state import create_playlist_state_entry, get_by_request_id
from app.services.crypto import TokenCipher
from app.spotify.token_store import save_tokens
from app.worker.poller import poll_forever, run_poll_tick


def _track_json(uri):
    return {
        "uri": uri,
        "name": "A Song",
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


def _mock_client(
    *, now_playing_uri, playlist_uris=(), repeat_calls=None, delete_calls=None
):
    async def handler(request):
        path = request.url.path
        method = request.method
        if path.endswith("/me/player/currently-playing"):
            if now_playing_uri is None:
                return httpx2.Response(204)
            return httpx2.Response(
                200, json={"is_playing": True, "item": _track_json(now_playing_uri)}
            )
        if path.endswith("/me/player/repeat"):
            if repeat_calls is not None:
                repeat_calls.append(dict(request.url.params))
            return httpx2.Response(204)
        if path.endswith("/tracks") and method == "GET":
            offset = int(request.url.params.get("offset", 0))
            limit = int(request.url.params.get("limit", 100))
            page = list(playlist_uris)[offset : offset + limit]
            return httpx2.Response(
                200,
                json={
                    "items": [{"track": _track_json(u)} for u in page],
                    "total": len(playlist_uris),
                    "limit": limit,
                    "offset": offset,
                },
            )
        if path.endswith("/tracks") and method == "DELETE":
            if delete_calls is not None:
                delete_calls.append(json.loads(request.read()))
            return httpx2.Response(200, json={"snapshot_id": "snap-removed"})
        raise AssertionError(f"unexpected {method} {request.url}")

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


@pytest.mark.asyncio
async def test_tick_returns_current_uri_when_nothing_previously_known(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    client = _mock_client(
        now_playing_uri="spotify:track:a", playlist_uris=["spotify:track:a"]
    )

    result = await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri=None,
    )

    assert result == "spotify:track:a"


@pytest.mark.asyncio
async def test_tick_returns_none_when_nothing_is_playing(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    client = _mock_client(now_playing_uri=None)

    result = await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri=None,
    )

    assert result is None


@pytest.mark.asyncio
async def test_tick_enforces_repeat_context_when_enabled(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, playlist_repeat_enabled=True)
    repeat_calls = []
    client = _mock_client(
        now_playing_uri="spotify:track:a",
        playlist_uris=["spotify:track:a"],
        repeat_calls=repeat_calls,
    )

    await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri=None,
    )

    assert repeat_calls == [{"state": "context"}]


@pytest.mark.asyncio
async def test_tick_enforces_repeat_off_when_disabled(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, playlist_repeat_enabled=False)
    repeat_calls = []
    client = _mock_client(
        now_playing_uri="spotify:track:a",
        playlist_uris=["spotify:track:a"],
        repeat_calls=repeat_calls,
    )

    await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri=None,
    )

    assert repeat_calls == [{"state": "off"}]


@pytest.mark.asyncio
async def test_tick_does_nothing_extra_when_track_unchanged(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    delete_calls = []
    client = _mock_client(
        now_playing_uri="spotify:track:a",
        playlist_uris=["spotify:track:a"],
        delete_calls=delete_calls,
    )

    result = await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri="spotify:track:a",
    )

    assert result == "spotify:track:a"
    assert delete_calls == []


@pytest.mark.asyncio
async def test_tick_removes_a_finished_app_inserted_track(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123")
    entry = create_playlist_state_entry(
        conn,
        spotify_track_uri="spotify:track:finished",
        source="request",
        request_id=1,
        inserted_position=0,
        snapshot_id_at_insert="snap-insert",
    )
    delete_calls = []
    client = _mock_client(
        now_playing_uri="spotify:track:next",
        playlist_uris=["spotify:track:finished", "spotify:track:next"],
        delete_calls=delete_calls,
    )

    await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri="spotify:track:finished",
    )

    assert len(delete_calls) == 1
    assert delete_calls[0]["tracks"][0]["uri"] == "spotify:track:finished"
    assert delete_calls[0]["tracks"][0]["positions"] == [0]

    updated_entry = get_by_request_id(conn, 1)
    assert updated_entry.played_at is not None
    assert updated_entry.removed_at is not None
    assert entry.id == updated_entry.id


@pytest.mark.asyncio
async def test_tick_writes_an_audit_log_entry_for_the_removal(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123")
    create_playlist_state_entry(
        conn,
        spotify_track_uri="spotify:track:finished",
        source="request",
        request_id=1,
        inserted_position=0,
        snapshot_id_at_insert="snap-insert",
    )
    client = _mock_client(
        now_playing_uri="spotify:track:next",
        playlist_uris=["spotify:track:finished", "spotify:track:next"],
        delete_calls=[],
    )

    await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri="spotify:track:finished",
    )

    entries = list_audit_log(conn)
    assert any(
        e.action == "playlist.track_removed" and e.actor == "system" for e in entries
    )


@pytest.mark.asyncio
async def test_tick_never_removes_a_backbone_track_that_finished(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123")
    # No playlist_state entry at all for this URI — it's a pre-existing backbone track.
    delete_calls = []
    client = _mock_client(
        now_playing_uri="spotify:track:next",
        playlist_uris=["spotify:track:backbone", "spotify:track:next"],
        delete_calls=delete_calls,
    )

    await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri="spotify:track:backbone",
    )

    assert delete_calls == []


@pytest.mark.asyncio
async def test_tick_skips_removal_when_no_default_playlist_configured(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    create_playlist_state_entry(
        conn,
        spotify_track_uri="spotify:track:finished",
        source="request",
        request_id=1,
        inserted_position=0,
        snapshot_id_at_insert="snap-insert",
    )

    async def unexpected_playlist_or_delete(request):
        path = request.url.path
        if path.endswith("/me/player/currently-playing"):
            return httpx2.Response(
                200,
                json={"is_playing": True, "item": _track_json("spotify:track:next")},
            )
        if path.endswith("/me/player/repeat"):
            return httpx2.Response(204)
        raise AssertionError(
            f"unexpected {request.method} {request.url} — no playlist configured"
        )

    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(unexpected_playlist_or_delete)
    )

    await run_poll_tick(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        last_known_uri="spotify:track:finished",
    )

    updated_entry = get_by_request_id(conn, 1)
    assert updated_entry.played_at is not None
    assert updated_entry.removed_at is None


@pytest.mark.asyncio
async def test_poll_forever_ticks_and_stops_promptly_when_signaled(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(
        conn, poll_interval_seconds=30
    )  # deliberately long — must not be waited out
    conn.close()

    tick_count = 0

    async def handler(request):
        nonlocal tick_count
        path = request.url.path
        if path.endswith("/me/player/currently-playing"):
            tick_count += 1
            return httpx2.Response(204)
        if path.endswith("/me/player/repeat"):
            return httpx2.Response(204)
        raise AssertionError(f"unexpected call to {request.url}")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        poll_forever(
            db_path,
            cipher,
            client,
            client_id="id",
            client_secret="secret",
            stop_event=stop_event,
        )
    )

    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(task, timeout=2)

    assert tick_count >= 1


@pytest.mark.asyncio
async def test_poll_forever_continues_after_a_tick_raises(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = _connection(tmp_path)
    update_config(conn, poll_interval_seconds=30)
    conn.close()
    # No Spotify tokens saved at all — every tick will raise SpotifyNotConnectedError,
    # which the loop must swallow and keep running rather than dying.
    cipher = TokenCipher(key=Fernet.generate_key().decode())

    async def unexpected(request):
        raise AssertionError(
            "should not make any HTTP call without a connected account"
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(unexpected))
    stop_event = asyncio.Event()
    task = asyncio.create_task(
        poll_forever(
            db_path,
            cipher,
            client,
            client_id="id",
            client_secret="secret",
            stop_event=stop_event,
        )
    )

    await asyncio.sleep(0.05)
    stop_event.set()
    await asyncio.wait_for(task, timeout=2)  # must not have crashed
