"""按用户确认的映射修复归属。保留业务 ID 与结论，跨数据库/文件写入留恢复记录。"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
from typing import Any
import uuid

from src.schemas.material_slot import SlotIdentity


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()


def atomic_json(path: Path, payload: Any) -> str:
    raw=(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n").encode()
    with tempfile.NamedTemporaryFile(dir=path.parent,prefix=".identity-repair-",delete=False) as handle:
        handle.write(raw)
        temporary=Path(handle.name)
    os.replace(temporary,path)
    return hashlib.sha256(raw).hexdigest()


async def repair_material_identity(conn: Any, *, old_id: str, new_id: str, slot_ids: list[str],
    catalog: list[dict], upload_root: Path, links_file: Path, receipt_dir: Path, actor: str, validate_locked=None) -> dict:
    by_id={o["id"]:o for o in catalog}
    if old_id in by_id or new_id not in by_id or old_id==new_id:
        raise ValueError("只允许将已不在目录中的历史 ID 映射到明确存在的当前 ID")
    target=by_id[new_id]
    receipt_dir.mkdir(parents=True,exist_ok=False)
    receipt_path=receipt_dir/"receipt.json"
    receipt={"actor":actor,"old_id":old_id,"new_id":new_id,"slot_ids":slot_ids,
             "state":"preparing","files":[],"slots":[],"documents":[],"jobs":[]}
    written=[]
    database_committed=False
    try:
        async with conn.transaction(isolation="serializable"):
            # 身份锁→槽位→版本→会话→任务；旧、新身份都锁，防止归属修复和上传竞态。
            raw_slots=await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id",
                                      [uuid.UUID(v) for v in slot_ids])
            if len(raw_slots)!=len(slot_ids):raise ValueError("材料槽位已变化")
            slots=[dict(r) for r in raw_slots]
            for slot in slots:
                if slot["subject_org_id"]!=old_id or slot["subject_kind"]!=target["level"] or slot["subject_org_name"]!=target["name"]:
                    raise ValueError("主体身份不一致，拒绝按名称强行合并")
                new_key=SlotIdentity(new_id,slot["subject_kind"],slot["material_scope"],slot["report_kind"],slot["fiscal_year"],slot["mapping_key"]).slot_key
                slot["new_slot_key"]=new_key
            for key in sorted({s["slot_key"] for s in slots}|{s["new_slot_key"] for s in slots}):
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",key)
            locked=await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",[s["id"] for s in slots])
            for original,current in zip(slots,locked, strict=False):
                if digest({k:v for k,v in original.items() if k!="new_slot_key"})!=digest(dict(current)):
                    raise ValueError("材料在确认后已改变，拒绝继续")
                collision=await conn.fetchval("SELECT id FROM material_slots WHERE slot_key=$1 AND id<>$2",original["new_slot_key"],original["id"])
                if collision:raise ValueError("目标已有材料，拒绝合并")
            ids=[s["id"] for s in slots]
            versions=[dict(r) for r in await conn.fetch("SELECT * FROM fiscal_document_versions WHERE slot_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",ids)]
            reviews=[dict(r) for r in await conn.fetch("SELECT * FROM review_sessions WHERE slot_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",ids)]
            if any(r["status"]=="in_progress" for r in reviews):raise ValueError("材料正在复核，拒绝修改归属")
            version_ids=[v["id"] for v in versions]
            jobs=[dict(r) for r in await conn.fetch("SELECT id,job_uuid,status,metadata FROM analysis_jobs WHERE structured_document_version_id=ANY($1::numeric[]) ORDER BY id FOR UPDATE",version_ids)]
            if any(j["status"] not in ("uploaded","review_required","done","completed","degraded","error","failed","cancelled") for j in jobs):
                raise ValueError("材料存在运行中的任务，拒绝修改归属")
            if validate_locked is not None:
                await validate_locked()
            documents=[dict(r) for r in await conn.fetch("SELECT * FROM fiscal_documents WHERE id=ANY($1::int[]) ORDER BY id FOR UPDATE",list({v["document_id"] for v in versions}))]
            for doc in documents:
                outside=await conn.fetchval("SELECT count(*) FROM fiscal_document_versions WHERE document_id=$1 AND (slot_id IS NULL OR NOT(slot_id=ANY($2::uuid[])))",doc["id"],ids)
                if outside:raise ValueError("文档存在其他材料引用，拒绝跨材料修改")
                collision=await conn.fetchval("SELECT id FROM fiscal_documents WHERE org_unit_id=$1 AND fiscal_year IS NOT DISTINCT FROM $2 AND doc_type=$3 AND subject_org_id=$4 AND id<>$5",doc["org_unit_id"],doc["fiscal_year"],doc["doc_type"],new_id,doc["id"])
                if collision:raise ValueError("目标已有文档，拒绝合并")
            receipt["slots"]=slots
            receipt["documents"]=documents
            receipt["jobs"]=[{"id":j["id"],"job_uuid":j["job_uuid"],"metadata_sha256":digest(j["metadata"])} for j in jobs]
            receipt["frozen_before"]=digest({"versions":versions,"reviews":reviews})
            # 在写入前复制原始字节；恢复不会从新分析结果反推旧结论。
            planned=[]
            for job in jobs:
                jid=str(job["job_uuid"])
                if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",jid):raise ValueError("非法任务 ID")
                path=upload_root/jid/"status.json"
                if not path.exists():raise ValueError("任务状态文件不存在，不能只修数据库")
                raw=path.read_bytes()
                payload=json.loads(raw)
                if payload.get("status") not in ("uploaded","review_required","done","completed","degraded","error","failed","cancelled"):
                    raise ValueError("任务状态文件显示正在处理，拒绝覆盖")
                payload["organization_id"]=new_id
                payload["organization_name"]=target["name"]
                planned.append((path,raw,payload))
            if links_file.exists():
                raw=links_file.read_bytes()
                payload=json.loads(raw)
                for link in payload.get("links",[]):
                    if link.get("job_id") in {j["job_uuid"] for j in jobs}:
                        link["org_id"]=new_id
                planned.append((links_file,raw,payload))
            for index,(path,raw,payload) in enumerate(planned):
                backup=receipt_dir/f"file-{index}.before"
                backup.write_bytes(raw)
                receipt["files"].append({"path":str(path.resolve()),"backup":str(backup.resolve()),"before_sha256":hashlib.sha256(raw).hexdigest()})
            receipt["state"]="prepared"
            atomic_json(receipt_path,receipt)
            # 沿当前目录补齐展示层祖先；原复核会话和文件身份均不重建。
            ancestors=[]
            current=target
            seen=set()
            while current and current["id"] not in seen:
                seen.add(current["id"])
                ancestors.append(current)
                current=by_id.get(current.get("parent_id"))
            region=next((o for o in ancestors if o["level"] in ("district","city")),None)
            department=next((o for o in ancestors if o["level"]=="department"),None)
            for slot in slots:
                await conn.execute("""UPDATE material_slots SET subject_org_id=$2,slot_key=$3,
                    department_org_id=$4,department_name=$5,jurisdiction_org_id=$6,jurisdiction_name=$7,updated_at=NOW()
                    WHERE id=$1""",slot["id"],new_id,slot["new_slot_key"],(department or {}).get("id"),(department or {}).get("name"),(region or {}).get("id"),(region or {}).get("name"))
            await conn.execute("UPDATE fiscal_documents SET subject_org_id=$2 WHERE id=ANY($1::int[])",[d["id"] for d in documents],new_id)
            for job in jobs:
                old_meta=json.loads(job["metadata"]) if isinstance(job["metadata"],str) else job["metadata"]
                receipt["jobs"][jobs.index(job)]["before_organization"]={k:old_meta[k] for k in ("organization_id","organization_name") if k in old_meta}
                new_meta={**old_meta,"organization_id":new_id,"organization_name":target["name"]}
                await conn.execute("UPDATE analysis_jobs SET metadata=$2::jsonb WHERE id=$1",job["id"],json.dumps(new_meta,ensure_ascii=False))
                receipt["jobs"][jobs.index(job)]["after_metadata_sha256"]=digest(new_meta)
            for entry,(path,raw,payload) in zip(receipt["files"],planned, strict=False):
                if hashlib.sha256(path.read_bytes()).hexdigest()!=entry["before_sha256"]:
                    raise ValueError("任务状态文件被并发修改，拒绝覆盖")
                entry["after_sha256"]=atomic_json(path,payload)
                written.append(entry)
                receipt["state"]="files_writing_database_pending"
                atomic_json(receipt_path,receipt)
            after_versions=[dict(r) for r in await conn.fetch("SELECT * FROM fiscal_document_versions WHERE slot_id=ANY($1::uuid[]) ORDER BY id",ids)]
            after_reviews=[dict(r) for r in await conn.fetch("SELECT * FROM review_sessions WHERE slot_id=ANY($1::uuid[]) ORDER BY id",ids)]
            receipt["frozen_after"]=digest({"versions":after_versions,"reviews":after_reviews})
            if receipt["frozen_before"]!=receipt["frozen_after"]:raise ValueError("原件版本或复核记录发生非预期变化")
            if any(hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest()!=entry["after_sha256"] for entry in written):
                raise ValueError("归属文件被并发修改，拒绝提交")
            receipt["after_slots_sha256"]=digest([dict(r) for r in await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id",ids)])
            receipt["state"]="files_written_database_pending"
            atomic_json(receipt_path,receipt)
        database_committed=True
        receipt["state"]="committed"
        atomic_json(receipt_path,receipt)
        return receipt
    except Exception:
        if database_committed:
            # 提交后凭证写入失败不等于数据库回滚；不能把文件恢复成旧归属。
            raise RuntimeError("归属已提交，但最终凭证写入失败；请保留 pending 恢复记录")
        recovery=[]
        for entry in reversed(written):
            path=Path(entry["path"])
            if hashlib.sha256(path.read_bytes()).hexdigest()==entry["after_sha256"]:
                path.write_bytes(Path(entry["backup"]).read_bytes())
            else:recovery.append(entry["path"])
        receipt["state"]="manual_recovery_required" if recovery else "rolled_back"
        receipt["recovery_paths"]=recovery
        atomic_json(receipt_path,receipt)
        raise


async def rollback_material_identity(conn: Any, receipt_path: Path) -> dict:
    """只回滚本次确认的归属，任何后续业务修改都会阻止回滚而非被覆盖。"""
    from datetime import datetime
    receipt=json.loads(receipt_path.read_text())
    if receipt["state"]!="committed":raise ValueError("只允许回滚完整提交的修复记录")
    ids=[uuid.UUID(s["id"]) for s in receipt["slots"]]
    written=[]
    database_committed=False
    try:
        async with conn.transaction(isolation="serializable"):
            for key in sorted({s["slot_key"] for s in receipt["slots"]}|{s["new_slot_key"] for s in receipt["slots"]}):
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))",key)
            rows=await conn.fetch("SELECT * FROM material_slots WHERE id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",ids)
            if digest([dict(r) for r in rows])!=receipt["after_slots_sha256"]:
                raise ValueError("材料已有后续变更，拒绝覆盖")
            versions=[dict(r) for r in await conn.fetch("SELECT * FROM fiscal_document_versions WHERE slot_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",ids)]
            reviews=[dict(r) for r in await conn.fetch("SELECT * FROM review_sessions WHERE slot_id=ANY($1::uuid[]) ORDER BY id FOR UPDATE",ids)]
            if digest({"versions":versions,"reviews":reviews})!=receipt["frozen_after"]:
                raise ValueError("文件版本或复核记录已有变化，拒绝回滚")
            for job in receipt["jobs"]:
                raw=await conn.fetchval("SELECT metadata FROM analysis_jobs WHERE id=$1 FOR UPDATE",job["id"])
                meta=json.loads(raw) if isinstance(raw,str) else raw
                if digest(meta)!=job["after_metadata_sha256"]:raise ValueError("分析任务已有后续变更")
                for key in ("organization_id","organization_name"):meta.pop(key,None)
                meta.update(job["before_organization"])
                await conn.execute("UPDATE analysis_jobs SET metadata=$2::jsonb WHERE id=$1",job["id"],json.dumps(meta,ensure_ascii=False))
            for slot in receipt["slots"]:
                await conn.execute("""UPDATE material_slots SET subject_org_id=$2,slot_key=$3,
                    department_org_id=$4,department_name=$5,jurisdiction_org_id=$6,jurisdiction_name=$7,updated_at=$8
                    WHERE id=$1""",uuid.UUID(slot["id"]),slot["subject_org_id"],slot["slot_key"],slot["department_org_id"],slot["department_name"],slot["jurisdiction_org_id"],slot["jurisdiction_name"],datetime.fromisoformat(slot["updated_at"]))
            for doc in receipt["documents"]:
                owner=await conn.fetchval("SELECT subject_org_id FROM fiscal_documents WHERE id=$1 FOR UPDATE",doc["id"])
                if owner!=receipt["new_id"]:raise ValueError("文档归属已有后续变更")
                await conn.execute("UPDATE fiscal_documents SET subject_org_id=$2 WHERE id=$1",doc["id"],doc["subject_org_id"])
            for entry in receipt["files"]:
                path=Path(entry["path"])
                raw=path.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=entry["after_sha256"]:raise ValueError("文件已有后续变更，拒绝覆盖")
                path.write_bytes(Path(entry["backup"]).read_bytes())
                written.append((path,raw))
        database_committed=True
        receipt["state"]="reverted"
        atomic_json(receipt_path,receipt)
        return receipt
    except Exception:
        if database_committed:
            raise RuntimeError("归属回滚已提交，但最终凭证写入失败")
        for path,raw in written:
            entry=next(e for e in receipt["files"] if e["path"]==str(path))
            if hashlib.sha256(path.read_bytes()).hexdigest()==entry["before_sha256"]:
                path.write_bytes(raw)
        raise
