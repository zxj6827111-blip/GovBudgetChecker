"""Read-only APIs for the PS shared schema."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder

from src.db.connection import DatabaseConnection
from src.services.ps_shared_query_service import PSSharedQueryService
from src.services.structured_ingest_runner import ensure_structured_ingest_ready

router = APIRouter()


async def _execute_ps_query(
    callback: Callable[[PSSharedQueryService], Awaitable[Dict[str, Any]]],
) -> Dict[str, Any]:
    if not await ensure_structured_ingest_ready():
        raise HTTPException(
            status_code=503,
            detail="structured ingest database unavailable",
        )

    conn = await DatabaseConnection.acquire()
    try:
        service = PSSharedQueryService(conn)
        payload = await callback(service)
        return jsonable_encoder(payload)
    finally:
        await DatabaseConnection.release(conn)


@router.get("/api/ps/reports")
async def list_ps_reports(
    department_id: Optional[str] = Query(default=None),
    unit_id: Optional[str] = Query(default=None),
    year: Optional[int] = Query(default=None),
    report_type: Optional[str] = Query(default=None),
    keyword: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    return await _execute_ps_query(
        lambda service: service.list_reports(
            department_id=department_id,
            unit_id=unit_id,
            year=year,
            report_type=report_type,
            keyword=keyword,
            limit=limit,
            offset=offset,
        )
    )


@router.get("/api/ps/reports/{report_id}")
async def get_ps_report(report_id: str):
    payload = await _execute_ps_query(lambda service: service.get_report(report_id))
    if not payload:
        raise HTTPException(status_code=404, detail="report not found")
    return payload


@router.get("/api/ps/reports/{report_id}/tables")
async def get_ps_report_tables(
    report_id: str,
    table_key: Optional[str] = Query(default=None),
    include_data: bool = Query(default=True),
):
    return await _execute_ps_query(
        lambda service: service.list_report_tables(
            report_id,
            table_key=table_key,
            include_data=include_data,
        )
    )


@router.get("/api/ps/reports/{report_id}/line-items")
async def get_ps_report_line_items(
    report_id: str,
    table_key: Optional[str] = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
):
    return await _execute_ps_query(
        lambda service: service.list_report_line_items(
            report_id,
            table_key=table_key,
            limit=limit,
            offset=offset,
        )
    )
