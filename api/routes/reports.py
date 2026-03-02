"""Report download/export endpoints."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from api import runtime

router = APIRouter()


def _normalize_bbox(raw: Any) -> Optional[List[float]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    out: List[float] = []
    for item in raw:
        try:
            out.append(float(item))
        except Exception:
            return None
    return out


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


@router.get("/api/reports/download")
async def download_report(
    job_id: str,
    format: str = Query(default="pdf", pattern="^(pdf|json|csv)$"),
):
    status_payload = runtime.get_job_status_payload(job_id)
    issues = _extract_issues(status_payload)

    if format == "json":
        return {
            "job_id": job_id,
            "status": status_payload.get("status"),
            "issues": issues,
            "count": len(issues),
        }

    if format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "rule_id",
                "title",
                "severity",
                "page",
                "bbox",
                "text_snippet",
                "suggestion",
                "message",
            ],
        )
        writer.writeheader()
        for item in issues:
            location = (
                item.get("location") if isinstance(item.get("location"), dict) else {}
            )
            evidence = (
                item.get("evidence") if isinstance(item.get("evidence"), list) else []
            )
            first_ev = evidence[0] if evidence and isinstance(evidence[0], dict) else {}
            bbox = _normalize_bbox(first_ev.get("bbox")) or _normalize_bbox(
                item.get("bbox")
            )
            writer.writerow(
                {
                    "rule_id": item.get("rule_id") or item.get("rule") or "",
                    "title": item.get("title") or "",
                    "severity": item.get("severity") or "",
                    "page": location.get("page") or first_ev.get("page") or "",
                    "bbox": bbox or "",
                    "text_snippet": first_ev.get("text_snippet")
                    or first_ev.get("text")
                    or item.get("text_snippet")
                    or "",
                    "suggestion": item.get("suggestion") or "",
                    "message": item.get("message") or "",
                }
            )
        data = output.getvalue().encode("utf-8-sig")
        return Response(
            content=data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{job_id}.csv"'},
        )

    report_pdf = _resolve_report_pdf(job_id, status_payload)
    return FileResponse(
        str(report_pdf),
        media_type="application/pdf",
        filename=report_pdf.name,
    )
