"""Calibre-backed ebook converter. Currently used for MOBI/AZW/AZW3 -> EPUB
so the in-browser reader (epub.js) can render them.

`ebook-convert` is a Calibre CLI that's baked into the Docker image
(Dockerfile installs the calibre apt package). The converter is bounded
by a process-wide semaphore and a per-file timeout.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

log = logging.getLogger(__name__)

# Limit concurrent conversions so a big scan doesn't fork dozens of calibre
# instances at once. Calibre is single-threaded per process but heavyweight.
_CONVERSION_LOCK = asyncio.Semaphore(2)

# Per-file timeout. Calibre typically takes 2-15s for novels; cap at 5 min
# to avoid hanging on broken sources.
_TIMEOUT_SECONDS = 300

_CONVERT_BIN = shutil.which("ebook-convert") or "ebook-convert"


def calibre_available() -> bool:
    return shutil.which("ebook-convert") is not None


async def convert_to_epub(
    src: Path,
    out: Path,
    *,
    extra_args: Sequence[str] = (),
    overwrite: bool = False,
    timeout: int = _TIMEOUT_SECONDS,
) -> Path | None:
    """Convert `src` to an EPUB at `out`. Returns the output path on success,
    None on failure (logs the error). Idempotent: if `out` already exists
    and is non-empty, returns it without re-converting (unless `overwrite`).

    `extra_args` are appended verbatim to the `ebook-convert` command after the
    output path — used by the high-quality export pipeline to pass heuristics +
    TOC flags. `timeout` overrides the per-file cap (exports use a larger value
    than the MOBI ingest path since big rulebook PDFs can run long)."""
    if not overwrite and out.exists() and out.stat().st_size > 0:
        return out
    if not calibre_available():
        log.warning("ebook-convert not found on PATH; cannot convert %s", src.name)
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    # Important: keep the .epub extension on the temp file so calibre's
    # `ebook-convert` infers EPUB as the output format. (Adding a .tmp suffix
    # breaks format detection -> "No plugin to handle output format: tmp".)
    tmp = out.with_name(out.stem + ".converting.epub")
    if tmp.exists():
        tmp.unlink()

    async with _CONVERSION_LOCK:
        log.info("converting %s -> %s via ebook-convert", src.name, out.name)
        try:
            proc = await asyncio.create_subprocess_exec(
                _CONVERT_BIN,
                str(src),
                str(tmp),
                "--no-default-epub-cover",
                *extra_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except TimeoutError:
                proc.kill()
                await proc.wait()
                log.warning("ebook-convert timed out after %ds on %s", timeout, src.name)
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                return None
            if proc.returncode != 0:
                log.warning(
                    "ebook-convert failed (exit %d) for %s: %s",
                    proc.returncode,
                    src.name,
                    (stderr or b"").decode(errors="replace")[-400:],
                )
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                return None
        except FileNotFoundError:
            log.error("ebook-convert binary not found; install calibre in the container")
            return None
        except Exception as e:
            log.exception("conversion subprocess crashed: %s", e)
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            return None

    # Atomic rename so partial writes never look complete to a watcher.
    tmp.replace(out)
    return out
