from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.db import get_db
from despereaux.middleware.auth import current_user
from despereaux.repos import books as books_repo
from despereaux.repos import progress as progress_repo
from despereaux.schemas.book import ProgressRead, ProgressUpdate

router = APIRouter(prefix="/api/books", tags=["progress"])


@router.get("/{book_id}/progress", response_model=ProgressRead | None)
async def get_progress(
    book_id: str,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    row = await progress_repo.get_progress(session, user_id=user["id"], book_id=book_id)
    if not row:
        return None
    return ProgressRead.model_validate(row)


@router.put("/{book_id}/progress", status_code=204)
async def put_progress(
    book_id: str,
    body: ProgressUpdate,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    await progress_repo.upsert_progress(
        session,
        user_id=user["id"],
        book_id=book_id,
        position=body.position,
        percent=max(0.0, min(1.0, body.percent)),
    )
    await session.commit()
