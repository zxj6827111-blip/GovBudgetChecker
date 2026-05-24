"""Upload endpoints."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api import runtime
from api.routes.organizations import clear_department_stats_cache
from src.services.org_matcher import get_org_matcher

router = APIRouter()
logger = logging.getLogger(__name__)
_MATCH_BASIS_PRIORITY = {
    "cover_field": 2,
    "content_fallback": 1,
}


def _clean_optional_text(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _resolve_manual_org(selected_org: Optional[str]) -> Optional[dict]:
    if not selected_org:
        return None
    if not runtime.ORG_AVAILABLE:
        raise HTTPException(status_code=503, detail="organization service unavailable")
    storage = runtime.require_org_storage()
    org = storage.get_by_id(selected_org)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    return runtime.to_dict(org)


def _serialize_match_candidate(org: Any, confidence: float, match_basis: str) -> Dict[str, Any]:
    storage = runtime.require_org_storage()
    department = None
    if getattr(org, "level", "") == "department":
        department = org
    else:
        parent_id = getattr(org, "parent_id", None)
        if parent_id:
            department = storage.get_by_id(parent_id)

    return {
        "organization_id": org.id,
        "organization_name": org.name,
        "level": getattr(org, "level", ""),
        "department_id": getattr(department, "id", None),
        "department_name": getattr(department, "name", None),
        "confidence": round(float(confidence or 0.0), 4),
        "match_basis": match_basis,
    }


def _collect_org_matches(
    *,
    filename: str,
    first_page_text: str,
    cover_org_name: str = "",
    cover_org_label: str = "",
    scope_hint: str = "",
    top_n: int = 5,
) -> List[Tuple[Any, float, str]]:
    if not runtime.ORG_AVAILABLE:
        return []

    matcher = get_org_matcher()
    search_requests: List[Tuple[str, str, str]] = []

    if cover_org_name:
        hint_tokens = ""
        if scope_hint == "unit":
            hint_tokens = "\u5355\u4f4d \u672c\u7ea7 \u533a\u7ea7\u5355\u4f4d"
        elif scope_hint == "department":
            hint_tokens = "\u4e3b\u7ba1\u90e8\u95e8 \u90e8\u95e8"
        search_requests.append(
            (
                "cover_field",
                cover_org_name,
                " ".join(token for token in (cover_org_label, hint_tokens, first_page_text) if token).strip(),
            )
        )

    search_requests.append(("content_fallback", filename, first_page_text))

    ranked: Dict[str, Tuple[Any, float, str]] = {}
    fetch_top_n = max(top_n, 5)
    for basis, query_filename, query_text in search_requests:
        for org, confidence in matcher.suggest_matches(
            query_filename,
            query_text,
            top_n=fetch_top_n,
        ):
            score = float(confidence or 0.0)
            current = ranked.get(org.id)
            if current is None:
                ranked[org.id] = (org, score, basis)
                continue

            current_priority = _MATCH_BASIS_PRIORITY.get(current[2], 0)
            next_priority = _MATCH_BASIS_PRIORITY.get(basis, 0)
            if (score, next_priority, len(getattr(org, "name", ""))) > (
                current[1],
                current_priority,
                len(getattr(current[0], "name", "")),
            ):
                ranked[org.id] = (org, score, basis)

    return sorted(
        ranked.values(),
        key=lambda item: (
            float(item[1]),
            _MATCH_BASIS_PRIORITY.get(item[2], 0),
            len(getattr(item[0], "name", "")),
        ),
        reverse=True,
    )[:top_n]


def _inspect_document_preflight(
    *,
    filename: str,
    preferred_year: Optional[str] = None,
    doc_type: Optional[str] = None,
    content: Optional[bytes] = None,
    pdf_path: Optional[Any] = None,
    top_n: int = 5,
) -> Dict[str, Any]:
    page_texts: List[str] = []
    if content is not None:
        page_texts = runtime.extract_pdf_page_texts_from_bytes(content, max_pages=3)
    elif pdf_path is not None:
        page_texts = runtime.extract_pdf_page_texts(pdf_path, max_pages=3)

    first_page_text = page_texts[0] if page_texts else ""
    cover = runtime.extract_cover_metadata(
        page_texts=page_texts,
        filename=filename,
        preferred_year=preferred_year,
        doc_type=doc_type,
    )
    report_year = cover.get("report_year")
    report_kind = str(cover.get("report_kind") or "unknown")
    normalized_doc_type = runtime.normalize_doc_type(
        doc_type,
        filename,
        report_kind=report_kind,
    )

    current = None
    suggestions: List[Dict[str, Any]] = []
    for index, (org, confidence, match_basis) in enumerate(
        _collect_org_matches(
            filename=filename,
            first_page_text=first_page_text,
            cover_org_name=str(cover.get("cover_org_name") or ""),
            cover_org_label=str(cover.get("cover_org_label") or ""),
            scope_hint=str(cover.get("scope_hint") or ""),
            top_n=top_n,
        )
    ):
        serialized = _serialize_match_candidate(org, confidence, match_basis)
        suggestions.append(serialized)
        if index == 0 and float(confidence or 0.0) >= 0.6:
            current = serialized

    return {
        "filename": filename,
        "report_year": report_year,
        "fiscal_year": str(report_year) if report_year else "",
        "doc_type": normalized_doc_type,
        "report_kind": report_kind,
        "cover_title": str(cover.get("cover_title") or ""),
        "cover_org_name": str(cover.get("cover_org_name") or ""),
        "cover_org_label": str(cover.get("cover_org_label") or ""),
        "scope_hint": str(cover.get("scope_hint") or ""),
        "current": current,
        "suggestions": suggestions,
    }


def _auto_match_organization(job_id: str, filename: str) -> Optional[dict]:
    if not runtime.ORG_AVAILABLE:
        return None
    try:
        pdf_path = runtime.find_first_pdf(runtime.UPLOAD_ROOT / job_id)
        preflight = _inspect_document_preflight(filename=filename, pdf_path=pdf_path)
        current = preflight.get("current") or {}
        organization_id = str(current.get("organization_id") or "").strip()
        confidence = float(current.get("confidence") or 0.0)
        if not organization_id:
            return None
        return runtime.set_job_organization(
            job_id,
            organization_id,
            match_type="auto",
            confidence=confidence,
        )
    except Exception:
        logger.exception("Failed to auto-match organization for job %s", job_id)
        return None


async def _handle_upload(
    file: UploadFile,
    *,
    selected_org: Optional[str] = None,
    fiscal_year: Optional[str] = None,
    doc_type: Optional[str] = None,
) -> dict:
    selected_org = _clean_optional_text(selected_org)
    fiscal_year = _clean_optional_text(fiscal_year)
    doc_type = _clean_optional_text(doc_type)

    org = _resolve_manual_org(selected_org)
    org_name = org["name"] if org else None

    uploaded = await runtime.store_upload_file(
        file,
        metadata={
            "organization_id": selected_org,
            "organization_name": org_name,
            "organization_match_type": "manual" if selected_org else None,
            "organization_match_confidence": 1.0 if selected_org else None,
            "fiscal_year": fiscal_year,
            "doc_type": doc_type,
        },
    )

    duplicate = runtime.find_duplicate_upload(
        checksum=str(uploaded.get("checksum") or ""),
        organization_id=selected_org,
        fiscal_year=fiscal_year,
        doc_type=doc_type,
        exclude_job_id=str(uploaded.get("job_id") or ""),
    )
    if duplicate is not None:
        runtime.delete_uploaded_job(str(uploaded.get("job_id") or ""))
        duplicate_filename = str(duplicate.get("filename") or "历史文件")
        duplicate_job_id = str(duplicate.get("job_id") or "")
        raise HTTPException(
            status_code=409,
            detail=f"检测到重复上传：{duplicate_filename}（任务 {duplicate_job_id}）",
        )

    org_binding: Optional[dict] = None
    if selected_org:
        org_binding = runtime.set_job_organization(
            uploaded["job_id"],
            selected_org,
            match_type="manual",
            confidence=1.0,
        )
    else:
        org_binding = _auto_match_organization(uploaded["job_id"], uploaded["filename"])

    uploaded["organization_id"] = (
        org_binding.get("organization_id") if org_binding else selected_org
    )
    uploaded["organization_name"] = (
        org_binding.get("organization_name") if org_binding else org_name
    )
    uploaded["organization_match_type"] = (
        org_binding.get("organization_match_type") if org_binding else None
    )
    uploaded["organization_match_confidence"] = (
        org_binding.get("organization_match_confidence") if org_binding else None
    )
    uploaded["fiscal_year"] = fiscal_year
    uploaded["doc_type"] = doc_type
    if uploaded.get("organization_id"):
        clear_department_stats_cache()
    return uploaded


@router.post("/api/documents/preflight")
async def preflight_document(
    file: UploadFile = File(...),
    fiscal_year: Optional[str] = Form(None),
    doc_type: Optional[str] = Form(None),
    api_key: str = Depends(runtime.verify_api_key),
):
    _ = api_key
    payload = _inspect_document_preflight(
        filename=file.filename or "",
        preferred_year=_clean_optional_text(fiscal_year),
        doc_type=_clean_optional_text(doc_type),
        content=await file.read(),
    )
    return payload


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    api_key: str = Depends(runtime.verify_api_key),
):
    _ = api_key
    return await _handle_upload(file)


@router.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    org_unit_id: Optional[str] = Form(None),
    org_id: Optional[str] = Form(None),
    fiscal_year: Optional[str] = Form(None),
    doc_type: Optional[str] = Form(None),
    api_key: str = Depends(runtime.verify_api_key),
):
    _ = api_key
    return await _handle_upload(
        file,
        selected_org=org_unit_id or org_id,
        fiscal_year=fiscal_year,
        doc_type=doc_type,
    )
