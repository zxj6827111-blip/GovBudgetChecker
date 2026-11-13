from pathlib import Path
import json, asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import sys
sys.path.insert(0, "/Volumes/workSSD/software development/gbc-worktrees/unified-closeout-20261103")
import fitz
from src.engine.rules_v33 import build_document
from src.engine.common_rules import CMM007_ZeroBaseTrendRecompute
from src.services.analysis_fact_snapshot import make_snapshot,publish_snapshot
from src.schemas.parse_artifact import ParseArtifact
from src.engine.fact_context import FactContext

OUT=Path('/Volumes/workSSD/software development/GovBudgetChecker/outputs/real_painpoint_diag_review_codex_20261104')
docs=json.loads((OUT/'independent_inventory.json').read_text())['documents']
import src.engine.common_rules as common_module
import src.services.analysis_fact_snapshot as snapshot_module
results={'cmm007':[], 'module_files':[common_module.__file__,snapshot_module.__file__]}
for order in (5,8):
    d=next(x for x in docs if x['order']==order)
    pdf=fitz.open(d['pdf']);texts=[page.get_text() for page in pdf]
    doc=build_document(path=d['pdf'],page_texts=texts,page_tables=[],filesize=Path(d['pdf']).stat().st_size)
    issues=list(CMM007_ZeroBaseTrendRecompute().apply(doc))
    results['cmm007'].append({'order':order,'issues':[{'rule':i.rule,'severity':i.severity,'message':i.message,'location':i.location} for i in issues]})

async def snapshot_probes():
    artifact=ParseArtifact(source_file_hash='a'*64,parse_run_uid='diagnostic-contract')
    ctx=FactContext(facts=[],file_hash=artifact.source_file_hash,parse_run_uid=artifact.parse_run_uid)
    snapshot=make_snapshot(job_uuid='isolated-probe',revision_id='rev-probe',artifact=artifact,raw_context=ctx,effective_context=ctx)
    receipt={k:snapshot[k] for k in ('revision_id','snapshot_hash','raw_facts_hash','effective_facts_hash','execution_fingerprint') if k in snapshot}
    receipt['storage_consistent']=True
    base={'job_id':'isolated-probe','analysis_revision':'rev-probe','structured_ingest':{'fact_snapshot':receipt}}
    probes=[]
    from src.services.analysis_result_store import compute_analysis_result_fingerprint
    for kind,value in [('string','1.23'),('decimal',Decimal('1.23'))]:
        payload={**base,'result':{'issues':{'all':[{'amount':value}]}}}
        conn=SimpleNamespace(fetchrow=AsyncMock(return_value={'payload':snapshot,'parse_run_id':1}),fetchval=AsyncMock(side_effect=[None,compute_analysis_result_fingerprint(payload)]),execute=AsyncMock())
        with patch('src.services.analysis_fact_snapshot.verify_persisted_facts',AsyncMock()),patch('src.services.fact_compatibility_projection.publish_compatibility_projection',AsyncMock()):
            try:
                await publish_snapshot(conn,payload)
                outcome='published_mock'
            except Exception as e:outcome=f'{type(e).__name__}: {e}'
        probes.append({'value_type':kind,'outcome':outcome,'db_execute_calls':conn.execute.await_count})
    return probes
results['snapshot_contract_probes']=asyncio.run(snapshot_probes())
(OUT/'boundary_reproduction.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,default=str)+'\n')
print(json.dumps(results,ensure_ascii=False,indent=2,default=str))
