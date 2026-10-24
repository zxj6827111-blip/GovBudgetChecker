"""不可变原始/有效财政事实快照，以及实际数据库往返校验。"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import os
import tempfile
from typing import Any, Sequence

from src.schemas.parse_artifact import FiscalFact
from src.services.input_processing_identity import content_hash, artifact_content_hash

SNAPSHOT_VERSION = 'gbc.analysis-fact-snapshot.v1'
_JSON_FIELDS = ('hierarchy_path', 'evidence_ids', 'extra_dims')


def canonical_fact(value: Any) -> dict:
    if isinstance(value, FiscalFact):
        data = value.to_dict()
    else:
        data = dict(value)
        for key in _JSON_FIELDS:
            if isinstance(data.get(key), str):
                data[key] = json.loads(data[key])
        data = FiscalFact.from_dict(data).to_dict()
    if data.get('normalized_value') is not None:
        data['normalized_value'] = format(Decimal(data['normalized_value']).normalize(), 'f')
    data['evidence_ids'] = sorted(data['evidence_ids'])
    return data


def canonical_facts(facts: Sequence) -> list[dict]:
    result = sorted((canonical_fact(f) for f in facts), key=lambda f: f['fact_uid'])
    if len({f['fact_uid'] for f in result}) != len(result):
        raise ValueError('duplicate fiscal fact UID in snapshot')
    return result


def facts_hash(facts: Sequence) -> str:
    return content_hash(canonical_facts(facts))


def bind_raw_interpretation(artifact, raw_context, mapping_report: dict) -> None:
    """保留人工列映射前的解释，不能把校正后重建结果冒充原始事实。"""
    if not mapping_report.get('applied') or raw_context is None:
        return
    raw = raw_context.to_dict()
    if raw.get('file_hash') != artifact.source_file_hash:
        raise ValueError('column mapping raw interpretation has different source')
    artifact.processing_manifest['raw_interpretation'] = {
        'source_file_hash': raw['file_hash'], 'parse_run_uid': raw['parse_run_uid'],
        'context_hash': content_hash(raw), 'column_mapping': deepcopy(mapping_report),
    }


def context_is_source_bound(artifact, value, *, raw: bool = False) -> bool:
    ctx = value.to_dict() if hasattr(value, 'to_dict') else value
    if ctx.get('file_hash') != artifact.source_file_hash:
        return False
    if ctx.get('parse_run_uid') == artifact.parse_run_uid:
        return True
    origin = artifact.processing_manifest.get('raw_interpretation') or {}
    return bool(raw and origin.get('column_mapping', {}).get('applied')
        and origin.get('source_file_hash') == artifact.source_file_hash
        and origin.get('parse_run_uid') == ctx.get('parse_run_uid')
        and origin.get('context_hash') == content_hash(ctx))


def make_snapshot(*, job_uuid: str, revision_id: str, artifact, raw_context, effective_context) -> dict:
    def context(value):
        return deepcopy(value.to_dict() if hasattr(value, 'to_dict') else value or {'facts': []})
    raw, effective = context(raw_context), context(effective_context)
    for name, ctx in [('raw', raw), ('effective', effective)]:
        if ctx.get('facts') and not context_is_source_bound(artifact, ctx, raw=name == 'raw'):
            raise ValueError(f'{name} fact context differs from artifact source/run')
    if not job_uuid or not revision_id:
        raise ValueError('fact snapshot requires job and revision identity')
    payload = {'schema_version': SNAPSHOT_VERSION, 'job_uuid': job_uuid,
               'revision_id': revision_id, 'source_file_hash': artifact.source_file_hash,
               'parse_run_uid': artifact.parse_run_uid,
               'artifact_content_hash': artifact_content_hash(artifact),
               'processing_manifest': getattr(artifact, 'processing_manifest', {}),
               'raw_context': raw, 'effective_context': effective,
               'raw_facts_hash': facts_hash(raw.get('facts', [])),
               'effective_facts_hash': facts_hash(effective.get('facts', []))}
    payload['snapshot_hash'] = content_hash(payload)
    return payload


def validate_snapshot(payload: dict) -> dict:
    value = deepcopy(payload)
    digest = value.pop('snapshot_hash', None)
    if value.get('schema_version') != SNAPSHOT_VERSION or content_hash(value) != digest:
        raise ValueError('analysis fact snapshot hash mismatch')
    for kind in ('raw', 'effective'):
        if facts_hash(value[f'{kind}_context'].get('facts', [])) != value[f'{kind}_facts_hash']:
            raise ValueError(f'{kind} fiscal facts hash mismatch')
    return payload


def verify_consumed_snapshot(snapshot: dict, *, artifact, raw_context, effective_context) -> None:
    """规则只能消费冻结事实；执行中意外改值或改身份必须在写库前阻断。"""
    validate_snapshot(snapshot)
    current = make_snapshot(job_uuid=snapshot['job_uuid'], revision_id=snapshot['revision_id'],
                            artifact=artifact, raw_context=raw_context,
                            effective_context=effective_context)
    if current != snapshot:
        raise ValueError('consumed fact snapshot changed during analysis')


def write_snapshot(path: Path, payload: dict) -> None:
    validate_snapshot(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if json.loads(path.read_text()) != payload:
            raise ValueError('immutable fact snapshot already contains different data')
        return
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.fact-snapshot-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f, ensure_ascii=False, sort_keys=True, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        # 独占创建，避免并发把同一revision的不同快照互相覆盖。
        os.link(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


async def verify_persisted_facts(conn, *, parse_run_id: int, facts: Sequence) -> dict:
    from src.services.parse_artifact_store import load_parse_facts, load_parse_evidence
    actual = await load_parse_facts(conn, parse_run_id)
    expected_hash, actual_hash = facts_hash(facts), facts_hash(actual)
    if len(actual) != len(facts) or actual_hash != expected_hash:
        expected_map = {f['fact_uid']: f for f in canonical_facts(facts)}
        actual_map = {f['fact_uid']: f for f in canonical_facts(actual)}
        differences = [{'fact_uid': uid, 'fields': [key for key in expected_map.get(uid, {})
            if expected_map.get(uid, {}).get(key) != actual_map.get(uid, {}).get(key)] or ['missing_or_extra']}
            for uid in sorted(expected_map.keys() | actual_map.keys())
            if expected_map.get(uid) != actual_map.get(uid)][:5]
        raise ValueError('persisted fiscal facts differ from raw snapshot: ' + json.dumps(differences, ensure_ascii=False))
    evidence = {str(e['evidence_id']): e for e in await load_parse_evidence(conn, parse_run_id)}
    for fact in facts:
        for ref in fact.extra_dims.get('evidence_refs', []):
            row = evidence.get(str(ref.get('evidence_id')))
            if row is None:
                raise ValueError('persisted fiscal evidence missing')
            for field in ('file_hash', 'physical_page', 'printed_page', 'source_cell_id', 'source_block_id',
                          'quote', 'source_method', 'verification_status', 'location_level'):
                if row.get(field) != ref.get(field):
                    raise ValueError(f'persisted fiscal evidence differs: {field}')
            stored_provenance = row.get('source_provenance') or {}
            if isinstance(stored_provenance, str):
                stored_provenance = json.loads(stored_provenance)
            if stored_provenance != (ref.get('source_provenance') or {}):
                raise ValueError('persisted fiscal evidence source provenance differs')
            def box(v): return list(v) if v else None
            if box(row.get('bbox')) != box(ref.get('bbox')):
                raise ValueError('persisted fiscal evidence bbox differs')
    return {'verified': True, 'facts_count': len(actual), 'facts_hash': actual_hash}


async def persist_snapshot(conn, *, snapshot: dict, parse_run_id: int) -> dict:
    validate_snapshot(snapshot)
    async with conn.transaction():
        await conn.execute('''INSERT INTO analysis_fact_snapshots
            (job_uuid, revision_id, parse_run_id, source_file_hash, snapshot_hash,
             raw_facts_hash, effective_facts_hash, payload)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
            ON CONFLICT(job_uuid, revision_id) DO NOTHING''',
            snapshot['job_uuid'], snapshot['revision_id'], parse_run_id,
            snapshot['source_file_hash'], snapshot['snapshot_hash'], snapshot['raw_facts_hash'],
            snapshot['effective_facts_hash'], json.dumps(snapshot, ensure_ascii=False))
        row = await conn.fetchrow('SELECT payload FROM analysis_fact_snapshots WHERE job_uuid=$1 AND revision_id=$2',
                                  snapshot['job_uuid'], snapshot['revision_id'])
        actual = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
        validate_snapshot(actual)
        if actual != snapshot:
            raise ValueError('immutable database analysis snapshot differs from current snapshot')
    return {'snapshot_hash': snapshot['snapshot_hash'], 'revision_id': snapshot['revision_id'],
            'raw_facts_hash': snapshot['raw_facts_hash'], 'effective_facts_hash': snapshot['effective_facts_hash'],
            'storage_consistent': True}


async def publish_snapshot(conn, payload: dict) -> None:
    """在结果提交事务内切换权威指针；文件指针只是可重试的镜像。"""
    receipt = (payload.get('structured_ingest') or {}).get('fact_snapshot')
    if receipt is None:
        return  # 历史结果允许继续读取，不冒充新的可信事实版本。
    revision = str(payload.get('analysis_revision') or '')
    current = await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1 FOR UPDATE', payload['job_id'])
    row = await conn.fetchrow('SELECT payload,parse_run_id FROM analysis_fact_snapshots '
                              'WHERE job_uuid=$1 AND revision_id=$2 FOR UPDATE',
                              payload['job_id'], revision)
    if row is None:
        raise ValueError('cannot publish analysis without persisted fiscal snapshot')
    snapshot = row['payload']
    if isinstance(snapshot, str):
        snapshot = json.loads(snapshot)
    validate_snapshot(snapshot)
    for kind in ('raw','effective'):
        for data in snapshot[kind+'_context'].get('facts',[]):
            if data['recognition_status']not in {'unverified','verified'}:continue
            required=('org_name','org_scope','fiscal_year','period','fund_scope','budget_stage','table_code','instance_uid','indicator')
            from src.engine.fact_context import normalized_amount_is_usable
            refs=data.get('extra_dims',{}).get('evidence_refs')or[]
            anchored=any(r.get('file_hash')==snapshot['source_file_hash']and r.get('physical_page')
                         and(r.get('source_cell_id')or r.get('source_block_id'))for r in refs)
            if (data.get('extra_dims',{}).get('identity_incomplete')or any(not data.get(k)for k in required)
                    or not(data.get('subject_code')or data.get('subject_name'))or not anchored
                    or not normalized_amount_is_usable(data.get('normalized_value'))):
                raise ValueError('incomplete fiscal identity/source cannot be published as an admitted fact')
    for key in ('revision_id', 'snapshot_hash', 'raw_facts_hash', 'effective_facts_hash'):
        if receipt.get(key) != snapshot.get(key):
            raise ValueError('analysis publication snapshot mismatch: ' + key)
    if receipt.get('storage_consistent') is not True:
        raise ValueError('analysis publication storage consistency unconfirmed')
    if ('expected_current_fact_revision_id' in payload
            and current not in (payload['expected_current_fact_revision_id'], revision)):
        raise ValueError('analysis candidate superseded; current revision has changed')
    if current and current != revision and 'expected_current_fact_revision_id' not in payload:
        raise ValueError('analysis candidate publication requires expected current revision')
    await verify_persisted_facts(conn,parse_run_id=row['parse_run_id'],
        facts=[FiscalFact.from_dict(f)for f in snapshot['raw_context'].get('facts',[])])
    from src.services.fact_compatibility_projection import publish_compatibility_projection
    await publish_compatibility_projection(conn, snapshot, row['parse_run_id'])
    from src.services.analysis_result_store import compute_analysis_result_fingerprint
    core_hash = compute_analysis_result_fingerprint(payload)
    await conn.execute('INSERT INTO analysis_revision_results(job_uuid,revision_id,core_hash,payload) '
                       'VALUES($1,$2,$3,$4::jsonb) ON CONFLICT DO NOTHING',
                       payload['job_id'], revision, core_hash, json.dumps(payload, ensure_ascii=False))
    stored_core = await conn.fetchval('SELECT core_hash FROM analysis_revision_results WHERE job_uuid=$1 AND revision_id=$2',
                                     payload['job_id'],revision)
    if stored_core != core_hash:
        raise ValueError('immutable analysis revision has different result content')
    await conn.execute('UPDATE analysis_fact_snapshots SET publication_state=\'published\', '
                       'mirror_pending=TRUE WHERE job_uuid=$1 AND revision_id=$2', payload['job_id'], revision)
    await conn.execute('UPDATE analysis_jobs SET current_fact_revision_id=$2 '
                       'WHERE job_uuid=$1', payload['job_id'], revision)


async def read_database_snapshot(job_uuid: str, revision_id: str | None = None):
    from src.db.connection import DatabaseConnection
    from src.services.analysis_result_store import ensure_analysis_persistence_ready
    if not await ensure_analysis_persistence_ready():
        raise RuntimeError('fiscal snapshot database unavailable')
    conn = await DatabaseConnection.acquire()
    try:
        if revision_id is None:
            revision_id = await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1', job_uuid)
        if not revision_id:
            return None
        row = await conn.fetchrow('SELECT payload FROM analysis_fact_snapshots WHERE job_uuid=$1 AND revision_id=$2', job_uuid, revision_id)
        if row is None:
            raise ValueError('published fiscal snapshot is missing')
        snapshot = json.loads(row['payload']) if isinstance(row['payload'], str) else row['payload']
        validate_snapshot(snapshot)
        if snapshot['job_uuid'] != job_uuid or snapshot['revision_id'] != revision_id:
            raise ValueError('database fiscal snapshot identity mismatch')
        return snapshot
    finally:
        await DatabaseConnection.release(conn)


async def synchronize_published_mirror(job_uuid: str, job_dir: Path) -> dict:
    """从数据库权威版本修复文件镜像，读回后才清除待同步标记。"""
    from src.db.connection import DatabaseConnection
    from src.services.analysis_result_store import compute_analysis_result_fingerprint
    from api import runtime
    if job_dir.name != job_uuid:
        raise ValueError('fiscal mirror directory identity mismatch')
    conn = await DatabaseConnection.acquire()
    try:
        async with conn.transaction():
            # 与发布流程使用同一任务锁，避免旧版本镜像越过更新后的权威指针。
            revision = await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs '
                'WHERE job_uuid=$1 FOR UPDATE', job_uuid)
            if not revision:
                return {'status': 'legacy_without_snapshot'}
            row = await conn.fetchrow('SELECT s.payload AS snapshot,r.payload AS result,r.core_hash '
                'FROM analysis_fact_snapshots s JOIN analysis_revision_results r '
                'ON r.job_uuid=s.job_uuid AND r.revision_id=s.revision_id '
                'WHERE s.job_uuid=$1 AND s.revision_id=$2', job_uuid, revision)
            if row is None:
                raise ValueError('published revision is missing immutable content')
            def decode(value): return json.loads(value) if isinstance(value,str) else value
            snapshot, result = decode(row['snapshot']), decode(row['result'])
            validate_snapshot(snapshot)
            if (snapshot['job_uuid'],snapshot['revision_id']) != (job_uuid,revision):
                raise ValueError('published mirror snapshot identity mismatch')
            if (result.get('job_id'),result.get('analysis_revision')) != (job_uuid,revision):
                raise ValueError('published mirror result identity mismatch')
            if compute_analysis_result_fingerprint(result) != row['core_hash']:
                raise ValueError('published mirror immutable result hash mismatch')
            receipt = (result.get('structured_ingest') or {}).get('fact_snapshot') or {}
            if receipt.get('snapshot_hash') != snapshot['snapshot_hash']:
                raise ValueError('published mirror result and snapshot differ')
            write_snapshot(job_dir/'fact_snapshots'/(revision+'.json'), snapshot)
            runtime.write_analysis_revision_snapshot(job_dir, result, publish_pointer=True)
            current_status=runtime.read_json_file(job_dir/'status.json',default={})
            if str(current_status.get('status')or'')in runtime.ACTIVE_ANALYSIS_STATUSES:
                # 已启动下一次重算时保留其进度，当前可见报告仍绑定数据库版本。
                visible={**current_status,'result':result.get('result'),
                         'analysis_revision':revision,'structured_ingest':result.get('structured_ingest'),
                         'review_report':result.get('review_report')}
            else:
                visible=result
            runtime.write_json_file(job_dir/'status.json',visible)
            if isinstance(result.get('structured_ingest'),dict):
                runtime.write_structured_ingest_payload(job_dir,result['structured_ingest'])
            archive = job_dir/'analysis_versions'/revision
            archived = json.loads((archive/'status.json').read_text())
            if (compute_analysis_result_fingerprint(archived) != row['core_hash']
                    or json.loads((archive/'fact_snapshot.json').read_text()) != snapshot
                    or json.loads((archive/'fact_context.json').read_text()) != snapshot['effective_context']
                    or runtime.read_analysis_revision_pointer(job_dir).get('revision_id') != revision):
                raise ValueError('published mirror readback differs from database')
            visible_readback=runtime.read_json_file(job_dir/'status.json',default={})
            if (visible_readback.get('analysis_revision')!=revision
                    or visible_readback.get('result')!=result.get('result')):
                raise ValueError('published current status mirror differs from database')
            await conn.execute('UPDATE analysis_fact_snapshots SET mirror_pending=FALSE '
                               'WHERE job_uuid=$1 AND revision_id=$2',job_uuid,revision)
            return {'status':'synchronized','revision_id':revision,
                    'snapshot_hash':snapshot['snapshot_hash']}
    finally:
        await DatabaseConnection.release(conn)


async def read_database_result(job_uuid: str, revision_id: str | None = None):
    """当前和历史导出从同一不可变结果读取，文件镜像缺失不切换版本。"""
    from src.db.connection import DatabaseConnection
    from src.services.analysis_result_store import ensure_analysis_persistence_ready, compute_analysis_result_fingerprint
    if not await ensure_analysis_persistence_ready():
        raise RuntimeError('analysis result database unavailable')
    conn = await DatabaseConnection.acquire()
    try:
        if revision_id is None:
            revision_id = await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1',job_uuid)
        if not revision_id:
            return None
        row = await conn.fetchrow('SELECT s.snapshot_hash,r.payload,r.core_hash FROM analysis_fact_snapshots s '
            'LEFT JOIN analysis_revision_results r ON s.job_uuid=r.job_uuid AND s.revision_id=r.revision_id '
            'WHERE s.job_uuid=$1 AND s.revision_id=$2',job_uuid,revision_id)
        if row is None:
            return None  # 无此新制版本时才允许读取旧文件格式。
        if row['payload'] is None:
            raise ValueError('immutable analysis result missing for fact snapshot')
        payload = json.loads(row['payload']) if isinstance(row['payload'],str) else row['payload']
        if (payload.get('job_id'),payload.get('analysis_revision')) != (job_uuid,revision_id):
            raise ValueError('immutable analysis result identity mismatch')
        if compute_analysis_result_fingerprint(payload) != row['core_hash']:
            raise ValueError('immutable analysis result content hash mismatch')
        receipt = (payload.get('structured_ingest') or {}).get('fact_snapshot') or {}
        if receipt.get('snapshot_hash') != row['snapshot_hash']:
            raise ValueError('immutable analysis result fact snapshot mismatch')
        return payload
    finally:
        await DatabaseConnection.release(conn)


async def current_revision(job_uuid: str):
    from src.db.connection import DatabaseConnection
    from src.services.analysis_result_store import ensure_analysis_persistence_ready
    if not await ensure_analysis_persistence_ready():
        raise RuntimeError('analysis database unavailable before processing')
    conn = await DatabaseConnection.acquire()
    try:
        return await conn.fetchval('SELECT current_fact_revision_id FROM analysis_jobs WHERE job_uuid=$1', job_uuid)
    finally:
        await DatabaseConnection.release(conn)
