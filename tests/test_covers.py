"""Unit tests for services/covers.py (Pillow WebP pipeline) — untested until
now despite sitting directly on a dependency that crossed a major version
(Pillow 11 → 12)."""

from __future__ import annotations

import io

from PIL import Image

from despereaux.config import get_settings
from despereaux.services.covers import COVER_WIDTH, cover_dimensions, write_cover


def _png(mode: str, size: tuple[int, int]) -> bytes:
    img = Image.new(mode, size, (200, 80, 40, 255) if mode == "RGBA" else (200, 80, 40))
    if mode == "P":
        img = Image.new("RGB", size, (200, 80, 40)).convert("P")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_rgba_input_produces_webp() -> None:
    out = write_cover("cover-test-rgba", _png("RGBA", (300, 450)))
    assert out is not None and out.exists()
    assert out.suffix == ".webp"
    with Image.open(out) as img:
        assert img.format == "WEBP"
        assert img.size == (300, 450)  # under the cap → untouched


def test_palette_mode_input_converted() -> None:
    out = write_cover("cover-test-palette", _png("P", (100, 150)))
    assert out is not None
    with Image.open(out) as img:
        assert img.format == "WEBP"


def test_wide_image_resized_to_cover_width() -> None:
    out = write_cover("cover-test-wide", _png("RGBA", (1200, 1800)))
    assert out is not None
    with Image.open(out) as img:
        assert img.width == COVER_WIDTH
        assert img.height == 900  # aspect ratio preserved (1800 * 600/1200)


def test_garbage_bytes_returns_none_without_stray_file() -> None:
    out_path = get_settings().covers_dir / "cover-test-garbage.webp"
    assert write_cover("cover-test-garbage", b"definitely not an image") is None
    assert not out_path.exists()


def test_cover_dimensions() -> None:
    assert cover_dimensions(_png("RGBA", (321, 123))) == (321, 123)
    assert cover_dimensions(b"nope") is None
