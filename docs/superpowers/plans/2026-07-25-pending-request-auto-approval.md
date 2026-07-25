# Pending-Request Auto-Approval + Reminder Email Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Requests left `pending` for too long auto-approve automatically, with a one-time reminder email sent 2 minutes before each request's own auto-approval fires.

**Architecture:** A second, independent in-process `asyncio` background loop (`app/worker/request_timeout.py`), mirroring the existing Spotify poller's `run_poll_tick`/`poll_forever` pattern exactly — a pure, directly-testable tick function wrapped by a thin loop, started/stopped alongside the poller in `app/main.py`'s lifespan. Auto-approval reuses the existing `approve_request()` service function unchanged. Reminder email delivery is a new, narrowly-scoped `app/services/notifications.py` module using only the Python stdlib.

**Tech Stack:** FastAPI, SQLite (stdlib `sqlite3`), `asyncio`, `httpx2`, stdlib `smtplib`/`email.message`, pytest.

**Design doc:** `docs/superpowers/specs/2026-07-25-pending-request-auto-approval-design.md`

## Global Constraints

- Single-process constraint (SPEC.md §2/§6.4): all background work stays as in-process `asyncio` tasks within the one existing app process — never a second container/process.
- Secrets and deployment-only settings (SMTP credentials, `NOTIFICATION_EMAIL`) are env vars via `app/config.py`'s `Settings`, never DB-stored, matching every existing secret in this app.
- Worker/service-layer functions must not read the global `settings` object internally — every existing background/service function in this codebase takes its configuration as explicit parameters (see `run_poll_tick`, `approve_request`), and callers read `settings` once at the top and pass values down. This applies to `app/worker/request_timeout.py` and `app/services/notifications.py` in this plan. Routers are the exception, not a violation of this rule: existing router code (e.g. `admin_spotify.py`'s `connect()`) already reads `from app.config import settings` directly and uses it inline — Task 6's router does the same for `settings.notification_email`, matching that existing precedent, not contradicting it.
- `notification_email` is never part of `_EDITABLE_FIELDS` / the `/admin/config` POST body — it's rendered `disabled` and read-only, sourced from `settings`, not the DB.
- All new DB-facing code goes through `sqlite3.Connection` passed in as a parameter (never opened ad hoc inside business logic), matching every existing model/service function.
- Every new async DB-touching function follows the existing "async all the way, one thread" rule from `app/dependencies.py:get_db`'s docstring/comment — don't introduce sync DB access on the event loop thread.
- Tests use the existing fixtures/conventions per file: `get_connection(str(tmp_path / "test.db"))` + `run_migrations(conn)` for model tests, `httpx2.MockTransport` for anything hitting the Spotify client, and the existing `TestClient` + `dependency_overrides` pattern for router tests.

---

### Task 1: Migration + config model support for the timeout setting

**Files:**
- Create: `app/migrations/0007_pending_request_timeout.sql`
- Modify: `app/models/config.py`
- Test: `tests/unit/test_config_model.py`

**Interfaces:**
- Consumes: nothing new (extends the existing `config` table / `AppConfig` dataclass).
- Produces: `AppConfig.pending_request_timeout_minutes: int` (default 15), and `pending_request_timeout_minutes` is now a valid keyword to `update_config(conn, **changes)`. Later tasks (4, 6) read `config.pending_request_timeout_minutes` from `get_config()`.

- [ ] **Step 1: Write the migration file**

Create `app/migrations/0007_pending_request_timeout.sql`:

```sql
-- Pending-request auto-approval + reminder email
-- (docs/superpowers/specs/2026-07-25-pending-request-auto-approval-design.md).
-- pending_request_timeout_minutes: admin-configurable, how long a request can sit
-- pending before the background timeout loop auto-approves it.
-- reminder_sent_at: tracks whether the one-time reminder email has already gone out
-- for a given request, so the timeout loop doesn't resend it every tick during the
-- warning window before auto-approval fires.
ALTER TABLE config ADD COLUMN pending_request_timeout_minutes INTEGER NOT NULL DEFAULT 15;
ALTER TABLE requests ADD COLUMN reminder_sent_at TEXT;
```

- [ ] **Step 2: Write the failing tests**

Add to the end of `tests/unit/test_config_model.py`:

```python
def test_get_config_returns_pending_request_timeout_default(tmp_path):
    conn = _connection(tmp_path)

    config = get_config(conn)

    assert config.pending_request_timeout_minutes == 15


def test_update_config_changes_pending_request_timeout(tmp_path):
    conn = _connection(tmp_path)

    updated = update_config(conn, pending_request_timeout_minutes=30)

    assert updated.pending_request_timeout_minutes == 30
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_config_model.py -v`
Expected: the two new tests FAIL with `TypeError: get_config() ... unexpected` or an `AttributeError`/tuple-unpacking error, since `AppConfig` has no `pending_request_timeout_minutes` field yet and the `config` table has no such column yet.

- [ ] **Step 4: Update `app/models/config.py`**

Replace the whole file with:

```python
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

_EDITABLE_FIELDS = {
    "require_admin_approval",
    "exclude_explicit",
    "default_playlist_id",
    "insert_tracks_ahead",
    "playlist_repeat_enabled",
    "poll_interval_seconds",
    "pending_request_timeout_minutes",
}

_SELECT_CONFIG = (
    "SELECT require_admin_approval, exclude_explicit, default_playlist_id, "
    "insert_tracks_ahead, playlist_repeat_enabled, poll_interval_seconds, "
    "pending_request_timeout_minutes, updated_at "
    "FROM config WHERE id = 1"
)


@dataclass
class AppConfig:
    require_admin_approval: bool
    exclude_explicit: bool
    default_playlist_id: str | None
    insert_tracks_ahead: int
    playlist_repeat_enabled: bool
    poll_interval_seconds: int
    pending_request_timeout_minutes: int
    updated_at: datetime


def get_config(conn: sqlite3.Connection) -> AppConfig:
    row = conn.execute(_SELECT_CONFIG).fetchone()
    return _row_to_config(row)


def update_config(conn: sqlite3.Connection, **changes) -> AppConfig:
    unknown_fields = set(changes) - _EDITABLE_FIELDS
    if unknown_fields:
        raise ValueError(f"Unknown config fields: {sorted(unknown_fields)}")

    if changes:
        # Field names are interpolated, but only after the allowlist check above —
        # every key in `changes` is guaranteed to be one of _EDITABLE_FIELDS, never
        # arbitrary input. Values are still passed as parameters, never interpolated.
        set_clause = ", ".join(f"{field} = ?" for field in changes)
        conn.execute(
            f"UPDATE config SET {set_clause}, updated_at = ?"  # nosec B608
            " WHERE id = 1",
            (*changes.values(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    return get_config(conn)


def _row_to_config(row) -> AppConfig:
    (
        require_admin_approval,
        exclude_explicit,
        default_playlist_id,
        insert_tracks_ahead,
        playlist_repeat_enabled,
        poll_interval_seconds,
        pending_request_timeout_minutes,
        updated_at,
    ) = row
    return AppConfig(
        require_admin_approval=bool(require_admin_approval),
        exclude_explicit=bool(exclude_explicit),
        default_playlist_id=default_playlist_id,
        insert_tracks_ahead=insert_tracks_ahead,
        playlist_repeat_enabled=bool(playlist_repeat_enabled),
        poll_interval_seconds=poll_interval_seconds,
        pending_request_timeout_minutes=pending_request_timeout_minutes,
        updated_at=datetime.fromisoformat(updated_at),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_config_model.py -v`
Expected: all tests PASS (7 total: 5 pre-existing + 2 new).

- [ ] **Step 6: Run the full suite to check nothing else broke**

Run: `pytest -q`
Expected: all tests PASS (193 pre-existing + 2 new = 195).

- [ ] **Step 7: Commit**

```bash
git add app/migrations/0007_pending_request_timeout.sql app/models/config.py tests/unit/test_config_model.py
git commit -m "Add pending_request_timeout_minutes to config"
```

---

### Task 2: Requests model support for the reminder flag and timeout queries

**Files:**
- Modify: `app/models/requests.py`
- Test: create `tests/unit/test_requests_model_timeout.py`

**Interfaces:**
- Consumes: the `requests.reminder_sent_at` column added by Task 1's migration.
- Produces:
  - `Request.reminder_sent_at: datetime | None` (new field on the existing dataclass)
  - `list_pending_requested_before(conn: sqlite3.Connection, cutoff: datetime) -> list[Request]`
  - `list_pending_needing_reminder(conn: sqlite3.Connection, cutoff: datetime) -> list[Request]`
  - `mark_reminder_sent(conn: sqlite3.Connection, request_id: int) -> None`

  Task 4 calls all three of these by exact name.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_requests_model_timeout.py`:

```python
from datetime import datetime, timedelta, timezone

from app.db import get_connection, run_migrations
from app.models.requests import (
    create_request,
    get_by_id,
    list_pending_needing_reminder,
    list_pending_requested_before,
    mark_reminder_sent,
)


def _connection(tmp_path):
    conn = get_connection(str(tmp_path / "test.db"))
    run_migrations(conn)
    return conn


def _create(conn, **overrides):
    defaults = dict(
        spotify_track_uri="spotify:track:abc123",
        track_name="A Song",
        artist_name="An Artist",
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


def test_new_request_has_no_reminder_sent_at(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    assert created.reminder_sent_at is None


def test_list_pending_requested_before_returns_only_older_pending_requests(tmp_path):
    conn = _connection(tmp_path)
    old = _create(conn, spotify_track_uri="spotify:track:old")
    _backdate(conn, old.id, minutes_ago=20)
    _create(conn, spotify_track_uri="spotify:track:new")

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    results = list_pending_requested_before(conn, cutoff)

    assert [r.id for r in results] == [old.id]


def test_list_pending_needing_reminder_excludes_already_reminded(tmp_path):
    conn = _connection(tmp_path)
    old = _create(conn, spotify_track_uri="spotify:track:old")
    _backdate(conn, old.id, minutes_ago=20)
    already_reminded = _create(conn, spotify_track_uri="spotify:track:reminded")
    _backdate(conn, already_reminded.id, minutes_ago=20)
    mark_reminder_sent(conn, already_reminded.id)

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)
    results = list_pending_needing_reminder(conn, cutoff)

    assert [r.id for r in results] == [old.id]


def test_mark_reminder_sent_sets_the_timestamp(tmp_path):
    conn = _connection(tmp_path)
    created = _create(conn)

    mark_reminder_sent(conn, created.id)

    updated = get_by_id(conn, created.id)
    assert updated.reminder_sent_at is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_requests_model_timeout.py -v`
Expected: FAIL with `ImportError: cannot import name 'list_pending_requested_before' from 'app.models.requests'`.

- [ ] **Step 3: Update `app/models/requests.py`**

Change the `_SELECT_REQUEST` constant to:

```python
_SELECT_REQUEST = (
    "SELECT id, spotify_track_uri, track_name, artist_name, is_explicit, requestor_name, "
    "reference_code, device_token, client_ip, status, requested_at, decided_at, decided_by, "
    "playlist_insert_position, reminder_sent_at FROM requests"
)
```

Add `reminder_sent_at: datetime | None` as the last field of the `Request` dataclass:

```python
@dataclass
class Request:
    id: int
    spotify_track_uri: str
    track_name: str
    artist_name: str
    is_explicit: bool
    requestor_name: str
    reference_code: str
    device_token: str
    client_ip: str
    status: str
    requested_at: datetime
    decided_at: datetime | None
    decided_by: str | None
    playlist_insert_position: int | None
    reminder_sent_at: datetime | None
```

Add these three functions after `list_added` (before `_row_to_request`):

```python
def list_pending_requested_before(
    conn: sqlite3.Connection, cutoff: datetime
) -> list[Request]:
    rows = conn.execute(
        f"{_SELECT_REQUEST} WHERE status = 'pending' AND requested_at <= ? "
        "ORDER BY requested_at",
        (cutoff.isoformat(),),
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def list_pending_needing_reminder(
    conn: sqlite3.Connection, cutoff: datetime
) -> list[Request]:
    rows = conn.execute(
        f"{_SELECT_REQUEST} WHERE status = 'pending' AND reminder_sent_at IS NULL "
        "AND requested_at <= ? ORDER BY requested_at",
        (cutoff.isoformat(),),
    ).fetchall()
    return [_row_to_request(row) for row in rows]


def mark_reminder_sent(conn: sqlite3.Connection, request_id: int) -> None:
    conn.execute(
        "UPDATE requests SET reminder_sent_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), request_id),
    )
    conn.commit()
```

Update `_row_to_request` to unpack and set the new column — replace the whole function with:

```python
def _row_to_request(row) -> Request:
    (
        id_,
        spotify_track_uri,
        track_name,
        artist_name,
        is_explicit,
        requestor_name,
        reference_code,
        device_token,
        client_ip,
        status,
        requested_at,
        decided_at,
        decided_by,
        playlist_insert_position,
        reminder_sent_at,
    ) = row
    return Request(
        id=id_,
        spotify_track_uri=spotify_track_uri,
        track_name=track_name,
        artist_name=artist_name,
        is_explicit=bool(is_explicit),
        requestor_name=requestor_name,
        reference_code=reference_code,
        device_token=device_token,
        client_ip=client_ip,
        status=status,
        requested_at=datetime.fromisoformat(requested_at),
        decided_at=datetime.fromisoformat(decided_at) if decided_at else None,
        decided_by=decided_by,
        playlist_insert_position=playlist_insert_position,
        reminder_sent_at=(
            datetime.fromisoformat(reminder_sent_at) if reminder_sent_at else None
        ),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_requests_model_timeout.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Run the full suite to check nothing else broke**

Run: `pytest -q`
Expected: all tests PASS (195 + 4 = 199). This also confirms every other place that constructs/reads a `Request` still works with the new dataclass field.

- [ ] **Step 6: Commit**

```bash
git add app/models/requests.py tests/unit/test_requests_model_timeout.py
git commit -m "Add reminder-tracking queries to the requests model"
```

---

### Task 3: SMTP settings + notification email module

**Files:**
- Modify: `app/config.py`
- Create: `app/services/notifications.py`
- Test: create `tests/unit/test_notifications.py`

**Interfaces:**
- Consumes: `app.models.requests.Request` (for the fields used in the email body: `track_name`, `artist_name`, `requestor_name`, `reference_code`, `id`).
- Produces:
  - `EmailSettings` dataclass (fields: `smtp_host: str`, `smtp_port: int`, `smtp_username: str`, `smtp_password: str`, `smtp_from_address: str`, `notification_email: str`, `domain: str`)
  - `send_pending_request_reminder(request: Request, email_settings: EmailSettings) -> None`

  Task 4 imports both by exact name from `app.services.notifications`.

- [ ] **Step 1: Add new settings to `app/config.py`**

Modify `app/config.py` — add these fields to the `Settings` class, after `secure_cookies`:

```python
    # SMTP settings for the pending-request reminder email (docs/superpowers/specs/
    # 2026-07-25-pending-request-auto-approval-design.md). All optional — if
    # smtp_host or notification_email is blank, app/services/notifications.py skips
    # sending rather than raising, so an unconfigured deploy (or local dev) never
    # breaks because of this.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = ""
    # Deployment-time only, deliberately not part of the DB config table — see the
    # design doc's "Notification recipient is not a DB field" note. Displayed
    # read-only on /admin/config, never editable through the admin panel.
    notification_email: str = ""
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_notifications.py`:

```python
from datetime import datetime, timezone

from app.models.requests import Request
from app.services.notifications import EmailSettings, send_pending_request_reminder


def _request(**overrides):
    defaults = dict(
        id=1,
        spotify_track_uri="spotify:track:abc123",
        track_name="A Song",
        artist_name="An Artist",
        is_explicit=False,
        requestor_name="Alex",
        reference_code="ABCD1234",
        device_token="device-1",
        client_ip="1.2.3.4",
        status="pending",
        requested_at=datetime.now(timezone.utc),
        decided_at=None,
        decided_by=None,
        playlist_insert_position=None,
        reminder_sent_at=None,
    )
    defaults.update(overrides)
    return Request(**defaults)


def _email_settings(**overrides):
    defaults = dict(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_username="user@example.com",
        smtp_password="secret",
        smtp_from_address="noreply@example.com",
        notification_email="admin@example.com",
        domain="event-playlist.example.com",
    )
    defaults.update(overrides)
    return EmailSettings(**defaults)


class _FakeSMTP:
    calls = []

    def __init__(self, host, port, timeout=10):
        _FakeSMTP.calls.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self):
        _FakeSMTP.calls.append(("starttls",))

    def login(self, username, password):
        _FakeSMTP.calls.append(("login", username, password))

    def send_message(self, message):
        _FakeSMTP.calls.append(("send", message))


def test_sends_via_smtp_with_expected_content(monkeypatch):
    _FakeSMTP.calls = []
    monkeypatch.setattr(
        "app.services.notifications.smtplib.SMTP", _FakeSMTP
    )

    send_pending_request_reminder(_request(), _email_settings())

    kinds = [call[0] for call in _FakeSMTP.calls]
    assert kinds == ["connect", "starttls", "login", "send"]
    sent_message = _FakeSMTP.calls[-1][1]
    assert sent_message["To"] == "admin@example.com"
    assert "A Song" in sent_message.get_content()
    assert "ABCD1234" in sent_message.get_content()
    assert "event-playlist.example.com/admin/requests" in sent_message.get_content()


def test_skips_sending_when_smtp_host_is_blank(monkeypatch):
    _FakeSMTP.calls = []
    monkeypatch.setattr(
        "app.services.notifications.smtplib.SMTP", _FakeSMTP
    )

    send_pending_request_reminder(
        _request(), _email_settings(smtp_host="")
    )

    assert _FakeSMTP.calls == []


def test_skips_sending_when_notification_email_is_blank(monkeypatch):
    _FakeSMTP.calls = []
    monkeypatch.setattr(
        "app.services.notifications.smtplib.SMTP", _FakeSMTP
    )

    send_pending_request_reminder(
        _request(), _email_settings(notification_email="")
    )

    assert _FakeSMTP.calls == []


def test_does_not_raise_when_smtp_connection_fails(monkeypatch):
    class _RaisingSMTP:
        def __init__(self, host, port, timeout=10):
            raise OSError("connection refused")

    monkeypatch.setattr(
        "app.services.notifications.smtplib.SMTP", _RaisingSMTP
    )

    send_pending_request_reminder(_request(), _email_settings())  # must not raise
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_notifications.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.notifications'`.

- [ ] **Step 4: Create `app/services/notifications.py`**

```python
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from app.models.requests import Request

logger = logging.getLogger(__name__)


@dataclass
class EmailSettings:
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from_address: str
    notification_email: str
    domain: str


def send_pending_request_reminder(
    request: Request, email_settings: EmailSettings
) -> None:
    # Both blank is the default, unconfigured state (local dev, or a deploy that
    # hasn't set these up yet) — skip quietly rather than raising, since a missing
    # reminder email must never block the actual auto-approval safety net.
    if not email_settings.smtp_host or not email_settings.notification_email:
        logger.warning(
            "Skipping pending-request reminder email for request %s: "
            "SMTP_HOST or NOTIFICATION_EMAIL not configured",
            request.id,
        )
        return

    message = EmailMessage()
    message["Subject"] = f"Pending request waiting: {request.track_name}"
    message["From"] = email_settings.smtp_from_address or email_settings.smtp_username
    message["To"] = email_settings.notification_email
    message.set_content(
        "A song request has been pending and will auto-approve soon if not "
        "reviewed.\n\n"
        f"Track: {request.track_name} — {request.artist_name}\n"
        f"Requested by: {request.requestor_name}\n"
        f"Reference code: {request.reference_code}\n\n"
        f"Review it: https://{email_settings.domain}/admin/requests\n"
    )

    # A failed send must never propagate — this is a best-effort courtesy
    # notification, not the backlog-prevention mechanism itself (the timeout loop's
    # auto-approval step runs regardless of whether this succeeded).
    try:
        with smtplib.SMTP(
            email_settings.smtp_host, email_settings.smtp_port, timeout=10
        ) as smtp:
            smtp.starttls()
            if email_settings.smtp_username:
                smtp.login(email_settings.smtp_username, email_settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError):
        logger.exception(
            "Failed to send pending-request reminder email for request %s",
            request.id,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_notifications.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 6: Run the full suite, lint, and format check**

Run: `pytest -q`
Expected: all tests PASS (199 + 4 = 203).

Run: `ruff check . && black --check .`
Expected: both report no issues.

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/services/notifications.py tests/unit/test_notifications.py
git commit -m "Add SMTP settings and the pending-request reminder email module"
```

---

### Task 4: Background timeout loop

**Files:**
- Create: `app/worker/request_timeout.py`
- Test: create `tests/unit/test_request_timeout.py`

**Interfaces:**
- Consumes:
  - `app.models.requests.list_pending_requested_before`, `list_pending_needing_reminder`, `mark_reminder_sent` (Task 2)
  - `app.services.notifications.EmailSettings`, `send_pending_request_reminder` (Task 3)
  - `app.services.playlist_ops.approve_request(conn, cipher, client, *, client_id, client_secret, request_id, admin_username) -> Request` and `PlaylistApprovalError` (pre-existing)
  - `app.models.config.get_config` (pre-existing, returns `AppConfig.pending_request_timeout_minutes` / `.poll_interval_seconds`)
- Produces:
  - `run_timeout_tick(conn, cipher, client, *, client_id, client_secret, timeout_minutes, email_settings) -> None`
  - `timeout_loop_forever(database_path, cipher, client, *, client_id, client_secret, email_settings, stop_event) -> None`

  Task 5 calls `timeout_loop_forever` by exact name/signature from `app.main`.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_request_timeout.py`:

```python
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
        conn, cipher, access_token="valid-token", refresh_token="r",
        expires_in=3600, scope="s",
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
        smtp_host="", smtp_port=587, smtp_username="", smtp_password="",
        smtp_from_address="", notification_email="", domain="example.com",
    )


@pytest.mark.asyncio
async def test_tick_auto_approves_a_request_past_its_timeout(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", pending_request_timeout_minutes=15)
    created = _create_pending(conn, spotify_track_uri="spotify:track:new")
    _backdate(conn, created.id, minutes_ago=20)
    client = _mock_spotify_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=["spotify:track:current"],
    )

    await run_timeout_tick(
        conn, cipher, client, client_id="id", client_secret="secret",
        timeout_minutes=15, email_settings=_no_op_email_settings(),
    )

    updated = get_by_id(conn, created.id)
    assert updated.status == "added"
    assert updated.decided_by == "system"


@pytest.mark.asyncio
async def test_tick_leaves_a_request_under_its_timeout_alone(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", pending_request_timeout_minutes=15)
    created = _create_pending(conn, spotify_track_uri="spotify:track:new")
    _backdate(conn, created.id, minutes_ago=5)
    client = _mock_spotify_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=["spotify:track:current"],
    )

    await run_timeout_tick(
        conn, cipher, client, client_id="id", client_secret="secret",
        timeout_minutes=15, email_settings=_no_op_email_settings(),
    )

    updated = get_by_id(conn, created.id)
    assert updated.status == "pending"


@pytest.mark.asyncio
async def test_tick_marks_reminder_sent_without_auto_approving_early(tmp_path):
    conn = _connection(tmp_path)
    cipher = _connected_cipher(conn)
    update_config(conn, default_playlist_id="playlist123", pending_request_timeout_minutes=15)
    created = _create_pending(conn, spotify_track_uri="spotify:track:new")
    _backdate(conn, created.id, minutes_ago=14)  # 1 minute inside the 2-minute window
    client = _mock_spotify_client(
        now_playing_uri="spotify:track:current",
        playlist_uris=["spotify:track:current"],
    )

    await run_timeout_tick(
        conn, cipher, client, client_id="id", client_secret="secret",
        timeout_minutes=15, email_settings=_no_op_email_settings(),
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
        conn, cipher, client, client_id="id", client_secret="secret",
        timeout_minutes=15, email_settings=_no_op_email_settings(),
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
            str(tmp_path / "test.db"), cipher, client,
            client_id="id", client_secret="secret",
            email_settings=_no_op_email_settings(), stop_event=stop_event,
        ),
        timeout=2,
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_request_timeout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.worker.request_timeout'`.

- [ ] **Step 3: Create `app/worker/request_timeout.py`**

```python
import asyncio
import contextlib
import logging
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx2

from app.db import get_connection
from app.models.config import get_config
from app.models.requests import (
    list_pending_needing_reminder,
    list_pending_requested_before,
    mark_reminder_sent,
)
from app.services.crypto import TokenCipher
from app.services.notifications import EmailSettings, send_pending_request_reminder
from app.services.playlist_ops import approve_request

logger = logging.getLogger(__name__)

DEFAULT_TICK_INTERVAL_SECONDS = 15
REMINDER_LEAD_MINUTES = 2


async def run_timeout_tick(
    conn: sqlite3.Connection,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    timeout_minutes: int,
    email_settings: EmailSettings,
) -> None:
    """Runs one tick: sends the one-time reminder for any pending request within
    REMINDER_LEAD_MINUTES of its own timeout, then auto-approves any pending
    request that's actually past its timeout. Reads fresh from the DB every call —
    no state carried between ticks, same accepted tradeoff as the Spotify poller's
    last_known_uri (a restart can only delay one tick's worth of work, never lose
    or duplicate it, since every check re-derives from requested_at/reminder_sent_at
    on the row itself)."""
    now = datetime.now(timezone.utc)

    if timeout_minutes > REMINDER_LEAD_MINUTES:
        reminder_cutoff = now - timedelta(
            minutes=timeout_minutes - REMINDER_LEAD_MINUTES
        )
        for request in list_pending_needing_reminder(conn, reminder_cutoff):
            # send_pending_request_reminder never raises (see
            # app/services/notifications.py) — marking as sent regardless of
            # whether delivery actually succeeded is deliberate: this is a
            # best-effort courtesy notification, not the safety net itself, and a
            # single-attempt policy avoids resending forever against a
            # persistently broken SMTP config.
            send_pending_request_reminder(request, email_settings)
            mark_reminder_sent(conn, request.id)

    approval_cutoff = now - timedelta(minutes=timeout_minutes)
    for request in list_pending_requested_before(conn, approval_cutoff):
        try:
            await approve_request(
                conn,
                cipher,
                client,
                client_id=client_id,
                client_secret=client_secret,
                request_id=request.id,
                admin_username="system",
            )
        except Exception:
            # One request that can't be auto-approved right now (no default
            # playlist configured, Spotify temporarily unreachable, etc.) must not
            # block the rest of the tick — it simply gets retried next tick.
            logger.exception("Auto-approval failed for request %s", request.id)


async def timeout_loop_forever(
    database_path: str,
    cipher: TokenCipher,
    client: httpx2.AsyncClient,
    *,
    client_id: str,
    client_secret: str,
    email_settings: EmailSettings,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        conn = get_connection(database_path)
        interval = DEFAULT_TICK_INTERVAL_SECONDS
        try:
            config = get_config(conn)
            interval = config.poll_interval_seconds
            await run_timeout_tick(
                conn,
                cipher,
                client,
                client_id=client_id,
                client_secret=client_secret,
                timeout_minutes=config.pending_request_timeout_minutes,
                email_settings=email_settings,
            )
        except Exception:
            logger.exception("Request-timeout tick failed")
        finally:
            conn.close()

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_request_timeout.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Run the full suite, lint, and format check**

Run: `pytest -q`
Expected: all tests PASS (203 + 5 = 208).

Run: `ruff check . && black --check .`
Expected: both report no issues.

- [ ] **Step 6: Commit**

```bash
git add app/worker/request_timeout.py tests/unit/test_request_timeout.py
git commit -m "Add the pending-request timeout/reminder background loop"
```

---

### Task 5: Wire the new loop into the app lifespan

**Files:**
- Modify: `app/main.py`

**Interfaces:**
- Consumes: `app.worker.request_timeout.timeout_loop_forever` (Task 4), `app.services.notifications.EmailSettings` (Task 3).
- Produces: nothing new consumed by later tasks — this is the final wiring point.

There's no unit test for lifespan wiring in this codebase (the Spotify poller's own wiring in `app/main.py` isn't unit-tested either — it's implicitly covered by every router test that boots the app via `TestClient`). This task's verification is the full test suite plus a manual local run.

- [ ] **Step 1: Modify `app/main.py`**

Change the import block — replace:

```python
from app.services.admin_seed import seed_default_admin_if_needed
from app.services.crypto import TokenCipher
from app.worker.poller import poll_forever
```

with:

```python
from app.services.admin_seed import seed_default_admin_if_needed
from app.services.crypto import TokenCipher
from app.services.notifications import EmailSettings
from app.worker.poller import poll_forever
from app.worker.request_timeout import timeout_loop_forever
```

Then replace the whole `lifespan` function with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(settings.database_path)
    run_migrations(conn)
    seed_default_admin_if_needed(conn, database_path=settings.database_path)
    conn.close()

    app.state.http_client = httpx2.AsyncClient()

    # Single in-process asyncio task, not a separate service — SPEC.md §2/§6.4
    # require the app to run as exactly one process (uvicorn --workers 1, one
    # container replica) specifically because of this poller. Running multiple
    # workers/replicas would start it multiple times, duplicating Spotify API
    # calls and racing playlist edits from independent pollers.
    poller_stop_event = asyncio.Event()
    poller_task = asyncio.create_task(
        poll_forever(
            settings.database_path,
            TokenCipher(key=settings.token_encryption_key),
            app.state.http_client,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            stop_event=poller_stop_event,
        )
    )

    # A second, independent in-process task — same single-process reasoning as the
    # poller above applies here too, but this loop is otherwise unrelated to it: it
    # only touches the requests table and calls approve_request(), never Spotify
    # playback-state polling. See docs/superpowers/specs/
    # 2026-07-25-pending-request-auto-approval-design.md for why this isn't folded
    # into the poller's own tick instead.
    timeout_stop_event = asyncio.Event()
    timeout_task = asyncio.create_task(
        timeout_loop_forever(
            settings.database_path,
            TokenCipher(key=settings.token_encryption_key),
            app.state.http_client,
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            email_settings=EmailSettings(
                smtp_host=settings.smtp_host,
                smtp_port=settings.smtp_port,
                smtp_username=settings.smtp_username,
                smtp_password=settings.smtp_password,
                smtp_from_address=settings.smtp_from_address,
                notification_email=settings.notification_email,
                domain=settings.domain,
            ),
            stop_event=timeout_stop_event,
        )
    )

    yield

    poller_stop_event.set()
    timeout_stop_event.set()
    with contextlib.suppress(asyncio.CancelledError):
        await poller_task
    with contextlib.suppress(asyncio.CancelledError):
        await timeout_task
    await app.state.http_client.aclose()
```

- [ ] **Step 2: Run the full suite**

Run: `pytest -q`
Expected: all 208 tests PASS — every router test boots the full app (including this lifespan) via `TestClient`, so any wiring mistake (typo, wrong kwarg name, import error) surfaces immediately as a widespread failure, not silence.

- [ ] **Step 3: Manually verify the app still boots locally**

```bash
cp .env.example .env
source .venv/bin/activate
uvicorn app.main:app --reload
```

Expected: startup logs show no traceback; `curl http://localhost:8000/healthz` returns `{"status":"ok"}` (or equivalent 200 response). Stop the server with Ctrl+C afterward — expect a clean shutdown with no `asyncio` warnings about a task never being awaited.

- [ ] **Step 4: Commit**

```bash
git add app/main.py
git commit -m "Start the pending-request timeout loop alongside the Spotify poller"
```

---

### Task 6: Admin Config page — editable timeout, read-only notification email

**Files:**
- Modify: `app/routers/admin_config.py`
- Modify: `app/templates/admin/config.html`
- Test: `tests/unit/test_admin_config_routes.py`

**Interfaces:**
- Consumes: `app.models.config.AppConfig.pending_request_timeout_minutes` (Task 1), `app.config.settings.notification_email` (Task 3).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing tests**

In `tests/unit/test_admin_config_routes.py`, change the import block from:

```python
from app.db import get_connection, run_migrations
from app.dependencies import get_db
from app.main import app
from app.security.session import require_onboarded_admin
```

to:

```python
from app.config import settings
from app.db import get_connection, run_migrations
from app.dependencies import get_db
from app.main import app
from app.security.session import require_onboarded_admin
```

Then add these tests at the end of the file:

```python
def test_config_form_renders_pending_request_timeout(client):
    response = client.get("/admin/config")

    assert response.status_code == 200
    assert 'name="pending_request_timeout_minutes" value="15"' in response.text


def test_config_form_renders_notification_email_as_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "notification_email", "admin@example.com")

    response = client.get("/admin/config")

    assert 'value="admin@example.com"' in response.text
    assert "disabled" in response.text


def test_config_submit_persists_pending_request_timeout(client, db_path):
    csrf_token = _extract_csrf_token(client.get("/admin/config").text)

    response = client.post(
        "/admin/config",
        data={
            "insert_tracks_ahead": "3",
            "poll_interval_seconds": "15",
            "pending_request_timeout_minutes": "45",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT pending_request_timeout_minutes FROM config"
    ).fetchone()
    assert row == (45,)


def test_config_submit_cannot_change_notification_email(client, db_path, monkeypatch):
    monkeypatch.setattr(settings, "notification_email", "real@example.com")
    csrf_token = _extract_csrf_token(client.get("/admin/config").text)

    # A disabled field never submits, but this also proves the server ignores it
    # even if a crafted request includes it — there's no code path that reads
    # notification_email out of the POST body at all.
    client.post(
        "/admin/config",
        data={
            "insert_tracks_ahead": "3",
            "poll_interval_seconds": "15",
            "pending_request_timeout_minutes": "15",
            "notification_email": "attacker@example.com",
            "csrf_token": csrf_token,
        },
        follow_redirects=False,
    )

    assert settings.notification_email == "real@example.com"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_admin_config_routes.py -v`
Expected: `test_config_form_renders_pending_request_timeout` and `test_config_submit_persists_pending_request_timeout` FAIL (field doesn't exist yet); `test_config_form_renders_notification_email_as_disabled` FAILs (nothing in the response mentions the email); `test_config_submit_cannot_change_notification_email` trivially PASSES already (no code reads that field), which is fine — it's here to lock in the behavior going forward, not to currently fail.

- [ ] **Step 3: Update `app/routers/admin_config.py`**

Replace the whole file with:

```python
import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import get_db
from app.models.audit_log import write_audit_log
from app.models.config import get_config, update_config
from app.security.csrf import get_or_create_csrf_token, verify_csrf_token
from app.security.session import require_onboarded_admin

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


@router.get("/config")
async def config_form(
    request: Request,
    _username: str = Depends(require_onboarded_admin),
    db: sqlite3.Connection = Depends(get_db),
) -> Response:
    config = get_config(db)
    return templates.TemplateResponse(
        request,
        "admin/config.html",
        {
            "config": config,
            "notification_email": settings.notification_email,
            "csrf_token": get_or_create_csrf_token(request),
        },
    )


@router.post("/config")
async def config_submit(
    request: Request,
    require_admin_approval: bool = Form(False),
    exclude_explicit: bool = Form(False),
    default_playlist_id: str = Form(""),
    insert_tracks_ahead: int = Form(...),
    playlist_repeat_enabled: bool = Form(False),
    poll_interval_seconds: int = Form(...),
    pending_request_timeout_minutes: int = Form(...),
    username: str = Depends(require_onboarded_admin),
    db: sqlite3.Connection = Depends(get_db),
    _csrf: None = Depends(verify_csrf_token),
) -> Response:
    updated = update_config(
        db,
        require_admin_approval=require_admin_approval,
        exclude_explicit=exclude_explicit,
        default_playlist_id=default_playlist_id or None,
        insert_tracks_ahead=insert_tracks_ahead,
        playlist_repeat_enabled=playlist_repeat_enabled,
        poll_interval_seconds=poll_interval_seconds,
        pending_request_timeout_minutes=pending_request_timeout_minutes,
    )
    write_audit_log(db, actor=username, action="config.update", detail=str(updated))
    return RedirectResponse("/admin/config", status_code=303)
```

- [ ] **Step 4: Update `app/templates/admin/config.html`**

Replace the whole file with:

```html
{% extends "base.html" %}
{% block title %}Admin config{% endblock %}
{% block content %}
{% include "admin/_nav.html" %}
<span class="eyebrow">House Rules</span>
<h1>Config</h1>
<form method="post" action="/admin/config">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <label>
    <input type="checkbox" name="require_admin_approval" value="true" {% if config.require_admin_approval %}checked{% endif %}>
    Require admin approval before adding
  </label>
  <label>
    <input type="checkbox" name="exclude_explicit" value="true" {% if config.exclude_explicit %}checked{% endif %}>
    Exclude explicit tracks
  </label>
  <label>
    Default playlist ID
    <input type="text" name="default_playlist_id" value="{{ config.default_playlist_id or '' }}">
  </label>
  <label>
    Insert requests this many tracks ahead of current
    <input type="number" name="insert_tracks_ahead" value="{{ config.insert_tracks_ahead }}" min="0" required>
  </label>
  <label>
    <input type="checkbox" name="playlist_repeat_enabled" value="true" {% if config.playlist_repeat_enabled %}checked{% endif %}>
    Playlist repeat enabled
  </label>
  <label>
    Poll interval (seconds)
    <input type="number" name="poll_interval_seconds" value="{{ config.poll_interval_seconds }}" min="1" required>
  </label>
  <label>
    Auto-approve a pending request after this many minutes
    <input type="number" name="pending_request_timeout_minutes" value="{{ config.pending_request_timeout_minutes }}" min="1" required>
  </label>
  <label>
    Notification email (set via server config, not editable here)
    <input type="email" value="{{ notification_email or 'not configured' }}" disabled>
  </label>
  <button type="submit">Save settings</button>
</form>
{% endblock %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_admin_config_routes.py -v`
Expected: all tests PASS (7 pre-existing + 4 new).

- [ ] **Step 6: Run the full suite, lint, and format check**

Run: `pytest -q`
Expected: all tests PASS (208 + 4 = 212).

Run: `ruff check . && black --check .`
Expected: both report no issues.

- [ ] **Step 7: Commit**

```bash
git add app/routers/admin_config.py app/templates/admin/config.html tests/unit/test_admin_config_routes.py
git commit -m "Expose the auto-approve timeout and notification email on /admin/config"
```

---

### Task 7: Deployment wiring — env vars, compose file, docs

**Files:**
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:**
- Consumes: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_ADDRESS`, `NOTIFICATION_EMAIL` (the env var names `app/config.py`'s `Settings` class already reads via pydantic-settings' default case-insensitive env-var matching, from Task 3).
- Produces: nothing — this is the final task.

- [ ] **Step 1: Add the new env vars to `.env.example`**

Append to the end of `.env.example`:

```
# SMTP settings for the pending-request reminder email (optional — if SMTP_HOST or
# NOTIFICATION_EMAIL is unset, reminder emails are skipped; auto-approval still
# happens on schedule regardless, since the timeout is the actual backlog-
# prevention mechanism and the email is just a courtesy heads-up)
SMTP_HOST=
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_FROM_ADDRESS=
NOTIFICATION_EMAIL=
```

- [ ] **Step 2: Add the new env vars to `docker-compose.yml`**

In the `app.environment` block, add these six lines after `DOMAIN: ${DOMAIN}`:

```yaml
      DOMAIN: ${DOMAIN}
      SMTP_HOST: ${SMTP_HOST:-}
      SMTP_PORT: ${SMTP_PORT:-587}
      SMTP_USERNAME: ${SMTP_USERNAME:-}
      SMTP_PASSWORD: ${SMTP_PASSWORD:-}
      SMTP_FROM_ADDRESS: ${SMTP_FROM_ADDRESS:-}
      NOTIFICATION_EMAIL: ${NOTIFICATION_EMAIL:-}
```

(i.e. replace the single existing `DOMAIN: ${DOMAIN}` line with all seven lines above.)

- [ ] **Step 3: Validate the compose file**

```bash
cp .env.example .env
docker compose -f docker-compose.yml config > /dev/null && echo "valid"
rm .env
```

Expected output: `valid`

- [ ] **Step 4: Update the README's environment variable table**

In `README.md`, find the table under "In Hostinger's Docker Manager..." (step 4, "Set up Hostinger") that lists `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, etc. Add these rows after the `SECURE_COOKIES` row:

```markdown
| `SMTP_HOST` | optional — SMTP server for the pending-request reminder email; leave blank to disable reminder emails entirely (auto-approval still works without it) |
| `SMTP_PORT` | optional — defaults to `587` |
| `SMTP_USERNAME` | optional — SMTP auth username |
| `SMTP_PASSWORD` | optional — SMTP auth password |
| `SMTP_FROM_ADDRESS` | optional — falls back to `SMTP_USERNAME` if blank |
| `NOTIFICATION_EMAIL` | optional — where pending-request reminder emails are sent; shown read-only on `/admin/config` |
```

- [ ] **Step 5: Run the full suite one last time**

Run: `pytest -q`
Expected: all 212 tests PASS.

Run: `ruff check . && black --check .`
Expected: both report no issues.

- [ ] **Step 6: Commit**

```bash
git add .env.example docker-compose.yml README.md
git commit -m "Document and wire the new SMTP/notification env vars"
```

---

## After implementation

Follow this project's established workflow (see `CLAUDE.md`): push the branch, open a PR, wait for the `lint-test-scan` CI check to go green, then get explicit confirmation before merging. Once merged, `publish-images.yml` builds and pushes new images automatically; a manual "Update" click in Hostinger's Docker Manager is still required to actually deploy (per the already-fixed `pull_policy: always` pipeline). After deploying, the new `SMTP_*`/`NOTIFICATION_EMAIL` env vars need to be set in Hostinger's panel for reminder emails to actually send — auto-approval itself works immediately regardless, using the default 15-minute timeout unless changed on `/admin/config`.
