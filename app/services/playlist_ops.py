import sqlite3

import httpx2

from app.models.audit_log import write_audit_log
from app.models.config import get_config
from app.models.playlist_state import create_playlist_state_entry
from app.models.requests import Request, delete_request, get_by_id, mark_request_added
from app.services.crypto import TokenCipher
from app.spotify.client import (
    get_now_playing,
    get_playlist_track_uris,
    insert_track_into_playlist,
)


class PlaylistApprovalError(Exception):
    pass


async def approve_request(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    request_id: int,
    admin_username: str,
) -> Request:
    request = get_by_id(conn, request_id)
    if request is None or request.status != "pending":
        raise PlaylistApprovalError("Request not found or already decided")

    config = get_config(conn)
    if not config.default_playlist_id:
        raise PlaylistApprovalError("No default playlist is configured")

    # Always re-fetched right before computing a position — never trust a position
    # calculated even a few seconds earlier, since playlist order shifts on every
    # mutation (SPEC.md §5). This same fetch also catches a track that was added to
    # the playlist manually, outside the app entirely, which a DB-only duplicate
    # check could never see.
    track_uris = await get_playlist_track_uris(
        conn,
        cipher,
        client,
        client_id=client_id,
        client_secret=client_secret,
        playlist_id=config.default_playlist_id,
    )
    if request.spotify_track_uri in track_uris:
        raise PlaylistApprovalError("That track is already in the playlist")

    now_playing = await get_now_playing(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    if now_playing is None or now_playing.track.uri not in track_uris:
        raise PlaylistApprovalError(
            "Can't determine the current playback position in this playlist"
        )

    current_index = track_uris.index(now_playing.track.uri)
    insert_position = min(current_index + config.insert_tracks_ahead, len(track_uris))

    snapshot_id = await insert_track_into_playlist(
        conn,
        cipher,
        client,
        client_id=client_id,
        client_secret=client_secret,
        playlist_id=config.default_playlist_id,
        track_uri=request.spotify_track_uri,
        position=insert_position,
    )

    create_playlist_state_entry(
        conn,
        spotify_track_uri=request.spotify_track_uri,
        source="request",
        request_id=request.id,
        inserted_position=insert_position,
        snapshot_id_at_insert=snapshot_id,
    )
    mark_request_added(
        conn,
        request_id=request.id,
        decided_by=admin_username,
        playlist_insert_position=insert_position,
    )
    write_audit_log(
        conn,
        actor=admin_username,
        action="request.approved",
        detail=f"request_id={request.id} track={request.spotify_track_uri} position={insert_position}",
    )

    return get_by_id(conn, request_id)


def deny_request(
    conn: sqlite3.Connection, *, request_id: int, admin_username: str
) -> None:
    request = get_by_id(conn, request_id)
    if request is None or request.status != "pending":
        raise PlaylistApprovalError("Request not found or already decided")

    delete_request(conn, request_id)
    write_audit_log(
        conn,
        actor=admin_username,
        action="request.denied",
        detail=f"request_id={request_id} track={request.spotify_track_uri}",
    )
