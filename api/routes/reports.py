"""Report download/export endpoints."""

from __future__ import annotations

import csv
import io
import json
import logging
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

    issues: List[Dict[str, Any]] = []

    legacy_issues = result.get("issues")
    if isinstance(legacy_issues, dict):
        all_items = legacy_issues.get("all")
        if isinstance(all_items, list):
            issues.extend([item for item in all_items if isinstance(item, dict)])
    elif isinstance(legacy_issues, list):
        issues.extend([item for item in legacy_issues if isinstance(item, dict)])

    for key in ("ai_findings", "rule_findings"):
        bucket = result.get(key)
        if isinstance(bucket, list):
            issues.extend([item for item in bucket if isinstance(item, dict)])

    return issues


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
    return enriched


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


def _build_legend_text(target: Dict[str, Any], *, label: str) -> str:
    parts = [label]
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
                entry_text = _build_legend_text(target, label=label)
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
            fieldnames=[
                "rule_id",
                "title",
                "summary",
                "severity",
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
            ],
        )
        writer.writeheader()
        for item in issues:
            enriched = _enrich_issue(item)
            display = enriched.get("display") if isinstance(enriched.get("display"), dict) else {}
            export_location = (
                enriched.get("export_location")
                if isinstance(enriched.get("export_location"), dict)
                else {}
            )
            writer.writerow(
                {
                    "rule_id": enriched.get("rule_id") or enriched.get("rule") or "",
                    "title": enriched.get("title") or "",
                    "summary": display.get("summary") or enriched.get("title") or "",
                    "severity": enriched.get("severity") or "",
                    "page": export_location.get("page") or "",
                    "pages": " | ".join(str(item) for item in export_location.get("pages", [])),
                    "table": export_location.get("table") or "",
                    "section": export_location.get("section") or "",
                    "row": export_location.get("row") or "",
                    "col": export_location.get("col") or "",
                    "field": export_location.get("field") or "",
                    "code": export_location.get("code") or "",
                    "subject": export_location.get("subject") or "",
                    "location": display.get("location_text") or "",
                    "detail_lines": " | ".join(
                        [
                            str(line)
                            for line in display.get("detail_lines", [])
                            if str(line).strip()
                        ]
                    ),
                    "evidence_text": display.get("evidence_text") or "",
                    "bbox": json.dumps(export_location.get("bbox"), ensure_ascii=False)
                    if export_location.get("bbox")
                    else "",
                    "table_ref_count": export_location.get("table_ref_count") or 0,
                    "evidence_role_summary": export_location.get("role_summary") or "",
                    "table_refs": json.dumps(
                        export_location.get("table_refs", []), ensure_ascii=False
                    ),
                    "text_snippet": _resolve_text_snippet(enriched),
                    "suggestion": _resolve_suggestion(enriched),
                    "message": enriched.get("message") or "",
                }
            )

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
