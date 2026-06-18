"""Tests for the optional tomeforge conversion sidecar client + its wiring into
run_export. No real sidecar runs — httpx.MockTransport drives the client, and the
run_export test stubs the client to confirm the PDF path is offloaded when
DESPEREAUX_TOMEFORGE_HOST is set."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from ebooklib import epub

from despereaux.config import get_settings, tomeforge_available
from despereaux.db import session_scope
from despereaux.models import Book, ConversionStatus
from despereaux.models.base import new_id
from despereaux.repos import conversions as conversions_repo
from despereaux.repos.books import get_book
from despereaux.services import epub_export, tomeforge_client


def _mock_sidecar(*, statuses, result_body=b"PK\x03\x04 epub", fail_error=None):
    """Build an httpx.MockTransport simulating a sidecar. `statuses` is the sequence
    of status payloads returned by successive GET /jobs/{id} calls."""
    seq = iter(statuses)
    last = {"v": statuses[-1]}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/convert":
            return httpx.Response(200, json={"job_id": "j1", "status": "queued"})
        if request.method == "GET" and path == "/jobs/j1":
            body = next(seq, last["v"])
            return httpx.Response(200, json=body)
        if request.method == "GET" and path == "/jobs/j1/result":
            return httpx.Response(200, content=result_body)
        if request.method == "DELETE" and path == "/jobs/j1":
            return httpx.Response(200, json={"deleted": "j1"})
        return httpx.Response(404, json={"detail": "nope"})

    return httpx.MockTransport(handler)


async def test_convert_pdf_happy_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tomeforge_client, "_POLL_INTERVAL", 0)
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF-1.4 ...")
    out = tmp_path / "out.epub"

    phases: list[str] = []
    transport = _mock_sidecar(statuses=[
        {"status": "running", "phase": "OCR page 1/3"},
        {"status": "running", "phase": "OCR page 3/3"},
        {"status": "done", "engine": "ocr", "scanned": True},
    ])

    result = await tomeforge_client.convert_pdf(
        "http://tomeforge:8400", src, out,
        overall_timeout=60, on_phase=lambda p: _record(phases, p), transport=transport,
    )

    assert out.read_bytes().startswith(b"PK")
    assert result.engine == "ocr" and result.scanned is True
    assert phases == ["OCR page 1/3", "OCR page 3/3"]  # de-duped, forwarded in order


async def test_convert_pdf_failed_job_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(tomeforge_client, "_POLL_INTERVAL", 0)
    src = tmp_path / "in.pdf"
    src.write_bytes(b"%PDF")
    transport = _mock_sidecar(statuses=[{"status": "failed", "error": "OCR ran out of memory"}])

    with pytest.raises(tomeforge_client.SidecarError, match="out of memory"):
        await tomeforge_client.convert_pdf(
            "http://tomeforge:8400", src, tmp_path / "out.epub",
            overall_timeout=60, transport=transport,
        )
    assert not (tmp_path / "out.epub").exists()


async def test_convert_pdf_unreachable_raises(tmp_path):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(tomeforge_client.SidecarError, match="not reachable"):
        await tomeforge_client.convert_pdf(
            "http://tomeforge:8400", _pdf(tmp_path), tmp_path / "o.epub",
            overall_timeout=60, transport=httpx.MockTransport(boom),
        )


def test_tomeforge_available_reflects_setting(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "tomeforge_host", None)
    assert tomeforge_available() is False
    monkeypatch.setattr(s, "tomeforge_host", "http://tomeforge:8400")
    assert tomeforge_available() is True


async def test_run_export_uses_sidecar_when_configured(tmp_path, monkeypatch):
    """End-to-end: with a sidecar configured, run_export offloads the PDF, the
    in-process pdf2md is never touched, and the job finishes 'done' with the
    sidecar's engine and the EPUB promoted to the book's primary."""
    monkeypatch.setattr(epub_export, "tomeforge_available", lambda: True)
    monkeypatch.setattr(epub_export, "calibre_available", lambda: True)
    monkeypatch.setattr(epub_export, "_pdfmd_available", lambda: (_ for _ in ()).throw(
        AssertionError("in-process pdf2md must NOT run when the sidecar is configured")
    ))

    src = tmp_path / "book.pdf"
    src.write_bytes(b"%PDF-1.4 ...")

    called = {"sidecar": False}

    # Stub the network call, not the orchestration helper, so the REAL
    # _convert_pdf_via_sidecar runs (and patches phase + engine on the row).
    async def fake_convert_pdf(host, src_, out, **kw):
        called["sidecar"] = True
        on_phase = kw.get("on_phase")
        if on_phase:
            await on_phase("OCR page 1/1")
        _write_min_epub(out, title="Converted")  # a real, readable EPUB
        return tomeforge_client.SidecarResult(engine="heuristic", scanned=False)

    monkeypatch.setattr(tomeforge_client, "convert_pdf", fake_convert_pdf)

    async with session_scope() as s:
        book = Book(
            id=new_id(), title="T", sort_title="t", format="pdf", library="Default",
            file_path=str(src), file_size=10, file_mtime=datetime.now(UTC),
            file_hash=new_id().replace("-", ""),
        )
        s.add(book)
        await s.flush()
        conv = await conversions_repo.create(
            s, book_id=book.id, requested_by="u1", source_hash=book.file_hash
        )
        book_id, cid = book.id, conv.id

    await epub_export.run_export(cid)

    assert called["sidecar"] is True
    async with session_scope() as s:
        row = await conversions_repo.get(s, cid)
        assert row.status == ConversionStatus.done
        assert row.engine == "heuristic"
        refreshed = await get_book(s, book_id)
        assert refreshed.epub_export_path and Path(refreshed.epub_export_path).exists()


def _write_min_epub(path, *, title="X", ident="id-x") -> None:
    book = epub.EpubBook()
    book.set_identifier(ident)
    book.set_title(title)
    book.set_language("en")
    c = epub.EpubHtml(title="C", file_name="c.xhtml")
    c.content = f"<html><body><h1>{title}</h1><p>body text here</p></body></html>"
    book.add_item(c)
    book.spine = [c]
    book.toc = (epub.Link("c.xhtml", "C", "c"),)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def _pdf(tmp_path) -> Path:
    p = tmp_path / "in.pdf"
    p.write_bytes(b"%PDF")
    return p


async def _record(bucket: list[str], phase: str) -> None:
    bucket.append(phase)
