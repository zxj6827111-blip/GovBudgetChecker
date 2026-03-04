"""Configuration endpoint."""

from __future__ import annotations

import os

from fastapi import APIRouter

from api import runtime
from api import queue_runtime

router = APIRouter()


@router.get("/config")
@router.get("/api/config")
async def get_config():
    ai_enabled = os.getenv("AI_ASSIST_ENABLED", "true").lower() == "true"
    ai_extractor_url = os.getenv("AI_EXTRACTOR_URL", "http://127.0.0.1:9009/ai/extract/v1")
    queue_enabled = queue_runtime.queue_enabled()
    queue_role = queue_runtime.get_queue_role()
    return {
        "ai_enabled": ai_enabled,
        "ai_assist_enabled": ai_enabled,
        "ai_extractor_url": ai_extractor_url,
        "auth_enabled": bool(runtime.security_config.enabled) if runtime.security_config else False,
        "queue_enabled": queue_enabled,
        "queue_role": queue_role,
        "queue_inline_fallback_enabled": queue_runtime.allow_inline_fallback(),
        "local_queue_expected": queue_enabled and queue_role in {"all", "worker"},
    }
