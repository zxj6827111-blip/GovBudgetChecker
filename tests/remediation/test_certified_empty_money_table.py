from copy import deepcopy
from decimal import Decimal

import pytest

from src.engine.native_empty_operands import NativeBlankTerm, blank_table_terms
from src.engine.fact_context import FactContext
from src.engine.native_table_arithmetic import table_facts, select
from src.engine.rules_v33 import R33117_BasicExpenseClassification, R33243_Table6_BasicExpenseAdvancedCheck
from tests.remediation.test_shiquan_native_empty_rows import grid_document


def document():
    rows=[['经济分类科目编码','科目名称','决算数','经济分类科目编码','科目名称','决算数'],
          ['301','工资福利支出','','302','商品和服务支出',''],
          ['30101','基本工资','','30201','办公费',''],
          ['人员经费合计','','','公用经费合计','','']]
    doc=grid_document(rows)
    doc.fact_context=FactContext([],doc.parse_artifact.source_file_hash,doc.parse_artifact.parse_run_uid,
                                org_name='甲局',fiscal_year=2025,report_kind='final')
    return doc


def test_whole_blank_money_grid_is_zero_observations_without_new_financial_facts():
    doc=document();before=doc.fact_context.to_dict();assert not doc.fact_context.facts
    terms,error=table_facts(doc,'FIN_06_basic_expenditure')
    assert error is None and len(terms)==6 and all(isinstance(term,NativeBlankTerm) for term in terms)
    for name in ['人员经费合计','公用经费合计']:
        value,error=select(doc,terms,'total_actual',name=name)
        assert error is None and value.normalized_value==Decimal(0)
    assert doc.fact_context.to_dict()==before


def test_all_blank_operands_reach_native_checks_and_legacy_projection_without_new_facts():
    from src.engine.fact_table_projection import fact_bound_table_rows
    doc=grid_document([
        ['经济分类科目编码','科目名称','决算数','经济分类科目编码','科目名称','决算数'],
        ['301','工资福利支出','','302','商品和服务支出',''],
        ['30101','基本工资','','30201','办公费',''],
        ['303','对个人和家庭的补助','','310','资本性支出',''],
        ['人员经费合计','','','公用经费合计','','']])
    doc.fact_context=FactContext([],doc.parse_artifact.source_file_hash,doc.parse_artifact.parse_run_uid,
                                org_name='甲局',fiscal_year=2025,report_kind='final')
    before=doc.fact_context.to_dict()
    assert R33117_BasicExpenseClassification().apply(doc)==[]
    assert R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)==[]
    rows=fact_bound_table_rows(doc,'一般公共预算财政拨款基本支出决算表',strict_unknown_values=True)
    assert rows[-1][2]==rows[-1][5]=='0'
    assert doc.structured_consumption[-1]['native_blank_observations']
    assert doc.fact_context.to_dict()==before


def test_wrapped_literal_name_matches_same_blank_cell_without_name_aliasing():
    from src.engine.native_empty_operands import blank_term
    doc=document()
    term=blank_term(doc,'FIN_06_basic_expenditure','total_actual',code='301',names=('工资福利\n支出',))
    assert term is not None and term.subject_name=='工资福利支出'
    assert blank_term(doc,'FIN_06_basic_expenditure','total_actual',code='301',names=('工资保障支出',)) is None


@pytest.mark.parametrize('change',['dash','unverified','ocr','no_data_rows','foreign_run','off','duplicate_instance'])
def test_absence_or_uncertain_blank_is_never_a_zero_table(change,monkeypatch):
    doc=document();a=doc.parse_artifact
    if change=='dash':a.table_fragments[0].cells[8].raw_text='—'
    elif change=='unverified':a.table_fragments[0].cells[8].native_blank_verified=False
    elif change=='ocr':a.table_fragments[0].extraction_method='ocr'
    elif change=='no_data_rows':a.table_fragments[0].rows=a.table_fragments[0].rows[:1];a.table_fragments[0].cells=a.table_fragments[0].cells[:6]
    elif change=='foreign_run':a.parse_run_uid='another'
    elif change=='off':monkeypatch.setenv('GOVBUDGET_EMPTY_VALUE_CONVENTIONS','off')
    else:a.logical_tables.append(deepcopy(a.logical_tables[0]))
    assert blank_table_terms(doc,'FIN_06_basic_expenditure')==[]
