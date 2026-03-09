"""Job list/status/detail endpoints."""

from __future__ import annotations

import shutil
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from api import runtime
from src.services.org_matcher import get_org_matcher

router = APIRouter()


def _serialize_org(org) -> Dict[str, Any]:
    payload = runtime.to_dict(org)
    level = str(payload.get("level") or "")
    if level and runtime.OrganizationLevel is not None:
        payload.setdefault("level_name", runtime.OrganizationLevel.get_display_name(level))
    return payload


@router.get("/api/jobs")
@router.get("/jobs")
async def list_jobs(
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    job_dirs = runtime.iter_job_dirs()

    def _quick_ts(job_dir):
        try:
            status_file = job_dir / "status.json"
            if status_file.exists():
                return status_file.stat().st_mtime
            return job_dir.stat().st_mtime
        except Exception:
            return 0.0

    if limit is None and offset == 0:
        jobs = [runtime.collect_job_summary(job_dir) for job_dir in job_dirs]
        jobs.sort(key=lambda x: x.get("ts", 0), reverse=True)
        return jobs

    sorted_dirs = sorted(job_dirs, key=_quick_ts, reverse=True)
    total = len(sorted_dirs)
    if limit is None:
        selected_dirs = sorted_dirs[offset:]
    else:
        selected_dirs = sorted_dirs[offset : offset + limit]

    items = [runtime.collect_job_summary(job_dir) for job_dir in selected_dirs]
    items.sort(key=lambda x: x.get("ts", 0), reverse=True)

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/jobs/{job_id}/status")
@router.get("/api/jobs/{job_id}/status")
async def get_job_status(job_id: str):
    return runtime.get_job_status_payload(job_id)


@router.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: str):
    payload = runtime.get_job_status_payload(job_id)
    payload.setdefault("job_id", job_id)
    try:
        payload["filename"] = runtime.find_first_pdf(runtime.UPLOAD_ROOT / job_id).name
    except Exception:
        payload.setdefault("filename", "")
    return payload


@router.get("/api/jobs/{job_id}/review")
async def get_job_review(job_id: str):
    return runtime.get_job_review_payload(job_id)


@router.get("/api/jobs/{job_id}/structured-ingest")
async def get_job_structured_ingest(job_id: str):
    return runtime.get_job_review_payload(job_id)


@router.get("/api/jobs/{job_id}/org-suggestions")
async def get_job_org_suggestions(
    job_id: str,
    top_n: int = Query(default=5, ge=1, le=20),
):
    job_dir = runtime.UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")
    if not runtime.ORG_AVAILABLE:
        raise HTTPException(status_code=503, detail="organization service unavailable")

    pdf_path = runtime.find_first_pdf(job_dir)
    first_page_text = runtime.extract_pdf_first_page_text(pdf_path)
    matcher = get_org_matcher()
    storage = runtime.require_org_storage()
    status_data = runtime.get_job_status_payload(job_id)

    current_org: Optional[Dict[str, Any]] = None
    link = storage.get_job_org(job_id)
    if link is not None:
        org = storage.get_by_id(link.org_id)
        if org is not None:
            current_org = {
                "organization": _serialize_org(org),
                "match_type": link.match_type,
                "confidence": round(float(link.confidence), 4),
            }
    elif status_data.get("organization_id"):
        org = storage.get_by_id(str(status_data["organization_id"]))
        if org is not None:
            current_org = {
                "organization": _serialize_org(org),
                "match_type": status_data.get("organization_match_type") or "unknown",
                "confidence": round(
                    float(status_data.get("organization_match_confidence") or 0.0),
                    4,
                ),
            }

    suggestions = []
    for org, confidence in matcher.suggest_matches(pdf_path.name, first_page_text, top_n=top_n):
        suggestions.append(
            {
                "organization": _serialize_org(org),
                "confidence": round(float(confidence), 4),
            }
        )

    return {
        "job_id": job_id,
        "filename": pdf_path.name,
        "current": current_org,
        "suggestions": suggestions,
    }


@router.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str):
    job_dir = runtime.UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id 不存在")
    try:
        shutil.rmtree(job_dir)
        if runtime.ORG_AVAILABLE:
            try:
                runtime.require_org_storage().unlink_job(job_id)
            except Exception:
                runtime.logger.exception("Failed to unlink job during delete: %s", job_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"删除任务失败: {e}") from e
    return {"success": True, "job_id": job_id}


@router.post("/api/jobs/{job_id}/associate")
async def associate_job(job_id: str, request: Request):
    body = await request.json()
    org_id = (body or {}).get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="org_id is required")

    storage = runtime.require_org_storage()
    org = storage.get_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")

    binding = runtime.set_job_organization(
        job_id,
        org_id,
        match_type="manual",
        confidence=1.0,
    )
    return {"success": True, **binding}
