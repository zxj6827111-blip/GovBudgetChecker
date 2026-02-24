"""Upload endpoints."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile

from api import runtime

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/upload")
async def upload_pdf(
    file: UploadFile = File(...),
    api_key: str = Depends(runtime.verify_api_key),
):
    _ = api_key
    return await runtime.store_upload_file(file)


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
    uploaded = await runtime.store_upload_file(file)
    selected_org = org_unit_id or org_id
    if selected_org and runtime.ORG_AVAILABLE:
        try:
            storage = runtime.require_org_storage()
            storage.link_job(uploaded["job_id"], selected_org, match_type="manual", confidence=1.0)
        except Exception:
            logger.exception("Failed to link uploaded job to organization")

    uploaded["organization_id"] = selected_org
    uploaded["fiscal_year"] = fiscal_year
    uploaded["doc_type"] = doc_type
    return uploaded

