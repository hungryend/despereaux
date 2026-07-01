"""Ingest E2E for the EPUB and PDF paths (previously only CBZ was covered).

The PDF tests double as the pypdfium2 canary: they run the real
PdfDocument → page.render → to_pil → JPEG → WebP cover pipeline, which has no
other automated coverage and sits on a dependency that recently crossed a
major version.

External enrichment is stubbed at its source module (`_finalise_ingest`
imports it lazily), so no test here touches the network.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from despereaux.db import session_scope
from despereaux.repos.books import get_book
from despereaux.services.ingest import ingest_file
from tests.util import write_epub, write_pdf


@pytest.fixture(autouse=True)
def _no_enrichment(monkeypatch):
    async def _noop(session, book):
        return None

    import despereaux.services.metadata_apply as metadata_apply

    monkeypatch.setattr(metadata_apply, "maybe_auto_enrich", _noop)


async def test_epub_ingest_full_metadata(tmp_path: Path) -> None:
    src = write_epub(
        tmp_path / "full.epub",
        title="The Long Walk",
        authors=("Richard Bachman", "Stephen King"),
        publisher="Signet",
        date="1979-07-02",
        description="A dystopian walk.",
        isbn="9780451196712",
        series="Bachman Books",
        series_index=2.0,
        subjects=("Horror", "Dystopia"),
        cover=True,
    )
    result = await ingest_file(src)
    assert result is not None
    book_id, status = result
    assert status == "created"

    async with session_scope() as s:
        book = await get_book(s, book_id)
        assert book.title == "The Long Walk"
        assert book.sort_title == "Long Walk"
        assert sorted(ba.author.name for ba in book.authors) == [
            "Richard Bachman",
            "Stephen King",
        ]
        assert book.publisher == "Signet"
        assert book.published_date == date(1979, 7, 2)
        assert book.language == "en"
        assert book.description == "A dystopian walk."
        assert book.isbn == "9780451196712"
        assert book.series is not None and book.series.name == "Bachman Books"
        assert book.series_index == 2.0
        assert sorted(bt.tag.name for bt in book.tags) == ["Dystopia", "Horror"]
        assert book.format == "epub"
        assert book.page_count is not None and book.page_count >= 1
        assert book.file_size == src.stat().st_size
        # Cover extracted and rewritten as WebP.
        assert book.cover_path is not None
        cover = Path(book.cover_path)
        assert cover.exists()
        head = cover.read_bytes()[:12]
        assert head[:4] == b"RIFF" and head[8:12] == b"WEBP"


async def test_epub_reingest_unchanged(tmp_path: Path) -> None:
    src = write_epub(tmp_path / "same.epub", title="Same Book", cover=True)
    first = await ingest_file(src)
    assert first is not None and first[1] == "created"
    second = await ingest_file(src)
    assert second is not None
    assert second[0] == first[0]
    assert second[1] == "unchanged"


async def test_epub_reingest_changed_updates_and_invalidates_export(tmp_path: Path) -> None:
    """Content change must update the row AND drop the stale EPUB export, which
    is keyed by the old file hash."""
    src = write_epub(tmp_path / "changing.epub", title="First Title", cover=True)
    result = await ingest_file(src)
    assert result is not None
    book_id = result[0]

    stale_export = tmp_path / "stale-export.epub"
    stale_export.write_bytes(b"PK\x03\x04 old export")
    async with session_scope() as s:
        book = await get_book(s, book_id)
        book.epub_export_path = str(stale_export)

    write_epub(tmp_path / "changing.epub", title="Second Title", cover=True)
    result = await ingest_file(src)
    assert result is not None
    assert result[0] == book_id
    assert result[1] == "updated"

    async with session_scope() as s:
        book = await get_book(s, book_id)
        assert book.title == "Second Title"
        assert book.epub_export_path is None
    assert not stale_export.exists()


async def test_epub_corrupt_returns_none(tmp_path: Path) -> None:
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"PK\x03\x04 this is not a real epub archive")
    assert await ingest_file(bad) is None


async def test_pdf_ingest_metadata_and_cover_render(tmp_path: Path) -> None:
    """The pypdfium2 canary: exact page count from pypdf, authors/keywords
    parsing, D:-date normalisation, and a real rendered cover."""
    src = write_pdf(
        tmp_path / "manual.pdf",
        pages=3,
        title="Player Handbook",
        author="Jane Doe; John Roe",
        subject="A rules tome.",
        keywords="fantasy; rules",
        creation="D:20240102030405Z",
    )
    result = await ingest_file(src)
    assert result is not None
    book_id, status = result
    assert status == "created"

    async with session_scope() as s:
        book = await get_book(s, book_id)
        assert book.format == "pdf"
        assert book.title == "Player Handbook"
        assert book.page_count == 3
        assert sorted(ba.author.name for ba in book.authors) == ["Jane Doe", "John Roe"]
        assert book.description == "A rules tome."
        assert sorted(bt.tag.name for bt in book.tags) == ["fantasy", "rules"]
        assert book.published_date == date(2024, 1, 2)
        assert book.cover_path is not None
        head = Path(book.cover_path).read_bytes()[:12]
        assert head[:4] == b"RIFF" and head[8:12] == b"WEBP"


async def test_pdf_title_falls_back_to_stem(tmp_path: Path) -> None:
    src = write_pdf(tmp_path / "Untitled Scan.pdf", pages=1, title=None)
    result = await ingest_file(src)
    assert result is not None
    async with session_scope() as s:
        book = await get_book(s, result[0])
        assert book.title == "Untitled Scan"
        assert book.page_count == 1


async def test_pdf_cover_render_skipped_over_size_cap(tmp_path: Path, monkeypatch) -> None:
    import despereaux.services.metadata.pdf as pdf_meta

    monkeypatch.setattr(pdf_meta, "COVER_MAX_FILE_BYTES", 10)
    src = write_pdf(tmp_path / "huge.pdf", pages=2, title="Huge Rulebook")
    result = await ingest_file(src)
    assert result is not None
    async with session_scope() as s:
        book = await get_book(s, result[0])
        assert book.page_count == 2
        assert book.cover_path is None  # render skipped, metadata still ingested


async def test_pdf_corrupt_returns_none(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage without a page tree or xref")
    assert await ingest_file(bad) is None


async def test_mobi_ingest_stores_converted_path(tmp_path: Path, monkeypatch) -> None:
    """MOBI dispatch: conversion output feeds metadata extraction and the row
    keeps both the original path and the converted EPUB. The Calibre binary
    itself is exercised in the container smoke tier, not here."""
    import despereaux.services.ingest as ingest_mod

    async def fake_convert(src: Path, out: Path):
        write_epub(out, title="Converted Mobi", authors=("M. Author",))
        return out

    monkeypatch.setattr(ingest_mod, "convert_to_epub", fake_convert)

    src = tmp_path / "old-kindle.mobi"
    src.write_bytes(b"BOOKMOBI fake bytes for hashing")
    result = await ingest_file(src)
    assert result is not None
    async with session_scope() as s:
        book = await get_book(s, result[0])
        assert book.format == "mobi"
        assert book.file_path == str(src.resolve())
        assert book.converted_path is not None and Path(book.converted_path).exists()
        assert book.title == "Converted Mobi"
        assert [ba.author.name for ba in book.authors] == ["M. Author"]


async def test_unsupported_extension_returns_none(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("not an ebook")
    assert await ingest_file(txt) is None
