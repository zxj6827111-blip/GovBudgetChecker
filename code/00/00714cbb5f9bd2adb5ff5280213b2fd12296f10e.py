import json
import sys
from decimal import Decimal
from types import SimpleNamespace

from src.engine.common_rules import CMM007_ZeroBaseTrendRecompute
from src.engine.checklist_structure_rules import CMM013_TocHeadingAlignment
from src.engine.rules_v33 import build_document


def trend(text):
    doc = build_document('review.pdf', ['某某局2025年度部门决算公开',
        '三、支出决算情况说明\n' + text], [], 0)
    return [dict(rule=i.rule, message=i.message, location=i.location)
            for i in CMM007_ZeroBaseTrendRecompute().apply(doc)]


def title(body_org):
    org = '上海市事业单位登记管理局'
    pages = [org + '\n2023年度部门决算',
        '目录\n第一部分 ' + org + '概况 ...... 3\n第二部分 ' + org +
        '2023年度部门决算表 ...... 4\n第三部分 ' + org + '2023年度部门决算情况说明 ...... 5',
        '第一部分 ' + org + '概况\n一、主要职能\n负责测试。',
        '第二部分 ' + org + '2023年度部门决算表\n收入支出决算总表\n收入决算表\n支出决算表',
        '第三部分 ' + body_org + '2023年度部门决算情况说明\n一、收入支出决算总体情况说明\n收支总计1.00万元。']
    doc = build_document('review.pdf', pages, [[] for _ in pages], 0)
    doc.report_kind = 'final'
    doc.document_profile = SimpleNamespace(
        organization_name=SimpleNamespace(value=org),
        report_year=SimpleNamespace(value=2023),
        subject_level=SimpleNamespace(value=None), jurisdiction=SimpleNamespace(value=None),
        writing_scope={'status': 'confirmed', 'value': 'department'})
    return [dict(rule=i.rule, message=i.message, location=i.location)
            for i in CMM013_TocHeadingAlignment().apply(doc)]


cases = {
    'R16_decrease_outside_true_rounding_range': trend(
        '公务接待费支出决算1.17万元。\n公务接待费支出决算减少0.31万元，下降21.40%。'),
    'R16_increase_true_target': trend(
        '公务接待费支出决算7.42万元。\n公务接待费支出决算增加6.20万元，增长508.09%。'),
    'R16_one_decimal_outside_frozen_005_range': trend(
        '公务接待费支出决算0.70万元。\n公务接待费支出决算增加0.4万元，增长150%。'),
    'R26_zero_integer_current_small_nonzero': trend(
        '因公出国（境）费支出决算0.50万元。\n因公出国（境）费支出决算减少0万元，下降0%。'),
    'R26_true_zero': trend(
        '因公出国（境）费支出决算0.00万元。\n因公出国（境）费支出决算减少0.00万元，下降0.00%。'),
    'R3_org_loses_unit_word': title('上海市事业登记管理局'),
    'R3_same_org': title('上海市事业单位登记管理局'),
}
current_lo, current_hi = Decimal('1.165'), Decimal('1.175')
delta_lo, delta_hi = Decimal('0.305'), Decimal('0.315')
cases['R16_exact_decrease_interval'] = {
    'low': str(delta_lo / (current_hi + delta_lo) * 100),
    'high': str(delta_hi / (current_lo + delta_hi) * 100),
    'point': str(Decimal('.31') / Decimal('1.48') * 100),
    'claim': '21.40',
}
print(json.dumps(cases, ensure_ascii=False, indent=2, default=str))
