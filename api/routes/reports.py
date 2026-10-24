"""Report download/export endpoints."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from api import runtime
from api.auth_utils import require_job_access, require_login
from src.utils.issue_display import build_issue_display

router = APIRouter()
logger = logging.getLogger(__name__)

_SEVERITY_COLORS: Dict[str, Tuple[float, float, float]] = {
    "critical": (0.82, 0.16, 0.12),
    "high": (0.87, 0.22, 0.18),
    "medium": (0.90, 0.52, 0.10),
    "low": (0.16, 0.45, 0.82),
    "info": (0.30, 0.36, 0.44),
}
_SEVERITY_LABELS: Dict[str, str] = {
    "critical": "严重",
    "high": "高",
    "medium": "中",
    "low": "低",
    "info": "提示",
    "error": "高",
    "warn": "中",
    "warning": "中",
    "manual_review": "待人工复核",
}
_DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_EXPORT_RENDERER_VERSION = "canonical-v3-manual-suggestions"
_ERROR_SEVERITIES = {"critical", "high", "error"}
_RISK_SEVERITIES = {"medium", "warn", "warning"}
_CSV_FIELDNAMES: List[str] = [
    "record_type",
    "issue_id",
    "规则编号",
    "标题",
    "摘要",
    "严重级别",
    "严重级别代码",
    "页码",
    "页码列表",
    "表",
    "章节",
    "行",
    "列",
    "字段",
    "编码",
    "科目",
    "定位",
    "明细",
    "证据",
    "坐标",
    "定位引用数量",
    "定位摘要",
    "定位引用",
    "命中文本",
    "建议",
    "原始消息",
    "rule_id",
    "title",
    "summary",
    "severity",
    "severity_label",
    "page",
    "pages",
    "table",
    "section",
    "row",
    "col",
    "field",
    "code",
    "subject",
    "location",
    "detail_lines",
    "evidence_text",
    "bbox",
    "location_level",
    "verification_status",
    "table_ref_count",
    "evidence_role_summary",
    "table_refs",
    "text_snippet",
    "suggestion",
    "message",
    "\u89c4\u5219\u540d\u79f0",
    "\u4e25\u91cd\u7ea7\u522b\u6587\u672c",
    "\u5b9a\u4f4d\u6587\u672c",
    "\u6807\u51c6\u540d\u79f0",
    "\u8bf4\u660e\u540d\u79f0",
    "\u7f16\u7801\u5c42\u7ea7",
    "\u5224\u5b9a\u57fa\u51c6",
    "rule_name",
    "severity_text",
    "location_text",
    "expected_name",
    "actual_name",
    "code_level",
    "source_of_truth",
    "关联检查项",
    "obligation_ids",
    "check_id",
    "check_status",
    "incomplete_reason",
    "formula",
    "tolerance",
    "issue_refs",
    "operand_fact_ids",
    "required_fact_ids",
    "evaluation_basis",
    "fact_migration_status",
    "analysis_revision",
    "check_relation_id",
    "fact_ids",
    "evidence_ids",
    "human_decision",
    "human_decision_note",
    "decision_revision",
    "decision_actor",
    "decision_inherited",
    "decision_identity_fingerprint",
    "review_report_sha256",
    "quality_status",
    "analysis_conclusion",
    "conclusion_scope",
    "manual_review_type",
    "conclusion_json",
]


def _review_report_sha256(status_payload: Dict[str, Any]) -> str:
    """Stable identity for the canonical ReviewReport rendered by every export."""
    report = status_payload.get("review_report")
    canonical = json.dumps(
        report if isinstance(report, dict) else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_bbox(raw: Any) -> Optional[List[float]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None

    out: List[float] = []
    for item in raw:
        try:
            out.append(round(float(item), 2))
        except Exception:
            return None

    if out[2] <= out[0] or out[3] <= out[1]:
        return None
    return out


def _to_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(float(value))
    except Exception:
        return None
    return parsed if parsed > 0 else None


def _collect_pages(*values: Any) -> List[int]:
    pages: List[int] = []
    seen = set()
    for value in values:
        if isinstance(value, list):
            iterable: Iterable[Any] = value
        else:
            iterable = [value]
        for item in iterable:
            page = _to_positive_int(item)
            if not page or page in seen:
                continue
            seen.add(page)
            pages.append(page)
    return pages


def _extract_structured_review_issues(status_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    structured = status_payload.get("structured_ingest")
    if not isinstance(structured, dict):
        result = status_payload.get("result")
        structured = result.get("structured_ingest") if isinstance(result, dict) else None
    if not isinstance(structured, dict):
        return []

    review_items = structured.get("review_items")
    if not isinstance(review_items, list):
        return []

    job_id = str(status_payload.get("job_id") or "job")
    issues: List[Dict[str, Any]] = []
    for index, item in enumerate(review_items, start=1):
        if not isinstance(item, dict):
            continue
        raw_page = _to_positive_int(item.get("page") or item.get("page_number"))
        table_code = str(item.get("table_code") or "").strip()
        item_type = str(item.get("type") or "review").strip() or "review"
        message = str(item.get("message") or "该结构化识别项需要人工复核。").strip()
        location: Dict[str, Any] = {}
        if raw_page:
            location["page"] = raw_page
        if table_code:
            location["table"] = table_code
        evidence: List[Dict[str, Any]] = [{"text": message, "text_snippet": message}]
        if raw_page:
            evidence[0]["page"] = raw_page
        issues.append(
            {
                "id": str(item.get("id") or f"{job_id}:structured-review:{item_type}:{table_code or 'document'}:{index}"),
                "source": "structured_ingest",
                "rule_id": f"STRUCTURED-{item_type.upper()}",
                "severity": str(item.get("severity") or "manual_review"),
                "title": f"结构化识别待复核：{table_code}" if table_code else "结构化识别结果待复核",
                "message": message,
                "location": location,
                "evidence": evidence,
                "suggestion": str(item.get("recommended_action") or "请核对原始 PDF 后确认。"),
            }
        )
    return issues


def _extract_issues(status_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    review_report = status_payload.get("review_report")
    if isinstance(review_report, dict) and isinstance(review_report.get("issues"), list):
        canonical = [item for item in review_report["issues"] if isinstance(item, dict)]
        tips = review_report.get("tips")
        if isinstance(tips, list):
            canonical.extend(item for item in tips if isinstance(item, dict))
        deduped: List[Dict[str, Any]] = []
        seen_ids = set()
        for item in canonical:
            item_id = str(item.get("id") or "").strip()
            if item_id and item_id in seen_ids:
                continue
            if item_id:
                seen_ids.add(item_id)
            deduped.append(item)
        return deduped
    result = status_payload.get("result")
    structured_issues = _extract_structured_review_issues(status_payload)
    if not isinstance(result, dict):
        return structured_issues

    def _dedupe(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in items:
            issue_id = str(item.get("id") or "").strip()
            if issue_id:
                if issue_id in seen:
                    continue
                seen.add(issue_id)
            deduped.append(item)
        return deduped

    legacy_issues = result.get("issues")
    issues: List[Dict[str, Any]] = []
    if isinstance(legacy_issues, dict):
        all_items = legacy_issues.get("all")
        if isinstance(all_items, list):
            issues.extend([item for item in all_items if isinstance(item, dict)])
    elif isinstance(legacy_issues, list):
        issues.extend([item for item in legacy_issues if isinstance(item, dict)])

    source_issues: List[Dict[str, Any]] = []
    for key in ("ai_findings", "rule_findings"):
        bucket = result.get(key)
        if isinstance(bucket, list):
            source_issues.extend([item for item in bucket if isinstance(item, dict)])

    deduped_source_issues = _dedupe(source_issues)
    merged = result.get("merged")
    merged_ids = (
        [
            str(item or "").strip()
            for item in merged.get("merged_ids", [])
            if str(item or "").strip()
        ]
        if isinstance(merged, dict) and isinstance(merged.get("merged_ids"), list)
        else []
    )
    if merged_ids and deduped_source_issues:
        issue_by_id = {
            str(item.get("id") or "").strip(): item
            for item in deduped_source_issues
            if str(item.get("id") or "").strip()
        }
        merged_issues: List[Dict[str, Any]] = []
        seen_merged = set()
        for merged_id in merged_ids:
            if merged_id in seen_merged:
                continue
            issue = issue_by_id.get(merged_id)
            if issue is None:
                continue
            seen_merged.add(merged_id)
            merged_issues.append(issue)
        if merged_issues:
            return _dedupe([*merged_issues, *structured_issues])

    deduped_issues = _dedupe(issues)
    if deduped_issues:
        return _dedupe([*deduped_issues, *structured_issues])

    return _dedupe([*deduped_source_issues, *structured_issues])


def _normalize_table_refs(raw_refs: Any) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    if not isinstance(raw_refs, list):
        return refs

    for item in raw_refs:
        if not isinstance(item, dict):
            continue
        ref = {
            "role": str(item.get("role") or "").strip(),
            "page": _to_positive_int(item.get("page")),
            "table": str(item.get("table") or "").strip(),
            "section": str(item.get("section") or "").strip(),
            "row": str(item.get("row") or "").strip(),
            "col": str(item.get("col") or "").strip(),
            "field": str(item.get("field") or "").strip(),
            "code": str(item.get("code") or "").strip(),
            "subject": str(item.get("subject") or "").strip(),
            "value": item.get("current_value", item.get("value")),
            "relation_id": str(item.get("relation_id") or "").strip(),
            "operand_id": str(item.get("operand_id") or "").strip(),
            "bbox": (
                _normalize_bbox(item.get("bbox"))
                if _has_verified_exact_location(item)
                else None
            ),
            "location_level": str(item.get("location_level") or "unresolved"),
            "verification_status": str(item.get("verification_status") or "unverified"),
        }
        refs.append({key: value for key, value in ref.items() if value not in (None, "", [])})
    return refs


def _resolve_text_snippet(issue: Dict[str, Any]) -> str:
    evidence = issue.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            snippet = str(item.get("text_snippet") or item.get("text") or "").strip()
            if snippet:
                return snippet
    return str(issue.get("text_snippet") or "").strip()


def _evidence_excerpt_label(issue: Dict[str, Any]) -> str:
    verification = issue.get("evidence_verification")
    if (
        issue.get("evidence_status") in {"complete", "verified"}
        and isinstance(verification, dict)
        and verification.get("status") == "verified"
        and verification.get("source_identity_complete") is True
    ):
        return "原文摘录"
    return "候选证据/计算说明（原文绑定未完成）"


def _resolve_suggestion(issue: Dict[str, Any]) -> str:
    suggestion = issue.get("suggestion")
    if isinstance(suggestion, str):
        return suggestion.strip()
    suggestions = issue.get("suggestions")
    if isinstance(suggestions, list):
        return " | ".join(str(item).strip() for item in suggestions if str(item).strip())
    return ""


def _source_filename(status_payload: Dict[str, Any], job_id: str) -> str:
    for key in ("filename", "original_filename"):
        value = status_payload.get(key)
        if isinstance(value, str) and value.strip():
            return Path(value.strip()).name

    saved_path = status_payload.get("saved_path")
    if isinstance(saved_path, str) and saved_path.strip():
        return Path(saved_path.strip()).name

    result = status_payload.get("result")
    if isinstance(result, dict):
        for key in ("filename", "original_filename", "saved_path"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return Path(value.strip()).name
        meta = result.get("meta")
        if isinstance(meta, dict):
            for key in ("filename", "original_filename", "saved_path"):
                value = meta.get(key)
                if isinstance(value, str) and value.strip():
                    return Path(value.strip()).name

    return f"{job_id}.pdf"


def _severity_label(severity: Any) -> str:
    return _SEVERITY_LABELS.get(str(severity or "").strip().lower(), "提示")


def _coalesce_issue_text(issue: Dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = issue.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _build_export_location(issue: Dict[str, Any]) -> Dict[str, Any]:
    location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), list) else []
    refs = _normalize_table_refs(location.get("table_refs"))
    pages = _collect_pages(
        location.get("page"),
        location.get("pages") if isinstance(location.get("pages"), list) else [],
        [item.get("page") for item in refs],
        [item.get("page") for item in evidence if isinstance(item, dict)],
        issue.get("page_number"),
    )

    # AC-12：主页与 bbox 必须同源——bbox 只能取自"恰好位于主页"的来源。
    # 此前 issue 主页（P1）可以配到第 2 页证据的 bbox，导出摘要自相矛盾。
    primary_page = (
        _to_positive_int(location.get("page"))
        or _to_positive_int(issue.get("page_number"))
        or (_to_positive_int(refs[0].get("page")) if refs else None)
        or (
            _to_positive_int(evidence[0].get("page"))
            if evidence and isinstance(evidence[0], dict)
            else None
        )
        or (pages[0] if pages else None)
    )

    def _bbox_on_page(source: Dict[str, Any]) -> Optional[List[float]]:
        if not _has_verified_exact_location(source):
            return None
        bbox = _normalize_bbox(source.get("bbox"))
        if bbox is None:
            return None
        page = _to_positive_int(source.get("page"))
        if page is not None and primary_page is not None and page != primary_page:
            return None
        return bbox

    primary_bbox = _bbox_on_page(issue)
    if primary_bbox is None:
        primary_bbox = next((_bbox_on_page(ref) for ref in refs if _bbox_on_page(ref)), None)
    if primary_bbox is None:
        primary_bbox = next(
            (_bbox_on_page(item) for item in evidence if isinstance(item, dict) and _bbox_on_page(item)),
            None,
        )

    evidence_ids = [
        str(item.get("evidence_id"))
        for item in evidence
        if isinstance(item, dict) and str(item.get("evidence_id") or "").strip()
    ]

    export_location = {
        "page": primary_page if primary_page is not None else (pages[0] if pages else None),
        "pages": pages,
        "table": str(location.get("table") or "").strip() or None,
        "section": str(location.get("section") or "").strip() or None,
        "row": str(location.get("row") or "").strip() or None,
        "col": str(location.get("col") or "").strip() or None,
        "field": str(location.get("field") or "").strip() or None,
        "code": str(location.get("code") or "").strip() or None,
        "subject": str(location.get("subject") or "").strip() or None,
        "expected_name": str(location.get("expected_name") or "").strip() or None,
        "actual_name": str(location.get("actual_name") or "").strip() or None,
        "code_level": str(location.get("code_level") or "").strip() or None,
        "source_of_truth": str(location.get("source_of_truth") or "").strip() or None,
        "bbox": primary_bbox,
        "location_level": str(
            issue.get("location_level") or location.get("location_level") or "unresolved"
        ),
        "verification_status": str(
            issue.get("verification_status") or location.get("verification_status") or "unverified"
        ),
        "table_ref_count": len(refs),
        "role_summary": _build_role_summary(refs, fallback=location),
        "table_refs": refs,
        # AC-12：定位对象携带证据身份，页码/bbox/等级/证据ID 同对象输出
        "evidence_ids": evidence_ids,
    }

    return {key: value for key, value in export_location.items() if value not in (None, "", [])}


def _has_verified_exact_location(source: Dict[str, Any]) -> bool:
    return (
        str(source.get("location_level") or "") in {"exact_cell", "exact_span"}
        and str(source.get("verification_status") or "") == "verified"
    )


def _build_role_summary(
    refs: Sequence[Dict[str, Any]],
    *,
    fallback: Optional[Dict[str, Any]] = None,
) -> str:
    parts: List[str] = []
    for ref in refs:
        role = str(ref.get("role") or "定位").strip()
        page = _to_positive_int(ref.get("page"))
        segments = [role]
        if page:
            segments.append(f"P{page}")
        for key, label in (
            ("table", "表"),
            ("section", "章节"),
            ("row", "行"),
            ("field", "字段"),
            ("code", "编码"),
            ("subject", "科目"),
        ):
            value = str(ref.get(key) or "").strip()
            if value:
                segments.append(f"{label}:{value}")
                break
        parts.append("/".join(segments))

    if parts:
        return " ; ".join(parts)

    if isinstance(fallback, dict):
        role = "定位"
        page = _to_positive_int(fallback.get("page"))
        segments = [role]
        if page:
            segments.append(f"P{page}")
        for key, label in (
            ("table", "表"),
            ("section", "章节"),
            ("row", "行"),
            ("field", "字段"),
        ):
            value = str(fallback.get(key) or "").strip()
            if value:
                segments.append(f"{label}:{value}")
                break
        return "/".join(segments)

    return ""


def _format_obligation_ids(issue: Dict[str, Any]) -> str:
    """把义务编号格式化成单元格文本。

    旧结果没有该字段时返回空串（不是 "0"、不是 "无"）：空串表示"未记录"，
    不会让阅读者以为"这条问题不属于任何检查项"。
    """
    raw = issue.get("obligation_ids")
    if not isinstance(raw, list):
        return ""
    return "; ".join(str(item) for item in raw if str(item).strip())


def _build_check_scope_export(status_payload: Dict[str, Any]) -> Dict[str, Any]:
    """导出用的检查范围摘要：四类内容与检查覆盖的身份信息。

    plan §6 要求页面、JSON、CSV、PDF 导出统一展示：已确认问题、待人工复核、
    未完成检查、舍入与格式建议。导出侧至少必须把"检查范围"和"未完成清单"
    交出去——只给问题列表会让接收方以为没列出来的部分都查过了。

    旧任务（本次整改前的结果）没有 ``check_coverage`` / ``document_profile``，
    这里明确写成 ``"旧版未记录"``，而不是补一个空的"已完成"。
    """
    review_report = status_payload.get("review_report")
    if isinstance(review_report, dict):
        conclusion = review_report.get("conclusion")
        conclusion = conclusion if isinstance(conclusion, dict) else {}
        coverage = review_report.get("obligation_coverage")
        metadata = review_report.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        manual_review = review_report.get("manual_review")
        manual_review = manual_review if isinstance(manual_review, list) else []
        unfinished = [
            item for item in manual_review
            if isinstance(item, dict) and item.get("record_type") == "unfinished_check"
        ]
        return {
            "conclusion_scope": conclusion.get("conclusion_scope") or "旧版未记录",
            "profile_status": metadata.get("profile_status") or "旧版未记录",
            "report_kind": metadata.get("report_kind") or "旧版未记录",
            "check_coverage": coverage if isinstance(coverage, dict) else "旧版未记录",
            "unfinished_checks": unfinished if isinstance(coverage, dict) else "旧版未记录",
            "document_profile": metadata,
            "coverage_note": (
                "本次结果为旧版记录，未包含检查覆盖台账，无法据此判断检查范围是否完整"
                if not isinstance(coverage, dict)
                else "“未发现问题”仅表示在本次已完成的检查范围内未发现问题"
            ),
        }

    result = status_payload.get("result")
    result = result if isinstance(result, dict) else {}
    meta = result.get("meta")
    meta = meta if isinstance(meta, dict) else {}

    coverage = status_payload.get("check_coverage")
    if not isinstance(coverage, dict):
        coverage = meta.get("obligation_coverage")
    profile = status_payload.get("document_profile")
    if not isinstance(profile, dict):
        profile = meta.get("document_profile")

    legacy = coverage is None or not coverage
    return {
        "conclusion_scope": status_payload.get("conclusion_scope") or "旧版未记录",
        "profile_status": status_payload.get("profile_status") or "旧版未记录",
        "report_kind": status_payload.get("report_kind") or "旧版未记录",
        "check_coverage": coverage if isinstance(coverage, dict) else "旧版未记录",
        "unfinished_checks": (
            [
                {
                    "obligation_id": item.get("obligation_id"),
                    "group_title": item.get("group_title"),
                    "title": item.get("title"),
                    "status": item.get("status"),
                    "reason_label": item.get("reason_label"),
                    "detail": item.get("detail"),
                    "basis": item.get("basis"),
                    "gap_note": item.get("gap_note"),
                }
                for item in coverage.get("instances", [])
                if isinstance(item, dict)
                and str(item.get("status") or "")
                not in {"completed", "not_applicable"}
            ]
            if isinstance(coverage, dict)
            else "旧版未记录"
        ),
        "document_profile": profile if isinstance(profile, dict) else "旧版未记录",
        "coverage_note": (
            "本次结果为旧版记录，未包含检查覆盖台账，"
            "无法据此判断检查范围是否完整"
            if legacy
            else "“未发现问题”仅表示在本次已完成的检查范围内未发现问题"
        ),
    }


def _build_export_conclusion(status_payload: Dict[str, Any], issue_count: int) -> Dict[str, Any]:
    """推导导出用的检查结论措辞（ZC-00 / SYS-12）。

    "问题列表为空"必须区分两种情况：
    - 检查确实完整完成且未发现问题 → 允许"已完成检查范围内未发现问题"；
    - 存在未完成检查（或旧版记录无法证明完整性）→ 必须写明结论受限，
      不得输出"未发现明显问题"。
    """
    scope = _build_check_scope_export(status_payload)
    unfinished = scope.get("unfinished_checks")
    # AC-12：结论单一来源——优先读选定版本 canonical ReviewReport 的结论；
    # 顶层 analysis_conclusion 只在没有 canonical 结论时兜底，禁止两个来源
    # 并存时互相矛盾（canonical=incomplete 被顶层 no_findings 覆盖的反例）。
    review_report = (
        status_payload.get("review_report")
        if isinstance(status_payload.get("review_report"), dict)
        else {}
    )
    canonical_conclusion = ""
    canonical_metadata = (
        review_report.get("metadata")
        if isinstance(review_report.get("metadata"), dict)
        else {}
    )
    for source in (
        review_report.get("conclusion"),
        canonical_metadata,
    ):
        if isinstance(source, dict):
            canonical_conclusion = str(source.get("analysis_conclusion") or "").strip()
            if canonical_conclusion:
                break
    analysis_conclusion = canonical_conclusion or str(
        status_payload.get("analysis_conclusion") or ""
    ).strip()

    unfinished_items: List[Dict[str, Any]] = []
    coverage_known: Optional[bool] = None
    if isinstance(unfinished, list):
        unfinished_items = unfinished
        coverage_known = True
    else:
        # "旧版未记录"：没有检查覆盖台账，无法证明检查是否完整
        coverage_known = False

    is_complete: Optional[bool]
    if coverage_known is False or analysis_conclusion == "incomplete":
        is_complete = False
    elif coverage_known is True and unfinished_items:
        is_complete = False
    elif analysis_conclusion == "no_findings" and coverage_known is True and not unfinished_items:
        is_complete = True
    elif analysis_conclusion == "findings_detected":
        # 已检出问题的任务，问题列表非空；范围说明照常给出
        is_complete = not unfinished_items if coverage_known else False
    else:
        # 结论字段缺失且无覆盖台账：按"无法确认完整"处理
        is_complete = None if coverage_known is None else False

    return {
        "scope": scope,
        "unfinished_items": unfinished_items,
        "coverage_known": coverage_known,
        "is_complete": is_complete,
        "conclusion_scope": scope.get("conclusion_scope") or "旧版未记录",
    }


def _zero_issue_wording(conclusion: Dict[str, Any]) -> str:
    """零问题结论措辞：完整完成才允许有限范围的无问题结论。"""
    if conclusion["is_complete"] is True:
        return "当前已完成的检查范围内未发现问题。"
    if conclusion["coverage_known"] is False:
        return (
            "问题列表为空。本次结果为旧版记录，未包含检查覆盖台账，"
            "无法据此判断检查范围是否完整；问题列表为空不代表材料没有问题。"
        )
    unfinished_items = conclusion["unfinished_items"]
    return (
        f"问题列表为空，但本次检查未完成（{len(unfinished_items)} 项检查未完成），"
        "不能据此给出完整结论。请结合未完成事项人工复核后再下结论。"
    )


def _enrich_issue(item: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(item)
    if not isinstance(enriched.get("display"), dict):
        enriched["display"] = build_issue_display(enriched)
    enriched["export_location"] = _build_export_location(enriched)
    enriched["severity_label"] = _severity_label(enriched.get("severity"))
    export_location = (
        enriched.get("export_location")
        if isinstance(enriched.get("export_location"), dict)
        else {}
    )
    display = enriched.get("display") if isinstance(enriched.get("display"), dict) else {}
    enriched["rule_name"] = _coalesce_issue_text(enriched, "rule_name", "title") or ""
    enriched["severity_text"] = enriched["severity_label"]
    enriched["location_text"] = str(display.get("location_text") or "").strip()
    for key in ("expected_name", "actual_name", "code_level", "source_of_truth"):
        enriched[key] = _coalesce_issue_text(enriched, key) or str(export_location.get(key) or "").strip()
    return enriched


def _manual_finding_details(item: Dict[str, Any]) -> List[tuple[str, str]]:
    """待核实发现仍导出完整证据与定位，同时保留候选证据标签。"""
    finding = item.get("finding")
    if not isinstance(finding, dict):
        return []
    enriched = _enrich_issue(finding)
    display = enriched.get("display") or {}
    return [
        ("规则编号", str(finding.get("rule_id") or "")),
        ("定位", str(enriched.get("location_text") or "当前无法定位")),
        (_evidence_excerpt_label(finding), _resolve_text_snippet(finding)),
        ("逐项定位", "\n".join(str(line) for line in display.get("detail_lines") or [])),
        ("建议处理方式", _resolve_suggestion(finding) or _resolve_suggestion(item)),
    ]


def _build_csv_row(issue: Dict[str, Any]) -> Dict[str, Any]:
    enriched = _enrich_issue(issue)
    display = enriched.get("display") if isinstance(enriched.get("display"), dict) else {}
    export_location = (
        enriched.get("export_location")
        if isinstance(enriched.get("export_location"), dict)
        else {}
    )
    severity = str(enriched.get("severity") or "").strip()
    severity_label = str(enriched.get("severity_label") or _severity_label(severity))
    pages_text = " | ".join(str(item) for item in export_location.get("pages", []))
    detail_lines = " | ".join(
        [str(line) for line in display.get("detail_lines", []) if str(line).strip()]
    )
    bbox_text = (
        json.dumps(export_location.get("bbox"), ensure_ascii=False)
        if export_location.get("bbox")
        else ""
    )
    table_refs_text = json.dumps(export_location.get("table_refs", []), ensure_ascii=False)
    role_summary = export_location.get("role_summary") or ""
    suggestion = _resolve_suggestion(enriched)
    human_decision = enriched.get("human_decision") if isinstance(enriched.get("human_decision"), dict) else {}
    row = {
        # record_type（ZC-00）：区分问题行与摘要行；ZC-04 将扩展为
        # 摘要/问题/提示/待复核/检查五类完整记录
        "record_type": "issue",
        "issue_id": enriched.get("id") or "",
        "规则编号": enriched.get("rule_id") or enriched.get("rule") or "",
        "标题": enriched.get("title") or "",
        "摘要": display.get("summary") or enriched.get("title") or "",
        "严重级别": severity_label,
        "严重级别代码": severity,
        "页码": export_location.get("page") or "",
        "页码列表": pages_text,
        "表": export_location.get("table") or "",
        "章节": export_location.get("section") or "",
        "行": export_location.get("row") or "",
        "列": export_location.get("col") or "",
        "字段": export_location.get("field") or "",
        "编码": export_location.get("code") or "",
        "科目": export_location.get("subject") or "",
        "定位": display.get("location_text") or "",
        "明细": detail_lines,
        "证据": display.get("evidence_text") or "",
        "坐标": bbox_text,
        "定位引用数量": export_location.get("table_ref_count") or 0,
        "定位摘要": role_summary,
        "定位引用": table_refs_text,
        "命中文本": _resolve_text_snippet(enriched),
        "建议": suggestion,
        "原始消息": enriched.get("message") or "",
        "rule_id": enriched.get("rule_id") or enriched.get("rule") or "",
        "title": enriched.get("title") or "",
        "summary": display.get("summary") or enriched.get("title") or "",
        "severity": severity,
        "severity_label": severity_label,
        "page": export_location.get("page") or "",
        "pages": pages_text,
        "table": export_location.get("table") or "",
        "section": export_location.get("section") or "",
        "row": export_location.get("row") or "",
        "col": export_location.get("col") or "",
        "field": export_location.get("field") or "",
        "code": export_location.get("code") or "",
        "subject": export_location.get("subject") or "",
        "location": display.get("location_text") or "",
        "detail_lines": detail_lines,
        "evidence_text": display.get("evidence_text") or "",
        "bbox": bbox_text,
        "location_level": export_location.get("location_level") or "unresolved",
        "verification_status": export_location.get("verification_status") or "unverified",
        "table_ref_count": export_location.get("table_ref_count") or 0,
        "evidence_role_summary": role_summary,
        "table_refs": table_refs_text,
        "text_snippet": _resolve_text_snippet(enriched),
        "suggestion": suggestion,
        "message": enriched.get("message") or "",
        "\u89c4\u5219\u540d\u79f0": enriched.get("rule_name") or "",
        "\u4e25\u91cd\u7ea7\u522b\u6587\u672c": enriched.get("severity_text") or severity_label,
        "\u5b9a\u4f4d\u6587\u672c": enriched.get("location_text") or display.get("location_text") or "",
        "\u6807\u51c6\u540d\u79f0": enriched.get("expected_name") or export_location.get("expected_name") or "",
        "\u8bf4\u660e\u540d\u79f0": enriched.get("actual_name") or export_location.get("actual_name") or "",
        "\u7f16\u7801\u5c42\u7ea7": enriched.get("code_level") or export_location.get("code_level") or "",
        "\u5224\u5b9a\u57fa\u51c6": enriched.get("source_of_truth") or export_location.get("source_of_truth") or "",
        "rule_name": enriched.get("rule_name") or "",
        "severity_text": enriched.get("severity_text") or severity_label,
        "location_text": enriched.get("location_text") or display.get("location_text") or "",
        "expected_name": enriched.get("expected_name") or export_location.get("expected_name") or "",
        "actual_name": enriched.get("actual_name") or export_location.get("actual_name") or "",
        "code_level": enriched.get("code_level") or export_location.get("code_level") or "",
        "source_of_truth": enriched.get("source_of_truth") or export_location.get("source_of_truth") or "",
        # 关联检查项：把问题挂回"应检查事项"，让线下沟通能直接说清
        # "这条问题属于哪一项检查"，而不必让接收方按规则编号反查。
        "关联检查项": _format_obligation_ids(enriched),
        "obligation_ids": _format_obligation_ids(enriched),
        "analysis_revision": human_decision.get("analysis_revision") or enriched.get("analysis_revision") or "",
        "check_relation_id": enriched.get("check_relation_id") or "",
        "fact_ids": json.dumps(enriched.get("fact_ids") or [], ensure_ascii=False),
        "evidence_ids": json.dumps(enriched.get("evidence_ids") or [], ensure_ascii=False),
        "human_decision": human_decision.get("status") or "",
        "human_decision_note": human_decision.get("note") or "",
        "decision_revision": human_decision.get("decision_revision") or "",
        "decision_actor": human_decision.get("actor") or "",
        "decision_inherited": human_decision.get("inherited", ""),
        "decision_identity_fingerprint": human_decision.get("identity_fingerprint") or "",
    }
    return {key: row.get(key, "") for key in _CSV_FIELDNAMES}


def _build_csv_summary_row(
    job_id: str,
    status_payload: Dict[str, Any],
    issue_count: int,
) -> Dict[str, Any]:
    """零问题时输出一条摘要记录（ZC-00 / SYS-12）。

    CSV 是纯行式结构，没有正文可写说明文字；若零问题时不输出任何行，
    接收方会把空表格读成"材料没有问题"。摘要行把结论限制写进
    标题/摘要/建议三个语义最稳的列，其余列留空。
    """
    conclusion = _build_export_conclusion(status_payload, issue_count)
    wording = _zero_issue_wording(conclusion) if issue_count == 0 else ""
    suggestion_text = (
        "请同时核对检查范围与未完成事项，再决定是否采信本清单。"
        if conclusion["is_complete"] is not True
        else "本次已完成检查范围内未发现问题。"
    )
    row: Dict[str, Any] = {key: "" for key in _CSV_FIELDNAMES}
    review_report = status_payload.get("review_report")
    review_conclusion = review_report.get("conclusion") if isinstance(review_report, dict) else None
    row.update(
        {
            "record_type": "summary",
            "规则编号": "SUMMARY",
            "标题": wording or f"共导出 {issue_count} 条问题",
            "摘要": wording or f"共导出 {issue_count} 条问题",
            "建议": suggestion_text,
            "title": wording or f"共导出 {issue_count} 条问题",
            "summary": wording or f"共导出 {issue_count} 条问题",
            "suggestion": suggestion_text,
            "message": wording or "",
            "analysis_revision": status_payload.get("analysis_revision") or "",
            "review_report_sha256": _review_report_sha256(status_payload),
            "quality_status": status_payload.get("quality_status") or "",
            "analysis_conclusion": status_payload.get("analysis_conclusion") or "",
            "conclusion_scope": status_payload.get("conclusion_scope") or "",
            "conclusion_json": json.dumps(review_conclusion or {}, ensure_ascii=False, sort_keys=True),
        }
    )
    return {key: row.get(key, "") for key in _CSV_FIELDNAMES}


def _xml_escape(value: Any) -> str:
    text = str(value if value is not None else "")
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _docx_text(value: Any, *, limit: int = 1000) -> str:
    text = str(value if value is not None else "").strip()
    text = " ".join(text.split())
    if len(text) > limit:
        return f"{text[:limit - 3]}..."
    return text


def _docx_paragraph(text: Any, *, style: Optional[str] = None) -> str:
    style_xml = f'<w:pStyle w:val="{style}"/>' if style else ""
    safe_text = _xml_escape(_docx_text(text, limit=3000))
    return (
        "<w:p>"
        f"<w:pPr>{style_xml}</w:pPr>"
        f'<w:r><w:t xml:space="preserve">{safe_text}</w:t></w:r>'
        "</w:p>"
    )


def _docx_table_cell(text: Any) -> str:
    # Canonical export checks compare complete record fields across JSON/CSV/
    # DOCX/PDF. A short per-cell cap silently erased long manual-review reasons
    # and made a real DOCX differ from the ReviewReport it claimed to represent.
    safe_text = _xml_escape(_docx_text(text, limit=250_000))
    return f'<w:tc><w:p><w:r><w:t xml:space="preserve">{safe_text}</w:t></w:r></w:p></w:tc>'


def _docx_table(rows: Sequence[Sequence[Any]]) -> str:
    row_xml = []
    for row in rows:
        cells = "".join(_docx_table_cell(cell) for cell in row)
        row_xml.append(f"<w:tr>{cells}</w:tr>")
    if rows and all(len(row) == 2 for row in rows):
        grid = '<w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="7026"/></w:tblGrid>'
        properties = '<w:tblW w:w="9026" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
    else:
        grid = ""
        properties = '<w:tblW w:w="0" w:type="auto"/>'
    return "<w:tbl><w:tblPr>" + properties + "</w:tblPr>" + grid + "".join(row_xml) + "</w:tbl>"


def _issue_bucket(issue: Dict[str, Any]) -> str:
    severity = str(issue.get("severity") or "").strip().lower()
    if severity in _ERROR_SEVERITIES:
        return "错误"
    if severity in _RISK_SEVERITIES:
        return "风险提示"
    return "规范性建议"


def _build_docx_document_xml(
    *,
    job_id: str,
    status_payload: Dict[str, Any],
    issues: Sequence[Dict[str, Any]],
) -> str:
    from src.schemas.review_report import normalize_manual_review

    enriched_issues = [_enrich_issue(item) for item in issues]
    error_count = sum(1 for item in enriched_issues if _issue_bucket(item) == "错误")
    risk_count = sum(1 for item in enriched_issues if _issue_bucket(item) == "风险提示")
    suggestion_count = sum(1 for item in enriched_issues if _issue_bucket(item) == "规范性建议")
    export_conclusion = _build_export_conclusion(status_payload, len(enriched_issues))
    conclusion_scope_text = str(export_conclusion["conclusion_scope"])
    scope = export_conclusion["scope"]
    body: List[str] = [
        _docx_paragraph("预决算公开材料审校报告", style="Title"),
        _docx_paragraph(f"REVIEW_REPORT_SHA256: {_review_report_sha256(status_payload)}"),
        _docx_paragraph("基本信息", style="Heading1"),
        _docx_table(
            [
                ("文件名称", _source_filename(status_payload, job_id)),
                ("分析版本", str(status_payload.get("analysis_revision") or "旧版未记录")),
                ("检查模式", str(status_payload.get("mode") or status_payload.get("check_mode") or "自动审校")),
                ("问题总数", str(len(enriched_issues))),
                ("错误数量", str(error_count)),
                ("风险提示数量", str(risk_count)),
                ("规范性建议数量", str(suggestion_count)),
            ]
        ),
        # ZC-00（SYS-12）：结论与检查范围同源输出，"没列出的问题"不能被读成"查过了"
        _docx_paragraph("检查结论与范围", style="Heading1"),
        _docx_table(
            [
                ("结论作用域", conclusion_scope_text),
                ("材料类型", str(scope.get("report_kind") or "旧版未记录")),
                ("检查范围说明", str(scope.get("coverage_note") or "")),
            ]
        ),
        _docx_paragraph("问题清单", style="Heading1"),
    ]

    if not enriched_issues:
        # ZC-00（SYS-12）：存在未完成检查（或旧版记录无法证明完整性）时，
        # 禁止输出"未发现明显问题"——空问题列表不等于材料没有问题。
        body.append(_docx_paragraph(_zero_issue_wording(export_conclusion)))
    else:
        issue_index = 1
        for bucket in ("错误", "风险提示", "规范性建议"):
            bucket_items = [item for item in enriched_issues if _issue_bucket(item) == bucket]
            if not bucket_items:
                continue
            body.append(_docx_paragraph(bucket, style="Heading2"))
            for issue in bucket_items:
                display = issue.get("display") if isinstance(issue.get("display"), dict) else {}
                export_location = (
                    issue.get("export_location")
                    if isinstance(issue.get("export_location"), dict)
                    else {}
                )
                rule_id = str(issue.get("rule_id") or issue.get("rule") or "").strip()
                title = str(issue.get("title") or display.get("summary") or issue.get("message") or "").strip()
                message = str(issue.get("message") or title).strip()
                page = export_location.get("page") or ""
                evidence = _resolve_text_snippet(issue) or str(display.get("evidence_text") or "").strip()
                suggestion = _resolve_suggestion(issue) or "请结合原文和表格数据复核，并按公开口径修正。"
                rows = [
                    ("问题编号", f"ISSUE_ID|{issue.get('id') or ''}|"),
                    ("序号", str(issue_index)),
                    ("问题等级", bucket),
                    ("规则编号", rule_id),
                    ("问题标题", title),
                    ("问题说明", message),
                    ("证据页码", page),
                    (
                        "定位等级/坐标",
                        f"{issue.get('location_level') or export_location.get('location_level') or 'unresolved'} / "
                        f"{issue.get('bbox') or export_location.get('bbox') or '当前无法定位'}",
                    ),
                    (
                        "关系与证据身份",
                        f"severity={issue.get('severity') or ''}; verification={issue.get('verification_status') or export_location.get('verification_status') or ''}; "
                        f"{issue.get('check_relation_id') or ''} / facts={issue.get('fact_ids') or []} / "
                        f"evidence={issue.get('evidence_ids') or []}",
                    ),
                    (_evidence_excerpt_label(issue), evidence),
                    ("建议处理方式", suggestion),
                ]
                decision = issue.get("human_decision")
                if isinstance(decision, dict):
                    rows.extend([
                        ("人工处理状态", str(decision.get("status") or "")),
                        ("人工复核说明", str(decision.get("note") or "")),
                    ])
                body.append(_docx_table(rows))
                issue_index += 1

    # 未完成检查清单：无论是否有问题，接收方都需要知道"哪些没查完"
    if export_conclusion["coverage_known"] is False:
        body.append(_docx_paragraph("检查覆盖台账", style="Heading2"))
        body.append(_docx_paragraph("旧版未记录：本次结果未包含检查覆盖台账，无法列出未完成事项。"))
    elif export_conclusion["unfinished_items"]:
        body.append(_docx_paragraph("未完成检查事项", style="Heading2"))
        for index, item in enumerate(export_conclusion["unfinished_items"], start=1):
            body.append(_docx_table([
                ("记录类型", "unfinished_check"),
                ("序号", str(index)),
                ("检查项", str(item.get("group_title") or item.get("title") or item.get("obligation_id") or "")),
                ("状态", str(item.get("status") or "")),
                ("原因", str(item.get("reason_label") or "")),
                ("说明", str(item.get("detail") or item.get("gap_note") or "")),
            ]))

    review_report = status_payload.get("review_report")
    if isinstance(review_report, dict):
        human_decisions = review_report.get("human_decisions")
        human_decisions = (
            [item for item in human_decisions if isinstance(item, dict)]
            if isinstance(human_decisions, list)
            else []
        )
        if human_decisions:
            body.append(_docx_paragraph("人工处理决定", style="Heading1"))
            for item in human_decisions:
                body.append(_docx_table([
                    ("问题编号", f"DECISION_ID|{item.get('issue_id') or ''}|"),
                    ("处理状态", str(item.get("status") or "")),
                    ("复核说明", str(item.get("note") or "")),
                    ("分析版本", str(item.get("analysis_revision") or "")),
                    ("决定版本", str(item.get("decision_revision") or "")),
                    ("复核人", str(item.get("actor") or "")),
                    ("跨版本继承", str(item.get("inherited", ""))),
                    ("事实/证据ID", f"facts={item.get('fact_ids') or []}; evidence={item.get('evidence_ids') or []}"),
                    ("身份指纹", str(item.get("identity_fingerprint") or "")),
                ]))
        manual_review = review_report.get("manual_review")
        manual_review = (
            [item for item in manual_review if isinstance(item, dict)]
            if isinstance(manual_review, list)
            else []
        )
        if manual_review:
            body.append(_docx_paragraph("待人工复核", style="Heading1"))
            for item in manual_review:
                body.append(_docx_table([
                    ("项目", f"MANUAL_ID|{item.get('id') or item.get('obligation_id') or item.get('code') or ''}| "
                     f"{normalize_manual_review(item)['display_title']}"),
                    ("类型", str(item.get("record_type") or "复核事项")),
                    ("状态", str(item.get("status") or "")),
                    ("原因/说明", str(item.get("message") or item.get("detail") or item.get("reason") or "")),
                    *_manual_finding_details(item),
                ]))
        receipts = review_report.get("check_receipts")
        receipts = (
            [item for item in receipts if isinstance(item, dict)]
            if isinstance(receipts, list)
            else []
        )
        if receipts:
            body.append(_docx_paragraph("检查回执", style="Heading1"))
            for receipt in receipts:
                body.append(_docx_table([
                    ("检查项", f"CHECK_ID|{receipt.get('rule_id') or ''}:{receipt.get('sub_check_id') or ''}|"),
                    ("规则", str(receipt.get("rule_id") or "")),
                    ("状态", str(receipt.get("status") or "")),
                    ("公式/依据", str(receipt.get("formula") or receipt.get("evaluation_basis") or "")),
                    ("操作数事实ID", str(receipt.get("operand_fact_ids") or receipt.get("required_fact_ids") or [])),
                    ("问题引用ID", str(receipt.get("issue_refs") or [])),
                    ("容差", str(receipt.get("tolerance") or {})),
                    ("未完成原因", str(receipt.get("incomplete_reason") or "")),
                ]))

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        + "".join(body)
        + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/></w:sectPr>'
        "</w:body></w:document>"
    )
    return document_xml


def _create_docx_report(
    job_id: str,
    status_payload: Dict[str, Any],
    issues: Sequence[Dict[str, Any]],
) -> bytes:
    document_xml = _build_docx_document_xml(
        job_id=job_id,
        status_payload=status_payload,
        issues=issues,
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/>'
        '<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="SimSun"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/><w:rFonts w:eastAsia="SimHei"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="24"/><w:rFonts w:eastAsia="SimHei"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/>'
        '<w:pPr><w:spacing w:before="160" w:after="80"/></w:pPr><w:rPr><w:b/><w:sz w:val="22"/><w:rFonts w:eastAsia="SimHei"/></w:rPr></w:style>'
        "</w:styles>"
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    document_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in (
            ("[Content_Types].xml", content_types_xml),
            ("_rels/.rels", rels_xml),
            ("word/document.xml", document_xml),
            ("word/styles.xml", styles_xml),
            ("word/_rels/document.xml.rels", document_rels_xml),
        ):
            # ZIP metadata must not change the bytes of an immutable historical report.
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o600 << 16
            archive.writestr(entry, content)
    return buffer.getvalue()


def _resolve_source_pdf(job_id: str, status_payload: Dict[str, Any]) -> Path:
    job_dir = runtime.UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")

    candidates: List[Path] = []

    def _append_candidate(raw_path: Any) -> None:
        if not isinstance(raw_path, str):
            return
        value = raw_path.strip()
        if not value:
            return
        path = Path(value)
        if not path.is_absolute():
            path = (runtime.UPLOAD_ROOT / path).resolve()
        candidates.append(path)

    _append_candidate(status_payload.get("saved_path"))
    result = status_payload.get("result")
    if isinstance(result, dict):
        _append_candidate(result.get("saved_path"))
        meta = result.get("meta")
        if isinstance(meta, dict):
            _append_candidate(meta.get("saved_path"))

    # 没有 status 明确的原件时，多 PDF 任务必须 fail-closed；按文件名
    # 排序猜测会把 annotated/report 派生件当成原件，破坏导出证据链。
    try:
        candidates.append(runtime.find_first_pdf(job_dir))
    except (FileNotFoundError, ValueError):
        pass

    for candidate in candidates:
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() == ".pdf":
            return candidate

    raise HTTPException(status_code=404, detail="source pdf not found")


def _resolve_report_pdf(job_id: str, status_payload: Dict[str, Any]) -> Path:
    job_dir = runtime.UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")

    candidates: List[Path] = []

    def _append_candidate(raw_path: Any) -> None:
        if not isinstance(raw_path, str):
            return
        value = raw_path.strip()
        if not value:
            return
        p = Path(value)
        if not p.is_absolute():
            p = (runtime.UPLOAD_ROOT / p).resolve()
        candidates.append(p)

    _append_candidate(status_payload.get("report_path"))
    result = status_payload.get("result")
    if isinstance(result, dict):
        meta = result.get("meta")
        if isinstance(meta, dict):
            _append_candidate(meta.get("report_path"))

    candidates.extend(
        [
            job_dir / "annotated.pdf",
            job_dir / "report.pdf",
            job_dir / "report_annotated.pdf",
        ]
    )

    try:
        candidates.append(runtime.find_first_pdf(job_dir))
    except Exception:
        pass

    for candidate in candidates:
        if (
            candidate.exists()
            and candidate.is_file()
            and candidate.suffix.lower() == ".pdf"
        ):
            return candidate

    raise HTTPException(status_code=404, detail="report pdf not found")


def _build_export_pdf_name(
    job_id: str,
    status_payload: Dict[str, Any],
    *,
    annotated: bool,
) -> str:
    raw_filename = str(status_payload.get("filename") or "").strip()
    base_name = Path(raw_filename).name if raw_filename else ""
    if not base_name or Path(base_name).suffix.lower() != ".pdf":
        base_name = f"{job_id}.pdf"

    source = Path(base_name)
    if annotated:
        return f"{source.stem}-annotated.pdf"
    return source.name


def _build_unique_zip_name(name: str, used_names: set[str], job_id: str) -> str:
    candidate = name
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    path = Path(name)
    candidate = f"{path.stem}-{job_id[:8]}{path.suffix or '.pdf'}"
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    index = 2
    while True:
        candidate = f"{path.stem}-{job_id[:8]}-{index}{path.suffix or '.pdf'}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        index += 1


def _iter_annotation_targets(issue: Dict[str, Any]) -> List[Dict[str, Any]]:
    export_location = issue.get("export_location")
    if not isinstance(export_location, dict):
        export_location = _build_export_location(issue)

    targets: List[Dict[str, Any]] = []
    seen = set()
    refs = export_location.get("table_refs")
    if isinstance(refs, list):
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            if not _has_verified_exact_location(ref):
                continue
            page = _to_positive_int(ref.get("page"))
            bbox = _normalize_bbox(ref.get("bbox"))
            if not page or not bbox:
                continue
            key = (page, tuple(bbox), str(ref.get("role") or "").strip())
            if key in seen:
                continue
            seen.add(key)
            targets.append(
                {
                    "page": page,
                    "bbox": bbox,
                    "role": str(ref.get("role") or "").strip(),
                    "table": str(ref.get("table") or "").strip(),
                    "section": str(ref.get("section") or "").strip(),
                    "row": str(ref.get("row") or "").strip(),
                    "field": str(ref.get("field") or "").strip(),
                    "code": str(ref.get("code") or "").strip(),
                    "subject": str(ref.get("subject") or "").strip(),
                }
            )

    if targets:
        return targets

    page = _to_positive_int(export_location.get("page"))
    bbox = _normalize_bbox(export_location.get("bbox")) if _has_verified_exact_location(issue) else None
    if page and bbox:
        return [
            {
                "page": page,
                "bbox": bbox,
                "role": "primary",
                "table": str(export_location.get("table") or "").strip(),
                "section": str(export_location.get("section") or "").strip(),
                "row": str(export_location.get("row") or "").strip(),
                "field": str(export_location.get("field") or "").strip(),
                "code": str(export_location.get("code") or "").strip(),
                "subject": str(export_location.get("subject") or "").strip(),
            }
        ]
    return []


def _build_annotation_label(
    issue: Dict[str, Any],
    target: Dict[str, Any],
    *,
    issue_index: int,
) -> str:
    parts = [f"#{issue_index}"]
    parts.append(str(issue.get("severity_label") or _severity_label(issue.get("severity"))))

    role = str(target.get("role") or "").strip()
    if role:
        parts.append({"parent": "合计项", "child": "明细项"}.get(role, role))

    row = str(target.get("row") or target.get("subject") or "").strip()
    code = str(target.get("code") or "").strip()
    if code and code not in row:
        row = f"{code} {row}".strip()
    if row:
        parts.append(row[:20])

    field = str(target.get("field") or "").strip()
    field = {
        "total_actual": "决算金额",
        "basic_actual": "基本支出决算",
        "project_actual": "项目支出决算",
        "income_actual": "收入决算金额",
        "expenditure_actual": "支出决算金额",
    }.get(field, field)
    if field:
        parts.append(field)

    if not row:
        rule_id = str(issue.get("rule_id") or issue.get("rule") or "").strip()
        if rule_id:
            parts.append(rule_id)
        else:
            title = str(issue.get("title") or "").strip()
            if title:
                parts.append(title[:20])

    label = " | ".join(parts)
    return label[:48]


def _color_for_severity(severity: str) -> Tuple[float, float, float]:
    return _SEVERITY_COLORS.get(str(severity or "").strip(), _SEVERITY_COLORS["info"])


def _draw_annotation(page: Any, bbox: Sequence[float], label: str, color: Tuple[float, float, float], fitz: Any) -> None:
    rect = fitz.Rect(*bbox)
    page_rect = page.rect
    rect.x0 = max(0.0, min(float(rect.x0), float(page_rect.x1)))
    rect.y0 = max(0.0, min(float(rect.y0), float(page_rect.y1)))
    rect.x1 = max(0.0, min(float(rect.x1), float(page_rect.x1)))
    rect.y1 = max(0.0, min(float(rect.y1), float(page_rect.y1)))
    if rect.x1 <= rect.x0 or rect.y1 <= rect.y0:
        return

    page.draw_rect(rect, color=color, width=2.2, overlay=True)

    label_height = 16.0
    label_width = min(float(page_rect.x1) - rect.x0, max(84.0, 6.5 * len(label) + 14.0))
    label_y0 = rect.y0 - label_height if rect.y0 >= label_height + 2.0 else rect.y0
    label_y1 = min(float(page_rect.y1), label_y0 + label_height)
    label_rect = fitz.Rect(rect.x0, label_y0, rect.x0 + label_width, label_y1)
    page.draw_rect(label_rect, color=color, fill=color, overlay=True)
    page.insert_text(
        fitz.Point(label_rect.x0 + 3.0, label_rect.y1 - 4.0),
        label,
        fontsize=7.5,
        fontname="china-s",
        color=(1, 1, 1),
        overlay=True,
    )


def _build_legend_text(issue: Dict[str, Any], target: Dict[str, Any], *, label: str) -> str:
    parts = [label]
    severity_label = str(issue.get("severity_label") or _severity_label(issue.get("severity")))
    if severity_label:
        parts.append(f"\u7ea7\u522b:{severity_label}")
    row = str(target.get("row") or target.get("subject") or "").strip()
    code = str(target.get("code") or "").strip()
    if code and code not in row:
        row = f"{code} {row}".strip()
    if row:
        parts.append(f"行:{row}")
    field = str(target.get("field") or "").strip()
    field = {
        "total_actual": "决算金额",
        "basic_actual": "基本支出决算",
        "project_actual": "项目支出决算",
        "income_actual": "收入决算金额",
        "expenditure_actual": "支出决算金额",
    }.get(field, field)
    if field:
        parts.append(f"字段:{field}")
    return " | ".join(parts)[:72]


def _draw_page_legend(page: Any, entries: Sequence[Dict[str, Any]], fitz: Any) -> None:
    if not entries:
        return

    max_entries = 8
    visible_entries = list(entries[:max_entries])
    overflow = len(entries) - len(visible_entries)

    page_rect = page.rect
    width = min(230.0, float(page_rect.width) - 24.0)
    header_height = 18.0
    line_height = 14.0
    padding = 8.0
    total_lines = len(visible_entries) + (1 if overflow > 0 else 0)
    height = padding * 2 + header_height + total_lines * line_height

    x1 = float(page_rect.x1) - 12.0
    y0 = 12.0
    x0 = max(12.0, x1 - width)
    y1 = min(float(page_rect.y1) - 12.0, y0 + height)
    legend_rect = fitz.Rect(x0, y0, x1, y1)

    page.draw_rect(
        legend_rect,
        color=(0.79, 0.83, 0.88),
        fill=(1, 1, 1),
        width=0.8,
        overlay=True,
    )
    page.insert_text(
        fitz.Point(legend_rect.x0 + padding, legend_rect.y0 + 13.0),
        "问题索引",
        fontsize=9.0,
        fontname="china-s",
        color=(0.19, 0.24, 0.33),
        overlay=True,
    )

    current_y = legend_rect.y0 + padding + header_height + 2.0
    for entry in visible_entries:
        color = entry["color"]
        marker_rect = fitz.Rect(legend_rect.x0 + padding, current_y, legend_rect.x0 + padding + 8.0, current_y + 8.0)
        page.draw_rect(marker_rect, color=color, fill=color, overlay=True)
        page.insert_text(
            fitz.Point(marker_rect.x1 + 5.0, current_y + 8.0),
            str(entry["text"]),
            fontsize=7.4,
            fontname="china-s",
            color=(0.19, 0.24, 0.33),
            overlay=True,
        )
        current_y += line_height

    if overflow > 0:
        page.insert_text(
            fitz.Point(legend_rect.x0 + padding, current_y + 8.0),
            f"... 另有 {overflow} 项",
            fontsize=7.2,
            fontname="china-s",
            color=(0.45, 0.49, 0.55),
            overlay=True,
        )


def _annotated_output_path(
    job_id: str,
    status_payload: Dict[str, Any],
    issues: Sequence[Dict[str, Any]],
) -> Path:
    """内容寻址的标注 PDF 产物路径（RC-12：防历史/当前/并发导出串版）。

    同一（分析版本 × 问题集）→ 同一文件（可复用，产物不可变）；
    任一不同 → 不同文件——后生成的导出绝不会覆盖上一个响应尚未
    发送完的字节。此前两版共用 `annotated.pdf` 单一路径，历史下载
    与当前下载互相覆盖（复核 RC-12 实证）。
    """
    revision = str(status_payload.get("analysis_revision") or "").strip()
    if not revision:
        report = status_payload.get("review_report")
        if isinstance(report, dict):
            revision = str(report.get("analysis_revision") or "").strip()
    digest_payload = json.dumps(
        {
            "issues": list(issues),
            "review_report_sha256": _review_report_sha256(status_payload),
            "renderer_version": _PDF_EXPORT_RENDERER_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    issues_digest = hashlib.sha256(digest_payload.encode("utf-8")).hexdigest()[:16]
    slug = revision or "unversioned"
    for part in ("/", "\\", ".."):
        slug = slug.replace(part, "_")
    return (runtime.UPLOAD_ROOT / job_id / f"annotated-{slug}-{issues_digest}.pdf").resolve()


def _create_zero_issue_appendix_pdf(
    job_id: str,
    status_payload: Dict[str, Any],
) -> Optional[Path]:
    """零问题报告也生成副本附录，说明检查范围、状态与待复核事项。

    只写副本，不修改原件；附录页顶部明确标注"系统生成附录"。
    """
    try:
        import fitz
    except Exception:
        return None

    try:
        source_pdf = _resolve_source_pdf(job_id, status_payload)
    except HTTPException:
        return None
    if source_pdf is None:
        return None
    output_path = _annotated_output_path(job_id, status_payload, [])
    if output_path.exists():
        # 产物不可变：同版本同内容直接复用，不重写、不覆盖
        return output_path

    try:
        with fitz.open(str(source_pdf)) as document:
            _append_report_appendix_pages(document, job_id, status_payload, [])
            document.save(str(output_path), garbage=4, deflate=True)
    except Exception:
        logger.exception("Failed to build zero-issue appendix pdf for job %s", job_id)
        return None
    return output_path


def _append_report_appendix_pages(
    document: Any,
    job_id: str,
    status_payload: Dict[str, Any],
    issues: Sequence[Dict[str, Any]],
) -> None:
    """Append the canonical report details to the derived PDF, including unlocated items."""
    import fitz
    from src.schemas.review_report import normalize_manual_review
    report = status_payload.get("review_report")
    report = report if isinstance(report, dict) else {}
    conclusion = _build_export_conclusion(status_payload, len(issues))
    lines: List[str] = ["系统生成附录 · 审校清单", f"任务：{job_id}", ""]
    decisions = report.get("human_decisions")
    decisions_by_issue = {
        str(item.get("issue_id") or ""): item
        for item in decisions
        if isinstance(item, dict) and str(item.get("issue_id") or "")
    } if isinstance(decisions, list) else {}
    lines.append(f"REVIEW_REPORT_SHA256: {_review_report_sha256(status_payload)}")
    if conclusion["is_complete"] is True:
        lines.append("检查范围已完成。以下问题和提示以本次 ReviewReport 为准。")
    elif conclusion["coverage_known"] is False:
        lines.append("检查覆盖范围未记录；问题清单为空不代表材料没有问题。")
    else:
        lines.append(
            f"检查尚未完整完成（{len(conclusion['unfinished_items'])} 项未完成）；问题清单为空不代表材料没有问题。"
        )
    decisions = report.get("human_decisions")
    if isinstance(decisions, list) and decisions:
        lines.extend(["", "人工处理决定："])
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            lines.append(
                "DECISION_ID|" + str(decision.get("issue_id") or "") + "| " + " · ".join(
                    str(value)
                    for value in (
                        decision.get("issue_id"),
                        decision.get("status"),
                        decision.get("note"),
                        decision.get("analysis_revision"),
                        decision.get("decision_revision"),
                        decision.get("actor"),
                        decision.get("inherited"),
                        decision.get("fact_ids"),
                        decision.get("evidence_ids"),
                        decision.get("identity_fingerprint"),
                    )
                    if value
                )
            )
    lines.append("")

    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    for label, key in (("材料类型", "report_kind"), ("年度", "year"), ("发布单位", "organization")):
        value = metadata.get(key)
        lines.append(f"{label}：{value if value not in (None, '') else '未识别到'}")
    if not report:
        lines.append("ReviewReport：旧版未记录")

    tip_ids = {
        str(item.get("id") or "")
        for item in report.get("tips", [])
        if isinstance(item, dict)
    }
    lines.extend(["", f"问题与提示（{len(issues)} 项）："])
    if not issues:
        lines.append("未发现已确认问题；检查范围和限制见上文及待复核清单。")
    for index, issue in enumerate(issues, start=1):
        issue_id = str(issue.get("id") or "")
        category = "提示" if issue_id in tip_ids else "问题"
        location = issue.get("location") if isinstance(issue.get("location"), dict) else {}
        evidence = issue.get("evidence") if isinstance(issue.get("evidence"), list) else []
        snippet = next(
            (
                str(item.get("text_snippet") or item.get("text") or "").strip()
                for item in evidence
                if isinstance(item, dict) and str(item.get("text_snippet") or item.get("text") or "").strip()
            ),
            str(issue.get("text_snippet") or "").strip(),
        )
        page = issue.get("page_number") or location.get("page")
        human_decision = issue.get("human_decision") if isinstance(issue.get("human_decision"), dict) else decisions_by_issue.get(issue_id, {})
        decision_summary = "；".join(
            str(value).strip()
            for value in (human_decision.get("status"), human_decision.get("note"))
            if str(value or "").strip()
        )
        lines.extend(
            [
                f"ISSUE_ID|{issue_id}|",
                f"{index}. [{category}] {issue.get('title') or issue.get('rule_id') or '未命名'}；严重度={issue.get('severity') or '未标级'}",
                f"规则：{issue.get('rule_id') or '未识别到'}；页码：{page if page else '未定位'}；定位等级：{issue.get('location_level') or location.get('location_level') or 'unresolved'}；验证：{issue.get('verification_status') or location.get('verification_status') or ''}；坐标：{issue.get('bbox') or location.get('bbox') or '当前无法定位'}",
                f"关系/事实/证据身份：{issue.get('check_relation_id') or ''}；{issue.get('fact_ids') or []}；{issue.get('evidence_ids') or []}",
                f"说明：{issue.get('message') or '未识别到'}",
                f"人工处理决定：{decision_summary or '未记录'}",
            ]
        )
        if snippet:
            lines.append(f"{_evidence_excerpt_label(issue)}：{snippet}")
        if issue.get("suggestion"):
            lines.append(f"建议：{issue.get('suggestion')}")
        lines.append("")

    manual_review = report.get("manual_review")
    manual_review = manual_review if isinstance(manual_review, list) else []
    if not report and conclusion["unfinished_items"]:
        manual_review = [
            {
                "record_type": "unfinished_check",
                "obligation_id": item.get("obligation_id"),
                "title": item.get("group_title") or item.get("title"),
                "status": item.get("status"),
                "reason": item.get("reason_label") or item.get("status"),
                "detail": item.get("detail") or item.get("gap_note"),
            }
            for item in conclusion["unfinished_items"]
        ]
    lines.append(f"待人工复核（{len(manual_review)} 项）：")
    if not manual_review:
        lines.append("无已记录的待人工复核项。")
    for index, item in enumerate(manual_review, start=1):
        if not isinstance(item, dict):
            continue
        title = normalize_manual_review(item)["display_title"]
        obligation_id = item.get("obligation_id") or item.get("code")
        detail = item.get("detail") or item.get("reason") or item.get("message") or "未提供原因"
        lines.append(
            f"{index}. [类型：{item.get('record_type') or 'manual_review'}] "
            + f"[状态：{item.get('status') or '未记录'}] "
            + f"MANUAL_ID|{item.get('id') or item.get('obligation_id') or item.get('code') or ''}| "
            + (f"{obligation_id} · " if obligation_id else "")
            + f"{title}：{detail}"
        )
        lines.extend(f"{label}：{value}" for label, value in _manual_finding_details(item) if value)
    lines.append("")

    receipts = report.get("check_receipts")
    receipts = receipts if isinstance(receipts, list) else []
    lines.append(f"检查回执（{len(receipts)} 项）：")
    if not receipts:
        lines.append("未记录子检查回执。")
    for index, receipt in enumerate(receipts, start=1):
        if not isinstance(receipt, dict):
            continue
        label = receipt.get("sub_check_id") or receipt.get("rule_id") or f"检查项{index}"
        state = receipt.get("status") or "未记录"
        reason = receipt.get("incomplete_reason") or ""
        formula = receipt.get("formula") or ""
        lines.append(
            f"CHECK_ID|{receipt.get('rule_id') or ''}:{receipt.get('sub_check_id') or ''}| "
            + f"{index}. {label}：{state}"
            + (f"；公式：{formula}" if formula else "")
            + (f"；未完成原因：{reason}" if reason else "")
        )
        lines.append(
            f"操作数事实ID：{receipt.get('operand_fact_ids') or receipt.get('required_fact_ids') or []}；"
            f"问题引用ID：{receipt.get('issue_refs') or []}；容差：{receipt.get('tolerance') or {}}"
        )

    wrapped: List[str] = []
    for line in lines:
        if not line:
            wrapped.append("")
            continue
        wrapped.extend(line[start : start + 58] for start in range(0, len(line), 58))
    chunk_size = 48
    font = fitz.Font("china-s")
    for start in range(0, len(wrapped), chunk_size):
        page = document.new_page()
        page.insert_text((48, 38), "系统生成附录 · 审校清单", fontsize=9, fontname="china-s", color=(0.35, 0.4, 0.48))
        writer = fitz.TextWriter(page.rect)
        y = 64.0
        for line in wrapped[start : start + chunk_size]:
            if line:
                # TextWriter supplies fallback glyphs for e.g. the mathematical
                # minus sign U+2212, which the CJK CID insert_text path drops.
                writer.append((48, y), line, fontsize=9, font=font)
            y += 14.0
        writer.write_text(page)


def _create_annotated_pdf(
    job_id: str,
    status_payload: Dict[str, Any],
    issues: Sequence[Dict[str, Any]],
) -> Optional[Path]:
    if not issues:
        return _create_zero_issue_appendix_pdf(job_id, status_payload)

    try:
        import fitz
    except Exception:
        return None

    source_pdf = _resolve_source_pdf(job_id, status_payload)
    if source_pdf is None:
        return None
    output_path = _annotated_output_path(job_id, status_payload, list(issues))
    if output_path.exists():
        # 产物不可变：同版本同问题集的标注 PDF 直接复用（RC-12）
        return output_path

    page_legends: Dict[int, List[Dict[str, Any]]] = {}
    legend_seen: Dict[int, set[tuple[str, str]]] = {}
    with fitz.open(str(source_pdf)) as document:
        for issue_index, raw_issue in enumerate(issues, start=1):
            issue = _enrich_issue(raw_issue)
            color = _color_for_severity(str(issue.get("severity") or "info"))
            for target in _iter_annotation_targets(issue):
                page_number = _to_positive_int(target.get("page"))
                bbox = _normalize_bbox(target.get("bbox"))
                if not page_number or not bbox:
                    continue
                if page_number > document.page_count:
                    continue
                page = document.load_page(page_number - 1)
                label = _build_annotation_label(issue, target, issue_index=issue_index)
                _draw_annotation(page, bbox, label, color, fitz)
                entry_text = _build_legend_text(issue, target, label=label)
                legend_key = (label, entry_text)
                page_legends.setdefault(page_number, [])
                legend_seen.setdefault(page_number, set())
                if legend_key not in legend_seen[page_number]:
                    legend_seen[page_number].add(legend_key)
                    page_legends[page_number].append({"text": entry_text, "color": color})
        for page_number, entries in page_legends.items():
            if page_number < 1 or page_number > document.page_count:
                continue
            page = document.load_page(page_number - 1)
            _draw_page_legend(page, entries, fitz)

        _append_report_appendix_pages(document, job_id, status_payload, issues)

        document.save(str(output_path), garbage=4, deflate=True)

    return output_path


def _load_revision_payload(job_id: str, analysis_revision: Optional[str]) -> Dict[str, Any]:
    """按 revision id 读取历史分析版本快照（AC-12：历史版本下载入口）。

    不传 revision 时返回当前 status payload；传入时只接受目录名或
    analysis_revision 精确匹配，路径穿越被拒绝，找不到返回 404。
    """
    if not analysis_revision:
        return runtime.get_job_status_payload(job_id)
    revision = str(analysis_revision).strip()
    if not revision or "/" in revision or "\\" in revision or revision.startswith("."):
        raise HTTPException(status_code=422, detail="invalid analysis_revision")
    versions_dir = runtime.UPLOAD_ROOT / job_id / "analysis_versions"
    candidates: List[Path] = []
    direct = versions_dir / revision / "status.json"
    if direct.is_file():
        candidates.append(direct)
    if not candidates and versions_dir.is_dir():
        for revision_dir in versions_dir.glob("rev-*"):
            path = revision_dir / "status.json"
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(payload.get("analysis_revision")) == revision:
                candidates.append(path)
    if not candidates:
        raise HTTPException(status_code=404, detail="analysis_revision not found")
    try:
        raw=candidates[-1].read_bytes()
        legacy_manifest=candidates[-1].parent/'legacy_archive_manifest.json'
        if legacy_manifest.is_file():
            record=json.loads(legacy_manifest.read_text())
            if (hashlib.sha256(raw).hexdigest()!=record.get('files',{}).get('status.json')
                or hashlib.sha256(json.dumps(record['files'],sort_keys=True).encode()).hexdigest()!=record.get('archive_sha256')):
                raise HTTPException(409,'historical original report integrity failed')
        payload = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="analysis_revision is unreadable") from exc
    payload.setdefault("job_id", job_id)
    return payload


def _hydrate_version_bound_decisions(job_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Include current audited decisions only when bound to this report revision/identity."""
    from src.schemas.review_report import build_review_report
    from src.services.issue_workflow_store import project_job_issue_decisions

    hydrated = dict(payload)
    hydrated["human_decisions"] = project_job_issue_decisions(job_id, hydrated)
    hydrated["review_report"] = build_review_report(hydrated)
    return hydrated


@router.get("/api/reports/download")
async def download_report(
    request: Request,
    job_id: str,
    format: str = Query(default="pdf", pattern="^(pdf|json|csv|docx)$"),
    analysis_revision: Optional[str] = Query(default=None),
):
    _, _, _, status_payload = require_job_access(request, job_id)
    if analysis_revision and ("/" in analysis_revision or "\\" in analysis_revision or analysis_revision.startswith('.')):
        raise HTTPException(status_code=422,detail='invalid analysis_revision')
    if os.getenv('DATABASE_URL'):
        from src.services.analysis_fact_snapshot import read_database_result
        try:
            published = await read_database_result(job_id,analysis_revision)
        except ValueError as exc:
            raise HTTPException(status_code=409,detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=503,detail=str(exc)) from exc
        status_payload = published if published is not None else _load_revision_payload(job_id,analysis_revision)
    else:
        status_payload = _load_revision_payload(job_id, analysis_revision)
    status_payload = _hydrate_version_bound_decisions(job_id, status_payload)
    issues = _extract_issues(status_payload)
    review_report = status_payload.get("review_report")
    review_report = review_report if isinstance(review_report, dict) else {}

    if format == "json":
        enriched_issues = [_enrich_issue(item) for item in issues]
        payload: Dict[str, Any] = {
            "job_id": job_id,
            "status": status_payload.get("status"),
            "analysis_revision": status_payload.get("analysis_revision"),
            "review_report_sha256": _review_report_sha256(status_payload),
            "review_report": review_report or "旧版未记录",
            "human_decisions": (
                review_report.get("human_decisions", [])
                if isinstance(review_report, dict)
                else []
            ),
            "issues": enriched_issues,
            "count": len(enriched_issues),
        }
        # 导出必须能回答"哪些检查没做完、为什么"，否则使用者会把
        # "结果里没有问题"读成"材料没有问题"（plan §3/§6）。
        # 旧任务没有这些字段时明确写"旧版未记录"，不显示成已完成。
        payload.update(_build_check_scope_export(status_payload))
        return payload

    if format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=_CSV_FIELDNAMES,
        )
        writer.writeheader()
        # 所有导出行都来自 ReviewReport。摘要、问题、提示、待复核和检查回执
        # 使用 record_type 区分；旧任务保留原来的兼容字段与范围说明。
        writer.writerow(_build_csv_summary_row(job_id, status_payload, len(issues)))
        tip_ids = {
            str(item.get("id") or "")
            for item in review_report.get("tips", [])
            if isinstance(item, dict)
        }
        for item in issues:
            row = _build_csv_row(item)
            if str(item.get("id") or "") in tip_ids:
                row["record_type"] = "tip"
            writer.writerow(row)
        for item in review_report.get("human_decisions", []):
            if not isinstance(item, dict):
                continue
            row = {key: "" for key in _CSV_FIELDNAMES}
            row.update({
                "record_type": "human_decision",
                "issue_refs": item.get("issue_id") or "",
                "human_decision": item.get("status") or "",
                "human_decision_note": item.get("note") or "",
                "analysis_revision": item.get("analysis_revision") or "",
                "decision_revision": item.get("decision_revision") or "",
                "decision_actor": item.get("actor") or "",
                "decision_inherited": item.get("inherited", ""),
                "decision_identity_fingerprint": item.get("identity_fingerprint") or "",
                "check_relation_id": item.get("check_relation_id") or "",
                "fact_ids": json.dumps(item.get("fact_ids") or [], ensure_ascii=False),
                "evidence_ids": json.dumps(item.get("evidence_ids") or [], ensure_ascii=False),
            })
            writer.writerow(row)
        for item in review_report.get("manual_review", []):
            if not isinstance(item, dict):
                continue
            from src.schemas.review_report import normalize_manual_review
            manual_title = normalize_manual_review(item)["title"]
            manual_detail = item.get("message") or item.get("detail") or item.get("reason") or ""
            finding = item.get("finding")
            row = (_build_csv_row(_enrich_issue(finding)) if isinstance(finding, dict)
                   else {key: "" for key in _CSV_FIELDNAMES})
            row.update(
                {
                    "record_type": "manual_review",
                    "manual_review_type": item.get("record_type") or "manual_review",
                    "issue_id": item.get("id") or "",
                    "obligation_ids": item.get("obligation_id") or "",
                    "规则编号": item.get("code") or (finding.get("rule_id") if isinstance(finding, dict) else "") or "",
                    "标题": manual_title,
                    "title": manual_title,
                    "摘要": manual_detail,
                    "message": manual_detail,
                    "严重级别": "待人工复核",
                    "严重级别代码": "manual_review",
                    "check_status": item.get("status") or "",
                    "incomplete_reason": item.get("message") or item.get("detail") or item.get("reason") or "",
                    "suggestion": item.get("suggestion") or "请核对原始材料后确认。",
                }
            )
            writer.writerow(row)
        for receipt in review_report.get("check_receipts", []):
            if not isinstance(receipt, dict):
                continue
            row = {key: "" for key in _CSV_FIELDNAMES}
            row.update(
                {
                    "record_type": "check",
                    "check_id": receipt.get("sub_check_id") or "",
                    "check_status": receipt.get("status") or "",
                    "incomplete_reason": receipt.get("incomplete_reason") or "",
                    "rule_id": receipt.get("rule_id") or "",
                    "规则编号": receipt.get("rule_id") or "",
                    "obligation_ids": receipt.get("obligation_id") or "",
                    "formula": receipt.get("formula") or "",
                    "tolerance": json.dumps(receipt.get("tolerance") or {}, ensure_ascii=False),
                    "issue_refs": json.dumps(receipt.get("issue_refs") or [], ensure_ascii=False),
                    "operand_fact_ids": json.dumps(receipt.get("operand_fact_ids") or [], ensure_ascii=False),
                    "required_fact_ids": json.dumps(receipt.get("required_fact_ids") or [], ensure_ascii=False),
                    "evaluation_basis": receipt.get("evaluation_basis") or "",
                    "fact_migration_status": receipt.get("fact_migration_status") or "",
                }
            )
            writer.writerow(row)

        data = output.getvalue().encode("utf-8-sig")
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'},
        )

    if format == "docx":
        data = _create_docx_report(job_id, status_payload, issues)
        return Response(
            content=data,
            media_type=_DOCX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{job_id}.docx"'},
        )

    annotated_pdf: Optional[Path] = None
    try:
        annotated_pdf = _create_annotated_pdf(job_id, status_payload, issues)
    except HTTPException:
        annotated_pdf = None
    except Exception:
        logger.exception("Failed to build annotated PDF for job %s", job_id)
        annotated_pdf = None

    report_pdf = annotated_pdf or _resolve_report_pdf(job_id, status_payload)
    return FileResponse(
        str(report_pdf),
        media_type="application/pdf",
        filename=report_pdf.name,
    )


@router.post("/api/reports/download-batch")
async def download_reports_batch(body: Dict[str, Any], request: Request):
    _, _, user = require_login(request)
    raw_job_ids = body.get("job_ids")
    if not isinstance(raw_job_ids, list):
        raise HTTPException(status_code=400, detail="job_ids must be a list")

    job_ids: List[str] = []
    seen_job_ids = set()
    for item in raw_job_ids:
        normalized = str(item or "").strip()
        if not normalized or normalized in seen_job_ids:
            continue
        seen_job_ids.add(normalized)
        job_ids.append(normalized)

    if not job_ids:
        raise HTTPException(status_code=400, detail="job_ids is required")

    package_id = str(body.get("package_id") or "").strip()
    archived_payloads = None
    if package_id:
        from src.services.issue_workflow_store import get_package_report_payloads

        archived_payloads = get_package_report_payloads(user, package_id)
        if set(job_ids) != set(archived_payloads):
            raise HTTPException(status_code=409, detail={"error": "package_job_set_mismatch"})

    archive_buffer = io.BytesIO()
    exported: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    used_names: set[str] = set()

    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for job_id in job_ids:
            try:
                _, _, _, status_payload = require_job_access(request, job_id)
                if archived_payloads is not None:
                    status_payload = archived_payloads[job_id]
                issues = _extract_issues(status_payload)
                annotated_pdf = _create_annotated_pdf(job_id, status_payload, issues)
                report_pdf = annotated_pdf or _resolve_report_pdf(job_id, status_payload)
                export_name = _build_export_pdf_name(
                    job_id,
                    status_payload,
                    annotated=annotated_pdf is not None,
                )
                zip_name = _build_unique_zip_name(export_name, used_names, job_id)
                archive.write(report_pdf, arcname=zip_name)
                exported.append(
                    {
                        "job_id": job_id,
                        "filename": zip_name,
                        "annotated": annotated_pdf is not None,
                        "analysis_revision": status_payload.get("analysis_revision"),
                    }
                )
            except HTTPException as exc:
                if exc.status_code in {401, 403}:
                    raise
                failed.append(
                    {
                        "job_id": job_id,
                        "status_code": exc.status_code,
                        "detail": exc.detail,
                    }
                )
            except Exception as exc:
                logger.exception("Failed to export report pdf for job %s", job_id)
                failed.append(
                    {
                        "job_id": job_id,
                        "status_code": 500,
                        "detail": str(exc),
                    }
                )

        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "requested_count": len(job_ids),
                    "package_id": package_id or None,
                    "exported_count": len(exported),
                    "failed_count": len(failed),
                    "exported": exported,
                    "failed": failed,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    if not exported:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "no_reports_exported",
                "requested_count": len(job_ids),
                "failed": failed,
            },
        )

    archive_buffer.seek(0)
    response = Response(
        content=archive_buffer.getvalue(),
        media_type="application/zip",
    )
    response.headers["Content-Disposition"] = 'attachment; filename="reports-batch.zip"'
    return response
