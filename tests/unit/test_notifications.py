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
    monkeypatch.setattr("app.services.notifications.smtplib.SMTP", _FakeSMTP)

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
    monkeypatch.setattr("app.services.notifications.smtplib.SMTP", _FakeSMTP)

    send_pending_request_reminder(_request(), _email_settings(smtp_host=""))

    assert _FakeSMTP.calls == []


def test_skips_sending_when_notification_email_is_blank(monkeypatch):
    _FakeSMTP.calls = []
    monkeypatch.setattr("app.services.notifications.smtplib.SMTP", _FakeSMTP)

    send_pending_request_reminder(_request(), _email_settings(notification_email=""))

    assert _FakeSMTP.calls == []


def test_does_not_raise_when_smtp_connection_fails(monkeypatch):
    class _RaisingSMTP:
        def __init__(self, host, port, timeout=10):
            raise OSError("connection refused")

    monkeypatch.setattr("app.services.notifications.smtplib.SMTP", _RaisingSMTP)

    send_pending_request_reminder(_request(), _email_settings())  # must not raise
