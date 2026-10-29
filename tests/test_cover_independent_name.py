"""CMM-019 封面名称的正文独立依据：折行残片、“本级”标注和并列名称都不能硬选。"""
import pytest

from src.engine.checklist_identity_rules import CMM019_CoverElements
from src.engine.rule_outcome import RuleDeferred
from src.services.template_scope import HumanDecision
from tests.test_material_writing_rules import document, resolution


def run(pages):
    doc = document(pages, resolution(pages, HumanDecision("unit", "test-only", True)))
    try:
        return CMM019_CoverElements().apply(doc), []
    except RuleDeferred as caught:
        return caught.partial_issues, caught.unresolved_reasons


def test_wrapped_name_fragment_is_not_an_independent_name():
    # 2024 真实样本：正文标题把带括号的全称折成两行，只剩“投促中心）”一类残片。
    pages = ["上海市甲服务中心\n2024年度单位决算",
             "目录\n第一部分 上海市甲服务中心（上海市乙\n促进中心）概况\n第二部分 上海市甲服务中心（上海市乙\n促进中心）2024年度决算表",
             "第二部分 上海市甲服务中心（上海市乙\n促进中心）2024年度决算表\n本表反映单位本年度收入。"]
    issues, reasons = run(pages)
    assert not any("促进中心）" in issue.message for issue in issues)
    assert "封面单位名称缺少正文独立依据" in reasons


def test_bare_self_level_suffix_is_not_a_different_name():
    # 2024 真实样本：目录写“××本级”，正文写全称，封面写全称不应报不一致。
    pages = ["上海市甲办公室\n2024年度单位决算",
             "目录\n第一部分 上海市甲办公室本级概况\n第二部分 上海市甲办公室本级2024年度决算表",
             "第一部分 上海市甲办公室概况\n一、主要职能",
             "上海市甲办公室2024年度单位决算表\n本表反映单位本年度收入。"]
    issues, reasons = run(pages)
    assert not any("单位名称" in issue.message or "单位全称" in issue.message for issue in issues)
    assert not reasons


@pytest.mark.parametrize("second", ["上海市乙办公室", "上海市甲办公室分部"])
def test_tied_different_names_leave_the_name_subcheck_incomplete(second):
    pages = ["上海市甲办公室\n2024年度单位决算",
             f"第一部分 上海市甲办公室概况\n第一部分 {second}概况",
             f"上海市甲办公室2024年度单位决算表\n{second}2024年度单位决算表"]
    issues, reasons = run(pages)
    assert not any("单位名称" in issue.message or "单位全称" in issue.message for issue in issues)
    assert "封面单位名称缺少正文独立依据" in reasons
