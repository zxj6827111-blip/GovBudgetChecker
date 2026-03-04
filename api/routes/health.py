"""Health and readiness endpoints."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter

from api.config import AppConfig
from api import runtime
from api import queue_runtime

router = APIRouter()


@router.get("/health")
@router.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "service": runtime.APP_TITLE, "ts": time.time()}


async def _check_database() -> tuple[bool, str]:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        return True, "not_configured"
    try:
        import asyncpg

        conn = await asyncpg.connect(database_url, timeout=3)
        try:
            await conn.execute("SELECT 1")
        finally:
            await conn.close()
        return True, "ok"
    except Exception as exc:
        return False, str(exc)


async def _check_ai_extractor() -> tuple[bool, str]:
    ai_enabled = (os.getenv("AI_ASSIST_ENABLED", "true").strip().lower() == "true")
    if not ai_enabled:
        return True, "disabled"

    url = (os.getenv("AI_EXTRACTOR_URL") or "").strip()
    if not url:
        url = AppConfig.load().ai_extractor_url.strip()
    if not url:
        return False, "AI extractor URL is empty"

    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(url)
            # Treat 2xx-4xx as reachable. 5xx means remote service unhealthy.
            if response.status_code < 500:
                return True, f"reachable:{response.status_code}"
            return False, f"remote_error:{response.status_code}"
    except Exception as exc:
        # Fallback: when extractor service is down, allow direct provider mode.
        try:
            from src.engine.ai.extractor_client import ExtractorClient

            extractor = ExtractorClient()
            if await extractor.health_check():
                return True, "direct_fallback"
        except Exception:
            pass
        return False, str(exc)


@router.get("/ready")
@router.get("/api/ready")
async def ready() -> Dict[str, Any]:
    rules_file = Path(os.getenv("RULES_FILE", "rules/v3_3.yaml"))
    auth_enabled = bool(runtime.security_config.enabled) if runtime.security_config else False
    auth_key_present = bool(os.getenv("GOVBUDGET_API_KEY")) if auth_enabled else True
    queue_enabled = queue_runtime.queue_enabled()
    queue_role = queue_runtime.get_queue_role()
    local_queue_required = queue_enabled and queue_role in {"all", "worker"}
    queue_started = runtime.get_job_queue() is not None if local_queue_required else True

    db_ok, db_detail = await _check_database()
    ai_ok, ai_detail = await _check_ai_extractor()

    checks = {
        "upload_root_exists": runtime.UPLOAD_ROOT.exists(),
        "upload_root_writable": os.access(runtime.UPLOAD_ROOT, os.W_OK),
        "rules_file_exists": rules_file.exists(),
        "auth_key_configured": auth_key_present,
        "db_reachable": db_ok,
        "ai_extractor_reachable": ai_ok,
        "job_queue_started": queue_started,
    }

    details = {
        "rules_file": str(rules_file),
        "auth_enabled": auth_enabled,
        "db": db_detail,
        "ai_extractor": ai_detail,
        "queue_enabled": queue_enabled,
        "queue_role": queue_role,
        "local_queue_required": local_queue_required,
        "inline_fallback_enabled": queue_runtime.allow_inline_fallback(),
    }

    ready_state = all(checks.values())
    return {
        "status": "ready" if ready_state else "not_ready",
        "checks": checks,
        "details": details,
        "ts": time.time(),
    }
