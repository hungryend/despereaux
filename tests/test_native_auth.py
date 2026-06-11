"""Native auth mode: first-run setup, login/logout sessions, header distrust,
account password change, and admin user management. Default (authentik) mode
behavior is covered by the existing test files; these tests flip the cached
settings object to native per-test."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

ADMIN = "captain"
ADMIN_PW = "correct horse battery"


@pytest.fixture
def native(monkeypatch) -> None:
    """Switch the (cached) settings to native mode with dev fallback off, and
    reset the middleware's setup cache so first-run detection re-queries."""
    from despereaux.config import get_settings
    from despereaux.middleware.auth import reset_setup_cache

    s = get_settings()
    monkeypatch.setattr(s, "auth_mode", "native")
    monkeypatch.setattr(s, "dev_mode", False)
    reset_setup_cache()
    yield
    reset_setup_cache()


@pytest.fixture
def client() -> TestClient:
    from despereaux.main import app

    return TestClient(app)


def _ensure_admin(client: TestClient) -> None:
    """Idempotent: create the native admin via /setup on first call, no-op after."""
    r = client.post(
        "/setup",
        data={"username": ADMIN, "password": ADMIN_PW},
        follow_redirects=False,
    )
    assert r.status_code == 303


def _login(client: TestClient, username: str, password: str) -> bool:
    r = client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    return r.status_code == 303


def test_first_run_setup_then_login_logout(native, client: TestClient) -> None:
    # Anonymous browser request → setup page (no native admin yet) or login
    # (if an earlier test already created one — both are the auth wall).
    r = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] in ("/setup", "/login")

    _ensure_admin(client)

    # The setup response set a session cookie — we're signed in.
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["username"] == ADMIN
    assert me.json()["is_admin"] is True

    # Library page renders with the session.
    assert client.get("/", headers={"Accept": "text/html"}).status_code == 200

    # Logout clears the session.
    client.get("/logout", follow_redirects=False)
    assert client.get("/api/me").status_code == 401

    # Wrong password rejected, right password accepted.
    assert not _login(client, ADMIN, "wrong password!")
    assert _login(client, ADMIN, ADMIN_PW)
    assert client.get("/api/me").json()["username"] == ADMIN


def test_setup_locked_once_admin_exists(native, client: TestClient) -> None:
    _ensure_admin_if_missing(client)
    r = client.get("/setup", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
    # POSTing again must not create another admin — it bounces to /login.
    r = client.post(
        "/setup",
        data={"username": "mallory", "password": "mallory-pw-123"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert not _login(client, "mallory", "mallory-pw-123")


def _ensure_admin_if_missing(client: TestClient) -> None:
    if not _login(client, ADMIN, ADMIN_PW):
        _ensure_admin(client)


def test_native_mode_ignores_authentik_headers(native, client: TestClient) -> None:
    """Without a trusted proxy the identity headers are client-controlled —
    native mode must never honour them."""
    r = client.get(
        "/api/me",
        headers={
            "X-authentik-username": "mallory",
            "X-authentik-groups": "ebook-admin",
        },
    )
    assert r.status_code == 401


def test_unauth_api_is_json_401_browser_redirects(native, client: TestClient) -> None:
    r = client.get("/api/books")
    assert r.status_code == 401
    assert "detail" in r.json()
    r = client.get("/book/some-id", headers={"Accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] in ("/login", "/setup")


def test_api_token_works_in_native_mode(native, client: TestClient) -> None:
    _ensure_admin_if_missing(client)
    minted = client.post("/api/tokens", json={"name": "native-phone"})
    assert minted.status_code == 201
    token = minted.json()["token"]

    fresh = TestClient(client.app)
    r = fresh.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == ADMIN
    # Cookie variant (the WebView path) too.
    r = fresh.get("/api/me", cookies={"despereaux_token": token})
    assert r.status_code == 200


def test_account_password_change(native, client: TestClient) -> None:
    _ensure_admin_if_missing(client)
    assert client.get("/account").status_code == 200

    r = client.post(
        "/account/password",
        data={"current_password": "not the password", "new_password": "a new password 1"},
    )
    assert r.status_code == 422

    r = client.post(
        "/account/password",
        data={"current_password": ADMIN_PW, "new_password": "a new password 1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    client.get("/logout")
    assert not _login(client, ADMIN, ADMIN_PW)
    assert _login(client, ADMIN, "a new password 1")
    # Restore for the other tests (DB is shared across this session).
    client.post(
        "/account/password",
        data={"current_password": "a new password 1", "new_password": ADMIN_PW},
        follow_redirects=False,
    )


def test_admin_user_management(native, client: TestClient) -> None:
    _ensure_admin_if_missing(client)
    assert client.get("/admin/users").status_code == 200

    r = client.post(
        "/admin/users/create",
        data={"username": "deckhand", "password": "deckhand-pw-1", "is_admin": "false"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    member = TestClient(client.app)
    assert _login(member, "deckhand", "deckhand-pw-1")
    me = member.get("/api/me").json()
    assert me["username"] == "deckhand"
    assert me["is_admin"] is False
    # Members can't reach user management.
    assert member.get("/admin/users").status_code == 403
    member_id = me["id"]

    # Admin resets the member's password.
    r = client.post(
        f"/admin/users/{member_id}/password",
        data={"password": "rotated-pw-22"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _login(member, "deckhand", "rotated-pw-22")

    # Promote, then demote.
    client.post(f"/admin/users/{member_id}/admin", data={"make_admin": "true"})
    assert member.get("/api/me").json()["is_admin"] is True
    client.post(f"/admin/users/{member_id}/admin", data={"make_admin": "false"})
    assert member.get("/api/me").json()["is_admin"] is False

    # An admin can't demote themselves.
    own_id = client.get("/api/me").json()["id"]
    r = client.post(
        f"/admin/users/{own_id}/admin", data={"make_admin": "false"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert client.get("/api/me").json()["is_admin"] is True


def test_login_routes_in_authentik_mode(client: TestClient) -> None:
    """Default mode: no login page (the proxy gates), setup hidden."""
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    assert client.get("/setup", follow_redirects=False).status_code == 404
    assert client.post("/setup", data={"username": "x", "password": "yyyyyyyy"}).status_code == 404
