"""Read-only checks of deployed code, data preservation and current database generation."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.request

import asyncpg

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT = Path(__file__).resolve().parent
MAIN = ROOT / 'artifacts/daily-main'
EXPECTED = json.loads((OUT / 'source_preservation.json').read_text())
TARGETS = ['1c770522c374bff0865f2a8fe4e3533a', 'b72007edfe134f94f76abce6aef96a5a']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def run():
    assert subprocess.check_output(['git', '-C', str(MAIN), 'rev-parse', 'HEAD'], text=True).strip() == EXPECTED['candidate']
    assert not subprocess.check_output(['git', '-C', str(MAIN), 'status', '--porcelain', '-uno'], text=True).strip()
    assert (MAIN / 'app/.next/BUILD_ID').read_text() == (ROOT / 'artifacts/claude-closeout-20261108/app/.next/BUILD_ID').read_text()
    assert digest(MAIN / 'src/engine/rules_v33.py') == EXPECTED['snapshot_engine_sha256']
    assert all(digest(ROOT / name) == value for name, value in EXPECTED['root_modified_files'].items())
    assert digest(ROOT / 'AGENTS.md') == digest(MAIN / 'AGENTS.md')
    before = json.loads((OUT / 'runtime_before.json').read_text())
    assert digest(ROOT / '.env') == before['root_env_sha256']
    originals = json.loads((OUT / 'original_pdf_manifest_before.json').read_text())
    assert all((ROOT / item['path']).stat().st_size == item['size']
               and (ROOT / item['path']).stat().st_mtime_ns == item['mtime_ns'] for item in originals)
    historical = json.loads((OUT / 'private/historical_jobs_before.json').read_text())
    changed = [item['id'] for item in historical if digest(ROOT / 'uploads' / item['id'] / 'status.json') != item['sha256']]
    assert set(changed) <= set(TARGETS)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    codes = {}
    for port, path in [(18743, '/health'), (18743, '/ready'), (3000, '/login')]:
        with opener.open(f'http://127.0.0.1:{port}{path}', timeout=15) as response:
            codes[f'{port}{path}'] = response.status
            assert response.status == 200
    listener_pids = {int(x) for x in subprocess.check_output(['lsof', '-t', '-iTCP:18743', '-sTCP:LISTEN'], text=True).split()}
    heartbeat = [json.loads(path.read_text()) for path in (ROOT / 'uploads/.worker-heartbeats').glob('*.json')]
    live = [item for item in heartbeat if item.get('pid') in listener_pids and time.time() - item.get('ts', 0) < 30]
    assert len(live) == 1
    env = json.loads((OUT / 'private/effective_environments.json').read_text())['api']
    conn = await asyncpg.connect(env['DATABASE_URL'], timeout=15)
    try:
        async with conn.transaction(readonly=True):
            rows = [dict(row) for row in await conn.fetch(
                'select job_uuid,status,analysis_revision from public.analysis_jobs where job_uuid=any($1::text[])', TARGETS)]
            migrations = [row['id'] for row in await conn.fetch('select id from public.schema_migrations order by id')]
    finally:
        await conn.close()
    receipt = {'main_sha': EXPECTED['candidate'], 'http': codes, 'single_fresh_worker': True,
               'original_pdfs_unchanged_metadata': len(originals), 'historical_status_files': len(historical),
               'changed_historical_jobs': changed, 'existing_dirty_files_unchanged': len(EXPECTED['root_modified_files']),
               'root_portable_rules_synced': True, 'target_database_rows': rows, 'migration_count': len(migrations)}
    name = 'deployment_verification_after_reanalysis.json' if changed else 'deployment_verification.json'
    (OUT / name).write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False))


asyncio.run(run())
