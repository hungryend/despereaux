"""Shared fixture builders for the regression suite.

These promote idioms already proven in individual test modules (ebooklib EPUBs
from test_tomeforge_sidecar, PIL page images from test_comic, direct Book-row
seeding from test_on_deck) into one importable place. Everything is generated
on the fly — no binary fixtures are committed.
"""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from ebooklib import epub
from httpx import ASGITransport, AsyncClient
from PIL import Image

from despereaux.db import session_scope
from despereaux.models import Book
from despereaux.models.base import new_id


def asgi_client() -> AsyncClient:
    from despereaux.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def png_bytes(
    color: tuple[int, int, int] = (180, 60, 60), size: tuple[int, int] = (64, 96)
) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def write_epub(
    path: Path,
    *,
    title: str = "Sample Book",
    authors: tuple[str, ...] = ("A. Mouse",),
    publisher: str | None = None,
    date: str | None = None,
    description: str | None = None,
    language: str = "en",
    isbn: str | None = None,
    series: str | None = None,
    series_index: float = 1.0,
    subjects: tuple[str, ...] = (),
    cover: bool = False,
    chapters: int = 2,
    body_repeat: int = 8,
) -> Path:
    """Write a small but fully valid EPUB (spine + NCX/Nav) with configurable
    metadata, matching what the ingest extractor reads back."""
    book = epub.EpubBook()
    book.set_identifier(f"urn:isbn:{isbn}" if isbn else f"id-{new_id()}")
    book.set_title(title)
    book.set_language(language)
    for a in authors:
        book.add_author(a)
    if publisher:
        book.add_metadata("DC", "publisher", publisher)
    if date:
        book.add_metadata("DC", "date", date)
    if description:
        book.add_metadata("DC", "description", description)
    for s in subjects:
        book.add_metadata("DC", "subject", s)
    if series:
        book.add_metadata(None, "meta", "", {"name": "calibre:series", "content": series})
        book.add_metadata(
            None, "meta", "", {"name": "calibre:series_index", "content": str(series_index)}
        )
    if cover:
        book.set_cover("cover.png", png_bytes(size=(200, 300)), create_page=False)

    items = []
    for i in range(1, chapters + 1):
        c = epub.EpubHtml(title=f"Chapter {i}", file_name=f"chap_{i:02d}.xhtml", lang=language)
        para = "<p>Words upon words, enough to make the page estimator count something.</p>"
        c.content = f"<html><body><h1>Chapter {i}</h1>{para * body_repeat}</body></html>"
        book.add_item(c)
        items.append(c)

    book.toc = tuple(epub.Link(c.file_name, c.title, f"chap{i}") for i, c in enumerate(items))
    book.spine = ["nav", *items]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(path), book)
    return path


def write_pdf(
    path: Path,
    *,
    pages: int = 3,
    title: str | None = "Sample PDF",
    author: str | None = None,
    subject: str | None = None,
    keywords: str | None = None,
    creation: str | None = None,
) -> Path:
    """Write a valid PDF via pypdf (blank page tree + document-info metadata).
    pdfium renders these pages fine, so the full cover pipeline is exercisable."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    meta: dict[str, str] = {}
    if title:
        meta["/Title"] = title
    if author:
        meta["/Author"] = author
    if subject:
        meta["/Subject"] = subject
    if keywords:
        meta["/Keywords"] = keywords
    if creation:
        meta["/CreationDate"] = creation
    if meta:
        writer.add_metadata(meta)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        writer.write(f)
    return path


def write_cbz(path: Path, *, pages: int = 3) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(1, pages + 1):
            zf.writestr(f"page{i:03d}.png", png_bytes(color=(i * 40 % 256, 100, 200)))
    return path


async def make_book_row(
    *,
    title: str,
    fmt: str = "epub",
    library: str = "Default",
    file_path: str | None = None,
    file_hash: str | None = None,
    file_size: int = 10,
    converted_path: str | None = None,
    epub_export_path: str | None = None,
    cover_path: str | None = None,
    page_count: int | None = None,
    parent_book_id: str | None = None,
    sort_title: str | None = None,
) -> str:
    """Insert a Book row directly (no ingest pipeline) and return its id."""
    async with session_scope() as s:
        b = Book(
            id=new_id(),
            title=title,
            sort_title=sort_title or title,
            format=fmt,
            library=library,
            file_path=file_path or f"/tmp/{new_id()}.{fmt}",
            file_size=file_size,
            file_mtime=datetime.now(UTC),
            file_hash=file_hash or new_id().replace("-", ""),
            converted_path=converted_path,
            epub_export_path=epub_export_path,
            cover_path=cover_path,
            page_count=page_count,
            parent_book_id=parent_book_id,
        )
        s.add(b)
        await s.flush()
        return b.id
