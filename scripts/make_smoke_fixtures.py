"""Seed sample ebooks for the black-box smoke tier (tests/smoke).

Writes into the target directory (the dir mounted at /ebooks in
docker-compose.ci.yml):

  sample.epub  — reuses scripts/make_sample_epub.py (title "Despereaux Sample")
  sample.pdf   — 3 blank pages + document metadata ("Smoke PDF Sample");
                 exercises in-container pypdf metadata + pypdfium2 cover render
  sample-jpx.pdf — one JPEG 2000 (/JPXDecode) page image ("Smoke JPX Sample");
                 the scanned-book shape, and the only fixture that proves the
                 reader actually PAINTS a page rather than merely loading one

The MOBI fixture is NOT written here: CI generates it inside the container
(`ebook-convert /ebooks/sample.epub /ebooks/sample.mobi`), which doubles as
the Calibre health probe on the current base image.

usage: uv run python scripts/make_smoke_fixtures.py <target-dir>
"""

from __future__ import annotations

import sys
from pathlib import Path

from make_sample_epub import build_sample


def build_sample_pdf(out: Path) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    writer.add_metadata(
        {
            "/Title": "Smoke PDF Sample",
            "/Author": "A. Mouse",
            "/Subject": "Seeded by make_smoke_fixtures.py for the smoke tier.",
            "/Keywords": "smoke; regression",
            "/CreationDate": "D:20260527000000Z",
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        writer.write(f)
    print(f"wrote {out}")


def build_jpx_pdf(out: Path) -> None:
    """A one-page PDF whose only content is a JPEG 2000 (/JPXDecode) image.

    This is the shape every scanned book has — one full-page image per page —
    and it is the shape no other fixture covers: sample.pdf is three BLANK
    vector pages, so a reader that renders nothing at all still passed. PDF.js
    decodes JPX in a WebAssembly OpenJPEG module it fetches from `wasmUrl`, and
    when that URL is missing it only warns, leaving a blank white page. This
    fixture is the regression net for that.
    """
    from io import BytesIO

    from PIL import Image
    from pypdf import PdfWriter
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
        NumberObject,
    )

    px_w, px_h = 240, 320
    img = Image.new("RGB", (px_w, px_h), "white")
    # Deliberately high-contrast and NOT uniform: the check that matters is
    # "did anything paint", so the fixture must be impossible to confuse with a
    # blank page.
    for y in range(px_h):
        for x in range(px_w):
            if (x // 40 + y // 40) % 2 == 0:
                img.putpixel((x, y), (10, 10, 10))
    buf = BytesIO()
    img.save(buf, format="JPEG2000", irreversible=False)
    jp2 = buf.getvalue()

    writer = PdfWriter()
    page = writer.add_blank_page(width=px_w, height=px_h)

    image = DecodedStreamObject()
    image.set_data(jp2)
    image[NameObject("/Type")] = NameObject("/XObject")
    image[NameObject("/Subtype")] = NameObject("/Image")
    image[NameObject("/Width")] = NumberObject(px_w)
    image[NameObject("/Height")] = NumberObject(px_h)
    # No /ColorSpace or /BitsPerComponent: for JPXDecode both come from the
    # JPEG 2000 codestream itself (PDF 32000-1 7.4.9).
    image[NameObject("/Filter")] = NameObject("/JPXDecode")
    image_ref = writer._add_object(image)

    content = DecodedStreamObject()
    content.set_data(f"q {px_w} 0 0 {px_h} 0 0 cm /Im0 Do Q".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/ProcSet"): ArrayObject([NameObject("/PDF"), NameObject("/ImageC")]),
            NameObject("/XObject"): DictionaryObject({NameObject("/Im0"): image_ref}),
        }
    )

    writer.add_metadata(
        {
            "/Title": "Smoke JPX Sample",
            "/Author": "A. Mouse",
            "/Subject": "JPEG 2000 page image; proves PDF.js can reach its OpenJPEG wasm.",
            "/CreationDate": "D:20260904000000Z",
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("wb") as f:
        writer.write(f)
    print(f"wrote {out}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".smoke/ebooks")
    target.mkdir(parents=True, exist_ok=True)
    build_sample(target / "sample.epub")
    build_sample_pdf(target / "sample.pdf")
    build_jpx_pdf(target / "sample-jpx.pdf")
