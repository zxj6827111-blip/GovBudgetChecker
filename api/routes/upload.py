"""Upload endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from api import runtime
from src.services.org_matcher import get_org_matcher

router = APIRouter()
logger = logging.getLogger(__name__)


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


def _auto_match_organization(job_id: str, filename: str) -> Optional[dict]:
    if not runtime.ORG_AVAILABLE:
        return None
    try:
        pdf_path = runtime.find_first_pdf(runtime.UPLOAD_ROOT / job_id)
        first_page_text = runtime.extract_pdf_first_page_text(pdf_path)
        matched_org, confidence = get_org_matcher().match(filename, first_page_text)
        if matched_org is None:
            return None
        return runtime.set_job_organization(
            job_id,
            matched_org.id,
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
    return uploaded


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
