"""On-demand EPUB conversion API: start a job, poll its status, download the
result, and drive the header notifications menu (list + clear)."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.db import get_db
from despereaux.middleware.auth import current_user
from despereaux.models import Conversion, ConversionStatus, Download
from despereaux.models.base import new_id
from despereaux.repos import books as books_repo
from despereaux.repos import conversions as conversions_repo
from despereaux.services import epub_export
from despereaux.services.converter import calibre_available

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["conversions"])


def _safe_filename(title: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 ._-]+", "_", title or "")
    name = re.sub(r"\s+", " ", name).strip().strip(".")
    return name[:120] or "book"


def _public(conv: Conversion, *, title: str | None = None) -> dict:
    data = {
        "conversion_id": conv.id,
        "book_id": conv.book_id,
        "status": conv.status.value,
        "phase": conv.phase,
        "toc_count": conv.toc_count,
        "toc_source": conv.toc_source,
        "image_count": conv.image_count,
        "engine": conv.engine,
        "error": conv.error,
        "download_ready": conv.status == ConversionStatus.done,
    }
    if title is not None:
        data["title"] = title
    return data


@router.post("/books/{book_id}/convert")
async def start_conversion(
    book_id: str,
    background: BackgroundTasks,
    force: bool = False,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    """Queue a high-quality EPUB conversion. Returns the existing job if one is
    already running (dedup double-clicks). `?force=1` re-converts from scratch."""
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    if not epub_export.can_convert(book.format):
        raise HTTPException(status_code=400, detail=f"cannot convert {book.format} to EPUB")
    if not calibre_available():
        raise HTTPException(status_code=503, detail="calibre (ebook-convert) is not installed")

    active = await conversions_repo.get_active_for_book(session, book_id)
    if active:
        return {"status": active.status.value, "conversion_id": active.id}

    conv = await conversions_repo.create(
        session, book_id=book_id, requested_by=user["id"], source_hash=book.file_hash
    )
    await session.commit()
    background.add_task(epub_export.run_export, conv.id, force=force)
    log.info("epub conversion queued: book=%s by=%s force=%s", book_id, user["username"], force)
    return {"status": "queued", "conversion_id": conv.id}


@router.get("/books/{book_id}/convert/status")
async def conversion_status(
    book_id: str,
    session: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
):
    conv = await conversions_repo.get_latest_for_book(session, book_id)
    if conv is None:
        return {"status": "none", "download_ready": False}
    return _public(conv)


@router.get("/books/{book_id}/convert/download")
async def download_epub(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    """Download the high-quality EPUB produced by the Convert-to-EPUB button."""
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")
    if not book.epub_export_path:
        raise HTTPException(status_code=404, detail="no EPUB export for this book")
    path = Path(book.epub_export_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="export file missing on disk")

    session.add(
        Download(
            id=new_id(),
            user_id=user["id"],
            book_id=book_id,
            user_agent=request.headers.get("user-agent"),
        )
    )
    await session.commit()

    filename = f"{_safe_filename(book.title)}.epub"
    return FileResponse(
        path,
        media_type="application/epub+zip",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---- Header notifications menu ----


@router.get("/conversions")
async def list_conversions(
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    """The calling user's recent (non-dismissed) conversions for the menu."""
    rows = await conversions_repo.list_for_user(session, user["id"])
    items = [_public(conv, title=title) for conv, title in rows]
    active = any(it["status"] in ("queued", "running") for it in items)
    return {"conversions": items, "active": active}


@router.post("/conversions/clear")
async def clear_conversions(
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    """Dismiss the user's finished conversions from the menu (jobs keep running)."""
    dismissed = await conversions_repo.dismiss_all_for_user(session, user["id"])
    await session.commit()
    return {"dismissed": dismissed}
