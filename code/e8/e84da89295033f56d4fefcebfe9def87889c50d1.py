import asyncio,json,os,subprocess,uuid
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit
import asyncpg
OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[1];CODE=ROOT/'artifacts/comprehensive-review-closure-20261109'
async def run():
 api=json.loads((OUT/'private/effective_environments.json').read_text())['api'];parts=urlsplit(api['DATABASE_URL']);name='gbc_review_closure_'+uuid.uuid4().hex[:12]
 conn=await asyncpg.connect(api['DATABASE_URL'],timeout=15)
 try:await conn.execute('CREATE DATABASE "'+name+'"')
 finally:await conn.close()
 dsn=urlunsplit((parts.scheme,parts.netloc,'/'+name,parts.query,parts.fragment));private=OUT/'private/pg_test_database_final6.json';private.write_text(json.dumps(dict(name=name,dsn=dsn)));private.chmod(0o600)
 env=dict(os.environ);env['GOVBUDGET_TEST_DATABASE_URL']=dsn;env['PYTHON_DOTENV_DISABLED']='1';env.pop('DATABASE_URL',None)
 print('Isolated database',name,flush=True)
 with (OUT/'postgres-final6-integration.log').open('w') as log:
  result=subprocess.run([str(CODE/'.venv/bin/python'),'-m','pytest','-q','-m','real_database'],cwd=CODE,env=env,stdout=log,stderr=subprocess.STDOUT)
 (OUT/'postgres-final6_test_receipt.json').write_text(json.dumps(dict(database=name,exit_code=result.returncode,production_database_untouched=True),indent=2));print('PostgreSQL test exit',result.returncode,flush=True)
asyncio.run(run())
