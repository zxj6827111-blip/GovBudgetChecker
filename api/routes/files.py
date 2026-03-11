"""File streaming and preview endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response

from api import runtime

router = APIRouter()


def _resolve_source_pdf(job_id: str) -> Path:
    job_dir = runtime.UPLOAD_ROOT / job_id
    if not job_dir.exists():
        raise HTTPException(status_code=404, detail="job_id does not exist")
    try:
        return runtime.find_first_pdf(job_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="source pdf not found") from exc


def _parse_bbox_query(raw_bbox: Optional[str]) -> Optional[List[float]]:
    if not raw_bbox:
        return None
    parts = [item.strip() for item in raw_bbox.split(",")]
    if len(parts) != 4:
        return None
    try:
        bbox = [float(item) for item in parts]
    except Exception:
        return None
    if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return None
    return bbox


def _clamp_bbox(
    bbox: Optional[List[float]],
    *,
    page_width: float,
    page_height: float,
) -> Optional[List[float]]:
    if not bbox:
        return None
    x0 = min(max(float(bbox[0]), 0.0), page_width)
    y0 = min(max(float(bbox[1]), 0.0), page_height)
    x1 = min(max(float(bbox[2]), 0.0), page_width)
    y1 = min(max(float(bbox[3]), 0.0), page_height)
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _expand_bbox(
    bbox: Optional[List[float]],
    *,
    page_width: float,
    page_height: float,
    padding: float,
) -> List[float]:
    if not bbox:
        return [0.0, 0.0, page_width, page_height]
    return [
        max(0.0, bbox[0] - padding),
        max(0.0, bbox[1] - padding),
        min(page_width, bbox[2] + padding),
        min(page_height, bbox[3] + padding),
    ]


def _render_preview_png(
    pdf_path: Path,
    *,
    page_number: int,
    bbox: Optional[List[float]],
    padding: float,
    scale: float,
) -> bytes:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:
        raise HTTPException(status_code=503, detail="PyMuPDF is not installed") from exc

    with fitz.open(pdf_path) as document:
        if page_number < 1 or page_number > document.page_count:
            raise HTTPException(status_code=404, detail="page out of range")

        page = document.load_page(page_number - 1)
        page_rect = page.rect
        clamped_bbox = _clamp_bbox(
            bbox,
            page_width=float(page_rect.width),
            page_height=float(page_rect.height),
        )
        clip_bbox = _expand_bbox(
            clamped_bbox,
            page_width=float(page_rect.width),
            page_height=float(page_rect.height),
            padding=padding,
        )

        if clamped_bbox:
            page.draw_rect(fitz.Rect(*clamped_bbox), color=(1, 0, 0), width=2.5, overlay=True)

        matrix = fitz.Matrix(scale, scale)
        pixmap = page.get_pixmap(matrix=matrix, clip=fitz.Rect(*clip_bbox), alpha=False)
        return pixmap.tobytes("png")


@router.get("/api/files/{job_id}/source")
async def get_source_pdf(job_id: str):
    pdf_path = _resolve_source_pdf(job_id)
    return FileResponse(str(pdf_path), media_type="application/pdf", filename=pdf_path.name)


@router.get("/api/files/{job_id}/preview")
async def get_source_preview(
    job_id: str,
    page: int = Query(default=1, ge=1),
    bbox: Optional[str] = Query(default=None),
    padding: float = Query(default=24.0, ge=0.0, le=200.0),
    scale: float = Query(default=2.0, ge=0.5, le=4.0),
):
    pdf_path = _resolve_source_pdf(job_id)
    parsed_bbox = _parse_bbox_query(bbox)
    png_bytes = _render_preview_png(
        pdf_path,
        page_number=page,
        bbox=parsed_bbox,
        padding=padding,
        scale=scale,
    )
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-store"},
    )
