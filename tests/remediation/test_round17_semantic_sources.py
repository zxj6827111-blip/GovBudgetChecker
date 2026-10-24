from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.fact_context import build_fact_context_from_artifact, FactContext, resolve_operand
from src.engine.common_rules import CMM002_TextAnomalyRule
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import R33242_Table4_ComprehensiveCheck, _extract_standard_three_public_total
from src.schemas.parse_artifact import LogicalTableInstance, ParseArtifact, ParsedCell, ParsedTable, FiscalFact
from src.schemas.review_report import build_review_report, ReviewReport
from src.services.document_profile_resolver import resolve_document_profile
from src.services.fiscal_column_identity import canonical_column_mappings
from src.services.fiscal_table_rules import NINE_TABLE_RULES
from src.services.fiscal_fact_materializer import FiscalFactMaterializer
from src.services.evidence_guard import apply_evidence_completeness
from src.services.pdf_parser import PDFParser
from src.services.rule_source_binding import bind_rule_sources, _vertical_operands, _total_sheet_operands


@pytest.mark.parametrize('text, expected', [
    ('主要原因：当年抚恤金据实支出,\n20', False),
    ('主要原因：当年抚恤金据实支出,\n20\n', False),
    ('支出,\n20万元。', True),
    ('支出,20万元。', True),
])
def test_punctuation_does_not_treat_final_page_footer_as_amount(text, expected):
    issues = CMM002_TextAnomalyRule().apply(SimpleNamespace(page_texts=[text]))
    assert bool(issues) == expected


def matrix():
    rows = [
        ['财政拨款“三公”经费'] + [None] * 11,
        ['合计', None, '因公出国\n（境）费', None, '公务用车购置及运行维护费', None, None, None, None, None, '公务接待费', None],
        [None] * 4 + ['小计', None, '公务用车\n购置费', None, '公务用车\n运行维护费', None, None, None],
        ['预算数', '决算数'] * 6,
        ['30.00', '20.00', '10.00', '8.00', '15.00', '10.00', '10.00', '4.00', '5.00', '6.00', '5.00', '2.00'],
    ]
    spans = {(0, 0): 12, (1, 0): 2, (1, 2): 2, (1, 4): 6, (1, 10): 2,
             (2, 4): 2, (2, 6): 2, (2, 8): 2}
    cells = []
    for row_idx, row in enumerate(rows):
        for col, value in enumerate(row):
            box = (col*10, row_idx*10, (col+spans.get((row_idx, col), 1))*10, (row_idx+1)*10) if value is not None else None
            cells.append(ParsedCell(row_idx, col, value or '', value or '',
                                    float(value) if row_idx == 4 else None, 1, box,
                                    row_idx < 4, .99, '万元', 'lines'))
    return ParsedTable('FIN_07_three_public', '财政拨款“三公”经费支出决算表',
                       1, 0, (0, 0, 120, 50), 4, .99, 'lines', '万元', rows, cells, 'matrix')


def test_stage_columns_survive_native_cache_and_have_distinct_ids():
    table = ParsedTable.from_dict(matrix().to_dict())
    assert table.rows[0][1] is None  # 原合并格占位与实际空白格须保持不同。
    artifact = ParseArtifact(source_file_hash='a'*64, parse_run_uid='run', page_count=1,
                             table_fragments=[table], physical_fragments=[table],
                             logical_tables=[LogicalTableInstance('matrix', table.table_code, table.title, ['matrix'], 1, 1)])
    context = build_fact_context_from_artifact(artifact, org_name='甲财政局', fiscal_year=2025, report_kind='final', doc_type='dept_final')
    assert len(context.facts) == 12
    assert len({f.fact_uid for f in context.facts}) == 12
    totals = {(f.budget_stage, f.normalized_value) for f in context.facts if f.indicator == 'total_actual'}
    assert totals == {('年初预算', Decimal('30')), ('决算', Decimal('20'))}
    operations = {(f.budget_stage, f.normalized_value) for f in context.facts if f.indicator == 'vehicle_operation'}
    assert operations == {('年初预算', Decimal('5')), ('决算', Decimal('6'))}


@pytest.mark.parametrize('code,rows', [
    ('FIN_02_income', [['科目编码', '科目名称', '本年收入合计', '财政拨款收入', '事业收入'],
                       ['', '', '决算数', '决算数', '决算数']]),
    ('FIN_03_expenditure', [['科目编码', '科目名称', '本年支出合计', '基本支出', '项目支出'],
                            ['', '', '决算数', '决算数', '决算数']]),
    ('FIN_05_general_public_expenditure', [['科目编码', '科目名称', '一般公共预算财政拨款支出决算数', '', ''],
                                         ['', '', '合计', '基本支出', '项目支出']]),
])
def test_business_total_header_beats_shared_stage(code, rows):
    table = ParsedTable(code, '原文表名', 1, 0, None, 2, .99, 'lines', '万元', rows, [], 'table')
    mappings = canonical_column_mappings(table, NINE_TABLE_RULES[code])
    total = [item for item in mappings if item['canonical_measure'] == 'total_actual']
    assert len(total) == 1
    assert total[0]['source_col_idx'] == 2


def test_grand_total_scope_does_not_mix_annual_total():
    facts = [FiscalFact(fact_uid=name, subject_name=name, indicator='expenditure_actual',
                         subject_code='total', table_code='FIN_01_income_expenditure_total',
                         raw_value=value, unit='万元', normalized_value=Decimal(value), recognition_status='unverified')
             for name, value in [('本年支出合计', '100'), ('总计', '120')]]
    context = FactContext(facts=facts)
    resolution = resolve_operand(context, 'expenditure_actual', subject_names=('总计', '合计'))
    assert resolution.status == 'ok' and resolution.value == Decimal('120')
    assert resolution.fact.fact_uid == '总计'


def test_canonical_report_and_csv_share_existing_evidence_page_without_certifying_it():
    from api.routes.reports import _build_csv_row

    issue = {'id': 'i', 'rule_id': 'V33-243', 'severity': 'info', 'title': '待复核',
             'evidence': [{'page': 13, 'text': '现有原页引用'}], 'verification_status': 'unverified'}
    report = build_review_report({'job_id': 'j', 'result': {'issues': {'all': [issue]}}})
    canonical = report['tips'][0]
    assert canonical['page_number'] == _build_csv_row(canonical)['page'] == 13
    assert canonical['verification_status'] == 'unverified' and 'bbox' not in canonical
    assert 'page_number' not in issue  # The original input remains unchanged.
    assert ReviewReport.from_dict(report).to_dict()['tips'][0]['page_number'] == 13


def test_right_ledger_and_economic_column_survive_empty_left_labels():
    table = ParsedTable('FIN_01_income_expenditure_total', '收入支出决算总表',
                        1, 0, (0, 0, 80, 20), 1, .99, 'lines', '万元',
                        [['项目', '决算数', '项目', '决算数'], ['', '', '本年支出合计', '100']], [], 'ledger')
    table.cells = [ParsedCell(row, col, text, text, 100.0 if text == '100' else None,
                              1, (col*20, row*10, (col+1)*20, (row+1)*10), row == 0, .99, '万元', 'lines')
                   for row, values in enumerate(table.rows) for col, text in enumerate(values)]
    facts = FiscalFactMaterializer(None).build_fiscal_facts_for_table(table, [
        {'source_col_idx': 1, 'canonical_measure': 'income_actual', 'source_label_cols': [0]},
        {'source_col_idx': 3, 'canonical_measure': 'expenditure_actual', 'source_label_cols': [2]}], budget_stage='决算')
    assert len(facts) == 1 and facts[0].subject_name == '本年支出合计'
    assert facts[0].normalized_value == Decimal('100')


def test_materializer_does_not_restore_rejected_concatenated_amount():
    assert FiscalFactMaterializer(None)._get_numeric_value({'raw_text': '1,158.99 445', 'numeric_value': None}) is None


def test_semantic_grid_beats_larger_clipped_text_shadow():
    parser = PDFParser(None)
    good = matrix()
    bad = ParsedTable(good.table_code, good.title, 1, 0, (0, 0, 150, 60), 4, .99,
                       'text', '万元', [['表名']] * 5, [], 'bad')
    assert parser._merge_candidates([good], [bad]) == [good]


def test_title_hint_does_not_interrupt_first_continuation_data_row():
    parser = PDFParser(None)
    first = ParsedTable('FIN_03_expenditure', '支出决算表', 1, 0, (0, 0, 40, 20), 1, .99,
                         'lines', '万元', [['科目编码', '科目名称', '合计', '基本支出'], ['208', '社会保障', '100', '100']], [], 'first')
    second = ParsedTable('UNKNOWN_P2_T0', '续表', 2, 0, (0, 20, 40, 40), 1, .99,
                          'lines', None, [['210', '医疗', '50', '50']], [], 'second')
    second.cells = [ParsedCell(0, col, text, text, float(text) if text.isdigit() else None,
                               2, (col*10, 20, (col+1)*10, 40), True, .99, None, 'lines')
                    for col, text in enumerate(second.rows[0])]
    hint = ParsedTable('FIN_09_state_capital', '国有资本经营预算支出', 2, 1, (0, 0, 40, 40),
                        1, .8, 'page_text_fallback', None, [['国有资本经营预算支出']], [], 'hint')
    joined = parser._coalesce_tables([first, hint, second])
    physical = next(t for t in joined if t.table_code == first.table_code)
    assert len(physical.rows) == 3
    assert all(not c.is_header for c in physical.cells if c.page_number == 2)
    assert {c.origin_fragment_uid for c in physical.cells} == {'second'}


def test_cover_publisher_is_literal_and_conflicting_names_are_not_guessed():
    profile = resolve_document_profile(filename='海虹桥.pdf', page_texts=['上海虹桥国际中央商务区管理委员会\n2025年度部门决算'])
    assert profile.organization_name.value == '上海虹桥国际中央商务区管理委员会'
    ambiguous = resolve_document_profile(page_texts=['甲财政局\n乙财政局\n2025年度部门决算'])
    assert ambiguous.organization_name.value is None
    absent = resolve_document_profile(filename='上海市交通委员会.pdf', page_texts=['2025年度部门决算'])
    assert absent.organization_name.value is None


@pytest.mark.parametrize('value', ['1,158.99 445', '12\n34', '30 20'])
def test_adjacent_amounts_are_not_concatenated(value):
    assert PDFParser(None)._parse_numeric(value) is None


def test_year_in_narrative_is_not_a_three_public_amount():
    assert _extract_standard_three_public_total('2024年度“三公”经费支出决算为88.70万元。', '决算') == 88.7
    assert _extract_standard_three_public_total('“三公”经费支出决算2024年度情况说明。', '决算') is None
    assert _extract_standard_three_public_total('“三公”经费决算为2024.00万元。', '决算') == 2024


def test_blank_fund_cells_keep_expense_column_identity(monkeypatch):
    rows = [
        ['收入', '', '支出', '', '', '', ''],
        ['项目', '决算数', '项目', '合计', '一般公共预算财政拨款', '政府性基金', '国有资本'],
        ['一般公共预算财政拨款', '100', '本年支出合计', '100', '100', '', ''],
        ['政府性基金', '', '年末财政拨款结转和结余', '0', '0', '', ''],
        ['年初财政拨款结转和结余', '0', '总计', '100', '100', '', ''],
        ['本年收入合计', '100', '', '', '', '', ''],
        ['总计', '100', '', '', '', '', ''],
    ]
    monkeypatch.setattr('src.engine.rules_v33._get_table_rows', lambda *_args, **_kwargs: rows)
    try:
        issues = R33242_Table4_ComprehensiveCheck().apply(SimpleNamespace())
    except RuleDeferred as signal:
        issues = signal.partial_issues
    assert not issues


def test_blank_carry_is_unresolved_instead_of_zero(monkeypatch):
    rows = [
        ['收入', '', '支出', '', '', '', ''],
        ['项目', '决算数', '项目', '合计', '一般公共预算', '政府性基金', '国有资本'],
        ['一般公共预算财政拨款', '100', '本年支出合计', '100', '100', '', ''],
        ['本年收入合计', '100', '', '', '', '', ''],
        ['年初财政拨款结转和结余', '', '年末财政拨款结转和结余', '', '', '', ''],
        ['一般公共预算财政拨款', '', '', '', '', '', ''],
        ['总计', '100', '总计', '100', '100', '', ''],
    ]
    monkeypatch.setattr('src.engine.rules_v33._get_table_rows', lambda *_args, **_kwargs: rows)
    with pytest.raises(RuleDeferred, match='空白或未绑定'):
        R33242_Table4_ComprehensiveCheck().apply(SimpleNamespace())


def test_parent_cell_cannot_certify_vertical_sum():
    item = finding('生成的父子求和说明')
    item['location'] = {'table': '支出决算表', 'code': '208', 'type': 'vertical'}
    context = SimpleNamespace(file_hash='a'*64, parse_run_uid='run', version_token='v',
                              facts=[SimpleNamespace(fact_uid='parent', subject_code='208', recognition_status='accepted')])
    artifact = SimpleNamespace(source_file_hash='a'*64, parse_run_uid='run')
    receipt = bind_rule_sources({'rule_findings': [item]}, page_texts=['真实原文'], source_file_hash='a'*64,
                               artifact=artifact, fact_context=context,
                               consumption=[{'rule_id': 'V33-113', 'fact_version': 'v', 'fact_uids': ['parent']}])
    assert receipt['bound_rule_findings'] == 0
    assert 'fact_ids' not in item


def test_vertical_proof_requires_every_current_child_operand():
    def fact(code, value, status='accepted'):
        return SimpleNamespace(table_code='FIN_03_expenditure', indicator='total_actual',
                               budget_stage='决算', subject_code=code, subject_name=code,
                               normalized_value=Decimal(value), recognition_status=status)
    operands = [fact('208', '100'), fact('20802', '40'), fact('20805', '50'), fact('20810', '0')]
    location = {'code': '208', 'type': 'vertical', 'diff': '10'}
    assert _vertical_operands(operands, 'FIN_03_expenditure', location) == operands
    assert not _vertical_operands(operands[:2], 'FIN_03_expenditure', location)
    assert not _vertical_operands(operands + [fact('20805', '50')], 'FIN_03_expenditure', location)
    assert not _vertical_operands(operands[:3] + [fact('20810', '0', 'low_confidence')], 'FIN_03_expenditure', location)


@pytest.mark.parametrize('case', ['complete', 'missing_zero', 'changed_value', 'budget_column'])
def test_total_identity_binds_all_four_explicit_current_operands(case):
    values = {'总计': '100', '本年支出合计': '90', '结余分配': '0', '年末结转和结余': '9'}
    facts = [SimpleNamespace(table_code='FIN_01_income_expenditure_total', indicator='expenditure_actual',
                             budget_stage='决算', subject_name=name, recognition_status='accepted',
                             normalized_value=Decimal(value)) for name, value in values.items()]
    if case == 'missing_zero': facts.pop(2)
    elif case == 'changed_value': facts[0].normalized_value = Decimal('99')
    elif case == 'budget_column': facts[0].budget_stage = '年初预算'
    result = _total_sheet_operands(facts, values)
    assert bool(result) == (case == 'complete')
    if result: assert len(result) == 4


def finding(quote, page=1):
    return {'id': 'R-1', 'source': 'rule', 'rule_id': 'V33-113', 'severity': 'error',
            'location': {'page': page}, 'evidence': [{'page': page, 'text': quote}]}


def test_generated_explanations_use_all_explicit_literal_reference_pages():
    item = finding('预算10、决算20，计算说明')
    item['location']['table_refs'] = [{'page': 1, 'span': '年初预算10万元'}, {'page': 2, 'span': '支出决算20万元'}]
    result = {'rule_findings': [item]}
    pages = ['年初预算 10 万元。', '支出决算20万元。']
    assert bind_rule_sources(result, page_texts=pages, source_file_hash='a'*64)['bound_rule_findings'] == 1
    assert [x['physical_page'] for x in item['evidence']] == [1, 2]
    check = apply_evidence_completeness(result, page_texts=pages, document_page_count=2, source_file_hash='a'*64)
    assert check['verified_count'] == 1
    assert check['rule_warning_count'] == 0
    assert item['evidence_explanation'][0]['text'] == '预算10、决算20，计算说明'


@pytest.mark.parametrize('case', ['wrong_page', 'ambiguous', 'stale_sha', 'invented'])
def test_native_binding_rejects_wrong_or_ambiguous_source(case):
    item = finding('真实原文')
    pages = ['真实原文']
    if case == 'wrong_page': item['evidence'][0]['page'] = 2
    elif case == 'ambiguous': pages = ['真实原文 真实原文']
    elif case == 'stale_sha': item['evidence'][0]['file_hash'] = 'b'*64
    elif case == 'invented': item['evidence'][0]['text'] = '编造原文'
    assert bind_rule_sources({'rule_findings': [item]}, page_texts=pages, source_file_hash='a'*64)['bound_rule_findings'] == 0
    assert item['evidence'][0].get('source_identity_status') is None
