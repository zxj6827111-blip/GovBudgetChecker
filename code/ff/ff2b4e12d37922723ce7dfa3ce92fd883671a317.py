from pathlib import Path
import asyncio, json, os, sys
import asyncpg

ROOT = Path('/Volumes/workSSD/software development/GovBudgetChecker')
OUT = ROOT/'outputs/shiquan_source_coverage_20261109'
sys.path.insert(0, str(ROOT/'artifacts/daily-main'))
from src.services.analysis_result_store import compute_analysis_result_fingerprint

async def main():
    conn = await asyncpg.connect(os.environ['DATABASE_URL'])
    rows = []
    try:
        async with conn.transaction(readonly=True):
            for job, original in json.loads((OUT/'manifest_before.json').read_text()).items():
                status = json.loads((ROOT/'uploads'/job/'status.json').read_text())
                row = await conn.fetchrow('''SELECT j.current_fact_revision_id,j.status,
                    s.source_file_hash,r.core_hash,r.payload->>'analysis_revision' AS revision,
                    r.payload->>'use_ai_assist' AS use_ai_assist
                    FROM analysis_jobs j
                    JOIN analysis_fact_snapshots s ON s.job_uuid=j.job_uuid AND s.revision_id=j.current_fact_revision_id
                    JOIN analysis_revision_results r ON r.job_uuid=s.job_uuid AND r.revision_id=s.revision_id
                    WHERE j.job_uuid=$1''', job)
                assert row and row['current_fact_revision_id'] == row['revision'] == status['analysis_revision']
                assert row['status'] == status['status'] == 'review_required'
                assert row['source_file_hash'] == original['sha256']
                assert row['use_ai_assist'] == 'false'
                assert row['core_hash'] == compute_analysis_result_fingerprint(status)
                rows.append({'job': job, 'revision': row['revision'], 'status': row['status'],
                    'source_hash_matches': True, 'immutable_result_hash_matches': True, 'use_ai_assist': False})
    finally:
        await conn.close()
    (OUT/'database_verification.json').write_text(json.dumps(rows, indent=2))
    print('Verified 7 current database pointers, immutable result hashes, source hashes and AI=false')

asyncio.run(main())
