"""兼容投影必须消费有效快照，逐笔保留身份、金额、资格和版本。"""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
import json

import pytest

from src.engine.fact_context import FactContext
from src.schemas.parse_artifact import ParseArtifact, FiscalFact
from src.services.analysis_fact_snapshot import make_snapshot
from src.services.analysis_fact_snapshot import canonical_facts
from src.services.fact_compatibility_projection import (
    snapshot_table_payloads, snapshot_line_item_rows, snapshot_reference,
    verify_legacy_fact_projection, verify_shared_fact_projection,
    effective_legacy_artifact,
)


def snapshot():
    artifact = ParseArtifact(source_file_hash='a'*64, parse_run_uid='run')
    fact = FiscalFact('one','total_actual','9007199254740993.01','万元',Decimal('9007199254740993.01'),
                      org_name='测试局',fiscal_year=2023,subject_code='2130105',subject_name='行政运行',
                      table_code='FIN_05_general_public_expenditure',instance_uid='table-1',row_order=2,
                      budget_stage='决算',recognition_status='unverified',confidence=.7822)
    second = deepcopy(fact)
    second.fact_uid='two';second.budget_stage='全年预算';second.recognition_status='ambiguous'
    third = deepcopy(fact)
    third.fact_uid='three';third.instance_uid='table-2';third.subject_code='2130106'
    raw = FactContext([fact,second,third],file_hash=artifact.source_file_hash,parse_run_uid='run')
    effective = deepcopy(raw)
    effective.facts[0].normalized_value=Decimal('13.01')
    effective.overlay_records=[{'record_id':'correction-1'}]
    return make_snapshot(job_uuid='job',revision_id='rev',artifact=artifact,raw_context=raw,effective_context=effective)


def test_projection_preserves_equal_amount_different_stage_instance_and_corrected_value():
    value=snapshot()
    tables=snapshot_table_payloads(value)
    assert len(tables)==1 and tables[0]['row_count']==4
    assert tables[0]['data_json']['facts']==canonical_facts(value['effective_context']['facts'])
    lines=snapshot_line_item_rows(value)
    assert len(lines)==3 and len({row['row_index']for row in lines})==3
    projected={f['fact_uid']:f for row in lines for f in row['values_json']['_facts']}
    assert projected['one']['normalized_value']=='13.01'
    assert projected['two']['normalized_value']=='9007199254740993.01'
    assert projected['two']['recognition_status']=='ambiguous'
    assert projected['two']['confidence']==.7822
    assert projected['two']['budget_stage']=='全年预算'
    assert projected['three']['instance_uid']=='table-2'
    assert value['raw_context']['facts'][0]['normalized_value']=='9007199254740993.01'


def test_real_commerce_legacy_grid_uses_correction_without_changing_original_or_unedited_zero():
    from pathlib import Path
    from src.services.pdf_parser import PDFParser
    from src.engine.fact_context import build_fact_context_from_artifact
    from src.services.table_import_receipt import source_cell_id
    path=Path(__file__).parents[1]/'fixtures/native_trusted_v27/commerce25.pdf'
    artifact=PDFParser(None).build_parse_artifact(str(path))
    original=artifact.to_dict()
    raw=build_fact_context_from_artifact(artifact,org_name='上海市商务委员会',fiscal_year=2025,report_kind='final')
    effective=deepcopy(raw)
    targets=[f for f in effective.facts if f.table_code=='FIN_06_basic_expenditure'and f.raw_value=='0.00']
    assert len(targets)>1
    target=targets[0];target.normalized_value=Decimal('1.23');target.raw_value='1.23'
    value=make_snapshot(job_uuid='job',revision_id='rev',artifact=artifact,raw_context=raw,effective_context=effective)
    projected=effective_legacy_artifact(artifact,value)
    cells={source_cell_id(t,c):c for t in projected.table_fragments for c in t.cells}
    corrected=cells[target.extra_dims['evidence_refs'][0]['source_cell_id']]
    untouched=cells[targets[1].extra_dims['evidence_refs'][0]['source_cell_id']]
    assert corrected.raw_text=='1.23'and corrected.normalized_value_wan==Decimal('1.23')
    assert untouched.raw_text=='0.00'
    assert artifact.to_dict()==original


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['none','amount','qualification','missing','version','table'])
async def test_shared_projection_actual_readback_rejects_drift(fault):
    value=snapshot()
    tables=[{'table_key':p['table_key'],'data_json':json.dumps(p['data_json'])}for p in snapshot_table_payloads(value)]
    lines=[{'table_key':p['table_key'],'row_index':p['row_index'],'values_json':deepcopy(p['values_json'])}
           for p in snapshot_line_item_rows(value)]
    ref=snapshot_reference(value)
    if fault=='amount':lines[0]['values_json']['total_actual']='13.02'
    if fault=='qualification':lines[0]['values_json']['_meta']['recognition_status']='verified'
    if fault=='missing':lines.pop()
    if fault=='version':ref['revision_id']='other'
    if fault=='table':tables[0]['data_json']='{}'
    conn=SimpleNamespace(fetch=AsyncMock(side_effect=[tables,lines]),fetchval=AsyncMock(return_value=json.dumps(ref)))
    if fault=='none':
        await verify_shared_fact_projection(conn,'report',value)
    else:
        with pytest.raises(ValueError,match='projection'):
            await verify_shared_fact_projection(conn,'report',value)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault',['none','amount','qualification','instance','missing'])
async def test_legacy_fact_actual_readback_keeps_complete_effective_fact(fault):
    value=snapshot();rows=[]
    for fact in value['effective_context']['facts']:
        rows.append({'amount':Decimal(fact['normalized_value']),'normalized_amount':Decimal(fact['normalized_value']),
                     'table_code':fact['table_code'],'statement_code':fact['table_code'],
                     'classification_type':'unresolved','classification_code':fact['subject_code'],
                     'classification_name':fact['subject_name'],'measure':fact['indicator'],
                     'parse_confidence':fact['confidence'],'instance_uid':fact['instance_uid'],
                     'row_order':Decimal(fact['row_order']),'classification_level':fact['classification_level'],
                     'parent_classification_code':fact['parent_subject_code'],'hierarchy_path':fact['hierarchy_path'],
                     'extra_dims':{'snapshot_fact':deepcopy(fact),'fact_projection':snapshot_reference(value)}})
    if fault=='amount':rows[0]['amount']+=Decimal('.01')
    if fault=='qualification':rows[0]['extra_dims']['snapshot_fact']['recognition_status']='verified'
    if fault=='instance':rows[0]['instance_uid']='other'
    if fault=='missing':rows.pop()
    conn=SimpleNamespace(fetch=AsyncMock(return_value=rows))
    if fault=='none':
        await verify_legacy_fact_projection(conn,1,value)
    else:
        with pytest.raises(ValueError,match='projection'):
            await verify_legacy_fact_projection(conn,1,value)


@pytest.mark.asyncio
@pytest.mark.parametrize('consumer',['qc_v1','qc_v2','qc_v3','materialize','shared_sync'])
async def test_legacy_consumer_cannot_reinterpret_or_overwrite_a_published_snapshot(consumer):
    conn=SimpleNamespace(fetchval=AsyncMock(side_effect=['published_fact_projections',True]),
                         execute=AsyncMock(),fetch=AsyncMock(),fetchrow=AsyncMock())
    from src.qc.runner import QCRunner
    from src.qc.runner_v2 import QCRunnerV2
    from src.qc.runner_v3 import QCRunnerV3
    from src.services.fiscal_fact_materializer import FiscalFactMaterializer
    from src.services.ps_schema_sync import PSSharedSchemaSync
    from pathlib import Path
    actions={'qc_v1':lambda:QCRunner(conn,1).execute(), 'qc_v2':lambda:QCRunnerV2(conn,1).execute(),
             'qc_v3':lambda:QCRunnerV3(conn,1).execute(),
             'materialize':lambda:FiscalFactMaterializer(conn).materialize(1),
             'shared_sync':lambda:PSSharedSchemaSync(conn).sync(1,'测试局',2023,'final',Path('source.pdf'),'a'*64)}
    with pytest.raises(ValueError,match='snapshot_bound_document'):
        await actions[consumer]()
    conn.execute.assert_not_awaited()
    conn.fetch.assert_not_awaited()
    conn.fetchrow.assert_not_awaited()
