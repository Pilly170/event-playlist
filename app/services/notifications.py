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
