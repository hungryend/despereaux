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
from despereaux.repos.book_delete import delete_by_id
from despereaux.services.metadata_apply import apply_candidate
from despereaux.services.metadata_lookup import (
    fetch_candidate_by_id,
    find_candidates,
    get_candidates_for,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# Use the bundled reader.js mtime as a cache-busting version stamp. Hashing
# would be more accurate but every static-file response already ships an
# ETag, so this just protects against the browser using a heuristic stale
# copy — mtime is plenty.
def _asset_version() -> str:
    bundle = Path(__file__).parent.parent / "static" / "reader" / "assets" / "reader.js"
    try:
        return str(int(bundle.stat().st_mtime))
    except OSError:
        return "0"


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
    duplicates = await books_repo.find_duplicates(session, book)
    children = await books_repo.get_children(session, book.id)
    parent = (
        await books_repo.get_book(session, book.parent_book_id) if book.parent_book_id else None
    )
    return templates.TemplateResponse(
        request,
        "book.html",
        {
            "user": user,
            "book": book,
            "authors": [ba.author.name for ba in (book.authors or [])],
            "tags": [bt.tag.name for bt in (book.tags or [])],
            "progress": prog,
            "duplicates": duplicates,
            "children": children,
            "parent": parent,
        },
    )


@router.get("/book/{book_id}/metadata", response_class=HTMLResponse)
async def edit_metadata(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
    refresh: bool = False,
    q: str | None = None,
    a: str | None = None,
):
    """Picker. With no q/a, queries auto-derived from local title + first author.
    With q/a, the user's keyword search overrides — bypasses cache and doesn't
    write the result back to the per-book cache (so a bad query doesn't
    poison the auto-cache for next time)."""
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    author = book.authors[0].author.name if book.authors else None

    if q or a:
        # Manual keyword query — go straight to the live APIs, no cache.
        candidates = await find_candidates(q or book.title, a or author)
    else:
        candidates = await get_candidates_for(book.id, book.title, author, force_refresh=refresh)
    return templates.TemplateResponse(
        request,
        "metadata.html",
        {
            "user": user,
            "book": book,
            "current_author": author,
            "candidates": candidates,
            "query_title": q or "",
            "query_author": a or "",
        },
    )


@router.get("/book/{book_id}/attach", response_class=HTMLResponse)
async def attach_picker(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
    q: str | None = None,
):
    """Picker for selecting a parent book to attach the current book under as
    an asset. Searches across top-level books, excluding the book being
    attached."""
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    # Search for potential parents — exclude this book + its current children.
    raw = await books_repo.list_books(session, limit=100, search=q)
    candidates = [b for b in raw if b.id != book.id and b.parent_book_id is None]
    return templates.TemplateResponse(
        request,
        "attach.html",
        {
            "user": user,
            "book": book,
            "candidates": candidates,
            "query": q or "",
        },
    )


@router.post("/book/{book_id}/attach")
async def attach_to(
    book_id: str,
    parent_id: str = Form(...),
    asset_label: str = Form(""),
    _user=Depends(current_user),
):
    async with session_scope() as session:
        ok = await books_repo.attach_to_parent(
            session, child_id=book_id, parent_id=parent_id, label=asset_label
        )
    if not ok:
        raise HTTPException(
            status_code=400, detail="couldn't attach (parent missing or would cycle)"
        )
    # Land on the parent so the user sees the asset they just attached.
    return RedirectResponse(url=f"/book/{parent_id}", status_code=303)


@router.post("/book/{book_id}/detach")
async def detach_from(book_id: str, _user=Depends(current_user)):
    async with session_scope() as session:
        await books_repo.detach_from_parent(session, book_id)
    # The detached book is now a top-level book again; show it.
    return RedirectResponse(url=f"/book/{book_id}", status_code=303)


@router.post("/book/{book_id}/delete")
async def delete_book(book_id: str, _user=Depends(current_user)):
    """Remove a book from the library (DB row only — the file on disk stays).
    Useful for pruning duplicates. The next /api/admin/scan would re-ingest
    the file if it's still in a configured library path; to permanently
    remove it, also delete or move the file."""
    async with session_scope() as session:
        await delete_by_id(session, book_id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/book/{book_id}/metadata/refresh")
async def refresh_metadata(book_id: str, _user=Depends(current_user)):
    # Re-render the picker with a fresh fetch from the APIs.
    return RedirectResponse(url=f"/book/{book_id}/metadata?refresh=true", status_code=303)


@router.post("/book/{book_id}/metadata/select")
async def select_metadata(
    book_id: str,
    source: str = Form(...),
    external_id: str = Form(...),
    # The picker preserves the search query that produced the candidate so
    # we can find it again. With manual search the auto-cache won't have it.
    q: str | None = Form(None),
    a: str | None = Form(None),
    _user=Depends(current_user),
):
    async with session_scope() as session:
        book = await books_repo.get_book(session, book_id)
        if not book:
            raise HTTPException(status_code=404, detail="book not found")

        match = await _resolve_candidate(book, source, external_id, q=q, a=a)
        if match is None:
            raise HTTPException(status_code=404, detail="candidate not found")
        await apply_candidate(session, book, match)
    return RedirectResponse(url=f"/book/{book_id}", status_code=303)


async def _resolve_candidate(book, source: str, external_id: str, *, q: str | None, a: str | None):
    """Find the user-selected candidate, trying (cheap → expensive):
    1. Per-book auto-match cache (cheap, in-process)
    2. Re-run the same search the user did (q/a if manual, else book defaults)
    3. Direct-by-ID fetch from the source API (canonical, slowest)
    """
    from despereaux.services.metadata_lookup import find_candidates as _find  # noqa: F401

    # 1. Auto-cache (covers the auto-match path).
    author = book.authors[0].author.name if book.authors else None
    cached = await get_candidates_for(book.id, book.title, author)
    for c in cached:
        if c.source == source and c.external_id == external_id:
            return c

    # 2. Re-run the search that produced it.
    search_title = q or book.title
    search_author = a or author
    fresh = await find_candidates(search_title, search_author)
    for c in fresh:
        if c.source == source and c.external_id == external_id:
            return c

    # 3. Last resort: ask the source for that ID directly. This always works
    # if the record still exists and the source is reachable.
    return await fetch_candidate_by_id(source, external_id)


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
            "asset_version": _asset_version(),
        },
    )
