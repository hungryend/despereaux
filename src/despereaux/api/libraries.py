from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.config import get_settings
from despereaux.db import get_db
from despereaux.middleware.auth import current_user
from despereaux.repos.books import count_books_by_library
from despereaux.schemas.book import LibraryRead

router = APIRouter(prefix="/api/libraries", tags=["libraries"])


@router.get("", response_model=list[LibraryRead])
async def list_libraries(
    session: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
):
    """List every configured library plus its current book count.

    Includes libraries with zero books so the UI shows empty slots that the
    user has configured but not populated.
    """
    settings = get_settings()
    counts = await count_books_by_library(session)
    rows: list[LibraryRead] = []
    for lib in settings.libraries:
        rows.append(
            LibraryRead(name=lib.name, path=str(lib.path), book_count=counts.get(lib.name, 0))
        )
    # Include any DB-discovered library names not in config (e.g. orphans from
    # config changes) so the user can see they exist + clean them up.
    configured = {lib.name for lib in settings.libraries}
    for name, count in counts.items():
        if name not in configured:
            rows.append(LibraryRead(name=name, path="(orphan — not in config)", book_count=count))
    return rows
