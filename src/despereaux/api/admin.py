from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends

from despereaux.middleware.auth import require_admin
from despereaux.services.scanner import scanner

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
