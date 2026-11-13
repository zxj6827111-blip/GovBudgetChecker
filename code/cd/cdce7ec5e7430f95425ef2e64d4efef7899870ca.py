"""Read-only proof of final live analyses, canonical database results and preserved originals."""
import asyncio
import gzip
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import asyncpg

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
MAIN = ROOT / 'artifacts/daily-main'
OUT = Path(__file__).resolve().parent
TARGETS = {'1c770522c374bff0865f2a8fe4e3533a': 10, 'b72007edfe134f94f76abce6aef96a5a': 15}
os.environ['PYTHON_DOTENV_DISABLED'] = '1'
os.environ['DATABASE_URL'] = ''
sys.path.insert(0, str(MAIN))
from src.services.analysis_result_store import compute_analysis_result_fingerprint


def digest(data):
    return hashlib.sha256(data).hexdigest()


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


async def run():
    source = json.loads((OUT / 'source_preservation.json').read_text())
    assert subprocess.check_output(['git', '-C', str(MAIN), 'rev-parse', 'HEAD'], text=True).strip() == source['candidate']
    before = json.loads((OUT / 'private/historical_jobs_before.json').read_text())
    statuses = {job: json.loads((ROOT / 'uploads' / job / 'status.json').read_text()) for job in TARGETS}
    status_dir = OUT / 'live_final_status'
    status_dir.mkdir(exist_ok=True)
    for job, data in statuses.items():
        assert data['status'] in {'done', 'review_required'} and data['progress'] == 100
        assert (data.get('structured_ingest') or {})['status'] == 'done'
        ai = data['result']['meta']['ai_execution']
        assert ai['state'] == 'succeeded' and ai['succeeded_calls'] == ai['planned_calls'] and not ai['failed_units']
        with gzip.open(status_dir / f'{job}.status.json.gz', 'wt') as output:
            json.dump(data, output, ensure_ascii=False)
    subprocess.run([sys.executable, str(OUT / 'queue_counts.py'), str(MAIN), str(status_dir),
                    str(OUT / 'live_final_counts.json')], check=True, stdout=subprocess.PIPE)
    counts = json.loads((OUT / 'live_final_counts.json').read_text())
    env = json.loads((OUT / 'private/effective_environments.json').read_text())['api']
    conn = await asyncpg.connect(env['DATABASE_URL'], timeout=15)
    receipts = {}
    try:
        async with conn.transaction(readonly=True):
            for job, expected in TARGETS.items():
                data = statuses[job]
                assert counts[job]['required'] == expected and counts[job]['formal'] == expected
                assert counts[job]['obligations'] == []
                row = await conn.fetchrow('''
                    SELECT j.status,j.analysis_revision,j.current_fact_revision_id,
                           r.core_hash,r.payload AS result_payload,s.payload AS fact_payload
                    FROM analysis_jobs j
                    JOIN analysis_revision_results r ON r.job_uuid=j.job_uuid AND r.revision_id=j.current_fact_revision_id
                    JOIN analysis_fact_snapshots s ON s.job_uuid=r.job_uuid AND s.revision_id=r.revision_id
                    WHERE j.job_uuid=$1
                ''', job)
                assert row and row['analysis_revision'] > 1 and row['current_fact_revision_id'] == data['analysis_revision']
                result, snapshot = decoded(row['result_payload']), decoded(row['fact_payload'])
                assert result['analysis_revision'] == data['analysis_revision'] == snapshot['revision_id']
                assert compute_analysis_result_fingerprint(data) == row['core_hash'] == compute_analysis_result_fingerprint(result)
                assert data['structured_ingest']['fact_snapshot']['snapshot_hash'] == snapshot['snapshot_hash']
                code = snapshot['execution_manifest']['processing_manifest']['code']
                for name in ('src/engine/native_issue_dedup.py', 'src/engine/rules_v33.py'):
                    assert code[name] == digest((MAIN / name).read_bytes())
                original = ROOT / 'uploads' / job / data['filename']
                old_hash = next(item['sha256'] for item in before if item['id'] == job)
                archived = []
                for path in (ROOT / 'uploads' / job / 'preserved_analyses').glob('*/manifest.json'):
                    manifest = json.loads(path.read_text())
                    if manifest.get('files', {}).get('status.json') == old_hash:
                        assert digest((path.parent / 'status.json').read_bytes()) == old_hash
                        archived.append(path.parent)
                assert archived
                old_data = json.loads((archived[0] / 'status.json').read_text())
                assert digest(original.read_bytes()) == data['checksum'] == old_data['checksum']
                if job.startswith('1c770522'):
                    for rule in ('V33-002', 'V33-114'):
                        findings = [item for item in data['review_report']['issues'] if item.get('rule_id') == rule]
                        assert len(findings) == 1
                        finding = findings[0]
                        assert finding['evidence_status'] == 'complete'
                        assert finding['evidence_verification']['status'] == 'verified'
                        assert finding['evidence_verification']['source_identity_complete']
                    assert not any(item.get('rule_id') in {'V33-201', 'V33-109'} for item in data['review_report']['issues'])
                    assert len(counts[job]['cascaded']) == 2
                receipts[job] = {'status': data['status'], 'database_analysis_revision': row['analysis_revision'],
                                 'revision_id': data['analysis_revision'], 'required': counts[job]['required'],
                                 'reference': counts[job]['reference'], 'independent_unverified_checks': 0,
                                 'ai_succeeded_calls': data['result']['meta']['ai_execution']['succeeded_calls'],
                                 'canonical_database_matches_file': True, 'final_source_code_identity_verified': True,
                                 'original_pdf_sha256_unchanged': data['checksum'], 'previous_result_archived_exactly': True}
    finally:
        await conn.close()
    (OUT / 'live_final_verification.json').write_text(json.dumps({'main_sha': source['candidate'], 'verified': True,
                                                                'analyses': receipts}, ensure_ascii=False, indent=2))
    print(json.dumps(receipts, ensure_ascii=False))


asyncio.run(run())
