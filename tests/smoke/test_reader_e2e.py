"""Browser-level reader checks (Playwright + headless Chromium).

The backend tiers can prove /file, /manifest and the static bundle serve
correctly — but only a real browser can prove epub.js actually paginates and
PDF.js actually rasterises a page. This is the net that catches a pdfjs-dist
or epub.js regression after a dependency bump.

Gated by DESPEREAUX_SMOKE_URL + DESPEREAUX_SMOKE_BROWSER=1. Requires browsers:
    uv run playwright install chromium
"""

from __future__ import annotations

import pytest

from tests.smoke.conftest import SMOKE_URL, requires_browser, requires_smoke

pytestmark = [requires_smoke, requires_browser]

RENDER_TIMEOUT_MS = 30_000


@pytest.fixture
def reader_page(context, smoke):
    """A browser page authenticated via the despereaux_token cookie — the same
    mechanism the Furlough Android WebView uses."""
    context.add_cookies([{"name": "despereaux_token", "value": smoke.token, "url": SMOKE_URL}])
    page = context.new_page()
    errors: list[str] = []
    page.on(
        "console",
        lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    yield page, errors
    page.close()


def test_epub_renders_in_reader(reader_page, smoke) -> None:
    page, errors = reader_page
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")

    page.goto(f"{SMOKE_URL}/read/{book['id']}")
    # epub.js renders the book into an iframe inside #reader-root.
    frame = page.wait_for_selector("#reader-root iframe", timeout=RENDER_TIMEOUT_MS)
    assert frame is not None
    # The chapter body actually rendered (not just an empty frame).
    page.wait_for_function(
        """() => {
          const f = document.querySelector('#reader-root iframe');
          return f && f.contentDocument
              && f.contentDocument.body
              && f.contentDocument.body.innerText.trim().length > 0;
        }""",
        timeout=RENDER_TIMEOUT_MS,
    )
    assert not errors, f"reader console errors on EPUB open: {errors}"


def test_pdf_renders_in_reader(reader_page, smoke) -> None:
    page, errors = reader_page
    book = smoke.wait_for_book("Smoke PDF Sample", fmt="pdf")

    page.goto(f"{SMOKE_URL}/read/{book['id']}")
    # PDF.js rasterises pages onto <canvas> elements inside #reader-root.
    # No canvas appearing usually means the worker failed to load — exactly
    # the failure mode of a botched pdfjs-dist upgrade.
    canvas = page.wait_for_selector("#reader-root canvas", timeout=RENDER_TIMEOUT_MS)
    assert canvas is not None
    box = canvas.bounding_box()
    assert box and box["width"] > 50 and box["height"] > 50, f"canvas too small: {box}"
    assert not errors, f"reader console errors on PDF open: {errors}"
