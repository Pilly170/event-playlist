from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = ""
    token_encryption_key: str = ""
    session_secret_key: str = ""
    database_path: str = "./data/app.db"
    domain: str = "localhost"
    # False by default for local/plain-HTTP dev; set True once Hostinger's TLS
    # termination is confirmed (SPEC.md §9/§12.1) — a Secure cookie is silently
    # dropped by browsers over plain HTTP, so flipping this too early breaks every
    # cookie-based feature (admin session, public device token) at once.
    secure_cookies: bool = False
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


settings = Settings()
