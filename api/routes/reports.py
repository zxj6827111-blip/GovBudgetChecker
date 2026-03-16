"""Report download/export endpoints."""

from __future__ import annotations

import csv
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from api import runtime
from src.utils.issue_display import build_issue_display

router = APIRouter()
logger = logging.getLogger(__name__)

_GENERATED_PDF_NAMES = {
    "annotated.pdf",
    "report.pdf",
    "report_annotated.pdf",
}
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
}
_CSV_FIELDNAMES: List[str] = [
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
]


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


def _extract_issues(status_payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = status_payload.get("result")
    if not isinstance(result, dict):
        return []

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
            return merged_issues

    deduped_issues = _dedupe(issues)
    if deduped_issues:
        return deduped_issues

    return deduped_source_issues


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
            "value": item.get("value"),
            "bbox": _normalize_bbox(item.get("bbox")),
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


def _resolve_suggestion(issue: Dict[str, Any]) -> str:
    suggestion = issue.get("suggestion")
    if isinstance(suggestion, str):
        return suggestion.strip()
    suggestions = issue.get("suggestions")
    if isinstance(suggestions, list):
        return " | ".join(str(item).strip() for item in suggestions if str(item).strip())
    return ""


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

    primary_bbox = (
        _normalize_bbox(issue.get("bbox"))
        or (_normalize_bbox(refs[0].get("bbox")) if refs else None)
        or (
            _normalize_bbox(evidence[0].get("bbox"))
            if evidence and isinstance(evidence[0], dict)
            else None
        )
    )

    export_location = {
        "page": pages[0] if pages else None,
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
        "table_ref_count": len(refs),
        "role_summary": _build_role_summary(refs, fallback=location),
        "table_refs": refs,
    }

    return {key: value for key, value in export_location.items() if value not in (None, "", [])}


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
    row = {
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
    }
    return {key: row.get(key, "") for key in _CSV_FIELDNAMES}


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

    for candidate in sorted(job_dir.glob("*.pdf")):
        if candidate.name in _GENERATED_PDF_NAMES:
            continue
        candidates.append(candidate)

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
    bbox = _normalize_bbox(export_location.get("bbox"))
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
        parts.append(role)

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
    for key, prefix in (
        ("field", "字段"),
        ("row", "行"),
        ("table", "表"),
        ("section", "章节"),
        ("code", "编码"),
        ("subject", "科目"),
    ):
        value = str(target.get(key) or "").strip()
        if value:
            parts.append(f"{prefix}:{value}")
            break
    return " | ".join(parts)[:42]


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


def _create_annotated_pdf(
    job_id: str,
    status_payload: Dict[str, Any],
    issues: Sequence[Dict[str, Any]],
) -> Optional[Path]:
    if not issues:
        return None

    try:
        import fitz
    except Exception:
        return None

    source_pdf = _resolve_source_pdf(job_id, status_payload)
    output_path = (runtime.UPLOAD_ROOT / job_id / "annotated.pdf").resolve()
    if output_path.exists():
        output_path.unlink()

    marker_count = 0
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
                marker_count += 1

        if marker_count == 0:
            return None

        for page_number, entries in page_legends.items():
            if page_number < 1 or page_number > document.page_count:
                continue
            page = document.load_page(page_number - 1)
            _draw_page_legend(page, entries, fitz)

        document.save(str(output_path), garbage=4, deflate=True)

    return output_path


@router.get("/api/reports/download")
async def download_report(
    job_id: str,
    format: str = Query(default="pdf", pattern="^(pdf|json|csv)$"),
):
    status_payload = runtime.get_job_status_payload(job_id)
    issues = _extract_issues(status_payload)

    if format == "json":
        enriched_issues = [_enrich_issue(item) for item in issues]
        return {
            "job_id": job_id,
            "status": status_payload.get("status"),
            "issues": enriched_issues,
            "count": len(enriched_issues),
        }

    if format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=_CSV_FIELDNAMES,
        )
        writer.writeheader()
        for item in issues:
            writer.writerow(_build_csv_row(item))

        data = output.getvalue().encode("utf-8-sig")
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'},
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
async def download_reports_batch(body: Dict[str, Any]):
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

    archive_buffer = io.BytesIO()
    exported: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    used_names: set[str] = set()

    with zipfile.ZipFile(archive_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for job_id in job_ids:
            try:
                status_payload = runtime.get_job_status_payload(job_id)
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
