"""Health and readiness endpoints."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request

from api.config import AppConfig
from api import runtime
from api import queue_runtime
from api.auth_utils import require_admin
from src.services.audit_log import get_audit_log_path

router = APIRouter()

_SENSITIVE_READY_KEYS = {"auth_key_configured"}


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

        health_url = url.replace("/ai/extract/v1", "/health")

        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(health_url)
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


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _status(ok: bool, required: bool, detail: str) -> Dict[str, Any]:
    return {
        "ok": ok,
        "required": required,
        "status": "ok" if ok else ("failed" if required else "degraded"),
        "detail": detail,
    }


def _redact_dependency_detail(item: Dict[str, Any]) -> Dict[str, Any]:
    redacted = dict(item)
    redacted["detail"] = "ok" if bool(item.get("ok")) else str(item.get("status") or "unavailable")
    return redacted


def _redact_dependencies(dependencies: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    return {
        section: {
            key: _redact_dependency_detail(value)
            for key, value in section_items.items()
            if key not in _SENSITIVE_READY_KEYS
        }
        for section, section_items in dependencies.items()
    }


def _include_ready_details(request: Request) -> bool:
    requested = str(request.query_params.get("details") or "").strip().lower()
    if requested not in {"1", "true", "yes", "on"}:
        return False
    if _env_flag("READY_EXPOSE_DETAILS", False):
        return True
    require_admin(request)
    return True


def _redact_checks(checks: Dict[str, bool]) -> Dict[str, bool]:
    return {
        key: value
        for key, value in checks.items()
        if key not in _SENSITIVE_READY_KEYS
    }


@router.get("/ready")
@router.get("/api/ready")
async def ready(request: Request) -> Dict[str, Any]:
    rules_file = Path(os.getenv("RULES_FILE", "rules/v3_3.yaml"))
    audit_log_path = get_audit_log_path()
    auth_enabled = bool(runtime.security_config.enabled) if runtime.security_config else False
    auth_key_present = bool(os.getenv("GOVBUDGET_API_KEY")) if auth_enabled else True
    queue_enabled = queue_runtime.queue_enabled()
    queue_role = queue_runtime.get_queue_role()
    local_queue_required = queue_enabled and queue_role in {"all", "worker"}
    queue_started = runtime.get_job_queue() is not None if local_queue_required else True
    inline_fallback_enabled = queue_runtime.allow_inline_fallback()
    inline_fallback_safe = not (
        queue_enabled and queue_role == "api" and inline_fallback_enabled
    )

    db_ok, db_detail = await _check_database()
    ai_ok, ai_detail = await _check_ai_extractor()
    db_required = bool((os.getenv("DATABASE_URL") or "").strip()) and _env_flag(
        "READY_REQUIRE_DATABASE",
        False,
    )
    ai_required = (os.getenv("AI_ASSIST_ENABLED", "true").strip().lower() == "true") and _env_flag(
        "READY_REQUIRE_AI_EXTRACTOR",
        False,
    )

    checks = {
        "upload_root_exists": runtime.UPLOAD_ROOT.exists(),
        "upload_root_writable": os.access(runtime.UPLOAD_ROOT, os.W_OK),
        "rules_file_exists": rules_file.exists(),
        "auth_key_configured": auth_key_present,
        "db_reachable": db_ok,
        "ai_extractor_reachable": ai_ok,
        "job_queue_started": queue_started,
        "inline_fallback_safe": inline_fallback_safe,
        "audit_log_parent_writable": os.access(audit_log_path.parent, os.W_OK) if audit_log_path.parent.exists() else True,
    }
    required = {
        "upload_root_exists": _status(checks["upload_root_exists"], True, str(runtime.UPLOAD_ROOT)),
        "upload_root_writable": _status(checks["upload_root_writable"], True, str(runtime.UPLOAD_ROOT)),
        "rules_file_exists": _status(checks["rules_file_exists"], True, str(rules_file)),
        "auth_key_configured": _status(
            checks["auth_key_configured"],
            True,
            "configured" if auth_key_present else "missing GOVBUDGET_API_KEY",
        ),
        "job_queue_started": _status(
            checks["job_queue_started"],
            True,
            "started" if queue_started else "local queue is required but not started",
        ),
        "inline_fallback_safe": _status(
            checks["inline_fallback_safe"],
            True,
            "ok" if inline_fallback_safe else "api role must not enable inline fallback",
        ),
        "audit_log_parent_writable": _status(
            checks["audit_log_parent_writable"],
            True,
            str(audit_log_path.parent),
        ),
    }
    optional = {
        "db_reachable": _status(db_ok, db_required, db_detail),
        "ai_extractor_reachable": _status(ai_ok, ai_required, ai_detail),
    }
    dependencies = {
        "required": {**required, **{key: value for key, value in optional.items() if value["required"]}},
        "optional": {key: value for key, value in optional.items() if not value["required"]},
    }

    details = {
        "rules_file": str(rules_file),
        "audit_log_path": str(audit_log_path),
        "auth_enabled": auth_enabled,
        "db": db_detail,
        "ai_extractor": ai_detail,
        "queue_enabled": queue_enabled,
        "queue_role": queue_role,
        "local_queue_required": local_queue_required,
        "inline_fallback_enabled": inline_fallback_enabled,
        "ready_policy": {
            "database_required": db_required,
            "ai_extractor_required": ai_required,
        },
        "upload_limits": {
            "max_upload_mb": runtime.MAX_UPLOAD_MB,
            "max_upload_pages": runtime.MAX_UPLOAD_PAGES,
        },
        "auth_key_configured": auth_key_present,
    }

    ready_state = all(item["ok"] for item in dependencies["required"].values())
    include_details = _include_ready_details(request)
    payload: Dict[str, Any] = {
        "status": "ready" if ready_state else "not_ready",
        "checks": checks if include_details else _redact_checks(checks),
        "dependencies": dependencies if include_details else _redact_dependencies(dependencies),
        "details": details
        if include_details
        else {
            "redacted": True,
            "detail": "append ?details=true with an admin session for diagnostics",
        },
        "ts": time.time(),
    }
    return payload
