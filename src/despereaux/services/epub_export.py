"""On-demand "Convert to EPUB" with an auto-generated, linked Table of Contents.

Pipeline (per book, run as a FastAPI BackgroundTask):

  PDF  -> offloaded to the tomeforge conversion sidecar (DESPEREAUX_TOMEFORGE_HOST):
          it does PDF -> Markdown (PyMuPDF) -> EPUB (Calibre), plus scanned-PDF OCR
          via Ollama. PDF conversion is ONLY offered when the sidecar is configured
          (see ``can_convert`` / ``config.tomeforge_available``); there is no
          in-process PDF engine and no PyMuPDF dependency in this image.
  MOBI / AZW / AZW3 -> Calibre converts directly, in-process.

After conversion a linked nav/NCX is GUARANTEED with ebooklib: if Calibre's TOC is
thin we rebuild it from the ``<h1..3>`` in the converted XHTML. That step is
STRICTLY ADDITIVE — it only injects ``id`` anchors and rewrites the nav, so body
text and inline images are preserved. The result is validated (opens as an EPUB)
before the job is marked done.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import warnings
from pathlib import Path

import ebooklib
import lxml.html
from ebooklib import epub
from fastapi.concurrency import run_in_threadpool

from despereaux.config import get_settings, tomeforge_available
from despereaux.db import session_scope
from despereaux.models import ConversionStatus
from despereaux.repos import books as books_repo
from despereaux.repos import conversions as conversions_repo
from despereaux.services import tomeforge_client
from despereaux.services.converter import calibre_available, convert_to_epub

log = logging.getLogger(__name__)

# ebooklib is noisy with namespace warnings we can't fix.
warnings.filterwarnings("ignore", category=UserWarning, module="ebooklib.epub")
warnings.filterwarnings("ignore", category=FutureWarning, module="ebooklib.epub")

# Big rulebook PDFs run long; give exports more headroom than the MOBI ingest path.
_EXPORT_TIMEOUT = 900

# Calibre flags. Heuristics + the level*-toc XPaths build a nav TOC from any
# <h1>/<h2>/<h3> in the intermediate XHTML (the `h:` prefix is calibre's XHTML
# namespace). No image-suppressing flags — embedded figures stay inline.
_COMMON_TOC_FLAGS = [
    "--enable-heuristics",
    "--toc-threshold", "6",
    "--max-toc-links", "50",
    "--duplicate-links-in-toc",
    "--level1-toc", "//h:h1",
    "--level2-toc", "//h:h2",
    "--level3-toc", "//h:h3",
]
_MOBI_FLAGS = list(_COMMON_TOC_FLAGS)

_CONVERTIBLE = {"pdf", "mobi", "azw", "azw3"}

# Cap rebuilt-nav entries so a large-print doc can't produce a runaway TOC.
_MAX_HEADINGS = 200

# A PDF conversion that yields images but almost no extractable text is a
# scanned/image-PDF dump: worse than the original PDF (no reflow, no TOC) and it
# breaks the reflowable reader. Below this many characters (with images present)
# we skip publishing the EPUB and keep the user on the PDF.
_MIN_EPUB_TEXT = 500

_SCANNED_MSG = (
    "This looks like a scanned/image PDF — an EPUB would only be page images with "
    "no reflowable text or table of contents, so it wasn't created. "
    "Read the original PDF instead."
)

# Serialize sidecar conversions so a single Ollama behind it isn't thrashed by
# parallel OCR jobs.
_OCR_SEMAPHORE = asyncio.Semaphore(1)


def can_convert(fmt: str) -> bool:
    """Whether the on-demand Convert-to-EPUB option is available for ``fmt``.

    Gated on the tomeforge sidecar: PDF→EPUB runs there, so we only surface the
    Convert option (for ANY format) when a sidecar is configured — without one the
    button is hidden and the convert endpoints reject. MOBI/AZW still convert via
    local Calibre once the option is shown."""
    return tomeforge_available() and fmt in _CONVERTIBLE


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


async def _patch(conversion_id: str, **fields) -> None:
    async with session_scope() as session:
        await conversions_repo.set_status(session, conversion_id, **fields)


async def _convert_pdf_via_sidecar(
    conversion_id: str, src: Path, out: Path
) -> tomeforge_client.SidecarResult | None:
    """Offload PDF→Markdown→EPUB to the tomeforge sidecar, forwarding its phase
    (incl. ``OCR page N/M``) onto the conversion row. Returns the sidecar result, or
    None after recording a failed status. The sidecar reaches Ollama itself for
    scans, using despereaux's DESPEREAUX_OLLAMA_* settings passed per request."""
    settings = get_settings()

    async def on_phase(phase: str) -> None:
        await _patch(conversion_id, phase=phase)

    await _patch(conversion_id, phase="Converting…")
    try:
        # One conversion at a time: a single Ollama behind the sidecar would thrash
        # under parallel OCR (matches the in-process OCR guarantee).
        async with _OCR_SEMAPHORE:
            result = await tomeforge_client.convert_pdf(
                settings.tomeforge_host or "",
                src,
                out,
                overall_timeout=settings.tomeforge_timeout,
                ocr="auto",
                ollama_host=settings.ollama_host,
                model=settings.ollama_model,
                dpi=settings.ollama_dpi,
                ocr_timeout=settings.ollama_ocr_timeout,
                num_ctx=settings.ollama_num_ctx,
                on_phase=on_phase,
            )
    except tomeforge_client.SidecarError as e:
        await _patch(conversion_id, status=ConversionStatus.failed, error=str(e)[:2000], phase=None)
        return None
    if result.engine in ("ocr", "heuristic"):
        await _patch(conversion_id, engine=result.engine)
    return result


async def run_export(conversion_id: str, *, force: bool = False) -> None:
    """Execute one conversion job, recording progress + result on its row.

    Never raises — any failure is captured as status=failed with the message, so
    the background task can't die silently and the UI always reflects reality.
    """
    settings = get_settings()

    async with session_scope() as session:
        conv = await conversions_repo.get(session, conversion_id)
        if conv is None:
            return
        book = await books_repo.get_book(session, conv.book_id)
        if book is None:
            await conversions_repo.set_status(
                session, conversion_id, status=ConversionStatus.failed,
                error="book not found", phase=None,
            )
            return
        book_id, fmt, file_hash = book.id, book.format, book.file_hash
        src = Path(book.file_path)

    if not can_convert(fmt):
        await _patch(conversion_id, status=ConversionStatus.failed,
                     error=f"cannot convert format: {fmt}", phase=None)
        return
    if not calibre_available():
        await _patch(conversion_id, status=ConversionStatus.failed,
                     error="calibre (ebook-convert) is not installed", phase=None)
        return
    if not src.exists():
        await _patch(conversion_id, status=ConversionStatus.failed,
                     error="source file missing on disk", phase=None)
        return

    out = settings.exports_dir / f"{file_hash}.epub"

    try:
        # Reuse a cached export ONLY when a prior conversion genuinely completed
        # for this exact content. A bare leftover file isn't trusted — it could be
        # a partial from an interrupted run.
        if not force:
            async with session_scope() as session:
                prior = await conversions_repo.get_done_for_hash(session, book_id, file_hash)
            if prior and out.exists() and out.stat().st_size > 0:
                await _finish(conversion_id, book_id, out, source="cached")
                return

        await _patch(conversion_id, status=ConversionStatus.running,
                     phase="Converting…", error=None)

        # PDF: offloaded to the tomeforge sidecar (PDF is only convertible when a
        # sidecar is configured — see can_convert). It returns a plain EPUB;
        # despereaux still runs its TOC guarantee + scanned backstop below.
        # `use_ocr` tracks the sidecar's engine so the backstop doesn't re-skip a
        # genuinely OCR'd book. MOBI/AZW: local Calibre.
        use_ocr = False
        if fmt == "pdf":
            sresult = await _convert_pdf_via_sidecar(conversion_id, src, out)
            if sresult is None:
                return
            use_ocr = sresult.engine == "ocr"
            result = out
        else:  # mobi / azw / azw3 — Calibre directly
            result = await convert_to_epub(
                src, out, extra_args=_MOBI_FLAGS, overwrite=True, timeout=_EXPORT_TIMEOUT
            )

        if result is None:
            await _patch(conversion_id, status=ConversionStatus.failed,
                         error="conversion failed (see server logs)", phase=None)
            return

        await _patch(conversion_id, phase="Building contents…")
        toc_count, toc_source = await run_in_threadpool(_ensure_linked_toc, out)

        # Only declare success once the finished file is on disk AND opens as a
        # valid EPUB — so "done" / "Read EPUB" never appears on a partial file.
        ok = (
            out.exists()
            and out.stat().st_size > 0
            and await run_in_threadpool(_is_valid_epub, out)
        )
        if not ok:
            await _patch(conversion_id, status=ConversionStatus.failed,
                         error="conversion finished but the EPUB was not readable", phase=None)
            return

        image_count = await run_in_threadpool(_count_images, out)

        # Scanned/image PDF backstop: if the sidecar's heuristic result is basically
        # page-images with no real text, don't publish a useless EPUB that also breaks
        # the reflowable reader — keep the user on the original PDF. Skipped when the
        # sidecar OCR'd it (OCR output is real text, never re-skip it).
        text_len = await run_in_threadpool(_extract_text_len, out)
        if not use_ocr and _looks_like_scanned_pdf(fmt, text_len, image_count):
            out.unlink(missing_ok=True)
            await _patch(conversion_id, status=ConversionStatus.failed,
                         error=_SCANNED_MSG, phase=None)
            log.info("skipped scanned/image PDF export for conversion %s", conversion_id)
            return

        await _finish(
            conversion_id, book_id, out,
            source=toc_source, toc_count=toc_count, image_count=image_count,
        )
    except Exception as e:
        # Record ANY failure on the row so the background task never dies silent.
        log.exception("epub export failed for conversion %s: %s", conversion_id, e)
        await _patch(conversion_id, status=ConversionStatus.failed,
                     error=str(e)[:2000], phase=None)


async def _finish(
    conversion_id: str,
    book_id: str,
    out: Path,
    *,
    source: str,
    toc_count: int | None = None,
    image_count: int | None = None,
) -> None:
    """Mark a job done, cache the path, and promote the EPUB to the book's primary."""
    if toc_count is None:
        toc_count = await run_in_threadpool(_count_toc, out)
    if image_count is None:
        image_count = await run_in_threadpool(_count_images, out)
    async with session_scope() as session:
        await conversions_repo.set_status(
            session, conversion_id,
            status=ConversionStatus.done, phase=None, error=None,
            output_path=str(out), toc_count=toc_count, toc_source=source,
            image_count=image_count,
        )
        book = await books_repo.get_book(session, book_id)
        if book is not None:
            book.epub_export_path = str(out)


# --------------------------------------------------------------------------- #
# EPUB inspection / TOC guarantee (ebooklib + lxml, run in a threadpool)
# --------------------------------------------------------------------------- #


def _count_links(toc) -> int:
    """Count navigable links in an ebooklib `book.toc` (Sections aren't links)."""
    items = toc if isinstance(toc, (list, tuple)) else [toc]
    count = 0
    for it in items:
        if isinstance(it, epub.Link):
            count += 1
        elif isinstance(it, (list, tuple)):
            count += _count_links(it)
        # epub.Section is a non-clickable header — skip.
    return count


def _count_toc(path: Path) -> int:
    try:
        book = epub.read_epub(str(path))
    except Exception:
        return 0
    return _count_links(book.toc)


def _count_images(path: Path) -> int:
    try:
        book = epub.read_epub(str(path))
    except Exception:
        return 0
    return sum(1 for _ in book.get_items_of_type(ebooklib.ITEM_IMAGE))


def _is_valid_epub(path: Path) -> bool:
    """True if `path` opens as an EPUB with at least one content document.
    Used to confirm a conversion fully finished before it's marked done."""
    try:
        book = epub.read_epub(str(path))
    except Exception:
        return False
    return any(True for _ in book.get_items_of_type(ebooklib.ITEM_DOCUMENT))


def _extract_text_len(path: Path) -> int:
    """Total visible text characters across the EPUB's content docs (nav excluded)."""
    try:
        book = epub.read_epub(str(path))
    except Exception:
        return 0
    total = 0
    for item in _content_docs(book):
        try:
            tree = lxml.html.fromstring(item.get_content())
            total += len(" ".join(tree.text_content().split()))
        except Exception:
            continue
    return total


def _looks_like_scanned_pdf(fmt: str, text_len: int, image_count: int) -> bool:
    """A PDF conversion that produced images but almost no text is a scan dump."""
    return fmt == "pdf" and image_count > 0 and text_len < _MIN_EPUB_TEXT


def _ensure_linked_toc(out: Path) -> tuple[int, str]:
    """Return (toc_count, toc_source). Rebuild the nav from the converted XHTML's
    headings only if Calibre's own TOC is thin. Any failure leaves the valid
    Calibre EPUB intact."""
    try:
        book = epub.read_epub(str(out))
    except Exception as e:
        log.warning("could not open converted EPUB %s for TOC check: %s", out.name, e)
        return (0, "none")

    existing = _count_links(book.toc)
    if existing >= 2:
        return (existing, "calibre")

    # Rebuild from real headings in the converted XHTML (both the sidecar's
    # Markdown pipeline and MOBI yield proper <h1..3>).
    headings = _collect_xhtml_headings(book)[:_MAX_HEADINGS]
    if not headings:
        return (existing, "none")

    tmp = out.with_name(out.stem + ".rebuild.epub")
    try:
        book.toc = [epub.Link(href, title, f"dx_toc_{i}") for i, (_lvl, title, href) in enumerate(headings)]
        _reset_nav_items(book)
        epub.write_epub(str(tmp), book)
        if not _is_valid_epub(tmp):
            raise ValueError("rebuilt nav produced an unreadable EPUB")
        tmp.replace(out)  # atomic — `out` is never left half-written
    except Exception as e:
        log.warning("nav rebuild failed for %s (keeping calibre output): %s", out.name, e)
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        return (existing, "calibre" if existing else "none")
    return (len(headings), "heading-detect")


def _reset_nav_items(book: epub.EpubBook) -> None:
    """Drop any existing nav/ncx items and add fresh ones so write_epub
    regenerates them from `book.toc`."""
    keep = []
    for it in book.items:
        fn = (getattr(it, "file_name", "") or "").lower()
        props = getattr(it, "properties", []) or []
        if isinstance(it, (epub.EpubNcx, epub.EpubNav)) or fn.endswith(".ncx") or "nav" in props:
            continue
        keep.append(it)
    book.items = keep
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())


def _content_docs(book: epub.EpubBook):
    """ITEM_DOCUMENT items excluding the navigation document(s) — we never want
    to anchor into / link at the generated nav.xhtml itself."""
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        props = getattr(item, "properties", []) or []
        if isinstance(item, epub.EpubNav) or "nav" in props:
            continue
        yield item


def _collect_xhtml_headings(book: epub.EpubBook) -> list[tuple[int, str, str]]:
    """Find <h1..3> in the converted XHTML, inject `id` anchors where missing,
    and return (level, title, 'file#anchor') in document order. Strictly additive."""
    headings: list[tuple[int, str, str]] = []
    counter = 0
    for item in _content_docs(book):
        try:
            tree = lxml.html.fromstring(item.get_content())
        except Exception:
            continue
        changed = False
        for el in tree.iter():
            tag = _local_tag(el.tag)
            if tag not in ("h1", "h2", "h3"):
                continue
            title = _clean_title(el.text_content())
            if not title:
                continue
            anchor = el.get("id")
            if not anchor:
                anchor = f"dx_h_{counter}"
                el.set("id", anchor)
                changed = True
            counter += 1
            headings.append((int(tag[1]), title, f"{item.file_name}#{anchor}"))
        if changed:
            _write_back(item, tree)
    return headings


def _local_tag(tag) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[-1].lower()


def _write_back(item, tree) -> None:
    with contextlib.suppress(Exception):
        item.set_content(lxml.html.tostring(tree, encoding="utf-8"))


def _clean_title(s: str | None) -> str:
    return " ".join((s or "").split())[:200]
