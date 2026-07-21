import secrets

from fastapi import Form, HTTPException, Request

CSRF_SESSION_KEY = "csrf_token"


def get_or_create_csrf_token(request: Request) -> str:
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


async def verify_csrf_token(request: Request, csrf_token: str = Form(...)) -> None:
    expected = request.session.get(CSRF_SESSION_KEY)
    if not expected or not secrets.compare_digest(csrf_token, expected):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")
