import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.requests import (
    count_recent_requests_by_client_ip,
    count_recent_requests_by_device_token,
)

# Device token is the primary rate-limit key, not IP: venue WiFi typically puts many
# genuine requestors behind one shared IP, so an IP-only limit would throttle the
# whole room collectively. IP is kept only as a coarse abuse backstop (SPEC.md §6.1).
DEVICE_TOKEN_LIMIT = 5
DEVICE_TOKEN_WINDOW_MINUTES = 30
IP_LIMIT = 30
IP_WINDOW_MINUTES = 30


@dataclass
class RateLimitResult:
    allowed: bool
    reason: str | None = None


def check_rate_limit(
    conn: sqlite3.Connection, *, device_token: str, client_ip: str
) -> RateLimitResult:
    now = datetime.now(timezone.utc)

    device_count = count_recent_requests_by_device_token(
        conn, device_token, now - timedelta(minutes=DEVICE_TOKEN_WINDOW_MINUTES)
    )
    if device_count >= DEVICE_TOKEN_LIMIT:
        return RateLimitResult(allowed=False, reason="device_token")

    ip_count = count_recent_requests_by_client_ip(
        conn, client_ip, now - timedelta(minutes=IP_WINDOW_MINUTES)
    )
    if ip_count >= IP_LIMIT:
        return RateLimitResult(allowed=False, reason="client_ip")

    return RateLimitResult(allowed=True)
