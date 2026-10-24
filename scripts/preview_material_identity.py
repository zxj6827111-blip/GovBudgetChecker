"""只读导出悬空材料及组织候选；候选不是已确认映射，不自动修复数据。"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import dotenv_values
import asyncpg


async def preview(output: Path):
    root = Path(__file__).resolve().parents[1]
    cfg = dotenv_values(root / ".env")
    catalog = json.loads((root / "data/organizations.json").read_text(encoding="utf-8-sig"))["organizations"]
    by_id = {o["id"]:o for o in catalog}
    conn = await asyncpg.connect(cfg["DATABASE_URL"])
    try:
        async with conn.transaction(readonly=True):
            slots = await conn.fetch("""SELECT s.id::text AS slot_id,s.slot_key,s.subject_org_id,
                s.subject_org_name,s.subject_org_code,s.subject_kind,s.material_scope,s.fiscal_year,s.report_kind,
                s.department_org_id,s.department_name,s.jurisdiction_org_id,s.jurisdiction_name,
                s.current_document_version_id,s.status FROM material_slots s
                WHERE NOT (s.subject_org_id = ANY($1::text[])) ORDER BY s.subject_org_id,s.fiscal_year""",list(by_id))
            groups={}
            for raw in slots:
                slot=dict(raw)
                group=groups.setdefault(slot["subject_org_id"],{"old_id":slot["subject_org_id"],"slots":[],"candidates":[]})
                group["slots"].append(slot)
            for group in groups.values():
                first=group["slots"][0]
                candidates=[o for o in catalog if o["name"]==first["subject_org_name"] and o["level"]==first["subject_kind"]]
                for candidate in candidates:
                    path=[];current=candidate;seen=set()
                    while current and current["id"] not in seen:
                        seen.add(current["id"]);path.insert(0,current["name"]);current=by_id.get(current.get("parent_id"))
                    group["candidates"].append({"id":candidate["id"],"name":candidate["name"],"level":candidate["level"],"code":candidate.get("code"),"path":path})
                ids=[s["slot_id"] for s in group["slots"]]
                versions=await conn.fetch("SELECT id,slot_id::text AS slot_id,document_id,file_hash FROM fiscal_document_versions WHERE slot_id::text=ANY($1::text[])",ids)
                reviews=await conn.fetch("SELECT id::text,slot_id::text,status,document_version_id,analysis_job_uuid,analysis_basis_token,completed_at,review_result->'issue_counts' AS issue_counts FROM review_sessions WHERE slot_id::text=ANY($1::text[])",ids)
                group["versions"]=[dict(r) for r in versions]
                group["reviews"]=[dict(r) for r in reviews]
            result={"read_only":True,"confirmation_required":True,"groups":list(groups.values())}
            fingerprint=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
            result["preview_sha256"]=fingerprint
    finally:
        await conn.close()
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    print(json.dumps({"output":str(output),"orphan_groups":len(result["groups"]),"preview_sha256":fingerprint},ensure_ascii=False))
    for group in result["groups"]:
        if "医疗保障" in str(group):
            print(json.dumps(group,ensure_ascii=False,default=str))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    asyncio.run(preview(parser.parse_args().output))
