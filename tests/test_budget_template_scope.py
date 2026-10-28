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
