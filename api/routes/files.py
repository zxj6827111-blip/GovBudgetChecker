"""File streaming endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api import runtime

router = APIRouter()


@router.get("/api/files/{job_id}/source")
async def get_source_pdf(job_id: str):
    job_dir = runtime.UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id 不存在")
    try:
        pdf_path = runtime.find_first_pdf(job_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="source pdf not found") from e
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=pdf_path.name)

