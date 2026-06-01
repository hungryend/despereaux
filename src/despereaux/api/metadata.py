from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.db import get_db, session_scope
from despereaux.middleware.auth import current_user
from despereaux.repos import books as books_repo
from despereaux.services.metadata_apply import apply_candidate
from despereaux.services.metadata_lookup import get_candidates_for

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/books", tags=["metadata"])


class SelectMatchBody(BaseModel):
    source: str  # 'googlebooks' | 'openlibrary'
    external_id: str


@router.get("/{book_id}/metadata-candidates")
async def list_candidates(
    book_id: str,
    session: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
    refresh: bool = Query(False, description="Bypass cache + hit the APIs again"),
) -> dict[str, Any]:
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")

    author = None
    if book.authors:
        author = book.authors[0].author.name

    candidates = await get_candidates_for(book.id, book.title, author, force_refresh=refresh)
    return {
        "book": {"id": book.id, "title": book.title, "author": author},
        "candidates": [
            {
                "source": c.source,
                "external_id": c.external_id,
                "key": c.key(),
                "title": c.title,
                "authors": c.authors,
                "publisher": c.publisher,
                "published_date": c.published_date,
                "description": (c.description[:600] + "…")
                if (c.description and len(c.description) > 600)
                else c.description,
                "isbn": c.isbn,
                "cover_url": c.cover_url,
                "page_count": c.page_count,
                "rating": c.rating,
                "rating_count": c.rating_count,
                "score": c.score,
            }
            for c in candidates
        ],
    }


@router.post("/{book_id}/select-metadata-match", status_code=204)
async def select_match(
    book_id: str,
    body: SelectMatchBody,
    _user=Depends(current_user),
) -> None:
    async with session_scope() as session:
        book = await books_repo.get_book(session, book_id)
        if not book:
            raise HTTPException(status_code=404, detail="book not found")

        # Find candidate in cached list (cheap) or refetch.
        candidates = await get_candidates_for(book.id, book.title, None)
        match = next(
            (
                c
                for c in candidates
                if c.source == body.source and c.external_id == body.external_id
            ),
            None,
        )
        if match is None:
            # Cache may be stale — refresh and try again.
            candidates = await get_candidates_for(book.id, book.title, None, force_refresh=True)
            match = next(
                (
                    c
                    for c in candidates
                    if c.source == body.source and c.external_id == body.external_id
                ),
                None,
            )
        if match is None:
            raise HTTPException(status_code=404, detail="candidate not found")

        await apply_candidate(session, book, match)
