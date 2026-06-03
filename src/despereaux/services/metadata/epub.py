"""EPUB metadata + cover extraction via ebooklib."""

from __future__ import annotations

import io
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from ebooklib import ITEM_COVER, ITEM_IMAGE, epub

log = logging.getLogger(__name__)

# ebooklib emits noisy UserWarnings about XML namespaces; silence them.
warnings.filterwarnings("ignore", category=UserWarning, module="ebooklib.epub")
warnings.filterwarnings("ignore", category=FutureWarning, module="ebooklib.epub")


@dataclass
class EpubMetadata:
    title: str
    authors: list[str] = field(default_factory=list)
    publisher: str | None = None
    published_date: str | None = None
    language: str | None = None
    description: str | None = None
    isbn: str | None = None
    series: tuple[str, float] | None = None
    tags: list[str] = field(default_factory=list)
    cover_bytes: bytes | None = None
    spine_count: int = 0


def _first(values: list[tuple[str, dict]]) -> str | None:
    if not values:
        return None
    value = values[0][0]
    return value.strip() if value else None


def _all(values: list[tuple[str, dict]]) -> list[str]:
    out: list[str] = []
    for v, _attrs in values or []:
        if v and v.strip():
            out.append(v.strip())
    return out


def _extract_isbn(book: epub.EpubBook) -> str | None:
    for value, attrs in book.get_metadata("DC", "identifier"):
        if not value:
            continue
        v = value.strip().lower()
        scheme = (attrs or {}).get("scheme", "").lower() if attrs else ""
        if "isbn" in scheme or v.startswith("urn:isbn:") or v.startswith("isbn:"):
            digits = "".join(c for c in v if c.isdigit() or c == "x")
            if len(digits) in (10, 13):
                return digits
    return None


def _extract_series(book: epub.EpubBook) -> tuple[str, float] | None:
    # Calibre stores series in <meta name="calibre:series" content="..."/>
    name: str | None = None
    index: float | None = None
    for _ns, values in book.metadata.items():
        for tag, items in values.items():
            if tag != "meta":
                continue
            for _val, attrs in items:
                if not attrs:
                    continue
                n = attrs.get("name", "")
                if n == "calibre:series":
                    name = attrs.get("content")
                elif n == "calibre:series_index":
                    try:
                        index = float(attrs.get("content", "0"))
                    except (TypeError, ValueError):
                        index = None
    if name:
        return (name, index if index is not None else 1.0)
    return None


def _extract_cover(book: epub.EpubBook) -> bytes | None:
    # First try ITEM_COVER (proper cover property).
    for item in book.get_items_of_type(ITEM_COVER):
        try:
            return item.get_content()
        except Exception as e:
            log.debug("cover item read failed: %s", e)

    # Look for <meta name="cover" content="<manifest-id>"/>.
    cover_id: str | None = None
    for _val, attrs in book.get_metadata("OPF", "meta"):
        if attrs and attrs.get("name") == "cover":
            cover_id = attrs.get("content")
            break
    if cover_id:
        item = book.get_item_with_id(cover_id)
        if item is not None:
            try:
                return item.get_content()
            except Exception as e:
                log.debug("cover-by-id read failed: %s", e)

    # Heuristic fallback: any image with "cover" in the file name.
    for item in book.get_items_of_type(ITEM_IMAGE):
        if "cover" in item.file_name.lower():
            try:
                return item.get_content()
            except Exception:
                continue

    # Last resort: first image in the manifest.
    for item in book.get_items_of_type(ITEM_IMAGE):
        try:
            return item.get_content()
        except Exception:
            continue
    return None


def read_epub_metadata(path: Path) -> EpubMetadata:
    book = epub.read_epub(str(path))

    title = _first(book.get_metadata("DC", "title")) or path.stem
    authors = _all(book.get_metadata("DC", "creator"))
    publisher = _first(book.get_metadata("DC", "publisher"))
    published_date = _first(book.get_metadata("DC", "date"))
    language = _first(book.get_metadata("DC", "language"))
    description = _first(book.get_metadata("DC", "description"))
    isbn = _extract_isbn(book)
    series = _extract_series(book)

    subjects = _all(book.get_metadata("DC", "subject"))

    cover = _extract_cover(book)
    spine = book.spine or []

    return EpubMetadata(
        title=title,
        authors=authors,
        publisher=publisher,
        published_date=published_date,
        language=language,
        description=description,
        isbn=isbn,
        series=series,
        tags=subjects,
        cover_bytes=cover,
        spine_count=len(spine),
    )


def estimate_page_count(path: Path) -> int | None:
    """Word-count-based page estimate at ~250 words/page. epub.js paginates client-side."""
    try:
        book = epub.read_epub(str(path))
    except Exception:
        return None
    words = 0
    for item in book.get_items():
        if not hasattr(item, "get_body_content"):
            continue
        try:
            content = item.get_body_content()
        except Exception:
            continue
        # Cheap text extraction: strip tags via a tiny state machine.
        in_tag = False
        buf = io.StringIO()
        for byte in content:
            ch = chr(byte) if isinstance(byte, int) else byte
            if ch == "<":
                in_tag = True
                continue
            if ch == ">":
                in_tag = False
                buf.write(" ")
                continue
            if not in_tag:
                buf.write(ch)
        words += len(buf.getvalue().split())
    if words == 0:
        return None
    return max(1, words // 250)
