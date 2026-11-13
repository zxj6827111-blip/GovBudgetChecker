import asyncio,json,sys,hashlib,collections
from pathlib import Path
import asyncpg
from dotenv import dotenv_values
ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker');sys.path.insert(0,str(ROOT))
OUT=ROOT/'outputs/workbuddy_independent_audit_20261020'
async def main():
 from src.services.parse_artifact_store import load_parse_artifact
 c=await asyncpg.connect(dotenv_values(ROOT/'.env').get('DATABASE_URL'),timeout=5)
 rows=[]
 try:
  async with c.transaction(readonly=True):
   await c.execute("SET LOCAL statement_timeout='20s'")
   src=json.loads((OUT/'historical_coverage_readback.json').read_text())['documents']
   manifest=json.loads((OUT/'manifest.json').read_text())['files'];byhash={m['sha256']:m for m in manifest}
   for x in json.loads((ROOT/'outputs/ab_coverage/fix_all_54.json').read_text())+json.loads((ROOT/'outputs/ab_coverage/fix_retry_11.json').read_text()):
    if not x.get('job_id'):continue
    fc=json.loads((ROOT/'uploads'/x['job_id']/'fact_context.json').read_text());m=byhash.get(fc['file_hash'])
    if not m:raise ValueError('source hash absent from independent manifest')
    if any(v['source_sha256']==fc['file_hash'] for v in src):continue
    src.append({'file':x['file'],'job_id':x['job_id'],'source_sha256':fc['file_hash'],'year':m['year']})
   for s in src:
    job=ROOT/'uploads'/s['job_id'];ing=json.loads((job/'structured_ingest.json').read_text());rid=ing.get('parse_run_id')
    run=await c.fetchrow('SELECT id,source_file_hash,parser_fingerprint,config_fingerprint,status,parse_run_uid FROM parse_runs WHERE id=$1',rid)
    if not run:rows.append({'file':s['file'],'error':'missing_run','parse_run_id':rid});continue
    art=await load_parse_artifact(c,rid)
    # Read canonical facts, compare identity and value with the saved analysis snapshot.
    db=await c.fetch('SELECT * FROM parse_facts WHERE parse_run_id=$1 ORDER BY id',rid)
    facts=[dict(d) for d in db]
    fc=json.loads((job/'fact_context.json').read_text())['facts']
    keys=['fact_uid','indicator','raw_value','unit','normalized_value','org_name','fiscal_year','budget_stage','subject_code','subject_name','table_code','recognition_status']
    def canon(fs):return {f['fact_uid']:tuple(str(f.get(k)) for k in keys) for f in fs}
    c1,c2=canon(fc),canon(facts)
    row={'file':s['file'],'year':s.get('year','2023'),'parse_run_id':rid,'run':dict(run),'source_hash_matches':run['source_file_hash']==s['source_sha256'],'db_facts_count':len(facts),'analysis_facts_count':len(fc),'fact_identity_value_mismatches':len(set(c1)^set(c2))+sum(c1[k]!=c2[k] for k in set(c1)&set(c2)), 'fact_mismatch_details':[{'fact_uid':k,'snapshot':dict(zip(keys,c1[k])) if k in c1 else None,'database':dict(zip(keys,c2[k])) if k in c2 else None} for k in set(c1)|set(c2) if c1.get(k)!=c2.get(k)],'cache_hit_reported':ing.get('parse_cache_hit')}
    fresh=OUT/(s.get('year','2023')+'__'+Path(s['file']).stem+'__artifact.json')
    if fresh.exists():
     old=art.to_dict();new=json.loads(fresh.read_text());fs=['table_code','title','rows','cells']
     def fragments(a):return [{k:t.get(k) for k in fs} for t in a['table_fragments']]
     row['fresh_fragment_content_matches_db']=fragments(old)==fragments(new)
     (OUT/(s.get('year','2023')+'__'+Path(s['file']).stem+'__db_artifact.json')).write_text(json.dumps(old,ensure_ascii=False))
    rows.append(row)
   report={'summary':{'jobs':len(rows),'hash_matches':sum(r.get('source_hash_matches',False) for r in rows),'errors':[r for r in rows if 'error'in r],'facts_db':sum(r.get('db_facts_count',0)for r in rows),'facts_analysis':sum(r.get('analysis_facts_count',0)for r in rows),'fact_identity_value_mismatches':sum(r.get('fact_identity_value_mismatches',0)for r in rows),'fresh_fragment_comparisons':sum('fresh_fragment_content_matches_db'in r for r in rows),'fresh_fragment_mismatches':sum(r.get('fresh_fragment_content_matches_db') is False for r in rows)},'documents':rows}
   (OUT/'database_all103_readback.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report['summary'],ensure_ascii=False,indent=2))
 finally:await c.close()
asyncio.run(main())
