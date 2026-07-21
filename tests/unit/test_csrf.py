from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app.security.csrf import get_or_create_csrf_token, verify_csrf_token


def _build_app():
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")

    @app.get("/form")
    def form(request: Request):
        return {"csrf_token": get_or_create_csrf_token(request)}

    @app.post("/submit")
    def submit(_: None = Depends(verify_csrf_token)):
        return {"ok": True}

    return app


def test_get_or_create_csrf_token_is_stable_across_requests_in_the_same_session():
    client = TestClient(_build_app())

    first = client.get("/form").json()["csrf_token"]
    second = client.get("/form").json()["csrf_token"]

    assert first == second


def test_get_or_create_csrf_token_differs_across_sessions():
    app = _build_app()

    token_a = TestClient(app).get("/form").json()["csrf_token"]
    token_b = TestClient(app).get("/form").json()["csrf_token"]

    assert token_a != token_b


def test_submit_succeeds_with_the_matching_token():
    client = TestClient(_build_app())
    token = client.get("/form").json()["csrf_token"]

    response = client.post("/submit", data={"csrf_token": token})

    assert response.status_code == 200


def test_submit_rejects_a_missing_session_token():
    client = TestClient(_build_app())

    response = client.post("/submit", data={"csrf_token": "anything"})

    assert response.status_code == 403


def test_submit_rejects_a_wrong_token():
    client = TestClient(_build_app())
    client.get("/form")

    response = client.post("/submit", data={"csrf_token": "the-wrong-token"})

    assert response.status_code == 403


def test_submit_rejects_a_token_from_a_different_session():
    app = _build_app()
    client_a = TestClient(app)
    client_a.get("/form")
    client_b = TestClient(app)
    token_b = client_b.get("/form").json()["csrf_token"]

    response = client_a.post("/submit", data={"csrf_token": token_b})

    assert response.status_code == 403
