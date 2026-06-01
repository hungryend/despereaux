from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.config import get_settings
from despereaux.db import get_db, session_scope
from despereaux.middleware.auth import current_user
from despereaux.repos import books as books_repo
from despereaux.repos import progress as progress_repo
from despereaux.services.metadata_apply import apply_candidate
from despereaux.services.metadata_lookup import get_candidates_for

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
async def library(
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
    search: str | None = None,
    library: str | None = None,
):
    settings = get_settings()
    books = await books_repo.list_books(session, limit=500, search=search, library=library)
    progress_map = {
        p.book_id: p.percent
        for p in await progress_repo.list_progress_for_user(session, user_id=user["id"])
    }
    counts = await books_repo.count_books_by_library(session)
    libraries = [
        {"name": lib.name, "book_count": counts.get(lib.name, 0)} for lib in settings.libraries
    ]
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "user": user,
            "books": books,
            "progress_map": progress_map,
            "search": search or "",
            "libraries": libraries,
            "current_library": library,
        },
    )


@router.get("/book/{book_id}", response_class=HTMLResponse)
async def book_detail(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    prog = await progress_repo.get_progress(session, user_id=user["id"], book_id=book_id)
    return templates.TemplateResponse(
        request,
        "book.html",
        {
            "user": user,
            "book": book,
            "authors": [ba.author.name for ba in (book.authors or [])],
            "tags": [bt.tag.name for bt in (book.tags or [])],
            "progress": prog,
        },
    )


@router.get("/book/{book_id}/metadata", response_class=HTMLResponse)
async def edit_metadata(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
    refresh: bool = False,
):
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    author = book.authors[0].author.name if book.authors else None
    candidates = await get_candidates_for(book.id, book.title, author, force_refresh=refresh)
    return templates.TemplateResponse(
        request,
        "metadata.html",
        {
            "user": user,
            "book": book,
            "current_author": author,
            "candidates": candidates,
        },
    )


@router.post("/book/{book_id}/metadata/refresh")
async def refresh_metadata(book_id: str, _user=Depends(current_user)):
    # Re-render the picker with a fresh fetch from the APIs.
    return RedirectResponse(url=f"/book/{book_id}/metadata?refresh=true", status_code=303)


@router.post("/book/{book_id}/metadata/select")
async def select_metadata(
    book_id: str,
    source: str = Form(...),
    external_id: str = Form(...),
    _user=Depends(current_user),
):
    async with session_scope() as session:
        book = await books_repo.get_book(session, book_id)
        if not book:
            raise HTTPException(status_code=404, detail="book not found")
        candidates = await get_candidates_for(book.id, book.title, None)
        match = next(
            (c for c in candidates if c.source == source and c.external_id == external_id),
            None,
        )
        if match is None:
            candidates = await get_candidates_for(book.id, book.title, None, force_refresh=True)
            match = next(
                (c for c in candidates if c.source == source and c.external_id == external_id),
                None,
            )
        if match is None:
            raise HTTPException(status_code=404, detail="candidate not found (cache stale)")
        await apply_candidate(session, book, match)
    return RedirectResponse(url=f"/book/{book_id}", status_code=303)


@router.get("/read/{book_id}", response_class=HTMLResponse)
async def read_book(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    # Converted books (MOBI/AZW -> EPUB) are served as EPUB to the reader JS.
    effective_format = "epub" if book.converted_path else book.format
    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "user": user,
            "book": book,
            "effective_format": effective_format,
        },
    )
