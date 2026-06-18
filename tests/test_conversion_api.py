"""Integration tests for the convert-to-EPUB API + reading-state plumbing.

Calibre isn't assumed on the test host, so the conversion subprocess is stubbed
(`run_export` monkeypatched) — we test the endpoints, dedup, status, the
notifications menu, download/serve preference, and the staleness cleanup.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ebooklib import epub
from httpx import ASGITransport, AsyncClient

from despereaux.config import get_settings
from despereaux.db import session_scope
from despereaux.main import app
from despereaux.models import Book, ConversionStatus
from despereaux.models.base import new_id
from despereaux.repos import conversions as conversions_repo
from despereaux.repos.books import get_book


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _make_book(*, fmt: str = "pdf", epub_export_path=None, file_path=None) -> str:
    async with session_scope() as s:
        b = Book(
            id=new_id(),
            title="Test Book",
            sort_title="Test Book",
            format=fmt,
            library="Default",
            file_path=file_path or f"/tmp/{new_id()}.{fmt}",
            file_size=10,
            file_mtime=datetime.now(UTC),
            file_hash=new_id().replace("-", ""),
            epub_export_path=epub_export_path,
        )
        s.add(b)
        await s.flush()
        return b.id


def _write_min_epub(path, *, title="X", ident="id-x") -> None:
    book = epub.EpubBook()
    book.set_identifier(ident)
    book.set_title(title)
    book.set_language("en")
    c = epub.EpubHtml(title="C", file_name="c.xhtml")
    c.content = f"<html><body><h1>{title}</h1><p>hi</p></body></html>"
    book.add_item(c)
    book.spine = [c]
    book.toc = (epub.Link("c.xhtml", "C", "c"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


async def _noop_run(conversion_id, *, force=False):
    return None


async def _async_noop(*args, **kwargs):
    return None


async def test_convert_rejects_epub():
    book_id = await _make_book(fmt="epub")
    async with _client() as c:
        r = await c.post(f"/api/books/{book_id}/convert")
    assert r.status_code == 400


async def test_convert_503_without_calibre(monkeypatch):
    monkeypatch.setattr("despereaux.api.export.calibre_available", lambda: False)
    book_id = await _make_book(fmt="pdf")
    async with _client() as c:
        r = await c.post(f"/api/books/{book_id}/convert")
    assert r.status_code == 503


async def test_convert_queues_dedupes_and_status(monkeypatch):
    monkeypatch.setattr("despereaux.api.export.calibre_available", lambda: True)
    monkeypatch.setattr("despereaux.services.epub_export.run_export", _noop_run)
    book_id = await _make_book(fmt="pdf")
    async with _client() as c:
        r = await c.post(f"/api/books/{book_id}/convert")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "queued"
        cid = body["conversion_id"]

        # Double-click returns the same active job rather than a duplicate.
        r2 = await c.post(f"/api/books/{book_id}/convert")
        assert r2.json()["conversion_id"] == cid

        st = await c.get(f"/api/books/{book_id}/convert/status")
        assert st.json()["status"] in ("queued", "running")

    async with session_scope() as s:
        conv = await conversions_repo.get(s, cid)
        assert conv is not None and conv.book_id == book_id


async def test_status_none_for_unconverted():
    book_id = await _make_book(fmt="pdf")
    async with _client() as c:
        r = await c.get(f"/api/books/{book_id}/convert/status")
    assert r.json()["status"] == "none"


async def test_conversions_menu_list_and_clear(monkeypatch):
    monkeypatch.setattr("despereaux.api.export.calibre_available", lambda: True)
    monkeypatch.setattr("despereaux.services.epub_export.run_export", _noop_run)
    book_id = await _make_book(fmt="pdf")
    async with _client() as c:
        await c.post(f"/api/books/{book_id}/convert")
        listed = (await c.get("/api/conversions")).json()
        assert any(i["book_id"] == book_id for i in listed["conversions"])

    # Clear only dismisses finished jobs, so mark it done first.
    async with session_scope() as s:
        conv = await conversions_repo.get_latest_for_book(s, book_id)
        conv.status = ConversionStatus.done

    async with _client() as c:
        cleared = (await c.post("/api/conversions/clear")).json()
        assert cleared["dismissed"] >= 1
        after = (await c.get("/api/conversions")).json()
        assert not any(i["book_id"] == book_id for i in after["conversions"])


async def test_download_404_without_export():
    book_id = await _make_book(fmt="pdf")
    async with _client() as c:
        r = await c.get(f"/api/books/{book_id}/convert/download")
    assert r.status_code == 404


async def test_download_and_serve_prefer_export(tmp_path):
    export = tmp_path / "export.epub"
    _write_min_epub(export)
    orig = tmp_path / "orig.pdf"
    orig.write_bytes(b"%PDF-1.4\n%mock\n")
    book_id = await _make_book(fmt="pdf", epub_export_path=str(export), file_path=str(orig))

    async with _client() as c:
        dl = await c.get(f"/api/books/{book_id}/convert/download")
        assert dl.status_code == 200
        assert dl.headers["content-type"] == "application/epub+zip"
        assert "attachment" in dl.headers.get("content-disposition", "")

        served = await c.get(f"/api/books/{book_id}/file")
        assert served.status_code == 200
        assert served.headers["content-type"] == "application/epub+zip"

        original = await c.get(f"/api/books/{book_id}/file?original=1")
        assert original.status_code == 200
        assert original.headers["content-type"] == "application/pdf"

        manifest = (await c.get(f"/api/books/{book_id}/manifest")).json()
        assert manifest["served_format"] == "epub"
        assert manifest["original_format"] == "pdf"
        assert manifest["has_epub_export"] is True


async def test_delete_unlinks_export(tmp_path):
    from despereaux.repos.book_delete import delete_by_id

    export = tmp_path / "del.epub"
    export.write_bytes(b"dummy")
    book_id = await _make_book(fmt="pdf", epub_export_path=str(export))
    async with session_scope() as s:
        n = await delete_by_id(s, book_id)
    assert n == 1
    assert not export.exists()


async def test_remove_converted_version_route(tmp_path):
    """The 'Remove EPUB version' button clears the export but keeps the book."""
    export = tmp_path / "todelete.epub"
    _write_min_epub(export)
    book_id = await _make_book(fmt="pdf", epub_export_path=str(export))
    async with _client() as c:
        r = await c.post(f"/book/{book_id}/convert/delete")
    assert r.status_code == 303  # redirect back to the book page
    assert not export.exists()
    async with session_scope() as s:
        b = await get_book(s, book_id)
        assert b is not None  # book row survives
        assert b.epub_export_path is None


async def test_reingest_clears_stale_export(tmp_path, monkeypatch):
    from despereaux.services.ingest import ingest_file

    # Keep ingest offline (no Google Books / Open Library on create).
    monkeypatch.setattr("despereaux.services.metadata_apply.maybe_auto_enrich", _async_noop)

    epub_path = tmp_path / "book.epub"
    _write_min_epub(epub_path, title="Version One", ident="id-v1")
    created = await ingest_file(epub_path)
    assert created is not None
    book_id, status = created
    assert status == "created"

    settings = get_settings()
    stale = settings.exports_dir / f"stale-{new_id()}.epub"
    stale.write_bytes(b"x")
    async with session_scope() as s:
        b = await get_book(s, book_id)
        b.epub_export_path = str(stale)

    # Change the file's content -> new hash -> re-ingest the same path. (The
    # created/updated label is a cover-presence heuristic, so assert on identity:
    # the same row is reused and its stale export pointer + file are cleared.)
    _write_min_epub(epub_path, title="Version Two Changed", ident="id-v2")
    updated = await ingest_file(epub_path)
    assert updated is not None and updated[0] == book_id

    async with session_scope() as s:
        b = await get_book(s, book_id)
        assert b.epub_export_path is None
    assert not stale.exists()


async def test_fail_orphaned_marks_active_failed():
    book_id = await _make_book(fmt="pdf")
    async with session_scope() as s:
        a = await conversions_repo.create(s, book_id=book_id, requested_by="u1", source_hash="h1")
        b = await conversions_repo.create(s, book_id=book_id, requested_by="u1", source_hash="h2")
        await conversions_repo.set_status(s, b.id, status=ConversionStatus.running)
        ids = [a.id, b.id]
    async with session_scope() as s:
        n = await conversions_repo.fail_orphaned(s)
    assert n >= 2
    async with session_scope() as s:
        for cid in ids:
            row = await conversions_repo.get(s, cid)
            assert row.status == ConversionStatus.failed
            assert row.error


async def test_reader_is_fresh_and_versioned(tmp_path):
    """Reader page is no-store and the file URL is cache-busted, so a converted
    book reads as EPUB instead of replaying the browser-cached PDF."""
    export = tmp_path / "r.epub"
    _write_min_epub(export)
    orig = tmp_path / "r.pdf"
    orig.write_bytes(b"%PDF-1.4\n%mock\n")
    book_id = await _make_book(fmt="pdf", epub_export_path=str(export), file_path=str(orig))
    async with _client() as c:
        r = await c.get(f"/read/{book_id}")
        assert r.headers.get("cache-control") == "no-store"
        assert 'format: "epub"' in r.text
        assert f"/api/books/{book_id}/file?v=" in r.text

        ro = await c.get(f"/read/{book_id}?original=1")
        assert 'format: "pdf"' in ro.text
        assert "&original=1" in ro.text
