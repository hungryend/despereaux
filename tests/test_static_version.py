"""Static assets must be cache-busted (?v=<hash>) so a deploy doesn't leave
browsers on a stale stylesheet. Regression guard for the On-deck shelf rendering
unstyled ("super large" covers, mislaid progress bar) until a hard refresh,
because base.html linked app.css with no version + no Cache-Control.
"""

from __future__ import annotations

from httpx import ASGITransport, AsyncClient

from despereaux.main import app
from despereaux.web.routes import static_version


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def test_static_version_hashes_existing_file() -> None:
    v = static_version("css/app.css")
    assert v and v != "dev"
    assert static_version("css/app.css") == v  # stable across calls (memoized)


def test_static_version_missing_file_is_safe() -> None:
    assert static_version("css/does-not-exist.css") == "dev"


async def test_library_page_cache_busts_app_css() -> None:
    async with _client() as client:
        r = await client.get("/")
        assert r.status_code == 200
        assert "/static/css/app.css?v=" in r.text
        # ...and the token is non-empty (not a bare `?v=""`).
        assert '/static/css/app.css?v=""' not in r.text


async def test_static_mount_serves_versioned_url() -> None:
    """StaticFiles routes on path only, so the ?v= query is ignored and the file
    (with the on-deck rules) is served."""
    async with _client() as client:
        r = await client.get("/static/css/app.css?v=abc123")
        assert r.status_code == 200
        assert "deck-" in r.text
