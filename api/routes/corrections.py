"""Evidence access and append-only human correction/feedback endpoints (ZC-05)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from api import runtime
from api.auth_utils import require_job_access
from src.services.correction_overlay_store import (
    affected_check_ids,
    append_record_with_basis_cas,
    current_analysis_basis_token,
    iter_evidence_refs,
    read_jsonl_records,
    report_evidence_ids,
)

logger = logging.getLogger("api.routes.corrections")

router = APIRouter()
_SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

CORRECTION_FIELDS = frozenset(
    {"raw_value", "unit", "year", "org", "scope", "indicator", "column_mapping", "cell_value", "manual_location"}
)
TABLE_FIELDS = frozenset({"column_mapping", "cell_value"})
FEEDBACK_REASONS = frozenset(
    {
        "parse_error",
        "caliber_binding_error",
        "rule_applicability_error",
        "tolerance_error",
        "duplicate_issue",
        "evidence_location_error",
    }
)


class CorrectionChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_type: Literal["fact", "table_instance"]
    target_id: StrictStr = Field(min_length=1, max_length=240)
    field: Literal[
        "raw_value",
        "unit",
        "year",
        "org",
        "scope",
        "indicator",
        "column_mapping",
        "cell_value",
        "manual_location",
    ]
    value: Any
    evidence_ids: List[StrictStr] = Field(default_factory=list, max_length=10)
    # 对照原页提交的位置（物理页 + bbox）；服务端校验后生成人工证据，可代替 evidence_ids。
    location: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_change(self) -> "CorrectionChange":
        if self.field in TABLE_FIELDS and self.target_type != "table_instance":
            raise ValueError(f"{self.field} corrections must target a table_instance")
        if self.field not in TABLE_FIELDS and self.target_type != "fact":
            raise ValueError("fact field corrections must target a fact")
        if self.field == "year":
            if isinstance(self.value, bool) or not isinstance(self.value, int) or not 1900 <= self.value <= 2200:
                raise ValueError("year correction must be an integer from 1900 through 2200")
        elif self.field == "column_mapping":
            if not isinstance(self.value, dict) or not self.value:
                raise ValueError("column_mapping correction must be a non-empty object")
        elif self.field == "cell_value":
            value = self.value if isinstance(self.value, dict) else {}
            row, col, text = value.get("row_idx"), value.get("col_idx"), value.get("raw_value")
            if (isinstance(row, bool) or isinstance(col, bool) or not isinstance(row, int) or not isinstance(col, int)
                    or row < 0 or col < 0 or not isinstance(text, str) or not text.strip() or len(text) > 100):
                raise ValueError("cell_value correction needs row_idx, col_idx and a raw_value of 1-100 characters")
        elif self.field == "manual_location":
            if not isinstance(self.value, dict):
                raise ValueError("manual_location correction must be an object")
        elif not isinstance(self.value, str) or not self.value.strip():
            raise ValueError(f"{self.field} correction must be a non-empty string")
        if self.field != "manual_location" and not self.evidence_ids and not self.location:
            raise ValueError("correction requires at least one evidence_id or an original-page location")
        return self


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_basis_token: StrictStr = Field(min_length=1, max_length=300)
    changes: List[CorrectionChange] = Field(min_length=1, max_length=50)
    reason: StrictStr = Field(min_length=3, max_length=2000)


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_basis_token: StrictStr = Field(min_length=1, max_length=300)
    feedback_type: Literal["false_positive", "missed_issue"]
    reason_code: StrictStr
    issue_id: Optional[StrictStr] = Field(default=None, max_length=240)
    rule_id: Optional[StrictStr] = Field(default=None, max_length=240)
    evidence_ids: List[StrictStr] = Field(default_factory=list, max_length=20)
    context: StrictStr = Field(min_length=3, max_length=4000)

    @model_validator(mode="after")
    def validate_feedback(self) -> "FeedbackRequest":
        if self.reason_code not in FEEDBACK_REASONS:
            raise ValueError("reason_code is not in the fixed feedback reason set")
        if self.feedback_type == "false_positive" and not self.issue_id:
            raise ValueError("false_positive feedback requires issue_id")
        if self.feedback_type == "missed_issue" and (not self.rule_id or not self.evidence_ids):
            raise ValueError("missed_issue feedback requires rule_id and evidence_ids")
        return self


def _job_dir(job_id: str) -> Path:
    return runtime.UPLOAD_ROOT / job_id


def _validate_job_id(job_id: str) -> None:
    if not _SAFE_JOB_ID.fullmatch(job_id) or job_id in {".", ".."}:
        raise HTTPException(status_code=404, detail="job_id does not exist")
    root = runtime.UPLOAD_ROOT.resolve()
    candidate = (root / job_id).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=404, detail="job_id does not exist")


def _revision_payload(job_id: str, revision: Optional[str]) -> Dict[str, Any]:
    # 旧协议历史与报告下载共用原始字节摘要校验，不能另开不校验的证据入口。
    from api.routes.reports import _load_revision_payload
    return _load_revision_payload(job_id, revision)


async def _bound_evidence_payload(job_id: str, revision: Optional[str]) -> Dict[str, Any]:
    if os.getenv('DATABASE_URL'):
        from src.services.analysis_fact_snapshot import read_database_result
        try:
            payload = await read_database_result(job_id, revision)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        if payload is not None:
            return payload
    return _revision_payload(job_id, revision)


def _document_version_id(payload: Dict[str, Any]) -> Optional[int]:
    sources = [payload]
    for key in ("structured_ingest", "analysis_metadata", "metadata", "result"):
        value = payload.get(key)
        if isinstance(value, dict):
            sources.append(value)
            nested = value.get("structured_ingest")
            if isinstance(nested, dict):
                sources.append(nested)
    for source in sources:
        for key in ("document_version_id", "current_document_version_id"):
            try:
                value = int(source.get(key))
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
    return None


async def _load_persisted_evidence(payload: Dict[str, Any], evidence_id: str) -> Optional[Dict[str, Any]]:
    document_version_id = _document_version_id(payload)
    if document_version_id is None or not os.getenv("DATABASE_URL", "").strip():
        return None
    from src.db.connection import DatabaseConnection
    from src.services.analysis_result_store import ensure_analysis_persistence_ready

    if not await ensure_analysis_persistence_ready():
        raise HTTPException(status_code=503, detail="evidence database unavailable")
    conn = None
    try:
        conn = await DatabaseConnection.acquire()
        row = await conn.fetchrow(
            """
            SELECT pe.evidence_id, pe.file_hash, pe.physical_page, pe.printed_page,
                   pe.source_cell_id, pe.source_block_id, pe.quote, pe.bbox,
                   pe.source_method, pe.verification_status, pe.location_level, pe.source_provenance
            FROM parse_evidence pe
            JOIN parse_runs pr ON pr.id = pe.parse_run_id
            WHERE pe.evidence_id = $1 AND pr.document_version_id = $2
            ORDER BY pr.id DESC
            LIMIT 1
            """,
            evidence_id,
            document_version_id,
        )
        return dict(row) if row else None
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - DB drivers expose multiple error types
        raise HTTPException(status_code=503, detail="evidence database unavailable") from exc
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)


def _parse_bbox(value: Any) -> Optional[List[float]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        return None
    try:
        bbox = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(item) for item in bbox) or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _validate_manual_location(job_id: str, job_dir: Path, value: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(value.get("physical_page"), bool):
        raise HTTPException(status_code=422, detail="manual_location needs a positive physical_page")
    try:
        page_number = int(value.get("physical_page"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="manual_location needs physical_page") from exc
    bbox = _parse_bbox(value.get("bbox"))
    if not bbox:
        raise HTTPException(status_code=422, detail="manual_location needs a valid bbox")
    try:
        import fitz

        pdf_path = runtime.find_first_pdf(job_dir)
        with fitz.open(pdf_path) as document:
            if page_number < 1 or page_number > document.page_count:
                raise HTTPException(status_code=422, detail="manual_location physical_page is out of range")
            rect = document.load_page(page_number - 1).rect
            if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > rect.width or bbox[3] > rect.height:
                raise HTTPException(status_code=422, detail="manual_location bbox exceeds the physical page")
    except HTTPException:
        raise
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="source pdf not found") from exc
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PyMuPDF is not installed") from exc

    quote = str(value.get("quote") or "")[:2000]
    evidence_seed = json.dumps(
        [job_id, page_number, bbox, quote], ensure_ascii=False, separators=(",", ":")
    )
    return {
        "evidence_id": "manual-" + hashlib.sha256(evidence_seed.encode("utf-8")).hexdigest()[:20],
        "file_hash": None,
        "physical_page": page_number,
        "printed_page": None,
        "source_cell_id": None,
        "source_block_id": None,
        "quote": quote,
        "bbox": bbox,
        "source_method": "manual",
        "verification_status": "unverified",
        "location_level": "region",
        "validation_note": "人工提交的位置已校验页码和边界；原文引用仍未由系统核实",
    }


def _evidence_preview_url(job_id: str, evidence: Dict[str, Any]) -> Optional[str]:
    page = evidence.get("physical_page")
    if not isinstance(page, int) or page < 1:
        return None
    params: Dict[str, Any] = {"page": page}
    bbox = _parse_bbox(evidence.get("bbox"))
    if (
        bbox
        and evidence.get("verification_status") == "verified"
        and evidence.get("location_level") in {"exact_cell", "exact_span"}
    ):
        params["bbox"] = ",".join(str(item) for item in bbox)
    return f"/api/files/{job_id}/preview?{urlencode(params)}"


def _sanitize_evidence(job_id: str, job_dir: Path, raw: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(raw)
    bbox = _parse_bbox(result.get("bbox"))
    result["bbox"] = bbox
    # AC-09/RC-09：读取端不回显存储侧 verified——同一条证据必须同时绑定
    # 原件哈希、物理页、来源 cell/block 和原文引文，缺任一项均不能认证。
    claimed_hash_probe = str(result.get("file_hash") or "").strip()
    quote_probe = str(result.get("quote") or "").strip()
    source_id_probe = str(result.get("source_cell_id") or result.get("source_block_id") or "")
    if result.get("verification_status") == "verified" and (
        not claimed_hash_probe or not quote_probe or not source_id_probe
    ):
        result["verification_status"] = "unverified"
        result["validation_note"] = (
            "证据缺少原件哈希、原文引文或来源 cell/block 身份，读取端不回显 verified"
        )
    page = result.get("physical_page")
    if isinstance(page, bool):
        page = None
    if page is None:
        result["bbox"] = None
        result["location_level"] = "unresolved"
        result["validation_note"] = "证据未提供物理页码，无法验证坐标"
        result["preview_url"] = None
        return result
    try:
        page_number = int(page)
        import fitz

        pdf_path = runtime.find_first_pdf(job_dir)
        quote_text = str(result.get("quote") or "").strip()
        with fitz.open(pdf_path) as document:
            if not 1 <= page_number <= document.page_count:
                raise ValueError("physical page out of range")
            if bbox:
                rect = document.load_page(page_number - 1).rect
                if bbox[0] < 0 or bbox[1] < 0 or bbox[2] > rect.width or bbox[3] > rect.height:
                    raise ValueError("bbox exceeds physical page")
            # AC-09：读取端不回显存储侧的 verified 标签——按当前原件重算：
            # 1) 文件哈希不匹配 → failed（证据不属于当前原件）；
            # 2) 引文在自称页不存在 → failed（伪造原文）；
            # 3) 精确定位还要求 bbox 区域文本包含引文，否则降级 region。
            claimed_hash = str(result.get("file_hash") or "").strip()
            expected_evidence_id = ""
            if claimed_hash:
                import hashlib

                digest = hashlib.sha256()
                with open(pdf_path, "rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != claimed_hash.lower():
                    result["verification_status"] = "failed"
                    result["location_level"] = "unresolved"
                    result["validation_note"] = "证据文件哈希与当前原件不一致"
                    result["preview_url"] = _evidence_preview_url(job_id, result)
                    return result
            if claimed_hash and quote_text and source_id_probe:
                import hashlib

                expected_evidence_id = hashlib.sha1(
                    "|".join((claimed_hash.lower(), str(page_number), source_id_probe, str(result.get("quote") or ""))).encode("utf-8")
                ).hexdigest()[:16]
                if str(result.get("evidence_id") or "").strip().lower() != expected_evidence_id:
                    result["verification_status"] = "unverified"
                    if result.get("location_level") in {"exact_cell", "exact_span"}:
                        result["location_level"] = "region"
                        result["bbox"] = None
                    result["validation_note"] = "证据 ID 与原件哈希、物理页、来源对象和原文引文不匹配"
            if result.get("source_method") == "ocr":
                from src.services.scan_evidence_certification import load_attestations, verify_scan_reference
                certified = bool(expected_evidence_id and result.get("evidence_id") == expected_evidence_id
                                 and verify_scan_reference(result, pdf_path, load_attestations()))
                result["verification_status"] = "verified" if certified else "unverified"
                if not certified:
                    result["location_level"] = "region" if bbox else "unresolved"
                result["validation_note"] = "扫描区域已按独立冻结人工记录核验" if certified else "扫描证据缺少匹配的独立冻结人工认证"
                result["physical_page"] = page_number
                result["preview_url"] = _evidence_preview_url(job_id, result)
                return result
            if quote_text:
                page_text = document.load_page(page_number - 1).get_text() or ""
                normalized_page = "".join(page_text.split())
                normalized_quote = "".join(quote_text.split())
                quote_found = bool(normalized_page) and normalized_quote in normalized_page
                if not quote_found:
                    result["verification_status"] = "failed"
                    result["location_level"] = "unresolved"
                    result["validation_note"] = "证据引文在当前原件页面中不存在"
                    result["preview_url"] = _evidence_preview_url(job_id, result)
                    return result
                if (
                    bbox
                    and result.get("location_level") in {"exact_cell", "exact_span"}
                ):
                    words = document.load_page(page_number - 1).get_text("words") or []
                    x0, y0, x1, y1 = bbox
                    region = "".join(
                        "".join(str(word[4] or "") for word in words
                                if not (word[2] < x0 or word[0] > x1 or word[3] < y0 or word[1] > y1)).split()
                    )
                    if not region or normalized_quote not in region:
                        result["location_level"] = "region"
                        if result.get("verification_status") == "verified":
                            result["verification_status"] = "unverified"
                        result["validation_note"] = "bbox 区域内容与引文不符，已降级为区域定位"
            if not claimed_hash or not quote_text or not source_id_probe or str(result.get("evidence_id") or "").strip().lower() != expected_evidence_id:
                result["verification_status"] = "unverified"
                if result.get("location_level") in {"exact_cell", "exact_span"}:
                    result["location_level"] = "region"
                    result["bbox"] = None
                result["validation_note"] = (
                    "原件哈希、原文引文和来源 cell/block 必须同时存在才能认证证据"
                )
        result["physical_page"] = page_number
    except ImportError:
        result["physical_page"] = None
        result["bbox"] = None
        result["verification_status"] = "unverified"
        result["location_level"] = "unresolved"
        result["validation_note"] = "无法使用 PDF 渲染器验证物理页和坐标"
    except Exception:
        result["physical_page"] = None
        result["bbox"] = None
        result["verification_status"] = "failed"
        result["location_level"] = "unresolved"
        result["validation_note"] = "证据页码或坐标无法在当前原件中验证"
    result["preview_url"] = _evidence_preview_url(job_id, result)
    return result


async def _resolve_evidence(
    job_id: str,
    payload: Dict[str, Any],
    evidence_id: str,
    *,
    basis_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    for evidence in iter_evidence_refs(payload):
        if str(evidence.get("evidence_id") or "") == evidence_id:
            return evidence
    for record in read_jsonl_records(_job_dir(job_id) / "corrections.jsonl"):
        if basis_token and record.get("analysis_basis_token") != basis_token:
            continue
        for evidence in record.get("manual_evidence", []):
            if isinstance(evidence, dict) and str(evidence.get("evidence_id") or "") == evidence_id:
                return evidence
    return await _load_persisted_evidence(payload, evidence_id)


def _current_basis_or_409(job_id: str, job_dir: Path, submitted: str) -> str:
    current = current_analysis_basis_token(job_id, job_dir)
    if current is None:
        raise HTTPException(
            status_code=409,
            detail={"error": "analysis_basis_unavailable", "message": "当前任务没有可验证的分析版本"},
        )
    if submitted != current:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "analysis_basis_changed",
                "message": "提交基于旧分析版本，请刷新后重新校正",
                "current_analysis_basis_token": current,
            },
        )
    return current


@router.get("/api/jobs/{job_id}/evidence/{evidence_id}")
async def get_job_evidence(
    job_id: str,
    evidence_id: str,
    request: Request,
    analysis_revision: Optional[str] = Query(default=None, max_length=300),
) -> Dict[str, Any]:
    """Read one evidence reference, optionally from a retained analysis snapshot."""
    _validate_job_id(job_id)
    _, _, _, _ = require_job_access(request, job_id)
    payload = await _bound_evidence_payload(job_id, analysis_revision)
    if analysis_revision:
        revision_key = (
            str(analysis_revision)
            if str(analysis_revision).startswith("rev-")
            else str(payload.get("analysis_revision") or analysis_revision)
        )
        selected_basis = f"{job_id}:{revision_key}"
    else:
        selected_basis = current_analysis_basis_token(job_id, _job_dir(job_id))
    evidence = await _resolve_evidence(
        job_id, payload, evidence_id, basis_token=selected_basis
    )
    if evidence is None:
        raise HTTPException(status_code=404, detail="evidence not found")
    safe = _sanitize_evidence(job_id, _job_dir(job_id), evidence)
    safe["analysis_revision"] = analysis_revision or runtime.read_analysis_revision_pointer(_job_dir(job_id)) or None
    if isinstance(safe["analysis_revision"], dict):
        safe["analysis_revision"] = safe["analysis_revision"].get("revision_id")
    return safe


@router.post("/api/jobs/{job_id}/corrections", status_code=status.HTTP_202_ACCEPTED)
async def create_job_correction(job_id: str, body: CorrectionRequest, request: Request) -> Dict[str, Any]:
    """Append a correction proposal against the exact current analysis basis."""
    _validate_job_id(job_id)
    _, _, user, payload = require_job_access(request, job_id)
    job_dir = _job_dir(job_id)
    basis = _current_basis_or_409(job_id, job_dir, body.analysis_basis_token)
    current_evidence_ids = report_evidence_ids(payload)
    manual_evidence: List[Dict[str, Any]] = []
    correction_changes: List[Dict[str, Any]] = []

    for change in body.changes:
        for evidence_id in change.evidence_ids:
            if evidence_id not in current_evidence_ids:
                resolved = await _resolve_evidence(
                    job_id, payload, evidence_id, basis_token=basis
                )
                if resolved is None:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": "evidence_reference_invalid",
                            "message": f"证据 {evidence_id} 不属于当前任务可读取的证据",
                        },
                    )
                current_evidence_ids.add(evidence_id)
        normalized_change = change.model_dump(exclude_none=True)
        if change.location is not None:
            location_evidence = _validate_manual_location(job_id, job_dir, change.location)
            manual_evidence.append(location_evidence)
            normalized_change["evidence_ids"] = [*change.evidence_ids, location_evidence["evidence_id"]]
            normalized_change["location"] = {
                "physical_page": location_evidence["physical_page"],
                "bbox": location_evidence["bbox"],
            }
        if change.field == "manual_location":
            location_evidence = _validate_manual_location(job_id, job_dir, change.value)
            manual_evidence.append(location_evidence)
            normalized_change["value"] = {
                "physical_page": location_evidence["physical_page"],
                "bbox": location_evidence["bbox"],
                "quote": location_evidence["quote"],
                "evidence_id": location_evidence["evidence_id"],
                "source_method": "manual",
                "verification_status": "unverified",
            }
        correction_changes.append(normalized_change)

    report = payload.get("review_report") if isinstance(payload.get("review_report"), dict) else {}
    receipts = report.get("check_receipts")
    if not isinstance(receipts, list):
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        receipts = meta.get("check_receipts")
    affected = affected_check_ids(receipts, (item.target_id for item in body.changes))
    record = {
        "record_id": "cor-" + uuid.uuid4().hex,
        "job_id": job_id,
        "analysis_basis_token": basis,
        "actor": str(user.get("username") or "unknown"),
        "reason": body.reason.strip(),
        "changes": correction_changes,
        "manual_evidence": manual_evidence,
        "created_at": runtime.datetime.now(runtime.timezone.utc).isoformat(),
        "record_status": "append_only_overlay",
    }
    result = append_record_with_basis_cas(
        job_dir,
        filename="corrections.jsonl",
        expected_basis_token=basis,
        record=record,
    )
    if not result.get("accepted"):
        if result.get("reason") == "analysis_basis_changed":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "analysis_basis_changed",
                    "message": "写入期间分析版本已变化，请刷新后重新校正",
                    "current_analysis_basis_token": result.get("current_basis_token"),
                },
            )
        raise HTTPException(status_code=409, detail="current analysis basis unavailable")

    # ZC-05（AC-10）：校正不再只是留痕——事实改值以不可变覆盖层进入下一轮
    # 分析，重跑受影响链路后发布新的分析版本（旧版本保留可回放）。
    reanalysis_status = "queued"
    runner = runtime.get_pipeline_runner()
    if runner is None:
        reanalysis_status = "not_executed_no_pipeline_runner"
    else:
        from api import queue_runtime
        if queue_runtime.queue_enabled() or not queue_runtime.allow_inline_fallback():
            # Use the same dispatch and review invalidation policy as Analyze.
            # In API role this persists queued status for the separate Worker.
            await runtime.reanalyze_job(job_id)
            return {
                "correction_id": record["record_id"], "record_status": "append_only_overlay",
                "analysis_basis_token": basis, "analysis_revision": basis.rsplit(":",1)[-1],
                "new_analysis_revision": None, "affected_checks": affected,
                "reanalysis_status": "queued", "review_decisions_status": "pending_reanalysis",
                "message": "校正已追加留痕并排队重分析；完成后发布新版本，旧版本保留可回放。",
            }
        async def _run_reanalysis() -> None:
            try:
                await runner(job_dir)
            except Exception:  # noqa: BLE001 - 后台重算失败由快照状态如实呈现
                logger.exception("correction reanalysis failed for job %s", job_id)

        asyncio.get_running_loop().create_task(_run_reanalysis())

    return {
        "correction_id": record["record_id"],
        "record_status": "append_only_overlay",
        "analysis_basis_token": basis,
        "analysis_revision": basis.rsplit(":", 1)[-1],
        "new_analysis_revision": None,
        "affected_checks": affected,
        "reanalysis_status": reanalysis_status,
        "review_decisions_status": (
            "pending_reanalysis" if reanalysis_status == "queued" else "unchanged_until_new_analysis_is_published"
        ),
        "message": (
            "校正已追加留痕，并已触发基于校正后事实的重新分析；"
            "完成后将发布新的分析版本，旧版本保留可回放。"
            if reanalysis_status == "queued"
            else "校正已追加留痕；重算未触发。"
        ),
    }


@router.post("/api/jobs/{job_id}/feedback", status_code=status.HTTP_202_ACCEPTED)
async def create_job_feedback(job_id: str, body: FeedbackRequest, request: Request) -> Dict[str, Any]:
    """Record false-positive/missed-issue feedback for later business confirmation."""
    _validate_job_id(job_id)
    _, _, user, payload = require_job_access(request, job_id)
    job_dir = _job_dir(job_id)
    basis = _current_basis_or_409(job_id, job_dir, body.analysis_basis_token)
    known_ids = report_evidence_ids(payload)
    for evidence_id in body.evidence_ids:
        if evidence_id not in known_ids and await _resolve_evidence(
            job_id, payload, evidence_id, basis_token=basis
        ) is None:
            raise HTTPException(status_code=422, detail=f"evidence reference not found: {evidence_id}")
    record = {
        "feedback_id": "fb-" + uuid.uuid4().hex,
        "job_id": job_id,
        "analysis_basis_token": basis,
        "actor": str(user.get("username") or "unknown"),
        "feedback_type": body.feedback_type,
        "reason_code": body.reason_code,
        "issue_id": body.issue_id,
        "rule_id": body.rule_id,
        "evidence_ids": body.evidence_ids,
        "context": body.context.strip(),
        "created_at": runtime.datetime.now(runtime.timezone.utc).isoformat(),
        "feedback_status": "awaiting_business_confirmation",
    }
    result = append_record_with_basis_cas(
        job_dir,
        filename="feedback.jsonl",
        expected_basis_token=basis,
        record=record,
    )
    if not result.get("accepted"):
        if result.get("reason") == "analysis_basis_changed":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "analysis_basis_changed",
                    "message": "写入期间分析版本已变化，请刷新后重新提交反馈",
                    "current_analysis_basis_token": result.get("current_basis_token"),
                },
            )
        raise HTTPException(status_code=409, detail="current analysis basis unavailable")
    return {
        "feedback_id": record["feedback_id"],
        "feedback_status": record["feedback_status"],
        "analysis_basis_token": basis,
        "message": "反馈已记录，需经业务确认后才能用于标准答案或规则修订。",
    }


_TARGET_TABLE_KEYS = (
    "fragment_uid", "instance_uid", "table_code", "title", "page", "bbox", "header_row_count",
    "extraction_method", "scope", "mapping_status", "column_mappings", "rows", "row_boxes", "unresolved_money_cells",
    "unqualified_ocr_cells", "arithmetic_conflicts",
)


@router.get("/api/jobs/{job_id}/correction-targets")
async def get_correction_targets(job_id: str, request: Request) -> Dict[str, Any]:
    """List what the reviewer can fix by hand: alerted tables, unread regions and prior corrections."""
    _validate_job_id(job_id)
    _, _, _, payload = require_job_access(request, job_id)
    from src.services.fiscal_table_rules import NINE_TABLE_RULES

    job_dir = _job_dir(job_id)
    receipt = ((payload.get("structured_ingest") or {}).get("reading_import") or {})
    reading = receipt.get("reading_quality") or {}
    tables = []
    for table in receipt.get("tables") or []:
        if not isinstance(table, dict) or "rows" not in table:
            continue
        rule = NINE_TABLE_RULES.get(str(table.get("table_code") or ""))
        tables.append({key: table.get(key) for key in _TARGET_TABLE_KEYS} | {
            "measure_options": [
                {"measure": measure, "label": aliases[0] if aliases else measure}
                for measure, aliases in (rule.measure_aliases.items() if rule else [])
            ],
        })
    history = [
        {"record_id": record.get("record_id"), "actor": record.get("actor"), "reason": record.get("reason"),
         "created_at": record.get("created_at"), "changes": record.get("changes") or []}
        for record in read_jsonl_records(job_dir / "corrections.jsonl")
    ]
    return {
        "job_id": job_id,
        "analysis_basis_token": current_analysis_basis_token(job_id, job_dir),
        "reading_status": reading.get("status"),
        "tables": tables,
        "gaps": [gap for gap in reading.get("gaps") or [] if isinstance(gap, dict)],
        "orphaned_amounts": receipt.get("orphaned_physical_amounts") or [],
        "overlay_status": payload.get("fact_overlay"),
        "history": history[-50:],
    }
