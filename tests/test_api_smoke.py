"""Smoke tests for the app: healthz + webhook auth flow. Uses FastAPI's TestClient
which talks to the ASGI app in-process (no real socket)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from despereaux.main import app

    return TestClient(app)


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_sync_no_token(client: TestClient) -> None:
    r = client.post("/api/admin/sync", json={"paths": []})
    assert r.status_code == 401
    assert "bearer" in r.json()["detail"].lower()


def test_sync_wrong_token(client: TestClient) -> None:
    r = client.post(
        "/api/admin/sync",
        headers={"authorization": "Bearer not-the-token"},
        json={"paths": []},
    )
    assert r.status_code == 403


def test_sync_right_token_full_scan(client: TestClient) -> None:
    r = client.post(
        "/api/admin/sync",
        headers={"authorization": "Bearer test-token-aaaaaaaaaaaaaaaaaaaaa"},
        json={"paths": []},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert body["mode"] == "full_scan"


def test_sync_right_token_targeted(client: TestClient) -> None:
    r = client.post(
        "/api/admin/sync",
        headers={"authorization": "Bearer test-token-aaaaaaaaaaaaaaaaaaaaa"},
        json={"paths": ["/ebooks/nonexistent.epub"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "targeted"
    assert body["count"] == 1


def test_api_books_dev_user(client: TestClient) -> None:
    """In DESPEREAUX_DEV_MODE=true the middleware auto-creates a devuser, so /api/books
    should return 200 with an empty list (or whatever's been ingested in this test run)."""
    r = client.get("/api/books")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
