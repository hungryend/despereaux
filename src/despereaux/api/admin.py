from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from despereaux.config import get_settings
from despereaux.middleware.auth import require_admin
from despereaux.services.scanner import scanner

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/scan")
async def trigger_scan(
    background: BackgroundTasks,
    _admin=Depends(require_admin),
):
    background.add_task(scanner.run_once)
    return {"status": "queued"}


@router.get("/scan/status")
async def scan_status(_admin=Depends(require_admin)):
    return {"last_result": scanner.last_result}


class SyncRequest(BaseModel):
    paths: list[str] = Field(
        default_factory=list,
        description="Absolute paths inside the container (e.g. /ebooks/Author/Book.epub). "
        "If empty, a full library scan is queued instead.",
    )


def _require_webhook_token(authorization: str | None) -> None:
    settings = get_settings()
    expected = settings.webhook_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="webhook disabled: set DESPEREAUX_WEBHOOK_TOKEN to enable",
        )
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    provided = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="invalid token")


@router.post("/sync")
async def webhook_sync(
    body: SyncRequest | None,
    background: BackgroundTasks,
    authorization: str | None = Header(default=None),
):
    """Token-authed webhook for chaptarr / Readarr / external import pipelines.

    Two modes:
      - With `paths`: ingest just those files (fast path, no full scan).
      - Without `paths`: queue a full library scan (chaptarr's coarse mode).

    Caller passes `Authorization: Bearer <DESPEREAUX_WEBHOOK_TOKEN>`.
    """
    _require_webhook_token(authorization)

    if body is None or not body.paths:
        background.add_task(scanner.run_once)
        return {"status": "queued", "mode": "full_scan"}

    paths = [Path(p) for p in body.paths]
    background.add_task(scanner.ingest_paths, paths)
    log.info("sync webhook accepted: %d paths", len(paths))
    return {"status": "queued", "mode": "targeted", "count": len(paths)}
