"""使用现有隔离服务验证器，保存待比对的真实数据库回读，避免改写既有任务。"""
import importlib.util
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parent
ROOT = BASE.parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('sample_isolated_service',ROOT/'scripts/run_isolated_service_validation.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
original=module.database_receipt
selection=json.loads((BASE/'selection.json').read_text())['documents']
byhash={x['sha256']:x for x in selection}


async def capture(dsn,job_id,ingest=None,target_fact=None):
    result=await original(dsn,job_id,ingest,target_fact)
    source_hash=(result.get('job') or {}).get('file_hash')
    item=byhash.get(source_hash)
    if item and ingest and ingest.get('parse_run_id'):
        import asyncpg
        from src.services.parse_artifact_store import load_parse_artifact,load_parse_facts,load_parse_evidence
        conn=await asyncpg.connect(dsn)
        try:
            run_id=ingest['parse_run_id']
            artifact=await load_parse_artifact(conn,run_id)
            facts=await load_parse_facts(conn,run_id)
            evidence=await load_parse_evidence(conn,run_id)
            out=BASE/'system'/item['id']
            out.mkdir(parents=True,exist_ok=True)
            (out/'artifact.json').write_text(json.dumps(artifact.to_dict(),ensure_ascii=False))
            (out/'facts.json').write_text(json.dumps(facts,ensure_ascii=False,default=str))
            (out/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,default=str))
            (out/'database_receipt.json').write_text(json.dumps(result,ensure_ascii=False,default=str,indent=2))
            (out/'ingest.json').write_text(json.dumps(ingest,ensure_ascii=False,default=str,indent=2))
            print(f'Saved actual DB artifact, facts and evidence for {item["id"]}',flush=True)
        finally:
            await conn.close()
    return result


module.database_receipt=capture
inputs=BASE/'service_inputs'
inputs.mkdir(exist_ok=True)
for item in selection[1:]:
    target=inputs/item['filename']
    if not target.exists():target.symlink_to(item['source_pdf'])
sys.argv=[str(ROOT/'scripts/run_isolated_service_validation.py'),'--output',str(BASE/'service'),
          '--final-pdf',selection[0]['source_pdf'],'--additional-pdfs-dir',str(inputs)]
raise SystemExit(module.main())
