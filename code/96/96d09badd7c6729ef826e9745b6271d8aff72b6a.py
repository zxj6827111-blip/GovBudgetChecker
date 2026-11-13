"""真实隔离 API/Worker/DB 读取回归；已知材料与合成控制不充当盲测。"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location("reading_isolated_service",ROOT/"scripts/run_isolated_service_validation.py")
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original=module.database_receipt

async def capture(dsn,job_id,ingest=None,target_fact=None):
    result=await original(dsn,job_id,ingest,target_fact)
    if ingest and ingest.get("parse_run_id"):
        import asyncpg
        from src.services.parse_artifact_store import load_parse_artifact,load_parse_facts,load_parse_evidence
        conn=await asyncpg.connect(dsn)
        try:
            run_id=ingest["parse_run_id"]
            artifact=await load_parse_artifact(conn,run_id)
            facts=await load_parse_facts(conn,run_id)
            evidence=await load_parse_evidence(conn,run_id)
            revision=(result.get("job") or {}).get("analysis_revision")
            out=BASE/"service_database"/job_id/str(revision)
            out.mkdir(parents=True,exist_ok=True)
            for name,data in [("artifact",artifact.to_dict()),("facts",facts),("evidence",evidence),("database_receipt",result),("ingest",ingest)]:
                (out/f"{name}.json").write_text(json.dumps(data,ensure_ascii=False,default=str,indent=2))
        finally:
            await conn.close()
    return result

module.database_receipt=capture
documents=json.loads((ROOT/"outputs/reading_recovery_20261015/known_unresolved_cases.json").read_text())
originals=[d for d in documents if d["reading_gaps"]]
inputs=BASE/"service_inputs";inputs.mkdir(exist_ok=True)
for item in originals[1:]:
    target=inputs/item["filename"]
    if not target.exists():target.symlink_to(item["source_pdf"])
correction=next(d for d in originals if "精神卫生" in d["filename"])
sys.argv=[str(ROOT/"scripts/run_isolated_service_validation.py"),"--output",str(BASE/"service"),
    "--final-pdf",originals[0]["source_pdf"],"--additional-pdfs-dir",str(inputs),
    "--native-correction-pdf",correction["source_pdf"]]
raise SystemExit(module.main())
