"""Analysis execution endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from api import runtime

router = APIRouter()


@router.post("/analyze/{job_id}")
@router.post("/api/analyze/{job_id}")
@router.post("/analyze2/{job_id}")
@router.post("/api/analyze2/{job_id}")
async def analyze_job(job_id: str, request: Request):
    body: Optional[Dict[str, Any]] = None
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = None

    return await runtime.start_analysis(job_id, body)


@router.post("/api/documents/{version_id}/run")
async def run_document(version_id: str, request: Request):
    body: Optional[Dict[str, Any]] = None
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except Exception:
        body = None

    return await runtime.start_analysis(version_id, body)

