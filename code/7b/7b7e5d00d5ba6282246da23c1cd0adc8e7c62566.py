"""仅更新已授权的本地 API；前端、配置、用户任务及历史结果不改写。"""
import asyncio
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

import asyncpg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
env = os.environ.copy()
env.update({k:v for k,v in dotenv_values(ROOT/'.env').items() if v is not None})
env.update(BACKEND_URL='http://127.0.0.1:18743', GOVBUDGET_BACKEND_PORT='18743', JOB_QUEUE_ROLE='all')

async def ensure_idle():
    schema = env.get('PG_SCHEMA', 'public')
    assert re.fullmatch('[A-Za-z_][A-Za-z0-9_]*', schema)
    conn = await asyncpg.connect(env['DATABASE_URL'], timeout=10)
    try:
        async with conn.transaction(readonly=True):
            count = await conn.fetchval(f'SELECT count(*) FROM "{schema}".analysis_jobs WHERE status = ANY($1::text[])',
                ['queued','processing','running','parsing','analyzing'])
            assert count == 0, 'Active analysis jobs; do not interrupt'
    finally:
        await conn.close()
    for path in (ROOT/'uploads').glob('*/status.json'):
        status = json.loads(path.read_text())
        assert status.get('status') not in {'queued','processing','running','parsing','analyzing','ai_running'}, 'Active file snapshot; do not interrupt'

asyncio.run(ensure_idle())
ids = subprocess.check_output(['lsof','-tiTCP:18743','-sTCP:LISTEN'], text=True).split()
assert len(set(ids)) == 1, 'Expected one API process'
previous_pid = ids[0]
command = subprocess.check_output(['ps','-p',previous_pid,'-o','command='], text=True)
cwd = subprocess.check_output(['lsof','-a','-p',previous_pid,'-d','cwd','-Fn'], text=True)
assert 'uvicorn api.main:app' in command and f'n{ROOT}\n' in cwd, 'Service identity mismatch'
frontend_before = subprocess.check_output(['lsof','-tiTCP:3000','-sTCP:LISTEN'], text=True).strip()
os.kill(int(previous_pid), signal.SIGTERM)
for _ in range(100):
    if not subprocess.run(['lsof','-tiTCP:18743','-sTCP:LISTEN'], capture_output=True).stdout:
        break
    time.sleep(.1)
else:
    raise RuntimeError('API did not release port')
with (OUT/'api.log').open('ab') as log:
    proc = subprocess.Popen([str(ROOT/'.venv/bin/python'),'-m','uvicorn','api.main:app','--host','127.0.0.1','--port','18743'],
        cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
record = {'previous_api_pid':int(previous_pid), 'api_pid':proc.pid, 'cwd':str(ROOT),
          'frontend_pid':frontend_before, 'frontend_unchanged':frontend_before == subprocess.check_output(['lsof','-tiTCP:3000','-sTCP:LISTEN'],text=True).strip(),
          'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
(OUT/'processes.json').write_text(json.dumps(record,indent=2))
print(json.dumps(record))
