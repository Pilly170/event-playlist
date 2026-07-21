import secrets

from fastapi import Request, Response

from app.config import settings

DEVICE_TOKEN_COOKIE_NAME = "device_token"
DEVICE_TOKEN_MAX_AGE_SECONDS = (
    365 * 24 * 60 * 60
)  # ~1 year — long-lived, opaque, no PII (SPEC.md §6.1)


def get_or_generate_device_token(request: Request) -> str:
    return request.cookies.get(DEVICE_TOKEN_COOKIE_NAME) or secrets.token_urlsafe(24)


def set_device_token_cookie(response: Response, token: str) -> None:
    # Setting this on the injected `response: Response` FastAPI dependency parameter
    # does NOT work when the route returns its own Response object (e.g. a
    # TemplateResponse) instead of letting FastAPI build one — FastAPI only merges
    # that placeholder's headers into a response it constructs itself. Call this on
    # the actual Response object the route is about to return.
    response.set_cookie(
        DEVICE_TOKEN_COOKIE_NAME,
        token,
        max_age=DEVICE_TOKEN_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )
