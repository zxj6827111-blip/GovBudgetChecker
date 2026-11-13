import asyncio,json
from pathlib import Path
import asyncpg
OUT=Path(__file__).resolve().parent
async def run():
 env=json.loads((OUT/'private/effective_environments.json').read_text())['api']
 conn=await asyncpg.connect(env['DATABASE_URL'],timeout=20)
 try:
  async with conn.transaction(readonly=True):
   jobs=await conn.fetch("SELECT job_uuid,status,analysis_revision,metadata FROM analysis_jobs WHERE job_uuid=$1",'4289b198cf38f87619cb34db6be11c3d')
   status=json.loads((OUT.parents[1]/'uploads/4289b198cf38f87619cb34db6be11c3d/status.json').read_text())
   version=status['structured_ingest']['document_version_id']; rows=await conn.fetch('''SELECT v.*,to_jsonb(d) AS document,to_jsonb(o) AS organization
    FROM fiscal_document_versions v JOIN fiscal_documents d ON d.id=v.document_id JOIN org_units o ON o.id=d.org_unit_id WHERE v.id=$1''',version)
   reports=await conn.fetch("SELECT * FROM org_dept_annual_report WHERE department_id IN (SELECT id FROM org_department WHERE name LIKE '%残疾%')")
   projections=await conn.fetch("SELECT p.*,to_jsonb(d) AS document FROM published_fact_projections p JOIN fiscal_documents d ON d.id=p.document_id WHERE p.receipt->>'shared_report_id'=ANY($1::text[])",[str(r['id']) for r in reports])
   result=dict(jobs=[dict(r) for r in jobs],versions=[dict(r) for r in rows],reports=[dict(r) for r in reports],projections=[dict(r) for r in projections])
   (OUT/'private/projection_collision.json').write_text(json.dumps(result,default=str,ensure_ascii=False,indent=2))
   for key,items in result.items():
    print(key)
    for item in items:print(json.dumps({k:v for k,v in item.items() if k not in ['metadata','fact_projection']},ensure_ascii=False,default=str))
 finally:await conn.close()
asyncio.run(run())
