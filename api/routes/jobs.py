"""Job list/status/detail endpoints."""

from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException, Request

from api import runtime

router = APIRouter()


@router.get("/api/jobs")
@router.get("/jobs")
async def list_jobs():
    jobs = [runtime.collect_job_summary(job_dir) for job_dir in runtime.iter_job_dirs()]
    jobs.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return jobs


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

    link = storage.link_job(job_id, org_id, match_type="manual", confidence=1.0)
    return {"success": True, "link": runtime.to_dict(link)}

