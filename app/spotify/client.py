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


def _track_json_to_result(track: dict) -> TrackResult:
    images = track.get("album", {}).get("images", [])
    return TrackResult(
        uri=track["uri"],
        name=track["name"],
        artist=", ".join(artist["name"] for artist in track["artists"]),
        album_image_url=images[0]["url"] if images else None,
        is_explicit=bool(track["explicit"]),
    )
