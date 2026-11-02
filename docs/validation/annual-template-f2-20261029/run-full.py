"""在已核实的专用 PostgreSQL 实例中新建测试库，隔离运行完整回归。"""
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import asyncio
import hashlib
import json
import os
import subprocess
import uuid
import asyncpg

STAGE = Path(__file__).resolve().parent
IMPLEMENTATION = Path('/Users/zhangxingjin/.codex/worktrees/annual-template-management/GovBudgetChecker')
cfg = json.loads(Path('/private/tmp/annual-v3/runtime/env.json').read_text())
base_dsn = cfg['DATABASE_URL']
parsed = urlsplit(base_dsn)
assert parsed.hostname == '127.0.0.1' and parsed.port == 25462
assert parsed.path == '/gbc_annual_test' and cfg['PG_SCHEMA'] == 'annual_runtime_verified'
database = 'gbc_heading_ctx_' + uuid.uuid4().hex[:12]
test_dsn = urlunsplit(parsed._replace(path='/' + database))


async def create_database():
    conn = await asyncpg.connect(base_dsn)
    try:
        await conn.execute('CREATE DATABASE "' + database + '"')
    finally:
        await conn.close()


asyncio.run(create_database())
env = dict(os.environ)
env.pop('DATABASE_URL', None)
env.update({
    'GOVBUDGET_TEST_DATABASE_URL': test_dsn,
    'PYTHONDONTWRITEBYTECODE': '1',
    'PYTHONPATH': str(IMPLEMENTATION) + ':/private/tmp/annual-v3/xlsdeps',
    'UPLOAD_DIR': str(STAGE / 'full-uploads'),
    'ORG_DATA_DIR': str(STAGE / 'full-organizations'),
    'USER_DATA_DIR': str(STAGE / 'full-users'),
    'GBC_TEMPLATE_STORAGE': str(STAGE / 'full-templates'),
    'AUDIT_LOG_PATH': str(STAGE / 'full-audit.jsonl'),
})
for name in ['full-uploads', 'full-organizations', 'full-users', 'full-templates']:
    (STAGE / name).mkdir()
files = ['src/services/template_anchor_checks.py', 'src/services/annual_templates.py', 'tests/test_template_heading_context.py']
source_hashes = {name: hashlib.sha256((IMPLEMENTATION / name).read_bytes()).hexdigest() for name in files}
print(json.dumps({'started': True, 'fresh_database': database, 'endpoint': '127.0.0.1:25462'}), flush=True)
args = ['/Volumes/workSSD/software development/GovBudgetChecker/.venv/bin/python', '-m', 'pytest', '-q', '-r', 's', '-p', 'no:cacheprovider', '--basetemp=' + str(STAGE / 'full-pytest')]
result = subprocess.run(args, cwd=IMPLEMENTATION, env=env, capture_output=True, text=True)
log = result.stdout + result.stderr
for secret in [base_dsn, test_dsn, parsed.password]:
    if secret:
        log = log.replace(secret, '[REDACTED]')
(STAGE / 'python-full.log').write_text(log)
summary = {'base_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=IMPLEMENTATION, text=True).strip(),
           'source_sha256': source_hashes, 'database': database, 'database_endpoint': '127.0.0.1:25462',
           'argv': args, 'exit_code': result.returncode, 'tail': log[-7000:]}
(STAGE / 'full-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'completed': True, 'exit_code': result.returncode, 'log': str(STAGE / 'python-full.log'), 'tail': log[-1200:]}, ensure_ascii=False), flush=True)
raise SystemExit(result.returncode)
