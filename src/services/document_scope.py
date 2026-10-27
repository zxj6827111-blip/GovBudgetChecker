"""材料口径证据。人工决定与归档组织层级、self/summary 画像分开保存。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from typing import Any

from fastapi import HTTPException

MATERIAL_SCOPE_LEVEL = {"unit_self": "unit", "department_summary": "department", "unknown": None}
logger = logging.getLogger(__name__)


def decision_dict(row: Any) -> dict | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("request_id", "created_at", "retry_not_before"):
        if result.get(key) is None:
            continue
        result[key] = str(result[key])
    if isinstance(result.get("source_evidence"), str):
        result["source_evidence"] = json.loads(result["source_evidence"])
    return result


async def latest_decision(conn, job_uuid: str, file_hash: str, version_id: int | None = None):
    return decision_dict(await conn.fetchrow(
        "SELECT * FROM document_scope_decisions WHERE job_uuid=$1 AND file_hash=$2 "
        "AND ($3::bigint IS NULL OR document_version_id=$3) ORDER BY revision DESC LIMIT 1",
        job_uuid, file_hash, version_id))


def scope_evidence(payload: dict) -> list[dict]:
    meta = (payload.get("result") or {}).get("meta") or {}
    profile = meta.get("document_profile") or {}
    evidence = []
    for key in ("subject_level", "cover_title", "organization_name"):
        field = profile.get(key) or {}
        if field.get("value"):
            evidence.append({"dimension": key, "value": field["value"], "source": field.get("source"),
                             "text": field.get("evidence") or str(field["value"]),
                             "decisive": key == "subject_level" and field.get("source") not in {"derived", "filename"}})
    subject = meta.get("check_subject") or {}
    if subject.get("organization_level"):
        evidence.append({"dimension": "archive_level", "value": subject["organization_level"],
                         "source": "organization_directory", "text": subject.get("organization_name"), "decisive": False})
    # 标题体例只建议，不从“本单位/本部门”用语倒推结论。
    return evidence


def source_conflicts(evidence: list[dict], material_scope: str) -> list[dict]:
    selected = MATERIAL_SCOPE_LEVEL[material_scope]
    return [row for row in evidence if selected and row.get("dimension") == "subject_level"
            and row.get("decisive") and row.get("value") in {"unit", "department"}
            and row["value"] != selected]


async def information(conn, job_uuid: str, payload: dict) -> dict:
    from src.services import review_context_query, review_session_store
    link = await review_context_query.resolve_slot_link_for_job(conn, job_uuid)
    if link is None:
        raise HTTPException(409, {"error": "review_context_unavailable", "message": "当前材料版本尚未建立可靠复核上下文"})
    slot = await review_context_query.load_slot_row_for_review(conn, link.slot_id)
    analysis = await review_context_query.resolve_current_analysis(conn, slot)
    if analysis.job_uuid != job_uuid or analysis.document_version_id != link.document_version_id:
        raise HTTPException(409, {"error": "analysis_basis_changed", "message": "当前材料已换版，请打开最新分析"})
    row = await conn.fetchrow("SELECT file_hash FROM analysis_jobs WHERE job_uuid=$1", job_uuid)
    current = await latest_decision(conn, job_uuid, row["file_hash"], link.document_version_id)
    evidence = scope_evidence(payload)
    profile = ((payload.get("result") or {}).get("meta") or {}).get("document_profile") or {}
    level = (profile.get("subject_level") or {}).get("value")
    archive_level = (((payload.get("result") or {}).get("meta") or {}).get("check_subject") or {}).get("organization_level")
    suggestion = "unit_self" if archive_level == "unit" else "department_summary" if level == "department" else None
    return {"job_id": job_uuid, "document_version_id": link.document_version_id, "file_hash": row["file_hash"],
            "analysis_basis_token": review_session_store.build_analysis_basis_token(job_uuid, analysis.analysis_revision),
            "analysis_completed": analysis.completed, "decision": current, "evidence": evidence,
            "suggestion": suggestion, "suggestion_is_confirmation": False}


async def load_for_analysis(job_uuid: str, file_hash: str) -> dict | None:
    if not os.getenv("DATABASE_URL"):
        return None
    from src.services.analysis_result_store import ensure_analysis_persistence_ready
    from src.db.connection import DatabaseConnection
    if not await ensure_analysis_persistence_ready():
        raise RuntimeError("材料口径证据数据库不可用，不能发布未知口径的替代结果")
    conn = await DatabaseConnection.acquire()
    try:
        return await latest_decision(conn, job_uuid, file_hash)
    finally:
        await DatabaseConnection.release(conn)


async def pending_reanalysis(job_uuid: str) -> dict | None:
    """文件投递前也投影 DB 已提交的等待状态，旧结果只作为上一版证据展示。"""
    from src.db.connection import DatabaseConnection
    conn = await DatabaseConnection.acquire()
    try:
        return decision_dict(await conn.fetchrow("""SELECT d.* FROM document_scope_decisions d
            JOIN analysis_jobs j ON j.job_uuid=d.job_uuid AND j.file_hash=d.file_hash
                AND j.structured_document_version_id=d.document_version_id
            WHERE d.job_uuid=$1 AND d.dispatch_status IN ('pending','queued')
            ORDER BY d.revision DESC LIMIT 1""", job_uuid))
    finally:
        await DatabaseConnection.release(conn)


def apply_profile(profile, decision: dict | None):
    if decision is None:
        return profile
    from src.schemas.document_profile import ProfileField, PROFILE_STATUS_PARTIAL, PROFILE_STATUS_RESOLVED
    previous = profile.subject_level.to_dict()
    profile.subject_level = ProfileField(value=MATERIAL_SCOPE_LEVEL[decision["material_scope"]],
        source="human_confirmation", evidence=f"人工确认 {decision['request_id']}：{decision['note']}；{decision['evidence_reference']}",
        rejected=[previous])
    profile.profile_status = PROFILE_STATUS_RESOLVED if profile.kind in {"budget", "final"} and profile.subject_level.resolved else PROFILE_STATUS_PARTIAL
    return profile


async def dispatch_pending() -> int:
    """提交后可靠投递。Worker 周期重试；崩溃后按请求 ID 恢复，不重复启动分析。"""
    if not os.getenv("DATABASE_URL"):
        return 0
    from api import runtime
    from src.db.connection import DatabaseConnection
    from src.services.analysis_result_store import ensure_analysis_persistence_ready
    if not await ensure_analysis_persistence_ready():
        return 0
    conn = await DatabaseConnection.acquire()
    count = 0
    try:
        rows = await conn.fetch("SELECT * FROM document_scope_decisions WHERE dispatch_status='pending' "
                                "AND (retry_not_before IS NULL OR retry_not_before<=NOW()) ORDER BY created_at LIMIT 10")
        for raw in rows:
            row = decision_dict(raw)
            key = "document-scope-dispatch:" + row["job_uuid"]
            if not await conn.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", key):
                continue
            try:
                latest = await latest_decision(conn, row["job_uuid"], row["file_hash"], row["document_version_id"])
                if latest["request_id"] != row["request_id"]:
                    continue
                job_dir = runtime.UPLOAD_ROOT / row["job_uuid"]
                status = runtime.read_json_file(job_dir / "status.json", default={})
                if status.get("status") in runtime.ACTIVE_ANALYSIS_STATUSES:
                    if status.get("document_scope_request_id") != row["request_id"]:
                        continue
                else:
                    pdf = runtime.find_first_pdf(job_dir)
                    if hashlib.sha256(pdf.read_bytes()).hexdigest() != row["file_hash"]:
                        raise ValueError("原件哈希已变化，拒绝将口径确认应用于其他文件")
                    await runtime.reanalyze_job(row["job_uuid"], {
                        "document_scope_request_id": row["request_id"], "mode": status.get("mode") or "legacy",
                        "use_local_rules": status.get("use_local_rules", True),
                        "use_ai_assist": status.get("use_ai_assist", False),
                    })
                await conn.execute("UPDATE document_scope_decisions SET dispatch_status='queued',dispatch_error=NULL "
                                   "WHERE request_id=$1::uuid AND dispatch_status='pending'", row["request_id"])
                count += 1
            except Exception as exc:
                await conn.execute("UPDATE document_scope_decisions SET dispatch_error=$2,dispatch_attempts=dispatch_attempts+1, "
                                   "retry_not_before=NOW()+LEAST(300,5*POWER(2,LEAST(dispatch_attempts,6)))*INTERVAL '1 second' "
                                   "WHERE request_id=$1::uuid",
                                   row["request_id"], str(exc)[:500])
                logger.exception("Document scope reanalysis dispatch failed for %s", row["job_uuid"])
            finally:
                await conn.execute("SELECT pg_advisory_unlock(hashtext($1))", key)
    finally:
        await DatabaseConnection.release(conn)
    return count
