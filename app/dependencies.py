import sqlite3
from collections.abc import AsyncGenerator

import httpx2
from fastapi import Request

from app.config import settings
from app.db import get_connection
from app.services.crypto import TokenCipher


async def get_db() -> AsyncGenerator[sqlite3.Connection, None]:
    # Async so this always runs on the event loop thread, not a threadpool worker —
    # a sync generator dependency would run on a different thread than an async
    # route body, and sqlite3 connections can't cross threads.
    conn = get_connection(settings.database_path)
    try:
        yield conn
    finally:
        conn.close()


def get_http_client(request: Request) -> httpx2.AsyncClient:
    return request.app.state.http_client


def get_cipher() -> TokenCipher:
    return TokenCipher(key=settings.token_encryption_key)


def get_database_path() -> str:
    return settings.database_path
