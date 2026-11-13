import asyncio,json
from pathlib import Path
import asyncpg
OUT=Path(__file__).resolve().parent
async def run():
 env=json.loads((OUT/'private/effective_environments.json').read_text())['api'];conn=await asyncpg.connect(env['DATABASE_URL'],timeout=20)
 try:
  async with conn.transaction(readonly=True):
   rows=await conn.fetch("""SELECT p.document_id,p.job_uuid,p.revision_id,p.receipt->>'shared_report_id' AS report_id,
    r.scope_key,r.file_hash,r.file_name,r.fact_projection FROM published_fact_projections p
    JOIN org_dept_annual_report r ON r.id::text=p.receipt->>'shared_report_id'
    WHERE p.job_uuid=ANY($1::text[])""",['4289b198cf38f87619cb34db6be11c3d','79efdd8f0d7b3a2938de65cfb5aad101'])
   assert len(rows)==2 and len({r['report_id'] for r in rows})==2 and len({r['document_id'] for r in rows})==2
   for row in rows:
    snapshot=json.loads(row['fact_projection']) if isinstance(row['fact_projection'],str) else row['fact_projection']
    assert snapshot['job_uuid']==row['job_uuid'] and snapshot['revision_id']==row['revision_id']
    if row['job_uuid'].startswith('4289'):assert row['scope_key']=='fiscal-document:'+str(row['document_id'])
   result=dict(distinct_documents_and_reports=True,both_current_snapshot_references_match=True,rows=[{k:v for k,v in dict(row).items() if k!='fact_projection'} for row in rows])
   (OUT/'shared_scope_final_verified.json').write_text(json.dumps(result,default=str,ensure_ascii=False,indent=2));print('Two independent source documents published to distinct current reports')
 finally:await conn.close()
asyncio.run(run())
