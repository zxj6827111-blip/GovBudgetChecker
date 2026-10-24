"""年度任务内归属核查。只修复可证明为同一主体的ID或缺失祖先，不猜名称。"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from typing import Any
import uuid

from fastapi import HTTPException

from src.services.material_identity_repair import atomic_json, digest, repair_material_identity
from src.services.reporting_task_model import ancestors, jurisdiction
from src.schemas.material_slot import SlotIdentity


async def cases(conn: Any, task: dict, catalog: list[dict], *, include_history: bool = False, visible_ids: list[str] | None = None) -> list[dict]:
    rows = []
    for part in ([None] if include_history else task["components"]):
        period = "TRUE" if part is None else "fiscal_year=$1 AND report_kind=$2"
        params = [] if part is None else [part["fiscal_year"], part["report_kind"]]
        scope_clause = ""
        if visible_ids is not None:
            params.append(visible_ids)
            n=len(params)
            scope_clause=f"AND (subject_org_id=ANY(${n}::text[]) OR department_org_id=ANY(${n}::text[]) OR jurisdiction_org_id=ANY(${n}::text[]))"
        rows += [dict(r) for r in await conn.fetch(f"""SELECT id::text AS slot_id,subject_org_id,subject_org_name,subject_kind,
            jurisdiction_org_id,department_org_id,material_scope,caliber,mapping_key,caliber_conflict_candidate,fiscal_year,report_kind
            FROM material_slots WHERE {period} AND
            (jurisdiction_org_id IS NULL OR department_org_id IS NULL OR mapping_key<>'' OR COALESCE(caliber_conflict_candidate,'')<>'')
            {scope_clause} ORDER BY subject_org_name,id""", *params)]
    index = {str(o["id"]): o for o in catalog}
    grouped = defaultdict(list)
    for row in rows:
        # 当前目录已明确属于其他地区的材料，不展示成待归入本区。
        region = jurisdiction(row["subject_org_id"], catalog)
        if region is not None and region != task["jurisdiction_org_id"]:
            continue
        grouped[row["subject_org_id"]].append(row)
    result = []
    for org_id, slots in grouped.items():
        first = slots[0]
        options = [{"id": str(o["id"]), "name": o["name"]} for o in catalog if o["name"] == first["subject_org_name"] and o["level"] == first["subject_kind"] and jurisdiction(str(o["id"]), catalog) == task["jurisdiction_org_id"]]
        identity_uncertain = any(r.get("mapping_key") or r.get("caliber_conflict_candidate") or r["material_scope"] == "unknown" for r in slots)
        kind = "manual" if identity_uncertain else "ancestors" if org_id in index else "historical_id"
        result.append({"old_id": org_id, "name": first["subject_org_name"], "kind": kind, "slot_ids": [r["slot_id"] for r in slots],
            "materials": [{"slot_id": r["slot_id"], "fiscal_year": r["fiscal_year"], "report_kind": r["report_kind"]} for r in slots],
            "candidates": options, "in_task": all(any(r["fiscal_year"]==p["fiscal_year"] and r["report_kind"]==p["report_kind"] for p in task["components"]) for r in slots),
            "disposition": "待人工核查" if kind=="manual" or not options else "待证据确认后预览", "statistics_basis": "需按当前发布名册及有效版本核对", "reason": "身份或口径未确认，保留人工核查" if kind == "manual" else "当前主体明确，仅核对缺失祖先" if kind == "ancestors" else "旧主体ID不在当前目录；需明确确认同一主体映射"})
    if include_history and visible_ids is None:
        legacy = await conn.fetch("""SELECT j.job_uuid,j.filename FROM analysis_jobs j
            LEFT JOIN fiscal_document_versions v ON v.id=j.structured_document_version_id
            LEFT JOIN material_slots s ON s.id=v.slot_id WHERE s.id IS NULL ORDER BY j.id""")
        result += [{"old_id": "job:"+r["job_uuid"], "name": r["filename"], "kind": "legacy", "slot_ids": [], "materials": [], "candidates": [], "in_task": False,
            "disposition": "待补齐材料链路", "included_in_region_statistics": False, "reason": "旧分析任务没有完整槽位及文件版本链路；不能直接套用历史ID修复"} for r in legacy]
    return result


async def preview(conn: Any, task: dict, catalog: list[dict], old_id: str, new_id: str, slot_ids: list[str]) -> dict:
    try:
        keys = sorted({uuid.UUID(v) for v in slot_ids}, key=str)
    except ValueError as exc:
        raise HTTPException(422, "材料ID不合法") from exc
    if not keys or len(keys) != len(slot_ids):
        raise HTTPException(422, "请选择不重复的具体材料")
    allowed = await cases(conn, task, catalog)
    case = next((c for c in allowed if c["old_id"] == old_id and set(slot_ids).issubset(c["slot_ids"])), None)
    if not case or case["kind"] == "manual" or new_id not in {c["id"] for c in case["candidates"]}:
        raise HTTPException(409, "没有可安全应用的明确同一主体映射，不能按名称强行归属")
    if case["kind"] == "ancestors" and old_id != new_id:
        raise HTTPException(409, "目录中的现存主体不能通过此入口重新指派")
    slots = [dict(r) for r in await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id", keys)]
    for slot in slots:
        target_key = SlotIdentity(new_id, slot["subject_kind"], slot["material_scope"], slot["report_kind"], slot["fiscal_year"], slot["mapping_key"]).slot_key
        collision = await conn.fetchval("SELECT id FROM material_slots WHERE slot_key=$1 AND id<>$2", target_key, slot["id"])
        if collision:
            raise HTTPException(409, "目标已有材料，不能自动合并")
    versions = [dict(r) for r in await conn.fetch("SELECT * FROM fiscal_document_versions WHERE slot_id=ANY($1::uuid[]) ORDER BY id", keys)]
    reviews = [dict(r) for r in await conn.fetch("SELECT * FROM review_sessions WHERE slot_id=ANY($1::uuid[]) ORDER BY id", keys)]
    jobs = [dict(r) for r in await conn.fetch("SELECT id,job_uuid,status,analysis_revision,updated_at FROM analysis_jobs WHERE structured_document_version_id=ANY($1::numeric[]) ORDER BY id", [v["id"] for v in versions])]
    if any(r["status"] == "in_progress" for r in reviews) or any(j["status"] not in {"uploaded", "review_required", "done", "completed", "degraded", "error", "failed", "cancelled"} for j in jobs):
        raise HTTPException(409, "存在运行中的分析或复核，暂不能修复")
    token = digest({"old_id": old_id, "new_id": new_id, "slots": slots, "versions": versions, "reviews": reviews, "jobs": jobs, "catalog": catalog})
    return {"preview_token": token, "kind": case["kind"], "old_id": old_id, "new_id": new_id, "slot_ids": slot_ids,
            "materials": case["materials"], "version_count": len(versions), "review_count": len(reviews), "job_count": len(jobs),
            "warning": "仅修复已确认的同一主体身份或缺失祖先；已发布年度名单仍保留原快照，需核查是否修订。"}


async def apply(conn: Any, task: dict, catalog: list[dict], *, old_id: str, new_id: str, slot_ids: list[str], token: str,
                note: str, actor: str, upload_root: Path, links_file: Path, receipt_root: Path) -> dict:
    current = await preview(conn, task, catalog, old_id, new_id, slot_ids)
    if current["preview_token"] != token:
        raise HTTPException(409, "材料、目录或复核记录已变化，请重新预览")
    receipt_id = str(uuid.uuid4())
    directory = receipt_root / receipt_id
    async def validate_locked():
        again = await preview(conn, task, catalog, old_id, new_id, slot_ids)
        if again["preview_token"] != token:
            raise HTTPException(409, "材料、版本或复核记录已变化，请重新预览")
    if current["kind"] == "historical_id":
        # 既有修复服务持有数据库事务，并以凭证补偿跨数据库/文件写入；不得外套事务。
        receipt = await repair_material_identity(conn, old_id=old_id, new_id=new_id, slot_ids=slot_ids,
            catalog=catalog, upload_root=upload_root, links_file=links_file, receipt_dir=directory, actor=actor, validate_locked=validate_locked)
        receipt["confirmation_note"] = note
        atomic_json(directory / "receipt.json", receipt)
    else:
        keys = sorted({uuid.UUID(v) for v in slot_ids}, key=str)
        directory.mkdir(parents=True, exist_ok=False)
        receipt = {"state": "preparing", "kind": "ancestors", "actor": actor, "confirmation_note": note, "slots": []}
        path = directory / "receipt.json"
        async with conn.transaction(isolation="serializable"):
            raw = await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id", keys)
            for key in sorted({r["slot_key"] for r in raw}):
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", key)
            locked = [dict(r) for r in await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", keys)]
            versions = [dict(r) for r in await conn.fetch("SELECT * FROM fiscal_document_versions WHERE slot_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", keys)]
            reviews = [dict(r) for r in await conn.fetch("SELECT * FROM review_sessions WHERE slot_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", keys)]
            jobs = [dict(r) for r in await conn.fetch("SELECT id,job_uuid,status,analysis_revision,updated_at FROM analysis_jobs WHERE structured_document_version_id=ANY($1::numeric[]) ORDER BY id FOR UPDATE", [v["id"] for v in versions])]
            receipt["frozen_business_sha256"] = digest({"versions": versions, "reviews": reviews, "jobs": jobs})
            again = await preview(conn, task, catalog, old_id, new_id, slot_ids)
            if again["preview_token"] != token:
                raise HTTPException(409, "材料已变化，请重新预览")
            chain = ancestors(new_id, catalog)
            region = next(o for o in chain if o["level"] in {"city", "district"})
            department = next((o for o in chain if o["level"] == "department"), None)
            receipt["slots"] = locked
            receipt["state"] = "prepared"
            atomic_json(path, receipt)
            await conn.execute("""UPDATE material_slots SET jurisdiction_org_id=$2,jurisdiction_name=$3,
                department_org_id=$4,department_name=$5,updated_at=NOW() WHERE id=ANY($1::uuid[])""", keys,
                region["id"], region["name"], (department or {}).get("id"), (department or {}).get("name"))
            receipt["after_slots_sha256"] = digest([dict(r) for r in await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id", keys)])
            receipt["state"] = "database_pending"
            atomic_json(path, receipt)
        receipt["state"] = "committed"
        atomic_json(path, receipt)
    return {"receipt_id": receipt_id, "state": receipt["state"], "slot_count": len(slot_ids), "note": "原文件、版本和复核记录保留，请核查已发布名册是否需修订。"}


async def rollback_ancestors(conn: Any, receipt_path: Path) -> None:
    """运维恢复入口；后续业务写入会阻止回滚，不能覆盖新修改。"""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("kind") != "ancestors" or receipt.get("state") != "committed":
        raise ValueError("恢复凭证不是已提交的祖先修复")
    keys = [uuid.UUID(r["id"]) for r in receipt["slots"]]
    async with conn.transaction():
        for key in sorted(r["slot_key"] for r in receipt["slots"]):
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", key)
        rows = [dict(r) for r in await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", keys)]
        if digest(rows) != receipt["after_slots_sha256"]:
            raise ValueError("材料已有后续变更，拒绝覆盖")
        versions = [dict(r) for r in await conn.fetch("SELECT * FROM fiscal_document_versions WHERE slot_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", keys)]
        reviews = [dict(r) for r in await conn.fetch("SELECT * FROM review_sessions WHERE slot_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE", keys)]
        jobs = [dict(r) for r in await conn.fetch("SELECT id,job_uuid,status,analysis_revision,updated_at FROM analysis_jobs WHERE structured_document_version_id=ANY($1::numeric[]) ORDER BY id FOR UPDATE", [v["id"] for v in versions])]
        if digest({"versions": versions, "reviews": reviews, "jobs": jobs}) != receipt["frozen_business_sha256"]:
            raise ValueError("文件版本、分析或复核已有后续变更，拒绝覆盖")
        from datetime import datetime
        for row in receipt["slots"]:
            await conn.execute("UPDATE material_slots SET jurisdiction_org_id=$2,jurisdiction_name=$3,department_org_id=$4,department_name=$5,updated_at=$6 WHERE id=$1",
                uuid.UUID(row["id"]), row["jurisdiction_org_id"], row["jurisdiction_name"], row["department_org_id"], row["department_name"], datetime.fromisoformat(row["updated_at"]))
    receipt["state"] = "rolled_back"
    atomic_json(receipt_path, receipt)
