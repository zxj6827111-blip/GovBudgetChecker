"""Shared runtime state and helpers for API routes.

This module keeps route-facing helpers out of `api.main`, so route modules can
depend on a stable API surface instead of importing `api.main` directly.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Iterable, List, Optional, Set

import aiofiles
from fastapi import HTTPException, Request, UploadFile

from api import queue_runtime
from src.services.pdf_selection import select_canonical_pdf

logger = logging.getLogger(__name__)

APP_TITLE = "GovBudgetChecker API"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
MAX_UPLOAD_PAGES = int(os.getenv("MAX_UPLOAD_PAGES", "800"))
UPLOAD_ROOT = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
DOCUMENT_STORAGE_BACKEND = os.getenv("DOCUMENT_STORAGE_BACKEND", "filesystem").strip() or "filesystem"

try:
    from src.security import (
        security_config,
        sanitize_filename,
        validate_upload_metadata,
        verify_api_key,
    )

    SECURITY_AVAILABLE = True
except ImportError:
    SECURITY_AVAILABLE = False
    security_config = None

    async def verify_api_key(request: Request = None):  # type: ignore[override]
        _ = request
        return "anonymous"

    def validate_upload_metadata(
        filename: str, content_type: str
    ) -> tuple[bool, str]:
        _ = (filename, content_type)
        return True, "OK"


try:
    from src.services.org_storage import get_org_storage
    from src.schemas.organization import Organization, OrganizationLevel

    ORG_AVAILABLE = True
except ImportError:
    ORG_AVAILABLE = False
    get_org_storage = None
    Organization = None
    OrganizationLevel = None

try:
    from src.services.user_store import get_user_store

    USER_STORE_AVAILABLE = True
except ImportError:
    USER_STORE_AVAILABLE = False
    get_user_store = None

from src.services.analysis_result_store import persist_analysis_job_snapshot
from src.schemas.issues import infer_analysis_conclusion
from config.settings import get_settings

# 展示层的问题计数必须与质量门禁同口径（缺证据被降级的条目不算正式问题），
# 否则会出现"任务是 review_required / incomplete，列表却显示有 N 个问题"的矛盾。
# 复用 evidence_guard 的判定函数，不在这里另立一套。
from src.services.evidence_guard import is_formal_finding

# 年份解析的唯一权威实现放在 src/utils/report_year.py，这里重新导出，
# 既保持 `runtime.parse_report_year` 这个既有对外名字，又保证与结构化入库路径同源。
from src.utils.report_year import (  # noqa: F401
    extract_report_year_candidates,
    parse_report_year,
)

# 报告画像的唯一解析器。此前文种/层级等识别在本模块与规则引擎里各写一套，
# 兜底口径互相矛盾，同一 PDF 在不同环节可能被判成不同文种。
# 本模块只保留对解析器的调用，判定逻辑不再在本模块维护。
from src.services.document_profile_resolver import (
    detect_cover_facts,
    resolve_document_profile,
)

_pipeline_runner: Optional[Callable[[Path], Awaitable[None]]] = None
_job_queue: Optional["DurableJobQueue"] = None
_JOB_SUMMARY_CACHE: Dict[str, Dict[str, Any]] = {}
_JOB_SUMMARY_CACHE_MAX_SIZE = 2048
STRUCTURED_INGEST_FILENAME = "structured_ingest.json"
IGNORED_ISSUES_FILENAME = "ignored_issues.json"
PERSISTENCE_STATE_FILENAME = "persistence.json"
JOB_STATUS_CONTEXT_KEYS = (
    "filename",
    "size",
    "saved_path",
    "checksum",
    "version_created_at",
    "job_created_at",
    "organization_id",
    "organization_name",
    "organization_match_type",
    "organization_match_confidence",
    "fiscal_year",
    "doc_type",
    "report_year",
    "report_year_source",
    "year_conflict",
    "report_kind",
    "created_by",
    # 质量门禁与分析结论相关字段，需跨状态流转保留，供列表/详情/复核直接读取
    "analysis_conclusion",
    "quality_status",
    "page_coverage",
    "scanned_page_count",
)
ACTIVE_ANALYSIS_STATUSES = {"queued", "processing", "running"}
REANALYZE_EPHEMERAL_FILES = {
    STRUCTURED_INGEST_FILENAME,
    IGNORED_ISSUES_FILENAME,
    "annotated.pdf",
    "report.pdf",
    "report_annotated.pdf",
    "compare_old_vs_new.json",
    "status_old_before_compare.json",
    "status_new_after_compare.json",
    "status_new_after_compare_with_ai.json",
}

if TYPE_CHECKING:
    from api.job_queue import DurableJobQueue

_COVER_ORG_LABELS = (
    ("\u9884\u7b97\u4e3b\u7ba1\u90e8\u95e8", "department", "budget"),
    ("\u9884\u7b97\u5355\u4f4d", "unit", "budget"),
    ("\u51b3\u7b97\u4e3b\u7ba1\u90e8\u95e8", "department", "final"),
    ("\u51b3\u7b97\u5355\u4f4d", "unit", "final"),
)


def set_pipeline_runner(runner: Callable[[Path], Awaitable[None]]) -> None:
    """Register the async pipeline runner used by `start_analysis`."""
    global _pipeline_runner
    _pipeline_runner = runner


def get_pipeline_runner() -> Optional[Callable[[Path], Awaitable[None]]]:
    """Return current pipeline runner."""
    return _pipeline_runner


def set_job_queue(queue: Optional["DurableJobQueue"]) -> None:
    """Register job queue implementation."""
    global _job_queue
    _job_queue = queue


def get_job_queue() -> Optional["DurableJobQueue"]:
    """Return active job queue implementation."""
    return _job_queue


def to_dict(obj: Any) -> Dict[str, Any]:
    """Best-effort conversion from pydantic/dataclass-like objects to dict."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return dict(obj)


def get_job_quick_timestamp(job_dir: Path) -> float:
    """Return the best-effort last-updated timestamp for a job directory."""
    try:
        status_file = job_dir / "status.json"
        if status_file.exists():
            return status_file.stat().st_mtime
        return job_dir.stat().st_mtime
    except Exception:
        return 0.0


def _parse_positive_timestamp(raw: Any) -> Optional[float]:
    try:
        value = float(raw)
    except Exception:
        return None
    return value if value > 0 else None


def get_job_birth_timestamp(job_dir: Path) -> float:
    """Return a stable creation-like timestamp for a job directory."""
    candidates: List[float] = []
    status_file = job_dir / "status.json"

    for path in (job_dir, status_file):
        try:
            if not path.exists():
                continue
            stat = path.stat()
            for value in (getattr(stat, "st_ctime", None), getattr(stat, "st_mtime", None)):
                parsed = _parse_positive_timestamp(value)
                if parsed is not None:
                    candidates.append(parsed)
        except Exception:
            continue

    return min(candidates) if candidates else 0.0


def get_job_created_timestamp(
    job_dir: Path,
    status_payload: Optional[Dict[str, Any]] = None,
) -> float:
    """Return the stable creation time for a job instance."""
    payload = status_payload if isinstance(status_payload, dict) else {}
    parsed = _parse_positive_timestamp(payload.get("job_created_at"))
    if parsed is not None:
        return parsed

    birth = get_job_birth_timestamp(job_dir)
    return birth if birth > 0 else get_job_quick_timestamp(job_dir)


def get_job_version_timestamp(
    job_dir: Path,
    status_payload: Optional[Dict[str, Any]] = None,
) -> float:
    """Return the stable report-version timestamp for latest-version comparisons."""
    payload = status_payload if isinstance(status_payload, dict) else {}
    for key in ("version_created_at", "job_created_at"):
        parsed = _parse_positive_timestamp(payload.get(key))
        if parsed is not None:
            return parsed

    birth = get_job_birth_timestamp(job_dir)
    return birth if birth > 0 else get_job_quick_timestamp(job_dir)


def find_first_pdf(job_dir: Path) -> Path:
    """Return the canonical PDF recorded by the job, failing closed if ambiguous."""
    return select_canonical_pdf(job_dir)


def read_json_file(
    path: Path, default: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Read JSON file safely and return `default` when missing/invalid."""
    if default is None:
        default = {}
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to read JSON file: %s", path)
    return default


def write_json_file(path: Path, payload: Dict[str, Any]) -> None:
    """Write JSON payload atomically via unique temp file + rename."""
    tmp_fd = None
    tmp_path = None
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(
            suffix=".tmp", dir=str(path.parent)
        )
        tmp_path = Path(tmp_name)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        os.write(tmp_fd, data)
        os.fsync(tmp_fd)
        os.close(tmp_fd)
        tmp_fd = None
        tmp_path.replace(path)
    except BaseException:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def calculate_file_checksum(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return SHA-256 checksum for a file."""
    sha256 = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def extract_job_status_context(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return stable job metadata that should survive status transitions."""
    if not isinstance(payload, dict):
        return {}
    return {
        key: payload.get(key)
        for key in JOB_STATUS_CONTEXT_KEYS
        if payload.get(key) is not None
    }


def merge_job_status(job_dir: Path, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge patch into job status and persist it."""
    status_file = job_dir / "status.json"
    current = read_json_file(status_file, default={})
    current.update(patch)
    write_json_file(status_file, current)
    return current


def invalidate_job_summary_cache(job_id: str) -> None:
    """Drop cached list summary for a job when sidecar/status changes."""
    _JOB_SUMMARY_CACHE.pop(job_id, None)


def get_structured_ingest_path(job_dir: Path) -> Path:
    return job_dir / STRUCTURED_INGEST_FILENAME


def write_structured_ingest_payload(job_dir: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    structured_path = get_structured_ingest_path(job_dir)
    write_json_file(structured_path, payload)
    invalidate_job_summary_cache(job_dir.name)
    return payload


def read_structured_ingest_payload(job_dir: Path) -> Dict[str, Any]:
    return read_json_file(get_structured_ingest_path(job_dir), default={})


def get_ignored_issues_path(job_dir: Path) -> Path:
    return job_dir / IGNORED_ISSUES_FILENAME


def read_ignored_issues_payload(job_dir: Path) -> Dict[str, Any]:
    payload = read_json_file(get_ignored_issues_path(job_dir), default={})
    return payload if isinstance(payload, dict) else {}


def read_ignored_issue_ids(job_dir: Path) -> Set[str]:
    payload = read_ignored_issues_payload(job_dir)
    raw_ids = payload.get("issue_ids")
    if not isinstance(raw_ids, list):
        return set()
    ignored: Set[str] = set()
    for item in raw_ids:
        issue_id = str(item or "").strip()
        if issue_id:
            ignored.add(issue_id)
    return ignored


def write_ignored_issue_ids(job_dir: Path, issue_ids: Set[str]) -> Dict[str, Any]:
    payload = {
        "issue_ids": sorted({str(item).strip() for item in issue_ids if str(item).strip()}),
        "updated_at": time.time(),
    }
    write_json_file(get_ignored_issues_path(job_dir), payload)
    invalidate_job_summary_cache(job_dir.name)
    return payload


def _filter_issue_list(items: Any, ignored_ids: Set[str]) -> Any:
    if not isinstance(items, list):
        return items
    filtered: List[Any] = []
    for item in items:
        if isinstance(item, dict):
            issue_id = str(item.get("id") or "").strip()
            if issue_id and issue_id in ignored_ids:
                continue
        filtered.append(item)
    return filtered


def _lift_result_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload

    result = payload.get("result")
    if not isinstance(result, dict):
        return payload

    lifted = dict(payload)
    for key in ("ai_findings", "rule_findings", "issues", "merged", "summary", "meta"):
        result_value = result.get(key)
        if result_value is None:
            continue
        current_value = lifted.get(key)
        if current_value in (None, [], {}, ""):
            lifted[key] = copy.deepcopy(result_value)

    return lifted


def _recompute_merged_payload(container: Dict[str, Any]) -> None:
    ai_findings = container.get("ai_findings")
    rule_findings = container.get("rule_findings")
    if not isinstance(ai_findings, list) or not isinstance(rule_findings, list):
        return
    try:
        from src.schemas.issues import AnalysisConfig, IssueItem as SchemaIssueItem
        from src.services.merge_findings import FindingsMerger

        ai_models = [
            item if isinstance(item, SchemaIssueItem) else SchemaIssueItem(**item)
            for item in ai_findings
            if isinstance(item, (dict, SchemaIssueItem))
        ]
        rule_models = [
            item if isinstance(item, SchemaIssueItem) else SchemaIssueItem(**item)
            for item in rule_findings
            if isinstance(item, (dict, SchemaIssueItem))
        ]
        merged_summary = FindingsMerger(AnalysisConfig()).merge_findings(
            ai_models,
            rule_models,
        )
        if hasattr(merged_summary, "model_dump"):
            container["merged"] = merged_summary.model_dump()
        elif hasattr(merged_summary, "dict"):
            container["merged"] = merged_summary.dict()
    except Exception:
        logger.exception("Failed to recompute merged summary after issue filtering")


def _filter_issue_container(container: Dict[str, Any], ignored_ids: Set[str]) -> Dict[str, Any]:
    filtered = copy.deepcopy(container)
    for key in ("ai_findings", "rule_findings"):
        if key in filtered:
            filtered[key] = _filter_issue_list(filtered.get(key), ignored_ids)

    issues = filtered.get("issues")
    if isinstance(issues, list):
        filtered["issues"] = _filter_issue_list(issues, ignored_ids)
    elif isinstance(issues, dict):
        next_issues = dict(issues)
        for key in ("error", "warn", "info", "all"):
            if key in next_issues:
                next_issues[key] = _filter_issue_list(next_issues.get(key), ignored_ids)
        if not isinstance(next_issues.get("all"), list):
            buckets: List[Any] = []
            for key in ("error", "warn", "info"):
                if isinstance(next_issues.get(key), list):
                    buckets.extend(next_issues[key])
            next_issues["all"] = buckets
        filtered["issues"] = next_issues

    if "merged" in filtered:
        _recompute_merged_payload(filtered)
    return filtered


def _collect_issue_ids_from_container(container: Any) -> Set[str]:
    issue_ids: Set[str] = set()
    if not isinstance(container, dict):
        return issue_ids

    def _consume(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            issue_id = str(item.get("id") or "").strip()
            if issue_id:
                issue_ids.add(issue_id)

    for key in ("ai_findings", "rule_findings"):
        _consume(container.get(key))

    issues = container.get("issues")
    if isinstance(issues, list):
        _consume(issues)
    elif isinstance(issues, dict):
        for key in ("error", "warn", "info", "all"):
            _consume(issues.get(key))

    return issue_ids


def apply_job_issue_filters(job_dir: Path, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return payload

    ignored_ids = read_ignored_issue_ids(job_dir)
    if not ignored_ids:
        next_payload = dict(payload)
        next_payload.setdefault("ignored_issue_ids", [])
        next_payload.setdefault("ignored_issue_count", 0)
        return next_payload

    filtered = copy.deepcopy(payload)
    filtered = _filter_issue_container(filtered, ignored_ids)

    result = filtered.get("result")
    if isinstance(result, dict):
        filtered["result"] = _filter_issue_container(result, ignored_ids)

    filtered["ignored_issue_ids"] = sorted(ignored_ids)
    filtered["ignored_issue_count"] = len(ignored_ids)
    return filtered


def ignore_job_issue(job_id: str, issue_id: str) -> Dict[str, Any]:
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")

    normalized_issue_id = str(issue_id or "").strip()
    if not normalized_issue_id:
        raise HTTPException(status_code=400, detail="issue_id is required")

    status_file = job_dir / "status.json"
    raw_payload = read_json_file(status_file, default={})
    known_issue_ids = _collect_issue_ids_from_container(raw_payload)
    result_payload = raw_payload.get("result")
    if isinstance(result_payload, dict):
        known_issue_ids.update(_collect_issue_ids_from_container(result_payload))

    ignored_ids = read_ignored_issue_ids(job_dir)
    if normalized_issue_id not in known_issue_ids and normalized_issue_id not in ignored_ids:
        raise HTTPException(status_code=404, detail="issue_id does not exist")

    # 写入必须走 workflow 存储的文件锁（WP3-A）：
    #   - 复核完成门禁的问题集合同时覆盖"workflow 决定"与"忽略清单"，
    #     而落编辑锁时的 CAS 也在那把锁之下重算快照；两边各写各的会有窗口；
    #   - 该锁之下还会先判复核锁：已完成的复核不允许再改问题集合
    #     （409 review_completed_locked）。
    # 延迟导入的理由与 _invalidate_reviews_before_analysis_start 相同：
    # issue_workflow_store 在模块级 import 本模块，这里必须等到调用时再导入。
    from src.services.issue_workflow_store import add_ignored_issue_id

    add_ignored_issue_id(job_id, normalized_issue_id)

    payload = get_job_status_payload(job_id)
    payload["ignored_issue_id"] = normalized_issue_id
    return payload


def _enrich_job_organization_context(job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill organization fields from link storage for legacy jobs."""
    if not ORG_AVAILABLE or not isinstance(payload, dict):
        return payload

    enriched = dict(payload)
    try:
        storage = require_org_storage()
        linked_org = None
        match_type = None
        confidence = None

        link = storage.get_job_org(job_id)
        if link is not None:
            linked_org = storage.get_by_id(str(link.org_id))
            match_type = getattr(link, "match_type", None)
            confidence = getattr(link, "confidence", None)
        elif enriched.get("organization_id") is not None:
            linked_org = storage.get_by_id(str(enriched["organization_id"]))
            match_type = enriched.get("organization_match_type")
            confidence = enriched.get("organization_match_confidence")

        if linked_org is None:
            return enriched

        # Keep job payloads aligned with the canonical organization record after renames.
        enriched["organization_id"] = linked_org.id
        enriched["organization_name"] = linked_org.name
        if match_type is not None:
            enriched["organization_match_type"] = match_type
        if confidence is not None:
            enriched["organization_match_confidence"] = round(float(confidence), 4)
    except Exception:
        logger.exception("Failed to enrich organization context for job %s", job_id)

    return enriched


def _extract_pdf_page_texts(pdf_source: Any, *, max_pages: int = 3) -> List[str]:
    """Best-effort text extraction for the first few PDF pages."""
    try:
        import pdfplumber
    except Exception:
        return []

    try:
        with pdfplumber.open(pdf_source) as pdf:
            if not pdf.pages:
                return []
            limit = max(1, int(max_pages or 1))
            return [
                str(page.extract_text() or "").strip()
                for page in pdf.pages[:limit]
            ]
    except Exception:
        logger.exception("Failed to extract page text from PDF source")
        return []


def extract_pdf_page_texts_from_bytes(content: bytes, max_pages: int = 3) -> List[str]:
    """Extract the first few page texts from in-memory PDF bytes."""
    if not content:
        return []
    return _extract_pdf_page_texts(io.BytesIO(content), max_pages=max_pages)


def get_pdf_page_count_from_bytes(content: bytes) -> Optional[int]:
    """Best-effort page count for in-memory PDF bytes (upload-center preflight).

    UI 重建第二批 Task 5：上传中心待上传文件列表需要在上传前显示真实页数
    （原型图"18.4 MB · 48 页"），但当时手上只有内存字节、没有落盘路径，
    不能直接复用需要 Path 的 `get_pdf_page_count()`。这里复用已有的
    `pdfplumber` 依赖（与 `_extract_pdf_page_texts` 同一个库，不新增依赖），
    只读页数不读正文，比整页文本抽取更轻。

    解析失败（损坏文件、非 PDF、加密文档等）时返回 None 而不是 0——
    0 意味着"已确认这份文件有 0 页"，解析失败时我们并不知道真实页数，
    与本仓库"未知用 None、绝不用 0 顶替"的一贯原则一致。
    """
    if not content:
        return None
    try:
        import pdfplumber
    except Exception:
        return None
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return len(pdf.pages)
    except Exception:
        logger.exception("Failed to read page count from in-memory PDF bytes")
        return None


def extract_pdf_page_texts(pdf_path: Path, max_pages: int = 3) -> List[str]:
    """Extract the first few page texts from a PDF path."""
    return _extract_pdf_page_texts(str(pdf_path), max_pages=max_pages)


def extract_pdf_first_page_text(pdf_path: Path) -> str:
    """Best-effort first-page text extraction for upload-time organization matching."""
    page_texts = extract_pdf_page_texts(pdf_path, max_pages=1)
    return page_texts[0] if page_texts else ""


def infer_report_year(
    filename: str = "",
    page_texts: Optional[List[str]] = None,
    preferred_year: Any = None,
) -> Optional[int]:
    """Infer report year from filename and page text with lightweight weighting."""
    scores: Dict[int, int] = {}

    def _bump(year: Optional[int], weight: int) -> None:
        if year is None:
            return
        if 2000 <= year <= 2099:
            scores[year] = scores.get(year, 0) + weight

    preferred = parse_report_year(preferred_year)
    _bump(preferred, 2)

    for year in extract_report_year_candidates(filename):
        _bump(year, 6)

    if page_texts:
        keywords = (
            "\u9884\u7b97",
            "\u51b3\u7b97",
            "\u90e8\u95e8",
            "\u5355\u4f4d",
            "\u76ee\u5f55",
            "budget",
            "final",
        )
        for pidx, page_text in enumerate(page_texts[:6]):
            if not page_text:
                continue
            for raw_line in page_text.splitlines()[:40]:
                line = raw_line.strip()
                if not line:
                    continue
                line_years = extract_report_year_candidates(line)
                if not line_years:
                    continue
                weight = 1
                if pidx == 0:
                    weight += 1
                if any(token in line for token in keywords):
                    weight += 2
                for year in line_years:
                    _bump(year, weight)

    if not scores:
        return preferred
    return max(scores.items(), key=lambda item: (item[1], item[0]))[0]


def normalize_report_kind(doc_type: Optional[str], filename: str = "") -> str:
    """Normalize report type to budget/final/unknown.

    实现已下沉到唯一解析器 ``src/services/document_profile_resolver``：
    此前文种在 8 处各判一次且兜底口径互相矛盾（本函数与 pipeline 在本口径上
    曾把"文种 vs 文件名"混在一个预算优先的判断里），导致同一 PDF 在不同环节
    得到不同文种，进而静默换掉整套专项检查。保留本函数签名是为了不破坏
    既有调用方与测试桩，判定逻辑不再在此处维护。
    """
    return resolve_document_profile(doc_type=doc_type, filename=filename).kind


def normalize_request_flag(value: Any, name: str) -> Optional[bool]:
    """请求布尔参数真值归一（分析请求契约，2026-09-06 K3 复核整改）。

    背景：start_analysis 此前用 ``is True`` 身份判断，调用方传字符串
    "true"/"1" 会绕过 422 冲突拦截、被静默当作 False 持久化；dual 分支
    的 ``bool()`` 又会把 "false" 扭曲成 True（请求 AI）。归一规则：

    - 缺省/None → None（未指定，由 mode 默认值决定）；
    - bool → 原样；
    - "true"/"1"/"yes"/"on"、"false"/"0"/"no"/"off"（忽略大小写与空白）
      → 对应真值；
    - 其余取值 → 422（fail-closed：不静默猜测调用方意图）。

    status.json 持久化的布尔值均由本函数产出，下游 ``bool()`` 不会再
    遇到字符串。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "on"}:
            return True
        if text in {"false", "0", "no", "off"}:
            return False
    raise HTTPException(
        status_code=422,
        # 不回显调用方原始值：既是日志安全要求，也避免把任意请求输入
        # 拼进 422 响应体。错误类型信息足以让调用方定位问题参数。
        detail=f"invalid boolean parameter '{name}': expected true/false",
    )


def normalize_doc_type(
    doc_type: Optional[str],
    filename: str = "",
    report_kind: Optional[str] = None,
) -> Optional[str]:
    """Normalize upload doc type to the route-facing values used by the app."""
    normalized = str(doc_type or "").strip().lower()
    if normalized in {"dept_budget", "budget"}:
        return "dept_budget"
    if normalized in {"dept_final", "final", "settlement", "accounts"}:
        return "dept_final"

    kind = str(report_kind or "").strip().lower()
    if not kind:
        kind = normalize_report_kind(doc_type, filename)
    if kind == "budget":
        return "dept_budget"
    if kind == "final":
        return "dept_final"
    return None


def extract_cover_metadata(
    *,
    page_texts: Optional[List[str]] = None,
    filename: str = "",
    preferred_year: Any = None,
    doc_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract lightweight cover metadata from the first page.

    返回值保持历史键不变（封面标题/机构/层级提示/文种/年度/doc_type），
    并新增 ``profile`` 与 ``profile_status``：前者是报告画像的完整留痕
    （含每个维度的来源与落选候选），后者让上层一眼看出画像是否足以
    选择检查配置。同一 PDF 在上传、规则执行、AI、入库、导出各环节
    都应消费这一个画像，不得各自重新猜文种。
    """
    normalized_pages = [str(text or "").strip() for text in (page_texts or [])]
    facts = detect_cover_facts(normalized_pages)

    # 年度沿用原有的加权推断（文件名权重最高、封面关键词加分、只看前 6 页），
    # 把它作为显式值交给画像，避免"统一画像"顺手把年度识别精度降级。
    report_year = infer_report_year(
        filename=filename,
        page_texts=normalized_pages,
        preferred_year=preferred_year,
    )

    profile = resolve_document_profile(
        doc_type=doc_type,
        filename=filename,
        page_texts=normalized_pages,
        cover_title=facts["cover_title"],
        cover_org_name=facts["cover_org_name"],
        cover_org_label=facts["cover_org_label"],
        cover_scope_hint=facts["cover_scope_hint"] or None,
        preferred_year=report_year,
    )

    report_kind = profile.kind
    return {
        "cover_title": facts["cover_title"],
        "cover_org_name": facts["cover_org_name"],
        "cover_org_label": facts["cover_org_label"],
        "scope_hint": profile.level if profile.level != "unknown" else "",
        "report_kind": report_kind,
        "report_year": report_year,
        "doc_type": normalize_doc_type(doc_type, filename, report_kind=report_kind),
        "profile": profile.to_dict(),
        "profile_status": profile.profile_status,
        "profile_unsupported_reason": profile.unsupported_reason,
    }


def _normalize_scope_name(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return re.sub(r"\s+", "", text).casefold()


def build_structured_ingest_scope(
    *,
    organization_id: Any = None,
    organization_name: Any = None,
    fiscal_year: Any = None,
    report_year: Any = None,
    doc_type: Any = None,
    report_kind: Any = None,
    filename: str = "",
) -> Optional[Dict[str, Any]]:
    """Build a comparable scope key for structured ingest latest-version checks."""
    year = parse_report_year(report_year if report_year is not None else fiscal_year)
    if year is None and filename:
        year = infer_report_year(filename=filename, preferred_year=fiscal_year)

    kind = str(report_kind or "").strip().lower()
    if not kind:
        kind = normalize_report_kind(
            str(doc_type) if doc_type is not None else None,
            filename,
        )
    if not kind or kind == "unknown":
        normalized_doc_type = str(doc_type or "").strip().lower()
        if normalized_doc_type in {"dept_budget", "budget"}:
            kind = "budget"
        elif normalized_doc_type in {"dept_final", "final", "settlement", "accounts"}:
            kind = "final"
        elif normalized_doc_type:
            kind = normalized_doc_type

    org_id = str(organization_id or "").strip()
    org_name = str(organization_name or "").strip()
    if org_id:
        org_key = f"id:{org_id}"
        scope_source = "organization_id"
    else:
        normalized_name = _normalize_scope_name(org_name)
        if not normalized_name:
            return None
        org_key = f"name:{normalized_name}"
        scope_source = "organization_name"

    if year is None or not kind:
        return None

    return {
        "scope_key": f"{org_key}|year:{year}|kind:{kind}",
        "organization_id": org_id or None,
        "organization_name": org_name or None,
        "report_year": year,
        "report_kind": kind,
        "scope_source": scope_source,
    }


def resolve_latest_structured_ingest_job(
    job_id: str,
    *,
    organization_id: Any = None,
    organization_name: Any = None,
    fiscal_year: Any = None,
    report_year: Any = None,
    doc_type: Any = None,
    report_kind: Any = None,
    filename: str = "",
    current_status_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve whether the current job is the latest report version for ingest scope."""
    scope = build_structured_ingest_scope(
        organization_id=organization_id,
        organization_name=organization_name,
        fiscal_year=fiscal_year,
        report_year=report_year,
        doc_type=doc_type,
        report_kind=report_kind,
        filename=filename,
    )
    if not scope:
        return {
            "is_latest": True,
            "reason": None,
            "scope": None,
            "latest_job_id": job_id,
            "latest_filename": filename or None,
        }

    candidates: List[Dict[str, Any]] = []
    for job_dir in iter_job_dirs():
        candidate_job_id = job_dir.name
        if candidate_job_id == job_id and isinstance(current_status_payload, dict):
            status_payload = dict(current_status_payload)
        else:
            status_payload = read_json_file(job_dir / "status.json", default={})
        if not isinstance(status_payload, dict):
            continue

        candidate_scope = build_structured_ingest_scope(
            organization_id=status_payload.get("organization_id"),
            organization_name=status_payload.get("organization_name"),
            fiscal_year=status_payload.get("fiscal_year"),
            report_year=status_payload.get("report_year"),
            doc_type=status_payload.get("doc_type"),
            report_kind=status_payload.get("report_kind"),
            filename=str(status_payload.get("filename") or ""),
        )
        if not candidate_scope or candidate_scope.get("scope_key") != scope.get("scope_key"):
            continue

        candidates.append(
            {
                "job_id": candidate_job_id,
                "filename": str(status_payload.get("filename") or ""),
                "version_created_at": get_job_version_timestamp(job_dir, status_payload),
                "job_created_at": get_job_created_timestamp(job_dir, status_payload),
                "quick_ts": get_job_quick_timestamp(job_dir),
            }
        )

    if not candidates:
        return {
            "is_latest": True,
            "reason": None,
            "scope": scope,
            "latest_job_id": job_id,
            "latest_filename": filename or None,
        }

    latest = max(
        candidates,
        key=lambda item: (
            float(item.get("version_created_at") or 0.0),
            float(item.get("job_created_at") or 0.0),
            float(item.get("quick_ts") or 0.0),
            str(item.get("job_id") or ""),
        ),
    )

    latest_job_id = str(latest.get("job_id") or "")
    return {
        "is_latest": latest_job_id == job_id,
        "reason": None if latest_job_id == job_id else "not_latest_version",
        "scope": scope,
        "latest_job_id": latest_job_id or job_id,
        "latest_filename": latest.get("filename") or None,
        "latest_version_created_at": latest.get("version_created_at"),
    }


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def _resolve_department_name(department_id: Optional[str]) -> Optional[str]:
    normalized_id = str(department_id or "").strip()
    if not normalized_id or not ORG_AVAILABLE:
        return None
    try:
        storage = require_org_storage()
        org = storage.get_by_id(normalized_id)
        if org is None:
            return None
        return str(getattr(org, "name", "") or "").strip() or None
    except Exception:
        logger.exception("Failed to resolve department name for cleanup scope %s", normalized_id)
        return None


#: 已绑定 Material Slot 的文件版本禁止由旧结构化清理链路删除。
#:
#: 一旦 ``fiscal_document_versions.slot_id`` 非空，这份 PDF 版本就进入了
#: 材料台账，成为"某条业务材料的一版文件历史"。旧的结构化入库清理是按
#: "每个 scope 只留最新"设计的，它看到的是入库维度的冗余，看不到台账维度的
#: 归属——让它删掉绑定版本会同时造成两件事：槽位的版本历史缺一块，
#: 以及 ``material_slots.current_document_version_id`` 被外键置空、
#: 材料突然变成"没有文件"。
#: 因此这里采取保守策略：**已绑定即不可由本链路删除**，无论它是不是当前版本。
MATERIAL_LEDGER_BLOCK_REASON = "material_slot_bound"


def _apply_material_ledger_protection(
    plan: Dict[str, Any],
    slot_bound_versions: Dict[int, str],
) -> Dict[str, Any]:
    """把已绑定 Material Slot 的版本从"待清理"移到"已阻断"。

    纯函数，不碰数据库：入参是"哪些版本已经绑定、绑到哪个槽位"的既定事实，
    出参是修正后的计划。这样阻断规则可以脱离数据库单测，
    也便于在真正的 DELETE 之前再做一次。

    只影响 ``cleanup_document_versions``；未绑定的候选原样保留，
    旧清理链路对它们的行为完全不变。
    """
    cleanup_versions = list(plan.get("cleanup_document_versions") or [])
    if not cleanup_versions:
        return {**plan, "material_ledger_checked": True}

    remaining: List[Dict[str, Any]] = []
    newly_blocked: List[Dict[str, Any]] = []
    for entry in cleanup_versions:
        version_id = _coerce_int(entry.get("document_version_id"))
        slot_id = slot_bound_versions.get(version_id) if version_id is not None else None
        if slot_id is None:
            remaining.append(entry)
            continue
        newly_blocked.append(
            {
                **entry,
                "reason": MATERIAL_LEDGER_BLOCK_REASON,
                # slot_id 一并返回，运维不必再查库就能知道它属于哪条材料
                "slot_id": slot_id,
            }
        )

    if not newly_blocked:
        return {**plan, "material_ledger_checked": True}

    remaining_version_ids = {
        _coerce_int(item.get("document_version_id")) for item in remaining
    }
    skipped_jobs = list(plan.get("skipped_jobs") or [])
    for entry in newly_blocked:
        for job in entry.get("jobs") or []:
            skipped_jobs.append(
                {
                    "job_id": job.get("job_id"),
                    "filename": job.get("filename"),
                    "scope_key": job.get("scope_key"),
                    "document_version_id": entry.get("document_version_id"),
                    "reason": MATERIAL_LEDGER_BLOCK_REASON,
                }
            )

    cleanup_jobs = [
        job
        for job in (plan.get("cleanup_jobs") or [])
        if _coerce_int(job.get("document_version_id")) in remaining_version_ids
    ]
    blocked = [*(plan.get("blocked_document_versions") or []), *newly_blocked]
    return {
        **plan,
        "material_ledger_checked": True,
        "cleanup_document_versions": remaining,
        "cleanup_jobs": cleanup_jobs,
        "blocked_document_versions": blocked,
        "skipped_jobs": skipped_jobs,
        "cleanup_document_version_count": len(remaining),
        "cleanup_job_count": len(cleanup_jobs),
        "blocked_document_version_count": len(blocked),
        "skipped_job_count": len(skipped_jobs),
    }


async def _fetch_slot_bound_versions(
    conn: Any, version_ids: Iterable[Any]
) -> Dict[int, str]:
    """查出候选版本里哪些已经绑定 Material Slot，返回 ``{版本 id: 槽位 id}``。

    只查候选集，不做全表扫描：清理链路每次只处理几十个版本。
    """
    ids = sorted({int(value) for value in (_coerce_int(item) for item in version_ids) if value is not None})
    if not ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT id, slot_id
        FROM fiscal_document_versions
        WHERE id = ANY($1::int[]) AND slot_id IS NOT NULL
        """,
        ids,
    )
    bound: Dict[int, str] = {}
    for row in rows or []:
        version_id = _coerce_int(row["id"])
        if version_id is not None:
            bound[version_id] = str(row["slot_id"])
    return bound


def plan_structured_ingest_cleanup(
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Preview which historical structured-ingest versions can be cleaned safely.

    这是**纯计划**，不知道 Material Slot 绑定情况，因此
    ``material_ledger_checked`` 恒为 False。真正对外返回的计划都必须经过
    ``_apply_material_ledger_protection`` 补齐这一步（见
    ``cleanup_structured_ingest_history``）；把未校验的计划当成可执行计划，
    就会把已绑定台账的版本算进"待删除"。
    """
    request_body = dict(body or {})
    department_id = str(request_body.get("department_id") or "").strip() or None
    department_name = _resolve_department_name(department_id)

    scanned_job_count = 0
    matched_job_count = 0
    scope_groups: Dict[str, List[Dict[str, Any]]] = {}
    skipped_jobs: List[Dict[str, Any]] = []

    for job_dir in sorted(iter_job_dirs(), key=get_job_quick_timestamp, reverse=True):
        scanned_job_count += 1
        job_id = job_dir.name
        status_payload = get_job_status_payload(job_id)
        department = resolve_job_department_context(
            job_id,
            status_payload=status_payload,
        )
        if department_id:
            if not department or str(department.get("department_id") or "").strip() != department_id:
                continue

        scope = build_structured_ingest_scope(
            organization_id=status_payload.get("organization_id"),
            organization_name=status_payload.get("organization_name"),
            fiscal_year=status_payload.get("fiscal_year"),
            report_year=status_payload.get("report_year"),
            doc_type=status_payload.get("doc_type"),
            report_kind=status_payload.get("report_kind"),
            filename=str(status_payload.get("filename") or ""),
        )
        if not scope:
            skipped_jobs.append(
                {
                    "job_id": job_id,
                    "filename": str(status_payload.get("filename") or ""),
                    "reason": "missing_scope",
                }
            )
            continue

        structured_payload = read_structured_ingest_payload(job_dir)
        if not structured_payload:
            embedded = status_payload.get("structured_ingest")
            if isinstance(embedded, dict):
                structured_payload = dict(embedded)
        structured_status = str(structured_payload.get("status") or "").strip().lower()
        matched_job_count += 1
        scope_groups.setdefault(scope["scope_key"], []).append(
            {
                "job_id": job_id,
                "filename": str(status_payload.get("filename") or ""),
                "organization_id": status_payload.get("organization_id"),
                "organization_name": status_payload.get("organization_name"),
                "department_id": department.get("department_id") if department else None,
                "department_name": department.get("department_name") if department else None,
                "report_year": scope.get("report_year"),
                "report_kind": scope.get("report_kind"),
                "scope_key": scope["scope_key"],
                "version_created_at": get_job_version_timestamp(job_dir, status_payload),
                "job_created_at": get_job_created_timestamp(job_dir, status_payload),
                "quick_ts": get_job_quick_timestamp(job_dir),
                "structured_status": structured_status or None,
                "document_version_id": _coerce_int(structured_payload.get("document_version_id")),
                "structured_payload": structured_payload,
            }
        )

    kept_jobs: List[Dict[str, Any]] = []
    cleanup_jobs: List[Dict[str, Any]] = []
    skipped_cleanup_jobs: List[Dict[str, Any]] = []
    retained_version_ids: Set[int] = set()
    cleanup_versions_map: Dict[int, Dict[str, Any]] = {}

    for scope_key, jobs in scope_groups.items():
        latest = max(
            jobs,
            key=lambda item: (
                float(item.get("version_created_at") or 0.0),
                float(item.get("job_created_at") or 0.0),
                float(item.get("quick_ts") or 0.0),
                str(item.get("job_id") or ""),
            ),
        )
        latest_job_id = str(latest.get("job_id") or "")
        latest_filename = str(latest.get("filename") or "") or None
        latest_document_version_id = _coerce_int(latest.get("document_version_id"))
        if latest_document_version_id is not None:
            retained_version_ids.add(latest_document_version_id)

        kept_jobs.append(
            {
                "job_id": latest_job_id,
                "filename": latest.get("filename"),
                "department_id": latest.get("department_id"),
                "department_name": latest.get("department_name"),
                "organization_id": latest.get("organization_id"),
                "organization_name": latest.get("organization_name"),
                "report_year": latest.get("report_year"),
                "report_kind": latest.get("report_kind"),
                "scope_key": scope_key,
                "document_version_id": latest_document_version_id,
                "structured_status": latest.get("structured_status"),
            }
        )

        for item in jobs:
            if str(item.get("job_id") or "") == latest_job_id:
                continue

            document_version_id = _coerce_int(item.get("document_version_id"))
            structured_status = str(item.get("structured_status") or "").strip().lower()
            if structured_status == "cleaned":
                skipped_cleanup_jobs.append(
                    {
                        "job_id": item.get("job_id"),
                        "filename": item.get("filename"),
                        "scope_key": scope_key,
                        "reason": "already_cleaned",
                    }
                )
                continue
            if document_version_id is None:
                skipped_cleanup_jobs.append(
                    {
                        "job_id": item.get("job_id"),
                        "filename": item.get("filename"),
                        "scope_key": scope_key,
                        "reason": "missing_document_version_id",
                    }
                )
                continue

            candidate = {
                "job_id": item.get("job_id"),
                "filename": item.get("filename"),
                "department_id": item.get("department_id"),
                "department_name": item.get("department_name"),
                "organization_id": item.get("organization_id"),
                "organization_name": item.get("organization_name"),
                "report_year": item.get("report_year"),
                "report_kind": item.get("report_kind"),
                "scope_key": scope_key,
                "document_version_id": document_version_id,
                "structured_status": item.get("structured_status"),
                "latest_job_id": latest_job_id,
                "latest_filename": latest_filename,
            }
            cleanup_jobs.append(candidate)
            version_entry = cleanup_versions_map.setdefault(
                document_version_id,
                {
                    "document_version_id": document_version_id,
                    "scope_key": scope_key,
                    "latest_job_id": latest_job_id,
                    "latest_filename": latest_filename,
                    "job_ids": [],
                    "jobs": [],
                },
            )
            version_entry["job_ids"].append(str(item.get("job_id") or ""))
            version_entry["jobs"].append(candidate)

    cleanup_document_versions: List[Dict[str, Any]] = []
    blocked_document_versions: List[Dict[str, Any]] = []

    for document_version_id, entry in sorted(cleanup_versions_map.items()):
        public_entry = {
            "document_version_id": document_version_id,
            "scope_key": entry["scope_key"],
            "latest_job_id": entry["latest_job_id"],
            "latest_filename": entry["latest_filename"],
            "job_count": len(entry["job_ids"]),
            "job_ids": list(entry["job_ids"]),
            "jobs": list(entry["jobs"]),
        }
        if document_version_id in retained_version_ids:
            blocked_document_versions.append(
                {
                    **public_entry,
                    "reason": "shared_with_latest_job",
                }
            )
            skipped_cleanup_jobs.extend(
                {
                    "job_id": job["job_id"],
                    "filename": job["filename"],
                    "scope_key": job["scope_key"],
                    "document_version_id": document_version_id,
                    "reason": "shared_with_latest_job",
                }
                for job in entry["jobs"]
            )
            continue
        cleanup_document_versions.append(public_entry)

    cleanup_version_ids = {
        int(item["document_version_id"])
        for item in cleanup_document_versions
        if _coerce_int(item.get("document_version_id")) is not None
    }
    executable_cleanup_jobs = [
        job for job in cleanup_jobs if _coerce_int(job.get("document_version_id")) in cleanup_version_ids
    ]

    return {
        "status": "preview",
        "dry_run": True,
        # 纯计划未经 Material Ledger 校验；对外返回前必须被
        # _apply_material_ledger_protection 置为 True。
        "material_ledger_checked": False,
        "department_id": department_id,
        "department_name": department_name,
        "scanned_job_count": scanned_job_count,
        "matched_job_count": matched_job_count,
        "scope_count": len(scope_groups),
        "kept_job_count": len(kept_jobs),
        "cleanup_job_count": len(executable_cleanup_jobs),
        "cleanup_document_version_count": len(cleanup_document_versions),
        "blocked_document_version_count": len(blocked_document_versions),
        "skipped_job_count": len(skipped_jobs) + len(skipped_cleanup_jobs),
        "kept_jobs": kept_jobs,
        "cleanup_jobs": executable_cleanup_jobs,
        "cleanup_document_versions": cleanup_document_versions,
        "blocked_document_versions": blocked_document_versions,
        "skipped_jobs": [*skipped_jobs, *skipped_cleanup_jobs],
    }


async def cleanup_structured_ingest_history(
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Delete historical structured-ingest document versions while keeping local job history.

    Material Ledger 兼容：已绑定 ``material_slots`` 的文件版本一律不删，
    且这个保护在两处生效——**计划阶段**把它移出待清理集合并标记
    ``material_slot_bound``，**DELETE 阶段**再自带 ``slot_id IS NULL`` 守卫。

    为什么需要两处：计划生成与执行之间可能插进新的绑定（重分析、重新归属），
    只信计划就会删掉刚刚进入台账的版本。DELETE 的守卫是兜底，
    它不依赖计划是否准确，因此 TOCTOU 窗口被关掉。

    预览（dry_run）同样要过 Material Ledger 校验：预览的全部意义就是
    "将会删除哪些"，而删除集合现在取决于绑定情况。查不到数据库时宁可
    明确报 503，也不返回一份可能夸大删除范围的计划——那正是
    "数据库里还在、文件系统说已清理"这类双真相的起点。
    """
    request_body = dict(body or {})
    dry_run = bool(request_body.get("dry_run", False))
    plan = plan_structured_ingest_cleanup(request_body)

    try:
        from src.db.connection import DatabaseConnection
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"structured ingest database unavailable: {exc}") from exc

    conn = None
    deleted_version_ids: List[int] = []
    blocked_at_delete: List[Dict[str, Any]] = []
    try:
        conn = await DatabaseConnection.acquire()

        candidate_ids = [
            item.get("document_version_id")
            for item in (plan.get("cleanup_document_versions") or [])
        ]
        slot_bound_versions = await _fetch_slot_bound_versions(conn, candidate_ids)
        plan = _apply_material_ledger_protection(plan, slot_bound_versions)

        if dry_run:
            return plan

        cleanup_versions = list(plan.get("cleanup_document_versions") or [])
        cleanup_jobs = list(plan.get("cleanup_jobs") or [])
        if not cleanup_versions:
            return {
                **plan,
                "status": "noop",
                "dry_run": False,
                "deleted_document_version_count": 0,
                "deleted_document_version_ids": [],
                "blocked_at_delete_count": 0,
                "blocked_at_delete": [],
                "updated_job_count": 0,
                "updated_job_ids": [],
            }

        async with conn.transaction():
            for item in cleanup_versions:
                document_version_id = _coerce_int(item.get("document_version_id"))
                if document_version_id is None:
                    continue
                # 计划之后可能新增了 Slot 绑定，所以守卫写在 DELETE 自身。
                # 影响 0 行即表示"这一版在执行前进入了台账"，按阻断处理，
                # 绝不当作普通成功。
                result = await conn.execute(
                    """
                    DELETE FROM fiscal_document_versions
                    WHERE id = $1 AND slot_id IS NULL
                    """,
                    document_version_id,
                )
                if str(result).strip().endswith("1"):
                    deleted_version_ids.append(document_version_id)
                else:
                    blocked_at_delete.append(
                        {
                            "document_version_id": document_version_id,
                            "reason": MATERIAL_LEDGER_BLOCK_REASON,
                        }
                    )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Structured ingest cleanup failed")
        raise HTTPException(status_code=500, detail=f"structured ingest cleanup failed: {exc}") from exc
    finally:
        if conn is not None:
            await DatabaseConnection.release(conn)

    deleted_version_set = set(deleted_version_ids)
    updated_job_ids: List[str] = []
    cleaned_at = time.time()
    for job in cleanup_jobs:
        document_version_id = _coerce_int(job.get("document_version_id"))
        # 只有**真的删掉了**（DELETE 1）才对 job 侧写 cleaned。
        # 计划阶段被阻断、或执行阶段才发现已绑定（DELETE 0）的版本，
        # 数据库里都还在，此时把 sidecar 标成 cleaned 会制造双真相：
        # 文件系统说"旧版入库已清理"，数据库里那一版却还在被槽位引用着。
        if document_version_id is None or document_version_id not in deleted_version_set:
            continue

        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            continue
        job_dir = UPLOAD_ROOT / job_id
        if not job_dir.exists():
            continue

        structured_payload = read_structured_ingest_payload(job_dir)
        if not structured_payload:
            status_payload = get_job_status_payload(job_id)
            embedded = status_payload.get("structured_ingest")
            if isinstance(embedded, dict):
                structured_payload = dict(embedded)

        next_payload = dict(structured_payload)
        ps_sync = next_payload.get("ps_sync")
        if isinstance(ps_sync, dict):
            next_ps_sync = dict(ps_sync)
            next_ps_sync["report_id"] = None
            next_payload["ps_sync"] = next_ps_sync

        next_payload.update(
            {
                "status": "cleaned",
                "reason": "historical_version_cleaned",
                "cleaned_at": cleaned_at,
                "cleaned_document_version_id": document_version_id,
                "document_version_id": None,
                "latest_job_id": job.get("latest_job_id"),
                "latest_filename": job.get("latest_filename"),
            }
        )
        write_structured_ingest_payload(job_dir, next_payload)
        updated_job_ids.append(job_id)

    blocked_at_delete_count = len(blocked_at_delete)
    # 日志只记版本 ID 与数量：清理链路会碰到与材料相关的记录，把整个条目打进
    # 日志等于把文件名等信息写进运行日志（仓库对日志内容有 fail-closed 门禁）。
    blocked_at_delete_version_ids = [
        item["document_version_id"] for item in blocked_at_delete
    ]
    if blocked_at_delete_count:
        logger.warning(
            "structured ingest cleanup skipped %d version(s) bound to material slots"
            " after planning: %s",
            blocked_at_delete_count,
            blocked_at_delete_version_ids,
        )

    return {
        **plan,
        "status": "done",
        "dry_run": False,
        "deleted_document_version_count": len(deleted_version_ids),
        "deleted_document_version_ids": deleted_version_ids,
        # 计划之后才被绑定、因而在执行阶段被拦下的版本。数量非零说明
        # 计划与执行之间确实发生了新的绑定，运维应当重新预览。
        "blocked_at_delete_count": blocked_at_delete_count,
        "blocked_at_delete": blocked_at_delete,
        "updated_job_count": len(updated_job_ids),
        "updated_job_ids": updated_job_ids,
    }


def collect_job_summary(job_dir: Path) -> Dict[str, Any]:
    """Build a job summary payload for list APIs."""
    status_file = job_dir / "status.json"
    filename = ""
    status_mtime_ns = -1
    status_size = -1
    pdf_mtime_ns = -1
    pdf_size = -1
    structured_mtime_ns = -1
    structured_size = -1
    ignored_mtime_ns = -1
    ignored_size = -1
    persistence_mtime_ns = -1
    persistence_size = -1

    try:
        stat = status_file.stat()
        status_mtime_ns = stat.st_mtime_ns
        status_size = stat.st_size
    except Exception:
        pass

    pdf_path: Optional[Path] = None
    try:
        pdf_path = find_first_pdf(job_dir)
        if pdf_path:
            filename = pdf_path.name
            pdf_stat = pdf_path.stat()
            pdf_mtime_ns = pdf_stat.st_mtime_ns
            pdf_size = pdf_stat.st_size
    except (FileNotFoundError, ValueError, OSError):
        pdf_path = None

    structured_path = get_structured_ingest_path(job_dir)
    try:
        if structured_path.exists():
            structured_stat = structured_path.stat()
            structured_mtime_ns = structured_stat.st_mtime_ns
            structured_size = structured_stat.st_size
    except Exception:
        pass

    ignored_path = get_ignored_issues_path(job_dir)
    try:
        if ignored_path.exists():
            ignored_stat = ignored_path.stat()
            ignored_mtime_ns = ignored_stat.st_mtime_ns
            ignored_size = ignored_stat.st_size
    except Exception:
        pass

    persistence_path = job_dir / PERSISTENCE_STATE_FILENAME
    try:
        if persistence_path.exists():
            persistence_stat = persistence_path.stat()
            persistence_mtime_ns = persistence_stat.st_mtime_ns
            persistence_size = persistence_stat.st_size
    except Exception:
        pass

    cache_key = (
        status_mtime_ns,
        status_size,
        filename,
        pdf_mtime_ns,
        pdf_size,
        structured_mtime_ns,
        structured_size,
        ignored_mtime_ns,
        ignored_size,
        persistence_mtime_ns,
        persistence_size,
    )
    cache_entry = _JOB_SUMMARY_CACHE.get(job_dir.name)
    if cache_entry and cache_entry.get("key") == cache_key:
        cached_summary = cache_entry.get("summary")
        if isinstance(cached_summary, dict):
            return dict(cached_summary)

    status_data = _enrich_job_organization_context(
        job_dir.name,
        read_json_file(status_file, default={}),
    )
    status_data = apply_job_issue_filters(job_dir, status_data)
    created_ts = get_job_created_timestamp(job_dir, status_data)
    updated_ts = get_job_quick_timestamp(job_dir)

    progress = status_data.get("progress", 0)
    status = status_data.get("status", "unknown")
    stage = status_data.get("stage")
    doc_type = str(status_data.get("doc_type") or "").strip()
    report_kind_raw = str(status_data.get("report_kind") or "").strip().lower()
    ts = status_data.get("ts")
    if ts is None:
        try:
            ts = job_dir.stat().st_mtime
        except Exception:
            ts = time.time()
    report_year: Optional[int] = None
    try:
        for key in ("report_year", "year", "fiscal_year"):
            year = parse_report_year(status_data.get(key))
            if year is not None:
                report_year = year
                break
    except Exception:
        report_year = None

    result_meta: Dict[str, Any] = {}
    try:
        result_meta = (status_data.get("result") or {}).get("meta") or {}
        if not isinstance(result_meta, dict):
            result_meta = {}
    except Exception:
        result_meta = {}

    # 前置修复 2：队列表"耗时"列。started_at/finished_at 写在 result.meta 里
    # （见 api/main.py 双模式/传统模式分析结束时的 _safe_write 调用），collect_job_summary
    # 此前从未把它们提取出来给列表接口用。真实历史数据实测（786 个任务目录）：
    # 用 finished_at-started_at 计算耗时，在"确实跑过分析"的任务子集（done/
    # review_required/error，336 个）里 97.6%（328/336）可计算；而 elapsed_ms.total
    # 只在双模式且 analyze_dual 写入时才有值，覆盖率明显更低（178/336，约 53%），
    # 因此优先用 started_at/finished_at 差值，elapsed_ms.total 仅作为兜底（例如
    # 某些历史产物两者都有但 finished_at 缺失的边缘情况）。
    # 严禁伪造：两者都拿不到时 elapsed_ms 为 None，前端必须显示"—"，不得显示 0。
    started_at = result_meta.get("started_at")
    finished_at = result_meta.get("finished_at")
    computed_elapsed_ms: Optional[int] = None
    if (
        isinstance(started_at, (int, float))
        and isinstance(finished_at, (int, float))
        and finished_at >= started_at
    ):
        computed_elapsed_ms = int(round((finished_at - started_at) * 1000))
    else:
        fallback_elapsed = result_meta.get("elapsed_ms")
        if isinstance(fallback_elapsed, dict):
            fallback_total = fallback_elapsed.get("total")
            if isinstance(fallback_total, (int, float)):
                computed_elapsed_ms = int(fallback_total)

    if not doc_type:
        try:
            doc_type = str(result_meta.get("doc_type") or "").strip()
        except Exception:
            doc_type = ""

    if report_year is None:
        try:
            for key in ("report_year", "year", "fiscal_year"):
                year = parse_report_year(result_meta.get(key))
                if year is not None:
                    report_year = year
                    break
        except Exception:
            report_year = None

    filename_year: Optional[int] = None
    if filename:
        try:
            filename_candidates = extract_report_year_candidates(filename)
            filename_year = filename_candidates[0] if filename_candidates else None
        except Exception:
            filename_year = None

    if report_year is None and filename_year is not None:
        report_year = filename_year
    elif report_year is None and filename:
        try:
            report_year = parse_report_year(filename)
        except Exception:
            report_year = None
    report_kind = normalize_report_kind(doc_type, filename)
    if report_kind_raw in {"budget", "final"}:
        report_kind = report_kind_raw
    mode = str(status_data.get("mode") or "legacy").strip() or "legacy"
    dual_mode_enabled = bool(
        status_data.get("dual_mode_enabled")
        or mode == "dual"
        or result_meta.get("dual_mode_enabled")
        or str(result_meta.get("mode") or "").strip().lower() == "dual"
    )
    use_local_rules = bool(
        status_data.get("use_local_rules", result_meta.get("use_local_rules", True))
    )
    use_ai_assist = bool(
        status_data.get("use_ai_assist", result_meta.get("use_ai_assist", True))
    )

    issue_total = 0
    issue_error = 0
    issue_warn = 0
    issue_info = 0
    # 因缺证据被降级为待复核的条目数：不计入 issue_total，但单独暴露供前端提示
    degraded_issue_total = 0
    merged_issue_total = 0
    merged_issue_conflicts = 0
    merged_issue_agreements = 0
    top_issue_rules: List[Dict[str, Any]] = []
    local_issue_total = 0
    local_issue_error = 0
    local_issue_warn = 0
    local_issue_info = 0
    ai_issue_total = 0
    ai_issue_error = 0
    ai_issue_warn = 0
    ai_issue_info = 0
    local_elapsed_ms = 0
    ai_elapsed_ms = 0
    provider_stats_count = 0
    local_participated = use_local_rules
    ai_participated = False
    structured_ingest = status_data.get("structured_ingest")
    if not isinstance(structured_ingest, dict):
        structured_ingest = read_structured_ingest_payload(job_dir)
    if not isinstance(structured_ingest, dict):
        structured_ingest = {}
    persistence_state = read_json_file(persistence_path, default={})
    if not isinstance(persistence_state, dict):
        persistence_state = {}

    def _severity_bucket(severity: Any) -> str:
        value = str(severity or "").lower()
        if value in {"critical", "high", "error", "fatal"}:
            return "error"
        if value in {"warn", "warning", "medium", "low"}:
            return "warn"
        return "info"

    def _partition_findings(items: Any) -> tuple[List[Any], int]:
        """按证据口径拆分 finding：返回 (正式问题列表, 降级问题条数)。

        历史任务产物没有 `evidence_status` 字段时 `is_formal_finding` 恒为 True，
        因此旧任务的计数结果与改动前完全一致（向后兼容）。
        """
        if not isinstance(items, list):
            return [], 0
        formal = [item for item in items if is_formal_finding(item)]
        return formal, len(items) - len(formal)

    def _collect_degraded_ids(result_payload: Any) -> Set[str]:
        """收集被降级条目的 finding id，用于把它们从 merged 计数里剔除。

        `apply_evidence_completeness` 只改写 ai_findings/rule_findings/issues，
        不会动 `merged.merged_ids`，所以这里按 id 求差集，
        避免"部门统计读 merged_issue_total 仍然大于 0"的口径分裂。
        """
        ids: Set[str] = set()
        if not isinstance(result_payload, dict):
            return ids
        buckets: List[Any] = [
            result_payload.get("ai_findings"),
            result_payload.get("rule_findings"),
        ]
        issues_payload = result_payload.get("issues")
        if isinstance(issues_payload, dict):
            buckets.append(issues_payload.get("all"))
            buckets.extend(issues_payload.get(key) for key in ("error", "warn", "info"))
        elif isinstance(issues_payload, list):
            buckets.append(issues_payload)
        for bucket in buckets:
            if not isinstance(bucket, list):
                continue
            for item in bucket:
                if is_formal_finding(item):
                    continue
                item_id = ""
                if isinstance(item, dict):
                    item_id = str(item.get("id") or "").strip()
                if item_id:
                    ids.add(item_id)
        return ids

    def _count_degraded_findings(result_payload: Any) -> int:
        """统计降级条目总数，遍历口径与 `count_formal_findings` 完全一致。

        legacy 结构里 `issues.all` 与 `issues.error/warn/info` 持有同一批对象，
        所以有 `all` 时只看 `all`，避免同一条被数两次。
        """
        if not isinstance(result_payload, dict):
            return 0
        issues_payload = result_payload.get("issues")
        if isinstance(issues_payload, dict):
            all_items = issues_payload.get("all")
            if isinstance(all_items, list):
                return _partition_findings(all_items)[1]
            return sum(
                _partition_findings(issues_payload.get(key))[1]
                for key in ("error", "warn", "info")
            )
        if isinstance(issues_payload, list):
            return _partition_findings(issues_payload)[1]
        return sum(
            _partition_findings(result_payload.get(key))[1]
            for key in ("rule_findings", "ai_findings")
        )

    def _summarize_finding_list(items: Any) -> tuple[int, int, int, int]:
        """汇总 finding 计数，只统计正式问题（缺证据被降级的不计入）。"""
        formal_items, _ = _partition_findings(items)
        total = len(formal_items)
        err = 0
        wrn = 0
        inf = 0
        for item in formal_items:
            if isinstance(item, dict):
                bucket = _severity_bucket(item.get("severity"))
            else:
                bucket = _severity_bucket("")
            if bucket == "error":
                err += 1
            elif bucket == "warn":
                wrn += 1
            else:
                inf += 1
        return (total, err, wrn, inf)

    try:
        result = status_data.get("result") or {}
        if not isinstance(result, dict):
            result = {}

        provider_stats = result_meta.get("provider_stats")
        if isinstance(provider_stats, list):
            provider_stats_count = len(provider_stats)

        degraded_ids = _collect_degraded_ids(result)
        degraded_issue_total = _count_degraded_findings(result)

        elapsed_ms = result_meta.get("elapsed_ms")
        if isinstance(elapsed_ms, dict):
            try:
                ai_elapsed_ms = int(elapsed_ms.get("ai") or 0)
            except Exception:
                ai_elapsed_ms = 0
            try:
                local_elapsed_ms = int(elapsed_ms.get("rule") or 0)
            except Exception:
                local_elapsed_ms = 0

        merged_summary = result.get("merged")
        if isinstance(merged_summary, dict):
            merged_totals = merged_summary.get("totals")
            if isinstance(merged_totals, dict):
                try:
                    merged_issue_total = int(merged_totals.get("merged") or 0)
                except Exception:
                    merged_issue_total = 0
                try:
                    merged_issue_conflicts = int(merged_totals.get("conflicts") or 0)
                except Exception:
                    merged_issue_conflicts = 0
                try:
                    merged_issue_agreements = int(merged_totals.get("agreements") or 0)
                except Exception:
                    merged_issue_agreements = 0
            # merged_ids 是合并前后的 finding id 集合，降级条目仍留在其中；
            # 这里按 id 求交集扣减，历史产物没有降级条目时交集为空，计数不变。
            if degraded_ids and merged_issue_total > 0:
                merged_ids = merged_summary.get("merged_ids")
                if isinstance(merged_ids, list):
                    degraded_in_merged = sum(
                        1
                        for item in merged_ids
                        if str(item or "").strip() in degraded_ids
                    )
                    merged_issue_total = max(0, merged_issue_total - degraded_in_merged)

        ai_findings = result.get("ai_findings")
        (
            ai_issue_total,
            ai_issue_error,
            ai_issue_warn,
            ai_issue_info,
        ) = _summarize_finding_list(ai_findings)

        rule_findings_for_local = result.get("rule_findings")
        (
            local_issue_total,
            local_issue_error,
            local_issue_warn,
            local_issue_info,
        ) = _summarize_finding_list(rule_findings_for_local)

        issues = result.get("issues")
        issue_items: List[Dict[str, Any]] = []

        if isinstance(issues, dict):
            err = issues.get("error")
            wrn = issues.get("warn")
            inf = issues.get("info")
            all_items = issues.get("all")

            err_formal, _ = _partition_findings(err)
            wrn_formal, _ = _partition_findings(wrn)
            inf_formal, _ = _partition_findings(inf)
            if isinstance(err, list):
                issue_error = len(err_formal)
            if isinstance(wrn, list):
                issue_warn = len(wrn_formal)
            if isinstance(inf, list):
                issue_info = len(inf_formal)

            if isinstance(all_items, list):
                all_formal, _ = _partition_findings(all_items)
                issue_total = len(all_formal)
                issue_items = [item for item in all_formal if isinstance(item, dict)]
            else:
                issue_total = issue_error + issue_warn + issue_info
                issue_items = [
                    item
                    for bucket in (err_formal, wrn_formal, inf_formal)
                    for item in bucket
                    if isinstance(item, dict)
                ]
        elif isinstance(issues, list):
            issues_formal, _ = _partition_findings(issues)
            issue_total = len(issues_formal)
            for item in issues_formal:
                bucket = _severity_bucket((item or {}).get("severity", ""))
                if bucket == "error":
                    issue_error += 1
                elif bucket == "warn":
                    issue_warn += 1
                else:
                    issue_info += 1
                if isinstance(item, dict):
                    issue_items.append(item)

        # Dual-mode fallback: when `issues` bucket is absent, use rule_findings.
        if issue_total == 0:
            rule_findings = result.get("rule_findings")
            if isinstance(rule_findings, list):
                rule_findings_formal, _ = _partition_findings(rule_findings)
                issue_total = len(rule_findings_formal)
                issue_items = [
                    item for item in rule_findings_formal if isinstance(item, dict)
                ]
                for item in rule_findings_formal:
                    bucket = _severity_bucket((item or {}).get("severity", ""))
                    if bucket == "error":
                        issue_error += 1
                    elif bucket == "warn":
                        issue_warn += 1
                    else:
                        issue_info += 1

        if issue_items:
            rule_counter: Dict[str, int] = {}
            for item in issue_items:
                rule_id = str(item.get("rule_id") or item.get("rule") or "").strip()
                if not rule_id:
                    continue
                rule_counter[rule_id] = rule_counter.get(rule_id, 0) + 1

            top_issue_rules = [
                {"rule_id": rid, "count": cnt}
                for rid, cnt in sorted(
                    rule_counter.items(),
                    key=lambda x: (-x[1], x[0]),
                )[:3]
            ]
    except Exception:
        logger.exception("Failed to summarize issue counts for job: %s", job_dir.name)

    # Legacy mode fallback: local findings come from the merged issue buckets.
    if local_issue_total == 0 and issue_total > 0:
        local_issue_total = issue_total
        local_issue_error = issue_error
        local_issue_warn = issue_warn
        local_issue_info = issue_info

    if merged_issue_total <= 0:
        merged_issue_total = issue_total

    ai_participated = bool(use_ai_assist) and (
        dual_mode_enabled
        or ai_issue_total > 0
        or ai_elapsed_ms > 0
        or provider_stats_count > 0
    )

    ps_sync = structured_ingest.get("ps_sync") if isinstance(structured_ingest, dict) else None
    if not isinstance(ps_sync, dict):
        ps_sync = {}

    summary = {
        "job_id": job_dir.name,
        "filename": filename,
        "status": status,
        "progress": progress,
        "ts": ts,
        "created_ts": created_ts,
        "updated_ts": updated_ts,
        "mode": mode,
        "dual_mode_enabled": dual_mode_enabled,
        "stage": stage,
        # Task 3：per-job 规范阶段进度（见 src/services/pipeline_stages.py）。
        # 未知/尚未写入时保持 None，前端必须显示"—"，不得补 0 或猜测值。
        "stage_progress": status_data.get("stage_progress"),
        # 失败任务的阶段归因：只在该任务确实失败过时才会有值，正常/进行中任务
        # 该字段不存在（不是显式的 None，是 status_data 里本来就没有这个键）。
        "stage_failed_at": status_data.get("stage_failed_at"),
        "quality_status": status_data.get("quality_status") or "complete",
        # 旧任务没有 analysis_conclusion 字段时按 status + 问题数反推，保证列表可读
        "analysis_conclusion": infer_analysis_conclusion(
            status,
            issue_total=merged_issue_total,
            explicit_conclusion=status_data.get("analysis_conclusion"),
        ),
        "review_reasons": status_data.get("review_reasons") or [],
        "page_coverage": status_data.get("page_coverage"),
        "scanned_page_count": status_data.get("scanned_page_count"),
        "report_year": report_year,
        "report_year_source": status_data.get("report_year_source"),
        "year_conflict": status_data.get("year_conflict"),
        "doc_type": doc_type or None,
        "report_kind": report_kind,
        "issue_total": issue_total,
        "issue_error": issue_error,
        "issue_warn": issue_warn,
        "issue_info": issue_info,
        # 与 evidence_guard.count_formal_findings 同口径的显式字段：
        # issue_total 已排除降级项，这里再单独暴露一份，便于下游明确引用口径。
        "formal_issue_total": issue_total,
        "degraded_issue_total": degraded_issue_total,
        "has_issues": issue_total > 0,
        "merged_issue_total": merged_issue_total,
        "merged_issue_conflicts": merged_issue_conflicts,
        "merged_issue_agreements": merged_issue_agreements,
        "top_issue_rules": top_issue_rules,
        "local_participated": local_participated,
        "ai_participated": ai_participated,
        "local_issue_total": local_issue_total,
        "local_issue_error": local_issue_error,
        "local_issue_warn": local_issue_warn,
        "local_issue_info": local_issue_info,
        "ai_issue_total": ai_issue_total,
        "ai_issue_error": ai_issue_error,
        "ai_issue_warn": ai_issue_warn,
        "ai_issue_info": ai_issue_info,
        "local_elapsed_ms": local_elapsed_ms,
        "ai_elapsed_ms": ai_elapsed_ms,
        # 前置修复 2：任务总耗时（毫秒），finished_at-started_at 优先，
        # elapsed_ms.total 兜底；两者都拿不到时为 None，前端显示"—"。
        "elapsed_ms": computed_elapsed_ms,
        "provider_stats_count": provider_stats_count,
        "structured_ingest_status": structured_ingest.get("status"),
        "structured_document_version_id": structured_ingest.get("document_version_id"),
        "structured_tables_count": structured_ingest.get("tables_count"),
        "structured_recognized_tables": structured_ingest.get("recognized_tables"),
        "structured_facts_count": structured_ingest.get("facts_count"),
        "structured_document_profile": structured_ingest.get("document_profile"),
        "structured_missing_optional_tables": structured_ingest.get("missing_optional_tables") or [],
        "review_item_count": structured_ingest.get("review_item_count"),
        "low_confidence_item_count": structured_ingest.get("low_confidence_item_count"),
        "structured_report_id": ps_sync.get("report_id"),
        "structured_table_data_count": ps_sync.get("table_data_count"),
        "structured_line_item_count": ps_sync.get("line_item_count"),
        "structured_sync_match_mode": ps_sync.get("match_mode"),
        "persistence_status": persistence_state.get("status") or "unknown",
        "persistence_last_attempt_ts": persistence_state.get("last_attempt_ts"),
        "persistence_error": persistence_state.get("error"),
        "organization_id": status_data.get("organization_id"),
        "organization_name": status_data.get("organization_name"),
        "organization_match_type": status_data.get("organization_match_type"),
        "organization_match_confidence": status_data.get("organization_match_confidence"),
        "created_by": status_data.get("created_by"),
    }

    _JOB_SUMMARY_CACHE[job_dir.name] = {
        "key": cache_key,
        "summary": summary,
    }
    if len(_JOB_SUMMARY_CACHE) > _JOB_SUMMARY_CACHE_MAX_SIZE:
        # Remove arbitrary oldest item (insertion-ordered dict).
        _JOB_SUMMARY_CACHE.pop(next(iter(_JOB_SUMMARY_CACHE)))

    return dict(summary)


def iter_job_dirs() -> List[Path]:
    """Return all existing job directories."""
    if not UPLOAD_ROOT.exists():
        return []
    return [
        path
        for path in UPLOAD_ROOT.iterdir()
        if path.is_dir()
        and not path.name.startswith(".")
        and _looks_like_job_dir(path)
    ]


def _looks_like_job_dir(path: Path) -> bool:
    """任务目录判定：含 status.json 或 PDF 文件才算任务目录。

    uploads/ 下可能遗留非任务目录（如 QC 报告输出目录 reports/），它们
    没有 status.json 也没有 PDF。若被当作任务目录，collect_job_summary 会
    给出 status="unknown"，前端 normalizeUiTaskStatus 的兜底分支把它归为
    analyzing，导致处理队列角标恒为 1（该"正在处理"任务实际并不存在）。
    """
    if (path / "status.json").exists():
        return True
    try:
        return any(p.suffix.lower() == ".pdf" for p in path.iterdir())
    except OSError:
        return False


def resolve_job_department_context(
    job_id: str,
    *,
    status_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """Resolve the owning department for a job via organization links or status metadata."""
    if not ORG_AVAILABLE:
        return None

    try:
        storage = require_org_storage()
    except Exception:
        return None

    org_id = ""
    try:
        link = storage.get_job_org(job_id)
        if link is not None:
            org_id = str(getattr(link, "org_id", "") or "").strip()
    except Exception:
        logger.exception("Failed to resolve org link for job %s", job_id)

    if not org_id:
        payload = status_payload or read_json_file(UPLOAD_ROOT / job_id / "status.json", default={})
        payload = _enrich_job_organization_context(job_id, payload)
        org_id = str(payload.get("organization_id") or "").strip()
    if not org_id:
        return None

    try:
        org = storage.get_by_id(org_id)
        while org is not None:
            level = str(getattr(org, "level", "") or "").strip().lower()
            if level == "department":
                return {
                    "department_id": str(getattr(org, "id", "") or ""),
                    "department_name": str(getattr(org, "name", "") or ""),
                    "organization_id": org_id,
                }
            parent_id = str(getattr(org, "parent_id", "") or "").strip()
            if not parent_id:
                break
            org = storage.get_by_id(parent_id)
    except Exception:
        logger.exception("Failed to resolve department context for job %s", job_id)
    return None


def resolve_organization_department_context(org_id: str) -> Optional[Dict[str, str]]:
    """Resolve the owning department for an organization id."""
    if not ORG_AVAILABLE:
        return None

    normalized_org_id = str(org_id or "").strip()
    if not normalized_org_id:
        return None

    try:
        storage = require_org_storage()
    except Exception:
        return None

    try:
        org = storage.get_by_id(normalized_org_id)
        while org is not None:
            level = str(getattr(org, "level", "") or "").strip().lower()
            if level == "department":
                return {
                    "department_id": str(getattr(org, "id", "") or ""),
                    "department_name": str(getattr(org, "name", "") or ""),
                }
            parent_id = str(getattr(org, "parent_id", "") or "").strip()
            if not parent_id:
                break
            org = storage.get_by_id(parent_id)
    except Exception:
        logger.exception("Failed to resolve department for organization %s", normalized_org_id)
    return None


def resolve_job_selection_scope(
    job_id: str,
    *,
    status_payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """Resolve the direct organization scope used for latest-job selection."""
    if not ORG_AVAILABLE:
        return None

    try:
        storage = require_org_storage()
    except Exception:
        return None

    payload = status_payload or read_json_file(UPLOAD_ROOT / job_id / "status.json", default={})
    payload = _enrich_job_organization_context(job_id, payload)
    linked_org_id = str(payload.get("organization_id") or "").strip()

    department = resolve_job_department_context(job_id, status_payload=payload)
    if not department:
        return None

    department_id = str(department.get("department_id") or "").strip()
    department_name = str(department.get("department_name") or "").strip()
    if not department_id:
        return None

    if linked_org_id:
        try:
            linked_org = storage.get_by_id(linked_org_id)
        except Exception:
            linked_org = None
        linked_level = str(getattr(linked_org, "level", "") or "").strip().lower()
        if linked_org is not None and linked_level in {"department", "unit"}:
            scope_name = str(
                getattr(linked_org, "name", "") or payload.get("organization_name") or ""
            ).strip()
            return {
                "scope_id": linked_org_id,
                "scope_name": scope_name or department_name,
                "scope_level": linked_level,
                "department_id": department_id,
                "department_name": department_name,
            }

    return {
        "scope_id": department_id,
        "scope_name": department_name,
        "scope_level": "department",
        "department_id": department_id,
        "department_name": department_name,
    }


def ensure_pdf(file: UploadFile) -> bool:
    """Basic PDF content-type/extension guard."""
    ct = (file.content_type or "").lower()
    name = (file.filename or "").lower()
    return ct in ("application/pdf", "application/x-pdf") or name.endswith(".pdf")


def delete_uploaded_job(job_id: str) -> None:
    job_dir = UPLOAD_ROOT / job_id
    if job_dir.exists():
        _remove_job_dir(job_dir)


def cleanup_uploaded_job(job_id: str) -> bool:
    """Best-effort cleanup that never replaces the original validation error."""
    try:
        delete_uploaded_job(job_id)
        return True
    except Exception:
        logger.exception("Failed to cleanup rejected upload %s", job_id)
        return False


def _remove_job_dir(job_dir: Path, *, attempts: int = 4, delay_seconds: float = 0.25) -> None:
    """Remove a job directory, retrying brief Windows file-handle races."""
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        if not job_dir.exists():
            return
        try:
            shutil.rmtree(job_dir)
            return
        except FileNotFoundError:
            return
        except (PermissionError, OSError) as exc:
            last_error = exc
            if attempt < attempts - 1:
                time.sleep(delay_seconds)

    if not job_dir.exists():
        return
    if last_error is not None:
        raise last_error


def get_pdf_page_count(pdf_path: Path) -> int:
    import fitz

    document = fitz.open(str(pdf_path))
    try:
        return int(document.page_count)
    finally:
        document.close()


def find_duplicate_upload(
    *,
    checksum: str,
    organization_id: Optional[str],
    fiscal_year: Optional[str] = None,
    doc_type: Optional[str] = None,
    exclude_job_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    normalized_org_id = str(organization_id or "").strip()
    normalized_checksum = str(checksum or "").strip()
    normalized_year = str(fiscal_year or "").strip()
    normalized_doc_type = str(doc_type or "").strip()
    if not normalized_org_id or not normalized_checksum:
        return None

    for job_dir in iter_job_dirs():
        if exclude_job_id and job_dir.name == exclude_job_id:
            continue
        status_file = job_dir / "status.json"
        if not status_file.exists():
            continue
        try:
            payload = json.loads(status_file.read_text(encoding="utf-8"))
        except Exception:
            continue

        if str(payload.get("organization_id") or "").strip() != normalized_org_id:
            continue
        if str(payload.get("checksum") or "").strip() != normalized_checksum:
            continue

        existing_year = str(payload.get("fiscal_year") or "").strip()
        if normalized_year and existing_year and existing_year != normalized_year:
            continue

        existing_doc_type = str(payload.get("doc_type") or "").strip()
        if normalized_doc_type and existing_doc_type and existing_doc_type != normalized_doc_type:
            continue

        return {
            "job_id": str(payload.get("job_id") or job_dir.name),
            "filename": str(payload.get("filename") or ""),
            "organization_id": normalized_org_id,
            "organization_name": str(payload.get("organization_name") or ""),
            "fiscal_year": existing_year,
            "doc_type": existing_doc_type,
        }

    return None


async def store_upload_file(
    file: UploadFile,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    persist_snapshot: bool = True,
) -> Dict[str, Any]:
    """Persist upload into a job directory and return metadata payload."""
    if SECURITY_AVAILABLE:
        safe_name = sanitize_filename(file.filename or "file.pdf")
        is_valid, error_msg = validate_upload_metadata(
            filename=safe_name,
            content_type=file.content_type or "",
        )
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_msg)
    else:
        if not ensure_pdf(file):
            raise HTTPException(status_code=415, detail="Only PDF files are supported")
        safe_name = Path(file.filename or "file.pdf").name

    max_upload_bytes = MAX_UPLOAD_MB * 1024 * 1024
    chunk_size = int(os.getenv("UPLOAD_CHUNK_BYTES", str(1024 * 1024)))
    job_id = os.urandom(16).hex()
    job_dir = UPLOAD_ROOT / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dst = job_dir / safe_name

    size = 0
    sha256 = hashlib.sha256()
    signature = bytearray()

    try:
        async with aiofiles.open(dst, "wb") as f:
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                if len(signature) < 4:
                    missing = 4 - len(signature)
                    signature.extend(chunk[:missing])
                size += len(chunk)
                if size > max_upload_bytes:
                    raise HTTPException(
                        status_code=413, detail=f"File exceeds {MAX_UPLOAD_MB}MB limit"
                    )
                sha256.update(chunk)
                await f.write(chunk)
    except HTTPException:
        try:
            if dst.exists():
                dst.unlink()
            if job_dir.exists() and not any(job_dir.iterdir()):
                job_dir.rmdir()
        except Exception:
            logger.exception("Failed to cleanup partial upload %s", job_id)
        raise
    finally:
        await file.close()

    if size < 4 or bytes(signature[:4]) != b"%PDF":
        try:
            if dst.exists():
                dst.unlink()
            if job_dir.exists() and not any(job_dir.iterdir()):
                job_dir.rmdir()
        except Exception:
            logger.exception("Failed to cleanup invalid upload %s", job_id)
        raise HTTPException(
            status_code=400,
            detail="File does not appear to be a valid PDF (invalid signature)",
        )

    try:
        page_count = get_pdf_page_count(dst)
    except Exception as exc:
        cleanup_uploaded_job(job_id)
        raise HTTPException(
            status_code=400,
            detail="File could not be parsed as a valid PDF",
        ) from exc
    if MAX_UPLOAD_PAGES > 0 and page_count > MAX_UPLOAD_PAGES:
        cleanup_uploaded_job(job_id)
        raise HTTPException(
            status_code=413,
            detail=f"PDF页数超过限制：{page_count} 页，当前上限为 {MAX_UPLOAD_PAGES} 页",
        )

    created_at = time.time()
    storage_key = dst.relative_to(UPLOAD_ROOT).as_posix()
    content_type = str(file.content_type or "application/pdf").strip() or "application/pdf"
    payload = {
        "id": job_id,
        "job_id": job_id,
        "filename": safe_name,
        "size": size,
        "page_count": page_count,
        "saved_path": storage_key,
        "storage_key": storage_key,
        "storage_backend": DOCUMENT_STORAGE_BACKEND,
        "content_type": content_type,
        "checksum": sha256.hexdigest(),
    }
    status_payload = {
        "job_id": job_id,
        "status": "uploaded",
        "progress": 0,
        "stage": "uploaded",
        "filename": safe_name,
        "size": size,
        "page_count": page_count,
        "saved_path": storage_key,
        "storage_key": storage_key,
        "storage_backend": DOCUMENT_STORAGE_BACKEND,
        "content_type": content_type,
        "checksum": sha256.hexdigest(),
        "version_created_at": created_at,
        "job_created_at": created_at,
        "ts": created_at,
    }
    if metadata:
        status_payload.update({key: value for key, value in metadata.items() if value is not None})
    write_json_file(job_dir / "status.json", status_payload)
    if persist_snapshot:
        await persist_analysis_job_snapshot(status_payload)
    return {**payload, **extract_job_status_context(status_payload)}


def get_job_status_payload(job_id: str) -> Dict[str, Any]:
    """Read job status payload by id."""
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")
    status_file = job_dir / "status.json"
    if not status_file.exists():
        return {"job_id": job_id, "status": "processing", "progress": 0}
    try:
        payload = json.loads(status_file.read_text(encoding="utf-8"))
        structured = read_structured_ingest_payload(job_dir)
        if structured:
            payload.setdefault("structured_ingest", structured)
        payload = _enrich_job_organization_context(job_id, payload)
        payload = _lift_result_payload(payload)
        return apply_job_issue_filters(job_dir, payload)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to read job status: {e}"
        ) from e


def get_job_review_payload(job_id: str) -> Dict[str, Any]:
    """Return structured ingest review payload for a job."""
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")

    structured = read_structured_ingest_payload(job_dir)
    if structured:
        return structured

    status_payload = get_job_status_payload(job_id)
    embedded = status_payload.get("structured_ingest")
    if isinstance(embedded, dict) and embedded:
        return embedded

    return {
        "job_id": job_id,
        "status": "pending",
        "review_item_count": 0,
        "review_items": [],
    }


def require_org_storage():
    """Return organization storage singleton or 503 when unavailable."""
    if not ORG_AVAILABLE:
        raise HTTPException(status_code=503, detail="organization service unavailable")
    return get_org_storage()


def require_user_store():
    """Return user store singleton or 503 when unavailable."""
    if not USER_STORE_AVAILABLE:
        raise HTTPException(status_code=503, detail="user service unavailable")
    return get_user_store()


def set_job_organization(
    job_id: str,
    org_id: str,
    *,
    match_type: str = "manual",
    confidence: float = 1.0,
) -> Dict[str, Any]:
    """Persist matched organization info into both status metadata and link storage."""
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")

    storage = require_org_storage()
    org = storage.get_by_id(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")

    normalized_confidence = round(float(confidence), 4)
    link = storage.link_job(
        job_id,
        org.id,
        match_type=match_type,
        confidence=normalized_confidence,
    )
    patch = {
        "organization_id": org.id,
        "organization_name": org.name,
        "organization_match_type": match_type,
        "organization_match_confidence": normalized_confidence,
    }
    merge_job_status(job_dir, patch)
    return {
        **patch,
        "link": to_dict(link),
    }


def delete_job(job_id: str) -> Dict[str, Any]:
    """Delete a job directory and remove its organization link if present."""
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")

    try:
        _remove_job_dir(job_dir)
        _JOB_SUMMARY_CACHE.pop(str(job_id), None)
        if ORG_AVAILABLE:
            try:
                require_org_storage().unlink_job(job_id)
            except Exception:
                logger.exception("Failed to unlink job during delete: %s", job_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to delete job: {exc}") from exc

    return {"success": True, "job_id": job_id}


async def start_analysis(
    job_id: str, body: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Queue a job for async processing and return started status."""
    job_dir = UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(
            status_code=404, detail="job_id does not exist, upload first"
        )

    if _pipeline_runner is None:
        raise HTTPException(
            status_code=500, detail="analysis pipeline is not configured"
        )

    status_file = job_dir / "status.json"
    body = body or {}
    existing_status = read_json_file(status_file, default={})

    # 分析请求契约（P0，docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md §3.5）：
    # - mode 只接受 legacy / dual / structured（structured 为历史存储值，
    #   仅结构化入库：无规则、无 AI）；
    # - legacy/structured 是规则模式别名，不请求 AI：显式 use_ai_assist=true
    #   属冲突参数，返回 422，不再静默忽略（此前样张就是这样
    #   "看似请求了 AI 实则没跑"）；
    # - 布尔参数统一经 normalize_request_flag 真值归一（字符串 "true"/"1"
    #   与 True 等价、未知取值 422），杜绝 is True 身份判断被字符串绕过；
    # - legacy 必须启用本地规则（否则无任何检查能力）；
    # - dual + use_ai_assist=true 才运行 AI；dual 默认请求 AI。
    mode = str(body.get("mode", "legacy")).strip().lower() or "legacy"
    if mode not in {"legacy", "dual", "structured"}:
        raise HTTPException(
            status_code=422,
            detail=f"invalid mode '{mode}': must be 'legacy', 'dual' or 'structured'",
        )
    ai_assist_flag = normalize_request_flag(body.get("use_ai_assist"), "use_ai_assist")
    local_rules_flag = normalize_request_flag(
        body.get("use_local_rules"), "use_local_rules"
    )
    if mode in {"legacy", "structured"}:
        if ai_assist_flag is True:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"conflicting analysis parameters: mode='{mode}' never runs "
                    "AI; use mode='dual' with use_ai_assist=true to request AI assist"
                ),
            )
        if mode == "legacy" and local_rules_flag is False:
            raise HTTPException(
                status_code=422,
                detail=(
                    "conflicting analysis parameters: mode='legacy' requires "
                    "use_local_rules=true (no other checker would run)"
                ),
            )
        use_ai_assist = False
    else:
        if not get_settings().is_dual_mode_enabled():
            raise HTTPException(
                status_code=422,
                detail=(
                    "dual mode is disabled by config (dual_mode.enabled=false); "
                    "enable it or use mode='legacy'"
                ),
            )
        use_ai_assist = True if ai_assist_flag is None else ai_assist_flag
    use_local_rules = True if local_rules_flag is None else local_rules_flag
    fiscal_year = (
        body.get("fiscal_year")
        if body.get("fiscal_year") is not None
        else existing_status.get("fiscal_year")
    )
    doc_type = (
        body.get("doc_type")
        if body.get("doc_type") is not None
        else existing_status.get("doc_type")
    )
    explicit_report_year = parse_report_year(body.get("report_year"))
    explicit_fiscal_year = parse_report_year(body.get("fiscal_year"))
    stored_report_year = parse_report_year(existing_status.get("report_year"))
    stored_fiscal_year = parse_report_year(existing_status.get("fiscal_year"))
    report_year = (
        explicit_report_year
        or explicit_fiscal_year
        or stored_report_year
        or stored_fiscal_year
    )
    report_year_source = (
        "request"
        if explicit_report_year or explicit_fiscal_year
        else "stored_metadata"
        if stored_report_year or stored_fiscal_year
        else "inferred"
    )
    filename = ""
    try:
        filename = find_first_pdf(job_dir).name
    except Exception:
        filename = ""

    filename_year: Optional[int] = None
    if filename:
        try:
            filename_candidates = extract_report_year_candidates(filename)
            filename_year = filename_candidates[0] if filename_candidates else None
        except Exception:
            filename_year = None

    if report_year is None:
        report_year = infer_report_year(
            filename=filename,
            preferred_year=fiscal_year,
        )
        report_year_source = "filename_or_content"
    year_conflict = None
    if report_year is not None and filename_year is not None and report_year != filename_year:
        year_conflict = {
            "selected_year": report_year,
            "filename_year": filename_year,
            "source": report_year_source,
            "requires_manual_review": True,
        }
    report_kind = normalize_report_kind(
        str(doc_type) if doc_type is not None else None,
        filename,
    )
    preserved_context = extract_job_status_context(existing_status)
    if filename and not preserved_context.get("filename"):
        preserved_context["filename"] = filename
    payload = {
        **preserved_context,
        "job_id": job_id,
        "status": "queued",
        "progress": 0,
        "message": "analysis task queued",
        "use_local_rules": use_local_rules,
        "use_ai_assist": use_ai_assist,
        "mode": mode,
        "fiscal_year": fiscal_year,
        "doc_type": doc_type,
        "report_year": report_year,
        "report_year_source": report_year_source,
        "year_conflict": year_conflict,
        "report_kind": report_kind,
        "ts": time.time(),
    }
    try:
        write_json_file(status_file, payload)
        # WP3-A：任何一次分析启动之前，先让上一次的复核立刻失效。
        # 挂在这里而不是某个具体路由，是为了让"从队列点开始分析"这条路径
        # 也走同一套失效逻辑（详见 _invalidate_reviews_before_analysis_start）。
        await _invalidate_reviews_before_analysis_start(job_id)
        await persist_analysis_job_snapshot(payload)
        queue = _job_queue
        dispatch = "local_queue"
        if queue is not None:
            await queue.enqueue(job_id)
        elif queue_runtime.should_enqueue_only():
            dispatch = "queued_waiting_worker"
            logger.info(
                "No local queue for job %s, keep queued for external worker dispatch",
                job_id,
            )
        elif queue_runtime.allow_inline_fallback():
            dispatch = "inline_fallback"
            logger.warning(
                "Job queue unavailable; fallback to in-process create_task for %s",
                job_id,
            )
            asyncio.create_task(_pipeline_runner(job_dir))
        else:
            dispatch = "queued_waiting_worker"
            logger.info(
                "Inline fallback disabled and local queue unavailable; job %s stays queued",
                job_id,
            )
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"failed to start analysis: {e}"
        ) from e
    return {"job_id": job_id, "status": "started", "dispatch": dispatch}


async def reanalyze_job(
    source_job_id: str, body: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Reset an existing job and queue it again for in-place analysis."""
    source_job_dir = UPLOAD_ROOT / source_job_id
    if not source_job_dir.exists():
        raise HTTPException(status_code=404, detail="source job_id does not exist")

    try:
        find_first_pdf(source_job_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="source job PDF does not exist") from exc

    source_status = get_job_status_payload(source_job_id)
    current_status = str(source_status.get("status") or "").strip().lower()
    if current_status in ACTIVE_ANALYSIS_STATUSES:
        raise HTTPException(status_code=409, detail="job is already being analyzed")

    for filename in REANALYZE_EPHEMERAL_FILES:
        target_path = source_job_dir / filename
        try:
            if target_path.exists():
                target_path.unlink()
        except Exception:
            logger.exception("Failed to clear stale reanalyze artifact %s", target_path)

    body = dict(body or {})
    if "use_local_rules" not in body:
        body["use_local_rules"] = bool(source_status.get("use_local_rules", True))
    if "mode" not in body:
        body["mode"] = str(source_status.get("mode") or "legacy")
    if "use_ai_assist" not in body:
        # 请求契约：legacy 不请求 AI，只有 dual 才沿用/默认请求 AI；
        # 否则旧任务（legacy + use_ai_assist=true 旧默认值）重分析会直接 422。
        # structured 模式保留原值（历史行为，结构化入库不消耗该标志）。
        # 历史 status.json 均为布尔值（分析请求经 normalize_request_flag
        # 归一后持久化），此处 bool() 是防御性收尾而非真值解析。
        resolved_mode = str(body["mode"])
        if resolved_mode == "dual":
            body["use_ai_assist"] = bool(source_status.get("use_ai_assist", True))
        elif resolved_mode == "legacy":
            body["use_ai_assist"] = False
        else:
            body["use_ai_assist"] = bool(source_status.get("use_ai_assist", True))
    if "fiscal_year" not in body and source_status.get("fiscal_year") is not None:
        body["fiscal_year"] = source_status.get("fiscal_year")
    if "doc_type" not in body and source_status.get("doc_type") is not None:
        body["doc_type"] = source_status.get("doc_type")
    if "report_year" not in body and source_status.get("report_year") is not None:
        body["report_year"] = source_status.get("report_year")

    started = await start_analysis(source_job_id, body)
    return {
        **started,
        "source_job_id": source_job_id,
        "job_id": source_job_id,
    }


async def _invalidate_reviews_before_analysis_start(job_id: str) -> None:
    """分析启动前的复核失效钩子（WP3-A）。

    挂在 ``start_analysis`` 这个**唯一**的分析起点上，而不是只挂在
    ``reanalyze_job``：仓库里还有一条会重跑分析的路——``POST /api/analyze/{job_id}``
    （处理队列的「开始分析」，``api/routes/analyze.py`` 直连 ``start_analysis``）。
    只挂在 reanalyze 上时，从队列对一份已复核完成的材料点「开始分析」会重新分析
    却**不失效旧复核**——页面继续显示「已完成复核」，而它复核的是上一代结果。
    挂在共同起点上，两个入口自动都覆盖。

    分析与复核的取舍：分析代际的变化本身由"重置时清指纹"保证（即使本钩子失败，
    complete 时的懒失效仍会拦下过期会话）；本钩子的价值是**立刻**让界面知道
    要重做，而不是等用户下一次打开页面。

    失败不阻断分析（维护与上传主流程不能被旁路能力卡住），但必须**大声报错**：
    钩子没生效意味着旧复核在库里仍显示"已完成"，这是运维需要立刻知道的事。

    函数内延迟导入是有意的，而且必须在延迟位置：``review_lifecycle_service``
    依赖 ``issue_workflow_store``，后者在模块级 ``from api import runtime``。
    若这里在模块级导入复核服务，``api.runtime → 复核服务 → issue_workflow_store
    → api.runtime`` 就构成循环导入。延迟到调用时，导入链已经走完，环不存在。
    """
    try:
        from src.services.review_lifecycle_service import (
            invalidate_reviews_for_analysis_restart,
        )

        result = await invalidate_reviews_for_analysis_restart(job_id)
    except Exception:  # noqa: BLE001 - 详见上方注释：不阻断但必须报错
        logger.error(
            "Failed to invalidate review sessions before analysis of job %s; "
            "a completed review may still be shown for this job",
            job_id,
            exc_info=True,
        )
        return
    if result.get("sessions_invalidated"):
        logger.info(
            "Analysis restart invalidated %s review session(s) for job %s",
            result.get("sessions_invalidated"),
            job_id,
        )


async def reanalyze_all_jobs(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Clone and requeue all eligible existing jobs."""
    request_body = dict(body or {})
    include_active = bool(request_body.pop("include_active", False))
    latest_per_department = bool(request_body.pop("latest_per_department", True))
    direct_department_only = bool(request_body.pop("direct_department_only", False))

    source_job_dirs = sorted(iter_job_dirs(), key=get_job_quick_timestamp, reverse=True)
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []

    if latest_per_department and ORG_AVAILABLE:
        seen_scopes = set()
        for job_dir in source_job_dirs:
            source_job_id = job_dir.name
            status_payload = get_job_status_payload(source_job_id)
            selection_scope = resolve_job_selection_scope(
                source_job_id,
                status_payload=status_payload,
            )
            if not selection_scope:
                skipped.append(
                    {
                        "source_job_id": source_job_id,
                        "reason": "unresolved_department",
                    }
                )
                continue

            department_id = str(selection_scope.get("department_id") or "").strip()
            if not department_id:
                skipped.append(
                    {
                        "source_job_id": source_job_id,
                        "reason": "unresolved_department",
                    }
                )
                continue

            if direct_department_only:
                scope_level = str(selection_scope.get("scope_level") or "").strip().lower()
                scope_id = str(selection_scope.get("scope_id") or "").strip()
                if scope_level != "department" or scope_id != department_id:
                    skipped.append(
                        {
                            "source_job_id": source_job_id,
                            "department_id": department_id,
                            "department_name": selection_scope.get("department_name"),
                            "scope_id": selection_scope.get("scope_id"),
                            "scope_name": selection_scope.get("scope_name"),
                            "scope_level": selection_scope.get("scope_level"),
                            "reason": "subordinate_unit_report",
                        }
                    )
                    continue

            scope_key = (
                department_id
                if direct_department_only
                else str(selection_scope.get("scope_id") or "").strip() or department_id
            )
            if scope_key in seen_scopes:
                skipped.append(
                    {
                        "source_job_id": source_job_id,
                        "department_id": department_id,
                        "department_name": selection_scope.get("department_name"),
                        "scope_id": selection_scope.get("scope_id"),
                        "scope_name": selection_scope.get("scope_name"),
                        "scope_level": selection_scope.get("scope_level"),
                        "reason": "not_latest_in_department" if direct_department_only else "not_latest_in_scope",
                    }
                )
                continue

            seen_scopes.add(scope_key)
            candidates.append(
                {
                    "job_dir": job_dir,
                    "job_id": source_job_id,
                    "status_payload": status_payload,
                    "department": {
                        "department_id": department_id,
                        "department_name": selection_scope.get("department_name"),
                    },
                    "selection_scope": selection_scope,
                }
            )
    else:
        for job_dir in source_job_dirs:
            source_job_id = job_dir.name
            candidates.append(
                {
                    "job_dir": job_dir,
                    "job_id": source_job_id,
                    "status_payload": get_job_status_payload(source_job_id),
                    "department": None,
                }
            )

    for candidate in candidates:
        source_job_id = str(candidate["job_id"])
        status_payload = candidate["status_payload"]
        department = candidate.get("department") or {}
        selection_scope = candidate.get("selection_scope") or {}
        try:
            current_status = str(status_payload.get("status") or "").strip().lower()
            if not include_active and current_status in ACTIVE_ANALYSIS_STATUSES:
                skipped.append(
                    {
                        "source_job_id": source_job_id,
                        "department_id": department.get("department_id"),
                        "department_name": department.get("department_name"),
                        "scope_id": selection_scope.get("scope_id"),
                        "scope_name": selection_scope.get("scope_name"),
                        "scope_level": selection_scope.get("scope_level"),
                        "status": current_status,
                        "reason": "active_analysis",
                    }
                )
                continue

            result = await reanalyze_job(source_job_id, request_body)
            created.append(
                {
                    "source_job_id": source_job_id,
                    "job_id": result.get("job_id"),
                    "status": result.get("status"),
                    "dispatch": result.get("dispatch"),
                    "department_id": department.get("department_id"),
                    "department_name": department.get("department_name"),
                    "scope_id": selection_scope.get("scope_id"),
                    "scope_name": selection_scope.get("scope_name"),
                    "scope_level": selection_scope.get("scope_level"),
                }
            )
        except HTTPException as exc:
            failed.append(
                {
                    "source_job_id": source_job_id,
                    "department_id": department.get("department_id"),
                    "department_name": department.get("department_name"),
                    "scope_id": selection_scope.get("scope_id"),
                    "scope_name": selection_scope.get("scope_name"),
                    "scope_level": selection_scope.get("scope_level"),
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }
            )
        except Exception as exc:
            logger.exception("Failed to batch reanalyze job %s", source_job_id)
            failed.append(
                {
                    "source_job_id": source_job_id,
                    "department_id": department.get("department_id"),
                    "department_name": department.get("department_name"),
                    "scope_id": selection_scope.get("scope_id"),
                    "scope_name": selection_scope.get("scope_name"),
                    "scope_level": selection_scope.get("scope_level"),
                    "status_code": 500,
                    "detail": str(exc),
                }
            )

    return {
        "status": "started",
        "include_active": include_active,
        "latest_per_department": latest_per_department,
        "direct_department_only": direct_department_only,
        "requested_count": len(source_job_dirs),
        "selected_count": len(candidates),
        "created_count": len(created),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "created": created,
        "skipped": skipped,
        "failed": failed,
    }


def rematch_job_organizations(body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Preview or apply organization re-matching for existing jobs."""
    if not ORG_AVAILABLE:
        raise HTTPException(status_code=503, detail="organization service unavailable")

    from src.services.org_matcher import get_org_matcher

    request_body = dict(body or {})
    dry_run = bool(request_body.get("dry_run", True))
    include_manual = bool(request_body.get("include_manual", False))

    try:
        minimum_confidence = float(request_body.get("minimum_confidence", 0.6))
    except Exception:
        minimum_confidence = 0.6
    minimum_confidence = max(0.0, min(1.0, minimum_confidence))

    scoped_department_id = str(request_body.get("department_id") or "").strip()
    scoped_department_name = str(request_body.get("department_name") or "").strip()

    storage = require_org_storage()
    matcher = get_org_matcher()

    matches: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    scanned_count = 0
    fast_path_hits = 0
    pdf_text_fallback_hits = 0

    def _serialize_org_brief(
        org_id: str,
        *,
        match_type: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_org_id = str(org_id or "").strip()
        if not normalized_org_id:
            return None

        org = storage.get_by_id(normalized_org_id)
        if org is None:
            return {
                "organization_id": normalized_org_id,
                "organization_name": None,
                "match_type": match_type,
                "confidence": confidence,
                "department_id": None,
                "department_name": None,
            }

        department = resolve_organization_department_context(normalized_org_id) or {}
        payload: Dict[str, Any] = {
            "organization_id": str(getattr(org, "id", "") or ""),
            "organization_name": str(getattr(org, "name", "") or ""),
            "level": str(getattr(org, "level", "") or ""),
            "department_id": department.get("department_id"),
            "department_name": department.get("department_name"),
        }
        if match_type is not None:
            payload["match_type"] = match_type
        if confidence is not None:
            payload["confidence"] = round(float(confidence), 4)
        return payload

    def _in_scope(*, current_org_id: str, suggested_org_id: str) -> bool:
        if not scoped_department_id:
            return True
        current_department = resolve_organization_department_context(current_org_id) or {}
        suggested_department = resolve_organization_department_context(suggested_org_id) or {}
        return scoped_department_id in {
            str(current_department.get("department_id") or "").strip(),
            str(suggested_department.get("department_id") or "").strip(),
        }

    def _pick_best_match(pdf_path: Path) -> tuple[Optional[Any], float]:
        nonlocal fast_path_hits, pdf_text_fallback_hits

        filename_matches = matcher.suggest_matches(pdf_path.name, "", top_n=1)
        if filename_matches:
            candidate, confidence = filename_matches[0]
            numeric_confidence = round(float(confidence), 4)
            if numeric_confidence >= minimum_confidence:
                fast_path_hits += 1
                return candidate, numeric_confidence

        first_page_text = extract_pdf_first_page_text(pdf_path)
        pdf_text_fallback_hits += 1
        matches_with_text = matcher.suggest_matches(pdf_path.name, first_page_text, top_n=1)
        if not matches_with_text:
            return None, 0.0
        candidate, confidence = matches_with_text[0]
        return candidate, round(float(confidence), 4)

    for job_dir in sorted(iter_job_dirs(), key=get_job_quick_timestamp, reverse=True):
        scanned_count += 1
        job_id = job_dir.name
        try:
            pdf_path = find_first_pdf(job_dir)
            status_payload = _enrich_job_organization_context(
                job_id,
                get_job_status_payload(job_id),
            )

            current_org_id = str(status_payload.get("organization_id") or "").strip()
            current_match_type = str(
                status_payload.get("organization_match_type") or ""
            ).strip().lower()

            try:
                current_confidence = round(
                    float(status_payload.get("organization_match_confidence") or 0.0),
                    4,
                )
            except Exception:
                current_confidence = 0.0

            if current_match_type == "manual" and not include_manual:
                skipped.append(
                    {
                        "job_id": job_id,
                        "filename": pdf_path.name,
                        "reason": "manual_locked",
                        "detail": "当前是手动关联，默认不参与批量重匹配",
                        "current": _serialize_org_brief(
                            current_org_id,
                            match_type=current_match_type,
                            confidence=current_confidence,
                        ),
                    }
                )
                continue

            suggested_org, suggested_confidence = _pick_best_match(pdf_path)
            if suggested_org is None:
                skipped.append(
                    {
                        "job_id": job_id,
                        "filename": pdf_path.name,
                        "reason": "no_match",
                        "detail": "没有找到可用的组织匹配建议",
                        "current": _serialize_org_brief(
                            current_org_id,
                            match_type=current_match_type or None,
                            confidence=current_confidence if current_org_id else None,
                        ),
                    }
                )
                continue

            suggested_org_id = str(getattr(suggested_org, "id", "") or "").strip()

            if not suggested_org_id:
                skipped.append(
                    {
                        "job_id": job_id,
                        "filename": pdf_path.name,
                        "reason": "invalid_match",
                        "detail": "匹配器返回了无效的组织建议",
                    }
                )
                continue

            if suggested_confidence < minimum_confidence:
                skipped.append(
                    {
                        "job_id": job_id,
                        "filename": pdf_path.name,
                        "reason": "low_confidence",
                        "detail": f"建议置信度过低（{suggested_confidence:.2f}）",
                        "current": _serialize_org_brief(
                            current_org_id,
                            match_type=current_match_type or None,
                            confidence=current_confidence if current_org_id else None,
                        ),
                        "suggested": _serialize_org_brief(
                            suggested_org_id,
                            confidence=suggested_confidence,
                        ),
                    }
                )
                continue

            if not _in_scope(current_org_id=current_org_id, suggested_org_id=suggested_org_id):
                skipped.append(
                    {
                        "job_id": job_id,
                        "filename": pdf_path.name,
                        "reason": "outside_department_scope",
                        "detail": "不在当前部门范围内",
                    }
                )
                continue

            if current_org_id and current_org_id == suggested_org_id:
                skipped.append(
                    {
                        "job_id": job_id,
                        "filename": pdf_path.name,
                        "reason": "same_match",
                        "detail": "当前关联与建议结果一致，无需调整",
                        "current": _serialize_org_brief(
                            current_org_id,
                            match_type=current_match_type or None,
                            confidence=current_confidence,
                        ),
                    }
                )
                continue

            match_item: Dict[str, Any] = {
                "job_id": job_id,
                "filename": pdf_path.name,
                "action": "reassociate" if current_org_id else "associate",
                "current": _serialize_org_brief(
                    current_org_id,
                    match_type=current_match_type or None,
                    confidence=current_confidence if current_org_id else None,
                ),
                "suggested": _serialize_org_brief(
                    suggested_org_id,
                    confidence=suggested_confidence,
                ),
            }

            if dry_run:
                matches.append(match_item)
                continue

            binding = set_job_organization(
                job_id,
                suggested_org_id,
                match_type="auto",
                confidence=suggested_confidence,
            )
            matches.append(
                {
                    **match_item,
                    "updated": True,
                    "binding": binding,
                }
            )
        except FileNotFoundError:
            skipped.append(
                {
                    "job_id": job_id,
                    "reason": "missing_pdf",
                    "detail": "未找到原始 PDF，无法重匹配",
                }
            )
        except HTTPException as exc:
            failed.append(
                {
                    "job_id": job_id,
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }
            )
        except Exception as exc:
            logger.exception("Failed to rematch organization for job %s", job_id)
            failed.append(
                {
                    "job_id": job_id,
                    "status_code": 500,
                    "detail": str(exc),
                }
            )

    if scoped_department_id and not scoped_department_name:
        scoped_department = resolve_organization_department_context(scoped_department_id) or {}
        scoped_department_name = str(scoped_department.get("department_name") or "").strip()

    return {
        "status": "preview" if dry_run else "applied",
        "dry_run": dry_run,
        "include_manual": include_manual,
        "minimum_confidence": minimum_confidence,
        "department_id": scoped_department_id or None,
        "department_name": scoped_department_name or None,
        "scanned_count": scanned_count,
        "candidate_count": len(matches),
        "updated_count": 0 if dry_run else len(matches),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "fast_path_hits": fast_path_hits,
        "pdf_text_fallback_hits": pdf_text_fallback_hits,
        "matches": matches,
        "skipped": skipped,
        "failed": failed,
    }


def repair_missing_job_organization_links(
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Repair missing or stale job-to-organization links."""
    if not ORG_AVAILABLE:
        raise HTTPException(status_code=503, detail="organization service unavailable")

    from src.services.org_matcher import get_org_matcher

    request_body = dict(body or {})
    dry_run = bool(request_body.get("dry_run", True))

    try:
        minimum_confidence = float(request_body.get("minimum_confidence", 0.6))
    except Exception:
        minimum_confidence = 0.6
    minimum_confidence = max(0.0, min(1.0, minimum_confidence))

    scoped_department_id = str(request_body.get("department_id") or "").strip()
    scoped_department_name = str(request_body.get("department_name") or "").strip()

    storage = require_org_storage()
    matcher = get_org_matcher()

    repairs: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    scanned_count = 0
    linked_from_status_count = 0
    matched_from_pdf_count = 0
    fast_path_hits = 0
    pdf_text_fallback_hits = 0

    def _serialize_org_brief(
        org_id: str,
        *,
        match_type: Optional[str] = None,
        confidence: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        normalized_org_id = str(org_id or "").strip()
        if not normalized_org_id:
            return None

        org = storage.get_by_id(normalized_org_id)
        if org is None:
            return {
                "organization_id": normalized_org_id,
                "organization_name": None,
                "match_type": match_type,
                "confidence": confidence,
                "department_id": None,
                "department_name": None,
            }

        department = resolve_organization_department_context(normalized_org_id) or {}
        payload: Dict[str, Any] = {
            "organization_id": str(getattr(org, "id", "") or ""),
            "organization_name": str(getattr(org, "name", "") or ""),
            "level": str(getattr(org, "level", "") or ""),
            "department_id": department.get("department_id"),
            "department_name": department.get("department_name"),
        }
        if match_type is not None:
            payload["match_type"] = match_type
        if confidence is not None:
            payload["confidence"] = round(float(confidence), 4)
        return payload

    def _pick_best_match(pdf_path: Path) -> tuple[Optional[Any], float]:
        nonlocal fast_path_hits, pdf_text_fallback_hits

        filename_matches = matcher.suggest_matches(pdf_path.name, "", top_n=1)
        if filename_matches:
            candidate, confidence = filename_matches[0]
            numeric_confidence = round(float(confidence), 4)
            if numeric_confidence >= minimum_confidence:
                fast_path_hits += 1
                return candidate, numeric_confidence

        first_page_text = extract_pdf_first_page_text(pdf_path)
        pdf_text_fallback_hits += 1
        matches_with_text = matcher.suggest_matches(pdf_path.name, first_page_text, top_n=1)
        if not matches_with_text:
            return None, 0.0

        candidate, confidence = matches_with_text[0]
        return candidate, round(float(confidence), 4)

    for job_dir in sorted(iter_job_dirs(), key=get_job_quick_timestamp, reverse=True):
        scanned_count += 1
        job_id = job_dir.name
        try:
            pdf_path = find_first_pdf(job_dir)
            status_payload = _enrich_job_organization_context(
                job_id,
                get_job_status_payload(job_id),
            )

            department = resolve_job_department_context(
                job_id,
                status_payload=status_payload,
            ) or {}
            department_id = str(department.get("department_id") or "").strip()
            department_name = str(department.get("department_name") or "").strip()

            if scoped_department_id and department_id != scoped_department_id:
                skipped.append(
                    {
                        "job_id": job_id,
                        "filename": pdf_path.name,
                        "reason": "outside_department_scope",
                        "detail": "job is outside the selected department scope",
                        "department_id": department_id or None,
                        "department_name": department_name or None,
                    }
                )
                continue

            link = storage.get_job_org(job_id)
            linked_org_id = str(getattr(link, "org_id", "") or "").strip()
            linked_org = storage.get_by_id(linked_org_id) if linked_org_id else None

            status_org_id = str(status_payload.get("organization_id") or "").strip()
            status_org = storage.get_by_id(status_org_id) if status_org_id else None

            current_match_type = str(
                status_payload.get("organization_match_type") or getattr(link, "match_type", "") or ""
            ).strip().lower()
            try:
                current_confidence = round(
                    float(
                        status_payload.get("organization_match_confidence")
                        or getattr(link, "confidence", 0.0)
                        or 0.0
                    ),
                    4,
                )
            except Exception:
                current_confidence = 0.0

            if linked_org is not None:
                skipped.append(
                    {
                        "job_id": job_id,
                        "filename": pdf_path.name,
                        "reason": "already_linked",
                        "detail": "job already has a valid organization link",
                        "current": _serialize_org_brief(
                            linked_org_id,
                            match_type=current_match_type or None,
                            confidence=current_confidence if current_confidence > 0 else None,
                        ),
                    }
                )
                continue

            target_org_id = ""
            target_confidence = current_confidence if current_confidence > 0 else 1.0
            action = ""
            detail = ""

            if status_org is not None:
                target_org_id = status_org_id
                action = "link_status_org"
                detail = "restored link from job status metadata"
                linked_from_status_count += 1
            else:
                suggested_org, suggested_confidence = _pick_best_match(pdf_path)
                if suggested_org is None:
                    skipped.append(
                        {
                            "job_id": job_id,
                            "filename": pdf_path.name,
                            "reason": "no_match",
                            "detail": "unable to infer organization from filename or first-page text",
                            "current": _serialize_org_brief(
                                linked_org_id or status_org_id,
                                match_type=current_match_type or None,
                                confidence=current_confidence if current_confidence > 0 else None,
                            ),
                        }
                    )
                    continue

                target_org_id = str(getattr(suggested_org, "id", "") or "").strip()
                if not target_org_id:
                    skipped.append(
                        {
                            "job_id": job_id,
                            "filename": pdf_path.name,
                            "reason": "invalid_match",
                            "detail": "matcher returned an invalid organization id",
                        }
                    )
                    continue

                if suggested_confidence < minimum_confidence:
                    skipped.append(
                        {
                            "job_id": job_id,
                            "filename": pdf_path.name,
                            "reason": "low_confidence",
                            "detail": f"suggested confidence is below threshold: {suggested_confidence:.2f}",
                            "suggested": _serialize_org_brief(
                                target_org_id,
                                confidence=suggested_confidence,
                            ),
                        }
                    )
                    continue

                target_confidence = suggested_confidence
                action = "match_from_pdf"
                detail = "repaired link using organization matcher"
                matched_from_pdf_count += 1

            repair_item: Dict[str, Any] = {
                "job_id": job_id,
                "filename": pdf_path.name,
                "action": action,
                "detail": detail,
                "department_id": department_id or None,
                "department_name": department_name or None,
                "current": _serialize_org_brief(
                    linked_org_id or status_org_id,
                    match_type=current_match_type or None,
                    confidence=current_confidence if current_confidence > 0 else None,
                ),
                "suggested": _serialize_org_brief(
                    target_org_id,
                    confidence=target_confidence,
                ),
            }

            if dry_run:
                repairs.append(repair_item)
                continue

            binding = set_job_organization(
                job_id,
                target_org_id,
                match_type=current_match_type or "auto",
                confidence=target_confidence,
            )
            repairs.append(
                {
                    **repair_item,
                    "updated": True,
                    "binding": binding,
                }
            )
        except FileNotFoundError:
            skipped.append(
                {
                    "job_id": job_id,
                    "reason": "missing_pdf",
                    "detail": "source pdf is missing and cannot be repaired automatically",
                }
            )
        except HTTPException as exc:
            failed.append(
                {
                    "job_id": job_id,
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                }
            )
        except Exception as exc:
            logger.exception("Failed to repair organization link for job %s", job_id)
            failed.append(
                {
                    "job_id": job_id,
                    "status_code": 500,
                    "detail": str(exc),
                }
            )

    if scoped_department_id and not scoped_department_name:
        scoped_department = resolve_organization_department_context(scoped_department_id) or {}
        scoped_department_name = str(scoped_department.get("department_name") or "").strip()

    return {
        "status": "preview" if dry_run else "applied",
        "dry_run": dry_run,
        "minimum_confidence": minimum_confidence,
        "department_id": scoped_department_id or None,
        "department_name": scoped_department_name or None,
        "scanned_count": scanned_count,
        "candidate_count": len(repairs),
        "repaired_count": 0 if dry_run else len(repairs),
        "linked_from_status_count": linked_from_status_count,
        "matched_from_pdf_count": matched_from_pdf_count,
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "fast_path_hits": fast_path_hits,
        "pdf_text_fallback_hits": pdf_text_fallback_hits,
        "repairs": repairs,
        "skipped": skipped,
        "failed": failed,
    }
