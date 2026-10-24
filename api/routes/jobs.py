"""Job list/status/detail endpoints."""

from __future__ import annotations

from typing import Any, Dict, Optional
import time
import os
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from api import job_list_query, runtime
from api.auth_utils import (
    require_admin,
    require_job_access,
    require_login,
    user_can_access_job,
    user_can_access_org,
)
from api.routes.organizations import clear_department_stats_cache
from src.services.analysis_result_store import (
    get_persisted_analysis_job_detail,
    list_persisted_analysis_jobs,
)
from src.services.org_matcher import get_org_matcher
from src.services.audit_log import append_audit_event

router = APIRouter()


async def published_job_view(job_id,payload,user):
    """当前结果以数据库为准；重算进度与正在展示的报告版本分别绑定。"""
    if not os.getenv('DATABASE_URL'):return payload
    from src.services.analysis_fact_snapshot import read_database_result
    try:published=await read_database_result(job_id)
    except (RuntimeError,ValueError)as error:
        if (payload.get('structured_ingest')or{}).get('fact_snapshot'):
            raise HTTPException(503,'current fiscal snapshot unavailable')from error
        return payload
    if published is None:return payload
    if not user_can_access_job(user,published):raise HTTPException(403,'无权访问当前分析版本')
    if payload.get('status')in runtime.ACTIVE_ANALYSIS_STATUSES|{'error','cancelled'}:
        return {**payload,'result':published.get('result'),'analysis_revision':published.get('analysis_revision'),
                'structured_ingest':published.get('structured_ingest'),'review_report':published.get('review_report')}
    return {**published,'job_id':job_id}


def _serialize_org(org) -> Dict[str, Any]:
    payload = runtime.to_dict(org)
    level = str(payload.get("level") or "")
    if level and runtime.OrganizationLevel is not None:
        payload.setdefault("level_name", runtime.OrganizationLevel.get_display_name(level))
    return payload


def _job_page_view(request: Request, summaries: list) -> Dict[str, Any]:
    """处理队列、部门报告、任务历史与工作台明细的分页视图；各参数口径见 api/job_list_query.py。

    org_scope=descendants 时组织范围包含全部下级（部门报告"部门及下属单位"），
    缺省只匹配该组织本身（处理队列）；org=unassigned 只看未关联组织的任务。
    scope=finished 只要已结束的任务（任务历史）；coverage_below 只要页面覆盖率低于该值的任务。
    """
    params = request.query_params
    org = str(params.get("org") or "").strip()
    organization_ids = None
    if org and org != "unassigned":
        if params.get("org_scope") == "descendants":
            organization_ids = job_list_query.descendant_organization_ids(
                runtime.require_org_storage().get_all(), org
            )
        else:
            organization_ids = frozenset({org})
    filters = job_list_query.JobListFilters(
        keyword=str(params.get("q") or ""),
        status=job_list_query.parse_choice(params.get("status"), job_list_query.STATUS_FILTERS, "all"),
        year=str(params.get("year") or "").strip(),
        kind=job_list_query.parse_choice(params.get("kind"), job_list_query.KIND_FILTERS, "all"),
        stage=job_list_query.parse_choice(params.get("stage"), job_list_query.STAGE_FILTERS, "all"),
        organization_ids=organization_ids,
        unassigned=org == "unassigned",
        finished_only=params.get("scope") == "finished",
        coverage_below=job_list_query.parse_ratio(params.get("coverage_below"), None),
    )
    include = {item.strip() for item in str(params.get("include") or "").split(",")}
    return job_list_query.build_page_view(
        summaries,
        filters,
        sort_key=job_list_query.parse_choice(params.get("sort"), job_list_query.SORT_KEYS, "updated_desc"),
        page=job_list_query.parse_positive_int(params.get("page"), 1, maximum=1_000_000),
        page_size=job_list_query.parse_positive_int(params.get("page_size"), 20, maximum=200),
        include_runnable="runnable" in include,
        include_organization_counts="organization_counts" in include,
    )


def _resolve_timezone(name: Optional[str]):
    """浏览器时区（IANA 名称）；缺省或无法识别时按服务器本地时间分日。"""
    try:
        return ZoneInfo(str(name)) if name else None
    except (ZoneInfoNotFoundError, ValueError):
        return None


def _job_overview_view(request: Request, summaries: list) -> Dict[str, Any]:
    """工作台总览与质量管理页的汇总；口径见 api/job_list_query.build_overview_view。"""
    params = request.query_params
    return job_list_query.build_overview_view(
        summaries,
        coverage_min=job_list_query.parse_ratio(params.get("coverage_min"), job_list_query.DEFAULT_COVERAGE_MIN),
        trend_days=job_list_query.parse_positive_int(params.get("trend_days"), 30, maximum=90),
        tz=_resolve_timezone(params.get("tz")),
        now_ms=time.time() * 1000,
    )


@router.get("/api/jobs")
@router.get("/jobs")
async def list_jobs(
    request: Request,
    limit: int | None = Query(default=None, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
):
    _, _, user = require_login(request)
    job_dirs = runtime.iter_job_dirs()

    def _quick_ts(job_dir):
        try:
            status_file = job_dir / "status.json"
            if status_file.exists():
                return status_file.stat().st_mtime
            return job_dir.stat().st_mtime
        except Exception:
            return 0.0

    def _collect_summaries():
        summaries = [
            summary
            for job_dir in sorted(job_dirs, key=_quick_ts, reverse=True)
            for summary in [runtime.collect_job_summary(job_dir)]
            if user_can_access_job(user, summary)
        ]
        summaries.sort(key=lambda item: item.get("ts", 0), reverse=True)
        return summaries

    # 冷缓存需要读取文件和重建报告；不要在事件循环里堵住其他列表/健康请求。
    summaries = await run_in_threadpool(_collect_summaries)

    # view=counts / page / overview 由服务端汇总与分页，页面不必再下载全部摘要。
    # 旧前端不带 view 参数，下面的返回形状保持不变。
    view = request.query_params.get("view") if request is not None else None
    if view == "counts":
        return job_list_query.build_counts_view(summaries)
    if view == "page":
        return _job_page_view(request, summaries)
    if view == "overview":
        return _job_overview_view(request, summaries)

    if limit is None and offset == 0:
        return summaries

    total = len(summaries)
    if limit is None:
        items = summaries[offset:]
    else:
        items = summaries[offset : offset + limit]

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
    result = await run_in_threadpool(runtime.rematch_job_organizations, body)
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


@router.post("/api/jobs/repair-missing-links")
async def repair_missing_job_organization_links(request: Request):
    _, _, user = require_admin(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    result = await run_in_threadpool(runtime.repair_missing_job_organization_links, body)
    if int(result.get("repaired_count") or 0) > 0:
        clear_department_stats_cache()
    append_audit_event(
        action="jobs.repair_missing_links",
        actor=str(user.get("username") or ""),
        result="success",
        resource_type="job_batch",
        details={
            "dry_run": bool(result.get("dry_run")),
            "candidate_count": int(result.get("candidate_count") or 0),
            "repaired_count": int(result.get("repaired_count") or 0),
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
async def get_job_status(job_id: str, request: Request):
    _, _, user, payload = await run_in_threadpool(require_job_access, request, job_id)
    return await published_job_view(job_id,payload,user)


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    """Request cooperative cancellation without racing the worker's status writes."""
    _, _, user, payload = require_job_access(request, job_id)
    if payload.get("status") not in runtime.ACTIVE_ANALYSIS_STATUSES:
        raise HTTPException(status_code=409, detail="job is not being analyzed")
    runtime.write_json_file(runtime.UPLOAD_ROOT / job_id / ".cancel-request.json", {"job_id": job_id, "ts": time.time()})
    append_audit_event(action="jobs.cancel", actor=str(user.get("username") or ""),
                       result="requested", resource_type="job", details={"job_id": job_id})
    return {"job_id": job_id, "status": "cancel_requested", "resume": "POST /api/jobs/{job_id}/reanalyze reuses compatible completed OCR pages"}


@router.get("/api/jobs/{job_id}")
async def get_job_detail(job_id: str, request: Request, analysis_revision: Optional[str] = None):
    _, _, user, payload = await run_in_threadpool(require_job_access, request, job_id)
    if analysis_revision:
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',analysis_revision):
            raise HTTPException(422,'invalid analysis revision')
        from src.services.analysis_fact_snapshot import read_database_result
        from api.routes.reports import _load_revision_payload
        try:
            archived=await read_database_result(job_id,analysis_revision)if os.getenv('DATABASE_URL')else None
            payload=archived or await run_in_threadpool(_load_revision_payload,job_id,analysis_revision)
        except RuntimeError as error:raise HTTPException(503,'historical analysis database unavailable')from error
        except ValueError as error:raise HTTPException(409,'historical analysis integrity failed')from error
        if not user_can_access_job(user,payload):raise HTTPException(403,'无权访问该历史分析版本')
    else:payload=await published_job_view(job_id,payload,user)
    payload.setdefault("job_id", job_id)
    try:
        payload["filename"] = runtime.find_first_pdf(runtime.UPLOAD_ROOT / job_id).name
    except Exception:
        payload.setdefault("filename", "")
    return payload


@router.get('/api/jobs/{job_id}/analysis-versions')
async def analysis_versions(job_id:str,request:Request,offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200)):
    require_job_access(request,job_id,include_detail=False)
    items={i['revision_id']:{**i,'state':'legacy','snapshot_hash':None,'is_current':False}
           for i in runtime.list_analysis_revisions(runtime.UPLOAD_ROOT/job_id)}
    current=None
    if os.getenv('DATABASE_URL'):
        from src.services.analysis_result_store import ensure_analysis_persistence_ready
        from src.db.connection import DatabaseConnection
        if not await ensure_analysis_persistence_ready():raise HTTPException(503,'analysis version database unavailable')
        conn=await DatabaseConnection.acquire()
        try:
            current=await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1',job_id)
            rows=await conn.fetch("SELECT s.revision_id,s.snapshot_hash,s.publication_state,s.created_at,"
                "r.payload->>'status' AS status FROM analysis_fact_snapshots s JOIN analysis_revision_results r USING(job_uuid,revision_id)"
                'WHERE s.job_uuid=$1 ORDER BY s.created_at DESC',job_id)
            for r in rows:
                items[r['revision_id']]={'revision_id':r['revision_id'],'snapshot_hash':r['snapshot_hash'],
                    'state':r['publication_state'],'status':r['status'],'is_current':r['revision_id']==current,
                    'created_at':r['created_at'].isoformat()}
        finally:await DatabaseConnection.release(conn)
    ordered=sorted(items.values(),key=lambda i:(bool(i['is_current']),i.get('created_at')or'',i['revision_id']),reverse=True)
    return {'job_id':job_id,'current_revision_id':current,'total':len(ordered),'offset':offset,'limit':limit,'items':ordered[offset:offset+limit]}


@router.get('/api/jobs/{job_id}/preserved-analyses')
async def preserved_analyses(job_id:str,request:Request):
    require_job_access(request,job_id,include_detail=False)
    import json,hashlib
    items=[]
    for path in (runtime.UPLOAD_ROOT/job_id/'preserved_analyses').glob('*/manifest.json'):
        record=json.loads(path.read_text())
        expected=hashlib.sha256(json.dumps(record['files'],sort_keys=True).encode()).hexdigest()
        if expected!=record.get('archive_sha256')or path.parent.name!=expected:
            raise HTTPException(409,'preserved original history integrity failed')
        items.append(record)
    return {'job_id':job_id,'items':items}


@router.get('/api/jobs/{job_id}/preserved-analyses/{archive_id}/{filename}')
async def preserved_analysis_file(job_id:str,archive_id:str,filename:str,request:Request):
    require_job_access(request,job_id,include_detail=False)
    if not re.fullmatch('[a-f0-9]{64}',archive_id)or filename not in {
        'status.json','fact_context.json','corrections.jsonl','structured_ingest.json',
        'annotated.pdf','report.pdf','report_annotated.pdf'}:raise HTTPException(404,'preserved report not found')
    import json,hashlib
    from fastapi.responses import FileResponse
    root=runtime.UPLOAD_ROOT/job_id/'preserved_analyses'/archive_id
    if not(root/'manifest.json').is_file()or not(root/filename).is_file():
        raise HTTPException(404,'preserved report not found')
    record=json.loads((root/'manifest.json').read_text())
    if (hashlib.sha256(json.dumps(record['files'],sort_keys=True).encode()).hexdigest()!=archive_id
        or hashlib.sha256((root/filename).read_bytes()).hexdigest()!=record['files'].get(filename)):
        raise HTTPException(409,'preserved original report bytes differ')
    return FileResponse(root/filename,filename=filename)


@router.get("/api/jobs/{job_id}/fact-snapshot")
async def get_job_fact_snapshot(
    job_id: str, request: Request,
    revision_id: Optional[str] = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,128}$"),
    view: str = Query(default="effective", pattern="^(raw|effective)$"),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=200, ge=1, le=1000),
):
    import json
    from src.services.analysis_fact_snapshot import validate_snapshot, canonical_facts
    _, _, _, payload = await run_in_threadpool(require_job_access, request, job_id)
    import os
    database_snapshot = None
    if (os.getenv('DATABASE_URL') or '').strip():
        from src.services.analysis_fact_snapshot import read_database_snapshot
        try:
            database_snapshot = await read_database_snapshot(job_id, revision_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    revision = (database_snapshot or {}).get('revision_id') or revision_id or payload.get("analysis_revision")
    if not revision:
        raise HTTPException(status_code=404, detail="immutable fact snapshot unavailable for legacy analysis")
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(revision)):
        raise HTTPException(status_code=400, detail="invalid analysis revision")
    path = runtime.UPLOAD_ROOT / job_id / "fact_snapshots" / (str(revision) + ".json")
    def read_snapshot():
        if database_snapshot is None and not path.is_file():
            raise HTTPException(status_code=404, detail="fact snapshot not found")
        snapshot = database_snapshot or validate_snapshot(json.loads(path.read_text(encoding="utf-8")))
        if snapshot["job_uuid"] != job_id or snapshot["revision_id"] != revision:
            raise HTTPException(status_code=409, detail="fact snapshot identity mismatch")
        facts = canonical_facts(snapshot[view + "_context"].get("facts", []))
        return {k: snapshot[k] for k in ("schema_version", "job_uuid", "revision_id", "snapshot_hash",
                "source_file_hash", "parse_run_uid", "raw_facts_hash", "effective_facts_hash")} | {
                "view": view, "total": len(facts), "offset": offset, "limit": limit,
                "facts": facts[offset:offset + limit]}
    try:
        return await run_in_threadpool(read_snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/api/jobs/{job_id}/review")
async def get_job_review(job_id: str, request: Request):
    require_job_access(request, job_id)
    return runtime.get_job_review_payload(job_id)


@router.get("/api/jobs/{job_id}/structured-ingest")
async def get_job_structured_ingest(job_id: str, request: Request):
    _,_,user,payload=require_job_access(request, job_id, include_detail=False)
    bound=await published_job_view(job_id,payload,user)
    if (bound.get('structured_ingest')or{}).get('fact_snapshot'):
        return bound['structured_ingest']
    return await run_in_threadpool(runtime.get_job_review_payload, job_id)


@router.get("/api/jobs/{job_id}/org-suggestions")
async def get_job_org_suggestions(
    request: Request,
    job_id: str,
    top_n: int = Query(default=5, ge=1, le=20),
):
    require_job_access(request, job_id)
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
    _, _, user, _ = require_job_access(request, job_id)
    body = await request.json()
    org_id = (body or {}).get("org_id")
    if not org_id:
        raise HTTPException(status_code=400, detail="org_id is required")

    storage = runtime.require_org_storage()
    org = storage.get_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    if not user_can_access_org(user, str(org_id)):
        raise HTTPException(status_code=403, detail="organization access denied")

    # 上传时已经拒绝“封面明确组织 A、手工选择组织 B”的绑定；关联接口
    # 也必须复用同一证据检查，否则用户可以在上传后绕过保护，重新制造
    # report scope 串线和 report_id 冲突。没有可读封面组织时仍允许人工
    # 覆盖自动匹配，这是产品原有的人工纠错路径。
    job_dir = runtime.UPLOAD_ROOT / job_id
    try:
        pdf_path = runtime.find_first_pdf(job_dir)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "pdf_not_found",
                "message": "任务不存在可用于复核组织绑定的 PDF。",
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "organization_binding_unverifiable",
                "message": "任务包含多个 PDF，且无法由 status.json 唯一确认原件。",
            },
        ) from exc
    from api.routes.upload import _inspect_document_preflight, _organization_binding_conflict

    preflight = _inspect_document_preflight(
        filename=pdf_path.name,
        pdf_path=pdf_path,
        include_matches=False,
    )
    conflict = _organization_binding_conflict(preflight, runtime.to_dict(org))
    if conflict is not None:
        raise HTTPException(status_code=422, detail=conflict)

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
    require_job_access(request, job_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    return await runtime.reanalyze_job(job_id, body)


@router.post("/api/jobs/{job_id}/issues/ignore")
async def ignore_job_issue(job_id: str, request: Request):
    _, _, user, _ = require_job_access(request, job_id)
    try:
        body = await request.json()
    except Exception:
        body = {}
    issue_id = str((body or {}).get("issue_id") or "").strip()
    if not issue_id:
        raise HTTPException(status_code=400, detail="issue_id is required")
    payload = runtime.ignore_job_issue(job_id, issue_id, user=user, note=(body or {}).get("note"))
    clear_department_stats_cache()
    return payload
