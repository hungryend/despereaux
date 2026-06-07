"""Per-file ingest pipeline.

Format dispatch:
  .epub                       → native EPUB
  .mobi / .azw / .azw3        → convert to EPUB via Calibre, store original + converted
  .pdf                        → native PDF (reader uses PDF.js on the frontend)

All paths converge on a single upsert through `_finalise_ingest()` so DB writes
and cover handling stay consistent.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from despereaux.config import get_settings
from despereaux.db import session_scope
from despereaux.models import MetadataSource
from despereaux.repos.books import get_book_by_path, upsert_book
from despereaux.services.converter import convert_to_epub
from despereaux.services.covers import write_cover
from despereaux.services.metadata.comic import read_comic_metadata
from despereaux.services.metadata.epub import estimate_page_count, read_epub_metadata
from despereaux.services.metadata.pdf import read_pdf_metadata

log = logging.getLogger(__name__)

NATIVE_EPUB_EXTS = {".epub"}
CONVERTIBLE_TO_EPUB_EXTS = {".mobi", ".azw", ".azw3"}
NATIVE_PDF_EXTS = {".pdf"}
COMIC_EXTS = {".cbz", ".cbr"}
SUPPORTED_EXTS = NATIVE_EPUB_EXTS | CONVERTIBLE_TO_EPUB_EXTS | NATIVE_PDF_EXTS | COMIC_EXTS


def detect_format(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in NATIVE_EPUB_EXTS:
        return "epub"
    if ext == ".mobi":
        return "mobi"
    if ext == ".azw":
        return "azw"
    if ext == ".azw3":
        return "azw3"
    if ext in NATIVE_PDF_EXTS:
        return "pdf"
    if ext == ".cbz":
        return "cbz"
    if ext == ".cbr":
        return "cbr"
    return None


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


@dataclass
class _ExtractedMeta:
    """Format-agnostic intermediate representation produced by per-format extractors."""

    title: str
    authors: list[str] = field(default_factory=list)
    publisher: str | None = None
    published_date: str | None = None
    language: str | None = None
    description: str | None = None
    isbn: str | None = None
    series: tuple[str, float] | None = None
    tags: list[str] = field(default_factory=list)
    page_count: int | None = None
    cover_bytes: bytes | None = None
    converted_path: Path | None = None  # for MOBI/AZW: where the .epub now lives


def _parse_pub_date(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _hash_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.blake2b(digest_size=16)
    with path.open("rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


async def _extract_epub(path: Path) -> _ExtractedMeta | None:
    try:
        m = read_epub_metadata(path)
    except Exception as e:
        log.warning("EPUB metadata extraction failed for %s: %s", path, e)
        return None
    return _ExtractedMeta(
        title=m.title,
        authors=m.authors,
        publisher=m.publisher,
        published_date=m.published_date,
        language=m.language,
        description=m.description,
        isbn=m.isbn,
        series=m.series,
        tags=m.tags,
        page_count=estimate_page_count(path),
        cover_bytes=m.cover_bytes,
    )


async def _extract_mobi(path: Path, file_hash: str) -> _ExtractedMeta | None:
    """Convert via Calibre to EPUB, then extract metadata from the result."""
    settings = get_settings()
    converted = settings.converted_dir / f"{file_hash}.epub"
    result = await convert_to_epub(path, converted)
    if result is None:
        log.warning("skipping %s: conversion to EPUB failed", path.name)
        return None
    meta = await _extract_epub(result)
    if meta is None:
        return None
    meta.converted_path = converted
    return meta


async def _extract_pdf(path: Path) -> _ExtractedMeta | None:
    try:
        m = read_pdf_metadata(path)
    except Exception as e:
        log.warning("PDF metadata extraction failed for %s: %s", path, e)
        return None
    return _ExtractedMeta(
        title=m.title,
        authors=m.authors,
        publisher=m.publisher,
        published_date=m.published_date,
        language=m.language,
        description=m.description,
        isbn=m.isbn,
        series=m.series,
        tags=m.tags,
        page_count=m.page_count or None,
        cover_bytes=m.cover_bytes,
    )


async def _extract_comic(path: Path) -> _ExtractedMeta | None:
    try:
        m = read_comic_metadata(path)
    except Exception as e:
        log.warning("comic metadata extraction failed for %s: %s", path, e)
        return None
    if m.page_count <= 0:
        log.warning("skipping %s: no page images found in archive", path.name)
        return None
    return _ExtractedMeta(
        title=m.title,
        authors=m.authors,
        publisher=m.publisher,
        published_date=m.published_date,
        language=m.language,
        description=m.description,
        isbn=m.isbn,
        series=m.series,
        tags=m.tags,
        page_count=m.page_count,
        cover_bytes=m.cover_bytes,
    )


async def ingest_file(path: Path, *, library: str = "Default") -> tuple[str, str] | None:
    """Returns (book_id, status) where status ∈ {'created', 'updated', 'unchanged'}.

    Returns None if the file is unsupported, missing, or fails extraction.
    """
    fmt = detect_format(path)
    if fmt is None:
        return None
    if not path.is_file():
        return None
    # Resolve to absolute path so watcher (absolute) + webhook (often relative)
    # don't create duplicate rows for the same file.
    path = path.resolve()

    file_hash = _hash_file(path)

    async with session_scope() as session:
        existing = await get_book_by_path(session, str(path))
        # Hash is the truth — mtime float-precision round-trip flakes.
        if existing and existing.file_hash == file_hash:
            return (existing.id, "unchanged")

    # Per-format extraction.
    if fmt == "epub":
        meta = await _extract_epub(path)
    elif fmt in {"mobi", "azw", "azw3"}:
        meta = await _extract_mobi(path, file_hash)
    elif fmt == "pdf":
        meta = await _extract_pdf(path)
    elif fmt in {"cbz", "cbr"}:
        meta = await _extract_comic(path)
    else:
        return None

    if meta is None:
        return None

    return await _finalise_ingest(
        path=path, file_hash=file_hash, fmt=fmt, library=library, meta=meta
    )


async def _finalise_ingest(
    *,
    path: Path,
    file_hash: str,
    fmt: str,
    library: str,
    meta: _ExtractedMeta,
) -> tuple[str, str]:
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    size = stat.st_size

    title = meta.title or path.stem
    sort_title = title.removeprefix("The ").removeprefix("A ").removeprefix("An ").strip() or title

    series_name = meta.series[0] if meta.series else None
    series_idx = meta.series[1] if meta.series else None

    fields = {
        "title": title,
        "sort_title": sort_title,
        "publisher": meta.publisher,
        "published_date": _parse_pub_date(meta.published_date),
        "language": meta.language,
        "isbn": meta.isbn,
        "description": meta.description,
        "format": fmt,
        "library": library,
        "file_path": str(path),
        "file_size": size,
        "file_mtime": mtime,
        "file_hash": file_hash,
        "page_count": meta.page_count,
        # Not setting cover_path here — it's written by a separate step
        # below and including it would wipe existing covers on every upsert.
        "converted_path": str(meta.converted_path) if meta.converted_path else None,
        "metadata_source": MetadataSource.local,
    }

    async with session_scope() as session:
        book = await upsert_book(
            session,
            fields=fields,
            author_names=meta.authors,
            series_name=series_name,
            series_index=series_idx,
            tag_names=meta.tags,
        )
        book_id = book.id
        was_created = book.cover_path is None

    if meta.cover_bytes:
        cover_path = write_cover(book_id, meta.cover_bytes)
        if cover_path is not None:
            async with session_scope() as session:
                from despereaux.repos.books import get_book

                refetched = await get_book(session, book_id)
                if refetched is not None:
                    refetched.cover_path = str(cover_path)

    # External enrichment: best-effort, in-band but bounded. If the local
    # metadata was decent (description present from EPUB OPF, say) this is a
    # cheap cache hit; if not, it actually fetches Google Books / Open Library.
    # Failures are logged and ignored — they don't block ingest.
    # External enrichment rarely matches comic scans (Google Books / OpenLibrary)
    # — skip it for CBZ/CBR.
    if was_created and fmt not in {"cbz", "cbr"}:
        try:
            async with session_scope() as session:
                from despereaux.repos.books import get_book
                from despereaux.services.metadata_apply import maybe_auto_enrich

                refetched = await get_book(session, book_id)
                if refetched is not None:
                    await maybe_auto_enrich(session, refetched)
        except Exception as e:
            log.info("auto-enrich skipped for %s (%s)", path.name, e)

    return (book_id, "created" if was_created else "updated")


async def ingest_directory(root: Path | None = None, *, library: str = "Default") -> dict[str, int]:
    settings = get_settings()
    base = root or settings.library_path
    counters = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    if not base.exists():
        log.warning("library '%s' path does not exist: %s", library, base)
        return counters

    log.info("scanning library '%s' at %s", library, base)
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTS:
            counters["skipped"] += 1
            continue
        try:
            result = await ingest_file(path, library=library)
        except Exception as e:
            log.exception("ingest failed for %s: %s", path, e)
            counters["failed"] += 1
            continue
        if result is None:
            counters["skipped"] += 1
        else:
            counters[result[1]] += 1
    log.info("library '%s' scan complete: %s", library, counters)
    return counters


def resolve_library_for_path(path: Path) -> str:
    """Figure out which configured library a given on-disk file belongs to."""
    settings = get_settings()
    try:
        rp = path.resolve()
    except OSError:
        rp = path
    for lib in settings.libraries:
        try:
            rp.relative_to(lib.path.resolve())
            return lib.name
        except ValueError:
            continue
    return "Default"
