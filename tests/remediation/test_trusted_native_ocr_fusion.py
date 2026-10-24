"""混合格合成契约：不能作为真实扫描件准确率或原件验收证据。"""
from copy import deepcopy
from decimal import Decimal
import pytest

from tests.remediation.test_round11_ocr import fixture
from src.schemas.parse_artifact import ParsedCell,ParsedTable,LogicalTableInstance
from src.services.ocr_fact_promotion import promote_ocr_candidates
from src.engine.fact_context import build_fact_context_from_artifact,operand_rejection_reason


def mixed_case(original=''):
    artifact,candidate=fixture()
    rows=deepcopy(candidate.rows)
    rows[1][2]=original
    cells=[]
    for item in candidate.cells:
        row,col=item['row_idx'],item['col_idx']
        raw=rows[row][col]
        number=float(raw)if raw.isdigit() else None
        cells.append(ParsedCell(row,col,raw,raw,number,1,tuple(item['page_bbox']),row==0,.9,'万元','lines',
                                grid_raw_text=raw,native_text_sources=[{'text':raw}]if raw else []))
    native=ParsedTable('FIN_02_income','测试局2025年度收入决算表',1,0,(0,0,400,100),1,.9,
                       'lines','万元',rows,cells,fragment_uid='native-table')
    artifact.page_count=1
    artifact.table_fragments=[native]
    artifact.physical_fragments=[deepcopy(native)]
    artifact.source_grids=[deepcopy(native)]
    artifact.logical_tables=[LogicalTableInstance('native-table',native.table_code,native.title,
        ['native-table'],1,1,row_count=2,confidence=.9,org_name='测试局',fiscal_year=2025)]
    artifact.ocr_table_candidates=[candidate]
    return artifact,candidate


def promote(artifact):
    return promote_ocr_candidates(artifact,organization='测试局',year=2025,report_kind='final')


def test_unknown_native_cell_uses_unique_qualified_ocr_and_preserves_raw_grid():
    artifact,_=mixed_case()
    physical=deepcopy(artifact.physical_fragments[0].to_dict())
    grids=deepcopy(artifact.source_grids[0].to_dict())
    run=artifact.parse_run_uid
    summary=promote(artifact)
    assert len(summary['fused_cells'])==1 and summary['usable_for_rules']
    assert len(artifact.logical_tables)==len(artifact.table_fragments)==1
    assert artifact.source_grids[0].to_dict()==grids
    assert artifact.physical_fragments[0].to_dict()==physical
    context=build_fact_context_from_artifact(artifact,org_name='测试局',fiscal_year=2025,report_kind='final')
    assert len(context.facts)==2
    fact=next(f for f in context.facts if f.indicator=='total_actual')
    assert fact.normalized_value==Decimal(100)and operand_rejection_reason(fact) is None
    # 组合分受原生结构分限制；真实OCR分另存，不能用抬分冒充结构确认。
    assert fact.confidence==.9 and fact.recognition_status=='unverified'
    assert fact.extra_dims['qualification_evidence']['raw_cell_score']==.995
    ref=fact.extra_dims['evidence_refs'][0]
    assert ref['source_method']=='ocr' and ref['source_provenance']['native_alignment']['native_raw_text']==''
    source=next(t for t in artifact.physical_fragments if ref['source_cell_id'].startswith(t.fragment_uid+':'))
    assert source.cells[0].raw_text=='100'and source.cells[0].bbox==tuple(ref['bbox'])
    assert artifact.parse_run_uid!=run
    assert artifact.source_tokens and artifact.cell_candidates and artifact.resolved_cells
    from src.services.table_import_receipt import build_table_import_receipt
    receipt=build_table_import_receipt(artifact,context.facts)
    assert receipt['amount_cell_count']==2 and receipt['rule_admitted_count']==2
    from src.schemas.parse_artifact import ParseArtifact
    assert ParseArtifact.from_dict(artifact.to_dict()).to_dict()==artifact.to_dict()
    frozen=artifact.to_dict()
    again=promote(artifact)
    assert not again['fused_cells'] and artifact.to_dict()==frozen


@pytest.mark.parametrize('fault',['certified_blank','dash','row_name','unit','year','subject','geometry',
                                'partial_fragment','other_stage','source_hash','score','response','existing_native_conflict'])
def test_unsafe_alignment_never_fills_native_cell(fault):
    artifact,candidate=mixed_case('—'if fault=='dash'else'')
    target=next(c for c in artifact.table_fragments[0].cells if (c.row_idx,c.col_idx)==(1,2))
    if fault=='certified_blank':target.native_blank_verified=True
    if fault=='row_name':
        artifact.table_fragments[0].rows[1][1]='不同科目'
        artifact.table_fragments[0].cells[5].raw_text='不同科目'
    if fault=='unit':artifact.table_fragments[0].unit_hint='亿元'
    if fault=='year':artifact.logical_tables[0].fiscal_year=2024
    if fault=='subject':artifact.logical_tables[0].org_name='另一个单位'
    if fault=='geometry':target.bbox=(100,50,200,100)
    if fault=='partial_fragment':target.bbox=(205,50,305,100)
    if fault=='other_stage':
        artifact.table_fragments[0].rows[0][2]='年初预算'
        artifact.table_fragments[0].cells[2].raw_text='年初预算'
    if fault=='source_hash':artifact.source_file_hash='f'*64
    if fault=='score':candidate.provenance['detector_input_verified']=False
    if fault=='response':candidate.cells[-2]['raw_text']='999'
    if fault=='existing_native_conflict':target.native_text_sources=[{'recovery_kind':'unresolved_original_owner'}]
    before=artifact.table_fragments[0].to_dict()
    result=promote(artifact)
    assert not result['fused_cells']
    assert artifact.table_fragments[0].to_dict()==before
    assert len(artifact.logical_tables)==1 and len(artifact.physical_fragments)==1


def test_disagreeing_usable_native_value_retains_both_sources_and_blocks_its_operand():
    artifact,_=mixed_case('105')
    physical=artifact.physical_fragments[0].to_dict()
    summary=promote(artifact)
    assert not summary['fused_cells']and len(summary['native_conflicts'])==1
    assert artifact.table_fragments[0].rows[1][2]=='105'
    assert artifact.physical_fragments[0].to_dict()==physical
    context=build_fact_context_from_artifact(artifact,org_name='测试局',fiscal_year=2025,report_kind='final')
    fact=next(f for f in context.facts if f.indicator=='total_actual')
    assert fact.raw_value=='105'and fact.normalized_value==Decimal(105)
    assert fact.recognition_status=='ambiguous'and operand_rejection_reason(fact)
    assert fact.extra_dims['qualification_evidence']['source_conflict']is True


def test_same_amount_different_overlapping_native_instance_is_not_flattened():
    artifact,_=mixed_case()
    second=deepcopy(artifact.table_fragments[0])
    second.fragment_uid='second-instance'
    artifact.table_fragments.append(second)
    result=promote(artifact)
    assert not result['fused_cells']
    assert all(t.rows[1][2]==''for t in artifact.table_fragments)
