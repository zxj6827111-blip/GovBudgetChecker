"""收到 PDF 即登记材料版本；不以解析成功作为材料到达的条件。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.db.connection import DatabaseConnection
from src.services.material_slot_resolver import decide_slot_allocation, load_org_records
from src.services.material_slot_service import MaterialSlotService
from src.services.material_slot_status import ANALYSIS_NOT_STARTED, REVIEW_NONE
from src.services.structured_ingest_runner import ensure_structured_ingest_ready, _ensure_document_version


async def register_uploaded_material(job_id: str, pdf_path: Path, metadata: dict[str, Any]) -> dict:
    if not await ensure_structured_ingest_ready():
        return {"status": "skipped", "reason": "database_unavailable", "bound": False}
    decision = decide_slot_allocation(metadata=metadata, org_records=load_org_records(),
                                     checksum=metadata.get("checksum"))
    if decision.identity is None or not decision.identity.is_resolved:
        return {"status": "mapping_required", "reason": decision.reason, "bound": False}
    conn = await DatabaseConnection.acquire()
    try:
        async with conn.transaction():
            service = MaterialSlotService(conn)
            # 与复核/版本切换保持相同锁序：身份锁→槽位行→文件版本。
            await service._lock_slot_identity(decision.identity.slot_key)
            await service._ensure_slot_row(identity=decision.identity, decision=decision,
                caliber="unknown", due_at=None, expected_source_url=None)
            slot = await conn.fetchrow("SELECT id,current_document_version_id FROM material_slots WHERE slot_key=$1 FOR UPDATE",
                                      decision.identity.slot_key)
            document = await _ensure_document_version(conn,
                org_name=str(metadata.get("organization_name") or ""),
                fiscal_year=decision.identity.fiscal_year,
                doc_type=str(metadata.get("doc_type") or metadata.get("report_kind") or "unknown"),
                subject_org_id=decision.identity.subject_org_id,
                checksum=str(metadata["checksum"]),storage_key=f"{job_id}/{pdf_path.name}",
                storage_backend="filesystem",original_filename=pdf_path.name,
                file_size_bytes=pdf_path.stat().st_size,content_type="application/pdf")
            version_id = document["document_version_id"]
            existing = await conn.fetchrow("SELECT slot_id FROM fiscal_document_versions WHERE id=$1", version_id)
            if existing and existing["slot_id"]:
                # 重放登记不能重置已完成复核；新版本才走分配与旧结论失效路径。
                if str(existing["slot_id"]) != str(slot["id"]):
                    raise ValueError("material_upload_identity_conflict")
            await service.bind_document_version(slot["id"], version_id)
            final = await service.get_slot_by_id(slot["id"])
            if final["current_document_version_id"] != slot["current_document_version_id"]:
                await service.refresh_status(slot["id"], analysis_state=ANALYSIS_NOT_STARTED, review_state=REVIEW_NONE)
            allocation = {"status":"resolved","slot_id":str(slot["id"]),"bound":True}
            return {**document,"status":"registered","material_slot":allocation}
    finally:
        await DatabaseConnection.release(conn)
