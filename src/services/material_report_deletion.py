"""业务报告级完整级联删除服务。

将「部门报告」及任务管理中的删除操作统一收口到同一套事务边界内，
确保上传任务、本地文件目录、问题工作流、解析产物、事实快照、兼容投影、
文件版本、复核/督办记录以及材料台账槽位之间的关联被完整且安全地处理。
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from fastapi import HTTPException

from api import runtime
from src.db.connection import DatabaseConnection
from src.db.transaction import transaction_scope
from src.services import issue_workflow_store, review_slot_sync
from src.services.analysis_result_store import ensure_analysis_persistence_ready
from src.services.material_slot_service import MaterialSlotService
from src.services.material_slot_status import ANALYSIS_NOT_STARTED, REVIEW_NONE

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _dedupe_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    seen: Set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _parse_uuid_or_none(raw: Any) -> Optional[uuid.UUID]:
    text = str(raw or "").strip()
    if not text or not _UUID_RE.match(text):
        return None
    try:
        return uuid.UUID(text)
    except ValueError:
        return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _existing_tables(conn: Any, table_names: Sequence[str]) -> Set[str]:
    rows = await conn.fetch(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = ANY(current_schemas(false))
          AND table_name = ANY($1::text[])
        """,
        list(table_names),
    )
    return {str(row["table_name"]) for row in rows or []}


def _scan_local_jobs_for_targets(
    *,
    target_slot_ids: Set[str],
    target_version_ids: Set[int],
) -> Set[str]:
    """扫描本地轻量 sidecar，补齐尚未写入数据库或仅落盘的任务目录。"""
    if not target_slot_ids and not target_version_ids:
        return set()
    matched: Set[str] = set()
    for job_dir in runtime.iter_job_dirs():
        structured = runtime.read_structured_ingest_payload(job_dir)
        if not isinstance(structured, dict) or not structured:
            continue
        version_id = _coerce_int(structured.get("document_version_id"))
        slot_id = str((structured.get("material_slot") or {}).get("slot_id") or "").strip()
        if (version_id is not None and version_id in target_version_ids) or (
            slot_id and slot_id in target_slot_ids
        ):
            matched.add(job_dir.name)
    return matched


def _local_job_version_hint(job_id: str) -> Optional[int]:
    job_dir = Path(runtime.UPLOAD_ROOT) / str(job_id)
    if not job_dir.is_dir():
        return None
    structured = runtime.read_structured_ingest_payload(job_dir)
    if isinstance(structured, dict):
        version_id = _coerce_int(structured.get("document_version_id"))
        if version_id is not None:
            return version_id
    return None


def _check_queue_active_and_cancel_enqueued(target_job_uuids: Sequence[str]) -> None:
    queue = runtime.get_job_queue()
    if queue is None:
        return
    active = getattr(queue, "_active", None) or set()
    for job_id in target_job_uuids:
        if job_id in active:
            raise HTTPException(
                status_code=409,
                detail="该报告存在正在执行的分析任务，请等待分析完成后再删除",
            )
    enqueued = getattr(queue, "_enqueued", None)
    probe_cache = getattr(queue, "_status_probe_cache", None)
    for job_id in target_job_uuids:
        if isinstance(enqueued, set):
            enqueued.discard(job_id)
        if isinstance(probe_cache, dict):
            probe_cache.pop(job_id, None)


def _cleanup_local_job_dir(job_id: str) -> None:
    job_dir = Path(runtime.UPLOAD_ROOT) / str(job_id)
    if job_dir.exists():
        runtime.delete_job(job_id)
        return
    runtime._JOB_SUMMARY_CACHE.pop(str(job_id), None)
    if runtime.ORG_AVAILABLE:
        try:
            runtime.require_org_storage().unlink_job(job_id)
        except Exception:
            logger.exception("Failed to unlink job %s during cleanup", job_id)


def _execute_tag_count(result: Any) -> int:
    text = str(result or "").strip()
    if not text:
        return 0
    tail = text.rsplit(" ", 1)[-1]
    try:
        return int(tail)
    except ValueError:
        return 0


async def _discover_closure_before_locks(
    conn: Any,
    *,
    req_slot_uuids: List[uuid.UUID],
    req_job_ids: List[str],
    tables: Set[str],
) -> Tuple[Set[uuid.UUID], Set[int], Set[str]]:
    """无锁预发现涉及的全部 slot_id / version_id / job_uuid，便于后续按全局锁序一次加锁。"""
    discovered_slot_uuids: Set[uuid.UUID] = set(req_slot_uuids)
    discovered_version_ids: Set[int] = set()
    discovered_job_uuids: Set[str] = set(req_job_ids)

    if req_slot_uuids:
        rows = await conn.fetch(
            """
            SELECT id, current_document_version_id
            FROM material_slots
            WHERE id = ANY($1::uuid[])
            """,
            req_slot_uuids,
        )
        current_ver_ids = [
            int(row["current_document_version_id"])
            for row in rows
            if row["current_document_version_id"] is not None
        ]
        version_rows = await conn.fetch(
            """
            SELECT id
            FROM fiscal_document_versions
            WHERE slot_id = ANY($1::uuid[]) OR id = ANY($2::int[])
            """,
            req_slot_uuids,
            current_ver_ids,
        )
        discovered_version_ids.update(int(row["id"]) for row in version_rows)

    if req_job_ids:
        job_rows = await conn.fetch(
            """
            SELECT job_uuid, structured_document_version_id::bigint AS version_id
            FROM analysis_jobs
            WHERE job_uuid = ANY($1::text[])
            """,
            req_job_ids,
        )
        for row in job_rows:
            if row["version_id"] is not None:
                discovered_version_ids.add(int(row["version_id"]))

        if "review_sessions" in tables:
            rs_rows = await conn.fetch(
                """
                SELECT slot_id, document_version_id
                FROM review_sessions
                WHERE analysis_job_uuid = ANY($1::text[])
                """,
                req_job_ids,
            )
            for row in rs_rows:
                if row["slot_id"] is not None:
                    discovered_slot_uuids.add(row["slot_id"])
                if row["document_version_id"] is not None:
                    discovered_version_ids.add(int(row["document_version_id"]))

        for decision_table in ("document_scope_decisions", "document_writing_scope_decisions"):
            if decision_table in tables:
                d_rows = await conn.fetch(
                    f"""
                    SELECT DISTINCT document_version_id
                    FROM {decision_table}
                    WHERE job_uuid = ANY($1::text[])
                    """,
                    req_job_ids,
                )
                for row in d_rows:
                    if row["document_version_id"] is not None:
                        discovered_version_ids.add(int(row["document_version_id"]))

        for job_id in req_job_ids:
            hinted = _local_job_version_hint(job_id)
            if hinted is not None:
                discovered_version_ids.add(hinted)

    if discovered_version_ids:
        ver_slot_rows = await conn.fetch(
            """
            SELECT slot_id
            FROM fiscal_document_versions
            WHERE id = ANY($1::int[]) AND slot_id IS NOT NULL
            """,
            sorted(discovered_version_ids),
        )
        for row in ver_slot_rows:
            if row["slot_id"] is not None:
                discovered_slot_uuids.add(row["slot_id"])

        ref_slots = await conn.fetch(
            """
            SELECT id
            FROM material_slots
            WHERE current_document_version_id = ANY($1::int[])
            """,
            sorted(discovered_version_ids),
        )
        for row in ref_slots:
            discovered_slot_uuids.add(row["id"])

    return discovered_slot_uuids, discovered_version_ids, discovered_job_uuids


async def delete_material_reports(
    conn: Any,
    *,
    slot_ids: Sequence[str] = (),
    job_ids: Sequence[str] = (),
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """在一个数据库事务内完整删除指定的业务报告（slot_id）或独立分析任务（job_id）。

    严格遵守全系统锁序：
    material_slots → fiscal_document_versions → review_sessions → analysis_jobs。
    """
    normalized_slot_inputs = _dedupe_strings(slot_ids)
    normalized_job_inputs = _dedupe_strings(job_ids)

    failed: List[Dict[str, Any]] = []
    req_slot_uuids: List[uuid.UUID] = []
    req_slot_str_by_uuid: Dict[uuid.UUID, str] = {}
    for raw_sid in normalized_slot_inputs:
        parsed = _parse_uuid_or_none(raw_sid)
        if parsed is None:
            failed.append(
                {
                    "slot_id": raw_sid,
                    "status_code": 400,
                    "detail": "invalid slot_id",
                }
            )
            continue
        req_slot_uuids.append(parsed)
        req_slot_str_by_uuid[parsed] = str(parsed)

    table_probe_names = (
        "material_slots",
        "material_sources",
        "fiscal_documents",
        "fiscal_document_versions",
        "analysis_jobs",
        "review_sessions",
        "review_obligation_decisions",
        "supervision_rounds",
        "supervision_baselines",
        "parse_runs",
        "analysis_fact_snapshots",
        "analysis_revision_results",
        "published_fact_projections",
        "legacy_projection_archives",
        "document_scope_decisions",
        "document_writing_scope_decisions",
        "org_dept_annual_report",
        "workflow_issue_records",
        "jobs",
    )

    deleted_slot_ids: List[str] = []
    reset_baseline_slot_ids: List[str] = []
    updated_slot_ids: List[str] = []
    deleted_version_ids: List[int] = []
    deleted_job_ids: List[str] = []
    deleted_document_ids: List[int] = []
    deleted_review_session_count = 0
    candidate_local_jobs: List[str] = []

    async with transaction_scope(conn):
        tables = await _existing_tables(conn, table_probe_names)

        # 1. 无锁预发现，确定需要锁定的全部槽位集合，避免后续反向加锁触发 ABBA 死锁。
        pre_slots, pre_versions, _ = await _discover_closure_before_locks(
            conn,
            req_slot_uuids=req_slot_uuids,
            req_job_ids=normalized_job_inputs,
            tables=tables,
        )

        # 2. 按全局锁序第 1 级：ORDER BY id FOR UPDATE 锁定全部受影响槽位。
        locked_slots: Dict[uuid.UUID, Dict[str, Any]] = {}
        if pre_slots:
            slot_rows = await conn.fetch(
                """
                SELECT id, slot_key, current_document_version_id, due_at,
                       expected_source_url, applicability_status
                FROM material_slots
                WHERE id = ANY($1::uuid[])
                ORDER BY id
                FOR UPDATE
                """,
                sorted(pre_slots, key=str),
            )
            for row in slot_rows:
                locked_slots[row["id"]] = dict(row)

        found_target_slot_uuids: List[uuid.UUID] = []
        for parsed_uuid in req_slot_uuids:
            if parsed_uuid not in locked_slots:
                failed.append(
                    {
                        "slot_id": req_slot_str_by_uuid[parsed_uuid],
                        "status_code": 404,
                        "detail": "material slot not found",
                    }
                )
            else:
                found_target_slot_uuids.append(parsed_uuid)

        # 检查每个请求的 job_id 是否在 DB 或磁盘上存在。
        existing_req_jobs_in_db: Set[str] = set()
        if normalized_job_inputs:
            db_job_rows = await conn.fetch(
                "SELECT job_uuid FROM analysis_jobs WHERE job_uuid = ANY($1::text[])",
                normalized_job_inputs,
            )
            existing_req_jobs_in_db = {str(row["job_uuid"]) for row in db_job_rows}

        valid_req_jobs: List[str] = []
        for job_id in normalized_job_inputs:
            local_exists = (Path(runtime.UPLOAD_ROOT) / job_id).exists()
            if job_id not in existing_req_jobs_in_db and not local_exists:
                failed.append(
                    {
                        "job_id": job_id,
                        "status_code": 404,
                        "detail": "job_id does not exist",
                    }
                )
            else:
                valid_req_jobs.append(job_id)

        if not found_target_slot_uuids and not valid_req_jobs:
            return {
                "requested_slot_count": len(normalized_slot_inputs),
                "requested_job_count": len(normalized_job_inputs),
                "deleted_slot_ids": [],
                "reset_baseline_slot_ids": [],
                "updated_slot_ids": [],
                "deleted_version_ids": [],
                "deleted_document_ids": [],
                "deleted_job_ids": [],
                "deleted_review_session_count": 0,
                "failed": failed,
            }

        # 3. 确定待删除的文件版本与任务闭包。
        found_target_slot_set = set(found_target_slot_uuids)
        found_slot_current_ver_ids = [
            int(locked_slots[sid]["current_document_version_id"])
            for sid in found_target_slot_uuids
            if locked_slots[sid]["current_document_version_id"] is not None
        ]

        lock_version_candidates = sorted(pre_versions | set(found_slot_current_ver_ids))
        locked_versions: Dict[int, Dict[str, Any]] = {}
        if found_target_slot_uuids or lock_version_candidates:
            v_rows = await conn.fetch(
                """
                SELECT id, document_id, slot_id
                FROM fiscal_document_versions
                WHERE slot_id = ANY($1::uuid[]) OR id = ANY($2::int[])
                ORDER BY id
                FOR UPDATE
                """,
                found_target_slot_uuids,
                lock_version_candidates,
            )
            for row in v_rows:
                locked_versions[int(row["id"])] = dict(row)

        # 属于目标删除槽位的版本一律删除。
        target_version_ids: Set[int] = {
            vid
            for vid, vrow in locked_versions.items()
            if vrow["slot_id"] in found_target_slot_set or vid in set(found_slot_current_ver_ids)
        }

        target_job_uuids: Set[str] = set(valid_req_jobs)
        if target_version_ids:
            linked_jobs = await conn.fetch(
                """
                SELECT job_uuid
                FROM analysis_jobs
                WHERE structured_document_version_id = ANY($1::numeric[])
                """,
                sorted(target_version_ids),
            )
            target_job_uuids.update(str(row["job_uuid"]) for row in linked_jobs)

        if "review_sessions" in tables and (found_target_slot_uuids or target_version_ids):
            session_jobs = await conn.fetch(
                """
                SELECT analysis_job_uuid
                FROM review_sessions
                WHERE slot_id = ANY($1::uuid[]) OR document_version_id = ANY($2::int[])
                """,
                found_target_slot_uuids,
                sorted(target_version_ids),
            )
            for row in session_jobs:
                j_uuid = str(row["analysis_job_uuid"] or "").strip()
                if j_uuid:
                    target_job_uuids.add(j_uuid)

        # 补扫本地仅落盘的任务目录（如入库失败但本地留存的任务）。
        local_matched_jobs = _scan_local_jobs_for_targets(
            target_slot_ids={str(s) for s in found_target_slot_set},
            target_version_ids=target_version_ids,
        )
        target_job_uuids.update(local_matched_jobs)

        # 仅按 job_id 删除时：如果关联版本没有被任何保留中的 analysis_jobs 引用，也清理该版本。
        job_only_candidate_versions = set(locked_versions.keys()) - target_version_ids
        if job_only_candidate_versions:
            referenced_by_kept = await conn.fetch(
                """
                SELECT DISTINCT structured_document_version_id::bigint AS version_id
                FROM analysis_jobs
                WHERE structured_document_version_id = ANY($1::numeric[])
                  AND NOT (job_uuid = ANY($2::text[]))
                """,
                sorted(job_only_candidate_versions),
                sorted(target_job_uuids),
            )
            kept_version_ids = {int(row["version_id"]) for row in referenced_by_kept}
            for vid in job_only_candidate_versions:
                if vid not in kept_version_ids:
                    target_version_ids.add(vid)

        target_version_list = sorted(target_version_ids)
        candidate_document_ids = sorted(
            {
                int(locked_versions[vid]["document_id"])
                for vid in target_version_list
                if vid in locked_versions and locked_versions[vid].get("document_id") is not None
            }
        )

        # 4. 按全局锁序第 3 级 & 第 4 级锁定 review_sessions 与 analysis_jobs。
        target_session_uuids: List[uuid.UUID] = []
        if "review_sessions" in tables and (
            found_target_slot_uuids or target_version_list or target_job_uuids
        ):
            s_rows = await conn.fetch(
                """
                SELECT id
                FROM review_sessions
                WHERE slot_id = ANY($1::uuid[])
                   OR document_version_id = ANY($2::int[])
                   OR analysis_job_uuid = ANY($3::text[])
                ORDER BY id
                FOR UPDATE
                """,
                found_target_slot_uuids,
                target_version_list,
                sorted(target_job_uuids),
            )
            target_session_uuids = [row["id"] for row in s_rows]

        locked_job_uuids: List[str] = []
        if target_job_uuids:
            j_rows = await conn.fetch(
                """
                SELECT job_uuid
                FROM analysis_jobs
                WHERE job_uuid = ANY($1::text[])
                ORDER BY id
                FOR UPDATE
                """,
                sorted(target_job_uuids),
            )
            locked_job_uuids = [str(row["job_uuid"]) for row in j_rows]

        all_target_jobs_sorted = sorted(target_job_uuids | set(locked_job_uuids))

        # 运行中任务保护（必须在修改任何数据之前校验）。
        _check_queue_active_and_cancel_enqueued(all_target_jobs_sorted)

        # 5. 开启事务级不可变触发器 DELETE 豁免，按外键反向依赖逐级清理。
        await conn.execute("SET LOCAL app.allow_admin_report_delete = 'on'")

        if "supervision_rounds" in tables and (
            found_target_slot_uuids or target_version_list or target_session_uuids
        ):
            await conn.execute(
                """
                DELETE FROM supervision_rounds
                WHERE slot_id = ANY($1::uuid[])
                   OR document_version_id = ANY($2::int[])
                   OR feedback_session_id = ANY($3::uuid[])
                   OR verification_session_id = ANY($3::uuid[])
                """,
                found_target_slot_uuids,
                target_version_list,
                target_session_uuids,
            )

        if "review_obligation_decisions" in tables and (
            target_session_uuids or found_target_slot_uuids
        ):
            await conn.execute(
                """
                DELETE FROM review_obligation_decisions
                WHERE review_session_id = ANY($1::uuid[])
                   OR slot_id = ANY($2::uuid[])
                """,
                target_session_uuids,
                found_target_slot_uuids,
            )

        if "review_sessions" in tables and target_session_uuids:
            deleted_res = await conn.execute(
                "DELETE FROM review_sessions WHERE id = ANY($1::uuid[])",
                target_session_uuids,
            )
            deleted_review_session_count = _execute_tag_count(deleted_res)

        if "document_writing_scope_decisions" in tables and (
            all_target_jobs_sorted or target_version_list
        ):
            await conn.execute(
                """
                DELETE FROM document_writing_scope_decisions
                WHERE job_uuid = ANY($1::text[])
                   OR document_version_id = ANY($2::int[])
                """,
                all_target_jobs_sorted,
                target_version_list,
            )

        if "document_scope_decisions" in tables and (
            all_target_jobs_sorted or target_version_list
        ):
            await conn.execute(
                """
                DELETE FROM document_scope_decisions
                WHERE job_uuid = ANY($1::text[])
                   OR document_version_id = ANY($2::int[])
                """,
                all_target_jobs_sorted,
                target_version_list,
            )

        target_parse_run_ids: List[int] = []
        if "parse_runs" in tables and target_version_list:
            pr_rows = await conn.fetch(
                "SELECT id FROM parse_runs WHERE document_version_id = ANY($1::int[])",
                target_version_list,
            )
            target_parse_run_ids = [int(row["id"]) for row in pr_rows]

        target_snapshot_ids: List[int] = []
        if "analysis_fact_snapshots" in tables and (
            all_target_jobs_sorted or target_parse_run_ids
        ):
            snap_rows = await conn.fetch(
                """
                SELECT id
                FROM analysis_fact_snapshots
                WHERE job_uuid = ANY($1::text[])
                   OR parse_run_id = ANY($2::bigint[])
                """,
                all_target_jobs_sorted,
                target_parse_run_ids,
            )
            target_snapshot_ids = [int(row["id"]) for row in snap_rows]

        shared_report_uuids: List[uuid.UUID] = []
        if "published_fact_projections" in tables and (
            target_version_list or all_target_jobs_sorted or target_snapshot_ids
        ):
            proj_rows = await conn.fetch(
                """
                SELECT p.document_id, p.receipt->>'shared_report_id' AS shared_report_id
                FROM published_fact_projections p
                WHERE p.document_version_id = ANY($1::int[])
                   OR p.job_uuid = ANY($2::text[])
                   OR EXISTS (
                       SELECT 1 FROM analysis_fact_snapshots s
                       WHERE s.id = ANY($3::bigint[])
                         AND s.job_uuid = p.job_uuid
                         AND s.revision_id = p.revision_id
                   )
                """,
                target_version_list,
                all_target_jobs_sorted,
                target_snapshot_ids,
            )
            for row in proj_rows:
                parsed_rep = _parse_uuid_or_none(row["shared_report_id"])
                if parsed_rep is not None:
                    shared_report_uuids.append(parsed_rep)

            await conn.execute(
                """
                DELETE FROM published_fact_projections p
                WHERE p.document_version_id = ANY($1::int[])
                   OR p.job_uuid = ANY($2::text[])
                   OR EXISTS (
                       SELECT 1 FROM analysis_fact_snapshots s
                       WHERE s.id = ANY($3::bigint[])
                         AND s.job_uuid = p.job_uuid
                         AND s.revision_id = p.revision_id
                   )
                """,
                target_version_list,
                all_target_jobs_sorted,
                target_snapshot_ids,
            )

        if "legacy_projection_archives" in tables and target_version_list:
            await conn.execute(
                "DELETE FROM legacy_projection_archives WHERE document_version_id = ANY($1::int[])",
                target_version_list,
            )

        if "analysis_revision_results" in tables and (
            target_snapshot_ids or all_target_jobs_sorted
        ):
            await conn.execute(
                """
                DELETE FROM analysis_revision_results AS arr
                WHERE arr.job_uuid = ANY($2::text[])
                   OR EXISTS (
                       SELECT 1 FROM analysis_fact_snapshots AS afs
                       WHERE afs.id = ANY($1::bigint[])
                         AND afs.job_uuid = arr.job_uuid
                         AND afs.revision_id = arr.revision_id
                   )
                """,
                target_snapshot_ids,
                all_target_jobs_sorted,
            )

        if "analysis_fact_snapshots" in tables and target_snapshot_ids:
            await conn.execute(
                "DELETE FROM analysis_fact_snapshots WHERE id = ANY($1::bigint[])",
                target_snapshot_ids,
            )

        if "parse_runs" in tables and target_parse_run_ids:
            await conn.execute(
                "DELETE FROM parse_runs WHERE id = ANY($1::bigint[])",
                target_parse_run_ids,
            )

        if "workflow_issue_records" in tables and all_target_jobs_sorted:
            await conn.execute(
                "DELETE FROM workflow_issue_records WHERE job_uuid = ANY($1::text[])",
                all_target_jobs_sorted,
            )

        if all_target_jobs_sorted:
            await conn.execute(
                "DELETE FROM analysis_jobs WHERE job_uuid = ANY($1::text[])",
                all_target_jobs_sorted,
            )

        if "jobs" in tables and target_version_list:
            await conn.execute(
                "DELETE FROM jobs WHERE document_version_id = ANY($1::int[])",
                target_version_list,
            )

        if target_version_list:
            await conn.execute(
                """
                UPDATE material_slots
                SET current_document_version_id = NULL,
                    updated_at = NOW()
                WHERE current_document_version_id = ANY($1::int[])
                """,
                target_version_list,
            )
            deleted_ver_rows = await conn.fetch(
                "DELETE FROM fiscal_document_versions WHERE id = ANY($1::int[]) RETURNING id",
                target_version_list,
            )
            deleted_version_ids = [int(row["id"]) for row in deleted_ver_rows]

        if candidate_document_ids:
            if "published_fact_projections" in tables:
                deleted_doc_rows = await conn.fetch(
                    """
                    DELETE FROM fiscal_documents d
                    WHERE d.id = ANY($1::int[])
                      AND NOT EXISTS (
                          SELECT 1 FROM fiscal_document_versions v WHERE v.document_id = d.id
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM published_fact_projections p WHERE p.document_id = d.id
                      )
                    RETURNING d.id
                    """,
                    candidate_document_ids,
                )
            else:
                deleted_doc_rows = await conn.fetch(
                    """
                    DELETE FROM fiscal_documents d
                    WHERE d.id = ANY($1::int[])
                      AND NOT EXISTS (
                          SELECT 1 FROM fiscal_document_versions v WHERE v.document_id = d.id
                      )
                    RETURNING d.id
                    """,
                    candidate_document_ids,
                )
            deleted_document_ids = [int(row["id"]) for row in deleted_doc_rows]

        if "org_dept_annual_report" in tables:
            doc_scope_keys = [f"fiscal-document:{doc_id}" for doc_id in deleted_document_ids]
            if shared_report_uuids or all_target_jobs_sorted or doc_scope_keys:
                await conn.execute(
                    """
                    DELETE FROM org_dept_annual_report
                    WHERE id = ANY($1::uuid[])
                       OR COALESCE(fact_projection->>'job_uuid', '') = ANY($2::text[])
                       OR COALESCE(scope_key, '') = ANY($3::text[])
                    """,
                    shared_report_uuids,
                    all_target_jobs_sorted,
                    doc_scope_keys,
                )

        # 6. 处理受影响的 material_slots（含本次目标槽位和因删任务而失去当前版本的槽位）。
        slot_service = MaterialSlotService(conn)
        for slot_uuid in sorted(locked_slots.keys(), key=str):
            slot_str = str(slot_uuid)
            remaining = await conn.fetchrow(
                """
                SELECT id
                FROM fiscal_document_versions
                WHERE slot_id = $1
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                slot_uuid,
            )
            if remaining is not None:
                latest_version_id = int(remaining["id"])
                await conn.execute(
                    """
                    UPDATE material_slots
                    SET current_document_version_id = $2,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    slot_uuid,
                    latest_version_id,
                )
                latest_job = await conn.fetchval(
                    """
                    SELECT job_uuid
                    FROM analysis_jobs
                    WHERE structured_document_version_id = $1::numeric
                    ORDER BY completed_at DESC NULLS LAST, created_at DESC NULLS LAST, id DESC
                    LIMIT 1
                    """,
                    latest_version_id,
                )
                synced = False
                if latest_job:
                    synced_res = await review_slot_sync.best_effort_sync_slot_review_state(
                        conn, str(latest_job)
                    )
                    synced = synced_res is not None
                if not synced:
                    await slot_service.refresh_status(
                        slot_uuid,
                        analysis_state=ANALYSIS_NOT_STARTED,
                        review_state=REVIEW_NONE,
                    )
                updated_slot_ids.append(slot_str)
                continue

            # 该槽位已无任何文件版本：先清理手工上传来源，再决定重置基线槽位或删除槽位。
            if "material_sources" in tables:
                await conn.execute(
                    "DELETE FROM material_sources WHERE slot_id = $1 AND source_kind = 'manual_upload'",
                    slot_uuid,
                )

            slot_row = locked_slots[slot_uuid]
            in_baseline = (
                slot_row.get("due_at") is not None
                or bool(slot_row.get("expected_source_url"))
                or str(slot_row.get("applicability_status") or "applicable") != "applicable"
            )
            if not in_baseline and "supervision_baselines" in tables:
                in_baseline = bool(
                    await conn.fetchval(
                        "SELECT EXISTS (SELECT 1 FROM supervision_baselines WHERE slot_ids ? $1)",
                        slot_str,
                    )
                )

            if in_baseline:
                await conn.execute(
                    """
                    UPDATE material_slots
                    SET current_document_version_id = NULL,
                        caliber_conflict_candidate = NULL,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    slot_uuid,
                )
                await slot_service.refresh_status(
                    slot_uuid,
                    analysis_state=ANALYSIS_NOT_STARTED,
                    review_state=REVIEW_NONE,
                )
                reset_baseline_slot_ids.append(slot_str)
            else:
                if "material_sources" in tables:
                    await conn.execute(
                        "DELETE FROM material_sources WHERE slot_id = $1",
                        slot_uuid,
                    )
                await conn.execute("DELETE FROM material_slots WHERE id = $1", slot_uuid)
                deleted_slot_ids.append(slot_str)

        candidate_local_jobs = all_target_jobs_sorted

    # 7. 数据库事务提交后，清理本地任务目录、组织关联、问题工作流与缓存。
    for job_id in candidate_local_jobs:
        try:
            _cleanup_local_job_dir(job_id)
            deleted_job_ids.append(job_id)
        except HTTPException as exc:
            failed.append(
                {
                    "job_id": job_id,
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }
            )
        except Exception as exc:
            logger.exception("Failed to remove local directory for job %s", job_id)
            failed.append(
                {
                    "job_id": job_id,
                    "status_code": 500,
                    "detail": str(exc),
                }
            )

    try:
        await issue_workflow_store.purge_and_persist_workflow_records_for_jobs(
            candidate_local_jobs,
            slot_ids=deleted_slot_ids + reset_baseline_slot_ids,
        )
    except Exception:
        logger.exception("Failed to purge workflow records after report delete")

    try:
        from api.routes.organizations import clear_department_stats_cache

        clear_department_stats_cache()
    except Exception:
        logger.exception("Failed to clear department stats cache after report delete")

    actor_id = str(actor or "unknown")
    logger.info(
        "Material report deletion completed: actor_id=%s deleted_slots=%d reset_slots=%d deleted_versions=%d deleted_jobs=%d",
        actor_id,
        len(deleted_slot_ids),
        len(reset_baseline_slot_ids),
        len(deleted_version_ids),
        len(deleted_job_ids),
    )
    return {
        "requested_slot_count": len(normalized_slot_inputs),
        "requested_job_count": len(normalized_job_inputs),
        "deleted_slot_ids": deleted_slot_ids,
        "reset_baseline_slot_ids": reset_baseline_slot_ids,
        "updated_slot_ids": updated_slot_ids,
        "deleted_version_ids": deleted_version_ids,
        "deleted_document_ids": deleted_document_ids,
        "deleted_job_ids": deleted_job_ids,
        "deleted_review_session_count": deleted_review_session_count,
        "failed": failed,
    }


async def delete_jobs_with_related_records(
    job_ids: Sequence[str],
    *,
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    """统一删除任务接口入口：有数据库时级联清理数据库关联记录，无数据库时退化到本地清理。"""
    normalized_job_ids = _dedupe_strings(job_ids)
    if not normalized_job_ids:
        return {
            "requested_count": 0,
            "deleted_count": 0,
            "failed_count": 0,
            "deleted_job_ids": [],
            "failed": [],
            "deleted_slot_ids": [],
            "reset_baseline_slot_ids": [],
            "updated_slot_ids": [],
            "deleted_version_ids": [],
        }

    use_db = bool((os.getenv("DATABASE_URL") or "").strip()) and await ensure_analysis_persistence_ready()
    if not use_db:
        _check_queue_active_and_cancel_enqueued(normalized_job_ids)
        deleted: List[str] = []
        failed: List[Dict[str, Any]] = []
        for job_id in normalized_job_ids:
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
            issue_workflow_store.purge_workflow_records_for_jobs(deleted)
            try:
                from api.routes.organizations import clear_department_stats_cache

                clear_department_stats_cache()
            except Exception:
                logger.exception("Failed to clear department stats cache")
        return {
            "requested_count": len(normalized_job_ids),
            "deleted_count": len(deleted),
            "failed_count": len(failed),
            "deleted_job_ids": deleted,
            "failed": failed,
            "deleted_slot_ids": [],
            "reset_baseline_slot_ids": [],
            "updated_slot_ids": [],
            "deleted_version_ids": [],
        }

    conn = None
    try:
        conn = await DatabaseConnection.acquire()
        summary = await delete_material_reports(
            conn,
            job_ids=normalized_job_ids,
            actor=actor,
        )
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)

    deleted_ids = list(summary.get("deleted_job_ids") or [])
    failed_items = list(summary.get("failed") or [])
    return {
        "requested_count": len(normalized_job_ids),
        "deleted_count": len(deleted_ids),
        "failed_count": len(failed_items),
        "deleted_job_ids": deleted_ids,
        "failed": failed_items,
        "deleted_slot_ids": summary.get("deleted_slot_ids") or [],
        "reset_baseline_slot_ids": summary.get("reset_baseline_slot_ids") or [],
        "updated_slot_ids": summary.get("updated_slot_ids") or [],
        "deleted_version_ids": summary.get("deleted_version_ids") or [],
    }
