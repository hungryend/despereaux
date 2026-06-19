"""Client for an optional tomeforge conversion sidecar (DESPEREAUX_TOMEFORGE_HOST).

When configured, the PDF→Markdown→EPUB step (the heavyweight PyMuPDF + optional
Ollama-OCR work) is offloaded to a tomeforge HTTP service instead of running
in-process — so this image can ship without the AGPL `pdf` extra and still convert
PDFs. The sidecar returns a plain EPUB; despereaux still runs its own TOC guarantee,
validation, and scanned-PDF backstop on top (see ``epub_export``).

Flow: POST the file → poll the job (forwarding its ``phase``, incl. ``OCR page N/M``)
→ stream the resulting EPUB to ``out`` → delete the job. Everything degrades to a
``SidecarError`` the caller records as a failed conversion.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# How often to poll the job + a short connect/read budget for the small JSON calls.
_POLL_INTERVAL = 2.0
_STATUS_TIMEOUT = httpx.Timeout(30.0)

PhaseCallback = Callable[[str], Awaitable[None]]


class SidecarError(RuntimeError):
    """Any failure talking to the sidecar or a job it reports as failed."""


@dataclass
class SidecarResult:
    engine: str | None  # 'heuristic' | 'ocr' | 'calibre' (as the sidecar reports)
    scanned: bool


async def convert_pdf(
    host: str,
    src: Path,
    out: Path,
    *,
    overall_timeout: int,
    ocr: str = "auto",
    ollama_host: str | None = None,
    model: str = "deepseek-ocr:3b",
    dpi: int = 150,
    ocr_timeout: int = 600,
    num_ctx: int = 8192,
    on_phase: PhaseCallback | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SidecarResult:
    """Convert ``src`` to an EPUB at ``out`` via the tomeforge sidecar at ``host``.

    Raises :class:`SidecarError` on any unreachable host, failed job, or timeout.
    ``transport`` is a test seam (e.g. ``httpx.MockTransport``).
    """
    base = host.rstrip("/")
    deadline = asyncio.get_event_loop().time() + overall_timeout

    async with httpx.AsyncClient(
        base_url=base, timeout=_STATUS_TIMEOUT, transport=transport
    ) as client:
        job_id = await _submit(client, src, ocr, ollama_host, model, dpi, ocr_timeout, num_ctx)
        try:
            result = await _await_job(client, job_id, deadline, on_phase)
            await _download(client, job_id, out, deadline)
            return result
        finally:
            # Best-effort cleanup; the sidecar also reaps on restart.
            with contextlib.suppress(Exception):
                await client.delete(f"/jobs/{job_id}")


async def _submit(
    client: httpx.AsyncClient,
    src: Path,
    ocr: str,
    ollama_host: str | None,
    model: str,
    dpi: int,
    ocr_timeout: int,
    num_ctx: int,
) -> str:
    data = {
        "ocr": ocr,
        "model": model,
        "dpi": str(dpi),
        "ocr_timeout": str(ocr_timeout),
        "num_ctx": str(num_ctx),
    }
    if ollama_host:
        data["ollama_host"] = ollama_host
    try:
        with src.open("rb") as fh:
            files = {"file": (src.name, fh, "application/octet-stream")}
            resp = await client.post("/convert", data=data, files=files)
    except httpx.HTTPError as e:
        raise SidecarError(f"tomeforge sidecar not reachable: {e}") from e
    if resp.status_code != 200:
        raise SidecarError(
            f"tomeforge rejected the job (HTTP {resp.status_code}): {resp.text[:300]}"
        )
    job_id = resp.json().get("job_id")
    if not job_id:
        raise SidecarError("tomeforge did not return a job id")
    return job_id


async def _await_job(
    client: httpx.AsyncClient,
    job_id: str,
    deadline: float,
    on_phase: PhaseCallback | None,
) -> SidecarResult:
    last_phase: str | None = None
    while True:
        if asyncio.get_event_loop().time() > deadline:
            raise SidecarError("tomeforge conversion timed out")
        try:
            resp = await client.get(f"/jobs/{job_id}")
        except httpx.HTTPError as e:
            raise SidecarError(f"lost contact with tomeforge: {e}") from e
        if resp.status_code != 200:
            raise SidecarError(f"tomeforge job vanished (HTTP {resp.status_code})")
        body = resp.json()
        status = body.get("status")
        phase = body.get("phase")
        if on_phase and phase and phase != last_phase:
            last_phase = phase
            await on_phase(phase)
        if status == "done":
            return SidecarResult(engine=body.get("engine"), scanned=bool(body.get("scanned")))
        if status == "failed":
            raise SidecarError(body.get("error") or "tomeforge conversion failed")
        await asyncio.sleep(_POLL_INTERVAL)


async def _download(client: httpx.AsyncClient, job_id: str, out: Path, deadline: float) -> None:
    remaining = max(30.0, deadline - asyncio.get_event_loop().time())
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    try:
        async with client.stream(
            "GET", f"/jobs/{job_id}/result", timeout=httpx.Timeout(remaining)
        ) as resp:
            if resp.status_code != 200:
                await resp.aread()
                raise SidecarError(f"could not fetch the converted EPUB (HTTP {resp.status_code})")
            with tmp.open("wb") as fh:
                async for chunk in resp.aiter_bytes():
                    fh.write(chunk)
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise SidecarError(f"failed downloading the converted EPUB: {e}") from e
    if not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        raise SidecarError("tomeforge returned an empty EPUB")
    tmp.replace(out)  # atomic — `out` is never left half-written
