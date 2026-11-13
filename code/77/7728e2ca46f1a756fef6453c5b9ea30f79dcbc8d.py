import asyncio,json,hashlib,sys
from pathlib import Path
import asyncpg
OUT=Path(__file__).resolve().parent
async def run():
 env=json.loads((OUT/'private/effective_environments.json').read_text())['api']
 conn=await asyncpg.connect(env['DATABASE_URL'],timeout=15)
 try:
  async with conn.transaction(readonly=True):
   tables=[r['table_name'] for r in await conn.fetch("select table_name from information_schema.tables where table_schema='public'")]
   counts={t:await conn.fetchval('select count(*) from public.'+t) for t in ['analysis_jobs','analysis_results','schema_migrations','review_sessions','review_obligation_decisions'] if t in tables}
   statuses={r['status']:r['n'] for r in await conn.fetch('select status,count(*) as n from analysis_jobs group by status')}
   revisions=[dict(r) for r in await conn.fetch('select job_uuid,status,analysis_revision,updated_at from analysis_jobs order by job_uuid')]
   history={}
   for table in ['review_sessions','review_obligation_decisions']:
    history[table]=[dict(r) for r in await conn.fetch('select * from '+table+' order by id')] if table in tables else []
   (OUT/('private/database_'+sys.argv[1]+'.json')).write_text(json.dumps(dict(revisions=revisions,history=history),ensure_ascii=False,default=str,indent=2));(OUT/('private/database_'+sys.argv[1]+'.json')).chmod(0o600)
   print(json.dumps(dict(counts=counts,statuses=statuses),ensure_ascii=False))
 finally:await conn.close()
asyncio.run(run())
