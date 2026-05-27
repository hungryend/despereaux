"""Generate a small synthetic EPUB for local testing.

    uv run python scripts/make_sample_epub.py data/ebooks/sample.epub
"""

from __future__ import annotations

import sys
from pathlib import Path

from ebooklib import epub


def build_sample(out: Path, title: str = "Despereaux Sample") -> None:
    book = epub.EpubBook()
    book.set_identifier("urn:isbn:9780000000001")
    book.set_title(title)
    book.set_language("en")
    book.add_author("A. Mouse")
    book.add_metadata("DC", "publisher", "Test Press")
    book.add_metadata("DC", "date", "2026-05-27")
    book.add_metadata(
        "DC",
        "description",
        "A short sample EPUB used to verify ingest + the reader. Has three chapters and a tiny TOC.",
    )

    chapters = []
    for i, name in enumerate(["The Burrow", "The Library", "The Threshold"], start=1):
        c = epub.EpubHtml(title=name, file_name=f"chap_{i:02d}.xhtml", lang="en")
        body = f"<h1>Chapter {i}: {name}</h1>" + (
            "<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "Maecenas euismod, libero ut aliquam vehicula, magna ipsum egestas leo, "
            "id porta neque dolor non urna. Etiam id mi a tortor scelerisque tincidunt.</p>"
        ) * 8
        c.content = f"<html><head><title>{name}</title></head><body>{body}</body></html>"
        book.add_item(c)
        chapters.append(c)

    book.toc = tuple(epub.Link(c.file_name, c.title, f"chap{i}") for i, c in enumerate(chapters))
    book.spine = ["nav", *chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    out.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out), book)
    print(f"wrote {out}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/ebooks/sample.epub")
    build_sample(target)
