"""Per-user API token tests: admin mint/list/revoke + the middleware auth chain
(authentik headers > bearer/cookie token > dev-mode fallback)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from despereaux.main import app

    return TestClient(app)


def _mint(client: TestClient, username: str, name: str = "test-device") -> dict:
    # conftest runs DEV_MODE=true → headerless requests are admin devuser.
    r = client.post("/api/admin/tokens", json={"username": username, "name": name})
    assert r.status_code == 201, r.text
    return r.json()


def test_mint_returns_plaintext_once(client: TestClient) -> None:
    body = _mint(client, "alice", "alice-phone")
    assert body["username"] == "alice"
    assert body["name"] == "alice-phone"
    assert body["token"].startswith("desp_")
    assert len(body["token"]) > 30

    listed = client.get("/api/admin/tokens").json()
    mine = [t for t in listed if t["id"] == body["id"]]
    assert len(mine) == 1
    # The list must never expose the plaintext or the hash.
    assert "token" not in mine[0]
    assert "token_hash" not in mine[0]
    assert mine[0]["username"] == "alice"


def test_bearer_token_authenticates_as_owner(client: TestClient) -> None:
    token = _mint(client, "bob")["token"]
    r = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "bob"
    # App-only users provisioned via token minting are NOT admins.
    assert body["groups"] == []


def test_cookie_token_authenticates_as_owner(client: TestClient) -> None:
    token = _mint(client, "carol")["token"]
    r = client.get("/api/me", cookies={"despereaux_token": token})
    assert r.status_code == 200
    assert r.json()["username"] == "carol"


def test_invalid_token_is_401_even_in_dev_mode(client: TestClient) -> None:
    # An explicitly presented bad token must fail loudly, not fall back to devuser.
    r = client.get("/api/me", headers={"Authorization": "Bearer desp_not-a-real-token"})
    assert r.status_code == 401
    r = client.get("/api/me", cookies={"despereaux_token": "desp_not-a-real-token"})
    assert r.status_code == 401


def test_revoked_token_stops_working(client: TestClient) -> None:
    minted = _mint(client, "dave")
    token = minted["token"]
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    r = client.delete(f"/api/admin/tokens/{minted['id']}")
    assert r.status_code == 204
    assert client.get("/api/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_revoke_unknown_token_404(client: TestClient) -> None:
    assert client.delete("/api/admin/tokens/no-such-id").status_code == 404


def test_authentik_headers_win_over_token(client: TestClient) -> None:
    token = _mint(client, "erin")["token"]
    r = client.get(
        "/api/me",
        headers={
            "X-authentik-username": "frank",
            "X-authentik-groups": "ebook-admin",
            "Authorization": f"Bearer {token}",
        },
    )
    assert r.status_code == 200
    assert r.json()["username"] == "frank"


def test_non_admin_token_cannot_manage_tokens(client: TestClient) -> None:
    token = _mint(client, "grace")["token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/admin/tokens", headers=auth).status_code == 403
    r = client.post("/api/admin/tokens", headers=auth, json={"username": "grace", "name": "sneaky"})
    assert r.status_code == 403


def test_user_can_hold_multiple_tokens(client: TestClient) -> None:
    t1 = _mint(client, "heidi", "phone")["token"]
    t2 = _mint(client, "heidi", "tablet")["token"]
    assert t1 != t2
    for tok in (t1, t2):
        r = client.get("/api/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200
        assert r.json()["username"] == "heidi"


def test_blank_fields_rejected(client: TestClient) -> None:
    r = client.post("/api/admin/tokens", json={"username": "   ", "name": "x"})
    assert r.status_code == 422


def test_token_works_on_real_api_routes(client: TestClient) -> None:
    token = _mint(client, "ivan")["token"]
    r = client.get("/api/books", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    r = client.get("/api/progress", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
