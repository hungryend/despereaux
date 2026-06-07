"""CBZ/CBR comic archive access.

A comic file is just an archive of page images, read on demand:
  .cbz -> zip  (stdlib zipfile)
  .cbr -> rar  (rarfile + the `unar` binary baked into the image)

Pages are the image entries, natural-sorted so "p2" precedes "p10".
"""

from __future__ import annotations

import contextlib
import re
import zipfile
from pathlib import Path

import rarfile  # provided by the `formats` extra (pyproject)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".avif"}

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".avif": "image/avif",
}


def _natural_key(name: str) -> list[object]:
    # Split into digit / non-digit runs so page ordering is numeric, not lexical.
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _is_page_image(name: str) -> bool:
    # Skip directories, dotfiles, and macOS archive junk (__MACOSX/, ._foo).
    base = name.rsplit("/", 1)[-1]
    if not base or base.startswith(".") or name.startswith("__MACOSX"):
        return False
    return Path(name).suffix.lower() in IMAGE_EXTS


def content_type_for(name: str) -> str:
    return _CONTENT_TYPES.get(Path(name).suffix.lower(), "application/octet-stream")


class _Archive:
    """Uniform read access over a CBZ (zip) or CBR (rar)."""

    def __init__(self, path: Path):
        ext = path.suffix.lower()
        if ext == ".cbz":
            self._zf: zipfile.ZipFile | rarfile.RarFile = zipfile.ZipFile(path)
        elif ext == ".cbr":
            # rarfile auto-detects an available backend (we ship `unar`).
            self._zf = rarfile.RarFile(path)
        else:
            raise ValueError(f"not a comic archive: {path}")
        self.pages: list[str] = sorted(
            (n for n in self._zf.namelist() if _is_page_image(n)), key=_natural_key
        )

    def read(self, index: int) -> bytes:
        return self._zf.read(self.pages[index])

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._zf.close()

    def __enter__(self) -> _Archive:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def list_pages(path: Path) -> list[str]:
    with _Archive(path) as a:
        return list(a.pages)


def page_count(path: Path) -> int:
    with _Archive(path) as a:
        return len(a.pages)


def read_page(path: Path, index: int) -> tuple[bytes, str] | None:
    """(bytes, content_type) for the nth page (0-based), or None if out of range."""
    with _Archive(path) as a:
        if index < 0 or index >= len(a.pages):
            return None
        return a.read(index), content_type_for(a.pages[index])


def first_page_bytes(path: Path) -> bytes | None:
    with _Archive(path) as a:
        return a.read(0) if a.pages else None
