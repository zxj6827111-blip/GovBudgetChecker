"""Organization management endpoints."""

from __future__ import annotations

import csv
import io
import os
import time
from typing import Annotated, Any, Dict, List, Tuple

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile

from api import runtime

router = APIRouter()

_DEPT_STATS_CACHE_TTL_SECONDS = float(
    os.getenv("DEPARTMENT_STATS_CACHE_TTL_SECONDS", "3")
)
_DEPT_STATS_CACHE_MAX_SIZE = int(
    os.getenv("DEPARTMENT_STATS_CACHE_MAX_SIZE", "512")
)
_DEPT_STATS_CACHE: Dict[str, Dict[str, Any]] = {}


def _clear_department_stats_cache() -> None:
    _DEPT_STATS_CACHE.clear()


def _prune_missing_job_links(
    storage: Any, job_ids: List[str]
) -> Tuple[List[str], List[str]]:
    existing_job_ids: List[str] = []
    stale_job_ids: List[str] = []

    for job_id in job_ids:
        job_dir = runtime.UPLOAD_ROOT / job_id
        if job_dir.exists():
            existing_job_ids.append(job_id)
        else:
            stale_job_ids.append(job_id)

    if stale_job_ids:
        for job_id in stale_job_ids:
            try:
                storage.unlink_job(job_id)
            except Exception:
                runtime.logger.exception(
                    "Failed to unlink stale organization job: %s", job_id
                )
        _clear_department_stats_cache()

    return existing_job_ids, stale_job_ids


@router.get("/api/organizations")
async def get_organizations():
    storage = runtime.require_org_storage()
    tree = [runtime.to_dict(node) for node in storage.get_tree()]
    return {"tree": tree, "total": len(storage.get_all())}


@router.post("/api/organizations")
async def create_organization(request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid request body")

    name = str(body.get("name") or "").strip()
    level = str(body.get("level") or "unit").strip()
    parent_id = body.get("parent_id")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    if runtime.Organization is None:
        raise HTTPException(status_code=503, detail="organization schema unavailable")

    org_id = body.get("id") or runtime.Organization.generate_id(name, level, parent_id)
    try:
        org = runtime.Organization(
            id=org_id,
            name=name,
            level=level,
            parent_id=parent_id,
            code=body.get("code"),
            keywords=body.get("keywords") or [name],
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid organization payload: {e}") from e

    storage = runtime.require_org_storage()
    created = storage.add(org)
    _clear_department_stats_cache()
    return runtime.to_dict(created)


@router.put("/api/organizations/{org_id}")
async def update_organization(org_id: str, request: Request):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid request body")
    
    name = str(body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
        
    storage = runtime.require_org_storage()
    updated = storage.update(org_id, {"name": name})
    if not updated:
        raise HTTPException(status_code=404, detail="organization not found")
    _clear_department_stats_cache()
    return runtime.to_dict(updated)


@router.delete("/api/organizations/{org_id}")
async def delete_organization(org_id: str):
    storage = runtime.require_org_storage()
    
    # Optional: We could check if there are linked jobs before deleting,
    # but the storage layer currently handles cascading deletions correctly.
    # Alternatively, we could prevent deletion if it's not empty, but user requested deletion capability.
    
    success = storage.delete(org_id)
    if not success:
        raise HTTPException(status_code=404, detail="organization not found")
    _clear_department_stats_cache()
    return {"success": True, "message": "organization deleted"}


@router.get("/api/organizations/list")
async def get_organizations_list():
    storage = runtime.require_org_storage()
    organizations = []
    for org in storage.get_all():
        level_name = org.level
        if runtime.OrganizationLevel is not None:
            level_name = runtime.OrganizationLevel.get_display_name(org.level)
        organizations.append(
            {
                "id": org.id,
                "name": org.name,
                "level": org.level,
                "level_name": level_name,
                "parent_id": org.parent_id,
            }
        )
    return {"organizations": organizations, "total": len(organizations)}


@router.get("/api/departments")
async def get_departments():
    storage = runtime.require_org_storage()
    departments = [runtime.to_dict(org) for org in storage.get_departments()]
    return {"departments": departments, "total": len(departments)}


@router.get("/api/departments/{dept_id}/units")
async def get_units_by_department(dept_id: str):
    storage = runtime.require_org_storage()
    department = storage.get_by_id(dept_id)
    if department is None or getattr(department, "level", None) != "department":
        raise HTTPException(status_code=404, detail="department not found")

    units = [runtime.to_dict(org) for org in storage.get_units_by_department(dept_id)]
    return {"units": units, "total": len(units)}


@router.get("/api/departments/{dept_id}/stats")
async def get_department_stats(dept_id: str):
    storage = runtime.require_org_storage()
    department = storage.get_by_id(dept_id)
    if department is None or getattr(department, "level", None) != "department":
        raise HTTPException(status_code=404, detail="department not found")

    now = time.time()
    if _DEPT_STATS_CACHE_TTL_SECONDS > 0:
        cached = _DEPT_STATS_CACHE.get(dept_id)
        if cached is not None:
            ts = float(cached.get("ts", 0.0))
            if now - ts <= _DEPT_STATS_CACHE_TTL_SECONDS:
                payload = cached.get("payload")
                if isinstance(payload, dict):
                    return payload

    orgs = [department, *storage.get_units_by_department(dept_id)]
    stats: Dict[str, Dict[str, Any]] = {}

    for org in orgs:
        valid_job_ids, _ = _prune_missing_job_links(
            storage, storage.get_org_jobs(org.id, include_children=False)
        )
        job_count = 0
        issue_total = 0
        for job_id in valid_job_ids:
            job_dir = runtime.UPLOAD_ROOT / job_id
            summary = runtime.collect_job_summary(job_dir)
            job_count += 1
            issue_total += int(summary.get("issue_total") or 0)
        stats[org.id] = {
            "job_count": job_count,
            "issue_total": issue_total,
            "has_issues": issue_total > 0,
        }

    payload = {"department_id": dept_id, "stats": stats}
    if _DEPT_STATS_CACHE_TTL_SECONDS > 0:
        _DEPT_STATS_CACHE[dept_id] = {"ts": now, "payload": payload}
        if len(_DEPT_STATS_CACHE) > _DEPT_STATS_CACHE_MAX_SIZE:
            _DEPT_STATS_CACHE.pop(next(iter(_DEPT_STATS_CACHE)))
    return payload


@router.post("/api/organizations/import")
async def import_organizations(
    file: Annotated[UploadFile, File(...)],
    clear_existing: Annotated[bool, Form()] = False,
):
    storage = runtime.require_org_storage()
    filename = (file.filename or "").lower()
    raw = await file.read()

    rows: List[Dict[str, Any]] = []
    if filename.endswith(".csv"):
        text = raw.decode("utf-8-sig", errors="ignore")
        reader = csv.DictReader(io.StringIO(text))
        rows = [dict(row) for row in reader if row]
    elif filename.endswith(".xlsx"):
        try:
            import openpyxl
        except Exception as e:
            raise HTTPException(status_code=400, detail="xlsx import requires openpyxl") from e

        workbook = openpyxl.load_workbook(io.BytesIO(raw), read_only=True)
        worksheet = workbook.active
        iterator = worksheet.iter_rows(values_only=True)
        headers_row = next(iterator, None)
        if headers_row is None:
            raise HTTPException(status_code=400, detail="empty xlsx")
        headers = [str(x).strip() if x is not None else "" for x in headers_row]
        for row in iterator:
            item: Dict[str, Any] = {}
            for idx, header in enumerate(headers):
                if not header:
                    continue
                value = row[idx] if idx < len(row) else None
                if value is not None:
                    item[header] = str(value).strip()
            if any(str(v).strip() for v in item.values()):
                rows.append(item)
    else:
        raise HTTPException(status_code=400, detail="only .csv/.xlsx are supported")

    result = storage.import_from_list(rows, clear_existing=clear_existing)
    _clear_department_stats_cache()
    return runtime.to_dict(result)


@router.get("/api/organizations/{org_id}/jobs")
async def get_organization_jobs(
    org_id: str,
    include_children: bool = Query(
        default=False,
        description="Whether to include jobs linked to descendant organizations.",
    ),
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    storage = runtime.require_org_storage()
    org = storage.get_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")

    job_ids, _ = _prune_missing_job_links(
        storage, storage.get_org_jobs(org_id, include_children=include_children)
    )
    total = len(job_ids)

    def _quick_ts(job_id: str) -> float:
        job_dir = runtime.UPLOAD_ROOT / job_id
        try:
            status_file = job_dir / "status.json"
            if status_file.exists():
                return status_file.stat().st_mtime
            return job_dir.stat().st_mtime
        except Exception:
            return 0.0

    if limit is not None or offset > 0:
        sorted_ids = sorted(job_ids, key=_quick_ts, reverse=True)
        if limit is None:
            selected_ids = sorted_ids[offset:]
        else:
            selected_ids = sorted_ids[offset : offset + limit]
    else:
        selected_ids = job_ids

    jobs: List[Dict[str, Any]] = []
    for job_id in selected_ids:
        job_dir = runtime.UPLOAD_ROOT / job_id
        jobs.append(runtime.collect_job_summary(job_dir))
    jobs.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return {
        "jobs": jobs,
        "total": total,
        "limit": limit,
        "offset": offset,
    }
