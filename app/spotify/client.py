import sqlite3
from dataclasses import dataclass

import httpx2

from app.services.crypto import TokenCipher
from app.spotify.auth_manager import get_valid_access_token

API_BASE_URL = "https://api.spotify.com/v1"


@dataclass
class TrackResult:
    uri: str
    name: str
    artist: str
    album_image_url: str | None
    is_explicit: bool


@dataclass
class NowPlaying:
    track: TrackResult
    is_playing: bool


@dataclass
class PlaylistPage:
    items: list[TrackResult]
    total: int
    limit: int
    offset: int


async def get_currently_playing(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
) -> dict | None:
    access_token = await get_valid_access_token(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    response = await client.get(
        f"{API_BASE_URL}/me/player/currently-playing",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code == 204:
        return None
    response.raise_for_status()
    return response.json()


async def get_now_playing(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
) -> NowPlaying | None:
    data = await get_currently_playing(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    if data is None or data.get("item") is None:
        return None
    return NowPlaying(
        track=_track_json_to_result(data["item"]),
        is_playing=bool(data.get("is_playing", False)),
    )


async def get_playlist_tracks(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    playlist_id: str,
    limit: int = 20,
    offset: int = 0,
) -> PlaylistPage:
    access_token = await get_valid_access_token(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    response = await client.get(
        f"{API_BASE_URL}/playlists/{playlist_id}/tracks",
        params={"limit": limit, "offset": offset},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    data = response.json()
    items = [
        _track_json_to_result(item["track"])
        for item in data["items"]
        if item.get("track") is not None
    ]
    return PlaylistPage(
        items=items, total=data["total"], limit=data["limit"], offset=data["offset"]
    )


async def get_playlist_track_uris(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    playlist_id: str,
    page_size: int = 100,
) -> list[str]:
    # Walks every page — needed to find a track's exact position for insertion-offset
    # math (SPEC.md §5), and to check "is this track already anywhere in the playlist"
    # at approval time, not just on the first page.
    uris: list[str] = []
    offset = 0
    while True:
        page = await get_playlist_tracks(
            conn,
            cipher,
            client,
            client_id=client_id,
            client_secret=client_secret,
            playlist_id=playlist_id,
            limit=page_size,
            offset=offset,
        )
        uris.extend(track.uri for track in page.items)
        offset += page_size
        if offset >= page.total or not page.items:
            break
    return uris


async def insert_track_into_playlist(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    playlist_id: str,
    track_uri: str,
    position: int,
) -> str:
    access_token = await get_valid_access_token(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    response = await client.post(
        f"{API_BASE_URL}/playlists/{playlist_id}/tracks",
        json={"uris": [track_uri], "position": position},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    return response.json()["snapshot_id"]


async def search_tracks(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    query: str,
    limit: int = 10,
) -> list[TrackResult]:
    access_token = await get_valid_access_token(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    response = await client.get(
        f"{API_BASE_URL}/search",
        params={"type": "track", "q": query, "limit": limit},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    items = response.json()["tracks"]["items"]
    return [_track_json_to_result(item) for item in items]


async def get_track(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    track_uri: str,
) -> TrackResult | None:
    track_id = track_uri.rsplit(":", 1)[-1]
    access_token = await get_valid_access_token(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    response = await client.get(
        f"{API_BASE_URL}/tracks/{track_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return _track_json_to_result(response.json())


async def set_repeat_mode(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    enabled: bool,
) -> None:
    access_token = await get_valid_access_token(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    response = await client.put(
        f"{API_BASE_URL}/me/player/repeat",
        params={"state": "context" if enabled else "off"},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()


async def delete_track_from_playlist(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    playlist_id: str,
    track_uri: str,
    position: int,
) -> str:
    # Removed by position, not URI alone — the same URI can legitimately appear more
    # than once in the playlist (SPEC.md §5), so removing "by URI" risks deleting the
    # wrong occurrence.
    access_token = await get_valid_access_token(
        conn, cipher, client, client_id=client_id, client_secret=client_secret
    )
    response = await client.request(
        "DELETE",
        f"{API_BASE_URL}/playlists/{playlist_id}/tracks",
        json={"tracks": [{"uri": track_uri, "positions": [position]}]},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    return response.json()["snapshot_id"]


def _track_json_to_result(track: dict) -> TrackResult:
    images = track.get("album", {}).get("images", [])
    return TrackResult(
        uri=track["uri"],
        name=track["name"],
        artist=", ".join(artist["name"] for artist in track["artists"]),
        album_image_url=images[0]["url"] if images else None,
        is_explicit=bool(track["explicit"]),
    )
