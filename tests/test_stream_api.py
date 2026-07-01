"""Regression tests for api/stream.py — previously zero-coverage.

Covers the read path the reader apps depend on: ETag/304 conditional GETs,
Range/206 partial content (epub.js requests chunks this way), EPUB content
negotiation (export > converted > original, ?original=1 escape hatch), cover
serving, download bookkeeping, and the manifest the reader boots from.

Books are seeded directly (row + real bytes on disk); the ingest pipeline has
its own E2E tests in test_ingest_epub_pdf.py.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from despereaux.db import session_scope
from despereaux.models import Download
from tests.util import asgi_client, make_book_row, write_epub, write_pdf


async def _epub_book(tmp_path: Path, name: str = "book.epub", **kw) -> tuple[str, Path]:
    src = write_epub(tmp_path / name, title=name)
    book_id = await make_book_row(
        title=name, fmt="epub", file_path=str(src), file_size=src.stat().st_size, **kw
    )
    return book_id, src


async def test_file_serves_epub_with_cache_headers(tmp_path: Path) -> None:
    book_id, src = await _epub_book(tmp_path)
    async with asgi_client() as client:
        r = await client.get(f"/api/books/{book_id}/file")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/epub+zip"
        assert r.headers["accept-ranges"] == "bytes"
        assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert r.headers["etag"].startswith('"') and r.headers["etag"].endswith('"')
        assert r.content == src.read_bytes()
        # No attachment disposition on the in-browser read path.
        assert "attachment" not in r.headers.get("content-disposition", "")


async def test_file_if_none_match_returns_304(tmp_path: Path) -> None:
    book_id, _ = await _epub_book(tmp_path)
    async with asgi_client() as client:
        etag = (await client.get(f"/api/books/{book_id}/file")).headers["etag"]
        r = await client.get(f"/api/books/{book_id}/file", headers={"if-none-match": etag})
        assert r.status_code == 304
        assert r.headers["etag"] == etag
        assert r.content == b""


async def test_file_range_request_returns_206(tmp_path: Path) -> None:
    """epub.js streams books via byte ranges; Starlette's FileResponse implements
    them. A dependency bump silently breaking Range support would wedge the reader."""
    book_id, src = await _epub_book(tmp_path)
    data = src.read_bytes()
    async with asgi_client() as client:
        r = await client.get(f"/api/books/{book_id}/file", headers={"range": "bytes=0-99"})
        assert r.status_code == 206
        assert r.content == data[:100]
        assert r.headers["content-range"] == f"bytes 0-99/{len(data)}"

        # Suffix range: the trailing N bytes (epub.js fetches the central directory).
        r = await client.get(f"/api/books/{book_id}/file", headers={"range": "bytes=-50"})
        assert r.status_code == 206
        assert r.content == data[-50:]


async def test_file_prefers_converted_epub_and_original_escape(tmp_path: Path) -> None:
    original = tmp_path / "book.mobi"
    original.write_bytes(b"MOBI-bytes-not-really")
    converted = write_epub(tmp_path / "conv.epub", title="Converted")
    book_id = await make_book_row(
        title="Mobi Book",
        fmt="mobi",
        file_path=str(original),
        converted_path=str(converted),
    )
    async with asgi_client() as client:
        r = await client.get(f"/api/books/{book_id}/file")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/epub+zip"
        assert r.content == converted.read_bytes()
        assert r.headers["etag"].endswith(':epub"')

        r = await client.get(f"/api/books/{book_id}/file", params={"original": 1})
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/x-mobipocket-ebook"
        assert r.content == original.read_bytes()
        assert ":epub" not in r.headers["etag"]


async def test_file_prefers_export_over_converted_with_fallback(tmp_path: Path) -> None:
    original = tmp_path / "b.pdf"
    write_pdf(original, pages=1)
    converted = write_epub(tmp_path / "conv.epub", title="Converted")
    export = write_epub(tmp_path / "export.epub", title="Export")
    book_id = await make_book_row(
        title="Pdf Book",
        fmt="pdf",
        file_path=str(original),
        converted_path=str(converted),
        epub_export_path=str(export),
    )
    async with asgi_client() as client:
        r = await client.get(f"/api/books/{book_id}/file")
        assert r.status_code == 200
        assert r.content == export.read_bytes()

        # Export file vanishes from disk → graceful fallback to converted.
        export.unlink()
        r = await client.get(f"/api/books/{book_id}/file")
        assert r.status_code == 200
        assert r.content == converted.read_bytes()


async def test_file_missing_and_unknown(tmp_path: Path) -> None:
    gone_id = await make_book_row(title="Gone", file_path=str(tmp_path / "nope.epub"))
    async with asgi_client() as client:
        assert (await client.get(f"/api/books/{gone_id}/file")).status_code == 410
        assert (await client.get("/api/books/does-not-exist/file")).status_code == 404


async def test_cover_roundtrip_and_304(tmp_path: Path) -> None:
    from despereaux.services.covers import write_cover
    from tests.util import png_bytes

    book_id = await make_book_row(title="Covered")
    cover_path = write_cover(book_id, png_bytes(size=(300, 400)))
    assert cover_path is not None
    async with session_scope() as s:
        from despereaux.repos.books import get_book

        book = await get_book(s, book_id)
        book.cover_path = str(cover_path)

    async with asgi_client() as client:
        r = await client.get(f"/api/books/{book_id}/cover")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/webp"
        assert r.content[:4] == b"RIFF" and r.content[8:12] == b"WEBP"
        etag = r.headers["etag"]
        r304 = await client.get(f"/api/books/{book_id}/cover", headers={"if-none-match": etag})
        assert r304.status_code == 304


async def test_cover_absent_is_404() -> None:
    book_id = await make_book_row(title="No Cover")
    async with asgi_client() as client:
        assert (await client.get(f"/api/books/{book_id}/cover")).status_code == 404


async def test_download_serves_original_and_logs_row(tmp_path: Path) -> None:
    original = tmp_path / "keep.mobi"
    original.write_bytes(b"MOBI original bytes")
    converted = write_epub(tmp_path / "c.epub")
    book_id = await make_book_row(
        title="DL", fmt="mobi", file_path=str(original), converted_path=str(converted)
    )
    async with asgi_client() as client:
        r = await client.get(
            f"/api/books/{book_id}/download", headers={"user-agent": "regression-suite"}
        )
        assert r.status_code == 200
        # Download always hands out the source file, even when a converted EPUB exists.
        assert r.content == original.read_bytes()
        assert 'attachment; filename="keep.mobi"' in r.headers["content-disposition"]

    async with session_scope() as s:
        rows = (
            (await s.execute(select(Download).where(Download.book_id == book_id))).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].user_agent == "regression-suite"
        assert rows[0].user_id


async def test_manifest_reports_served_format(tmp_path: Path) -> None:
    plain_id, _ = await _epub_book(tmp_path, "plain.epub")
    converted = write_epub(tmp_path / "conv2.epub")
    conv_id = await make_book_row(
        title="Conv", fmt="mobi", file_path=str(tmp_path / "x.mobi"), converted_path=str(converted)
    )
    async with asgi_client() as client:
        m = (await client.get(f"/api/books/{plain_id}/manifest")).json()
        assert m["served_format"] == "epub"
        assert m["original_format"] == "epub"
        assert m["has_epub_export"] is False
        assert m["file_hash"]

        m = (await client.get(f"/api/books/{conv_id}/manifest")).json()
        assert m["served_format"] == "epub"
        assert m["original_format"] == "mobi"


async def test_manifest_export_flag_requires_file_on_disk(tmp_path: Path) -> None:
    export = write_epub(tmp_path / "e.epub")
    book_id = await make_book_row(
        title="Exp",
        fmt="pdf",
        file_path=str(write_pdf(tmp_path / "s.pdf", pages=1)),
        epub_export_path=str(export),
    )
    async with asgi_client() as client:
        assert (await client.get(f"/api/books/{book_id}/manifest")).json()[
            "has_epub_export"
        ] is True
        export.unlink()
        assert (await client.get(f"/api/books/{book_id}/manifest")).json()[
            "has_epub_export"
        ] is False


async def test_comic_page_on_non_comic_is_404(tmp_path: Path) -> None:
    book_id, _ = await _epub_book(tmp_path, "not-comic.epub")
    async with asgi_client() as client:
        assert (await client.get(f"/api/books/{book_id}/page/0")).status_code == 404
