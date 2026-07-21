import httpx2
import pytest
from cryptography.fernet import Fernet

from app.db import get_connection, run_migrations
from app.models.audit_log import list_audit_log
from app.models.config import update_config
from app.models.playlist_state import get_by_request_id
from app.models.requests import create_request, get_by_id, mark_request_added
from app.services.crypto import TokenCipher
from app.services.playlist_ops import (
    PlaylistApprovalError,
    approve_request,
    deny_request,
)
from app.spotify.token_store import save_tokens


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


def _create_pending_request(conn, **overrides):
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


def _mock_client(*, now_playing_uri, playlist_uris, insert_response=None):
    insert_response = insert_response or {"snapshot_id": "snap-1"}

    async def handler(request):
        path = request.url.path
        if path.endswith("/me/player/currently-playing"):
            if now_playing_uri is None:
                return httpx2.Response(204)
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


@pytest.mark.asyncio
async def test_approve_request_inserts_at_current_index_plus_offset(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", insert_tracks_ahead=3)
    request = _create_pending_request(conn)
    client = _mock_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=[
            "spotify:track:zero",
            "spotify:track:current",
            "spotify:track:two",
            "spotify:track:three",
        ],
    )

    approved = await approve_request(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        request_id=request.id,
        admin_username="admin",
    )

    assert approved.status == "added"
    assert approved.decided_by == "admin"
    assert (
        approved.playlist_insert_position == 4
    )  # current_index (1) + insert_tracks_ahead (3)


@pytest.mark.asyncio
async def test_approve_request_creates_a_playlist_state_entry(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", insert_tracks_ahead=1)
    request = _create_pending_request(conn)
    client = _mock_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=["spotify:track:current"],
        insert_response={"snapshot_id": "snap-xyz"},
    )

    await approve_request(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        request_id=request.id,
        admin_username="admin",
    )

    entry = get_by_request_id(conn, request.id)
    assert entry.source == "request"
    assert entry.snapshot_id_at_insert == "snap-xyz"
    assert entry.spotify_track_uri == "spotify:track:new"


@pytest.mark.asyncio
async def test_approve_request_writes_an_audit_log_entry(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", insert_tracks_ahead=1)
    request = _create_pending_request(conn)
    client = _mock_client(
        now_playing_uri="spotify:track:current", playlist_uris=["spotify:track:current"]
    )

    await approve_request(
        conn,
        cipher,
        client,
        client_id="id",
        client_secret="secret",
        request_id=request.id,
        admin_username="admin",
    )

    entries = list_audit_log(conn)
    assert any(e.action == "request.approved" and e.actor == "admin" for e in entries)


@pytest.mark.asyncio
async def test_approve_request_rejects_when_track_already_in_playlist(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", insert_tracks_ahead=1)
    request = _create_pending_request(
        conn, spotify_track_uri="spotify:track:already-there"
    )
    client = _mock_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=["spotify:track:current", "spotify:track:already-there"],
    )

    with pytest.raises(PlaylistApprovalError):
        await approve_request(
            conn,
            cipher,
            client,
            client_id="id",
            client_secret="secret",
            request_id=request.id,
            admin_username="admin",
        )

    assert get_by_id(conn, request.id).status == "pending"


@pytest.mark.asyncio
async def test_approve_request_rejects_when_nothing_is_playing(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", insert_tracks_ahead=1)
    request = _create_pending_request(conn)
    client = _mock_client(now_playing_uri=None, playlist_uris=["spotify:track:a"])

    with pytest.raises(PlaylistApprovalError):
        await approve_request(
            conn,
            cipher,
            client,
            client_id="id",
            client_secret="secret",
            request_id=request.id,
            admin_username="admin",
        )


@pytest.mark.asyncio
async def test_approve_request_rejects_when_current_track_not_in_target_playlist(
    tmp_path,
):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", insert_tracks_ahead=1)
    request = _create_pending_request(conn)
    client = _mock_client(
        now_playing_uri="spotify:track:somewhere-else",
        playlist_uris=["spotify:track:a"],
    )

    with pytest.raises(PlaylistApprovalError):
        await approve_request(
            conn,
            cipher,
            client,
            client_id="id",
            client_secret="secret",
            request_id=request.id,
            admin_username="admin",
        )


@pytest.mark.asyncio
async def test_approve_request_rejects_when_no_default_playlist_configured(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    request = _create_pending_request(conn)

    async def unexpected(request_):
        raise AssertionError("should not make any HTTP call")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(unexpected))

    with pytest.raises(PlaylistApprovalError):
        await approve_request(
            conn,
            cipher,
            client,
            client_id="id",
            client_secret="secret",
            request_id=request.id,
            admin_username="admin",
        )


@pytest.mark.asyncio
async def test_approve_request_rejects_when_request_already_decided(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123")
    request = _create_pending_request(conn)
    mark_request_added(
        conn, request_id=request.id, decided_by="admin", playlist_insert_position=0
    )

    async def unexpected(request_):
        raise AssertionError("should not make any HTTP call")

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(unexpected))

    with pytest.raises(PlaylistApprovalError):
        await approve_request(
            conn,
            cipher,
            client,
            client_id="id",
            client_secret="secret",
            request_id=request.id,
            admin_username="admin",
        )


def test_deny_request_deletes_the_row(tmp_path):
    conn = _connection(tmp_path)
    request = _create_pending_request(conn)

    deny_request(conn, request_id=request.id, admin_username="admin")

    assert get_by_id(conn, request.id) is None


def test_deny_request_writes_an_audit_log_entry(tmp_path):
    conn = _connection(tmp_path)
    request = _create_pending_request(conn)

    deny_request(conn, request_id=request.id, admin_username="admin")

    entries = list_audit_log(conn)
    assert any(e.action == "request.denied" and e.actor == "admin" for e in entries)


def test_deny_request_rejects_when_request_not_found(tmp_path):
    conn = _connection(tmp_path)

    with pytest.raises(PlaylistApprovalError):
        deny_request(conn, request_id=999, admin_username="admin")
