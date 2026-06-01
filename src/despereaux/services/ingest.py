"""Per-file ingest pipeline."""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path

from despereaux.config import get_settings
from despereaux.db import session_scope
from despereaux.models import MetadataSource
from despereaux.repos.books import get_book_by_path, upsert_book
from despereaux.services.converter import convert_to_epub
from despereaux.services.covers import write_cover
from despereaux.services.metadata.epub import estimate_page_count, read_epub_metadata

log = logging.getLogger(__name__)

# Formats we can ingest. EPUB is native; MOBI/AZW/AZW3 are auto-converted to
# EPUB at ingest via Calibre (see services/converter.py). PDF / CBZ / CBR
# are Phase 2.2+.
NATIVE_EPUB_EXTS = {".epub"}
CONVERTIBLE_TO_EPUB_EXTS = {".mobi", ".azw", ".azw3"}
SUPPORTED_EXTS = NATIVE_EPUB_EXTS | CONVERTIBLE_TO_EPUB_EXTS


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
    return None


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


def _parse_pub_date(value: str | None):
    if not value:
        return None
    # EPUB dates can be plain "2019", "2019-01", "2019-01-23", or full ISO timestamps.
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


async def ingest_file(path: Path, *, library: str = "Default") -> tuple[str, str] | None:
    """Returns (book_id, status) where status ∈ {'created', 'updated', 'unchanged', 'skipped'}.

    Returns None if the file is not supported.

    `library` tags the book with its logical library name; if the watcher or
    webhook can't figure out which library a path belongs to, it's resolved via
    `resolve_library_for_path()` against the configured library roots.
    """
    fmt = detect_format(path)
    if fmt is None:
        return None
    if not path.is_file():
        return None
    # Normalise to an absolute, resolved path so the watcher (absolute) and the webhook
    # (often relative) don't create duplicate rows for the same file.
    path = path.resolve()

    file_hash = _hash_file(path)
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    size = stat.st_size

    async with session_scope() as session:
        existing = await get_book_by_path(session, str(path))
        # Hash is the truth (content unchanged). mtime is just for indexing —
        # comparing it loses to float-precision round-trip through SQLite.
        if existing and existing.file_hash == file_hash:
            return (existing.id, "unchanged")

    # For non-EPUB formats, convert to EPUB once and cache by hash so re-ingest
    # doesn't pay the Calibre cost again. The original `path` is preserved on
    # disk + in DB (so /download serves the original), while metadata extraction
    # and the in-browser reader use the converted EPUB.
    converted_path: Path | None = None
    epub_for_metadata = path
    if path.suffix.lower() in CONVERTIBLE_TO_EPUB_EXTS:
        settings = get_settings()
        converted_path = settings.converted_dir / f"{file_hash}.epub"
        converted = await convert_to_epub(path, converted_path)
        if converted is None:
            log.warning("skipping %s: conversion to EPUB failed", path.name)
            return None
        epub_for_metadata = converted

    try:
        meta = read_epub_metadata(epub_for_metadata)
    except Exception as e:
        log.warning("metadata extraction failed for %s: %s", path, e)
        return None

    title = meta.title or path.stem
    sort_title = title.removeprefix("The ").removeprefix("A ").removeprefix("An ").strip() or title
    pages = estimate_page_count(epub_for_metadata)

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
        "format": fmt,  # original format: 'epub', 'mobi', 'azw', 'azw3'
        "library": library,
        "file_path": str(path),
        "file_size": size,
        "file_mtime": mtime,
        "file_hash": file_hash,
        "page_count": pages,
        # Don't include cover_path here — it's written by a separate async step
        # after this block, and overwriting it to None on every upsert wipes
        # the existing cover before the next write can re-create it.
        "converted_path": str(converted_path) if converted_path else None,
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
    """Figure out which configured library a given on-disk file belongs to.

    Returns the first library whose root contains the path, falling back to
    "Default" if none match (which happens for stray paths supplied via the
    webhook outside any configured library).
    """
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
