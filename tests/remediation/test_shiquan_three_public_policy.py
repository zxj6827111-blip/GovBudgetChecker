"""石泉原件暴露的三公零金额适用性及反例，不扩大legacy取数。"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_native_blank_cells import grid_document
from src.engine.three_public_disclosure_policy import movement_is_explicit_zero, reception_quantity_not_required


pytestmark = pytest.mark.usefixtures('approved_test_blank_convention')
RULE = SimpleNamespace(code='V33-246')


@pytest.mark.parametrize('clause', [
    '因公出国（境）费支出决算增加 0 万元', '增长0%', '减少0', '下降0.00%',
    '公务接待费增加0.00万元、增长0.00%', '与2024年度相比增加0万元',
])
def test_explicit_zero_movement_is_not_a_direction_change(clause):
    assert movement_is_explicit_zero(clause)


@pytest.mark.parametrize('clause', [
    '减少为0万元', '降至0万元', '减少到0万元', '增加1万元', '减少0.01万元',
    '增加0万元、增长1%', '增加0万元至3万元', '增加0万元、减少为0万元',
    '增加0万元且降至0万元', '高于0万元但增长0%',
    '下降', '持平', '无支出', '',
])
def test_target_zero_and_mixed_nonzero_do_not_hide_direction_conflict(clause):
    assert not movement_is_explicit_zero(clause)


def native_reception(actual='', budget='1.00'):
    rows = [
        ['财政拨款“三公”经费'] + [None] * 11,
        ['合计', None, '因公出国（境）费', None, '公务用车购置及运行维护费',
         None, None, None, None, None, '公务接待费', None],
        [None, None, None, None, '小计', None, '公务用车购置费', None,
         '公务用车运行维护费', None, None, None],
        ['预算数', '决算数'] * 6,
        ['5', '5', '', '', '5', '5', '', '', '5', '5', budget, actual],
    ]
    return grid_document(rows, code='FIN_07_three_public', headers=4,
                         text='甲局2024年度部门决算。公务接待费支出0万元。')


@pytest.mark.parametrize('actual', ['', '0.00'])
def test_current_native_reception_zero_records_real_source_without_new_fact(actual):
    doc = native_reception(actual)
    before = doc.fact_context.to_dict()
    assert reception_quantity_not_required(RULE, doc, '公务用车决算5万元；公务接待费支出0万元。')
    assert doc.fact_context.to_dict() == before
    rec = doc.fact_receipts[-1]
    assert rec['status'] == 'not_applicable'
    if actual:
        assert len(rec['operand_fact_ids']) == 1 and rec['disclosure_sources'][0]['source_cell_id']
    else:
        assert rec['operand_fact_ids'] == [] and len(rec['cell_observations']) == 1
        assert rec['cell_observations'][0]['indicator'] == 'reception'
        assert rec['cell_observations'][0]['budget_stage'] == '决算'


@pytest.mark.parametrize('obstacle', [
    'positive_actual', 'unknown_cell', 'missing_cell', 'ambiguous', 'duplicate', 'unknown_stage',
    'only_budget_zero', 'only_total_zero', 'stale_version', 'other_year', 'other_owner',
    'other_period', 'duplicate_table', 'text_table', 'identity_conflict', 'no_native',
    'source_hash', 'source_cell', 'fund_scope',
    'missing_version', 'missing_fact_id',
])
def test_unknown_or_incompatible_native_reception_never_uses_nearby_zero(obstacle):
    doc = native_reception('0')
    fact = next(f for f in doc.fact_context.facts if f.indicator == 'reception' and f.budget_stage == '决算')
    table = doc.parse_artifact.table_fragments[0]
    if obstacle == 'positive_actual':
        fact.normalized_value = 2
    elif obstacle in {'unknown_cell', 'missing_cell', 'only_budget_zero', 'only_total_zero'}:
        doc.fact_context.facts.remove(fact)
        cell = next(c for c in table.cells if c.row_idx == 4 and c.col_idx == 11)
        if obstacle == 'unknown_cell':
            cell.raw_text, cell.native_blank_verified = '', False
        elif obstacle == 'missing_cell':
            table.cells.remove(cell)
    elif obstacle == 'ambiguous':
        fact.recognition_status = 'ambiguous'
    elif obstacle == 'duplicate':
        doc.fact_context.facts.append(deepcopy(fact))
    elif obstacle == 'unknown_stage':
        fact.budget_stage = None
    elif obstacle == 'stale_version':
        doc.parse_artifact.parse_run_uid = 'old'
    elif obstacle == 'other_year':
        fact.fiscal_year = 2023
    elif obstacle == 'other_owner':
        fact.org_name = '乙局'
    elif obstacle == 'other_period':
        fact.period = '上年'
    elif obstacle == 'duplicate_table':
        doc.parse_artifact.logical_tables.append(deepcopy(doc.parse_artifact.logical_tables[0]))
    elif obstacle == 'text_table':
        table.extraction_method = 'text'
    elif obstacle == 'identity_conflict':
        doc.parse_artifact.logical_tables[0].identity_conflict = True
    elif obstacle == 'source_hash':
        fact.extra_dims['evidence_refs'][0]['file_hash'] = 'old'
    elif obstacle == 'source_cell':
        fact.extra_dims['evidence_refs'][0]['source_cell_id'] = 'missing'
    elif obstacle == 'fund_scope':
        fact.fund_scope = 'government_fund'
    elif obstacle == 'missing_version':
        doc.fact_context.parse_run_uid = ''
        doc.parse_artifact.parse_run_uid = ''
    elif obstacle == 'missing_fact_id':
        fact.fact_uid = ''
    else:
        doc.parse_artifact = None
    assert not reception_quantity_not_required(RULE, doc, '公务接待费支出0万元。')
    assert not getattr(doc, 'fact_receipts', [])


@pytest.mark.parametrize('scope', [
    '公务接待费支出决算1万元。', '公务接待费支出0万元；国内公务接待支出2万元。',
    '公务接待费支出0万元；接待外宾支出1万元。', '公务接待费金额3万元。',
    '国内公务接待支出3万。',
    '公务接待费支出1,000.00万元。', '公务接待费支出1,234,000万元。',
    '公务接待费支出决算为1.53万元，完成预算的0%。公务接待费支出决算0万元。',
])
def test_positive_or_unclear_stage_reception_claim_blocks_zero_exemption(scope):
    assert not reception_quantity_not_required(RULE, native_reception('0'), scope)


def test_explicit_budget_amount_is_not_the_actual_and_legacy_keeps_existing_rule():
    assert reception_quantity_not_required(RULE, native_reception('0'), '公务接待费年初预算1万元，决算0万元。')
    assert not reception_quantity_not_required(RULE, SimpleNamespace(), '公务接待费本年无发生额，支出0万元。')
