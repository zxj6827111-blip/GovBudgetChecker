"""Persist analysis job snapshots into PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from src.db.connection import DatabaseConnection
from src.db.migrations import run_migrations

logger = logging.getLogger(__name__)

_DB_READY = False
_DB_READY_LOCK = asyncio.Lock()
_ACTIVE_JOB_STATUSES = {"queued", "processing", "running"}
_UUIDISH_FILENAME_RE = re.compile(r"^[0-9a-f]{24,}\.pdf$", re.I)


async def ensure_analysis_persistence_ready() -> bool:
    """Initialize PostgreSQL access for analysis result persistence."""
    global _DB_READY

    if _DB_READY and DatabaseConnection.is_initialized():
        return True
    if not (os.getenv("DATABASE_URL") or "").strip():
        return False

    async with _DB_READY_LOCK:
        if _DB_READY and DatabaseConnection.is_initialized():
            return True
        try:
            await DatabaseConnection.initialize()
            await run_migrations()
            _DB_READY = True
        except Exception:
            logger.exception("Failed to initialize analysis result persistence")
            _DB_READY = False
            return False
    return True


async def persist_analysis_job_snapshot(
    payload: Dict[str, Any],
    *,
    include_results: bool = False,
) -> bool:
    """Best-effort persistence for file-backed job snapshots."""
    if not isinstance(payload, dict):
        return False

    job_uuid = str(payload.get("job_id") or "").strip()
    if not job_uuid:
        return False

    if not await ensure_analysis_persistence_ready():
        return False

    conn = None
    try:
        conn = await DatabaseConnection.acquire()
        async with conn.transaction():
            job_db_id = await _upsert_analysis_job(conn, payload)
            status = str(payload.get("status") or "").strip().lower()
            if include_results:
                await _upsert_analysis_result(conn, job_db_id, payload)
            elif status in _ACTIVE_JOB_STATUSES:
                await conn.execute("DELETE FROM analysis_results WHERE job_id = $1", job_db_id)
        return True
    except Exception:
        logger.exception("Failed to persist analysis snapshot for job %s", job_uuid)
        return False
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)


async def _upsert_analysis_job(conn, payload: Dict[str, Any]) -> int:
    metadata = _build_job_metadata(payload)
    organization_fk = await _resolve_organization_fk(conn, payload.get("organization_id"))
    status = str(payload.get("status") or "pending").strip() or "pending"
    mode = str(payload.get("mode") or "legacy").strip() or "legacy"
    started_at = _resolve_started_at(payload)
    completed_at = _resolve_completed_at(payload)
    error_message = _resolve_error_message(payload)

    return int(
        await conn.fetchval(
            """
            INSERT INTO analysis_jobs (
                job_uuid,
                filename,
                file_hash,
                organization_id,
                status,
                mode,
                started_at,
                completed_at,
                error_message,
                metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb)
            ON CONFLICT (job_uuid)
            DO UPDATE SET
                filename = CASE
                    WHEN EXCLUDED.filename <> '' THEN EXCLUDED.filename
                    ELSE analysis_jobs.filename
                END,
                file_hash = CASE
                    WHEN EXCLUDED.file_hash <> '' THEN EXCLUDED.file_hash
                    ELSE analysis_jobs.file_hash
                END,
                organization_id = COALESCE(EXCLUDED.organization_id, analysis_jobs.organization_id),
                status = EXCLUDED.status,
                mode = EXCLUDED.mode,
                started_at = COALESCE(analysis_jobs.started_at, EXCLUDED.started_at),
                completed_at = COALESCE(EXCLUDED.completed_at, analysis_jobs.completed_at),
                error_message = EXCLUDED.error_message,
                metadata = COALESCE(analysis_jobs.metadata, '{}'::jsonb) || EXCLUDED.metadata,
                updated_at = NOW()
            RETURNING id
            """,
            str(payload.get("job_id") or "").strip(),
            _resolve_filename(payload),
            str(payload.get("checksum") or "").strip(),
            organization_fk,
            status,
            mode,
            started_at,
            completed_at,
            error_message,
            _to_json(metadata),
        )
    )


async def _upsert_analysis_result(conn, job_db_id: int, payload: Dict[str, Any]) -> None:
    result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}

    ai_findings = _normalize_list(
        result_payload.get("ai_findings", payload.get("ai_findings"))
    )
    rule_findings = _normalize_list(
        result_payload.get("rule_findings", payload.get("rule_findings"))
    )
    merged_result = _normalize_dict(
        result_payload.get("merged", payload.get("merged"))
    )

    if not ai_findings and not rule_findings:
        legacy_issues = _flatten_legacy_issues(result_payload.get("issues", payload.get("issues")))
        if legacy_issues:
            rule_findings = legacy_issues

    await conn.execute(
        """
        INSERT INTO analysis_results (
            job_id,
            ai_findings,
            rule_findings,
            merged_result,
            raw_response
        )
        VALUES ($1, $2::jsonb, $3::jsonb, $4::jsonb, $5::jsonb)
        ON CONFLICT (job_id)
        DO UPDATE SET
            ai_findings = EXCLUDED.ai_findings,
            rule_findings = EXCLUDED.rule_findings,
            merged_result = EXCLUDED.merged_result,
            raw_response = EXCLUDED.raw_response
        """,
        job_db_id,
        _to_json(ai_findings),
        _to_json(rule_findings),
        _to_json(merged_result),
        _to_json(payload),
    )


def _build_job_metadata(payload: Dict[str, Any]) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {}
    for key in (
        "organization_id",
        "organization_name",
        "organization_match_type",
        "organization_match_confidence",
        "fiscal_year",
        "doc_type",
        "report_year",
        "report_kind",
        "use_local_rules",
        "use_ai_assist",
        "progress",
        "stage",
    ):
        value = payload.get(key)
        if value is not None:
            metadata[key] = value

    structured_ingest = payload.get("structured_ingest")
    if isinstance(structured_ingest, dict) and structured_ingest:
        metadata["structured_ingest"] = structured_ingest

    result_payload = payload.get("result")
    if isinstance(result_payload, dict):
        result_meta = result_payload.get("meta")
        if isinstance(result_meta, dict) and result_meta:
            metadata["result_meta"] = result_meta

    return metadata


async def _resolve_organization_fk(conn, raw_value: Any) -> Optional[int]:
    try:
        organization_id = int(str(raw_value).strip())
    except Exception:
        return None

    if organization_id <= 0:
        return None

    exists = await conn.fetchval(
        "SELECT id FROM organizations WHERE id = $1 LIMIT 1",
        organization_id,
    )
    return int(exists) if exists is not None else None


def _resolve_filename(payload: Dict[str, Any]) -> str:
    filename = str(payload.get("filename") or "").strip()
    job_uuid = str(payload.get("job_id") or "").strip()
    if filename and not _looks_like_job_uuid_filename(filename, job_uuid):
        return filename

    saved_path = str(payload.get("saved_path") or "").strip()
    if saved_path:
        return saved_path.replace("\\", "/").rsplit("/", 1)[-1]

    uploaded_filename = _resolve_uploaded_pdf_name(job_uuid)
    if uploaded_filename:
        return uploaded_filename

    job_id = job_uuid or "analysis"
    return f"{job_id}.pdf"


def _resolve_started_at(payload: Dict[str, Any]) -> Optional[datetime]:
    result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    result_meta = result_payload.get("meta") if isinstance(result_payload.get("meta"), dict) else {}
    for raw in (
        result_meta.get("started_at"),
        payload.get("job_created_at"),
        payload.get("version_created_at"),
        payload.get("ts"),
    ):
        parsed = _parse_timestamp(raw)
        if parsed is not None:
            return parsed
    return None


def _resolve_completed_at(payload: Dict[str, Any]) -> Optional[datetime]:
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"done", "error", "completed", "failed"}:
        return None

    result_payload = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    result_meta = result_payload.get("meta") if isinstance(result_payload.get("meta"), dict) else {}
    for raw in (
        result_meta.get("finished_at"),
        payload.get("completed_at"),
        payload.get("ts"),
    ):
        parsed = _parse_timestamp(raw)
        if parsed is not None:
            return parsed
    return None


def _resolve_error_message(payload: Dict[str, Any]) -> Optional[str]:
    for raw in (
        payload.get("error"),
        payload.get("error_message"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    return None


def _flatten_legacy_issues(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []

    flattened: List[Dict[str, Any]] = []
    for key in ("all", "error", "warn", "info"):
        value = raw.get(key)
        if not isinstance(value, list):
            continue
        flattened.extend(item for item in value if isinstance(item, dict))

    seen_ids = set()
    deduped: List[Dict[str, Any]] = []
    for item in flattened:
        issue_id = str(item.get("id") or "").strip()
        if issue_id:
            if issue_id in seen_ids:
                continue
            seen_ids.add(issue_id)
        deduped.append(item)
    return deduped


def _normalize_list(raw: Any) -> List[Any]:
    value = _coerce_json_value(raw)
    return value if isinstance(value, list) else []


def _normalize_dict(raw: Any) -> Dict[str, Any]:
    value = _coerce_json_value(raw)
    return value if isinstance(value, dict) else {}


def _coerce_json_value(raw: Any) -> Any:
    if isinstance(raw, (list, dict)):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return raw
        try:
            return json.loads(text)
        except Exception:
            return raw
    return raw


def _parse_timestamp(raw: Any) -> Optional[datetime]:
    try:
        value = float(raw)
    except Exception:
        return None
    if value <= 0:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _to_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


async def list_persisted_analysis_jobs(
    *,
    limit: int = 50,
    offset: int = 0,
    search: str = "",
    status: str = "",
    mode: str = "",
) -> Dict[str, Any]:
    """Return paginated persisted analysis job summaries."""
    normalized_limit = min(max(int(limit or 50), 1), 200)
    normalized_offset = max(int(offset or 0), 0)
    normalized_search = str(search or "").strip()
    normalized_status = str(status or "").strip().lower()
    normalized_mode = str(mode or "").strip().lower()

    if not await ensure_analysis_persistence_ready():
        return {
            "available": False,
            "items": [],
            "total": 0,
            "limit": normalized_limit,
            "offset": normalized_offset,
            "summary": {
                "total": 0,
                "done": 0,
                "processing": 0,
                "queued": 0,
                "error": 0,
                "ai_findings_total": 0,
                "rule_findings_total": 0,
            },
            "detail": "database unavailable",
        }

    conn = None
    try:
        conn = await DatabaseConnection.acquire()
        where_sql, args = _build_job_filters(
            search=normalized_search,
            status=normalized_status,
            mode=normalized_mode,
        )

        summary = await conn.fetchrow(
            f"""
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(CASE WHEN j.status = 'done' THEN 1 ELSE 0 END), 0) AS done,
                COALESCE(SUM(CASE WHEN j.status IN ('processing', 'running') THEN 1 ELSE 0 END), 0) AS processing,
                COALESCE(SUM(CASE WHEN j.status = 'queued' THEN 1 ELSE 0 END), 0) AS queued,
                COALESCE(SUM(CASE WHEN j.status IN ('error', 'failed') THEN 1 ELSE 0 END), 0) AS error,
                COALESCE(SUM(COALESCE(jsonb_array_length(r.ai_findings), 0)), 0) AS ai_findings_total,
                COALESCE(SUM(COALESCE(jsonb_array_length(r.rule_findings), 0)), 0) AS rule_findings_total
            FROM analysis_jobs j
            LEFT JOIN analysis_results r ON r.job_id = j.id
            {where_sql}
            """,
            *args,
        )

        list_args = [*args, normalized_limit, normalized_offset]
        rows = await conn.fetch(
            f"""
            SELECT
                j.id,
                j.job_uuid,
                j.filename,
                j.file_hash,
                j.status,
                j.mode,
                j.started_at,
                j.completed_at,
                j.error_message,
                j.metadata,
                j.created_at,
                j.updated_at,
                COALESCE(jsonb_array_length(r.ai_findings), 0) AS ai_findings_count,
                COALESCE(jsonb_array_length(r.rule_findings), 0) AS rule_findings_count,
                COALESCE(
                    NULLIF(r.merged_result -> 'totals' ->> 'merged', '')::INTEGER,
                    COALESCE(jsonb_array_length(r.ai_findings), 0) + COALESCE(jsonb_array_length(r.rule_findings), 0)
                ) AS merged_findings_count,
                r.id IS NOT NULL AS has_results
            FROM analysis_jobs j
            LEFT JOIN analysis_results r ON r.job_id = j.id
            {where_sql}
            ORDER BY COALESCE(j.completed_at, j.started_at, j.updated_at, j.created_at) DESC, j.id DESC
            LIMIT ${len(args) + 1}
            OFFSET ${len(args) + 2}
            """,
            *list_args,
        )

        return {
            "available": True,
            "items": [_serialize_job_summary_row(row) for row in rows],
            "total": int(summary["total"] or 0) if summary else 0,
            "limit": normalized_limit,
            "offset": normalized_offset,
            "summary": {
                "total": int(summary["total"] or 0) if summary else 0,
                "done": int(summary["done"] or 0) if summary else 0,
                "processing": int(summary["processing"] or 0) if summary else 0,
                "queued": int(summary["queued"] or 0) if summary else 0,
                "error": int(summary["error"] or 0) if summary else 0,
                "ai_findings_total": int(summary["ai_findings_total"] or 0) if summary else 0,
                "rule_findings_total": int(summary["rule_findings_total"] or 0) if summary else 0,
            },
            "filters": {
                "search": normalized_search,
                "status": normalized_status,
                "mode": normalized_mode,
            },
        }
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)


async def get_persisted_analysis_job_detail(job_uuid: str) -> Optional[Dict[str, Any]]:
    """Return persisted analysis job detail by job uuid."""
    normalized_job_uuid = str(job_uuid or "").strip()
    if not normalized_job_uuid:
        return None

    if not await ensure_analysis_persistence_ready():
        return {
            "available": False,
            "job_uuid": normalized_job_uuid,
            "detail": "database unavailable",
        }

    conn = None
    try:
        conn = await DatabaseConnection.acquire()
        row = await conn.fetchrow(
            """
            SELECT
                j.id,
                j.job_uuid,
                j.filename,
                j.file_hash,
                j.status,
                j.mode,
                j.started_at,
                j.completed_at,
                j.error_message,
                j.metadata,
                j.created_at,
                j.updated_at,
                COALESCE(jsonb_array_length(r.ai_findings), 0) AS ai_findings_count,
                COALESCE(jsonb_array_length(r.rule_findings), 0) AS rule_findings_count,
                COALESCE(
                    NULLIF(r.merged_result -> 'totals' ->> 'merged', '')::INTEGER,
                    COALESCE(jsonb_array_length(r.ai_findings), 0) + COALESCE(jsonb_array_length(r.rule_findings), 0)
                ) AS merged_findings_count,
                r.ai_findings,
                r.rule_findings,
                r.merged_result,
                r.raw_response
            FROM analysis_jobs j
            LEFT JOIN analysis_results r ON r.job_id = j.id
            WHERE j.job_uuid = $1
            LIMIT 1
            """,
            normalized_job_uuid,
        )
        if row is None:
            return None

        payload = _serialize_job_summary_row(row)
        metadata = _normalize_dict(row.get("metadata"))
        payload.update(
            {
                "available": True,
                "ai_findings": _normalize_list(row.get("ai_findings")),
                "rule_findings": _normalize_list(row.get("rule_findings")),
                "merged_result": _normalize_dict(row.get("merged_result")),
                "raw_response": _normalize_dict(row.get("raw_response")),
                "structured_ingest": _normalize_dict(metadata.get("structured_ingest")),
                "result_meta": _normalize_dict(metadata.get("result_meta")),
            }
        )
        return payload
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)


def _build_job_filters(
    *,
    search: str,
    status: str,
    mode: str,
) -> tuple[str, List[Any]]:
    clauses: List[str] = []
    args: List[Any] = []

    if search:
        args.append(f"%{search}%")
        idx = len(args)
        clauses.append(
            f"""(
                j.job_uuid ILIKE ${idx}
                OR j.filename ILIKE ${idx}
                OR COALESCE(j.metadata ->> 'organization_name', '') ILIKE ${idx}
            )"""
        )

    if status:
        args.append(status)
        clauses.append(f"LOWER(j.status) = ${len(args)}")

    if mode:
        args.append(mode)
        clauses.append(f"LOWER(j.mode) = ${len(args)}")

    if not clauses:
        return "", args
    return "WHERE " + " AND ".join(clauses), args


def _serialize_job_summary_row(row: Any) -> Dict[str, Any]:
    metadata = _normalize_dict(row.get("metadata"))
    structured_ingest = _normalize_dict(metadata.get("structured_ingest"))
    result_meta = _normalize_dict(metadata.get("result_meta"))
    elapsed_ms = _normalize_dict(result_meta.get("elapsed_ms"))
    job_uuid = str(row.get("job_uuid") or "")
    resolved_filename = _resolve_effective_filename(job_uuid, row.get("filename"))
    organization_name = str(metadata.get("organization_name") or "")
    report_year = metadata.get("report_year")
    doc_type = str(metadata.get("doc_type") or "")
    report_kind = str(metadata.get("report_kind") or "")
    display_title, display_subtitle = _build_display_labels(
        job_uuid=job_uuid,
        filename=resolved_filename,
        organization_name=organization_name,
        report_year=report_year,
        doc_type=doc_type,
        report_kind=report_kind,
    )

    return {
        "job_uuid": job_uuid,
        "filename": resolved_filename,
        "file_hash": str(row.get("file_hash") or ""),
        "status": str(row.get("status") or ""),
        "mode": str(row.get("mode") or ""),
        "started_at": _serialize_datetime(row.get("started_at")),
        "completed_at": _serialize_datetime(row.get("completed_at")),
        "created_at": _serialize_datetime(row.get("created_at")),
        "updated_at": _serialize_datetime(row.get("updated_at")),
        "error_message": str(row.get("error_message") or ""),
        "organization_name": organization_name,
        "organization_id": metadata.get("organization_id"),
        "report_year": report_year,
        "doc_type": doc_type,
        "report_kind": report_kind,
        "display_title": display_title,
        "display_subtitle": display_subtitle,
        "use_local_rules": bool(metadata.get("use_local_rules")),
        "use_ai_assist": bool(metadata.get("use_ai_assist")),
        "stage": str(metadata.get("stage") or ""),
        "progress": metadata.get("progress"),
        "ai_findings_count": int(row.get("ai_findings_count") or 0),
        "rule_findings_count": int(row.get("rule_findings_count") or 0),
        "merged_findings_count": int(row.get("merged_findings_count") or 0),
        "has_results": bool(row.get("has_results")),
        "structured_ingest_status": str(structured_ingest.get("status") or ""),
        "structured_document_version_id": structured_ingest.get("document_version_id"),
        "structured_report_id": _normalize_dict(structured_ingest.get("ps_sync")).get("report_id"),
        "elapsed_total_ms": elapsed_ms.get("total"),
    }


def _serialize_datetime(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return None


def _resolve_effective_filename(job_uuid: str, raw_filename: Any) -> str:
    filename = str(raw_filename or "").strip()
    if filename and not _looks_like_job_uuid_filename(filename, job_uuid):
        return filename

    uploaded_filename = _resolve_uploaded_pdf_name(job_uuid)
    if uploaded_filename:
        return uploaded_filename
    return filename


def _resolve_uploaded_pdf_name(job_uuid: str) -> str:
    normalized_job_uuid = str(job_uuid or "").strip()
    if not normalized_job_uuid:
        return ""

    upload_root = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
    job_dir = upload_root / normalized_job_uuid
    try:
        pdfs = sorted(job_dir.glob("*.pdf"))
    except Exception:
        return ""

    if not pdfs:
        return ""
    return pdfs[0].name


def _looks_like_job_uuid_filename(filename: str, job_uuid: str) -> bool:
    normalized_filename = str(filename or "").strip()
    normalized_job_uuid = str(job_uuid or "").strip()
    if not normalized_filename:
        return False

    stem = Path(normalized_filename).stem.strip().lower()
    if normalized_job_uuid and stem == normalized_job_uuid.lower():
        return True
    return bool(_UUIDISH_FILENAME_RE.fullmatch(normalized_filename))


def _build_display_labels(
    *,
    job_uuid: str,
    filename: str,
    organization_name: str,
    report_year: Any,
    doc_type: str,
    report_kind: str,
) -> tuple[str, str]:
    year_label = _format_report_year_label(report_year)
    kind = _normalize_report_kind(doc_type, report_kind, filename)
    kind_label = _report_kind_label(kind)
    cleaned_filename = str(filename or "").strip()

    if organization_name:
        title = organization_name
        if year_label and kind_label:
            title = f"{organization_name}{year_label}{kind_label}"
        elif year_label:
            title = f"{organization_name}{year_label}"
        elif kind_label and kind != "unknown":
            title = f"{organization_name}{kind_label}"
        subtitle = cleaned_filename or job_uuid
        return title, subtitle

    humanized_filename = _humanize_filename(cleaned_filename)
    if humanized_filename:
        subtitle_parts = [part for part in (year_label, kind_label if kind_label else "", cleaned_filename) if part]
        return humanized_filename, " / ".join(_dedupe_strings(subtitle_parts))

    fallback_title = cleaned_filename or job_uuid
    subtitle_parts = [part for part in (year_label, kind_label if kind_label else "", job_uuid) if part]
    return fallback_title, " / ".join(_dedupe_strings(subtitle_parts))


def _format_report_year_label(value: Any) -> str:
    try:
        year = int(str(value).strip())
    except Exception:
        return ""
    if year <= 0:
        return ""
    return f"{year}年"


def _normalize_report_kind(doc_type: str, report_kind: str, filename: str) -> str:
    normalized_kind = str(report_kind or "").strip().lower()
    if normalized_kind in {"budget", "final"}:
        return normalized_kind

    joined = " ".join(
        part for part in (str(doc_type or "").strip().lower(), str(filename or "").strip().lower()) if part
    )
    if "budget" in joined or "预算" in joined:
        return "budget"
    if any(token in joined for token in ("final", "settlement", "accounts")) or "决算" in joined:
        return "final"
    return "unknown"


def _report_kind_label(kind: str) -> str:
    if kind == "budget":
        return "部门预算"
    if kind == "final":
        return "部门决算"
    return "报告"


def _humanize_filename(filename: str) -> str:
    text = Path(str(filename or "").strip()).stem.strip()
    if not text:
        return ""
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _dedupe_strings(items: List[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(text)
    return deduped
