from __future__ import annotations

import contextlib
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import Book


def _unlink_export(path: str | None) -> None:
    """Remove a book's derived EPUB export file (the original is never touched)."""
    if not path:
        return
    with contextlib.suppress(OSError):
        Path(path).unlink(missing_ok=True)


async def delete_by_path(session: AsyncSession, file_path: str) -> int:
    export = (
        await session.execute(select(Book.epub_export_path).where(Book.file_path == file_path))
    ).scalar_one_or_none()
    result = await session.execute(delete(Book).where(Book.file_path == file_path))
    _unlink_export(export)
    return result.rowcount or 0


async def delete_by_id(session: AsyncSession, book_id: str) -> int:
    """Delete a single book by its DB id. The original file on disk is untouched —
    next scan would re-ingest it unless the user also removes the file (or
    the watcher detects a delete event). The derived EPUB export (if any) IS
    removed, since it's a despereaux-generated artifact."""
    export = (
        await session.execute(select(Book.epub_export_path).where(Book.id == book_id))
    ).scalar_one_or_none()
    result = await session.execute(delete(Book).where(Book.id == book_id))
    _unlink_export(export)
    return result.rowcount or 0
