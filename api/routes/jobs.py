"""Job list/status/detail endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

from api import runtime
from api.auth_utils import require_admin
from api.routes.organizations import clear_department_stats_cache
from src.services.analysis_result_store import (
    get_persisted_analysis_job_detail,
    list_persisted_analysis_jobs,
)
from src.services.org_matcher import get_org_matcher
from src.services.audit_log import append_audit_event

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


@router.get("/api/admin/analysis-results")
async def list_admin_analysis_results(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default=""),
    status: str = Query(default=""),
    mode: str = Query(default=""),
):
    require_admin(request)
    return await list_persisted_analysis_jobs(
        limit=limit,
        offset=offset,
        search=search,
        status=status,
        mode=mode,
    )


@router.get("/api/admin/analysis-results/{job_uuid}")
async def get_admin_analysis_result_detail(job_uuid: str, request: Request):
    require_admin(request)
    payload = await get_persisted_analysis_job_detail(job_uuid)
    if payload is None:
        raise HTTPException(status_code=404, detail="analysis job not found")
    if payload.get("available") is False:
        raise HTTPException(status_code=503, detail="analysis result database unavailable")
    return payload


@router.post("/api/jobs/reanalyze-all")
async def reanalyze_all_jobs(request: Request):
    _, _, user = require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = await runtime.reanalyze_all_jobs(body)
    append_audit_event(
        action="jobs.reanalyze_all",
        actor=str(user.get("username") or ""),
        result="success",
        resource_type="job_batch",
        details={
            "requested_count": int(result.get("requested_count") or 0),
            "created_count": int(result.get("created_count") or 0),
            "skipped_count": int(result.get("skipped_count") or 0),
            "failed_count": int(result.get("failed_count") or 0),
        },
    )
    return result


@router.post("/api/jobs/rematch-organizations")
async def rematch_job_organizations(request: Request):
    _, _, user = require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = runtime.rematch_job_organizations(body)
    if not bool(result.get("dry_run")) and int(result.get("updated_count") or 0) > 0:
        clear_department_stats_cache()
    append_audit_event(
        action="jobs.rematch_organizations",
        actor=str(user.get("username") or ""),
        result="success",
        resource_type="job_batch",
        details={
            "dry_run": bool(result.get("dry_run")),
            "candidate_count": int(result.get("candidate_count") or 0),
            "updated_count": int(result.get("updated_count") or 0),
            "skipped_count": int(result.get("skipped_count") or 0),
            "failed_count": int(result.get("failed_count") or 0),
        },
    )
    return result


@router.post("/api/jobs/structured-ingest-cleanup")
async def cleanup_structured_ingest_history(request: Request):
    _, _, user = require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = await runtime.cleanup_structured_ingest_history(body)
    append_audit_event(
        action="jobs.structured_cleanup",
        actor=str(user.get("username") or ""),
        result="success",
        resource_type="structured_cleanup",
        details={"dry_run": bool(body.get("dry_run", False)), "status": result.get("status")},
    )
    return result


@router.post("/api/jobs/batch-delete")
async def batch_delete_jobs(request: Request):
    _, _, user = require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}

    raw_ids = (body or {}).get("job_ids")
    if not isinstance(raw_ids, list):
        raise HTTPException(status_code=400, detail="job_ids must be a list")

    job_ids: list[str] = []
    seen = set()
    for item in raw_ids:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        job_ids.append(normalized)

    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids is required")

    deleted: list[str] = []
    failed: list[Dict[str, Any]] = []
    for job_id in job_ids:
        try:
            runtime.delete_job(job_id)
            deleted.append(job_id)
        except HTTPException as exc:
            failed.append(
                {
                    "job_id": job_id,
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }
            )
        except Exception as exc:
            failed.append(
                {
                    "job_id": job_id,
                    "status_code": 500,
                    "detail": str(exc),
                }
            )

    if deleted:
        clear_department_stats_cache()

    append_audit_event(
        action="jobs.batch_delete",
        actor=str(user.get("username") or ""),
        result="success" if not failed else "partial_success",
        resource_type="job_batch",
        details={
            "requested_count": len(job_ids),
            "deleted_count": len(deleted),
            "failed_count": len(failed),
        },
    )
    return {
        "success": len(deleted) > 0 and not failed,
        "requested_count": len(job_ids),
        "deleted_count": len(deleted),
        "failed_count": len(failed),
        "deleted_job_ids": deleted,
        "failed": failed,
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
async def delete_job(job_id: str, request: Request):
    _, _, user = require_admin(request)
    runtime.delete_job(job_id)
    clear_department_stats_cache()
    append_audit_event(
        action="jobs.delete",
        actor=str(user.get("username") or ""),
        result="success",
        resource_type="job",
        details={"job_id": job_id},
    )
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
    clear_department_stats_cache()
    return {"success": True, **binding}


@router.post("/api/jobs/{job_id}/reanalyze")
async def reanalyze_job(job_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await runtime.reanalyze_job(job_id, body)


@router.post("/api/jobs/{job_id}/issues/ignore")
async def ignore_job_issue(job_id: str, request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    issue_id = str((body or {}).get("issue_id") or "").strip()
    if not issue_id:
        raise HTTPException(status_code=400, detail="issue_id is required")
    payload = runtime.ignore_job_issue(job_id, issue_id)
    clear_department_stats_cache()
    return payload
