from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine.native_narrative_checks import check_narrative_relations
from src.engine.rules_v33 import R33234_NarrativePercentConsistency
from src.services.evidence_guard import evaluate_finding_evidence
from src.services.rule_source_binding import bind_rule_sources


def run(body, *, owner='甲局', year=2025, heading='一、收入支出决算总体情况说明'):
    cover = f'{owner}\n{year}年度部门决算\n' if year else ''
    doc = SimpleNamespace(page_texts=[cover + heading + '\n' + body], report_kind='final',
                          fiscal_year=year, organization_name=owner)
    issues, unresolved = check_narrative_relations(R33234_NarrativePercentConsistency(), doc)
    return issues, unresolved, doc


def test_yoy_delta_reconstructs_previous_basis_instead_of_using_current_amount():
    issues, unresolved, doc = run(
        '甲局2025年度收入支出总计29,626.54万元。与2024年度相比，收入支出减少2,104.35万元，下降7.1%。')
    assert not unresolved and len(issues) == 1
    location = issues[0].location
    assert location['current_amount_wan'] == '29626.54'
    assert location['previous_amount_wan'] == '31730.89'
    assert abs(Decimal(location['computed_percent']) - Decimal('6.6318656678')) < Decimal('.000000001')
    assert Decimal(location['difference_pp']) > Decimal('.1')
    assert 'current + change' in location['formula']
    assert doc.fact_receipts[-1]['status'] == 'fail'
    assert location['table_refs'][0]['span'] in doc.page_texts[0]


@pytest.mark.parametrize('body', [
    '甲局2025年度收入支出总计90万元。与2024年度相比，收入支出减少10万元，下降10.1%。',
    '甲局2025年度收入支出总计110万元。与2024年度相比，收入支出增加10万元，增长10%。',
    '甲局2025年度收入支出总计29,626.54万元。与2024年度相比，收入支出减少2,104.35万元，下降6.6%。',
    '甲局2025年度收入支出总计900000元。与2024年度相比，收入支出减少100000元，下降10%。',
])
def test_valid_yoy_and_inclusive_percentage_tolerance(body):
    issues, unresolved, doc = run(body)
    assert not issues and not unresolved
    assert doc.fact_receipts[-1]['status'] == 'pass'


@pytest.mark.parametrize('body', [
    '甲局2025年度收入支出总计110万元。与2024年度相比，收入支出增加10万元，增长9.09%。',
    '甲局2025年度收入支出总计90万元。与2024年度相比，收入支出减少10万元，增长10%。',
])
def test_increase_uses_current_minus_change_and_direction_must_agree(body):
    issues, unresolved, _ = run(body)
    assert len(issues) == 1 and not unresolved
    assert issues[0].location['previous_amount_wan'] == '100'


@pytest.mark.parametrize('body', [
    '与2024年度相比，收入支出减少10万元，下降20%。',
    '甲局2025年度收入支出总计100万元，其中项目支出60万元。与2024年度相比，收入支出减少10万元，下降20%。',
    '甲局2025年度收入支出总计100万元。与2024年度相比，本年收入合计减少10万元，下降20%。',
    '乙局2025年度收入支出总计100万元。与2024年度相比，收入支出减少10万元，下降20%。',
    '甲局2024年度收入支出总计100万元。与2023年度相比，收入支出减少10万元，下降20%。',
    '甲局2025年度收入支出总计100万元。与2023年度相比，收入支出减少10万元，下降20%。',
    '甲局2025年度收入支出总计100万元。与2024年度相比，收入支出减少10万元，下降20%，按可比口径复算。',
    '统计口径调整后，甲局2025年度收入支出总计100万元。与2024年度相比，收入支出减少10万元，下降20%。',
    '甲局2025年度收入支出总计100万元。另有项目安排。与2024年度相比，收入支出减少10万元，下降20%。',
    '甲局2025年度本年收入合计100万元、本年收入合计200万元，与2024年度相比，减少10万元，下降20%。',
])
def test_yoy_missing_or_changed_scope_is_unresolved_without_numeric_finding(body):
    issues, unresolved, doc = run(body)
    assert not issues and unresolved
    assert doc.fact_receipts[-1]['status'] == 'insufficient_data'


def test_yoy_cannot_take_current_total_from_previous_section():
    issues, unresolved, _ = run(
        '甲局2025年度收入支出总计100万元。\n二、收入决算情况说明\n'
        '与2024年度相比，收入支出减少10万元，下降20%。')
    assert not issues and unresolved


def test_procurement_is_the_sum_of_exactly_three_same_sentence_categories():
    issues, unresolved, doc = run(
        '甲局2025年度政府采购金额（以合同签订为准）为23,348.87万元，其中：'
        '货物采购金额3,302.67万元、工程采购金额2,154.71万元、服务采购金额20,642.33万元。'
        '面向中小企业预留政府采购项目预算金额14,602.22万元。', heading='（二）政府采购支出情况')
    assert not unresolved and len(issues) == 1
    location = issues[0].location
    assert location['declared_total_wan'] == '23348.87'
    assert location['computed_sum_wan'] == '26099.71'
    assert location['difference_wan'] == '-2750.84'
    assert set(location['parts_wan']) == {'货物', '工程', '服务'}
    assert '14,602.22' not in location['table_refs'][0]['span']
    assert doc.fact_receipts[-1]['status'] == 'fail'


# 2026-10-22 口径决议①：容差 0.01 万元 + 三项显示舍入包络 0.02 万元（含边界）。
@pytest.mark.parametrize('total', ['100', '100.02'])
def test_procurement_amount_tolerance_is_inclusive(total):
    issues, unresolved, doc = run(
        f'甲局2025年度政府采购支出总额{total}万元，其中政府采购服务支出30万元、'
        '政府采购货物支出50万元、政府采购工程支出20万元。', heading='（二）政府采购支出情况')
    assert not issues and not unresolved
    assert doc.fact_receipts[-1]['status'] == 'pass'


@pytest.mark.parametrize('absence', ['无工程采购', '未发生工程采购支出', '没有发生工程采购'])
def test_explicit_procurement_absence_closes_with_original_evidence(absence):
    issues, unresolved, doc = run(
        f'甲局2025年度政府采购金额271.10万元，其中货物采购金额166.85万元、{absence}、'
        '服务采购金额104.25万元。', heading='（二）政府采购支出情况')
    assert not issues and not unresolved
    receipt = doc.fact_receipts[-1]
    assert receipt['status'] == 'pass'
    assert any(absence in ref['span'] for ref in receipt['disclosure_sources'])
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1] for ref in receipt['disclosure_sources'])


@pytest.mark.parametrize('absence', [
    '无工程采购预算', '无工程采购数据', '无工程采购、工程采购金额0万元',
    '2024年度无工程采购', '乙局无工程采购', '不含工程采购', '无工程采购。',
    '暂无工程采购', '并非无工程采购', '未核实是否无工程采购',
])
def test_missing_ambiguous_or_foreign_procurement_absence_is_not_zero(absence):
    issues, unresolved, _ = run(
        f'甲局2025年度政府采购金额271.10万元，其中货物采购金额166.85万元、{absence}、'
        '服务采购金额104.25万元。', heading='（二）政府采购支出情况')
    assert not issues and unresolved


def test_explicit_absence_does_not_hide_a_numeric_procurement_difference():
    issues, unresolved, _ = run(
        '甲局2025年度政府采购金额280万元，其中货物采购金额166.85万元、无工程采购、'
        '服务采购金额104.25万元。', heading='（二）政府采购支出情况')
    assert not unresolved and len(issues) == 1
    assert issues[0].location['computed_sum_wan'] == '271.10'
    assert issues[0].location['explicit_absence_terms'] == ['工程']


@pytest.mark.parametrize('body', [
    '甲局2025年度政府采购总额100万元，其中货物采购金额50万元、工程采购金额20万元。',
    '甲局2025年度政府采购总额100万元，其中货物采购金额50万元。工程采购金额20万元、服务采购金额40万元。',
    '甲局2025年度政府采购总额100万元，其中货物采购金额50万元、其中进口货物20万元、工程采购金额20万元、服务采购金额40万元。',
    '甲局2025年度政府采购总额100万元，其中货物采购金额50万元、工程采购金额20万元、服务采购金额40万元、服务采购金额10万元。',
    '乙局2025年度政府采购总额100万元，其中货物采购金额50万元、工程采购金额20万元、服务采购金额40万元。',
    '甲局2024年度政府采购总额100万元，其中货物采购金额50万元、工程采购金额20万元、服务采购金额40万元。',
])
def test_procurement_incomplete_nested_or_foreign_parts_are_not_confirmed(body):
    issues, unresolved, _ = run(body, heading='（二）政府采购支出情况')
    assert not issues and unresolved


@pytest.mark.parametrize('body', [
    '甲局2025年度服务采购总额100万元，工程采购金额50万元，货物采购金额60万元。',
    '甲局2025年度收入总额100万元，其中货物金额50万元、工程金额20万元、服务金额40万元。',
    '甲局2025年度政府采购总额100万元。某项目货物采购金额50万元、工程采购金额20万元、服务采购金额40万元。',
    '公务接待总额45.57万元，国内公务接待44.72万元，其中外宾接待0.85万元。',
])
def test_single_category_non_procurement_or_reception_relation_is_excluded(body):
    issues, _, _ = run(body, heading='（二）其他相关情况说明')
    assert not issues


def test_unknown_material_year_is_unresolved():
    issues, unresolved, _ = run(
        '本年收入合计100万元。与上年相比，减少10万元，下降20%。', year=None)
    assert not issues and unresolved


def test_cross_page_soft_line_breaks_keep_each_original_page_quote():
    doc = SimpleNamespace(page_texts=[
        '甲局\n2025年度部门决算\n一、收入支出决算总体情况说明\n'
        '甲局2025年度收入支出总计29,626.54万元。与2024年度相比，收入支出减少',
        '2,104.35万元，下降7.1%。'], report_kind='final', fiscal_year=2025, organization_name='甲局')
    issues, unresolved = check_narrative_relations(R33234_NarrativePercentConsistency(), doc)
    assert not unresolved and len(issues) == 1
    refs = issues[0].location['table_refs']
    assert [ref['page'] for ref in refs] == [1, 2]
    assert all(ref['span'] in doc.page_texts[ref['page'] - 1] for ref in refs)
    item = {'id': 'probe', 'source': 'rule', 'rule_id': 'V33-234', 'severity': 'high',
            'location': issues[0].location, 'evidence': [{'page': 1, 'text': issues[0].evidence_text}]}
    assert bind_rule_sources({'rule_findings': [item]}, page_texts=doc.page_texts,
                             source_file_hash='a' * 64)['bound_rule_findings'] == 1
    assert evaluate_finding_evidence(item, page_texts=doc.page_texts, document_page_count=2,
                                    source_file_hash='a' * 64)[0]


@pytest.mark.parametrize(('filename', 'page', 'kind', 'expected_count'), [
    ('上海市信访办公室.pdf', 17, 'narrative_yoy_delta', 3),
    ('上海市生态环境局.pdf', 38, 'procurement_total', 1),
])
def test_original_pdf_plain_text_replay(filename, page, kind, expected_count):
    path = Path('/Users/zhangxingjin/Desktop/决算测试上海市') / filename
    if not path.exists():
        pytest.skip('原始真实PDF未在该电脑提供；合成边界测试仍执行')
    pdfplumber = pytest.importorskip('pdfplumber')
    with pdfplumber.open(path) as pdf:
        page_texts = [original.extract_text() or '' for original in pdf.pages]
    doc = SimpleNamespace(page_texts=page_texts, report_kind='final')
    issues, _ = check_narrative_relations(R33234_NarrativePercentConsistency(), doc)
    found = [issue for issue in issues if issue.location['page'] == page and issue.location['relation_type'] == kind]
    assert len(found) == expected_count
    for issue in found:
        assert all(ref['span'] in page_texts[ref['page'] - 1] for ref in issue.location['table_refs'])
    if kind == 'procurement_total':
        assert found[0].location['computed_sum_wan'] == '26099.71'
        assert found[0].location['difference_wan'] == '-2750.84'
    else:
        assert [Decimal(issue.location['computed_percent']).quantize(Decimal('.001')) for issue in found] == [
            Decimal('6.632'), Decimal('6.632'), Decimal('6.649')]
