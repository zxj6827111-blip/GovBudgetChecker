"""人工复核清单的批量只读查询；不按文件名猜材料归属，不在浏览时创建会话。"""

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from src.schemas.issues import COMPLETED_JOB_STATUSES, normalize_job_status
from src.schemas.review_worklist import ReviewWorklistData, ReviewWorklistItem
from src.services.material_detail_query_service import (
    _NUMERIC_GUARD, _STRUCTURED_VERSION_JSON_PATH, select_current_run,
)
from src.services.material_ledger_query_service import MaterialSlotFilters
from src.services.review_context_query import _json_value, analysis_from_run_row
from src.services.review_lifecycle_service import build_worklist_review_summary

WORKLIST_STATUSES = ("pending", "in_progress", "invalidated", "completed", "waiting_analysis", "history_only")
PENDING_STATUSES = frozenset(("pending", "in_progress", "invalidated"))


def _moment(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e12 else value, timezone.utc).isoformat()
        except (OverflowError, ValueError, OSError):
            return None
    return str(value)


async def load_review_worklist(
    conn: Any, *, jobs: List[Dict[str, Any]], visible_org_ids: Optional[Sequence[str]],
    job_accessible: Callable[[str], bool], viewer: str,
) -> ReviewWorklistData:
    where, params = MaterialSlotFilters(visible_org_ids=visible_org_ids).where()
    # 一次取当前文件上的运行与会话；当前运行仍由详情页的排序函数选择。
    rows = await conn.fetch(
        f"""
        WITH visible_slots AS (SELECT * FROM material_slots WHERE {where})
        SELECT s.id::text AS slot_id, s.current_document_version_id,
               s.subject_org_id, s.subject_org_name, s.department_name,
               s.subject_kind, s.material_scope, s.fiscal_year, s.report_kind,
               s.mapping_key, s.applicability_status, s.caliber_conflict_candidate,
               s.updated_at AS slot_updated_at,
               v.original_filename, v.id AS document_version_id,
               j.id, j.job_uuid, j.status, j.analysis_revision, j.metadata,
               j.started_at, j.completed_at, j.created_at, j.updated_at,
               r.id AS result_id, r.ai_findings, r.rule_findings, r.merged_result,
               sessions.history
        FROM visible_slots s
        JOIN fiscal_document_versions v ON v.id = s.current_document_version_id AND v.slot_id = s.id
        LEFT JOIN analysis_jobs j ON CASE WHEN {_NUMERIC_GUARD} THEN {_STRUCTURED_VERSION_JSON_PATH}::numeric END = v.id::numeric
        LEFT JOIN analysis_results r ON r.job_id = j.id
        LEFT JOIN LATERAL (
            SELECT jsonb_agg(to_jsonb(rs) ORDER BY rs.created_at DESC, rs.id DESC) AS history
            FROM review_sessions rs WHERE rs.slot_id = s.id
        ) sessions ON TRUE
        """, *params,
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["slot_id"]), []).append(dict(row))

    candidates = []
    session_ids = set()
    for slot_rows in grouped.values():
        run = select_current_run([row for row in slot_rows if row.get("id") is not None])
        if run is None or not job_accessible(str(run.get("job_uuid") or "")):
            continue
        history_raw = _json_value(run.get("history"))
        history = []
        for session in history_raw if isinstance(history_raw, list) else []:
            session = dict(session)
            session["review_session_id"] = str(session.get("id") or "")
            history.append(session)
            session_ids.add(session["review_session_id"])
        candidates.append((dict(run), history))

    decisions = await conn.fetch(
        """SELECT review_session_id::text AS review_session_id, obligation_id, decision
           FROM review_obligation_decisions WHERE review_session_id::text = ANY($1::text[])""",
        list(session_ids),
    ) if session_ids else []
    decisions_by_session: Dict[str, List[Dict[str, Any]]] = {}
    for decision in decisions:
        decisions_by_session.setdefault(str(decision["review_session_id"]), []).append(dict(decision))

    items = []
    for row, history in candidates:
        analysis = analysis_from_run_row(row, int(row["current_document_version_id"]))
        summary = build_worklist_review_summary(
            row, analysis, history,
            [decision for session in history for decision in decisions_by_session.get(session["review_session_id"], [])],
        )
        items.append(ReviewWorklistItem(
            item_id=f"slot:{row['slot_id']}", slot_id=row["slot_id"], job_id=analysis.job_uuid,
            document_version_id=analysis.document_version_id, filename=row.get("original_filename"),
            organization_id=row.get("subject_org_id"), organization_name=row.get("subject_org_name"),
            department_name=row.get("department_name"), fiscal_year=row.get("fiscal_year"),
            report_kind=row.get("report_kind") or "unknown",
            updated_at=_moment(row.get("updated_at") or row.get("slot_updated_at")), **summary,
        ))

    # 有精确关联的旧运行不能再次成为待办；无关联的历史结果保留只读入口。
    job_ids = [str(job.get("job_id")) for job in jobs if job.get("job_id")]
    links = await conn.fetch(
        f"""SELECT j.job_uuid FROM analysis_jobs j
            JOIN fiscal_document_versions v ON CASE WHEN {_NUMERIC_GUARD} THEN {_STRUCTURED_VERSION_JSON_PATH}::numeric END = v.id::numeric
            WHERE j.job_uuid = ANY($1::text[]) AND v.slot_id IS NOT NULL""", job_ids,
    ) if job_ids else []
    linked = {str(row["job_uuid"]) for row in links}
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        if not job_id or job_id in linked or normalize_job_status(job.get("status")) not in COMPLETED_JOB_STATUSES:
            continue
        items.append(ReviewWorklistItem(
            item_id=f"history:{job_id}", job_id=job_id, review_status="history_only",
            filename=job.get("filename"), organization_id=job.get("organization_id"),
            organization_name=job.get("organization_name"), fiscal_year=job.get("report_year"),
            report_kind=job.get("report_kind") or "unknown", updated_at=_moment(job.get("updated_ts") or job.get("ts")),
        ))
    counts = Counter(item.review_status for item in items)
    return ReviewWorklistData(
        items=items, counts={status: counts[status] for status in WORKLIST_STATUSES},
        pending_total=sum(counts[status] for status in PENDING_STATUSES), viewer=viewer,
    )
