"""Black-box regression checks against a running despereaux instance.

Read-only checks run against any target; RW/CONVERT-gated checks are meant for
the throwaway CI container (docker-compose.ci.yml). See conftest.py for the
env contract.
"""

from __future__ import annotations

import time

import httpx

from tests.smoke.conftest import (
    SMOKE_URL,
    requires_convert,
    requires_rw,
    requires_smoke,
    requires_tomeforge,
)

pytestmark = requires_smoke


def test_healthz_ok() -> None:
    r = httpx.get(f"{SMOKE_URL}/healthz", timeout=30.0)
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_api_requires_auth() -> None:
    """Proves the target is NOT running with dev-mode auto-auth."""
    r = httpx.get(f"{SMOKE_URL}/api/books", timeout=30.0)
    assert r.status_code == 401


def test_me_identity(smoke) -> None:
    body = smoke.get("/api/me").json()
    assert body["username"]
    assert "is_admin" in body


def test_sample_book_ingested_with_detail(smoke) -> None:
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")
    detail = smoke.get(f"/api/books/{book['id']}").json()
    assert detail["title"] == "Despereaux Sample"
    assert detail["authors"] == ["A. Mouse"]
    assert detail["page_count"] and detail["page_count"] >= 1
    assert detail["file_size"] > 0


def test_cover_served_as_webp(smoke) -> None:
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")
    r = smoke.get(f"/api/books/{book['id']}/cover")
    # The generated sample EPUB has no embedded cover unless enrichment found
    # one; both outcomes are valid — what must not happen is a 5xx.
    assert r.status_code in (200, 404)
    if r.status_code == 200:
        assert r.headers["content-type"] == "image/webp"
        assert r.content[:4] == b"RIFF"


def test_file_etag_304_and_range(smoke) -> None:
    """The reader's transport contract over a real socket: ETag/304 + Range/206."""
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")
    url = f"/api/books/{book['id']}/file"

    full = smoke.get(url)
    assert full.status_code == 200
    assert full.headers["content-type"] == "application/epub+zip"
    assert full.headers["cache-control"] == "public, max-age=31536000, immutable"
    etag = full.headers["etag"]

    assert smoke.get(url, headers={"if-none-match": etag}).status_code == 304

    part = smoke.get(url, headers={"range": "bytes=0-99"})
    assert part.status_code == 206
    assert len(part.content) == 100
    assert part.content[:2] == b"PK"  # EPUB = zip container

    tail = smoke.get(url, headers={"range": "bytes=-50"})
    assert tail.status_code == 206
    assert len(tail.content) == 50


def test_reader_static_assets(smoke) -> None:
    """The Vite bundle landed in the image and serves immutable — including the
    PDF.js worker (the asset a pdfjs-dist major bump is most likely to break)."""
    js = smoke.get("/static/reader/assets/reader.js")
    assert js.status_code == 200
    assert js.headers["cache-control"] == "public, max-age=31536000, immutable"

    css = smoke.get("/static/reader/assets/reader.css")
    assert css.status_code == 200

    worker = smoke.get("/static/reader/assets/pdf.worker.min.mjs")
    assert worker.status_code == 200
    assert len(worker.content) > 100_000  # a real worker bundle, not an error page


def test_read_page_with_token_cookie(smoke) -> None:
    """/read/{id} via the despereaux_token cookie — the Furlough WebView path."""
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")
    r = httpx.get(
        f"{SMOKE_URL}/read/{book['id']}",
        cookies={"despereaux_token": smoke.token},
        timeout=30.0,
    )
    assert r.status_code == 200
    assert "window.DESPEREAUX_BOOK" in r.text
    assert "reader-root" in r.text


@requires_rw
def test_progress_roundtrip(smoke) -> None:
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")
    url = f"/api/books/{book['id']}/progress"
    r = smoke.client.put(
        url,
        headers=smoke.auth,
        json={"position": "epubcfi(/6/2!/4/2)", "percent": 0.25},
    )
    assert r.status_code == 204
    body = smoke.get(url).json()
    assert body["position"] == "epubcfi(/6/2!/4/2)"
    assert body["percent"] == 0.25
    listing = smoke.get("/api/progress").json()
    assert any(row["book_id"] == book["id"] for row in listing)


@requires_rw
def test_admin_scan_trigger_and_status(smoke) -> None:
    r = smoke.client.post("/api/admin/scan", headers=smoke.auth)
    assert r.status_code == 200
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status = smoke.get("/api/admin/scan/status").json()
        if status.get("last_result"):
            return
        time.sleep(2)
    raise AssertionError("scan status never reported a last_result")


@requires_rw
@requires_convert
def test_mobi_auto_converts_via_calibre(smoke) -> None:
    """The CI workflow drops a sample.mobi into /ebooks (generated in-container
    by ebook-convert). The watcher must ingest it, Calibre must convert it, and
    /file must serve the converted EPUB — proving the Calibre toolchain works
    on the current base image."""
    book = smoke.wait_for_book("Despereaux Sample", fmt="mobi", timeout=180.0)
    manifest = smoke.get(f"/api/books/{book['id']}/manifest").json()
    assert manifest["served_format"] == "epub"
    assert manifest["original_format"] == "mobi"

    r = smoke.get(f"/api/books/{book['id']}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/epub+zip"
    assert r.content[:2] == b"PK"

    # Download still hands out the original MOBI.
    dl = smoke.get(f"/api/books/{book['id']}/download")
    assert dl.status_code == 200
    assert "attachment" in dl.headers.get("content-disposition", "")


def test_pdf_ingested_with_page_count(smoke) -> None:
    """The seeded sample.pdf exercises in-container pypdf + pypdfium2."""
    book = smoke.wait_for_book("Smoke PDF Sample", fmt="pdf")
    detail = smoke.get(f"/api/books/{book['id']}").json()
    assert detail["page_count"] == 3
    cover = smoke.get(f"/api/books/{book['id']}/cover")
    assert cover.status_code == 200, "PDF cover render failed in-container"
    assert cover.headers["content-type"] == "image/webp"


@requires_rw
@requires_tomeforge
def test_pdf_convert_via_tomeforge_sidecar(smoke) -> None:
    """Optional: full PDF→EPUB through a live tomeforge sidecar. Local-only."""
    book = smoke.wait_for_book("Smoke PDF Sample", fmt="pdf")
    r = smoke.client.post(f"/api/books/{book['id']}/convert", headers=smoke.auth)
    assert r.status_code == 200, r.text

    deadline = time.monotonic() + 900
    status = {}
    while time.monotonic() < deadline:
        status = smoke.get(f"/api/books/{book['id']}/convert/status").json()
        if status.get("status") in ("done", "failed"):
            break
        time.sleep(5)
    assert status.get("status") == "done", f"conversion did not finish cleanly: {status}"

    manifest = smoke.get(f"/api/books/{book['id']}/manifest").json()
    assert manifest["has_epub_export"] is True


@requires_rw
def test_invalid_token_is_hard_401(smoke) -> None:
    r = httpx.get(
        f"{SMOKE_URL}/api/books",
        headers={"authorization": "Bearer definitely-not-a-real-token"},
        timeout=30.0,
    )
    assert r.status_code == 401
