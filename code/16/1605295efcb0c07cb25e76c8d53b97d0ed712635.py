"""旧共享接口的兼容投影，仅从已封存有效快照生成，随结果原子发布。"""

from collections import defaultdict
from pathlib import Path
import hashlib
import json
import os
import uuid

from src.engine.fact_context import FactContext, operand_rejection_reason
from src.services.analysis_fact_snapshot import AnalysisFactSnapshot, canonical
from src.services.ps_schema_sync import PSSharedSchemaSync

PROJECTION_VERSION = "effective-projection-v1-snapshot-only"


def projection_identity(snapshot, job_id):
    revision = snapshot.summary()["revision_id"]
    if not str(job_id) or ":" in str(job_id):
        raise ValueError("invalid projection job identity")
    scope_key = f"v2:{job_id}:{revision}"
    return {
        "report_id": str(uuid.uuid5(uuid.NAMESPACE_URL, "govbudgetchecker:" + scope_key)),
        "scope_key": scope_key,
        "fact_snapshot": snapshot.summary(),
        "compatibility": "v2",
        "projection_version": PROJECTION_VERSION,
    }


def snapshot_projection(snapshot):
    # 同时验证原始/有效摘要；不能把未封存的内存事实当成正式版本。
    snapshot = AnalysisFactSnapshot.from_dict(snapshot.to_dict())
    context = FactContext.from_dict(snapshot.to_dict()["effective_context"])
    binding = snapshot.summary()
    grouped = defaultdict(list)
    for fact in context.facts:
        grouped[fact.table_code or "UNKNOWN"].append(fact)
    tables, items = [], []
    for code, facts in sorted(grouped.items()):
        ordered = sorted(
            facts, key=lambda f: canonical([f.identity_key(), f.physical_source_key(), f.fact_uid])
        )
        rows, instances, pages = [], set(), set()
        for index, fact in enumerate(ordered):
            saved = fact.to_dict()
            identity = json.loads(canonical(fact.identity_key()))
            refs = saved["extra_dims"].get("evidence_refs", [])
            pages.update(
                ref["physical_page"] for ref in refs if isinstance(ref.get("physical_page"), int)
            )
            if fact.instance_uid:
                instances.add(fact.instance_uid)
            meta = {
                "fact_snapshot": binding,
                "projection_version": PROJECTION_VERSION,
                "fact": saved,
                "business_identity": identity,
                "identity_sha256": hashlib.sha256(canonical(identity).encode()).hexdigest(),
                "source_row_order": fact.row_order,
                "row_index_role": "snapshot_display_index",
                "operand_rejection_reason": operand_rejection_reason(fact, context.org_name),
            }
            value = saved["normalized_value"]
            # 金额始终是原始 Decimal 的十进制字符串，未知/空白保持 None。
            item = {
                "id": str(
                    uuid.uuid5(uuid.NAMESPACE_URL, snapshot.digest + f":item:{code}:{index}")
                ),
                "table_key": code,
                "row_index": index,
                "class_code": None,
                "type_code": None,
                "item_code": fact.subject_code,
                "item_name": fact.subject_name,
                "values_json": {fact.indicator: value, "_meta": meta},
            }
            items.append(item)
            rows.append(
                {
                    "row_index": index,
                    "instance_uid": fact.instance_uid,
                    "source_row_order": fact.row_order,
                    "fact_uid": fact.fact_uid,
                    "cells": [
                        {
                            "col_index": 0,
                            "raw_text": fact.raw_value,
                            "normalized_text": fact.raw_value,
                            "numeric_value": value,
                            "unit": fact.unit,
                            "is_header": False,
                            "confidence": fact.confidence,
                            "evidence_refs": refs,
                            "indicator": fact.indicator,
                            "subject_name": fact.subject_name,
                            "subject_code": fact.subject_code,
                        }
                    ],
                    "_meta": meta,
                }
            )
        tables.append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, snapshot.digest + f":table:{code}")),
                "table_key": code,
                "table_title": code,
                "page_numbers": sorted(pages),
                "row_count": len(rows),
                "col_count": 1,
                "data_json": {
                    "source_system": "GovBudgetChecker",
                    "table_key": code,
                    "table_title": code,
                    "rows": rows,
                    "instance_uids": sorted(instances),
                    "fact_snapshot": binding,
                    "projection_version": PROJECTION_VERSION,
                    "view_kind": "effective_fiscal_facts",
                },
            }
        )
    return context, tables, items


async def publish_effective_projection(conn, snapshot, payload):
    """由结果发布事务调用；任一投影失败必须回滚正式结果及快照。"""
    if not conn.is_in_transaction():
        raise ValueError("effective projection requires the result publication transaction")
    context, tables, items = snapshot_projection(snapshot)
    run = await conn.fetchrow(
        "SELECT document_version_id, source_file_hash FROM parse_runs WHERE parse_run_uid=$1",
        context.parse_run_uid,
    )
    if run is None or run["source_file_hash"] != context.file_hash:
        raise ValueError("effective projection parse source mismatch")
    version_id = run["document_version_id"]
    supplied = (payload.get("structured_ingest") or {}).get("document_version_id")
    if supplied is not None and supplied != version_id:
        raise ValueError("effective projection document version mismatch")
    # 旧历史报告不改写；同一原件的 v2 当前视图独立保存，历史值仍在追加快照中。
    sync = PSSharedSchemaSync(conn)
    owner = context.org_name or f"待识别主体:{context.file_hash[:16]}"
    scope = sync.resolve_scope(org_name=owner, organization_id=None)
    department_id = await sync._ensure_department(
        str(scope["department_name"]), preferred_code=scope.get("department_code")
    )
    unit_id = await sync._ensure_unit(
        department_id, str(scope["unit_name"]), preferred_code=scope.get("unit_code")
    )
    job_dir = Path(os.getenv("UPLOAD_DIR", "uploads")) / str(payload["job_id"])
    from api.runtime import find_first_pdf

    pdf_path = find_first_pdf(job_dir)
    if hashlib.sha256(pdf_path.read_bytes()).hexdigest() != context.file_hash:
        raise ValueError("effective projection original hash mismatch")
    report_type = sync._normalize_report_type(context.report_kind) or "UNKNOWN"
    projection = projection_identity(snapshot, payload["job_id"])
    report_id = await conn.fetchval(
        """INSERT INTO org_dept_annual_report
           (id,department_id,unit_id,year,report_type,file_name,file_path,file_hash,file_size,scope_key)
           VALUES ($1::uuid,$2,$3,$4,$5,$6,$7,$8,$9,$10)
           ON CONFLICT (department_id,unit_id,COALESCE(year,-1),report_type,scope_key)
           DO UPDATE SET file_name=EXCLUDED.file_name,file_path=EXCLUDED.file_path
           RETURNING id""",
        projection["report_id"],
        department_id,
        unit_id,
        context.fiscal_year,
        report_type,
        pdf_path.name,
        str(pdf_path),
        context.file_hash,
        pdf_path.stat().st_size,
        projection["scope_key"],
    )
    if str(report_id) != projection["report_id"]:
        raise ValueError("snapshot report identity collision")
    await conn.execute("DELETE FROM org_dept_table_data WHERE report_id=$1", report_id)
    await conn.execute("DELETE FROM org_dept_line_items WHERE report_id=$1", report_id)
    for table in tables:
        await conn.execute(
            """INSERT INTO org_dept_table_data
            (report_id,department_id,year,report_type,table_key,table_title,page_numbers,row_count,col_count,data_json,id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11::uuid)""",
            report_id,
            department_id,
            context.fiscal_year,
            report_type,
            table["table_key"],
            table["table_title"],
            table["page_numbers"],
            table["row_count"],
            table["col_count"],
            canonical(table["data_json"]),
            table["id"],
        )
    for item in items:
        await conn.execute(
            """INSERT INTO org_dept_line_items
            (report_id,department_id,year,report_type,table_key,row_index,class_code,type_code,item_code,item_name,values_json,id)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11::jsonb,$12::uuid)""",
            report_id,
            department_id,
            context.fiscal_year,
            report_type,
            item["table_key"],
            item["row_index"],
            item["class_code"],
            item["type_code"],
            item["item_code"],
            item["item_name"],
            canonical(item["values_json"]),
            item["id"],
        )
    return {
        "status": "done",
        "report_id": str(report_id),
        "document_version_id": version_id,
        "fact_snapshot": snapshot.summary(),
        "tables_count": len(tables),
        "facts_count": len(items),
    }
