from urllib.parse import parse_qs, urlparse

import httpx2
import pytest

from app.spotify.oauth import (
    REQUIRED_SCOPES,
    build_authorize_url,
    exchange_code_for_token,
    refresh_access_token,
)


def test_build_authorize_url_includes_client_id_redirect_and_state():
    url = build_authorize_url(
        client_id="client123",
        redirect_uri="https://example.com/callback",
        state="xyz",
    )

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.spotify.com"
    assert query["client_id"] == ["client123"]
    assert query["redirect_uri"] == ["https://example.com/callback"]
    assert query["state"] == ["xyz"]
    assert query["response_type"] == ["code"]


def test_build_authorize_url_requests_all_required_scopes():
    url = build_authorize_url(
        client_id="client123",
        redirect_uri="https://example.com/callback",
        state="xyz",
    )

    query = parse_qs(urlparse(url).query)
    requested_scopes = set(query["scope"][0].split(" "))
    assert requested_scopes == set(REQUIRED_SCOPES)


@pytest.mark.asyncio
async def test_exchange_code_for_token_sends_authorization_code_grant():
    captured = {}

    async def handler(request):
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx2.Response(
            200,
            json={
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
                "scope": "playlist-read-private",
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    token = await exchange_code_for_token(
        client,
        code="auth-code",
        redirect_uri="https://example.com/callback",
        client_id="client123",
        client_secret="secret456",
    )

    assert captured["url"] == "https://accounts.spotify.com/api/token"
    assert "grant_type=authorization_code" in captured["body"]
    assert "code=auth-code" in captured["body"]
    assert token["access_token"] == "new-access-token"
    assert token["refresh_token"] == "new-refresh-token"


@pytest.mark.asyncio
async def test_refresh_access_token_sends_refresh_token_grant():
    captured = {}

    async def handler(request):
        captured["body"] = request.read().decode()
        return httpx2.Response(
            200,
            json={
                "access_token": "refreshed-access-token",
                "expires_in": 3600,
                "scope": "playlist-read-private",
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))

    token = await refresh_access_token(
        client,
        refresh_token="old-refresh-token",
        client_id="client123",
        client_secret="secret456",
    )

    assert "grant_type=refresh_token" in captured["body"]
    assert "refresh_token=old-refresh-token" in captured["body"]
    assert token["access_token"] == "refreshed-access-token"
