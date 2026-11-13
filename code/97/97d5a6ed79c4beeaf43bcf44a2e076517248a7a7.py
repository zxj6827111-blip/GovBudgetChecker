from pathlib import Path
import asyncio
import hashlib
import json
import subprocess
import time
import uuid
import asyncpg

OUT = Path(__file__).resolve().parent
receipt = json.loads((OUT / 'live_services/service_receipt.json').read_text())
name = 'gbc-review14-jsonb-' + uuid.uuid4().hex[:10]
password = uuid.uuid4().hex
result = {'scope': 'actual isolated asyncpg/PostgreSQL JSONB type probe; production failure separately recorded', 'container': name}


async def run(dsn):
    for _ in range(60):
        try:
            conn = await asyncpg.connect(dsn, timeout=2)
            break
        except (OSError, asyncpg.PostgresError):
            await asyncio.sleep(.5)
    else:
        raise TimeoutError('isolated PG not ready')
    try:
        for label, obj in [('empty_native', {}), ('ocr_nested', {'source': {'model_fingerprint': 'a' * 64}, 'qualification': {'status': 'qualified'}})]:
            row = await conn.fetchrow('SELECT $1::jsonb AS ocr_provenance', json.dumps(obj))
            value = row['ocr_provenance']
            item = {'database_type': 'jsonb', 'python_type': type(value).__name__, 'decoded_equals_control': json.loads(value) == obj}
            try:
                dict(value or {})
                item['current_loader_expression'] = 'succeeded'
            except (ValueError, TypeError) as exc:
                item['current_loader_expression'] = type(exc).__name__ + ': ' + str(exc)
            result[label] = item
        result['postgres_version'] = await conn.fetchval('SELECT version()')
        assert all(result[k]['python_type'] == 'str' and result[k]['current_loader_expression'].startswith('ValueError:') for k in ['empty_native', 'ocr_nested'])
    finally:
        await conn.close()


try:
    subprocess.run(['docker', 'run', '-d', '--pull=never', '--name', name, '--tmpfs', '/var/lib/postgresql/data:rw', '-e', 'POSTGRES_PASSWORD=' + password, '-p', '127.0.0.1::5432', receipt['postgres']['image_id']], check=True, capture_output=True, text=True)
    mapping = subprocess.check_output(['docker', 'port', name, '5432/tcp'], text=True).strip()
    port = int(mapping.rsplit(':', 1)[1])
    asyncio.run(run('postgresql://postgres:' + password + '@127.0.0.1:' + str(port) + '/postgres'))
    result['status'] = 'reproduced'
finally:
    removed = subprocess.run(['docker', 'rm', '-f', name], capture_output=True, text=True)
    result['own_pg_removed'] = removed.returncode == 0
    (OUT / 'pg_jsonb_probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False))
