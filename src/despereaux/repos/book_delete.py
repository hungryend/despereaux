from __future__ import annotations

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.models import Book


async def delete_by_path(session: AsyncSession, file_path: str) -> int:
    result = await session.execute(delete(Book).where(Book.file_path == file_path))
    return result.rowcount or 0
