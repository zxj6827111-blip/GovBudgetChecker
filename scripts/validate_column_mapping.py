"""隔离服务中验证人工列身份校正与原始解释的分离；不做材料准确率验收。"""
from __future__ import annotations

import asyncio
import json
import time


async def _artifact(dsn, job_id):
    import asyncpg
    from src.services.parse_artifact_store import load_parse_artifact
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow('SELECT payload,parse_run_id FROM analysis_fact_snapshots '
            'WHERE job_uuid=$1 AND revision_id=(SELECT current_fact_revision_id '
            'FROM analysis_jobs WHERE job_uuid=$1)', job_id)
        snapshot = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
        return snapshot, await load_parse_artifact(conn, row['parse_run_id'])
    finally:
        await conn.close()


def run_mapping_validation(*, client, env, work, out, job_id, timeout_seconds):
    from src.services.correction_overlay_store import current_analysis_basis_token
    from src.services.analysis_fact_snapshot import canonical_facts, validate_snapshot
    from src.services.input_processing_identity import content_hash
    before, artifact = asyncio.run(_artifact(env['DATABASE_URL'], job_id))
    from src.services.fiscal_column_identity import canonical_column_mappings
    from src.services.fiscal_table_rules import NINE_TABLE_RULES
    instance = next(t for t in artifact.logical_tables if t.table_code == 'FIN_02_income')
    fragment = next(t for t in artifact.table_fragments if t.fragment_uid in instance.fragment_uids)
    mapping = canonical_column_mappings(fragment, NINE_TABLE_RULES[instance.table_code])[0]
    evidence_fact = next(f for f in before['raw_context']['facts']
        if f.get('instance_uid') == instance.instance_uid
        and f.get('indicator') == mapping['canonical_measure'] and f.get('evidence_ids'))
    directory = work / 'uploads' / job_id
    # 确认原件已有的收入列身份，避免控制测试凭空改换一个业务指标。
    body = {'analysis_basis_token': current_analysis_basis_token(job_id, directory),
        'reason': '隔离控制验证：确认列身份后原始解释必须仍可回读',
        'changes': [{'target_type': 'table_instance', 'target_id': instance.instance_uid,
            'field': 'column_mapping', 'value': {'canonical_measure': mapping['canonical_measure'],
                'source_col_idx': mapping['source_col_idx']},
            'evidence_ids': evidence_fact['evidence_ids'][:1]}]}
    response = client.post(f'/api/jobs/{job_id}/corrections', json=body)
    if not response.is_success:
        (out / 'column_mapping_error.json').write_text(response.text)
        raise AssertionError(f'column mapping API rejected request: {response.status_code}; see column_mapping_error.json')
    response.raise_for_status()
    for _ in range(timeout_seconds):
        response = client.get(f'/api/jobs/{job_id}/status')
        if response.status_code == 429:
            time.sleep(2)
            continue
        response.raise_for_status()
        status = response.json()
        if status.get('status') not in {'queued', 'running', 'processing', 'pending'}:
            break
        time.sleep(1)
    else:
        raise TimeoutError('column mapping analysis did not complete')
    after, _ = asyncio.run(_artifact(env['DATABASE_URL'], job_id))
    validate_snapshot(after)
    assert status['status'] != 'error', status.get('error')
    assert before['revision_id'] != after['revision_id']
    assert before['parse_run_uid'] != after['parse_run_uid']
    assert before['raw_context'] == after['raw_context'], 'column mapping overwrote raw interpretation'
    origin = after['processing_manifest']['raw_interpretation']
    assert origin['context_hash'] == content_hash(before['raw_context'])
    for view in ('raw', 'effective'):
        facts = after[view + '_context']['facts']
        actual = []
        for offset in range(0, max(1, len(facts)), 1000):
            response = client.get(f'/api/jobs/{job_id}/fact-snapshot',
                params={'view': view, 'limit': 1000, 'offset': offset})
            response.raise_for_status()
            assert response.json()['snapshot_hash'] == after['snapshot_hash']
            actual.extend(response.json()['facts'])
        assert canonical_facts(actual) == canonical_facts(facts)
    receipt = {'passed': True, 'job_id': job_id, 'old_revision': before['revision_id'],
        'new_revision': after['revision_id'], 'raw_interpretation_preserved': True,
        'both_views_database_and_http_verified': True, 'independent_accuracy_acceptance': False}
    (out / 'column_mapping_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt
