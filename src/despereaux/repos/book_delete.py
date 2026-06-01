from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import Book


async def delete_by_path(session: AsyncSession, file_path: str) -> int:
    result = await session.execute(delete(Book).where(Book.file_path == file_path))
    return result.rowcount or 0


async def delete_by_id(session: AsyncSession, book_id: str) -> int:
    """Delete a single book by its DB id. The file on disk is untouched —
    next scan would re-ingest it unless the user also removes the file (or
    the watcher detects a delete event)."""
    result = await session.execute(delete(Book).where(Book.id == book_id))
    return result.rowcount or 0
