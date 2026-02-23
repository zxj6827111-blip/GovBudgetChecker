"""Health and readiness endpoints."""

from __future__ import annotations

import os
import time
from typing import Any, Dict

from fastapi import APIRouter

from api import runtime

router = APIRouter()


@router.get("/health")
@router.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": runtime.APP_TITLE, "ts": time.time()}


@router.get("/ready")
@router.get("/api/ready")
async def ready() -> Dict[str, Any]:
    checks = {
        "upload_root_exists": runtime.UPLOAD_ROOT.exists(),
        "upload_root_writable": os.access(runtime.UPLOAD_ROOT, os.W_OK),
    }
    ready_state = all(checks.values())
    return {
        "status": "ready" if ready_state else "not_ready",
        "checks": checks,
        "ts": time.time(),
    }

