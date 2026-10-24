"""真实缺陷对应的快照/往返反例；禁止只比较写入循环次数。"""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest
from src.engine.fact_context import FactContext
from src.schemas.parse_artifact import FiscalFact, ParseArtifact
from src.services.analysis_fact_snapshot import (canonical_facts, facts_hash, make_snapshot,
    validate_snapshot, verify_persisted_facts, write_snapshot)
from src.services.input_processing_identity import artifact_content_hash
from src.engine.fact_context import remap_overlay_targets, FactOverlay


def fact():
    return FiscalFact(fact_uid='cell-1', indicator='total_actual', raw_value='12.00',
        unit='万元', normalized_value=Decimal('12.00'), org_name='测试局', fiscal_year=2023,
        table_code='FIN_07_three_public', subject_code='total', budget_stage='决算',
        recognition_status='unverified', confidence=.9)


def contexts():
    artifact = ParseArtifact(source_file_hash='a'*64, parse_run_uid='run-1')
    raw = FactContext(facts=[fact()],file_hash=artifact.source_file_hash,parse_run_uid='run-1')
    effective = deepcopy(raw)
    effective.facts[0].normalized_value = Decimal('13')
    effective.overlay_records = [{'record_id':'manual-1'}]
    return artifact, raw, effective


@pytest.mark.asyncio
async def test_historical_evidence_reads_immutable_database_result_without_file_fallback(monkeypatch):
    from api.routes import corrections
    from src.services import analysis_fact_snapshot
    monkeypatch.setenv('DATABASE_URL', 'postgresql://isolated-test-only')
    reader = AsyncMock(return_value={'job_id': 'job', 'analysis_revision': 'old', 'immutable': True})
    monkeypatch.setattr(analysis_fact_snapshot, 'read_database_result', reader)
    monkeypatch.setattr(corrections, '_revision_payload', lambda *_: pytest.fail('mutable file fallback'))
    payload = await corrections._bound_evidence_payload('job', 'old')
    assert payload['immutable'] is True
    reader.assert_awaited_once_with('job', 'old')
    reader.side_effect = ValueError('historical result hash mismatch')
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await corrections._bound_evidence_payload('job', 'old')
    assert error.value.status_code == 409


def test_immutable_raw_and_effective_snapshot_and_identity_tamper(tmp_path):
    artifact, raw, effective = contexts()
    snapshot = make_snapshot(job_uuid='job',revision_id='rev-1',artifact=artifact,
        raw_context=raw,effective_context=effective)
    assert snapshot['raw_facts_hash'] != snapshot['effective_facts_hash']
    path = tmp_path/'rev-1.json'
    write_snapshot(path,snapshot)
    write_snapshot(path,snapshot)
    changed = deepcopy(snapshot)
    changed['effective_context']['facts'][0]['subject_name']='另一科目'
    with pytest.raises(ValueError,match='hash mismatch'):validate_snapshot(changed)
    effective.facts[0].normalized_value = Decimal('14')
    changed=make_snapshot(job_uuid='job',revision_id='rev-1',artifact=artifact,
        raw_context=raw,effective_context=effective)
    with pytest.raises(ValueError,match='immutable'):write_snapshot(path,changed)
    assert json.loads(path.read_text())==snapshot


def test_artifact_content_comparison_ignores_only_run_attempt_identity():
    artifact,_,_=contexts()
    second=deepcopy(artifact)
    second.parse_run_uid='run-2'
    assert artifact_content_hash(artifact)==artifact_content_hash(second)
    second.reading_assessment={'status':'partial'}
    assert artifact_content_hash(artifact)!=artifact_content_hash(second)


def test_runtime_timing_updates_never_overwrite_historical_facts_or_report(tmp_path):
    from api import runtime
    artifact,raw,effective=contexts()
    job=tmp_path/'job'
    job.mkdir()
    snapshot=make_snapshot(job_uuid='job',revision_id='rev-1',artifact=artifact,
                           raw_context=raw,effective_context=effective)
    write_snapshot(job/'fact_snapshots/rev-1.json',snapshot)
    receipt={key:snapshot[key]for key in ('snapshot_hash','raw_facts_hash','effective_facts_hash','revision_id')}
    receipt['storage_consistent']=True
    payload={'job_id':'job','status':'done','analysis_revision':'rev-1',
             'structured_ingest':{'fact_snapshot':receipt},'result':{'meta':{'fact_snapshot':receipt}}}
    runtime.write_analysis_revision_snapshot(job,payload)
    archived=job/'analysis_versions/rev-1/status.json'
    original=archived.read_bytes()
    (job/'fact_context.json').write_text(json.dumps({'facts':[{'wrong':'other analysis'}]}))
    payload['pipeline_budget']={'outcome':'completed','elapsed_seconds':1}
    runtime.write_analysis_revision_snapshot(job,payload)
    assert archived.read_bytes()==original
    assert json.loads((archived.parent/'fact_context.json').read_text())==snapshot['effective_context']
    payload['result']['rule_findings']=[{'rule':'changed','message':'different business result'}]
    with pytest.raises(ValueError,match='immutable analysis revision'):
        runtime.write_analysis_revision_snapshot(job,payload)


@pytest.mark.parametrize('change',['same_uid_identity','instance','source','file'])
def test_correction_migration_requires_complete_identity_and_original_anchor(change):
    old=fact()
    old.instance_uid='table-1'
    old.extra_dims['evidence_refs']=[{'file_hash':'frozen','physical_page':1,
                                    'source_cell_id':'grid:r1c2','bbox':[1,2,3,4]}]
    new=deepcopy(old)
    if change=='same_uid_identity':new.indicator='project_actual'
    if change=='instance':new.instance_uid='table-2'
    if change=='source':new.extra_dims['evidence_refs'][0]['source_cell_id']='grid:r2c2'
    context=FactContext([new],file_hash='different'if change=='file'else'frozen')
    mapped,unresolved,_=remap_overlay_targets([old],context,[FactOverlay(old.fact_uid,raw_value='13')])
    assert not mapped and unresolved


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['missing','admission','identity','unit','evidence'])
async def test_readback_rejects_missing_or_changed_fact_even_if_writer_reports_success(change):
    f=fact()
    row=f.to_dict()
    if change=='admission':row['recognition_status']='low_confidence'
    if change=='identity':row['subject_name']='其他科目'
    if change=='unit':row['unit']='元'
    if change=='evidence':
        f.extra_dims['evidence_refs']=[{'evidence_id':'missing'}]
        row=f.to_dict()
    conn=SimpleNamespace(fetch=AsyncMock(side_effect=[[]if change=='missing'else[row],[]]))
    with pytest.raises(ValueError):
        await verify_persisted_facts(conn,parse_run_id=1,facts=[f])


@pytest.mark.asyncio
async def test_readback_accepts_database_json_fields_and_decimal_equivalence():
    f=fact()
    row=f.to_dict()
    row['normalized_value']='12.00000'
    for k in ['extra_dims','evidence_ids','hierarchy_path']:row[k]=json.dumps(row[k])
    conn=SimpleNamespace(fetch=AsyncMock(side_effect=[[row],[]]))
    assert (await verify_persisted_facts(conn,parse_run_id=1,facts=[f]))['verified']


@pytest.mark.asyncio
async def test_cache_must_not_replace_supplied_different_artifact(monkeypatch,tmp_path):
    from src.services import structured_ingest_runner as runner
    artifact,_,_=contexts()
    cached=deepcopy(artifact)
    cached.reading_assessment={'wrong':True}
    source = tmp_path/'source.pdf'
    source.write_bytes(b'%PDF-1.4\n%%EOF')
    artifact.source_file_hash = runner._sha256_file(source)
    cached.source_file_hash = artifact.source_file_hash
    for k,v in {'ensure_structured_ingest_ready':AsyncMock(return_value=True),
        '_ensure_document_version':AsyncMock(return_value={'document_id':1,'document_version_id':1}),
        '_allocate_material_slot':AsyncMock(return_value={}),
        'find_cached_parse_run':AsyncMock(return_value=7),
        'load_parse_artifact':AsyncMock(return_value=cached),
        'persist_parse_artifact':AsyncMock()}.items():monkeypatch.setattr(runner,k,v)
    monkeypatch.setattr(runner.DatabaseConnection,'acquire',AsyncMock(return_value=object()))
    monkeypatch.setattr(runner.DatabaseConnection,'release',AsyncMock())
    result=await runner.run_structured_ingest('job',source,{
        'checksum':artifact.source_file_hash,'organization_name':'测试局'},parse_artifact=artifact)
    assert result['status']=='error'
    assert 'cache_key_conflict'in result['error']
    runner.persist_parse_artifact.assert_not_awaited()
    assert artifact.reading_assessment=={}


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["metadata", "artifact"])
async def test_original_hash_mismatch_blocks_before_database_write(monkeypatch, tmp_path, mismatch):
    from src.services import structured_ingest_runner as runner
    source = tmp_path/'source.pdf'
    source.write_bytes(b'%PDF-1.4 original')
    digest = runner._sha256_file(source)
    artifact = ParseArtifact(source_file_hash=digest)
    metadata = {'checksum': digest}
    if mismatch == 'metadata': metadata['checksum'] = '0'*64
    else: artifact.source_file_hash = '0'*64
    monkeypatch.setattr(runner, 'ensure_structured_ingest_ready', AsyncMock(return_value=True))
    acquire = AsyncMock()
    monkeypatch.setattr(runner.DatabaseConnection, 'acquire', acquire)
    result = await runner.run_structured_ingest('hash-test',source,metadata,parse_artifact=artifact)
    assert result['status'] == 'error'
    assert 'source_file_hash_mismatch' in result['error']
    acquire.assert_not_awaited()
    assert artifact.source_file_hash == ('0'*64 if mismatch=='artifact' else digest)


@pytest.mark.parametrize('mutation', ['amount','identity','qualification','artifact'])
def test_rules_cannot_mutate_the_frozen_input(mutation):
    from src.services.analysis_fact_snapshot import verify_consumed_snapshot
    artifact,raw,effective = contexts()
    snapshot = make_snapshot(job_uuid='job', revision_id='rev',artifact=artifact,
                             raw_context=raw,effective_context=effective)
    verify_consumed_snapshot(snapshot,artifact=artifact,raw_context=raw,effective_context=effective)
    if mutation=='amount':effective.facts[0].normalized_value = Decimal('99')
    elif mutation=='identity':effective.facts[0].subject_code='2210201'
    elif mutation=='qualification':effective.facts[0].recognition_status='low_confidence'
    else:artifact.reading_assessment={'changed':True}
    with pytest.raises(ValueError,match='changed during analysis'):
        verify_consumed_snapshot(snapshot,artifact=artifact,raw_context=raw,effective_context=effective)


def test_rule_configuration_is_part_of_processing_identity(monkeypatch,tmp_path):
    from src.services import input_processing_identity as identity
    (tmp_path/'rules').mkdir()
    rule = tmp_path/'rules/v3_3.yaml'
    rule.write_text('rules: []')
    for name in ('api/main.py','api/runtime.py','src/db/migrations.py'):
        path = tmp_path/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text('# test')
    monkeypatch.setattr(identity,'ROOT',tmp_path)
    identity.runtime_code_manifest.cache_clear()
    first = identity.processing_manifest()
    rule.write_text('rules: [changed]')
    # 进程加载身份冻结，只有重新加载后的实现才能取得新身份。
    assert identity.processing_manifest()==first
    identity.runtime_code_manifest.cache_clear()
    second = identity.processing_manifest()
    assert second['sha256']!=first['sha256']
    assert 'changed' not in json.dumps(second)
    identity.runtime_code_manifest.cache_clear()


def test_trusted_archive_cannot_switch_database_owned_pointer(monkeypatch,tmp_path):
    from api import runtime
    monkeypatch.setenv('DATABASE_URL','configured-by-test')
    artifact,raw,effective=contexts()
    job=tmp_path/'job'
    job.mkdir()
    snap=make_snapshot(job_uuid='job',revision_id='rev-new',artifact=artifact,
                       raw_context=raw,effective_context=effective)
    write_snapshot(job/'fact_snapshots/rev-new.json',snap)
    pointer=job/'analysis_versions/current.json'
    pointer.parent.mkdir()
    pointer.write_text(json.dumps({'revision_id':'rev-current'}))
    receipt={k:snap[k]for k in ('revision_id','snapshot_hash','raw_facts_hash','effective_facts_hash')}
    result=runtime.write_analysis_revision_snapshot(job,{'job_id':'job','analysis_revision':'rev-new',
        'status':'done','structured_ingest':{'fact_snapshot':receipt}})
    assert result['mirror_pending']
    assert json.loads(pointer.read_text())['revision_id']=='rev-current'
    assert (pointer.parent/'rev-new/fact_snapshot.json').exists()


def test_semantic_configuration_change_cannot_reuse_parse_identity(monkeypatch):
    from src.services.input_processing_identity import processing_config_fingerprint
    monkeypatch.setenv('OCR_TILE_WIDTH','512')
    first=processing_config_fingerprint('caller-config')
    assert processing_config_fingerprint(first)==first
    monkeypatch.setenv('OCR_TILE_WIDTH','1024')
    second=processing_config_fingerprint(first)
    assert second!=first and second.count(':processing-v2:')==1
    assert second.startswith('caller-config:')
