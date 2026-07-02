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


READER_BAR_H = 44  # --reader-bar-h in reader.css


def _overlaps(a: dict, b: dict) -> bool:
    return (
        a["x"] < b["x"] + b["width"]
        and b["x"] < a["x"] + a["width"]
        and a["y"] < b["y"] + b["height"]
        and b["y"] < a["y"] + a["height"]
    )


def _boxes(page) -> tuple[dict, dict, dict]:
    content = page.locator("#reader-root iframe").bounding_box()
    prev_btn = page.locator("#reader-prev").bounding_box()
    next_btn = page.locator("#reader-next").bounding_box()
    assert content and prev_btn and next_btn
    return content, prev_btn, next_btn


def _assert_in_bottom_strip(btn: dict, content: dict, viewport_h: int, name: str) -> None:
    """Two-sided: below the text AND fully on-screen (an off-screen button is
    exactly the regression class a one-sided check would wave through)."""
    assert btn["y"] >= content["y"] + content["height"] - 1, f"{name} not below text: {btn}"
    assert btn["y"] + btn["height"] <= viewport_h + 1, f"{name} pushed off-screen: {btn}"


def _assert_in_top_strip(btn: dict, content: dict, name: str) -> None:
    """Two-sided: above the text AND below the fixed reader bar (not under it,
    not off the top of the screen)."""
    assert btn["y"] + btn["height"] <= content["y"] + 1, f"{name} not above text: {btn}"
    assert btn["y"] >= READER_BAR_H - 1, f"{name} under the reader bar / off-screen: {btn}"


def test_epub_text_clear_of_nav_buttons_on_narrow_viewport(reader_page, smoke) -> None:
    """Regression: the fixed prev/next buttons used to float over the EPUB text
    column's edges. They now live in a reserved strip (bottom by default), so
    the rendered iframe must keep the FULL viewport width and never intersect
    either button."""
    page, errors = reader_page
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")

    page.set_viewport_size({"width": 412, "height": 800})  # typical Android phone
    page.goto(f"{SMOKE_URL}/read/{book['id']}")
    page.wait_for_selector("#reader-root iframe", timeout=RENDER_TIMEOUT_MS)

    content, prev_btn, next_btn = _boxes(page)
    assert not _overlaps(prev_btn, content), f"prev button overlaps text: {prev_btn} vs {content}"
    assert not _overlaps(next_btn, content), f"next button overlaps text: {next_btn} vs {content}"
    _assert_in_bottom_strip(prev_btn, content, 800, "prev")
    _assert_in_bottom_strip(next_btn, content, 800, "next")
    # No side gutters: the text column keeps essentially the whole viewport width.
    assert content["width"] > 400, f"text column narrower than expected: {content['width']}px"
    assert not errors, f"reader console errors: {errors}"


def test_nav_position_toggle_moves_buttons_and_persists(reader_page, smoke) -> None:
    """The ⇅ toggle swaps the button strip between bottom (default) and top,
    the text reflows to the remaining space, and the choice survives a reload
    (localStorage)."""
    page, errors = reader_page
    book = smoke.wait_for_book("Despereaux Sample", fmt="epub")

    page.set_viewport_size({"width": 760, "height": 1000})  # tablet-ish
    page.goto(f"{SMOKE_URL}/read/{book['id']}")
    page.wait_for_selector("#reader-root iframe", timeout=RENDER_TIMEOUT_MS)

    # Default: both buttons in the bottom strip, on-screen.
    content, prev_btn, next_btn = _boxes(page)
    _assert_in_bottom_strip(prev_btn, content, 1000, "prev")
    _assert_in_bottom_strip(next_btn, content, 1000, "next")

    def _wait_buttons_above_text() -> None:
        page.wait_for_function(
            """() => {
              const f = document.querySelector('#reader-root iframe');
              const p = document.querySelector('#reader-prev');
              const n = document.querySelector('#reader-next');
              if (!f || !p || !n) return false;
              const fr = f.getBoundingClientRect();
              return p.getBoundingClientRect().bottom <= fr.top + 1
                  && n.getBoundingClientRect().bottom <= fr.top + 1;
            }""",
            timeout=RENDER_TIMEOUT_MS,
        )

    def _wait_buttons_below_text() -> None:
        page.wait_for_function(
            """() => {
              const f = document.querySelector('#reader-root iframe');
              const p = document.querySelector('#reader-prev');
              const n = document.querySelector('#reader-next');
              if (!f || !p || !n) return false;
              const fr = f.getBoundingClientRect();
              return p.getBoundingClientRect().top >= fr.bottom - 1
                  && n.getBoundingClientRect().top >= fr.bottom - 1;
            }""",
            timeout=RENDER_TIMEOUT_MS,
        )

    # Toggle to top; both buttons must land in the top strip band.
    page.click("#nav-pos-toggle")
    _wait_buttons_above_text()
    content, prev_btn, next_btn = _boxes(page)
    _assert_in_top_strip(prev_btn, content, "prev")
    _assert_in_top_strip(next_btn, content, "next")

    # Reload: 'top' must come back from localStorage.
    page.reload()
    page.wait_for_selector("#reader-root iframe", timeout=RENDER_TIMEOUT_MS)
    content, prev_btn, next_btn = _boxes(page)
    _assert_in_top_strip(prev_btn, content, "prev (after reload)")
    _assert_in_top_strip(next_btn, content, "next (after reload)")

    # Round trip: toggle back to bottom (exercises the storage write for
    # 'bottom', not just the pre-paint default) and reload again.
    page.click("#nav-pos-toggle")
    _wait_buttons_below_text()
    page.reload()
    page.wait_for_selector("#reader-root iframe", timeout=RENDER_TIMEOUT_MS)
    content, prev_btn, next_btn = _boxes(page)
    _assert_in_bottom_strip(prev_btn, content, 1000, "prev (round trip)")
    _assert_in_bottom_strip(next_btn, content, 1000, "next (round trip)")
    assert not errors, f"reader console errors: {errors}"


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
