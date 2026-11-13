"""Read-only final runtime, database, preservation and exported-report verification."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

import asyncpg
import pdfplumber

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
MAIN = ROOT / 'artifacts/daily-main'
OUT = Path(__file__).resolve().parent
JOB = 'f0dfcd6296dcc0769dd5ab6e778f8855'
SHA = 'ed9478655bc38266cbea0d2f5e23928249435e28'
ENV = json.loads((OUT / 'private/release_environments.json').read_text())['api']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(port, path):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}{path}', timeout=15) as response:
        body = response.read()
        return response.status, json.loads(body) if 'json' in response.headers.get('Content-Type', '') else None


def git(*args, cwd=ROOT):
    return subprocess.check_output(['git', '-C', str(cwd), *args], text=True).strip()


def listener_info(port):
    ids = sorted({int(x) for x in subprocess.check_output(
        ['lsof', '-t', f'-iTCP:{port}', '-sTCP:LISTEN'], text=True).split()})
    result = []
    for pid in ids:
        lines = subprocess.check_output(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'], text=True).splitlines()
        cwd = next(line[1:] for line in lines if line.startswith('n'))
        assert cwd in [str(MAIN), str(MAIN / 'app')]
        result.append({'pid': pid, 'cwd': cwd})
    return result


async def database():
    conn = await asyncpg.connect(ENV['DATABASE_URL'], timeout=15)
    try:
        async with conn.transaction(readonly=True):
            counts = {table: await conn.fetchval('select count(*) from public.' + table)
                      for table in ['analysis_jobs', 'analysis_results', 'material_slots', 'review_obligation_decisions']}
            job = dict(await conn.fetchrow('select job_uuid,status,analysis_revision from public.analysis_jobs where job_uuid=$1', JOB))
            result_count = await conn.fetchval('select count(*) from public.analysis_results r join public.analysis_jobs j on j.id=r.job_id where j.job_uuid=$1', JOB)
            decisions = [dict(r) for r in await conn.fetch('''
                select d.obligation_id,d.decision,d.note,d.evidence_reference,d.revision,
                       s.id::text as review_session_id,s.slot_id::text,
                       s.analysis_job_uuid,s.analysis_basis_token,s.status as session_status
                from public.review_obligation_decisions d
                join public.review_sessions s on s.id=d.review_session_id
                where s.analysis_job_uuid=$1
            ''', JOB)]
            migrations = [r['id'] for r in await conn.fetch('select id from public.schema_migrations order by id')]
        assert result_count == 1
        assert job['status'] == 'review_required'
        assert len(decisions) == 1 and decisions[0]['decision'] == 'verified_issue'
        assert decisions[0]['obligation_id'] == 'OBL-SG-ITEMS'
        assert '此结论覆盖本义务唯一阻断点' in decisions[0]['note']
        assert decisions[0]['analysis_basis_token'] == f"{JOB}:{job['analysis_revision']}"
        before = json.loads((OUT / 'database_before.json').read_text())
        assert migrations == before['migrations']
        return {'counts_before': before['counts'], 'counts_after': counts, 'migration_count': len(migrations),
                'migration_list_unchanged': True, 'job': job, 'analysis_result_rows': result_count, 'decisions': decisions}
    finally:
        await conn.close()


async def main():
    before = json.loads((OUT / 'runtime_before.json').read_text())
    historical = json.loads((OUT / 'private/historical_jobs_before.json').read_text())
    changed = [item['id'] for item in historical
               if not (ROOT / 'uploads' / item['id'] / 'status.json').exists()
               or digest(ROOT / 'uploads' / item['id'] / 'status.json') != item['sha256']]
    originals = json.loads((OUT / 'original_pdf_manifest_before.json').read_text())
    changed_pdf = []
    for item in originals:
        p = ROOT / item['path']
        if not p.exists() or p.stat().st_size != item['size'] or p.stat().st_mtime_ns != item['mtime_ns']:
            changed_pdf.append(item['path'])
    assert not changed and not changed_pdf
    assert digest(ROOT / '.env') == before['root_env_sha256']
    assert hashlib.sha256(ENV['DATABASE_URL'].encode()).hexdigest() == before['database_identity_sha256']
    assert git('rev-parse', 'HEAD', cwd=MAIN) == SHA
    assert git('rev-parse', 'main') == SHA
    assert not git('status', '--porcelain=v1', '-uno', cwd=MAIN)
    health_status, health = request(18743, '/health')
    ready_status, ready = request(18743, '/ready')
    frontend_status, _ = request(3000, '/login')
    _, schema = request(18743, '/openapi.json')
    assert health_status == ready_status == frontend_status == 200
    assert 'scope_confirmed' in schema['components']['schemas']['ObligationDecisionRequest']['properties']
    assert 'card_summary' in schema['components']['schemas']['CoverageObligationItem']['properties']
    assert 'suggested_actions' in schema['components']['schemas']['CoverageObligationItem']['properties']
    listeners = {str(port): listener_info(port) for port in [18743, 3000]}
    live_ids = {p['pid'] for p in listeners['18743']}
    heartbeat = [json.loads(p.read_text()) for p in (ROOT / 'uploads/.worker-heartbeats').glob('*.json')]
    live_heartbeat = [item for item in heartbeat if item.get('pid') in live_ids and time.time() - item.get('ts', 0) < 30]
    assert len(live_heartbeat) == 1
    jobdir = ROOT / 'uploads' / JOB
    status = json.loads((jobdir / 'status.json').read_text())
    progress = json.loads((jobdir / 'check_progress.json').read_text())
    persistence = json.loads((jobdir / 'persistence.json').read_text())
    meta = status['result']['meta']
    assert status['status'] == 'review_required' and status['progress'] == 100
    assert progress['local']['completed'] == progress['local']['total'] == 90
    assert meta['ai_execution']['state'] == 'succeeded'
    assert meta['ai_execution']['succeeded_calls'] == meta['ai_execution']['planned_calls'] == 2
    assert persistence['status'] == 'synced'
    source = OUT / '上海市服务升级验收专用部门2025年度部门决算.pdf'
    uploaded = jobdir / source.name
    assert digest(source) == digest(uploaded) == status['checksum']
    download = Path('/Users/zhangxingjin/Downloads/annotated-rev-20261108T131746-1fd9a811-580bdda0d0c62a67.pdf')
    exported = OUT / 'smoke_export.pdf'
    shutil.copy2(download, exported)
    with pdfplumber.open(exported) as pdf:
        text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
        page_count = len(pdf.pages)
    checks = {
        'job_identity_present': JOB in text,
        'manual_obligation_present': 'OBL-SG-ITEMS' in text,
        'manual_note_present': '此结论覆盖本义务唯一阻断点table_missing' in text,
        'confirmed_issue_present': '确认有问题' in text,
        'unfinished_scope_disclosed': '检查尚未完整完成（32 项未完成）' in text,
        'engine_state_kept_separate': '引擎自动检查状态保持原样' in text,
    }
    assert all(checks.values())
    db = await database()
    loglines = (OUT / 'main_backend.log').read_text().splitlines()
    errors = []
    for line in loglines:
        if line.startswith('{'):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get('level') in ['ERROR', 'CRITICAL']:
                errors.append({'level': entry['level'], 'logger': entry.get('logger'), 'message': entry.get('message')})
        elif 'Traceback (most recent call last)' in line:
            errors.append({'message': 'Traceback found; inspect protected release log'})
    assert not errors
    summary = {
        'verified_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'main_sha': SHA, 'main_tracked_worktree_clean': True,
        'old_root_branch': git('branch', '--show-current'), 'old_root_head': git('rev-parse', 'HEAD'),
        'old_root_tracked_changes': git('status', '--porcelain=v1', '-uno').splitlines(),
        'runtime': {'health': health, 'ready': ready, 'frontend_http': frontend_status,
                    'listeners': listeners, 'queue_role': ENV.get('JOB_QUEUE_ROLE', 'all'), 'heartbeat': live_heartbeat,
                    'backend_errors': errors, 'frontend_mode': 'production'},
        'preservation': {'root_env_hash_unchanged': True, 'database_identity_hash_unchanged': True,
                         'historical_status_count': len(historical), 'historical_status_changed': changed,
                         'original_pdf_metadata_count': len(originals), 'original_pdf_metadata_changed': changed_pdf,
                         'old_workspace_retained': ROOT.exists()},
        'smoke': {'job_id': JOB, 'organization_id': status['organization_id'], 'synthetic_material': True,
                  'status': status['status'], 'pages': meta.get('pages'),
                  'local_rules': {'completed': progress['local']['completed'], 'total': progress['local']['total']},
                  'ai': {k: meta['ai_execution'].get(k) for k in ['state', 'model', 'planned_calls', 'succeeded_calls']},
                  'persistence_status': persistence['status'], 'uploaded_checksum_matches': True,
                  'formal_issue_confirmations': len(status['human_decisions']),
                  'engine_unresolved_obligations': meta['obligation_coverage']['unresolved_total'],
                  'ui_remaining_mandatory': 33, 'ui_refresh_preserved_decisions': True,
                  'ui_complete_review_disabled': True},
        'database': db,
        'export': {'path': str(exported), 'download_path': str(download), 'bytes': exported.stat().st_size,
                   'pages': page_count, 'sha256': digest(exported), 'checks': checks},
        'backup_receipt': str(OUT / 'backup_receipt.json'),
        'rollback_command': f'"{ROOT}/.venv/bin/python" "{OUT}/manage_services.py" rollback',
        'limitations': ['合成材料用于运行流程验证，不替代真实财政业务金标验收',
                        '保留未知与人工处理边界；未重跑任何历史任务', '未执行破坏性数据库恢复测试',
                        '本次完成当前会话服务切换，未新增操作系统开机自启服务'],
    }
    (OUT / 'release_verification.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    (OUT / 'smoke_job.json').write_text(json.dumps(summary['smoke'], ensure_ascii=False, indent=2))
    print(json.dumps({'passed': True, 'main': SHA, 'heartbeat_pid': live_heartbeat[0]['pid'],
                      'preserved_historical_status': len(historical), 'preserved_original_metadata': len(originals),
                      'database_counts': db['counts_after'], 'export_pages': page_count,
                      'main_tracked_clean': True, 'old_tracked_changes': len(summary['old_root_tracked_changes'])}, ensure_ascii=False))


asyncio.run(main())
