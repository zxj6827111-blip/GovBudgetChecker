"""Shared runtime state and helpers for API routes.

This module keeps route-facing helpers out of `api.main`, so route modules can
depend on a stable API surface instead of importing `api.main` directly.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import aiofiles
from fastapi import HTTPException, Request, UploadFile

logger = logging.getLogger(__name__)

APP_TITLE = "GovBudgetChecker API"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
UPLOAD_ROOT = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

try:
    from src.security import (
        security_config,
        sanitize_filename,
        validate_file_upload,
        verify_api_key,
    )

    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False
    security_config = None

    async def verify_api_key(request: Request = None):  # type: ignore[override]
        _ = request
        return "anonymous"

try:
    from src.services.org_storage import get_org_storage
    from src.schemas.organization import Organization, OrganizationLevel

    ORG_AVAILABLE = True
except ImportError:
    ORG_AVAILABLE = False
    get_org_storage = None
    Organization = None
    OrganizationLevel = None

_pipeline_runner: Optional[Callable[[Path], Awaitable[None]]] = None


def set_pipeline_runner(runner: Callable[[Path], Awaitable[None]]) -> None:
    """Register the async pipeline runner used by `start_analysis`."""
    global _pipeline_runner
    _pipeline_runner = runner


def to_dict(obj: Any) -> Dict[str, Any]:
    """Best-effort conversion from pydantic/dataclass-like objects to dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(obj)


def find_first_pdf(job_dir: Path) -> Path:
    """Return the first PDF in a job directory."""
    pdfs = sorted(job_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError("PDF file not found under job directory")
    return pdfs[0]


def read_json_file(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read JSON file safely and return `default` when missing/invalid."""
    if default is None:
        default = {}
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read JSON file: %s", path)
    return default


def collect_job_summary(job_dir: Path) -> Dict[str, Any]:
    """Build a job summary payload for list APIs."""
    status_file = job_dir / "status.json"
    status_data = read_json_file(status_file, default={})

    filename = ""
    try:
        filename = find_first_pdf(job_dir).name
    except Exception:
        pass

    progress = status_data.get("progress", 0)
    status = status_data.get("status", "unknown")
    stage = status_data.get("stage")
    ts = status_data.get("ts")
    if ts is None:
        try:
            ts = job_dir.stat().st_mtime
        except Exception:
            ts = time.time()

    return {
        "job_id": job_dir.name,
        "filename": filename,
        "status": status,
        "progress": progress,
        "ts": ts,
        "mode": status_data.get("mode", "legacy"),
        "stage": stage,
    }


def iter_job_dirs() -> List[Path]:
    """Return all existing job directories."""
    if not UPLOAD_ROOT.exists():
        return []
    return [p for p in UPLOAD_ROOT.iterdir() if p.is_dir()]


def ensure_pdf(file: UploadFile) -> bool:
    """Basic PDF content-type/extension guard."""
    ct = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    return ct in ("application/pdf", "application/x-pdf") or name.endswith(".pdf")


async def store_upload_file(file: UploadFile) -> Dict[str, Any]:
    """Persist upload into a job directory and return metadata payload."""
    if SECURITY_AVAILABLE:
        safe_name = sanitize_filename(file.filename or "file.pdf")
        data = await file.read()
        is_valid, error_msg = validate_file_upload(
            filename=safe_name,
            content_type=file.content_type or "",
            content=data,
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
    else:
        if not ensure_pdf(file):
            raise HTTPException(status_code=415, detail="Only PDF files are supported")
        data = await file.read()
        safe_name = Path(file.filename or "file.pdf").name

    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_MB}MB limit")

    job_id = os.urandom(16).hex()
    job_dir = UPLOAD_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dst = job_dir / safe_name

    async with aiofiles.open(dst, "wb") as f:
        await f.write(data)

    checksum = hashlib.sha256(data).hexdigest()
    return {
        "id": job_id,
        "job_id": job_id,
        "filename": safe_name,
        "size": len(data),
        "saved_path": str(dst.relative_to(UPLOAD_ROOT)),
        "checksum": checksum,
    }


def get_job_status_payload(job_id: str) -> Dict[str, Any]:
    """Read job status payload by id."""
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")
    status_file = job_dir / "status.json"
    if not status_file.exists():
        return {"job_id": job_id, "status": "processing", "progress": 0}
    try:
        return json.loads(status_file.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read job status: {e}") from e


def require_org_storage():
    """Return organization storage singleton or 503 when unavailable."""
    if not ORG_AVAILABLE:
        raise HTTPException(status_code=503, detail="organization service unavailable")
    return get_org_storage()


async def start_analysis(job_id: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Queue a job for async processing and return started status."""
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist, upload first")

    if _pipeline_runner is None:
        raise HTTPException(status_code=500, detail="analysis pipeline is not configured")

    status_file = job_dir / "status.json"
    body = body or {}
    use_local_rules = bool(body.get("use_local_rules", True))
    use_ai_assist = bool(body.get("use_ai_assist", True))
    mode = str(body.get("mode", "legacy"))
    payload = {
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "analysis task queued",
        "use_local_rules": use_local_rules,
        "use_ai_assist": use_ai_assist,
        "mode": mode,
        "ts": time.time(),
    }
    try:
        status_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        asyncio.create_task(_pipeline_runner(job_dir))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"failed to start analysis: {e}") from e
    return {"job_id": job_id, "status": "started"}
