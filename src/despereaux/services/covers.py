"""Cover thumbnail generation."""

from __future__ import annotations

import contextlib
import io
import logging
from pathlib import Path

from PIL import Image

from despereaux.config import get_settings

log = logging.getLogger(__name__)

COVER_WIDTH = 600
COVER_QUALITY = 82


def write_cover(book_id: str, cover_bytes: bytes) -> Path | None:
    settings = get_settings()
    out = settings.covers_dir / f"{book_id}.webp"
    try:
        img = Image.open(io.BytesIO(cover_bytes))
        img.load()
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        if img.width > COVER_WIDTH:
            ratio = COVER_WIDTH / img.width
            new_h = int(img.height * ratio)
            img = img.resize((COVER_WIDTH, new_h), Image.LANCZOS)
        img.save(out, format="WEBP", quality=COVER_QUALITY, method=6)
        return out
    except Exception as e:
        log.warning("cover write failed for %s: %s", book_id, e)
        if out.exists():
            with contextlib.suppress(OSError):
                out.unlink()
        return None


def cover_dimensions(cover_bytes: bytes) -> tuple[int, int] | None:
    try:
        img = Image.open(io.BytesIO(cover_bytes))
        return img.size
    except Exception:
        return None
