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
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional, Set

import aiofiles
from fastapi import HTTPException, Request, UploadFile

from api import queue_runtime

logger = logging.getLogger(__name__)

APP_TITLE = "GovBudgetChecker API"
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "30"))
MAX_UPLOAD_PAGES = int(os.getenv("MAX_UPLOAD_PAGES", "800"))
UPLOAD_ROOT = Path(os.getenv("UPLOAD_DIR", "uploads")).resolve()
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

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

_pipeline_runner: Optional[Callable[[Path], Awaitable[None]]] = None
_job_queue: Optional["DurableJobQueue"] = None
_JOB_SUMMARY_CACHE: Dict[str, Dict[str, Any]] = {}
_JOB_SUMMARY_CACHE_MAX_SIZE = 2048
STRUCTURED_INGEST_FILENAME = "structured_ingest.json"
IGNORED_ISSUES_FILENAME = "ignored_issues.json"
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
    "report_kind",
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

_YEAR_4_RE = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
_YEAR_2_RE = re.compile(
    r"(?<!\d)(\d{2})(?=\s*(?:\u5e74|\u5e74\u5ea6|\u9884\u7b97|\u51b3\u7b97|budget|final|settlement|accounts|$))",
    re.I,
)
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
    """Return the first PDF in a job directory."""
    pdfs = sorted(job_dir.glob("*.pdf"))
    if not pdfs:
        raise FileNotFoundError("PDF file not found under job directory")
    return pdfs[0]


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
    """Write JSON payload with UTF-8 encoding."""
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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

    ignored_ids.add(normalized_issue_id)
    write_ignored_issue_ids(job_dir, ignored_ids)

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


def extract_pdf_page_texts(pdf_path: Path, max_pages: int = 3) -> List[str]:
    """Extract the first few page texts from a PDF path."""
    return _extract_pdf_page_texts(str(pdf_path), max_pages=max_pages)


def extract_pdf_first_page_text(pdf_path: Path) -> str:
    """Best-effort first-page text extraction for upload-time organization matching."""
    page_texts = extract_pdf_page_texts(pdf_path, max_pages=1)
    return page_texts[0] if page_texts else ""


def parse_report_year(raw: Any) -> Optional[int]:
    """Parse year from arbitrary value and return 4-digit year."""
    for year in extract_report_year_candidates(raw):
        return year
    return None


def extract_report_year_candidates(raw: Any) -> List[int]:
    """Extract report year candidates from free-form text."""
    if raw is None:
        return []
    text = str(raw).strip()
    if not text:
        return []

    years: List[int] = []

    if re.fullmatch(r"\d{1,4}", text):
        try:
            value = int(text)
        except Exception:
            value = -1
        if 2000 <= value <= 2099:
            years.append(value)
        elif 0 <= value <= 99:
            years.append(2000 + value)

    for match in _YEAR_4_RE.finditer(text):
        year = int(match.group(1))
        if 2000 <= year <= 2099 and year not in years:
            years.append(year)

    for match in _YEAR_2_RE.finditer(text):
        year = 2000 + int(match.group(1))
        if 2000 <= year <= 2099 and year not in years:
            years.append(year)

    return years


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
    """Normalize report type to budget/final/unknown."""
    text = (doc_type or "").strip()
    text_lower = text.lower()
    name_lower = (filename or "").lower()
    filename_text = filename or ""

    if (
        "budget" in text_lower
        or "预算" in text
        or "budget" in name_lower
        or "预算" in filename_text
    ):
        return "budget"
    if (
        "final" in text_lower
        or "settlement" in text_lower
        or "accounts" in text_lower
        or "决算" in text
        or "final" in name_lower
        or "settlement" in name_lower
        or "决算" in filename_text
    ):
        return "final"
    return "unknown"


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


def _normalize_cover_line(raw: Any) -> str:
    return re.sub(r"\s+", "", str(raw or "").strip())


def _detect_cover_scope_hint(text: str) -> Optional[str]:
    compact = _normalize_cover_line(text)
    if not compact:
        return None
    if "\u4e3b\u7ba1\u90e8\u95e8" in compact or "\u90e8\u95e8\u9884\u7b97" in compact or "\u90e8\u95e8\u51b3\u7b97" in compact:
        return "department"
    if "\u9884\u7b97\u5355\u4f4d" in compact or "\u51b3\u7b97\u5355\u4f4d" in compact:
        return "unit"
    if "\u5355\u4f4d\u9884\u7b97" in compact or "\u5355\u4f4d\u51b3\u7b97" in compact or "\u672c\u7ea7" in compact:
        return "unit"
    if "\u90e8\u95e8" in compact:
        return "department"
    if "\u5355\u4f4d" in compact:
        return "unit"
    return None


def _detect_cover_report_kind(text: str) -> Optional[str]:
    compact = _normalize_cover_line(text)
    if not compact:
        return None
    if "\u9884\u7b97" in compact or "budget" in compact.lower():
        return "budget"
    if "\u51b3\u7b97" in compact or "final" in compact.lower():
        return "final"
    return None


def extract_cover_metadata(
    *,
    page_texts: Optional[List[str]] = None,
    filename: str = "",
    preferred_year: Any = None,
    doc_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract lightweight cover metadata from the first page."""
    normalized_pages = [str(text or "").strip() for text in (page_texts or [])]
    first_page_text = normalized_pages[0] if normalized_pages else ""
    lines = [line.strip() for line in first_page_text.splitlines() if line.strip()]

    cover_title = ""
    for line in lines[:20]:
        compact = _normalize_cover_line(line)
        if not compact:
            continue
        if (
            ("\u9884\u7b97" in compact or "\u51b3\u7b97" in compact)
            and ("\u90e8\u95e8" in compact or "\u5355\u4f4d" in compact)
        ):
            cover_title = line.strip()
            break

    if not cover_title:
        for line in lines[:20]:
            compact = _normalize_cover_line(line)
            if "\u9884\u7b97" in compact or "\u51b3\u7b97" in compact:
                cover_title = line.strip()
                break

    cover_org_name = ""
    cover_org_label = ""
    scope_hint: Optional[str] = None
    report_kind: Optional[str] = None

    for line in lines[:40]:
        compact = _normalize_cover_line(line)
        if not compact:
            continue
        for label, label_scope, label_kind in _COVER_ORG_LABELS:
            if label not in compact:
                continue
            _, _, remainder = compact.partition(label)
            remainder = re.sub(r"^[\uff1a:]+", "", remainder).strip()
            if not remainder:
                continue
            cover_org_name = remainder
            cover_org_label = label
            scope_hint = label_scope
            report_kind = label_kind
            break
        if cover_org_name:
            break

    scope_hint = scope_hint or _detect_cover_scope_hint(cover_title)
    report_kind = report_kind or _detect_cover_report_kind(cover_title)

    fallback_kind = normalize_report_kind(doc_type, filename)
    if not report_kind and fallback_kind != "unknown":
        report_kind = fallback_kind

    report_year = infer_report_year(
        filename=filename,
        page_texts=normalized_pages,
        preferred_year=preferred_year,
    )

    return {
        "cover_title": cover_title,
        "cover_org_name": cover_org_name,
        "cover_org_label": cover_org_label,
        "scope_hint": scope_hint or "",
        "report_kind": report_kind or "unknown",
        "report_year": report_year,
        "doc_type": normalize_doc_type(doc_type, filename, report_kind=report_kind),
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


def plan_structured_ingest_cleanup(
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Preview which historical structured-ingest versions can be cleaned safely."""
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
    """Delete historical structured-ingest document versions while keeping local job history."""
    request_body = dict(body or {})
    dry_run = bool(request_body.get("dry_run", False))
    plan = plan_structured_ingest_cleanup(request_body)
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
            "updated_job_count": 0,
            "updated_job_ids": [],
        }

    try:
        from src.db.connection import DatabaseConnection
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"structured ingest database unavailable: {exc}") from exc

    conn = None
    deleted_version_ids: List[int] = []
    try:
        conn = await DatabaseConnection.acquire()
        async with conn.transaction():
            for item in cleanup_versions:
                document_version_id = _coerce_int(item.get("document_version_id"))
                if document_version_id is None:
                    continue
                result = await conn.execute(
                    "DELETE FROM fiscal_document_versions WHERE id = $1",
                    document_version_id,
                )
                if str(result).strip().endswith("1"):
                    deleted_version_ids.append(document_version_id)
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

    return {
        **plan,
        "status": "done",
        "dry_run": False,
        "deleted_document_version_count": len(deleted_version_ids),
        "deleted_document_version_ids": deleted_version_ids,
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

    try:
        stat = status_file.stat()
        status_mtime_ns = stat.st_mtime_ns
        status_size = stat.st_size
    except Exception:
        pass

    pdf_path: Optional[Path] = None
    try:
        pdfs = sorted(job_dir.glob("*.pdf"))
        if pdfs:
            pdf_path = pdfs[0]
            filename = pdf_path.name
            pdf_stat = pdf_path.stat()
            pdf_mtime_ns = pdf_stat.st_mtime_ns
            pdf_size = pdf_stat.st_size
    except Exception:
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

    if filename_year is not None:
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

    def _severity_bucket(severity: Any) -> str:
        value = str(severity or "").lower()
        if value in {"critical", "high", "error", "fatal"}:
            return "error"
        if value in {"warn", "warning", "medium", "low"}:
            return "warn"
        return "info"

    def _summarize_finding_list(items: Any) -> tuple[int, int, int, int]:
        if not isinstance(items, list):
            return (0, 0, 0, 0)
        total = len(items)
        err = 0
        wrn = 0
        inf = 0
        for item in items:
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

            if isinstance(err, list):
                issue_error = len(err)
            if isinstance(wrn, list):
                issue_warn = len(wrn)
            if isinstance(inf, list):
                issue_info = len(inf)

            if isinstance(all_items, list):
                issue_total = len(all_items)
                issue_items = [item for item in all_items if isinstance(item, dict)]
            else:
                issue_total = issue_error + issue_warn + issue_info
                issue_items = [
                    item
                    for bucket in (err, wrn, inf)
                    if isinstance(bucket, list)
                    for item in bucket
                    if isinstance(item, dict)
                ]
        elif isinstance(issues, list):
            issue_total = len(issues)
            for item in issues:
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
                issue_total = len(rule_findings)
                issue_items = [item for item in rule_findings if isinstance(item, dict)]
                for item in rule_findings:
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
        "report_year": report_year,
        "doc_type": doc_type or None,
        "report_kind": report_kind,
        "issue_total": issue_total,
        "issue_error": issue_error,
        "issue_warn": issue_warn,
        "issue_info": issue_info,
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
        "organization_id": status_data.get("organization_id"),
        "organization_name": status_data.get("organization_name"),
        "organization_match_type": status_data.get("organization_match_type"),
        "organization_match_confidence": status_data.get("organization_match_confidence"),
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
    return [p for p in UPLOAD_ROOT.iterdir() if p.is_dir()]


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
        shutil.rmtree(job_dir, ignore_errors=True)


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

    page_count = get_pdf_page_count(dst)
    if MAX_UPLOAD_PAGES > 0 and page_count > MAX_UPLOAD_PAGES:
        delete_uploaded_job(job_id)
        raise HTTPException(
            status_code=413,
            detail=f"PDF页数超过限制：{page_count} 页，当前上限为 {MAX_UPLOAD_PAGES} 页",
        )

    created_at = time.time()
    payload = {
        "id": job_id,
        "job_id": job_id,
        "filename": safe_name,
        "size": size,
        "page_count": page_count,
        "saved_path": str(dst.relative_to(UPLOAD_ROOT)),
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
        "checksum": sha256.hexdigest(),
        "version_created_at": created_at,
        "job_created_at": created_at,
        "ts": created_at,
    }
    if metadata:
        status_payload.update({key: value for key, value in metadata.items() if value is not None})
    write_json_file(job_dir / "status.json", status_payload)
    return payload


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
        shutil.rmtree(job_dir)
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
    use_local_rules = bool(body.get("use_local_rules", True))
    use_ai_assist = bool(body.get("use_ai_assist", True))
    mode = str(body.get("mode", "legacy"))
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
    report_year = parse_report_year(
        body.get("report_year") if body.get("report_year") is not None else fiscal_year
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

    if filename_year is not None:
        report_year = filename_year
    elif report_year is None:
        report_year = infer_report_year(
            filename=filename,
            preferred_year=fiscal_year,
        )
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
        "report_kind": report_kind,
        "ts": time.time(),
    }
    try:
        status_file.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
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
        target = source_job_dir / filename
        try:
            if target.exists():
                target.unlink()
        except Exception:
            logger.exception("Failed to clear stale reanalyze artifact %s", target)

    body = dict(body or {})
    if "use_local_rules" not in body:
        body["use_local_rules"] = bool(source_status.get("use_local_rules", True))
    if "use_ai_assist" not in body:
        body["use_ai_assist"] = bool(source_status.get("use_ai_assist", True))
    if "mode" not in body:
        body["mode"] = str(source_status.get("mode") or "legacy")
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
