"""Configuration endpoint."""

from __future__ import annotations

import os

from fastapi import APIRouter

from api import runtime

router = APIRouter()


@router.get("/config")
@router.get("/api/config")
async def get_config():
    ai_enabled = os.getenv("AI_ASSIST_ENABLED", "true").lower() == "true"
    ai_extractor_url = os.getenv("AI_EXTRACTOR_URL", "http://127.0.0.1:9009/ai/extract/v1")
    return {
        "ai_enabled": ai_enabled,
        "ai_assist_enabled": ai_enabled,
        "ai_extractor_url": ai_extractor_url,
        "auth_enabled": bool(runtime.security_config.enabled) if runtime.security_config else False,
    }

