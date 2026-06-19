"""Unit tests for the convert-to-EPUB internals that don't need Calibre:

- the ebooklib linked-TOC guarantee (rebuild from headings, anchors resolve)
- image counting, text length, scan heuristic, and the can_convert gate

The Calibre subprocess is exercised manually; the PDF path (tomeforge sidecar)
is covered in tests/test_tomeforge_sidecar.py. Here we validate the EPUB
post-processing that's easy to get wrong.
"""

from __future__ import annotations

import io

import ebooklib
import lxml.html
from ebooklib import epub

from despereaux.services import epub_export

# --------------------------------------------------------------------------- #
# ebooklib TOC guarantee
# --------------------------------------------------------------------------- #


def _make_epub(path, *, body_html, with_image=False):
    book = epub.EpubBook()
    book.set_identifier("id-1")
    book.set_title("T")
    book.set_language("en")
    c1 = epub.EpubHtml(title="C1", file_name="c1.xhtml", lang="en")
    c1.content = f"<html><body>{body_html}</body></html>"
    book.add_item(c1)
    if with_image:
        buf = io.BytesIO()
        from PIL import Image

        Image.new("RGB", (4, 4), (10, 20, 30)).save(buf, format="PNG")
        book.add_item(
            epub.EpubImage(
                uid="img1", file_name="img1.png", media_type="image/png", content=buf.getvalue()
            )
        )
    book.toc = ()
    book.spine = [c1]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(path), book)


def _flatten_links(toc):
    out = []
    for it in toc if isinstance(toc, (list, tuple)) else [toc]:
        if isinstance(it, epub.Link):
            out.append(it)
        elif isinstance(it, (list, tuple)):
            out.extend(_flatten_links(it))
    return out


def _assert_toc_linked(path):
    """Every TOC link resolves to a real document, and each #anchor exists."""
    book = epub.read_epub(str(path))
    docs = {it.file_name: it.get_content() for it in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)}
    links = _flatten_links(book.toc)
    assert links, "expected at least one TOC link"
    for link in links:
        file, _, anchor = link.href.partition("#")
        matches = [fn for fn in docs if fn == file or fn.endswith("/" + file) or file.endswith(fn)]
        assert matches, f"href {link.href!r} not among {list(docs)}"
        if anchor:
            tree = lxml.html.fromstring(docs[matches[0]])
            ids = {el.get("id") for el in tree.iter() if el.get("id")}
            assert anchor in ids, f"anchor {anchor!r} missing in {matches[0]}"


def test_ensure_linked_toc_builds_from_xhtml_headings(tmp_path):
    out = tmp_path / "b.epub"
    _make_epub(out, body_html="<h1>Intro</h1><p>x</p><h2>Details</h2><p>y</p>")
    count, source = epub_export._ensure_linked_toc(out)
    assert count >= 2
    assert source == "heading-detect"
    _assert_toc_linked(out)


def test_ensure_linked_toc_keeps_a_good_existing_toc(tmp_path):
    out = tmp_path / "c.epub"
    book = epub.EpubBook()
    book.set_identifier("x")
    book.set_title("X")
    book.set_language("en")
    c1 = epub.EpubHtml(title="A", file_name="a.xhtml")
    c1.content = "<html><body><h1 id='a'>A</h1></body></html>"
    c2 = epub.EpubHtml(title="B", file_name="b.xhtml")
    c2.content = "<html><body><h1 id='b'>B</h1></body></html>"
    book.add_item(c1)
    book.add_item(c2)
    book.toc = (epub.Link("a.xhtml#a", "A", "a"), epub.Link("b.xhtml#b", "B", "b"))
    book.spine = [c1, c2]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(out), book)

    count, source = epub_export._ensure_linked_toc(out)
    assert count >= 2
    assert source == "calibre"  # left Calibre's TOC alone


def test_count_images(tmp_path):
    out = tmp_path / "img.epub"
    _make_epub(out, body_html="<p>a</p><img src='img1.png'/><p>b</p>", with_image=True)
    assert epub_export._count_images(out) >= 1


def test_is_valid_epub(tmp_path):
    good = tmp_path / "good.epub"
    _make_epub(good, body_html="<h1>X</h1><p>y</p>")
    assert epub_export._is_valid_epub(good) is True
    bad = tmp_path / "bad.epub"
    bad.write_bytes(b"definitely not a zip / not an epub")
    assert epub_export._is_valid_epub(bad) is False


def test_extract_text_len(tmp_path):
    text_epub = tmp_path / "t.epub"
    _make_epub(text_epub, body_html="<h1>Chapter</h1><p>" + ("word " * 200) + "</p>")
    assert epub_export._extract_text_len(text_epub) > 500
    img_epub = tmp_path / "i.epub"
    _make_epub(img_epub, body_html="<p><img src='img1.png'/></p>", with_image=True)
    assert epub_export._extract_text_len(img_epub) < 50


def test_looks_like_scanned_pdf():
    assert epub_export._looks_like_scanned_pdf("pdf", 0, 66) is True  # image dump
    assert epub_export._looks_like_scanned_pdf("pdf", 5000, 10) is False  # real text
    assert epub_export._looks_like_scanned_pdf("mobi", 0, 5) is False  # only guard PDFs
    assert epub_export._looks_like_scanned_pdf("pdf", 100, 0) is False  # no images


def test_can_convert_gate(monkeypatch):
    # Convert is gated on the tomeforge sidecar (conftest configures one by default).
    assert epub_export.can_convert("pdf")
    assert epub_export.can_convert("mobi")
    assert not epub_export.can_convert("epub")
    assert not epub_export.can_convert("cbz")

    # Without a sidecar configured, NOTHING is convertible — the button is hidden.
    monkeypatch.setattr(epub_export, "tomeforge_available", lambda: False)
    assert not epub_export.can_convert("pdf")
    assert not epub_export.can_convert("mobi")
