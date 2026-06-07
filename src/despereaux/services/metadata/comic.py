"""Comic (CBZ/CBR) metadata + cover.

Title/series/author come from an embedded ComicInfo.xml when present (common in
CBZ), else the filename. Cover = the first page image; page count = image count.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from despereaux.services.comic import first_page_bytes, page_count

log = logging.getLogger(__name__)


@dataclass
class ComicMetadata:
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


def _read_comicinfo(path: Path) -> dict[str, str]:
    """Pull a few fields from ComicInfo.xml if a CBZ has one. (CBR: skipped.)"""
    if path.suffix.lower() != ".cbz":
        return {}
    try:
        with zipfile.ZipFile(path) as zf:
            name = next(
                (n for n in zf.namelist() if n.lower().endswith("comicinfo.xml")), None
            )
            if not name:
                return {}
            root = ET.fromstring(zf.read(name))
    except Exception as e:
        log.debug("ComicInfo parse failed for %s: %s", path.name, e)
        return {}
    out: dict[str, str] = {}
    for tag in (
        "Title", "Series", "Number", "Writer", "Publisher", "Year", "Summary", "LanguageISO",
    ):
        el = root.find(tag)
        if el is not None and el.text and el.text.strip():
            out[tag] = el.text.strip()
    return out


def read_comic_metadata(path: Path) -> ComicMetadata:
    info = _read_comicinfo(path)

    series: tuple[str, float] | None = None
    if info.get("Series"):
        try:
            num = float(info["Number"]) if info.get("Number") else 0.0
        except ValueError:
            num = 0.0
        series = (info["Series"], num)

    title = info.get("Title")
    if not title and series:
        n = series[1]
        title = f"{series[0]} #{int(n) if n == int(n) else n}"
    title = title or path.stem

    try:
        pc = page_count(path)
    except Exception as e:
        log.warning("comic page count failed for %s: %s", path.name, e)
        pc = 0
    try:
        cover = first_page_bytes(path)
    except Exception as e:
        log.warning("comic cover read failed for %s: %s", path.name, e)
        cover = None

    return ComicMetadata(
        title=title,
        authors=[info["Writer"]] if info.get("Writer") else [],
        publisher=info.get("Publisher"),
        published_date=info.get("Year"),
        description=info.get("Summary"),
        page_count=pc,
        cover_bytes=cover,
        series=series,
        language=info.get("LanguageISO"),
    )
