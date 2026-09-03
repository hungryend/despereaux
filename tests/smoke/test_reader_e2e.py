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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

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


READER_BAR_H = 44  # --reader-bar-h in reader.css


# Sub-pixel slack: the zone width is a clamp() of a vw unit, so the zone edge
# and the text column's edge land on fractional pixels that can cross by ~0.2px.
# That is invisible and untappable — only a real overlap should fail a test.
OVERLAP_TOL = 1.0


def _overlaps(a: dict, b: dict, tol: float = OVERLAP_TOL) -> bool:
    return (
        a["x"] + tol < b["x"] + b["width"]
        and b["x"] + tol < a["x"] + a["width"]
        and a["y"] + tol < b["y"] + b["height"]
        and b["y"] + tol < a["y"] + a["height"]
    )


def _boxes(page) -> tuple[dict, dict, dict]:
    content = page.locator("#reader-root iframe").bounding_box()
    prev_btn = page.locator("#reader-prev").bounding_box()
    next_btn = page.locator("#reader-next").bounding_box()
    assert content and prev_btn and next_btn
    return content, prev_btn, next_btn


def _assert_full_height_zone(btn: dict, viewport_h: int, name: str) -> None:
    """The zone runs the whole side: from just under the fixed reader bar down
    to the bottom of the viewport. A short corner button (what this replaced) or
    one pushed off-screen both fail here."""
    assert btn["y"] <= READER_BAR_H + 1, f"{name} starts below the reader bar: {btn}"
    assert btn["y"] + btn["height"] >= viewport_h - 1, f"{name} stops short of the bottom: {btn}"


def _assert_at_edge(btn: dict, viewport_w: int, side: str, name: str) -> None:
    """Flush against its own screen edge — the whole point of the zone is that
    the thumb finds it without looking."""
    if side == "left":
        assert btn["x"] <= 1, f"{name} not flush left: {btn}"
    else:
        right = btn["x"] + btn["width"]
        assert right >= viewport_w - 1, f"{name} not flush right: {btn} (viewport {viewport_w})"


def test_epub_text_clear_of_nav_zones_on_narrow_viewport(reader_page, smoke) -> None:
    """The page-turn zones are full-height strips down both edges and the EPUB
    text column is inset between them: a tap anywhere down either side turns a
    page, and no word ever sits under a zone."""
    page, errors = reader_page
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")

    page.set_viewport_size({"width": 412, "height": 800})  # typical Android phone
    page.goto(f"{SMOKE_URL}/read/{book['id']}")
    page.wait_for_selector("#reader-root iframe", timeout=RENDER_TIMEOUT_MS)

    content, prev_btn, next_btn = _boxes(page)
    assert not _overlaps(prev_btn, content), f"prev zone overlaps text: {prev_btn} vs {content}"
    assert not _overlaps(next_btn, content), f"next zone overlaps text: {next_btn} vs {content}"
    _assert_full_height_zone(prev_btn, 800, "prev")
    _assert_full_height_zone(next_btn, 800, "next")
    _assert_at_edge(prev_btn, 412, "left", "prev")
    _assert_at_edge(next_btn, 412, "right", "next")
    # The text column still gets the bulk of a narrow screen, gutters aside.
    assert content["width"] > 412 * 0.5, f"text column squeezed too far: {content['width']}px"
    assert not errors, f"reader console errors: {errors}"


def test_nav_zones_are_full_side_edges_on_tablet(reader_page, smoke) -> None:
    """Same on a tablet-sized viewport, where the text column is capped at 900px
    and the zones sit far out at the edges. Also pins the removal of the old
    top/bottom strip toggle: with a zone down each side there is nothing to
    move, and the button is gone from the reader bar."""
    page, errors = reader_page
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")

    page.set_viewport_size({"width": 760, "height": 1000})
    page.goto(f"{SMOKE_URL}/read/{book['id']}")
    page.wait_for_selector("#reader-root iframe", timeout=RENDER_TIMEOUT_MS)

    content, prev_btn, next_btn = _boxes(page)
    _assert_full_height_zone(prev_btn, 1000, "prev")
    _assert_full_height_zone(next_btn, 1000, "next")
    _assert_at_edge(prev_btn, 760, "left", "prev")
    _assert_at_edge(next_btn, 760, "right", "next")
    assert not _overlaps(prev_btn, content), f"prev zone overlaps text: {prev_btn} vs {content}"
    assert not _overlaps(next_btn, content), f"next zone overlaps text: {next_btn} vs {content}"
    assert page.locator("#nav-pos-toggle").count() == 0, "the strip toggle should be gone"
    assert not errors, f"reader console errors: {errors}"


def test_jpeg2000_pdf_actually_paints(reader_page, smoke) -> None:
    """Regression: a scanned (JPEG 2000) page rendered as a blank WHITE page.

    PDF.js 5 decodes JPX in a WebAssembly OpenJPEG module it fetches from the
    `wasmUrl` API parameter. That parameter was never set, so the decoder never
    loaded — and PDF.js only WARNS ("OpenJPEG failed to initialize"), so the
    canvas came up blank, the error overlay stayed silent, and every existing
    check passed: sample.pdf is three BLANK vector pages, so "the reader painted
    nothing at all" was indistinguishable from success.

    This asserts real ink on the canvas, and that PDF.js logged no decode
    failure while producing it.
    """
    page, errors = reader_page
    warnings: list[str] = []
    page.on(
        "console",
        lambda m: warnings.append(m.text) if m.type in ("warning", "error") else None,
    )
    book = smoke.wait_for_book("Smoke JPX Sample", fmt="pdf")

    page.goto(f"{SMOKE_URL}/read/{book['id']}")
    page.wait_for_selector("#reader-root canvas", timeout=RENDER_TIMEOUT_MS)

    # Poll the pixels rather than sleeping: the canvas element exists before the
    # image finishes decoding, so a one-shot read would race the decoder.
    # OPAQUE and non-white. The alpha test matters: a canvas that has not been
    # painted yet is transparent black (0,0,0,0), which reads as "dark ink" and
    # makes this check pass against a reader that draws nothing at all.
    ink = """() => {
      const c = document.querySelector('#reader-root canvas');
      if (!c || !c.width) return false;
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      for (let i = 0; i < d.length; i += 4 * 37) {
        if (d[i + 3] < 200) continue;
        if (d[i] < 200 || d[i + 1] < 200 || d[i + 2] < 200) return true;
      }
      return false;
    }"""
    try:
        page.wait_for_function(ink, timeout=RENDER_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        pytest.fail(
            "JPEG 2000 page rendered blank — PDF.js decoded no image. "
            f"console: {warnings or '(silent, as in the original bug)'}"
        )

    decode_failures = [
        w for w in warnings if "OpenJPEG" in w or "Unable to decode image" in w or "wasmUrl" in w
    ]
    assert not decode_failures, f"PDF.js could not load its decoders: {decode_failures}"
    assert not errors, f"reader console errors on JPX open: {errors}"


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

    # Turn a page: re-renders onto the same canvas — the reuse pattern a
    # pdf.js major bump is most likely to break (and one render alone
    # wouldn't catch). Two subtleties learned the hard way:
    # - the canvas paints DURING reader.start() but the nav buttons bind just
    #   after, so wait for __despereaux_reader (assigned last in bootstrap);
    # - the reader RESTORES the saved reading position, so the start page is
    #   whatever an earlier run left behind — assert RELATIVE movement, and
    #   step backwards first if we happen to be parked on the last page.
    page.wait_for_function("() => !!window.__despereaux_reader", timeout=RENDER_TIMEOUT_MS)
    start = page.evaluate("window.__despereaux_reader.currentPage")
    num_pages = page.evaluate("window.__despereaux_reader.numPages")
    assert num_pages >= 2, f"fixture PDF should be multi-page, got {num_pages}"
    if start >= num_pages:
        page.click("#reader-prev")
        page.wait_for_function(
            f"() => window.__despereaux_reader.currentPage === {start - 1}",
            timeout=RENDER_TIMEOUT_MS,
        )
        start -= 1

    page.click("#reader-next")
    page.wait_for_function(
        f"() => window.__despereaux_reader.currentPage === {start + 1}",
        timeout=RENDER_TIMEOUT_MS,
    )
    page.click("#reader-prev")
    page.wait_for_function(
        f"() => window.__despereaux_reader.currentPage === {start}",
        timeout=RENDER_TIMEOUT_MS,
    )
    assert not errors, f"reader console errors on PDF open/page-turn: {errors}"
