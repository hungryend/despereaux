"""Tests for the PDF -> Markdown -> EPUB pipeline (pdf2md intermediate step).

- `pdf_is_scan` classification (needs PyMuPDF -> importorskip).
- `_pdf_to_markdown` Options/convert wiring (no PyMuPDF needed; convert stubbed).
- `run_export` PDF guards: PyMuPDF-absent failure, scan skip, temp-dir cleanup
  (all stubbed — no Calibre/PyMuPDF required).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from despereaux.db import session_scope
from despereaux.models import Book, ConversionStatus
from despereaux.models.base import new_id
from despereaux.repos import conversions as conversions_repo
from despereaux.services import epub_export


async def _make_pdf_book(tmp_path) -> str:
    f = tmp_path / f"{new_id()}.pdf"
    f.write_bytes(b"%PDF-1.4 mock")
    async with session_scope() as s:
        b = Book(
            id=new_id(), title="T", sort_title="T", format="pdf", library="Default",
            file_path=str(f), file_size=12, file_mtime=datetime.now(UTC),
            file_hash=new_id().replace("-", ""),
        )
        s.add(b)
        await s.flush()
        return b.id


async def _make_conv(book_id: str) -> str:
    async with session_scope() as s:
        c = await conversions_repo.create(s, book_id=book_id, requested_by="u1", source_hash="h")
        return c.id


# --------------------------------------------------------------------------- #
# pdf_is_scan (real PyMuPDF)
# --------------------------------------------------------------------------- #


def test_pdf_is_scan_classifies(tmp_path):
    fitz = pytest.importorskip("fitz")
    from PIL import Image

    from despereaux.services.pdf2md import pdf_is_scan

    img = tmp_path / "page.png"
    Image.new("RGB", (1200, 1600), (20, 40, 60)).save(img)

    # OCR'd scan: full-page image + an INVISIBLE OCR text layer (the Morte case).
    scan = tmp_path / "scan.pdf"
    d = fitz.open()
    for _ in range(3):
        pg = d.new_page(width=600, height=800)
        pg.insert_image(pg.rect, filename=str(img))
        pg.insert_text((72, 120), "garbled ocr layer text " * 20, render_mode=3)  # invisible
    d.save(str(scan))
    d.close()
    assert pdf_is_scan(str(scan)) is True

    # Born-digital with a full-page background image + REAL visible text -> NOT a scan.
    born = tmp_path / "text.pdf"
    d = fitz.open()
    for _ in range(3):
        pg = d.new_page(width=600, height=800)
        pg.insert_image(pg.rect, filename=str(img))               # parchment background
        pg.insert_text((72, 120), "Real visible body text. " * 30)  # visible vector text
    d.save(str(born))
    d.close()
    assert pdf_is_scan(str(born)) is False


# --------------------------------------------------------------------------- #
# _pdf_to_markdown wiring (convert/Options stubbed — no PyMuPDF needed)
# --------------------------------------------------------------------------- #


def test_pdf_to_markdown_wiring(tmp_path, monkeypatch):
    import despereaux.services.pdf2md as pdf2md

    captured: dict = {}

    class FakeOptions:
        def __init__(self, **kw):
            captured["opts"] = kw

    def fake_convert(pdf_path, opt, pages_spec="", toc=True, quiet=False):
        captured["call"] = {"pdf_path": pdf_path, "toc": toc, "quiet": quiet}
        out = Path(tmp_path) / "output.md"
        out.write_text("# Title\n\nbody", encoding="utf-8")
        return str(out)

    monkeypatch.setattr(pdf2md, "Options", FakeOptions)
    monkeypatch.setattr(pdf2md, "convert", fake_convert)

    res = epub_export._pdf_to_markdown(Path("in.pdf"), Path(tmp_path))
    assert res == Path(tmp_path) / "output.md"
    assert captured["opts"]["out_dir"] == str(tmp_path)
    assert captured["call"]["toc"] is True
    assert captured["call"]["quiet"] is True


def test_pdf_to_markdown_none_when_empty(tmp_path, monkeypatch):
    import despereaux.services.pdf2md as pdf2md

    monkeypatch.setattr(pdf2md, "Options", lambda **kw: object())
    monkeypatch.setattr(pdf2md, "convert", lambda *a, **k: str(Path(tmp_path) / "missing.md"))
    assert epub_export._pdf_to_markdown(Path("in.pdf"), Path(tmp_path)) is None


# --------------------------------------------------------------------------- #
# run_export PDF guards (Calibre/PyMuPDF stubbed)
# --------------------------------------------------------------------------- #


async def test_run_export_pdf_requires_pymupdf(tmp_path, monkeypatch):
    monkeypatch.setattr(epub_export, "calibre_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdfmd_available", lambda: False)
    cid = await _make_conv(await _make_pdf_book(tmp_path))
    await epub_export.run_export(cid)
    async with session_scope() as s:
        conv = await conversions_repo.get(s, cid)
    assert conv.status == ConversionStatus.failed
    assert "pdf" in (conv.error or "").lower()


async def test_run_export_pdf_scan_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(epub_export, "calibre_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdfmd_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdf_is_scan", lambda src: True)
    book_id = await _make_pdf_book(tmp_path)
    cid = await _make_conv(book_id)
    await epub_export.run_export(cid)
    async with session_scope() as s:
        conv = await conversions_repo.get(s, cid)
        from despereaux.repos.books import get_book

        book = await get_book(s, book_id)
    assert conv.status == ConversionStatus.failed
    assert "scanned" in (conv.error or "").lower()
    assert book.epub_export_path is None


async def test_run_export_pdf_tempdir_cleaned_up(tmp_path, monkeypatch):
    monkeypatch.setattr(epub_export, "calibre_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdfmd_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdf_is_scan", lambda src: False)
    seen: dict = {}

    def fake_md(src, workdir):
        seen["work"] = Path(workdir)
        assert Path(workdir).exists()  # created before _pdf_to_markdown runs
        return None  # force "could not extract" -> exercises the finally cleanup

    monkeypatch.setattr(epub_export, "_pdf_to_markdown", fake_md)
    cid = await _make_conv(await _make_pdf_book(tmp_path))
    await epub_export.run_export(cid)
    assert "work" in seen and not seen["work"].exists()
    async with session_scope() as s:
        conv = await conversions_repo.get(s, cid)
    assert conv.status == ConversionStatus.failed


# --------------------------------------------------------------------------- #
# OCR fallback path (Ollama stubbed — no real OCR server)
# --------------------------------------------------------------------------- #


def test_pdf_to_markdown_ocr_wiring(tmp_path, monkeypatch):
    import types

    import despereaux.services.pdf2md as pdf2md

    cap: dict = {}

    class FakeOptions:
        def __init__(self, **kw):
            cap["opts"] = kw

    def fake_convert(pdf_path, opt, pages_spec="", toc=True, quiet=False):
        cap["call"] = {"toc": toc, "quiet": quiet}
        out = Path(tmp_path) / "output.md"
        out.write_text("# H\n\nbody", encoding="utf-8")
        return str(out)

    monkeypatch.setattr(pdf2md, "Options", FakeOptions)
    monkeypatch.setattr(pdf2md, "convert", fake_convert)
    monkeypatch.setattr(
        epub_export, "get_settings",
        lambda: types.SimpleNamespace(
            ollama_host="http://ocr:11434", ollama_model="m:1",
            ollama_ocr_timeout=42, ollama_num_ctx=4096, ollama_dpi=120,
        ),
    )
    res = epub_export._pdf_to_markdown_ocr(Path("in.pdf"), Path(tmp_path))
    assert res == Path(tmp_path) / "output.md"
    o = cap["opts"]
    assert o["engine"] == "ollama" and o["resume"] is True and o["page_images"] == "auto"
    assert o["ollama_host"] == "http://ocr:11434" and o["model"] == "m:1"
    assert o["ocr_timeout"] == 42 and o["ocr_num_ctx"] == 4096 and o["dpi"] == 120
    assert cap["call"] == {"toc": True, "quiet": True}


async def test_run_export_ocr_path(tmp_path, monkeypatch):
    monkeypatch.setattr(epub_export, "calibre_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdfmd_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdf_is_scan", lambda src: True)
    monkeypatch.setattr(epub_export, "ocr_available", lambda: True)
    monkeypatch.setattr(epub_export, "_ollama_reachable", lambda host: True)
    monkeypatch.setattr(epub_export, "_page_count", lambda src: 3)

    called: dict = {}

    def fake_ocr(src, workdir):
        called["workdir"] = Path(workdir)
        Path(workdir).mkdir(parents=True, exist_ok=True)
        md = Path(workdir) / "output.md"
        md.write_text("# H\n\nbody", encoding="utf-8")
        return md

    async def fake_convert(src, out, **kw):
        Path(out).write_bytes(b"epub-bytes")
        return out

    monkeypatch.setattr(epub_export, "_pdf_to_markdown_ocr", fake_ocr)
    monkeypatch.setattr(epub_export, "convert_to_epub", fake_convert)
    monkeypatch.setattr(epub_export, "_ensure_linked_toc", lambda out: (3, "heading-detect"))
    monkeypatch.setattr(epub_export, "_is_valid_epub", lambda out: True)
    monkeypatch.setattr(epub_export, "_count_images", lambda out: 0)
    monkeypatch.setattr(epub_export, "_extract_text_len", lambda out: 1000)

    book_id = await _make_pdf_book(tmp_path)
    cid = await _make_conv(book_id)
    await epub_export.run_export(cid)

    async with session_scope() as s:
        conv = await conversions_repo.get(s, cid)
        from despereaux.repos.books import get_book

        book = await get_book(s, book_id)
    assert conv.status == ConversionStatus.done
    assert conv.engine == "ocr"
    assert book.epub_export_path is not None
    assert "workdir" in called and not called["workdir"].exists()  # OCR cache cleaned on success


async def test_run_export_scan_skips_without_ocr(tmp_path, monkeypatch):
    monkeypatch.setattr(epub_export, "calibre_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdfmd_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdf_is_scan", lambda src: True)
    monkeypatch.setattr(epub_export, "ocr_available", lambda: False)
    cid = await _make_conv(await _make_pdf_book(tmp_path))
    await epub_export.run_export(cid)
    async with session_scope() as s:
        conv = await conversions_repo.get(s, cid)
    assert conv.status == ConversionStatus.failed
    assert "scanned" in (conv.error or "").lower()


async def test_run_export_ocr_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(epub_export, "calibre_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdfmd_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdf_is_scan", lambda src: True)
    monkeypatch.setattr(epub_export, "ocr_available", lambda: True)
    monkeypatch.setattr(epub_export, "_ollama_reachable", lambda host: False)
    cid = await _make_conv(await _make_pdf_book(tmp_path))
    await epub_export.run_export(cid)
    async with session_scope() as s:
        conv = await conversions_repo.get(s, cid)
    assert conv.status == ConversionStatus.failed
    assert "reachable" in (conv.error or "").lower()


def test_ocr_mostly_failed(tmp_path):
    good = tmp_path / "good.md"
    good.write_text("# T\n<!-- page 1 -->\nReal text here\n<!-- page 2 -->\nMore real text")
    assert epub_export._ocr_mostly_failed(good) is False
    bad = tmp_path / "bad.md"
    bad.write_text(
        "# T\n<!-- page 1 -->\n*(OCR failed on page 1: x)*\n"
        "<!-- page 2 -->\n*(OCR failed on page 2: y)*"
    )
    assert epub_export._ocr_mostly_failed(bad) is True


async def test_poll_ocr_progress_updates_phase(tmp_path, monkeypatch):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "page-001.md").write_text("x")
    (pages / "page-002.md").write_text("x")
    captured: list = []

    async def fake_patch(cid, **f):
        captured.append(f)

    monkeypatch.setattr(epub_export, "_patch", fake_patch)
    stop = asyncio.Event()
    task = asyncio.create_task(epub_export._poll_ocr_progress("cid", pages, 5, stop))
    await asyncio.sleep(0.05)  # let the first iteration's _patch fire
    stop.set()
    await asyncio.gather(task, return_exceptions=True)
    assert captured and captured[0].get("phase") == "OCR page 2/5"
