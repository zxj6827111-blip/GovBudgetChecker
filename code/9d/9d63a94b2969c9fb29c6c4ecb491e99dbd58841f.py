from validate_joint_chain import env, RT, OUT, checked
import httpx
import json
from pathlib import Path
import time

target = OUT / 'native_c7'
target.mkdir(exist_ok=True)
client = httpx.Client(base_url='http://127.0.0.1:3013', timeout=180)
checked(client.post('/api/auth/login', json=json.loads((RT / 'validator.json').read_text())))
source = Path.home() / 'Desktop/决算测试上海市/2022/上海市教育委员会/上海市材料工程学校2022年度决算.pdf'
with source.open('rb') as h:
    pre = checked(client.post('/api/documents/preflight', files={'file': (source.name, h, 'application/pdf')})).json()
(target / 'preflight.json').write_text(json.dumps(pre, ensure_ascii=False, indent=2))
with source.open('rb') as h:
    upload = checked(client.post('/api/documents/upload', files={'file': (source.name, h, 'application/pdf')})).json()
(target / 'upload.json').write_text(json.dumps(upload, ensure_ascii=False, indent=2))
job = upload['job_id']
checked(client.post(f'/api/analyze/{job}', json={'mode': 'legacy', 'use_local_rules': True, 'use_ai_assist': False}))
print('Native C7 uploaded:', job, flush=True)
deadline = time.monotonic() + 420
while time.monotonic() < deadline:
    status = checked(client.get(f'/api/jobs/{job}/status')).json()
    if status.get('status') not in {'pending', 'queued', 'running', 'processing'}:
        break
    time.sleep(1)
else:
    raise TimeoutError('Native C7 analysis timeout')
(target / 'status.json').write_text(json.dumps(status, ensure_ascii=False, indent=2))
assert status.get('status') != 'failed'
ingest = status['result']['meta']['structured_ingest']
reading = ingest['reading_import']
(target / 'reading_import.json').write_text(json.dumps(reading, ensure_ascii=False, indent=2))
for fmt in ('json', 'csv'):
    response = checked(client.get('/api/reports/download', params={'job_id': job, 'format': fmt}))
    (target / f'report.{fmt}').write_bytes(response.content)
    if fmt == 'json':
        assert response.json()['analysis_revision'] == status['analysis_revision']
summary = {'job_id': job, 'analysis_revision': status['analysis_revision'], 'status': status['status'],
    'structured_ingest_status': ingest['status'], 'facts_count': ingest['facts_count'],
    'review_item_count': ingest['review_item_count'], 'reading_import': reading,
    'ai_execution': status['result']['meta'].get('ai_execution'), 'rules_only': True}
(target / 'receipt.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
print(json.dumps({k:v for k,v in summary.items() if k != 'reading_import'}, ensure_ascii=False), flush=True)
