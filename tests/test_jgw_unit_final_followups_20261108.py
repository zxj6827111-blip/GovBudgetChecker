"""2026-11-08 建管委 2025 单位决算遗留项回归。

- 本级单位材料：目录名“X本级”、封面与正文写“X”时，“X”就是本单位（部门材料不放宽）；
- 只列项级行的一般公共预算表：按项名绑定，类金额按编码前 3 位汇总，类名写错照报；
- 政府性基金表合计行印子表头：本年支出合计按顶层科目行推导；
- 同一根因（缺表、空表无说明、同一组原格的取整提示）只保留一条；
- 同比百分比只印到整数时，按显示精度四舍五入的误差不算错。
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.checklist_final_rules import _top_level_rows_total
from src.engine.fact_context import FactContext
from src.engine.native_final_disclosure import GENERAL, check_function_disclosures, leaf_only_classes
from src.engine.native_issue_dedup import deduplicate_native_issues
from src.engine.native_narrative_checks import _foreign_owner, check_narrative_relations
from src.engine.pipeline import build_document
from src.engine.rules_v33 import R33234_NarrativePercentConsistency, Rule, _structure_class_name_issues
from src.schemas.parse_artifact import FiscalFact
from src.services.check_subject import resolve_check_subject


# ---------------------------------------------------------------------------
# 本级主体
# ---------------------------------------------------------------------------

DEPT = '上海市普陀区建设和管理委员会'


def head_unit(**changes):
    values = dict(organization_id='unit-node', organization_name=DEPT + '本级', organization_level='unit',
                  cover_name=DEPT, cover_text=DEPT + '\n2025 年度决算')
    values.update(changes)
    return resolve_check_subject(**values)


@pytest.mark.parametrize('linked', [DEPT + '本级', DEPT + '（本级）', DEPT + '(本级)', DEPT + '本部'])
def test_head_unit_marker_uses_cover_name_as_current_subject(linked):
    subject = head_unit(organization_name=linked)
    assert subject['name'] == DEPT and subject['source'] == 'verified_cover_alias'
    assert subject['organization_name'] == linked  # 归档关联不变
    assert subject['evidence'][0]['relation'] == 'directory_head_unit_marker'
    assert not _foreign_owner(DEPT + '2025年度收入支出总计24,699.82万元', subject['name'])
    # 其他单位照旧是外单位
    assert _foreign_owner('上海市普陀区财政局2025年度收入支出总计100万元', subject['name'])


@pytest.mark.parametrize('changes', [
    dict(organization_level='department'),                      # 部门节点不走别名
    dict(cover_text=DEPT + '\n2025 年度部门决算'),              # 封面自称部门决算：不把部门名认作本级
    dict(subject_level='department'),
    dict(subject_level='government'),
    dict(cover_conflicted=True),
    dict(cover_text=None),                                      # 封面没有原文证据
    dict(organization_name=DEPT + '本级下属中心'),
    dict(organization_name='上海市普陀区财政局本级'),
    dict(organization_id=None),
])
def test_head_unit_alias_keeps_department_and_other_subjects_apart(changes):
    subject = head_unit(**changes)
    assert subject['source'] != 'verified_cover_alias'
    assert subject['name'] != DEPT or changes.get('organization_name') == DEPT


# ---------------------------------------------------------------------------
# 只列项级行的一般公共预算表
# ---------------------------------------------------------------------------

class NameRule(Rule):
    code, severity = 'V33-227', 'warn'


class DisclosureRule(Rule):
    code, severity = 'V33-222', 'warn'


SECTION = (
    '五、一般公共预算财政拨款支出决算情况说明\n'
    '（一）一般公共预算财政拨款支出决算总体情况\n'
    '一般公共预算财政拨款支出3,100.00万元。\n'
    '（二）一般公共预算财政拨款支出决算结构情况\n'
    '一般公共预算财政拨款支出3,100.00万元，主要用于以下方面：社会保障和就业支出（类）{social}万元，'
    '占3.23%；{wrong}（类）3,000.00万元，占96.77%。\n'
    '（三）一般公共预算财政拨款支出决算具体情况\n'
    '1、社会保障和就业支出（类）行政事业单位养老支出（款）行政单位离退休（项）。主要用于：离退休支出。'
    '年初预算为100.00万元，支出决算为100.00万元。\n'
    '2、城乡社区支出（类）城乡社区管理事务（款）行政运行（项）。主要用于：工资福利。'
    '年初预算为1,000.00万元，支出决算为1,000.00万元。\n'
    '3、城乡社区支出（类）城乡社区管理事务（款）其他城乡社区管理事务支出（项）。主要用于：日常公用。'
    '年初预算为2,000.00万元，支出决算为2,000.00万元。\n'
    '六、一般公共预算财政拨款基本支出决算情况说明\n'
)


def leaf_document(*, social='100.00', wrong='行政运行', extra_rows=(), total='3100.00'):
    rows = [('2080501', '行政单位离退休', '100.00'), ('2120101', '行政运行', '1000.00'),
            ('2120199', '其他城乡社区管理事务支出', '2000.00'), ('total', '合计', total), *extra_rows]
    doc = build_document('final.pdf', ['甲局\n2025年度决算', SECTION.format(social=social, wrong=wrong)], [], 0)
    facts = [FiscalFact(fact_uid=f'fact:{i}', indicator='total_actual', raw_value=amount, unit='万元',
                        normalized_value=Decimal(amount), org_name='甲局', fiscal_year=2025, period='本年',
                        budget_stage='决算', subject_code=code, subject_name=name, table_code=GENERAL,
                        instance_uid=GENERAL, fund_scope='general_public_budget',
                        extra_dims={'evidence_refs': [{'physical_page': 1, 'quote': amount,
                                                       'bbox': [1, 1, 5, 5], 'source_cell_id': f'cell:{i}'}]})
             for i, (code, name, amount) in enumerate(rows)]
    doc.fact_context = FactContext(facts, 'a' * 64, 'run', org_name='甲局', fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash='a' * 64, parse_run_uid='run')
    return doc


def test_leaf_only_table_binds_items_by_xiang_and_sums_classes_by_code_prefix():
    doc = leaf_document()
    classes = leaf_only_classes(doc)
    assert {code: (entry['sum'], entry['name']) for code, entry in classes.items()} == {
        '208': (Decimal('100.00'), '社会保障和就业支出'), '212': (Decimal('3000.00'), '城乡社区支出')}
    assert check_function_disclosures(DisclosureRule(), doc) == []
    statuses = {r['sub_check_id']: r['status'] for r in doc.fact_receipts}
    # 三个具体情况条目都按项级行核对，208 类结构金额按项合计核对
    assert sum(1 for key, status in statuses.items() if 'general:text:item:' in key and status == 'pass') == 3
    assert any(key.startswith('general:text:class:208:') and status == 'pass' for key, status in statuses.items())


def test_leaf_only_class_amount_mismatch_is_reported_against_leaf_sum():
    doc = leaf_document(social='120.00')
    issue, = check_function_disclosures(DisclosureRule(), doc)
    assert issue.location['derived_total_from'] == 'leaf_rows' and issue.location['subject_code'] == '208'
    assert '该类各项级行合计100.00万元' in issue.message


def test_leaf_only_wrong_class_name_is_a_finding_not_a_gap():
    doc = leaf_document()
    issue, = _structure_class_name_issues(NameRule(), doc, {})
    assert issue.location['code'] == '212' and issue.location['expected_name'] == '城乡社区支出'
    assert issue.location['actual_name'] == '行政运行'
    assert '“行政运行”是表内2120101项的名称' in issue.message
    assert {ref['fact_uid'] for ref in issue.location['operand_refs']} == {'fact:1', 'fact:2'}
    # 写对类名则不报
    assert _structure_class_name_issues(NameRule(), leaf_document(wrong='城乡社区支出'), {}) == []


def test_other_class_name_difference_is_reported_without_choosing_a_winner():
    issue, = _structure_class_name_issues(NameRule(), leaf_document(wrong='城乡社区事务支出'), {})
    assert '两处类名写法不一致，请核对' in issue.message and '应为' not in issue.message


def test_tables_with_any_class_or_kuan_row_keep_the_hierarchy_path():
    doc = leaf_document(extra_rows=[('212', '城乡社区支出', '3000.00')])
    assert leaf_only_classes(doc) == {}


def test_incomplete_leaf_rows_never_stand_in_for_class_totals():
    # 项级行之和（3100）不等于合计行（3200）：可能漏读了行，不推导类金额也不报类名
    doc = leaf_document(total='3200.00')
    assert leaf_only_classes(doc) == {}
    assert _structure_class_name_issues(NameRule(), doc, {}) == []
    # 只有原文总额与合计行的差异照常报；不出现按项合计推导的类金额问题，条目仍逐项核对
    issues = check_function_disclosures(DisclosureRule(), doc)
    assert issues and all(issue.location['subject_code'] == 'total' for issue in issues)
    assert not any(issue.location.get('derived_total_from') == 'leaf_rows' for issue in issues)
    assert sum(1 for r in doc.fact_receipts
               if 'general:text:item:' in r['sub_check_id'] and r['status'] == 'pass') == 3


# ---------------------------------------------------------------------------
# 政府性基金表合计行印子表头
# ---------------------------------------------------------------------------

def fund_row(code, value, indicator='total_actual'):
    return SimpleNamespace(indicator=indicator, subject_code=code, budget_stage='决算',
                           normalized_value=None if value is None else Decimal(value))


def test_fund_total_is_derived_from_top_level_rows_only():
    assert _top_level_rows_total([fund_row('2120803', '2166.50'),
                                  fund_row('2120803', '2166.50', 'income_actual')]) == Decimal('2166.50')
    # 有类行时取类行，不把下级行重复加进去
    assert _top_level_rows_total([fund_row('212', '300'), fund_row('21208', '300'),
                                  fund_row('2120803', '200'), fund_row('2120804', '100'),
                                  fund_row('229', '5')]) == Decimal('305')
    assert _top_level_rows_total([fund_row('2120803', None)]) is None
    assert _top_level_rows_total([fund_row('2120803', '1'), fund_row('2120803', '1')]) is None
    assert _top_level_rows_total([]) is None


# ---------------------------------------------------------------------------
# 同一根因只保留一条
# ---------------------------------------------------------------------------

def plain(issue_id, rule, message, **location):
    return {'id': issue_id, 'rule_id': rule, 'severity': 'error', 'message': message, 'location': location}


def test_missing_table_and_its_cross_check_symptom_are_one_problem():
    issues = [plain('a', 'V33-002', '十二、缺失表：支出决算表', table='支出决算表'),
              plain('b', 'V33-201', '十三、T3(支出决算表)缺失，无法与T1支出合计(11747.36)进行勾稽', t1_expense=11747.36),
              plain('c', 'V33-201', 'T1支出合计与T3不一致')]
    merged, other = deduplicate_native_issues(issues)
    assert merged['rule_id'] == 'V33-002' and merged['message'].endswith('缺失表：支出决算表')
    assert merged['location']['related_rule_ids'] == ['V33-002', 'V33-201']
    assert [f['rule_id'] for f in merged['location']['related_findings']] == ['V33-002', 'V33-201']
    assert other['id'] == 'c'


def test_empty_table_statement_reported_by_two_rules_is_one_problem():
    issues = [plain('a', 'V33-109', '【一般公共预算财政拨款“三公”经费支出决算表】为空表，但该页未见针对该表的空表说明', page=16),
              plain('b', 'V33-114', '空表缺少说明：财政拨款“三公”经费支出决算表为空表，但缺少相应的说明。',
                    page=16, table='财政拨款“三公”经费支出决算表'),
              plain('c', 'V33-114', '空表缺少说明：国有资本经营预算财政拨款收入支出决算表为空表，但缺少相应的说明。',
                    page=18, table='国有资本经营预算财政拨款收入支出决算表')]
    merged, other = deduplicate_native_issues(issues)
    assert merged['rule_id'] == 'V33-114' and merged['location']['table'] == '财政拨款“三公”经费支出决算表'
    assert merged['location']['merged_issue_ids'] == ['a', 'V33-109-1', 'b', 'V33-114-2']
    assert other['id'] == 'c'


def operand_issue(rule, uids, severity='info'):
    refs = [{'fact_uid': uid, 'role': 'parent' if i == 0 else 'child', 'relation_id': 'FIN_03:total'}
            for i, uid in enumerate(uids)]
    return {'id': f'{rule}-x', 'rule_id': rule, 'severity': severity,
            'message': '支出决算表科目total/total_actual与其明细之和差额0.02万元，可能为取整误差。',
            'location': {'operand_refs': refs}, 'check_relation_id': f'{rule}:FIN_03:total'}


def test_same_rounding_hint_from_generic_and_table_sum_rules_is_one_tip():
    uids = ['t3:total', 't3:208', 't3:210']
    context = SimpleNamespace(version_token='v', facts=[])
    result = deduplicate_native_issues([operand_issue('V33-005', uids), operand_issue('V33-120', uids),
                                        operand_issue('V33-240', ['t2:total', 't2:208'])],
                                       SimpleNamespace(fact_context=context))
    assert len(result) == 2
    assert result[0]['location']['related_rule_ids'] == ['V33-005', 'V33-120']
    # 不同原格的提示不合并
    assert result[1]['rule_id'] == 'V33-240'


# ---------------------------------------------------------------------------
# 同比百分比按显示精度取整
# ---------------------------------------------------------------------------

def yoy(percent):
    body = f'甲局2025年度收入支出总计24,699.82万元。与2024年度相比，收入支出总计增加7,849.31万元，增长{percent}%。'
    doc = SimpleNamespace(page_texts=['甲局\n2025年度决算\n一、收入支出决算总体情况说明\n' + body],
                          report_kind='final', fiscal_year=2025, organization_name='甲局')
    issues, unresolved = check_narrative_relations(R33234_NarrativePercentConsistency(), doc)
    assert not unresolved
    return issues


@pytest.mark.parametrize('percent, failed', [
    ('47', False),      # 46.58% 印到整数为 47%
    ('46.6', False),    # 一位小数，差 0.02
    ('46.58', False),
    ('46', True),       # 整数也要四舍五入对
    ('48', True),
    ('46.4', True),     # 一位小数仍按 0.1 容差
])
def test_integer_percent_allows_display_rounding_only(percent, failed):
    issues = yoy(percent)
    assert bool(issues) is failed
    if issues:
        assert issues[0].location['tolerance_pp'] == ('0.5' if '.' not in percent else '0.1')
