from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.db import get_db
from despereaux.middleware.auth import current_user
from despereaux.repos import progress as progress_repo
from despereaux.schemas.book import ProgressRead

router = APIRouter(prefix="/api/progress", tags=["progress"])


@router.get("", response_model=list[ProgressRead])
async def list_my_progress(
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    """Every reading position for the current user.

    Lets clients (e.g. the Furlough Android app) render a "Continue reading" shelf
    and per-book progress badges in one request instead of one call per book.
    """
    rows = await progress_repo.list_progress_for_user(session, user_id=user["id"])
    return [ProgressRead.model_validate(r) for r in rows]
