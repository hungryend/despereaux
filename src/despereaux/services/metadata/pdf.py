"""PDF metadata + cover extraction.

- Metadata via `pypdf`: title, author, page count (exact), outline.
- Cover via `pypdfium2`: renders page 1 to PNG.

Both libraries are pure-Python wheels (no Poppler/system deps) and ship via
the `formats` extra in pyproject.toml.
"""

from __future__ import annotations

import io
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# pypdf 5.x emits deprecation warnings about metadata access patterns we use.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="pypdf")


@dataclass
class PdfMetadata:
    title: str
    authors: list[str] = field(default_factory=list)
    publisher: str | None = None
    published_date: str | None = None
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    page_count: int = 0
    cover_bytes: bytes | None = None
    series: tuple[str, float] | None = None
    isbn: str | None = None
    language: str | None = None


# Cover render scale. write_cover() resizes to 600px wide regardless, so 1.0
# is plenty for a sharp thumbnail and keeps memory bounded — page 1 of a big
# rulebook at scale=1.7 was blowing the container's 1 GB cgroup limit.
COVER_SCALE = 1.0

# Skip cover rendering for files over this size — the embedded raster
# images on page 1 of huge rulebooks can transiently allocate gigabytes
# inside pdfium even at scale=1.0.
COVER_MAX_FILE_BYTES = 200 * 1024 * 1024  # 200 MB


def _render_cover(path: Path) -> bytes | None:
    """Render page 1 of the PDF to JPEG bytes via pypdfium2.

    Returns None on:
      - pypdfium2 not installed
      - file > COVER_MAX_FILE_BYTES (large PDFs can OOM the process during render)
      - pdfium open / render failure
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size > COVER_MAX_FILE_BYTES:
        log.info(
            "skipping cover render for %s (%d MB > limit %d MB)",
            path.name,
            size // (1024 * 1024),
            COVER_MAX_FILE_BYTES // (1024 * 1024),
        )
        return None

    try:
        import pypdfium2 as pdfium
    except ImportError:
        log.warning("pypdfium2 not installed; PDF covers disabled")
        return None

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as e:
        log.warning("pypdfium2 failed to open %s: %s", path.name, e)
        return None

    try:
        if len(pdf) == 0:
            return None
        page = pdf[0]
        bitmap = page.render(scale=COVER_SCALE)
        pil = bitmap.to_pil()
        # Drop alpha channel if present so JPEG (smaller than PNG) is valid.
        if pil.mode != "RGB":
            pil = pil.convert("RGB")
        buf = io.BytesIO()
        pil.save(buf, format="JPEG", quality=85, optimize=True)
        return buf.getvalue()
    except Exception as e:
        log.warning("PDF cover render failed for %s: %s", path.name, e)
        return None
    finally:
        with _suppress():
            pdf.close()


class _suppress:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return True


def read_pdf_metadata(path: Path) -> PdfMetadata:
    from pypdf import PdfReader

    reader = PdfReader(str(path), strict=False)
    info = reader.metadata or {}

    # Both / and lowercase keys appear depending on producer.
    def _get(*keys: str) -> str | None:
        for k in keys:
            v = info.get(k)
            if v:
                return str(v).strip()
        return None

    title = _get("/Title", "title") or path.stem
    author_raw = _get("/Author", "author")
    authors: list[str] = []
    if author_raw:
        # PDF authors are often "Foo Bar; Baz Qux" or comma-separated.
        for sep in (";", ","):
            if sep in author_raw:
                authors = [a.strip() for a in author_raw.split(sep) if a.strip()]
                break
        if not authors:
            authors = [author_raw]

    subject = _get("/Subject", "subject")
    keywords = _get("/Keywords", "keywords")
    tags: list[str] = []
    if keywords:
        for sep in (";", ","):
            if sep in keywords:
                tags = [k.strip() for k in keywords.split(sep) if k.strip()]
                break
        if not tags:
            tags = [keywords]

    creation = _get("/CreationDate", "creation_date")
    # Normalise "D:20231005..." -> "2023-10-05" loosely; metadata_lookup will refine.
    pub_date: str | None = None
    if creation and creation.startswith("D:") and len(creation) >= 10:
        y = creation[2:6]
        m = creation[6:8]
        d = creation[8:10]
        if y.isdigit() and m.isdigit() and d.isdigit():
            pub_date = f"{y}-{m}-{d}"

    try:
        page_count = len(reader.pages)
    except Exception:
        page_count = 0

    cover = _render_cover(path)

    return PdfMetadata(
        title=title,
        authors=authors,
        publisher=_get("/Producer", "producer"),
        published_date=pub_date,
        description=subject,
        tags=tags,
        page_count=page_count,
        cover_bytes=cover,
    )
