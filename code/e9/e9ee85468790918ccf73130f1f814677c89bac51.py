"""真实前端代理 → API/worker → 规则/AI → 数据库 → JSON/CSV 的联合验证。"""
import asyncio
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import httpx

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
WT = ROOT / 'artifacts/pdf-ai-local-acceptance-20261026'
RT = OUT / 'runtime'
EVIDENCE = OUT / 'joint_chain'
EVIDENCE.mkdir(exist_ok=True)
env = json.loads((RT / 'environment.json').read_text())
os.environ.update(env)
sys.path.insert(0, str(WT))
from scripts.run_isolated_service_validation import database_receipt, structured_ingest_from_payload, structured_ingest_validation
from scripts.validate_intake_e1 import check_e1_synthetic_receipts

def save(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))

def checked(response):
    if not response.is_success:
        raise RuntimeError(f'{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:600]}')
    return response

def main():
    credentials = json.loads((RT / 'validator.json').read_text())
    api = httpx.Client(base_url=env['BACKEND_URL'], headers={'X-API-Key': env['GOVBUDGET_API_KEY']}, timeout=180)
    token = checked(api.post('/api/auth/login', json=credentials)).json()['token']
    api.headers['X-Session-Token'] = token
    frontend = httpx.Client(base_url='http://127.0.0.1:3013', timeout=180)
    login = checked(frontend.post('/api/auth/login', json=credentials))
    cookie_names = list(frontend.cookies.keys())
    assert 'gbc_acceptance_20261026' in cookie_names and 'gbc_session' not in cookie_names
    # 夹具初始化不冒充组织管理接口验收。保留真实目录已有错误，不清洗或删除原记录。
    from src.schemas.organization import Organization
    from src.services.org_storage import _catalog_lock, _atomic_catalog_json
    catalog_path = RT / 'data/organizations.json'
    with _catalog_lock():
        catalog = json.loads(catalog_path.read_text())
        department = Organization(id=Organization.generate_id('上海市E1测试主管局', 'department'), name='上海市E1测试主管局', level='department').model_dump()
        unit = Organization(id=Organization.generate_id('上海市E1测试中心', 'unit', department['id']), name='上海市E1测试中心', level='unit', parent_id=department['id']).model_dump()
        for item in (department, unit):
            assert not any(org['id'] == item['id'] for org in catalog['organizations']), 'Fixture already seeded; inspect before rerunning'
            catalog['organizations'].append(item)
        _atomic_catalog_json(catalog_path, catalog)
    save('test_organizations.json', {'department': department, 'unit': unit, 'setup_only': True, 'organization_management_api': 'blocked by existing duplicate catalog records'})
    sample = WT / 'outputs/intake_remediation_20261024/t0/e1/E1_scope_pending.pdf'
    assert hashlib.sha256(sample.read_bytes()).hexdigest() == 'e2e30201a399156f759ae5edfa7d29219fc94b1ae2751ccb19280f19c541d0c9'
    with sample.open('rb') as handle:
        preflight = checked(frontend.post('/api/documents/preflight', files={'file': (sample.name, handle, 'application/pdf')})).json()
    save('preflight.json', preflight)
    with sample.open('rb') as handle:
        uploaded = checked(frontend.post('/api/documents/upload', files={'file': (sample.name, handle, 'application/pdf')})).json()
    save('upload.json', uploaded)
    job = uploaded['job_id']
    settings = json.loads((RT / 'data/ai-model-settings.json').read_text())
    request = {'mode': 'dual', 'use_local_rules': True, 'use_ai_assist': True, 'ai_profile_id': settings['active_profile_id']}
    save('analyze_request.json', request)
    started = checked(frontend.post(f'/api/analyze/{job}', json=request)).json()
    save('analyze_response.json', started)
    print(f'Real uploaded job {job}: rule + managed AI requested', flush=True)
    deadline = time.monotonic() + 900
    last = None
    while time.monotonic() < deadline:
        status = checked(frontend.get(f'/api/jobs/{job}/status')).json()
        phase = (status.get('status'), (status.get('stage_progress') or {}).get('phase'))
        if phase != last:
            print(f'Job stage: {phase}', flush=True)
            last = phase
        if status.get('status') not in {'queued', 'processing', 'running', 'pending'}:
            break
        time.sleep(1)
    else:
        raise TimeoutError('Real joint analysis exceeded 900s; task evidence retained')
    save('status.json', status)
    assert status.get('status') != 'failed', status.get('error')
    revision = status['analysis_revision']
    result = checked(frontend.get(f'/api/jobs/{job}')).json()
    save('job_detail.json', result)
    relations = check_e1_synthetic_receipts(status)
    save('relation_validation.json', relations)
    snapshot = json.loads((RT / 'uploads' / job / 'fact_snapshots' / f'{revision}.json').read_text())
    facts = snapshot['effective_context']['facts']
    assert facts and all(f.get('org_scope') is None for f in facts)
    index = {(f.get('subject_code'), f['indicator']): f for f in facts}
    for code, expected in {'total': ('300', '200', '90'), '208': ('100', '50', '40')}.items():
        terms = [index[(code, role)] for role in ('total_actual', 'fiscal_allocation', 'business_income')]
        assert [Decimal(str(f['normalized_value'])) for f in terms] == list(map(Decimal, expected))
        assert all(any(ref.get('physical_page') == 2 and ref.get('source_cell_id') for ref in f['extra_dims']['evidence_refs']) for f in terms)
    ingest = structured_ingest_from_payload(status)
    db = asyncio.run(database_receipt(env['DATABASE_URL'], job, ingest))
    save('database.json', db)
    storage = structured_ingest_validation(status, db)
    assert storage['passed'], storage
    exported = checked(frontend.get('/api/reports/download', params={'job_id': job, 'format': 'json'}))
    (EVIDENCE / 'report.json').write_bytes(exported.content)
    assert exported.json()['analysis_revision'] == revision
    assert '汇总口径待确认' in json.dumps(exported.json(), ensure_ascii=False)
    csv = checked(frontend.get('/api/reports/download', params={'job_id': job, 'format': 'csv'}))
    (EVIDENCE / 'report.csv').write_bytes(csv.content)
    assert revision in csv.text and 'V33-240' in csv.text
    meta = status['result']['meta']
    receipt = {'job_id': job, 'analysis_revision': revision, 'source_sha256': hashlib.sha256(sample.read_bytes()).hexdigest(),
        'frontend_url': f'http://127.0.0.1:3013/task/{job}', 'real_frontend_proxy': True, 'real_worker': True,
        'cookie_isolated': True, 'rule_relations': relations, 'storage': storage,
        'ai_execution': meta.get('ai_execution'), 'ai_coverage': meta.get('ai_coverage'),
        'same_revision_json_csv': True, 'browser_pending': True}
    save('receipt.json', receipt)
    print(json.dumps({'job_id': job, 'revision': revision, 'rules': 'passed', 'storage': storage['passed'], 'ai_execution': meta.get('ai_execution'), 'ai_coverage': meta.get('ai_coverage')}, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
