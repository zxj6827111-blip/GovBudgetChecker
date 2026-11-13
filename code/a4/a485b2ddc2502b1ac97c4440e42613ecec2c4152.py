import asyncio,hashlib,json,os,sys,importlib.util
from pathlib import Path
import asyncpg
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];MAIN=ROOT/'artifacts/daily-main';env=json.loads((OUT/'private/effective_environments.json').read_text())['api'];os.environ.update(env);sys.path.insert(0,str(MAIN))
from src.services.analysis_result_store import compute_analysis_result_fingerprint
from src.schemas.review_report import review_queue_counts
expected=[r['job'] for r in json.loads((OUT/'daily_latest_preview.json').read_text())['selected']];before=json.loads((OUT/'deployment_final_preflight.json').read_text());source=json.loads((OUT/'baseline_fresh_source_manifest.json').read_text());code=json.loads((OUT/'implementation_manifest.json').read_text());partial='--partial' in sys.argv
def decode(value):return json.loads(value) if isinstance(value,str) else value
async def run():
 statuses={j:json.loads((ROOT/'uploads'/j/'status.json').read_text()) for j in expected}
 targets=[j for j,s in statuses.items() if s.get('analysis_revision') and s['analysis_revision']!=before['revisions'][j] and s.get('status') in ['done','review_required','completed','degraded']]
 if not partial:assert len(targets)==len(expected),(len(targets),len(expected))
 conn=await asyncpg.connect(env['DATABASE_URL'],timeout=20);verified=[];pending=[]
 try:
  async with conn.transaction(readonly=True):
   rows=await conn.fetch("""SELECT j.job_uuid,j.status,j.analysis_revision,j.current_fact_revision_id,
    s.source_file_hash,s.snapshot_hash,s.publication_state,s.mirror_pending,
    s.payload->>'execution_fingerprint' AS execution_fingerprint,
    s.payload->'execution_manifest'->'processing_manifest'->'code' AS code_manifest,
    r.core_hash,r.payload->>'analysis_revision' AS result_revision,r.payload->>'use_ai_assist' AS use_ai_assist
    FROM analysis_jobs j LEFT JOIN analysis_fact_snapshots s ON s.job_uuid=j.job_uuid AND s.revision_id=j.current_fact_revision_id
    LEFT JOIN analysis_revision_results r ON r.job_uuid=s.job_uuid AND r.revision_id=s.revision_id
    WHERE j.job_uuid=ANY($1::text[])""",targets)
   migrations=await conn.fetchval('select count(*) from schema_migrations')
   assert migrations==44
   by_job={r['job_uuid']:r for r in rows}
   for job in targets:
    s=statuses[job];r=by_job.get(job)
    if not r or r['current_fact_revision_id']!=s['analysis_revision'] or r['result_revision']!=s['analysis_revision'] or r['status']!=s['status'] or r['publication_state']!='published' or r['mirror_pending']:
     pending.append(job);continue
    assert r['source_file_hash']==source[job]['sha256']==s['checksum'],job
    assert r['core_hash']==compute_analysis_result_fingerprint(s),job
    local_snapshot=json.loads((ROOT/'uploads'/job/'fact_snapshots'/(s['analysis_revision']+'.json')).read_text())
    assert local_snapshot['snapshot_hash']==r['snapshot_hash'] and local_snapshot['execution_fingerprint']==r['execution_fingerprint'],job
    assert json.loads((ROOT/'uploads'/job/'fact_context.json').read_text())==local_snapshot['effective_context'],job
    manifest=decode(r['code_manifest']);assert manifest and all(manifest.get(name)==sha for name,sha in code.items() if name.startswith(('src/','api/'))),job
    assert r['use_ai_assist']=='false' and s.get('use_ai_assist') is False,job
    summary=s['result']['meta']['rule_execution_summary'];assert not summary['execution_error'] and not summary['parse_error'],job
    archives=[p for p in (ROOT/'uploads'/job/'preserved_analyses').glob('*/manifest.json') if json.loads(p.read_text()).get('files',{}).get('status.json')==before['statuses'][job]]
    assert archives and hashlib.sha256((archives[0].parent/'status.json').read_bytes()).hexdigest()==before['statuses'][job],job
    verified.append(dict(job=job,filename=s.get('filename'),revision=s['analysis_revision'],status=s['status'],source_hash_matches=True,current_pointer_matches=True,immutable_result_hash_matches=True,executed_code_hashes_match=True,ai_disabled=True,original_report_archived=True,counts=review_queue_counts(s.get('review_report') or {},())))
 finally:await conn.close()
 if not partial:assert not pending,pending
 result=dict(expected=len(expected),finished_candidates=len(targets),verified=len(verified),pending=pending,migrations=44,rows=verified)
 name='database_final_partial.json' if partial else 'database_final_verified.json';(OUT/name).write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False))
asyncio.run(run())
