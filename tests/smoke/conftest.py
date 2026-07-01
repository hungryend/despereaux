"""Black-box smoke tier: drives a RUNNING despereaux over real HTTP.

Skipped entirely unless DESPEREAUX_SMOKE_URL is set, so `uv run pytest` stays
self-contained by default. Two target profiles:

  CI / local container (full):
    DESPEREAUX_SMOKE_URL=http://localhost:8810
    DESPEREAUX_SMOKE_RW=1          # mutating checks (progress, admin scan)
    DESPEREAUX_SMOKE_CONVERT=1     # in-container Calibre MOBI→EPUB check
    DESPEREAUX_SMOKE_BROWSER=1     # Playwright reader render checks
    (no token: bootstraps the native-mode first-run admin itself)

  Production probe (read-only):
    DESPEREAUX_SMOKE_URL=<https base url>
    DESPEREAUX_SMOKE_TOKEN=<existing API token>
    (RW unset → GET-tier checks only; safe against live data)

  DESPEREAUX_SMOKE_TOMEFORGE=1 additionally exercises a PDF convert through a
  live tomeforge sidecar — local-only, not wired into CI.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

SMOKE_URL = os.environ.get("DESPEREAUX_SMOKE_URL", "").rstrip("/")
SMOKE_TOKEN = os.environ.get("DESPEREAUX_SMOKE_TOKEN", "")
RW = os.environ.get("DESPEREAUX_SMOKE_RW") == "1"
CONVERT = os.environ.get("DESPEREAUX_SMOKE_CONVERT") == "1"
TOMEFORGE = os.environ.get("DESPEREAUX_SMOKE_TOMEFORGE") == "1"
BROWSER = os.environ.get("DESPEREAUX_SMOKE_BROWSER") == "1"

# Throwaway credentials for the CI container's native-mode first-run setup.
# Never used against a real deployment (those pass DESPEREAUX_SMOKE_TOKEN).
ADMIN_USERNAME = "smoke-admin"
ADMIN_PASSWORD = "smoke-ci-only-pw-1"

HEALTH_TIMEOUT = 90.0

requires_smoke = pytest.mark.skipif(
    not SMOKE_URL, reason="smoke tier disabled (DESPEREAUX_SMOKE_URL not set)"
)
requires_rw = pytest.mark.skipif(
    not RW, reason="mutating smoke checks disabled (DESPEREAUX_SMOKE_RW != 1)"
)
requires_convert = pytest.mark.skipif(
    not CONVERT, reason="Calibre convert check disabled (DESPEREAUX_SMOKE_CONVERT != 1)"
)
requires_tomeforge = pytest.mark.skipif(
    not TOMEFORGE, reason="tomeforge check disabled (DESPEREAUX_SMOKE_TOMEFORGE != 1)"
)
requires_browser = pytest.mark.skipif(
    not BROWSER, reason="browser checks disabled (DESPEREAUX_SMOKE_BROWSER != 1)"
)


class Smoke:
    """Session-wide handle: base client, bearer token, polling helpers."""

    def __init__(self, client: httpx.Client, token: str):
        self.client = client
        self.token = token

    @property
    def auth(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}

    def get(self, path: str, **kwargs) -> httpx.Response:
        headers = {**self.auth, **kwargs.pop("headers", {})}
        return self.client.get(path, headers=headers, **kwargs)

    def wait_for_book(self, search: str, *, fmt: str | None = None, timeout: float = 90.0) -> dict:
        """Poll /api/books until the scanner has ingested a matching book."""
        deadline = time.monotonic() + timeout
        last: list = []
        while time.monotonic() < deadline:
            r = self.get("/api/books", params={"search": search})
            if r.status_code == 200:
                last = r.json()
                for book in last:
                    if fmt is None or book["format"] == fmt:
                        return book
            time.sleep(2)
        raise AssertionError(
            f"book matching search={search!r} fmt={fmt!r} never appeared; last result: {last}"
        )


def _wait_for_health(client: httpx.Client) -> None:
    deadline = time.monotonic() + HEALTH_TIMEOUT
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if client.get("/healthz").status_code == 200:
                return
        except httpx.HTTPError as e:
            last_error = e
        time.sleep(2)
    raise AssertionError(f"{SMOKE_URL}/healthz never became healthy: {last_error}")


def _bootstrap_native_token(base_url: str) -> str:
    """First-run /setup (or /login on re-runs) → session cookie → default API key.

    Exercises the real in-container bcrypt + session-signing + token stack.
    """
    with httpx.Client(base_url=base_url, timeout=30.0, follow_redirects=False) as c:
        r = c.post("/setup", data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
        setup_ok = r.status_code == 303 and r.headers.get("location") == "/"
        if not setup_ok:
            # Admin already exists (container restarted with a kept volume) — log in.
            r = c.post("/login", data={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
            assert r.status_code == 303, f"native login failed: {r.status_code} {r.text[:200]}"
        r = c.get("/api/tokens/default")
        assert r.status_code == 200, f"default-token reveal failed: {r.status_code}"
        token = r.json()["token"]
        assert token
        return token


@pytest.fixture(scope="session")
def smoke() -> Smoke:
    client = httpx.Client(base_url=SMOKE_URL, timeout=30.0, follow_redirects=False)
    _wait_for_health(client)
    if SMOKE_TOKEN:
        token = SMOKE_TOKEN
    elif RW:
        token = _bootstrap_native_token(SMOKE_URL)
    else:
        pytest.skip("no DESPEREAUX_SMOKE_TOKEN and RW bootstrap disabled")
    s = Smoke(client, token)
    r = s.get("/api/me")
    assert r.status_code == 200, f"token rejected by target: {r.status_code}"
    yield s
    client.close()
