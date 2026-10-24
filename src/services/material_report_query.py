"""部门报告的材料级投影。只读、批量查询，文件版本与分析任务分别计数。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from typing import Any, Callable, Optional, Sequence

from src.schemas.issues import COMPLETED_JOB_STATUSES, normalize_job_status
from src.services.material_detail_query_service import _RUN_VERSION_JOIN, select_current_run, run_sort_key
from src.services.material_ledger_query_service import MaterialSlotFilters
from src.services.review_lifecycle_service import review_session_matches


def json_value(value: Any, default: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return value if value is not None else default


def iso(value: Any) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else str(value) if value is not None else None


def frozen_confirmed_count(raw: Any) -> Optional[int]:
    counts = json_value(raw, {})
    if not isinstance(counts, dict):
        return None
    confirmed = counts.get("confirmed")
    packaged = counts.get("in_package", 0)
    if type(confirmed) is not int or confirmed < 0 or type(packaged) is not int or packaged < 0:
        return None
    # 已确认的问题进入归档包后仍是确认问题，不能显示成“未确认问题”。
    return confirmed + packaged


def review_summary(slot_id: str, version_id: Any, run: Optional[dict], sessions: list) -> dict:
    completed_analysis = bool(run and run.get("result_id") is not None
        and normalize_job_status(run.get("status")) in COMPLETED_JOB_STATUSES)
    matching = [s for s in sessions if run and review_session_matches(s, slot_id=slot_id,
        document_version_id=version_id, job_uuid=run.get("job_uuid"),
        analysis_revision=run.get("analysis_revision"))]
    effective = next((s for s in matching if s.get("status") == "in_progress"), None)
    effective = effective or next((s for s in matching if s.get("status") == "completed"), None)
    status = ("waiting_analysis" if not completed_analysis else
              str(effective["status"]) if effective else "invalidated" if sessions else "pending")
    return {"status": status,
            "confirmed_count": frozen_confirmed_count((effective or {}).get("issue_counts")) if status == "completed" else None,
            "reviewer": (effective or {}).get("completed_by") or (effective or {}).get("started_by"),
            "reviewed_at": iso((effective or {}).get("completed_at") or (effective or {}).get("started_at"))}


async def load_report_rows(conn: Any, *, visible_org_ids: Optional[Sequence[str]],
                           job_accessible: Callable[[dict], bool], slot_id: Optional[str] = None) -> list[dict]:
    """一次轻量 join；不取完整 metadata、发现或证据，不扫描 uploads。"""
    where, params = MaterialSlotFilters(visible_org_ids=visible_org_ids).where()
    if slot_id:
        params.append(slot_id)
        where += f" AND id::text = ${len(params)}"
    rows = await conn.fetch(f"""
        WITH slots AS (SELECT * FROM material_slots WHERE {where}),
        versions AS (
            SELECT v.*, row_number() OVER(PARTITION BY v.slot_id ORDER BY v.created_at,v.id) AS version_number
            FROM fiscal_document_versions v JOIN slots s ON s.id=v.slot_id
        ), sessions AS (
            SELECT rs.slot_id, jsonb_agg(jsonb_build_object(
                'slot_id',rs.slot_id::text,'document_version_id',rs.document_version_id,
                'analysis_job_uuid',rs.analysis_job_uuid,'analysis_basis_token',rs.analysis_basis_token,
                'status',rs.status,'started_by',rs.started_by,'started_at',rs.started_at,
                'completed_by',rs.completed_by,'completed_at',rs.completed_at,
                'invalidated_reason',rs.invalidated_reason,
                'issue_counts',rs.review_result->'issue_counts') ORDER BY rs.created_at DESC,rs.id DESC) AS history
            FROM review_sessions rs JOIN slots s ON s.id=rs.slot_id GROUP BY rs.slot_id
        )
        SELECT s.id::text AS slot_id,s.subject_org_id,s.subject_org_name,s.subject_kind,
            s.department_org_id,s.jurisdiction_org_id,s.material_scope,s.fiscal_year,s.report_kind,
            s.current_document_version_id,s.status AS slot_status,s.status_reason,s.mapping_key,
            s.updated_at AS slot_updated_at,s.caliber_conflict_candidate,
            v.id AS document_version_id,v.version_number,v.original_filename,v.file_hash,
            v.file_size_bytes,v.created_at AS version_created_at,
            j.id,j.job_uuid,j.status,j.analysis_revision,j.created_at,j.updated_at,j.started_at,j.completed_at,
            j.access_organization_id AS organization_id,j.access_created_by AS created_by,
            r.id AS result_id,sessions.history
        FROM slots s JOIN versions v ON v.slot_id=s.id
        LEFT JOIN analysis_jobs j ON {_RUN_VERSION_JOIN}
        LEFT JOIN analysis_results r ON r.job_id=j.id
        LEFT JOIN sessions ON sessions.slot_id=s.id
        """, *params)
    grouped: dict[str, list] = {}
    for raw in rows:
        row = dict(raw)
        # 槽位可见不代表运行可见；先过滤任务，再计算任务数、历史和可用链接。
        row["job_visible"] = bool(row.get("job_uuid") and job_accessible({**row, "job_id": row.get("job_uuid")}))
        grouped.setdefault(row["slot_id"], []).append(row)
    return [project_material(slot_rows) for slot_rows in grouped.values()]


def project_material(rows: list[dict]) -> dict:
    first = rows[0]
    slot_id = str(first["slot_id"])
    versions: dict[int, list] = {}
    for row in rows:
        versions.setdefault(int(row["document_version_id"]), []).append(row)
    allowed_jobs = {r["job_uuid"] for r in rows if r.get("job_visible")}
    sessions = [s for s in json_value(first.get("history"), [])
                if s.get("analysis_job_uuid") in allowed_jobs]
    projected = []
    for version_id, version_rows in versions.items():
        head = version_rows[0]
        # 当前运行先按全部记录确定，再鉴权；不得把旧的可见分析冒充最新分析。
        latest = select_current_run([r for r in version_rows if r.get("id") is not None])
        current = latest if latest and latest.get("job_visible") else None
        visible = sorted([r for r in version_rows if r.get("job_visible")], key=run_sort_key)
        source_job = next(iter(visible), None)
        version_sessions = [s for s in sessions if s.get("document_version_id") == version_id]
        projected.append({
            "document_version_id": version_id,"version_number": int(head["version_number"]),
            "is_current": version_id == first.get("current_document_version_id"),
            "filename": head.get("original_filename"),"file_hash": head.get("file_hash"),
            "file_size_bytes": head.get("file_size_bytes"),"created_at": iso(head.get("version_created_at")),
            "source_job_id": (source_job or {}).get("job_uuid"),
            "current_run": run_summary(current) if current else None,
            "analysis_unavailable": latest is not None and current is None,
            "review": review_summary(slot_id,version_id,current,version_sessions),
            "runs": [run_summary(r) for r in visible],
            "reviews": [{**s,"confirmed_count": frozen_confirmed_count(s.get("issue_counts")),
                         "started_at": iso(s.get("started_at")),"completed_at": iso(s.get("completed_at"))}
                        for s in version_sessions],
        })
    projected.sort(key=lambda v: v["version_number"], reverse=True)
    current_version = next((v for v in projected if v["is_current"]), None)
    historical_review = next((s for s in sessions if s.get("completed_at") and (
        s.get("document_version_id") != first.get("current_document_version_id")
        or (current_version or {}).get("review",{}).get("status") != "completed")),None)
    return {
        "item_id": f"slot:{slot_id}","slot_id": slot_id,"organization_id": first.get("subject_org_id"),
        "organization_name": first.get("subject_org_name"),"subject_kind": first.get("subject_kind"),
        "material_scope": first.get("material_scope"),"fiscal_year": first.get("fiscal_year"),
        "report_year": first.get("fiscal_year"),"report_kind": first.get("report_kind"),
        "filename": (current_version or {}).get("filename"),"slot_status": first.get("slot_status"),
        "mapping_required": bool(first.get("mapping_key") or first.get("caliber_conflict_candidate")),
        "current_version": current_version,"version_count": len(projected),"analysis_task_count": len(allowed_jobs),
        "review": (current_version or {}).get("review") or {"status":"waiting_analysis","confirmed_count":None},
        "previous_review": historical_review,"updated_at": iso(first.get("slot_updated_at")),
        "versions": projected,
    }


def run_summary(row: dict) -> dict:
    return {"job_id":row.get("job_uuid"),"status":row.get("status"),
            "analysis_revision":row.get("analysis_revision"),"created_at":iso(row.get("created_at")),
            "completed_at":iso(row.get("completed_at"))}


async def load_unlinked_reports(conn: Any, *, job_accessible: Callable[[dict], bool]) -> list[dict]:
    """旧任务与未登记的上传必须保留。只认精确版本链路，不按文件名猜归属。"""
    rows = await conn.fetch(f"""SELECT j.job_uuid,j.status,j.analysis_revision,j.created_at,j.updated_at,
        j.access_organization_id AS organization_id,j.summary_organization_name AS organization_name,
        j.access_created_by AS created_by,COALESCE(j.summary_filename,j.filename) AS filename,j.file_hash,
        j.summary_report_year AS report_year,j.summary_report_kind AS report_kind
        FROM analysis_jobs j LEFT JOIN fiscal_document_versions v ON {_RUN_VERSION_JOIN}
        WHERE v.slot_id IS NULL""")
    items = []
    for raw in rows:
        row = dict(raw)
        if not job_accessible({**row, "job_id": row.get("job_uuid")}):
            continue
        raw_year = str(row.get("report_year") or "")
        year = int(raw_year) if raw_year.isdigit() and 2000 <= int(raw_year) <= 2099 else None
        items.append({"item_id":f"history:{row['job_uuid']}","slot_id":None,
            "job_id":row["job_uuid"],"organization_id":row.get("organization_id"),
            "organization_name":row.get("organization_name"),"filename":row.get("filename"),
            "fiscal_year":year,"report_year":year,"report_kind":row.get("report_kind") or "unknown",
            "review":{"status":"history_only","confirmed_count":None},"version_count":None,
            "analysis_task_count":1,"current_version":None,"versions":[],
            "original_checksum":row.get("file_hash"),
            "updated_at":iso(row.get("updated_at") or row.get("created_at"))})
    return items


def management_organization_ids(records: Sequence[Any], management_scope: str) -> set[str]:
    """按真实祖先区分市级与区级；行政地域父节点不等同于市级业务范围。"""
    by_id = {str(r.id): r for r in records}
    selected: set[str] = set()
    for org_id, record in by_id.items():
        current = record
        seen: set[str] = set()
        district = False
        valid = True
        while current is not None:
            current_id = str(current.id)
            if current_id in seen:
                valid = False
                break
            seen.add(current_id)
            if current.level == "district":
                district = True
                break
            parent = current.parent_id
            if parent and str(parent) not in by_id:
                valid = False
                break
            current = by_id.get(str(parent)) if parent else None
        if valid and ((management_scope == "district") == district):
            selected.add(org_id)
    return selected


def organization_review_status(items: list[dict], catalog_ids: set[str]) -> dict[str, dict]:
    """目录状态：与 organization_report_counts 同口径（已登记、在组织目录内、无需确认归属）。

    每个组织给出本组织材料的复核状态计数（单位本级口径另计一份），以及部门汇总、单位本级
    两种口径下预算与决算各自最新财政年度那份材料的状态。只汇总已有材料，不推断应交未交。
    """
    result: dict[str, dict] = {}
    order: dict[tuple[str, str], tuple] = {}
    for item in items:
        if not item.get("slot_id") or item.get("organization_id") not in catalog_ids or item.get("mapping_required"):
            continue
        org_id = str(item["organization_id"])
        entry = result.setdefault(org_id, {"review_counts": {}, "unit_review_counts": {}, "latest": {}})
        review = item.get("review") or {}
        status = str(review.get("status") or "")
        entry["review_counts"][status] = entry["review_counts"].get(status, 0) + 1
        scope, kind, year = item.get("material_scope"), item.get("report_kind"), item.get("fiscal_year")
        if scope == "unit_self":
            entry["unit_review_counts"][status] = entry["unit_review_counts"].get(status, 0) + 1
        if scope not in ("department_summary", "unit_self") or kind not in ("budget", "final") or not year:
            continue
        key = f"{scope}:{kind}"
        rank = (int(year), item.get("updated_at") or "", item["item_id"])
        if (org_id, key) in order and order[(org_id, key)] >= rank:
            continue
        order[(org_id, key)] = rank
        run = (item.get("current_version") or {}).get("current_run") or {}
        entry["latest"][key] = {"fiscal_year": int(year), "review_status": status,
            "confirmed_count": review.get("confirmed_count"), "run_status": run.get("status")}
    return result


def report_page(items: list[dict], *, organization_ids: Optional[set[str]], unassigned: bool,
                catalog_ids: set[str], year: str, kind: str, keyword: str, review: str,
                page: int, page_size: int, report_category: str = "all") -> dict:
    counts = Counter(str(i.get("organization_id")) for i in items if i.get("slot_id")
                     and i.get("organization_id") in catalog_ids and not i.get("mapping_required"))
    unit_counts = Counter(str(i.get("organization_id")) for i in items if i.get("slot_id")
                          and i.get("material_scope") == "unit_self"
                          and i.get("organization_id") in catalog_ids and not i.get("mapping_required"))
    def unresolved(item: dict) -> bool:
        return not item.get("slot_id") or item.get("mapping_required") or item.get("organization_id") not in catalog_ids
    scope = [i for i in items if (unresolved(i) if unassigned else
        organization_ids is None or i.get("organization_id") in organization_ids)]
    uncategorized_total = sum(i.get("material_scope") in (None, "unknown", "") for i in scope)
    if report_category != "all":
        # 部门本级的单位材料与部门汇总材料可能共用组织ID，不能用层级或名称代替材料口径。
        material_scope = "department_summary" if report_category == "department" else "unit_self"
        scope = [item for item in scope if item.get("material_scope") == material_scope]
    years = sorted({i["fiscal_year"] for i in scope if i.get("fiscal_year")},reverse=True)
    query = keyword.strip().casefold()
    selected = [i for i in scope if (not year or str(i.get("fiscal_year") or "unknown") == year)
        and (kind == "all" or i.get("report_kind") == kind)
        and (review == "all" or i["review"]["status"] == review)
        and (not query or any(query in str(i.get(k) or "").casefold()
                             for k in ("filename","organization_name","job_id","slot_id")))]
    selected.sort(key=lambda i:(i.get("fiscal_year") or 0,i.get("updated_at") or "",i["item_id"]),reverse=True)
    total = len(selected)
    pages = (total + page_size - 1)//page_size
    page = min(page,max(1,pages))
    visible = []
    for item in selected[(page-1)*page_size:page*page_size]:
        # 历史按需展开，首屏只返回当前摘要。
        projected = {k:v for k,v in item.items() if k != "versions"}
        if projected.get("current_version"):
            projected["current_version"] = {k:v for k,v in projected["current_version"].items() if k not in ("runs","reviews")}
        visible.append(projected)
    active = any(v.get("status") in ("processing","queued","running","analyzing","pending")
                 for i in items for v in [(i.get("current_version") or {}).get("current_run") or {}])
    return {"items":visible,"total":total,"page":page,"page_size":page_size,"page_count":pages,
            "years":years,"has_unknown_year":any(not i.get("fiscal_year") for i in scope),
            "organization_report_counts":dict(counts),"unassigned_total":sum(unresolved(i) for i in items),
            "organization_unit_report_counts":dict(unit_counts),"uncategorized_total":uncategorized_total,
            "material_total":sum(bool(i.get("slot_id")) for i in scope),
            "legacy_total":sum(not i.get("slot_id") for i in scope),
            "organization_review_status":organization_review_status(items, catalog_ids),
            # 与 material_total 同一范围：各复核状态之和等于本范围已登记材料数。
            "review_status_counts":dict(Counter(str(i["review"]["status"]) for i in scope if i.get("slot_id"))),
            "activity":{"running":int(active),"waiting":0}}
