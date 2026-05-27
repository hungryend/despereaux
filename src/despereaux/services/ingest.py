"""Per-file ingest pipeline."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from despereaux.config import get_settings
from despereaux.db import session_scope
from despereaux.models import MetadataSource
from despereaux.repos.books import get_book_by_path, upsert_book
from despereaux.services.covers import write_cover
from despereaux.services.metadata.epub import estimate_page_count, read_epub_metadata

log = logging.getLogger(__name__)

SUPPORTED_EXTS = {".epub"}  # Phase 1; Phase 2 adds .pdf, .cbz, .cbr, .mobi, .azw, .azw3


def detect_format(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext == ".epub":
        return "epub"
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


async def ingest_file(path: Path) -> tuple[str, str] | None:
    """Returns (book_id, status) where status ∈ {'created', 'updated', 'unchanged', 'skipped'}.

    Returns None if the file is not supported.
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
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    size = stat.st_size

    async with session_scope() as session:
        existing = await get_book_by_path(session, str(path))
        if existing and existing.file_hash == file_hash and existing.file_mtime == mtime:
            return (existing.id, "unchanged")

    try:
        meta = read_epub_metadata(path)
    except Exception as e:
        log.warning("metadata extraction failed for %s: %s", path, e)
        return None

    title = meta.title or path.stem
    sort_title = title.removeprefix("The ").removeprefix("A ").removeprefix("An ").strip() or title
    pages = estimate_page_count(path)

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
        "format": "epub",
        "file_path": str(path),
        "file_size": size,
        "file_mtime": mtime,
        "file_hash": file_hash,
        "page_count": pages,
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


async def ingest_directory(root: Path | None = None) -> dict[str, int]:
    settings = get_settings()
    base = root or settings.library_path
    counters = {"created": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    if not base.exists():
        log.warning("library path does not exist: %s", base)
        return counters

    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTS:
            counters["skipped"] += 1
            continue
        try:
            result = await ingest_file(path)
        except Exception as e:
            log.exception("ingest failed for %s: %s", path, e)
            counters["failed"] += 1
            continue
        if result is None:
            counters["skipped"] += 1
        else:
            counters[result[1]] += 1
    log.info("scan complete: %s", counters)
    return counters
