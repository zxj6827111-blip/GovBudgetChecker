import os
from pathlib import Path
from unittest.mock import AsyncMock
from dataclasses import asdict
import pytest
from src.services import material_writing,annual_templates
from src.services.template_scope import resolve_material_scope
from src.schemas.document_profile import DocumentProfile,ProfileField


def profile():
    return DocumentProfile(report_kind=ProfileField(value='budget',source='cover_title'),report_year=ProfileField(value=2026,source='cover_title'))


@pytest.mark.parametrize('cover,body',[('部门','单位'),('单位','部门')])
def test_budget_two_directions_remain_conflicts(cover,body):
    pages=[f'上海市测试局2026年{cover}预算',f'2026年{body}财务收支预算总表',f'2026年{body}一般公共预算支出功能分类预算表']
    rows=material_writing.collect(pages,report_kind='budget')
    result=resolve_material_scope(evidence=rows,template_qualified=True,required_categories=('budget_financial_table','budget_function_table'))
    assert result.status=='conflict'


@pytest.mark.parametrize('cover,body',[('部门','单位'),('单位','部门')])
def test_budget_conflict_unchanged_even_with_selected_level(cover,body):
    """阶段 0 审核修正：所选层级只用于决算写法。

    开启预算模板开关时，封面与正文写法冲突仍按基线判 conflict，
    不因上传所选层级改判 confirmed（预算路径不变约定）。
    """
    pages=[f'上海市测试局2026年{cover}预算',f'2026年{body}财务收支预算总表',f'2026年{body}一般公共预算支出功能分类预算表']
    rows=material_writing.collect(pages,report_kind='budget')
    result=resolve_material_scope(evidence=rows,template_qualified=True,required_categories=('budget_financial_table','budget_function_table'),
                                  selected_level='unit' if body=='单位' else 'department')
    assert result.status=='conflict' and result.value is None
    assert result.archive_subject_mismatch is False


@pytest.mark.asyncio
async def test_budget_load_ignores_selected_level_but_final_uses_it(monkeypatch):
    """load() 层面同样只对决算消费所选层级：预算冲突保持基线，决算按基准裁决。"""
    from src.db.connection import DatabaseConnection
    conn=type('Connection',(),{'fetchrow':AsyncMock(return_value=None)})()
    monkeypatch.setenv('DATABASE_URL','test-only-unused-no-network')
    monkeypatch.setenv('GBC_TEMPLATE_SCOPE','true')
    monkeypatch.setenv('GBC_BUDGET_TEMPLATE_SCOPE','true')
    monkeypatch.setattr(DatabaseConnection,'acquire',AsyncMock(return_value=conn))
    monkeypatch.setattr(DatabaseConnection,'release',AsyncMock())
    snap={'available':True,'capability_scope':{'writing_auto':True},'content':{'config':{}},'target_year':2026}
    monkeypatch.setattr(annual_templates,'select_for_profile',AsyncMock(return_value=snap))
    basis={'organization_id':'org-1','organization_name':'上海市测试局','level':'unit','source':'upload_selection'}
    # 预算：封面单位写法、正文部门写法 → 与基线一致判 conflict，不按所选层级 confirmed。
    budget_pages=['上海市测试局2026年单位预算','2026年部门财务收支预算总表','2026年部门一般公共预算支出功能分类预算表']
    budget_result=await material_writing.load('test','a'*64,profile(),budget_pages,'unit',archive_basis=dict(basis))
    assert budget_result['status']=='conflict' and budget_result['value'] is None
    assert not budget_result.get('archive_subject_mismatch')
    # 决算对照：同样提供所选层级时按基准裁决。
    final_pages=['2026年度单位决算','2026年度部门决算表\n本表反映部门本年度收入。','2026年度部门决算情况说明']
    final_profile=DocumentProfile(report_kind=ProfileField(value='final'))
    final_result=await material_writing.load('test','a'*64,final_profile,final_pages,'unit',archive_basis=dict(basis))
    assert final_result['source']=='archive' and final_result['value']=='unit'


@pytest.mark.asyncio
async def test_budget_requires_both_feature_and_approved_capability(monkeypatch):
    from src.db.connection import DatabaseConnection
    conn=type('Connection',(),{'fetchrow':AsyncMock(return_value=None)})()
    monkeypatch.setenv('DATABASE_URL','test-only-unused-no-network')
    monkeypatch.setenv('GBC_TEMPLATE_SCOPE','true')
    monkeypatch.delenv('GBC_BUDGET_TEMPLATE_SCOPE',raising=False)
    monkeypatch.setattr(DatabaseConnection,'acquire',AsyncMock(return_value=conn))
    monkeypatch.setattr(DatabaseConnection,'release',AsyncMock())
    snap={'available':True,'capability_scope':{'writing_auto':True},'content':{'config':{}},'target_year':2026}
    monkeypatch.setattr(annual_templates,'select_for_profile',AsyncMock(return_value=snap))
    pages=['2026年度单位预算','2026年单位财务收支预算总表','2026年单位一般公共预算支出功能分类预算表']
    assert await material_writing.load('test','a'*64,profile(),pages)=={}
    monkeypatch.setenv('GBC_BUDGET_TEMPLATE_SCOPE','true')
    result=await material_writing.load('test','a'*64,profile(),pages)
    assert result['domain']=='budget' and result['value']=='unit'
    annual_templates.select_for_profile.return_value={'available':True,'capability_scope':{}}
    assert await material_writing.load('test','a'*64,profile(),pages)=={}


def test_real_budget_xls_import_is_read_only_and_keeps_unknown_year(tmp_path):
    directory=os.environ.get('GOVBUDGET_TEST_BUDGET_TEMPLATE_DIR')
    if not directory:pytest.skip('真实XLS模板核验需显式提供来源目录')
    from src.services.disclosure_template_import import import_source
    source=Path(directory)/'附件3：20XX年单位预算公开样张.xls'
    result=import_source(source,tmp_path/'source',{'report_kind':'budget','source_report_year':None})
    assert result['extraction_status']=='needs_visual_verification'
    assert result['metadata']['source_report_year'] is None
    assert result['converter'].startswith('xlrd:2.0.2')
    import json
    cells=json.loads((tmp_path/'source/spreadsheet_cells.json').read_text())
    assert any(c['sheet']=='单位收支总表' and '预算单位财务收支预算总表' in str(c['value']) for c in cells)
    assert all('text_start' in c and 'text_end' in c for c in cells)


def test_budget_default_unchanged_and_new_chain_has_receipt_obligation(monkeypatch):
    from src.engine.rule_registry import select_rules
    from src.engine.check_obligations import build_obligation_ledger
    monkeypatch.delenv('GBC_BUDGET_TEMPLATE_SCOPE',raising=False)
    assert 'CMM-027' not in [r.code for r in select_rules('budget')]
    monkeypatch.setenv('GBC_TEMPLATE_SCOPE','true');monkeypatch.setenv('GBC_BUDGET_TEMPLATE_SCOPE','true')
    assert 'CMM-027' in [r.code for r in select_rules('budget')]
    current=profile();current.writing_scope={'domain':'budget'}
    ledger=build_obligation_ledger(current,report_kind='budget',rule_execution_summary={'rule_statuses':{'CMM-018':'pass','BUD-113':'pass'}})
    import json
    assert 'CMM-027' in json.dumps(ledger)
    entries=ledger['instances']
    assert any('CMM-027' in x.get('checkers',[]) and x['status']!='completed' for x in entries)



def budget_position_snapshot(legacy=False):
    caps = {"writing_auto": False, "position_checks": True, "structure": True}
    content = {"config": {"domain": "budget"}}
    proof = {"report_reference": "synthetic-budget-position-report", "independent_labels": True,
             "boundary_cases_passed": True, "independent_sample_count": 100,
             "automatic_verified_count": 0, "automatic_errors": 0,
             "source_report_year": 2025, "target_year": 2026, "capability_scope": caps,
             "algorithm": annual_templates.ALGORITHM, "package_hash": annual_templates.sha(content)}
    if legacy: proof.pop("independent_sample_count")
    return annual_templates.qualify_binding_capabilities({
        "available": True, "adoption_kind": "not_received", "target_year": 2026,
        "source_report_year": 2025, "content": content, "content_sha256": annual_templates.sha(content),
        "capability_scope": caps, "basis": annual_templates.canonical({"compatibility": proof})})


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_budget_position_only_m0_runs_or_defers_by_its_own_compatibility_proof(monkeypatch, legacy):
    from src.engine.checklist_identity_rules import CMM027_TemplatePositionScope
    from src.engine.rule_outcome import RuleDeferred
    from tests.test_material_writing_rules import document
    monkeypatch.setenv("GBC_TEMPLATE_SCOPE", "true")
    monkeypatch.setenv("GBC_BUDGET_TEMPLATE_SCOPE", "true")
    monkeypatch.setenv("GBC_TEMPLATE_EXPORT", "synthetic-only")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    snap = budget_position_snapshot(legacy)
    monkeypatch.setattr(annual_templates, "offline_for_profile", lambda *args: snap)
    pages = ["上海市测试局2026年度单位预算", "2026年部门财务收支预算总表", "2026年部门一般公共预算支出功能分类预算表"]
    value = await material_writing.load("test-budget", "a" * 64, profile(), pages)
    assert value["domain"] == "budget" and value["status"] == "confirmed"
    assert value["source"] == "declaration" and value["value"] == "unit"
    doc = document(pages, value); doc.report_kind = "budget"
    doc.document_profile.report_kind = ProfileField(value="budget")
    if legacy:
        assert value["template_capability_pending"]["position_checks"]
        with pytest.raises(RuleDeferred): CMM027_TemplatePositionScope().apply(doc)
    else:
        issues = CMM027_TemplatePositionScope().apply(doc)
        assert len(issues) == 2
        assert all("反向写法" in issue.message or "反向写法" in str(issue.location) for issue in issues)


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_budget_default_off_still_excludes_position_or_pending_new_chain(monkeypatch, legacy):
    from src.engine.checklist_identity_rules import CMM027_TemplatePositionScope
    from src.engine.rule_outcome import RuleNotApplicable
    from tests.test_material_writing_rules import document
    monkeypatch.setenv("GBC_TEMPLATE_SCOPE", "true")
    monkeypatch.delenv("GBC_BUDGET_TEMPLATE_SCOPE", raising=False)
    monkeypatch.setenv("GBC_TEMPLATE_EXPORT", "synthetic-only")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    snap = budget_position_snapshot(legacy)
    monkeypatch.setattr(annual_templates, "offline_for_profile", lambda *args: snap)
    pages = ["上海市测试局2026年度单位预算", "2026年部门财务收支预算总表"]
    assert await material_writing.load("test-budget", "a" * 64, profile(), pages) == {}
    # 即使旧材料保存着预算写法视图，总开关关闭仍不接入新的预算位置链路。
    doc = document(pages, {"domain": "budget", "status": "confirmed", "value": "unit", "snapshot": snap})
    doc.report_kind = "budget"; doc.document_profile.report_kind = ProfileField(value="budget")
    with pytest.raises(RuleNotApplicable): CMM027_TemplatePositionScope().apply(doc)
