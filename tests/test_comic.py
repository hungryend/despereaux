"""Comic (CBZ) support: archive service unit tests + an end-to-end ingest →
page-endpoint integration test.

Only CBZ is exercised here — CBR needs the `unar` binary (shipped in the Docker
image, not assumed on dev/CI hosts), and constructing a real RAR requires a
proprietary encoder. The CBR path shares the same `_Archive`/ingest code, so the
CBZ coverage validates the logic that matters.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

from despereaux.services import comic


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 12), color).save(buf, format="PNG")
    return buf.getvalue()


def _make_cbz(
    path: Path,
    *,
    page_names: list[str] | None = None,
    comicinfo: str | None = None,
    junk: bool = True,
) -> list[str]:
    """Write a tiny CBZ. Pages are added in scrambled order to prove sorting.
    Returns the page names in the order they were written."""
    names = page_names or ["page10.png", "page1.png", "page2.png"]
    with zipfile.ZipFile(path, "w") as zf:
        for i, name in enumerate(names):
            # Distinct colour per page so bytes differ.
            zf.writestr(name, _png_bytes((i * 40 % 256, 100, 200)))
        if comicinfo is not None:
            zf.writestr("ComicInfo.xml", comicinfo)
        if junk:
            # macOS archive cruft + a dotfile + a non-image — all must be ignored.
            zf.writestr("__MACOSX/._page1.png", b"junk")
            zf.writestr(".DS_Store", b"junk")
            zf.writestr("notes.txt", b"not a page")
    return names


def test_natural_sort_orders_pages_numerically(tmp_path: Path) -> None:
    cbz = tmp_path / "x.cbz"
    _make_cbz(cbz, page_names=["page10.png", "page1.png", "page2.png"])
    assert comic.list_pages(cbz) == ["page1.png", "page2.png", "page10.png"]


def test_page_count_excludes_non_page_entries(tmp_path: Path) -> None:
    cbz = tmp_path / "x.cbz"
    _make_cbz(cbz, page_names=["a.jpg", "b.jpg"], comicinfo="<ComicInfo/>", junk=True)
    # 2 images only — ComicInfo.xml, __MACOSX, .DS_Store, notes.txt all skipped.
    assert comic.page_count(cbz) == 2


def test_read_page_returns_bytes_and_content_type(tmp_path: Path) -> None:
    cbz = tmp_path / "x.cbz"
    _make_cbz(cbz, page_names=["p1.png", "p2.png"], junk=False)
    result = comic.read_page(cbz, 0)
    assert result is not None
    data, content_type = result
    assert content_type == "image/png"
    assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic


def test_read_page_out_of_range_is_none(tmp_path: Path) -> None:
    cbz = tmp_path / "x.cbz"
    _make_cbz(cbz, page_names=["p1.png"], junk=False)
    assert comic.read_page(cbz, 5) is None
    assert comic.read_page(cbz, -1) is None


def test_first_page_bytes(tmp_path: Path) -> None:
    cbz = tmp_path / "x.cbz"
    _make_cbz(cbz, page_names=["p2.png", "p1.png"], junk=False)
    first = comic.first_page_bytes(cbz)
    assert first is not None and first[:4] == b"\x89PNG"


def test_content_type_for() -> None:
    assert comic.content_type_for("a.JPG") == "image/jpeg"
    assert comic.content_type_for("a.webp") == "image/webp"
    assert comic.content_type_for("a.xyz") == "application/octet-stream"


def test_read_comic_metadata_from_comicinfo(tmp_path: Path) -> None:
    from despereaux.services.metadata.comic import read_comic_metadata

    cbz = tmp_path / "saga.cbz"
    comicinfo = (
        "<?xml version='1.0'?>"
        "<ComicInfo><Series>Saga</Series><Number>3</Number>"
        "<Writer>Brian K. Vaughan</Writer><Publisher>Image</Publisher>"
        "<Year>2013</Year><Summary>Hello</Summary><LanguageISO>en</LanguageISO>"
        "</ComicInfo>"
    )
    _make_cbz(cbz, page_names=["p1.png", "p2.png"], comicinfo=comicinfo, junk=False)
    meta = read_comic_metadata(cbz)
    assert meta.title == "Saga #3"
    assert meta.series == ("Saga", 3.0)
    assert meta.authors == ["Brian K. Vaughan"]
    assert meta.publisher == "Image"
    assert meta.language == "en"
    assert meta.page_count == 2
    assert meta.cover_bytes is not None


def test_read_comic_metadata_falls_back_to_filename(tmp_path: Path) -> None:
    from despereaux.services.metadata.comic import read_comic_metadata

    cbz = tmp_path / "My Great Comic.cbz"
    _make_cbz(cbz, page_names=["p1.png"], junk=False)
    meta = read_comic_metadata(cbz)
    assert meta.title == "My Great Comic"
    assert meta.series is None
    assert meta.page_count == 1


async def test_ingest_comic_and_serve_page(tmp_path: Path) -> None:
    """End-to-end: ingest a CBZ, confirm the DB row + cover, then fetch a page and
    a cover over HTTP through the real ASGI app (dev-mode auto-auth)."""
    from httpx import ASGITransport, AsyncClient

    from despereaux.db import session_scope
    from despereaux.main import app
    from despereaux.repos.books import get_book
    from despereaux.services.ingest import ingest_file

    cbz = tmp_path / "Ingest Me.cbz"
    _make_cbz(cbz, page_names=["page1.png", "page2.png", "page3.png"], junk=True)

    result = await ingest_file(cbz)
    assert result is not None
    book_id, status = result
    assert status == "created"

    async with session_scope() as session:
        book = await get_book(session, book_id)
        assert book is not None
        assert book.format == "cbz"
        assert book.page_count == 3
        assert book.cover_path is not None
        assert Path(book.cover_path).exists()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # First page (0-based) → image bytes.
        r = await client.get(f"/api/books/{book_id}/page/0")
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")
        assert r.content[:4] == b"\x89PNG"
        etag = r.headers.get("etag")
        assert etag

        # Conditional GET → 304.
        r304 = await client.get(
            f"/api/books/{book_id}/page/0", headers={"if-none-match": etag}
        )
        assert r304.status_code == 304

        # Out-of-range page → 404.
        r404 = await client.get(f"/api/books/{book_id}/page/99")
        assert r404.status_code == 404

        # Cover endpoint serves the generated thumbnail.
        rc = await client.get(f"/api/books/{book_id}/cover")
        assert rc.status_code == 200
        assert rc.headers["content-type"] == "image/webp"
