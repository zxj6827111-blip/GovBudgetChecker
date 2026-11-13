"""Read-only database identity/counts before the service cutover."""
import asyncio
import json
from pathlib import Path
import sys
import asyncpg

ROOT=Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT=Path(__file__).resolve().parent
env=json.loads((OUT/'private/effective_environments.json').read_text())['api']
async def main():
 conn=await asyncpg.connect(env['DATABASE_URL'],timeout=10)
 try:
  async with conn.transaction(readonly=True):
   identity=dict(await conn.fetchrow('select current_database() as database, current_schema() as schema'))
   migrations=[r['id'] for r in await conn.fetch('select id from schema_migrations order by id')]
   counts={}
   for table in ['analysis_jobs','analysis_results','material_slots','material_versions','review_obligation_decisions']:
    if await conn.fetchval('select to_regclass($1)',table):counts[table]=await conn.fetchval('select count(*) from '+table)
  result={'identity':identity,'migrations':migrations,'counts':counts}
  (OUT/'database_before.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
  print(json.dumps({'connected':True,'migration_count':len(migrations),'counts':counts},ensure_ascii=False))
 finally:await conn.close()
asyncio.run(main())
