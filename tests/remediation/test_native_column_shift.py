"""明细行整列错位根因判定（native_column_shift）的回归测试。

数据取自 2026-10-31 华东政法大学 2025 年度决算实测：
《收入决算表》明细行"本年收入合计"列之和 = 合计行"财政拨款收入"，
《支出决算表》同型；合计行与文字说明自洽，明细行整体左错一列。
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import FactContext
from src.engine.native_column_shift import (
    EXPENSE_LABELS,
    INCOME_LABELS,
    detect_column_shift,
)
from src.engine.native_table_arithmetic import check_expenditure_arithmetic
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33241_Table3_ExpenseAdvancedCheck
from src.schemas.parse_artifact import FiscalFact

HASH = 'b' * 64
ORG = '华东政法大学'


def _fact(code, role, amount, *, org=ORG, uid=None):
    return FiscalFact(
        fact_uid=uid or f'{code}:{role}', indicator=role, raw_value=str(amount),
        normalized_value=Decimal(amount), unit='万元', display_precision=2,
        subject_code=code, subject_name=code, table_code='FIN_02_income',
        instance_uid='one', org_name=org, fiscal_year=2025, period='本年',
        budget_stage='决算',
    )


INCOME_COLUMNS = [(role, INCOME_LABELS[role]) for role in (
    'total_actual', 'fiscal_allocation', 'superior_subsidy_income', 'business_income',
    'operational_income', 'subsidiary_income', 'other_income')]

#: 合计行（与文字说明自洽：147338.58 = 101023.18 + 33450.96 + 12864.44）
INCOME_TOTAL_ROW = {
    'total_actual': '147338.58', 'fiscal_allocation': '101023.18',
    'superior_subsidy_income': '0.00', 'business_income': '33450.96',
    'operational_income': '0.00', 'subsidiary_income': '0.00', 'other_income': '12864.44',
}

#: 明细行按原件印出的列位（金额整体左错一列，"其他收入"列空白）。
INCOME_DETAIL_ROWS = [
    ('205', '87161.66', '0.00', '27561.36', '0.00', '0.00', '12864.44', ''),
    ('20502', '87161.66', '0.00', '27561.36', '0.00', '0.00', '12864.44', ''),
    ('2050205', '86423.96', '0.00', '27561.36', '0.00', '0.00', '12864.44', ''),
    ('2050299', '737.70', '0.00', '0.00', '0.00', '0.00', '0.00', ''),
    ('208', '8749.72', '0.00', '2338.06', '0.00', '0.00', '0.00', ''),
    ('2080502', '1721.64', '0.00', '28.60', '0.00', '0.00', '0.00', ''),
    ('2080505', '4669.00', '0.00', '1553.97', '0.00', '0.00', '0.00', ''),
    ('2080506', '2335.00', '0.00', '755.49', '0.00', '0.00', '0.00', ''),
    ('2080599', '24.08', '0.00', '0.00', '0.00', '0.00', '0.00', ''),
    ('210', '3068.80', '0.00', '937.22', '0.00', '0.00', '0.00', ''),
    ('2101102', '3064.00', '0.00', '937.22', '0.00', '0.00', '0.00', ''),
    ('2109999', '4.80', '0.00', '0.00', '0.00', '0.00', '0.00', ''),
    ('221', '2043.00', '0.00', '2614.32', '0.00', '0.00', '0.00', ''),
    ('2210201', '2043.00', '0.00', '2614.32', '0.00', '0.00', '0.00', ''),
]

INCOME_ROLES = [role for role, _ in INCOME_COLUMNS]


def income_facts():
    facts = [_fact('total', role, value) for role, value in INCOME_TOTAL_ROW.items()]
    for row in INCOME_DETAIL_ROWS:
        code, values = row[0], row[1:]
        for role, value in zip(INCOME_ROLES, values, strict=True):
            if value == '':
                continue
            facts.append(_fact(code, role, value))
    return facts


def test_income_detail_column_shift_detected_with_hdzf_numbers():
    detection = detect_column_shift(INCOME_COLUMNS, income_facts(), org_name=ORG)
    assert detection is not None
    assert detection['direction'] == 'left'
    assert detection['missing_label'] == '本年收入合计'
    assert detection['blank_label'] == '其他收入'
    # 6 个分项列逐列对齐：明细列 k 之和 == 合计行第 k+1 列
    assert len(detection['pairs']) == 6
    first = detection['pairs'][0]
    assert first['detail_label'] == '本年收入合计'
    assert first['total_label'] == '财政拨款收入'
    assert first['detail_sum'] == Decimal('101023.18')
    assert detection['rows_mismatched'] == detection['rows_checked'] >= 3


def test_normal_balanced_income_table_is_not_flagged():
    facts = [_fact('total', role, value) for role, value in INCOME_TOTAL_ROW.items()]
    # 常规表：明细行合计 = 各分项之和，列合计 = 合计行。
    facts += [_fact('205', 'total_actual', '147338.58'),
              _fact('205', 'fiscal_allocation', '101023.18'),
              _fact('205', 'business_income', '33450.96'),
              _fact('205', 'other_income', '12864.44'),
              _fact('208', 'total_actual', '0.00')]
    assert detect_column_shift(INCOME_COLUMNS, facts, org_name=ORG) is None


def test_isolated_row_mismatch_is_not_a_column_shift():
    facts = [_fact('total', role, value) for role, value in INCOME_TOTAL_ROW.items()]
    facts += [_fact('205', 'total_actual', '147338.58'),
              _fact('205', 'fiscal_allocation', '101023.18'),
              _fact('205', 'business_income', '33450.96'),
              _fact('205', 'other_income', '12864.44'),
              # 单行合计错报，但列合计仍然对得上：不得判为整列错位。
              _fact('208', 'total_actual', '999.00'),
              _fact('208', 'fiscal_allocation', '0.00')]
    assert detect_column_shift(INCOME_COLUMNS, facts, org_name=ORG) is None


def test_ambiguous_cell_identity_skips_detection():
    facts = income_facts()
    facts.append(_fact('205', 'other_income', '1.00', uid='dup'))
    assert detect_column_shift(INCOME_COLUMNS, facts, org_name=ORG) is None


EXPENSE_COLUMNS = [(role, EXPENSE_LABELS[role]) for role in (
    'total_actual', 'basic_actual', 'project_actual', 'to_superior_actual',
    'operational_actual', 'subsidiary_actual')]

EXPENSE_TOTAL_ROW = {
    'total_actual': '141688.48', 'basic_actual': '78916.73', 'project_actual': '62771.75',
    'to_superior_actual': '0.00', 'operational_actual': '0.00', 'subsidiary_actual': '0.00',
}

#: 明细行：本年支出合计列之和 = 合计行基本支出；基本支出列之和 = 合计行项目支出。
EXPENSE_DETAIL_ROWS = [
    ('205', '59170.41', '62766.95', '0.00', '0.00', '0.00', ''),
    ('208', '19746.32', '4.80', '0.00', '0.00', '0.00', ''),
    ('221', '0.00', '0.00', '0.00', '0.00', '0.00', ''),
]

EXPENSE_ROLES = [role for role, _ in EXPENSE_COLUMNS]


def expense_facts():
    facts = [_fact('total', role, value) for role, value in EXPENSE_TOTAL_ROW.items()]
    for row in EXPENSE_DETAIL_ROWS:
        code, values = row[0], row[1:]
        for role, value in zip(EXPENSE_ROLES, values, strict=True):
            if value == '':
                continue
            facts.append(_fact(code, role, value))
    return facts


def test_expense_detail_column_shift_detected():
    detection = detect_column_shift(EXPENSE_COLUMNS, expense_facts(), org_name=ORG)
    assert detection is not None
    assert detection['direction'] == 'left'
    assert detection['missing_label'] == '本年支出合计'
    assert detection['blank_label'] == '对附属单位补助支出'


def _expense_doc():
    facts = []
    for index, fact in enumerate(expense_facts()):
        facts.append(FiscalFact(
            fact_uid=fact.fact_uid, indicator=fact.indicator, raw_value=str(fact.normalized_value),
            normalized_value=fact.normalized_value, unit='万元', subject_code=fact.subject_code,
            subject_name=fact.subject_code, table_code='FIN_03_expenditure',
            instance_uid='one', org_name=ORG, fiscal_year=2025,
            budget_stage='决算', period='本年'))
    context = FactContext(facts, HASH, 'run', org_name=ORG, fiscal_year=2025,
                          report_kind='final', budget_stage='决算')
    artifact = SimpleNamespace(source_file_hash=HASH, parse_run_uid='run')
    return SimpleNamespace(parse_artifact=artifact, fact_context=context,
                           fact_receipts=[], structured_consumption=[])


def test_expense_rule_emits_single_high_root_cause_and_material_view_reason():
    with pytest.raises(RuleDeferred) as caught:
        check_expenditure_arithmetic(R33241_Table3_ExpenseAdvancedCheck(), _expense_doc())
    signal = caught.value
    root_cause = [issue for issue in signal.partial_issues
                  if str(issue.check_relation_id).endswith(':column_shift')]
    assert len(root_cause) == 1
    issue = root_cause[0]
    # error → 高危；且不得被"多行勾稽不平"聚簇改写。
    assert issue.severity == 'error'
    assert issue.message.startswith('《支出决算表》明细行金额整体左错一列')
    assert '疑似源文件漏印「本年支出合计」列或整列错位' in issue.message
    # 补核原因带材料视角提示，并指向根因判定。
    assert any('原材料可能存在整列错位' in reason for reason in signal.unresolved_reasons)


def test_income_rule_wires_root_cause_and_material_view_reason(monkeypatch):
    import src.engine.native_income_sources as income_module

    facts = income_facts()
    mappings = [{'source_col_idx': index + 2, 'canonical_measure': role}
                for index, (role, _label) in enumerate(INCOME_COLUMNS)]
    code_rows = {row[0]: index + 1 for index, row in enumerate(INCOME_DETAIL_ROWS)}
    code_rows['total'] = 0
    table = SimpleNamespace(title='收入决算表', cells=[])
    view = (SimpleNamespace(), table, mappings, {}, code_rows, facts)
    monkeypatch.setattr(income_module, '_table_view', lambda doc, code: (view, None))
    monkeypatch.setattr(income_module, '_column_mappings_for_instance',
                        lambda instance, table: mappings)
    # 末尾的 check_native_detail_tables 走它自己模块内的视图入口，需同步打桩。
    import src.engine.native_hierarchy_checks as hierarchy_module
    monkeypatch.setattr(hierarchy_module, '_table_view',
                        lambda doc, code, include_income_sources=False: (view, None))
    context = SimpleNamespace(org_name=ORG, fiscal_year=2025, file_hash=HASH,
                              parse_run_uid='run', version_token='v1', facts=facts,
                              report_kind='final', budget_stage='决算')
    doc = SimpleNamespace(
        parse_artifact=SimpleNamespace(source_file_hash=HASH, parse_run_uid='run',
                                       table_fragments=[]),
        fact_context=context, fact_receipts=[], structured_consumption=[])

    from src.engine.rules_v33 import R33240_Table2_IncomeAdvancedCheck
    with pytest.raises(RuleDeferred) as caught:
        income_module.check_income_sources(R33240_Table2_IncomeAdvancedCheck(), doc)
    signal = caught.value
    root_cause = [issue for issue in signal.partial_issues
                  if str(issue.check_relation_id).endswith(':column_shift')]
    assert len(root_cause) == 1
    issue = root_cause[0]
    assert issue.severity == 'error'
    assert issue.message.startswith('《收入决算表》明细行金额整体左错一列')
    assert '疑似源文件漏印「本年收入合计」列或整列错位' in issue.message
    assert any('原材料可能存在整列错位' in reason for reason in signal.unresolved_reasons)
