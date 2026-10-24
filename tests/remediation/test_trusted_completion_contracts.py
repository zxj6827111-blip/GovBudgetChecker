"""完整身份、逐出现次数验收和候选发布的控制合同。"""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from src.engine.fact_context import FactContext,resolve_operand
from src.schemas.parse_artifact import FiscalFact,ParseArtifact
from src.services.analysis_fact_snapshot import make_snapshot
from src.services.historical_rebuild import compare_snapshots,validate_acceptance
from src.services.pdf_cell_acceptance import evaluate_cells,acceptance_suite
from src.services.recognition_metrics import recognition_state


def source():
    a=ParseArtifact(source_file_hash='a'*64,parse_run_uid='run')
    f=FiscalFact('same-amount','total_actual','100.00','万元',Decimal('100'),org_name='测试局',
        fiscal_year=2025,period='本年',budget_stage='决算',subject_name='合计',table_code='FIN_02_income',
        instance_uid='one',recognition_status='unverified',
        extra_dims={'source_header':'本年收入合计','evidence_refs':[{'physical_page':1,'bbox':[10,10,30,30]}]})
    return a,f


def test_stage_year_and_fund_instance_must_be_uniquely_bound():
    _,f=source()
    for field,query in [('budget_stage',{'budget_stages':('决算',)}),('fiscal_year',{'report_year':2025})]:
        assert resolve_operand(FactContext([replace(f,**{field:None})]),'total_actual',**query).status=='blocked'
    other=replace(f,fact_uid='same-value-other',instance_uid='two',fund_scope='政府性基金')
    context=FactContext([f,other])
    assert resolve_operand(context,'total_actual').status=='blocked'
    assert resolve_operand(context,'total_actual',instance_uid='one').fact==f
    assert resolve_operand(context,'total_actual',fund_scope='政府性基金').status=='blocked'
    assert resolve_operand(context,'total_actual',fund_scope='政府性基金',instance_uid='two').fact==other
    context.facts.append(replace(f,fact_uid='unresolved',recognition_status='ambiguous'))
    assert resolve_operand(context,'total_actual',instance_uid='one').status=='blocked'


@pytest.mark.asyncio
async def test_current_job_view_keeps_new_attempt_failure_and_denies_changed_owner(monkeypatch):
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from api.routes.jobs import published_job_view
    monkeypatch.setenv('DATABASE_URL','postgresql://control.invalid/test')
    monkeypatch.setattr('src.services.analysis_fact_snapshot.read_database_result',AsyncMock(return_value={
        'analysis_revision':'old','status':'done','result':{'bound':'old'},'structured_ingest':{}}))
    monkeypatch.setattr('api.routes.jobs.user_can_access_job',lambda user,payload:True)
    result=await published_job_view('job',{'status':'error','error':'new attempt failed'}, {})
    assert result['status']=='error'and result['analysis_revision']=='old'and result['result']=={'bound':'old'}
    monkeypatch.setattr('api.routes.jobs.user_can_access_job',lambda user,payload:False)
    with pytest.raises(HTTPException)as error:await published_job_view('job',{}, {})
    assert error.value.status_code==403


@pytest.mark.asyncio
async def test_document_soft_budget_retains_unstarted_pages(tmp_path,monkeypatch):
    import pymupdf as fitz
    from src.services.ocr_tiled_pipeline import recognize_document_tiles
    path=tmp_path/'two-pages.pdf';doc=fitz.open();doc.new_page();doc.new_page();doc.save(path);doc.close()
    now=[0.]
    monkeypatch.setenv('OCR_DOCUMENT_SOFT_BUDGET_SEC','1')
    monkeypatch.setattr('src.services.ocr_tiled_pipeline.time.monotonic',lambda:now[0])
    async def recognize(image,**kwargs):
        now[0]=2.
        return [],[],{'status':'completed','regions':{},'completed':{},'identity':{'tile_boxes':[]},
                     'pending':[],'unresolved':[],'elapsed_seconds':2}
    monkeypatch.setattr('src.services.ocr_tiled_pipeline.recognize_tiles',recognize)
    artifact=ParseArtifact(source_file_hash='a'*64,page_count=2)
    result=await recognize_document_tiles(path,[1,2],artifact,
        {'model_fingerprint':'b'*64,'pipeline_fingerprint':'c'*64},checkpoint_path=tmp_path/'checkpoint.json')
    assert result['status']=='pending'and result['failed_pages']==[2]
    assert result['pages'][-1]['reason']=='document_pass_soft_budget'


def test_repeated_same_amount_truth_is_not_deduplicated_and_missing_stays_denominator():
    a,f=source();raw=FactContext([f],file_hash=a.source_file_hash,parse_run_uid=a.parse_run_uid)
    snapshot=make_snapshot(job_uuid='job',revision_id='r1',artifact=a,raw_context=raw,effective_context=raw)
    cell={k:f.to_dict().get(k)for k in ('org_name','org_scope','fiscal_year','period','fund_scope','budget_stage',
          'table_code','indicator','subject_code','subject_name')}
    cell.update(occurrence_id='one',physical_page=1,bbox=[10,10,30,30],raw_value='100.00',unit='万元',
                column_identity='本年收入合计',literal_kind='amount',legibility='readable')
    second={**cell,'occurrence_id':'two','bbox':[30,10,50,30]}
    truth={'source_file_hash':a.source_file_hash,'cells':[cell,second],'split':'independent_acceptance',
           'review_status':'human_verified','production_output_used_as_truth':False}
    result=evaluate_cells(truth,snapshot)
    assert result['readable_occurrences']==2 and result['correct_occurrences']==1
    assert result['correct_auto_recovery_coverage']==.5 and len(result['unrecovered'])==1
    # 只标少量已恢复格，不能借人工核对标记冒充完整独立真值。
    partial={**truth,'cells':[cell]}
    assert evaluate_cells(partial,snapshot)['correct_auto_recovery_coverage']==1
    assert evaluate_cells(partial,snapshot)['independent_truth_confirmed']is False
    complete={**partial,'annotation_complete':True}
    assert evaluate_cells(complete,snapshot)['independent_truth_confirmed']is True
    wrong=deepcopy(truth);wrong['cells'][0]['subject_name']='另一个科目'
    assert evaluate_cells(wrong,snapshot)['wrong_admissions'][0]['fields']==['subject_name']
    blank=deepcopy(truth);blank['cells'][0]['literal_kind']='blank'
    assert evaluate_cells(blank,snapshot)['wrong_admissions'][0]['reason']=='amount_in_non_amount_cell'
    assert acceptance_suite([])['accepted']is False
    assert recognition_state(a)['recognition_accuracy']is None


def test_blank_acceptance_uses_local_unit_scope_and_requires_native_blank_proof():
    from types import SimpleNamespace
    from tests.remediation.test_rc_batch1 import _make_fragment, _instance_for, _artifact_with
    from src.services.pdf_cell_acceptance import non_amount_cell_identity
    table=_make_fragment('FIN_02_income', [['科目编码','科目名称','本年收入合计'],
        ['201','一般公共服务支出','']])
    table.title='测试局单位决算收入决算表'
    instance=_instance_for(table)
    instance.org_name='测试局';instance.fiscal_year=2025
    artifact=_artifact_with([table],[instance],file_hash='a'*64)
    cell=next(c for c in table.cells if c.row_idx==1 and c.col_idx==2)
    cell.native_blank_verified=True
    logical=SimpleNamespace(table_instance_uid=instance.instance_uid,row_idx=1,col_idx=2,value_kind='blank')
    snapshot={'effective_context':{'org_name':'测试局','org_scope':'department','fiscal_year':2025,
        'report_kind':'final','budget_stage':'决算'}}
    identity=non_amount_cell_identity(artifact,snapshot,logical)
    assert identity['org_scope']=='unit'
    cell.native_blank_verified=False
    assert non_amount_cell_identity(artifact,snapshot,logical)is None


def test_snapshot_comparison_includes_status_and_identity_and_publication_requires_acceptance():
    a,f=source();raw=FactContext([f],file_hash=a.source_file_hash,parse_run_uid='run')
    old=make_snapshot(job_uuid='job',revision_id='old',artifact=a,raw_context=raw,effective_context=raw)
    effective=deepcopy(raw);effective.facts[0].recognition_status='ambiguous'
    new=make_snapshot(job_uuid='job',revision_id='new',artifact=a,raw_context=raw,effective_context=effective)
    assert compare_snapshots(old,new)['changed_facts'][0]['fields']==['recognition_status']
    item={'acceptance':{'accepted':True,'snapshot_hash':new['snapshot_hash'],'source_file_hash':a.source_file_hash,
                        'reviewer':'CONTROL_TEST_ONLY','reviewed_at':'2026-10-20','errors':[]}}
    validate_acceptance(item,new,{'status':'review_required'})
    for fault in ('accepted','snapshot_hash','source_file_hash','reviewer','reviewed_at','errors'):
        bad=deepcopy(item);bad['acceptance'][fault]=None
        with pytest.raises(ValueError):validate_acceptance(bad,new,{'status':'review_required'})
    with pytest.raises(ValueError):validate_acceptance(item,new,{'status':'done','fact_overlay':{'status':'partial'}})
