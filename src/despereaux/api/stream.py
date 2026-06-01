"""File-serving endpoints. ETag + Range + immutable cache headers — this is the foundation
the predictive cache (Phase 3) builds on."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from despereaux.db import get_db
from despereaux.middleware.auth import current_user
from despereaux.models import Download
from despereaux.models.base import new_id
from despereaux.repos import books as books_repo

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/books", tags=["stream"])


_MIME = {
    "epub": "application/epub+zip",
    "pdf": "application/pdf",
    "cbz": "application/vnd.comicbook+zip",
    "cbr": "application/vnd.comicbook-rar",
    "mobi": "application/x-mobipocket-ebook",
    "azw": "application/vnd.amazon.ebook",
    "azw3": "application/vnd.amazon.ebook",
}


def _build_etag(book_hash: str) -> str:
    return f'"{book_hash}"'


@router.get("/{book_id}/file")
async def serve_file(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
):
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")

    # If the source format needed conversion (MOBI/AZW -> EPUB), serve the
    # converted file to the reader. Download still serves the original below.
    if book.converted_path:
        path = Path(book.converted_path)
        served_format = "epub"
    else:
        path = Path(book.file_path)
        served_format = book.format

    if not path.exists():
        raise HTTPException(status_code=410, detail="file missing on disk")

    etag = _build_etag(book.file_hash + (":epub" if book.converted_path else ""))
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable",
        "Accept-Ranges": "bytes",
    }
    media_type = _MIME.get(served_format, "application/octet-stream")
    # FileResponse handles Range requests natively via Starlette.
    # No filename= here — we don't want Content-Disposition: attachment on the in-browser read path.
    return FileResponse(path, media_type=media_type, headers=headers)


@router.get("/{book_id}/cover")
async def serve_cover(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
):
    book = await books_repo.get_book(session, book_id)
    if not book or not book.cover_path:
        raise HTTPException(status_code=404, detail="cover not found")

    path = Path(book.cover_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="cover missing")

    etag = _build_etag(f"{book.file_hash}:cover")
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})

    return FileResponse(
        path,
        media_type="image/webp",
        headers={
            "ETag": etag,
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@router.get("/{book_id}/download")
async def download_book(
    book_id: str,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user=Depends(current_user),
):
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")

    path = Path(book.file_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="file missing on disk")

    session.add(
        Download(
            id=new_id(),
            user_id=user["id"],
            book_id=book_id,
            user_agent=request.headers.get("user-agent"),
        )
    )
    await session.commit()

    media_type = _MIME.get(book.format, "application/octet-stream")
    return FileResponse(
        path,
        media_type=media_type,
        filename=path.name,
        headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
    )


@router.get("/{book_id}/manifest")
async def book_manifest(
    book_id: str,
    session: AsyncSession = Depends(get_db),
    _user=Depends(current_user),
):
    """Lightweight pre-parsed manifest for the reader to boot without re-parsing the OPF."""
    book = await books_repo.get_book(session, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="book not found")

    return {
        "id": book.id,
        "title": book.title,
        "format": book.format,
        # `served_format` is what /file actually returns — 'epub' for converted
        # MOBI/AZW, original format otherwise. The reader uses this to pick its
        # rendering engine.
        "served_format": "epub" if book.converted_path else book.format,
        "page_count": book.page_count,
        "file_hash": book.file_hash,
    }
