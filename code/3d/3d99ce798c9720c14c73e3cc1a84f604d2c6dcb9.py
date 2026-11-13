"""隔离API/Worker/Postgres，回读修复金额，注入冻结v23缓存并验证指纹隔离。"""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import uuid

BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[1]
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('isolated_validation',ROOT/'scripts/run_isolated_service_validation.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
original=module.database_receipt
seeded={}


async def capture(dsn,job_id,ingest=None,target_fact=None):
    result=await original(dsn,job_id,ingest,target_fact)
    if not ingest or not ingest.get('parse_run_id'):return result
    import asyncpg
    from src.schemas.parse_artifact import ParseArtifact, FiscalFact
    from src.services.parse_artifact_store import (load_parse_artifact,load_parse_facts,load_parse_evidence,
        persist_parse_artifact,persist_facts_and_evidence,find_cached_parse_run)
    conn=await asyncpg.connect(dsn)
    try:
        run_id=ingest['parse_run_id'];artifact=await load_parse_artifact(conn,run_id)
        facts=await load_parse_facts(conn,run_id);evidence=await load_parse_evidence(conn,run_id)
        out=BASE/'service_database'/job_id/str((result.get('job') or {}).get('analysis_revision'))
        out.mkdir(parents=True,exist_ok=True)
        for name,data in [('artifact',artifact.to_dict()),('facts',facts),('evidence',evidence),('database_receipt',result),('ingest',ingest)]:
            (out/f'{name}.json').write_text(json.dumps(data,ensure_ascii=False,default=str,indent=2))
        inventory=json.loads((BASE/'baseline/inventory.json').read_text())
        doc=next((d for d in inventory if d['sha256']==artifact.source_file_hash),None)
        if doc and ingest['document_version_id'] not in seeded:
            # 源自本轮修改前冻结的真正v23产物；保留错误列绑定，不能伪造为正确缓存。
            folder=BASE/'baseline/documents'/doc['doc_id']
            old=ParseArtifact.from_dict(json.loads((folder/'artifact.json').read_text()))
            old.config_fingerprint=artifact.config_fingerprint
            old.parse_run_uid='stale-v23-'+uuid.uuid4().hex
            old_id=await persist_parse_artifact(conn,old,document_version_id=ingest['document_version_id'])
            oldfacts=[FiscalFact.from_dict(f) for f in json.loads((folder/'facts.json').read_text())]
            await persist_facts_and_evidence(conn,parse_run_id=old_id,facts=oldfacts,file_hash=old.source_file_hash)
            kwargs=dict(document_version_id=ingest['document_version_id'],source_file_hash=artifact.source_file_hash,
                        config_fingerprint=artifact.config_fingerprint,ocr_model_fingerprint=artifact.ocr_model_fingerprint)
            assert await find_cached_parse_run(conn,parser_fingerprint=old.parser_fingerprint,**kwargs)==old_id
            assert await find_cached_parse_run(conn,parser_fingerprint=artifact.parser_fingerprint,**kwargs)==run_id
            seeded[ingest['document_version_id']]={'doc_id':doc['doc_id'],'v23_run_id':old_id,'v24_run_id':run_id,
                  'legacy_created_after_current':old_id>run_id,'v24_lookup_rejected_newer_v23':True}
            (BASE/'service_cache_isolation.json').write_text(json.dumps(list(seeded.values()),ensure_ascii=False,indent=2))
        if doc:
            rows=[dict(r) for r in await conn.fetch('SELECT id,parser_fingerprint,config_fingerprint,status FROM parse_runs WHERE document_version_id=$1 ORDER BY id',ingest['document_version_id'])]
            (out/'cache_runs.json').write_text(json.dumps(rows,ensure_ascii=False,default=str,indent=2))
    finally:await conn.close()
    return result


module.database_receipt=capture
frozen=ROOT/'outputs/shanghai_2024_v23_acceptance_20261015/documents'
inputs=BASE/'service_inputs';inputs.mkdir(exist_ok=True)
for doc in ['SH2024_11','SH2024_14']:
    name=next(d['filename'] for d in json.loads((BASE/'baseline/inventory.json').read_text()) if d['doc_id']==doc)
    target=inputs/name
    if not target.exists():target.symlink_to(frozen/doc/'source.pdf')
sys.argv=[str(ROOT/'scripts/run_isolated_service_validation.py'),'--output',str(BASE/'service_complete'),
          '--final-pdf',str(frozen/'SH2024_01/source.pdf'),'--additional-pdfs-dir',str(inputs),
          '--native-correction-pdf',str(frozen/'SH2024_01/source.pdf')]
raise SystemExit(module.main())
