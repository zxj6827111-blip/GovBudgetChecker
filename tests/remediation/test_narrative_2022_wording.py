"""上海 2022 版说明文字的取数回归（2026-10-23）。

2022 版与 2023 版的写法差异：三公章节标题带“一般公共预算”、条目用“类-款-项”连接号、
“本年支出 X 万元”不带“合计”、占比写成整数、政府采购只列已发生的分项、公务用车只写
运行维护费。修复前这些材料的 V33-222/227/104/234/249、CMM-001 大量转“取数不足”。
"""

from decimal import Decimal

import pytest

from src.engine.common_rules import CMM001_ThreePublicNarrativeConsistency
from src.engine.native_final_disclosure import (
    GENERAL, _DASH_ITEM, check_final_expenditure_shares, check_function_disclosures,
)
from src.engine.native_table_text import is_general_expenditure_title
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import _FUNCTIONAL_NARRATIVE_DASH_RE, _resolve_indicator_percentage
from tests.remediation.test_native_blank_cells import three_public
from tests.remediation.test_native_narrative_checks import run
from tests.remediation.test_real_intake_disclosure_numeric import DisclosureRule, document, shares

MEDICAL = [(GENERAL, '210', '卫生健康支出', 'total_actual', '544.49'),
           (GENERAL, '21011', '行政事业单位医疗', 'total_actual', '544.49'),
           (GENERAL, '2101102', '事业单位医疗', 'total_actual', '544.49'),
           (GENERAL, 'total', '合计', 'total_actual', '544.49')]


# ---------------------------------------------------------------------------
# 说明五的章节定位：2022 版三公章节标题也带“一般公共预算”
# ---------------------------------------------------------------------------


def test_three_public_and_basic_titles_are_not_section_five():
    assert is_general_expenditure_title('五、一般公共预算财政拨款支出决算情况说明')
    assert not is_general_expenditure_title('七、一般公共预算财政拨款“三公”经费支出决算情况说明')
    assert not is_general_expenditure_title('六、一般公共预算财政拨款基本支出决算情况说明')


def test_section_five_is_read_even_when_the_three_public_title_follows_it():
    doc = document('一般公共预算财政拨款支出544.49万元。\n'
                   '1、卫生健康支出（类）行政事业单位医疗（款）事业单位医疗（项）。支出决算为544.49万元。\n'
                   '七、一般公共预算财政拨款“三公”经费支出决算情况说明\n'
                   '“三公”经费财政拨款支出年初预算为10万元，支出决算为8万元。', MEDICAL)
    assert check_function_disclosures(DisclosureRule(), doc) == []
    assert {row['status'] for row in doc.fact_receipts} == {'pass'}


# ---------------------------------------------------------------------------
# “类-款-项”连接号写法
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('text, expected', [
    ('其中：1、“社会保障和就业支出-行政事业单位养老支出-行政单位离退休”。主要用于：',
     ('社会保障和就业支出', '行政事业单位养老支出', '行政单位离退休')),
    ('1、社会保障和就业支出—行政事业单位养老支出—行政单位离退休。主要用于',
     ('社会保障和就业支出', '行政事业单位养老支出', '行政单位离退休')),
    ('执行。3“社会保障和就业支出-行政事业单位养老支出-机关事业单位基本养老保险缴费支出”。主要用于',
     ('社会保障和就业支出', '行政事业单位养老支出', '机关事业单位基本养老保险缴费支出')),
])
def test_dash_separated_items_are_recognised(text, expected):
    match = _DASH_ITEM.search(text)
    assert match and (match['klass'], match['kuan'], match['xiang']) == expected
    raw = _FUNCTIONAL_NARRATIVE_DASH_RE.search(text.replace('其中：', '\n').replace('执行。', '\n'))
    assert raw and raw['item_name'] == expected[2]


def test_dates_and_ranges_are_not_dash_items():
    assert _DASH_ITEM.search('2022-2023年度。') is None
    assert _FUNCTIONAL_NARRATIVE_DASH_RE.search('\n2022-2023-2024年。') is None


@pytest.mark.parametrize('amount, failed', [('544.49', False), ('545.49', True)])
def test_dash_item_amount_is_compared_with_its_table_row(amount, failed):
    doc = document('一般公共预算财政拨款支出544.49万元。\n'
                   f'1、“卫生健康支出-行政事业单位医疗-事业单位医疗”。主要用于：医疗。支出决算为{amount}万元。',
                   MEDICAL)
    issues = check_function_disclosures(DisclosureRule(), doc)
    assert bool(issues) == failed
    assert doc.fact_receipts[-1]['status'] == ('fail' if failed else 'pass')


# ---------------------------------------------------------------------------
# “本年支出 X 万元”不带“合计”
# ---------------------------------------------------------------------------


def test_bare_current_year_total_is_an_indicator_not_an_unknown_owner():
    claim = _resolve_indicator_percentage(
        '三、支出决算情况说明本年支出154,417.43万元，其中：基本支出50,676.42万元，占32.82%',
        ('基本支出',), ('本年支出合计', '本年支出', '支出合计'),
        organization_name='甲局', owner_aliases=[], report_year=2022,
        report_kind='final', reference_year=2022)
    assert claim.status == 'ok' and claim.value == Decimal('32.82')


# ---------------------------------------------------------------------------
# 占比写成整数：容差取原文末位半个单位
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('declared, failed', [('1', False), ('0.8', False), ('2', True)])
def test_share_written_with_fewer_decimals_uses_its_display_precision(declared, failed):
    doc = shares(f'社会保障和就业支出18,553.22万元，占{declared}%。')  # 实际 0.8447%
    issues = check_final_expenditure_shares(DisclosureRule(), doc)
    assert bool(issues) == failed


# ---------------------------------------------------------------------------
# 政府采购只列已发生的分项
# ---------------------------------------------------------------------------


def test_procurement_with_an_unmentioned_category_closes_when_listed_parts_equal_total():
    issues, unresolved, doc = run(
        '甲局2025年度政府采购金额（以合同签订为准）为220.11万元，其中：货物采购金额14.07万元、'
        '服务采购金额206.04万元。', heading='（二）政府采购支出情况')
    assert not issues and not unresolved
    assert doc.fact_receipts[-1]['status'] == 'pass'
    assert '工程' in doc.fact_receipts[-1]['formula']


def test_listed_procurement_parts_above_the_total_are_an_error():
    issues, unresolved, _ = run(
        '甲局2025年度政府采购金额220.11万元，其中：货物采购金额20.00万元、服务采购金额206.04万元。',
        heading='（二）政府采购支出情况')
    assert not unresolved and len(issues) == 1
    assert issues[0].location['omitted_terms'] == ['工程']


def test_listed_procurement_parts_below_the_total_stay_unresolved():
    issues, unresolved, _ = run(
        '甲局2025年度政府采购金额220.11万元，其中：货物采购金额10.00万元、服务采购金额200.00万元。',
        heading='（二）政府采购支出情况')
    assert not issues and unresolved


# ---------------------------------------------------------------------------
# 公务用车只写运行维护费
# ---------------------------------------------------------------------------


def test_unmentioned_vehicle_purchase_follows_from_subtotal_minus_operation():
    doc = three_public(absence='')
    assert CMM001_ThreePublicNarrativeConsistency().apply(doc) == []
    assert any(row['formula'].startswith('omitted vehicle detail') for row in doc.fact_receipts)


def test_deduced_vehicle_purchase_must_still_match_the_table():
    with pytest.raises(RuleDeferred):
        CMM001_ThreePublicNarrativeConsistency().apply(three_public(purchase='1.00', absence=''))


# ---------------------------------------------------------------------------
# V33-251：补零编码的类/款行不是项级科目
# ---------------------------------------------------------------------------


def test_padded_class_and_section_rows_are_not_counted_as_listed_items():
    from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness

    doc = document(
        '（三）一般公共预算财政拨款支出决算具体情况\n'
        '一般公共预算财政拨款支出年初预算为100万元，支出决算为100万元。其中：'
        '1、社会保障和就业支出（类）行政事业单位养老支出（款）机关事业单位基本养老保险缴费支出（项）。'
        '年初预算为60万元，支出决算为60万元。\n六、一般公共预算财政拨款基本支出决算情况说明',
        [(GENERAL, '2080000', '社会保障和就业支出', 'total_actual', '100'),
         (GENERAL, '2080500', '行政事业单位养老支出', 'total_actual', '100'),
         (GENERAL, '2080505', '机关事业单位基本养老保险缴费支出', 'total_actual', '60'),
         (GENERAL, '2080506', '机关事业单位职业年金缴费支出', 'total_actual', '40'),
         (GENERAL, 'total', '合计', 'total_actual', '100')])
    messages = [issue.message for issue in R33251_FunctionalDetailCompleteness().apply(doc)]
    assert any('决算表项级科目2个，说明只列1项' in m and '职业年金' in m for m in messages)


# ---------------------------------------------------------------------------
# 主体名带全角括号
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('owner', ['中国（上海）自由贸易试验区临港新片区管理委员会',
                                   '中国自由贸易试验区临港新片区管理委员会'])
def test_owner_with_fullwidth_parentheses_recognises_itself(owner):
    from src.engine.native_narrative_checks import _foreign_owner

    sentence = '中国(上海)自由贸易试验区临港新片区管理委员会2023年度财政拨款收入支出总计14,634.42万元'
    assert not _foreign_owner(sentence, owner)
    assert _foreign_owner('乙局2023年度财政拨款收入支出总计100万元', owner)
    assert not _foreign_owner('上海市水务局(上海市海洋局)2023年度收入', '上海市水务局（上海市海洋局）')


# ---------------------------------------------------------------------------
# 章节标题软换行；三公只漏写一项；V33-236 无可查表述
# ---------------------------------------------------------------------------


def test_wrapped_section_heading_is_still_a_section():
    from src.engine.native_table_text import sections
    from src.engine.rules_v33 import build_document

    doc = build_document('final.pdf', [
        '甲局\n2022年度决算',
        '六、一般公共预算财政拨款基本支出决算情况说明\n基本支出100万元。\n'
        '七、一般公共预算财政拨款“三公”经费支出决算情况说\n明\n“三公”经费财政拨款支出决算为8万元。\n'
        '八、政府性基金预算财政拨款收入支出决算情况说明\n甲局2022年度无政府性基金。'], [], 0)
    text, ranges = sections(doc, lambda title: '三公' in title and '决算' in title)
    assert ranges and '支出决算为8万元' in text.compact[ranges[0][1]:ranges[0][2]]


def _three_public_text(body):
    from tests.remediation.test_native_blank_cells import grid_document

    rows = [['财政拨款“三公”经费'] + [None] * 11,
            ['合计', None, '因公出国（境）费', None, '公务用车购置及运行维护费'] + [None] * 5 + ['公务接待费', None],
            [None] * 4 + ['小计', None, '公务用车购置费', None, '公务用车运行维护费', None, None, None],
            ['预算数', '决算数'] * 6,
            ['10.00', '8.00', '0.00', '', '5.00', '5.00', '', '', '5.00', '5.00', '3.00', '3.00']]
    text = ('甲局2024年度部门决算\n七、财政拨款“三公”经费支出决算情况说明\n'
            '“三公”经费年初预算为10.00万元，支出决算为8.00万元。\n（二）具体情况说明\n' + body)
    return grid_document(rows, code='FIN_07_three_public', headers=4, text=text)


def test_single_unmentioned_three_public_item_follows_from_the_stated_total():
    doc = _three_public_text('受疫情影响无因公出国（境）团组。公务用车购置及运行维护费支出5.00万元。'
                             '无公务用车购置支出。公务用车运行维护费支出5.00万元。公务接待费支出3.00万元。')
    assert CMM001_ThreePublicNarrativeConsistency().apply(doc) == []
    assert any(row['formula'].startswith('omitted three-public item') for row in doc.fact_receipts)


def test_two_unmentioned_three_public_items_stay_for_manual_review():
    doc = _three_public_text('公务用车购置及运行维护费支出5.00万元。无公务用车购置支出。'
                             '公务用车运行维护费支出5.00万元。')
    with pytest.raises(RuleDeferred):
        CMM001_ThreePublicNarrativeConsistency().apply(doc)


def test_scope_wording_rule_has_nothing_to_check_without_either_phrase():
    from src.engine.rules_v33 import R33236_DocumentScopeTerminology, build_document

    doc = build_document('甲中心2022年度决算.pdf', ['甲中心\n2022年度决算', '收入支出决算总体情况说明'], [], 0)
    doc.report_kind = 'final'
    assert R33236_DocumentScopeTerminology().apply(doc) == []
    unit = build_document('甲中心2022年度决算.pdf', ['甲中心\n2022年度决算',
                          '第二部分 甲中心2022年度决算表\n第三部分 甲中心2022年度决算情况说明',
                          '一、主要职能', '（三）其他\n本年部门决算安排如下'], [], 0)
    unit.report_kind = 'final'
    issues = R33236_DocumentScopeTerminology().apply(unit)
    assert issues and '单位决算安排' in issues[0].message
