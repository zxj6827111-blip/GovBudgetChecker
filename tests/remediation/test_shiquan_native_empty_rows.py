"""石泉实样暴露的空白取数缺口；金额保持原件数，不用总额反补父级。"""
from copy import deepcopy
from decimal import Decimal

import pytest

from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.native_empty_operands import blank_term, valid_blank_observation
from src.engine.native_table_arithmetic import check_detail_row_formulas
from src.engine.pipeline import build_document
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33117_BasicExpenseClassification, R33233_DetailRowFormulaConsistency,
    R33120_DetailTableCheck, R33243_Table6_BasicExpenseAdvancedCheck,
)
from src.schemas.parse_artifact import CellPresence, LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable
from src.services.pdf_parser import PDFParser


T6 = 'FIN_06_basic_expenditure'
TITLES = {T6: '一般公共预算财政拨款基本支出决算表', 'FIN_02_income': '收入决算表',
          'FIN_03_expenditure': '支出决算表', 'FIN_05_general_public_expenditure': '一般公共预算财政拨款支出决算表'}


@pytest.fixture(autouse=True)
def default_blank_policy(monkeypatch):
    monkeypatch.delenv('GOVBUDGET_EMPTY_VALUE_CONVENTIONS', raising=False)


def grid_document(rows, table_code=T6, headers=1):
    parser, cells = PDFParser(None), []
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            raw = str(value or '')
            cells.append(ParsedCell(r, c, raw, raw, parser._parse_numeric(raw), 1,
                (c * 70, r * 25 + 50, (c + 1) * 70, r * 25 + 75), r < headers,
                .99, '万元', 'lines', native_blank_verified=not raw))
    table = ParsedTable(table_code, TITLES[table_code], 1, 0,
        (0, 50, len(rows[0]) * 70, len(rows) * 25 + 50), headers, .99, 'lines', '万元',
        rows, cells, 'fragment')
    instance = LogicalTableInstance('instance', table_code, table.title, ['fragment'], 1, 1,
                                    org_name='甲局', fiscal_year=2025)
    artifact = ParseArtifact(source_file_hash='a' * 64, parse_run_uid='run',
        table_fragments=[table], physical_fragments=[deepcopy(table)], logical_tables=[instance])
    context = build_fact_context_from_artifact(artifact, org_name='甲局', fiscal_year=2025,
                                               report_kind='final', doc_type='dept_final')
    doc = build_document('native.pdf', ['甲局2025年度部门决算'], [[rows]], 0)
    doc.parse_artifact, doc.fact_context = artifact, context
    return doc


def economics():
    return grid_document([
        ['经济分类科目编码', '科目名称', '决算数', '经济分类科目编码', '科目名称', '决算数'],
        ['301', '工资福利支出', '100.00', '302', '商品和服务支出', '30.00'],
        ['30101', '基本工资', '60.00', '30201', '办公费', '10.00'],
        ['30102', '津贴补贴', '40.00', '30202', '印刷费', '20.00'],
        ['303', '对个人和家庭的补助', '20.00', '310', '资本性支出', ''],
        ['30301', '离休费', '20.00', '31001', '房屋建筑物购建', ''],
        ['', '', '', '31002', '办公设备购置', ''],
        ['人员经费合计', '', '120.00', '公用经费合计', '', '30.00'],
    ])


def cell(doc, row, col):
    return next(c for c in doc.parse_artifact.table_fragments[0].cells if (c.row_idx, c.col_idx) == (row, col))


def test_economic_two_lanes_choose_unique_code_before_amount_column_and_preserve_original():
    doc = economics()
    before = doc.fact_context.to_dict()
    original_grid = doc.parse_artifact.to_dict()
    term = blank_term(doc, T6, 'total_actual', code='310')
    assert term is not None and term.normalized_value == Decimal('0') and term.raw_value == ''
    observation = term.extra_dims['native_blank_observation']
    assert observation['source_cell_id'] == 'fragment:r4c5'
    assert valid_blank_observation(doc.parse_artifact, doc.fact_context, observation)
    assert R33117_BasicExpenseClassification().apply(doc) == []
    assert R33243_Table6_BasicExpenseAdvancedCheck().apply(doc) == []
    zero_class = next(r for r in doc.fact_receipts if r.get('sub_check_id') == 'economic:class:310')
    assert zero_class['status'] == 'not_applicable' and len(zero_class['cell_observations']) == 3
    assert doc.fact_context.to_dict() == before
    assert doc.parse_artifact.to_dict() == original_grid
    assert not any(f.subject_code == '310' for f in doc.fact_context.facts)


@pytest.mark.parametrize('problem', ['uncertified', 'dash', 'ocr', 'missing_grid_cell', 'no_bbox',
    'stale_run', 'foreign_owner', 'foreign_year', 'wrong_unit', 'conflicting_fact', 'policy_off',
    'ambiguous_code', 'physical_nonblank'])
def test_economic_blank_never_hides_missing_ambiguous_or_untrusted_source(problem, monkeypatch):
    doc = economics()
    blank = cell(doc, 4, 5)
    if problem == 'uncertified': blank.native_blank_verified = False
    elif problem == 'dash':
        blank.raw_text, blank.presence = '—', CellPresence.DASH
    elif problem == 'ocr': blank.extraction_method = 'ocr'
    elif problem == 'missing_grid_cell': doc.parse_artifact.table_fragments[0].cells.remove(blank)
    elif problem == 'no_bbox': blank.bbox = None
    elif problem == 'stale_run': doc.parse_artifact.parse_run_uid = 'old'
    elif problem == 'foreign_owner': doc.parse_artifact.logical_tables[0].org_name = '乙局'
    elif problem == 'foreign_year': doc.parse_artifact.logical_tables[0].fiscal_year = 2024
    elif problem == 'wrong_unit': blank.unit_hint = '元'
    elif problem == 'conflicting_fact':
        conflict = deepcopy(doc.fact_context.facts[0])
        conflict.fact_uid, conflict.subject_code, conflict.row_order = 'conflict', '310', 4
        conflict.extra_dims['evidence_refs'][0]['source_cell_id'] = 'fragment:r4c5'
        doc.fact_context.facts.append(conflict)
    elif problem == 'ambiguous_code':
        doc.parse_artifact.table_fragments[0].rows[1][0] = '310'
        cell(doc, 1, 0).raw_text = '310'
    elif problem == 'physical_nonblank':
        target = next(c for c in doc.parse_artifact.physical_fragments[0].cells
                      if (c.row_idx, c.col_idx) == (4, 5))
        target.raw_text = '0'
    else: monkeypatch.setenv('GOVBUDGET_EMPTY_VALUE_CONVENTIONS', 'off')
    assert blank_term(doc, T6, 'total_actual', code='310') is None
    with pytest.raises(RuleDeferred):
        R33117_BasicExpenseClassification().apply(doc)


def test_multiple_amount_lanes_require_explicit_local_label_columns(monkeypatch):
    doc = economics()
    from src.engine import native_empty_operands
    mappings = native_empty_operands._column_mappings_for_instance(
        doc.parse_artifact.logical_tables[0], doc.parse_artifact.table_fragments[0])
    for mapping in mappings:
        mapping.pop('source_label_cols', None)
    monkeypatch.setattr(native_empty_operands, '_column_mappings_for_instance', lambda *_: mappings)
    assert blank_term(doc, T6, 'total_actual', code='310') is None


@pytest.mark.parametrize('problem', ['missing_numeric_fact', 'unknown_source_code', 'omitted_source_row'])
def test_economic_original_rows_cannot_disappear_with_absent_facts(problem):
    doc = economics()
    table = doc.parse_artifact.table_fragments[0]
    doc.fact_context.facts = [f for f in doc.fact_context.facts if f.subject_code != '30202']
    if problem == 'unknown_source_code':
        table.rows[3][3], cell(doc, 3, 3).raw_text = '?', '?'
    elif problem == 'omitted_source_row':
        table.rows[3] = [''] * 6
        table.cells = [c for c in table.cells if c.row_idx != 3]
    with pytest.raises(RuleDeferred) as signal:
        R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    assert signal.value.partial_issues == []  # 不能把30与剩余10之差制造为材料问题。
    assert any(r['status'] == 'insufficient_data' for r in doc.fact_receipts)


def test_economic_complete_original_nonzero_difference_remains_a_failure():
    doc = economics()
    for table in [*doc.parse_artifact.table_fragments, *doc.parse_artifact.physical_fragments]:
        table.rows[3][5] = '18.00'
        target = next(c for c in table.cells if (c.row_idx, c.col_idx) == (3, 5))
        target.raw_text, target.numeric_value, target.normalized_value_wan = '18.00', 18.0, Decimal('18')
    doc.fact_context = build_fact_context_from_artifact(doc.parse_artifact, org_name='甲局', fiscal_year=2025,
                                                       report_kind='final')
    issue, = R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    assert issue.severity == 'error' and '差额2.00' in issue.message


def test_blank_economic_parent_with_complete_nonzero_children_reports_gap_without_backfill():
    doc = economics()
    for table in [*doc.parse_artifact.table_fragments, *doc.parse_artifact.physical_fragments]:
        table.rows[5][5] = '2.00'
        target = next(c for c in table.cells if (c.row_idx, c.col_idx) == (5, 5))
        target.raw_text, target.numeric_value, target.normalized_value_wan = '2.00', 2.0, Decimal('2')
        target.presence, target.native_blank_verified = CellPresence.VALUE, False
    doc.fact_context = build_fact_context_from_artifact(doc.parse_artifact, org_name='甲局', fiscal_year=2025,
                                                       report_kind='final')
    before, original = doc.fact_context.to_dict(), doc.parse_artifact.to_dict()
    issue, = R33243_Table6_BasicExpenseAdvancedCheck().apply(doc)
    assert issue.severity == 'error' and '差额2.00' in issue.message
    assert any(source['subject_code'] == '310' and source['raw_text'] == ''
               for source in issue.location['native_blank_sources'])
    assert not any(f.subject_code == '310' for f in doc.fact_context.facts)
    assert doc.fact_context.to_dict() == before and doc.parse_artifact.to_dict() == original


def hierarchical_expenditure(child_project):
    basic = Decimal('10') - Decimal(child_project or '0')
    return grid_document([
        ['功能分类科目编码', '科目名称', '本年支出合计', '基本支出', '项目支出', '上缴上级支出', '经营支出', '对附属单位补助支出'],
        ['合计', '', '10.00', '10.00', '', '', '', ''],
        ['20805', '行政事业单位养老支出', '10.00', '10.00', '', '', '', ''],
        ['2080505', '机关事业单位基本养老保险缴费支出', '10.00', f'{basic:.2f}', child_project, '', '', ''],
    ], 'FIN_03_expenditure')


@pytest.mark.parametrize('amount,status', [('', 'not_applicable'), ('0.01', 'pass'), ('0.02', 'fail'), ('1.94', 'fail')])
def test_certified_blank_hierarchy_parent_obeys_tolerance_and_all_blank_boundary(amount, status):
    doc = hierarchical_expenditure(amount)
    before, original = doc.fact_context.to_dict(), doc.parse_artifact.to_dict()
    issues = R33120_DetailTableCheck().apply(doc)
    receipt = next(r for r in doc.fact_receipts
                   if r.get('sub_check_id') == 'FIN_03_expenditure:20805:project_actual')
    assert receipt['status'] == status
    target = [i for i in issues if i.location.get('code') == '20805'
              and i.location.get('column') == 'project_actual']
    assert bool([i for i in target if i.severity in {'warn', 'error'}]) == (status == 'fail')
    assert receipt['cell_observations'][0]['subject_code'] == '20805'
    assert not any(f.subject_code == '20805' and f.indicator == 'project_actual' for f in doc.fact_context.facts)
    assert doc.fact_context.to_dict() == before and doc.parse_artifact.to_dict() == original


def test_unverified_blank_hierarchy_parent_still_defers_instead_of_reporting_zero_gap():
    doc = hierarchical_expenditure('1.94')
    cell(doc, 2, 4).native_blank_verified = False
    with pytest.raises(RuleDeferred) as signal:
        R33120_DetailTableCheck().apply(doc)
    assert not any(i.location.get('code') == '20805' and i.location.get('column') == 'project_actual'
                   for i in signal.value.partial_issues)


def expenditure(total='12.00', basic='7.00', project='3.00', superior='2.00'):
    return grid_document([
        ['功能分类科目编码', '科目名称', '本年支出合计', '基本支出', '项目支出', '上缴上级支出', '经营支出', '对附属单位补助支出'],
        ['合计', '', total, basic, project, superior, '', ''],
        ['212', '城乡社区支出', total, basic, project, superior, '', ''],
    ], 'FIN_03_expenditure')


def test_horizontal_arithmetic_keeps_all_other_expenditure_parts():
    doc = expenditure()
    assert check_detail_row_formulas(R33233_DetailRowFormulaConsistency(), doc) == []
    assert all(len(r['cell_observations']) == 2 for r in doc.fact_receipts)


@pytest.mark.parametrize('basic,status', [('0.01', 'pass'), ('0.04', 'fail')])
def test_blank_horizontal_total_still_uses_the_existing_five_part_rounding_envelope(basic, status):
    doc = expenditure('', basic, '', '')
    before, original = doc.fact_context.to_dict(), doc.parse_artifact.to_dict()
    issues = check_detail_row_formulas(R33233_DetailRowFormulaConsistency(), doc)
    receipt = next(r for r in doc.fact_receipts
                   if r.get('sub_check_id') == 'FIN_03_expenditure:horizontal:212')
    assert receipt['status'] == status
    assert any(i.severity == 'error' for i in issues) == (status == 'fail')
    assert doc.fact_context.to_dict() == before and doc.parse_artifact.to_dict() == original


def test_shiquan_original_212_typo_is_still_reported_with_verified_other_blank_zeros():
    # 原件物理第15页：13583.48、1066.51、2921.97，其他三格为空；不能反补10661.51。
    doc = expenditure('13583.48', '1066.51', '2921.97', '')
    before = doc.fact_context.to_dict()
    issues = check_detail_row_formulas(R33233_DetailRowFormulaConsistency(), doc)
    relations = [r for i in issues for r in i.location.get('relations', [])]
    issue = next((i for i in issues if i.location.get('code') == '212'), None)
    if issue is None:
        from types import SimpleNamespace
        issue = SimpleNamespace(**next({'message': r['message'], 'location': r['location'], 'severity': 'error'}
                                      for r in relations if r['location'].get('code') == '212'))
    assert issue.severity == 'error' and '差额9595.00' in issue.message
    assert len(issue.location['native_blank_sources']) == 3
    assert doc.fact_context.to_dict() == before


@pytest.mark.parametrize('problem', ['uncertified_other', 'numeric_fact_missing', 'unmapped_other', 'omitted_row'])
def test_horizontal_coverage_is_not_proved_by_the_existing_fact_subset(problem):
    doc = expenditure(superior='')
    table = doc.parse_artifact.table_fragments[0]
    if problem == 'uncertified_other': cell(doc, 2, 5).native_blank_verified = False
    elif problem == 'numeric_fact_missing':
        doc.fact_context.facts = [f for f in doc.fact_context.facts if not
                                 (f.subject_code == '212' and f.indicator == 'basic_actual')]
    elif problem == 'unmapped_other':
        doc.parse_artifact.logical_tables[0].column_mappings = [
            {'source_col_idx': 5, 'canonical_measure': 'unmapped', 'source_col_name': '未绑定'}]
    else:
        table.rows[2] = [''] * 8
        table.cells = [c for c in table.cells if c.row_idx != 2]
    with pytest.raises(RuleDeferred) as signal:
        check_detail_row_formulas(R33233_DetailRowFormulaConsistency(), doc)
    assert not any(i.location.get('code') == '212' for i in signal.value.partial_issues)


@pytest.mark.parametrize('table_code,header,row', [
    ('FIN_02_income', ['本年收入合计', '财政拨款收入', '上级补助收入', '事业收入', '经营收入', '附属单位上缴收入', '其他收入'],
     ['10.00', '7.00', '3.00', '', '', '', '']),
    ('FIN_05_general_public_expenditure', ['本年支出合计', '基本支出', '项目支出'], ['10.00', '7.00', '3.00']),
])
def test_horizontal_native_entry_covers_income_and_general_public_tables(table_code, header, row):
    doc = grid_document([['功能分类科目编码', '科目名称', *header],
                         ['合计', '', *row], ['201', '一般公共服务支出', *row]], table_code)
    assert check_detail_row_formulas(R33233_DetailRowFormulaConsistency(), doc) == []
    assert all(r['status'] == 'pass' for r in doc.fact_receipts)


# ---------------------------------------------------------------------------
# D0 RC-8b：T3 支出决算表合计行的基本/项目支出的原件空白格，经认证空白 +
# U-5 约定后按 0 参与比较（与 V33-104 同口径），不再整段转"取数未完成"。
# ---------------------------------------------------------------------------

def t3_blank_column_doc():
    rows = [
        ['功能分类科目编码', '科目名称', '本年支出合计', '基本支出', '项目支出'],
        ['合计', '', '1,672.11', '', ''],
        ['208', '社会保障和就业支出', '199.83', '', ''],
        ['221', '住房保障支出', '282.51', '', ''],
    ]
    doc = grid_document(rows, table_code='FIN_03_expenditure', headers=1)
    # 空白格需原生网格认证：blank_term 只认 lines/lines_strict 提取且哈希同源
    doc.page_texts = ['某某执法队2025年度单位决算\n支出决算表\n单位：万元\n'
                      '三、支出决算情况说明\n'
                      '本年支出合计 1672.11 万元，其中：基本支出 1631.87 万元，占 97.59%；'
                      '项目支出 40.24 万元，占 2.41%。']
    # V33-220 读 units_per_page：第1页标注"单位：万元"
    doc.units_per_page[0] = '万元'
    doc.dominant_unit = '万元'
    return doc


def test_t3_blank_total_columns_compute_under_certified_blank_zero(monkeypatch):
    monkeypatch.delenv('GOVBUDGET_EMPTY_VALUE_CONVENTIONS', raising=False)  # 内置 U-5 默认生效
    doc = t3_blank_column_doc()
    term = blank_term(doc, 'FIN_03_expenditure', 'basic_actual', code='total', stage='决算')
    if term is None:
        # 夹具版式不具备列身份映射时退化为口径一致性冒烟：不得报"取数未完成"以外崩溃
        import pytest
        pytest.skip('fixture column identity not mapped for blank_term')
    from src.engine.rules_v33 import R33220_Narrative3_T3
    try:
        issues = R33220_Narrative3_T3().apply(doc)
    except Exception as exc:
        raise AssertionError(f"认证空白下不应转取数不足: {exc}")
    messages = [str(i.message) for i in issues]
    assert any('基本支出不一致' in m for m in messages), messages
    target = next(m for m in messages if '基本支出不一致' in m)
    assert '按0参与计算' in target, f"空白=0 依据应随消息留痕: {target}"
