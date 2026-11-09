from copy import deepcopy

import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.native_empty_three_public_cross import check_certified_empty_three_public_cross
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33CrossSanGongEcon
from tests.remediation.test_putuo_code_repair import empty_three_public
from tests.remediation.test_shiquan_native_empty_rows import grid_document


def cross_doc(amount=''):
    doc = grid_document([
        ['经济分类科目编码', '科目名称', '决算数', '经济分类科目编码', '科目名称', '决算数'],
        ['301', '工资福利支出', '10.00', '302', '商品和服务支出', '0.00'],
        ['30101', '基本工资', '10.00', '30209', '差旅费', ''],
        ['', '', '', '30211', '因公出国（境）费用', ''],
        ['', '', '', '30216', '公务接待费', amount],
        ['', '', '', '30229', '公务用车运行维护费', ''],
        ['303', '对个人和家庭的补助', '', '310', '资本性支出', ''],
        ['', '', '', '31013', '公务用车购置', ''],
        ['人员经费合计', '', '10.00', '公用经费合计', '', '0.00'],
    ])
    empty = empty_three_public()
    for table in empty.parse_artifact.table_fragments + empty.parse_artifact.physical_fragments:
        table.fragment_uid = 'sg'
    instance = empty.parse_artifact.logical_tables[0]
    instance.instance_uid, instance.fragment_uids, instance.fiscal_year = 'sg-instance', ['sg'], 2025
    doc.parse_artifact.table_fragments += empty.parse_artifact.table_fragments
    doc.parse_artifact.physical_fragments += empty.parse_artifact.physical_fragments
    doc.parse_artifact.logical_tables += empty.parse_artifact.logical_tables
    doc.page_texts = [empty.page_texts[0].replace('2024', '2025')]
    doc.fact_context = build_fact_context_from_artifact(doc.parse_artifact, org_name='甲局',
        fiscal_year=2025, report_kind='final', doc_type='dept_final')
    return doc


def test_certified_empty_cross_checks_four_items_and_keeps_both_table_sources():
    doc = cross_doc()
    before = doc.fact_context.to_dict()
    assert R33CrossSanGongEcon().apply(doc) == []
    receipts = [r for r in doc.fact_receipts if r['sub_check_id'].startswith('empty_three_public_cross:')]
    assert len(receipts) == 4 and all(r['status'] == 'pass' for r in receipts)
    for receipt in receipts:
        assert {o['table_code'] for o in receipt['cell_observations']} == {
            'FIN_06_basic_expenditure', 'FIN_07_three_public'}
    assert before == doc.fact_context.to_dict()


def test_positive_economic_item_is_a_real_cross_error_not_an_empty_table_exemption():
    doc = cross_doc('1.00')
    issues = R33CrossSanGongEcon().apply(doc)
    assert len(issues) == 1 and '30216' in issues[0].message and '1.00' in issues[0].message
    assert issues[0].location['native_blank_sources'][0]['table_code'] == 'FIN_07_three_public'
    assert issues[0].fact_ids


@pytest.mark.parametrize('problem', ['missing_row', 'wrong_code', 'uncertified_econ', 'wrong_year', 'missing_fact'])
def test_incomplete_economic_sources_remain_deferred(problem):
    doc = cross_doc()
    table = doc.parse_artifact.table_fragments[0]
    if problem == 'missing_row':
        table.rows[4][3:6] = ['', '', '']
    elif problem == 'wrong_code':
        table.rows[4][3] = '30217'
    elif problem == 'uncertified_econ':
        next(c for c in table.cells if (c.row_idx, c.col_idx) == (4, 5)).native_blank_verified = False
    elif problem == 'wrong_year':
        doc.fact_context.fiscal_year = 2024
    else:
        doc = cross_doc('1.00')
        doc.fact_context.facts = [f for f in doc.fact_context.facts if f.subject_code != '30216']
    with pytest.raises(RuleDeferred):
        R33CrossSanGongEcon().apply(doc)


def test_no_note_or_non_native_does_not_enter_special_branch():
    doc = cross_doc()
    doc.page_texts = ['甲局2025年度部门决算']
    assert check_certified_empty_three_public_cross(R33CrossSanGongEcon(), doc) is None
