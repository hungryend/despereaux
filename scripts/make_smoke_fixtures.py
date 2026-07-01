"""Seed sample ebooks for the black-box smoke tier (tests/smoke).

Writes into the target directory (the dir mounted at /ebooks in
docker-compose.ci.yml):

  sample.epub  — reuses scripts/make_sample_epub.py (title "Despereaux Sample")
  sample.pdf   — 3 blank pages + document metadata ("Smoke PDF Sample");
                 exercises in-container pypdf metadata + pypdfium2 cover render

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


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".smoke/ebooks")
    target.mkdir(parents=True, exist_ok=True)
    build_sample(target / "sample.epub")
    build_sample_pdf(target / "sample.pdf")
