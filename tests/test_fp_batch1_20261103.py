"""误报整改第一批（2026-11-03）：六类已裁定口径的正反用例。

每类至少一正一反：
- D2-正文第二部分 / D2-2022三公表名：照当年官方模板写的不再报；真缺陷（单位决算
  缺“第二部分”、2024 年仍写“一般公共预算”）仍然报；
- R3-标题措辞：只差“情况/说明/部门/单位”不报；多字、错字仍报；
- R26-零比零：0/0 写 0% 不报；分子不为 0 仍报；
- R16-舍入区间：正文值落在“本年数、增减额各 ±显示半步长”的复算区间内不报；
  区间外仍报；
- R30-泛泛原因：写了一句原因（含“根据年度实际情况”“较预算略有结余”）不报；
  完全没写原因仍报。
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.annual_template_facts import (
    body_writes_part_number_2,
    final_template_facts,
    normative_three_public_table_name,
)
from src.engine.checklist_content_rules import CMM021_TableTitleNormative
from src.engine.checklist_structure_rules import CMM013_TocHeadingAlignment
from src.engine.common_rules import CMM007_ZeroBaseTrendRecompute
from src.engine.fact_context import FactContext
from src.engine.native_narrative_quality import check_quality
from src.engine.rules_v33 import (
    R33234_NarrativePercentConsistency,
    R33TrendCompletionRate,
    build_document,
)
from src.schemas.parse_artifact import FiscalFact


def _field(value):
    return SimpleNamespace(value=value)


def _final_doc(pages, *, year=2023, scope="department", org="上海市测试局"):
    """构造带已确认部门/单位口径的决算文档。"""
    doc = build_document("final.pdf", pages, [[] for _ in pages], 0)
    doc.report_kind = "final"
    doc.document_profile = SimpleNamespace(
        organization_name=_field(org),
        report_year=_field(year),
        subject_level=_field(None),
        jurisdiction=_field(None),
        writing_scope={"status": "confirmed", "value": scope},
    )
    return doc


def _toc_final_doc(*, year=2023, scope="department", body_part2="", body_part3=None):
    """目录含第一/二/三部分，第二、三部分正文标题可控。"""
    titles = {"2022": "2022", "2023": "2023", "2024": "2024", "2025": "2025"}
    y = titles.get(str(year), str(year))
    toc = (
        "目 录\n"
        f"第一部分 上海市测试局概况 ...... 3\n"
        f"第二部分 上海市测试局 {y} 年度部门决算表 ...... 4\n"
        f"第三部分 上海市测试局 {y} 年度部门决算情况说明 ...... 5"
    )
    part2 = body_part2 or f"上海市测试局{y}年度部门决算表"
    part3 = body_part3 or f"第三部分 上海市测试局{y}年度部门决算情况说明"
    pages = [
        f"上海市测试局\n{y} 年度部门决算",
        toc,
        "第一部分 上海市测试局概况\n一、主要职能\n负责测试。",
        part2 + "\n收入支出决算总表\n收入决算表\n支出决算表",
        part3 + "\n一、收入支出决算总体情况说明\n收支总计1.00万元。",
    ]
    return _final_doc(pages, year=year, scope=scope)


def _final_table_doc(title, *, year, scope):
    pages = ["封面", f"{title}\n单位：万元\n合计 1.00"]
    return build_document("table.pdf", pages, [[], [["合计", "1.00"]]], 0), year, scope


# ---------------------------------------------------------------------------
# 年度模板事实数据文件
# ---------------------------------------------------------------------------


def test_annual_template_facts_cover_modeled_years_and_2025_alias():
    assert final_template_facts(2021, "department") is None  # 2021 及以前不改变现有行为
    assert final_template_facts(2023, None) is None
    assert body_writes_part_number_2(2022, "department") is True
    assert body_writes_part_number_2(2023, "department") is False
    assert body_writes_part_number_2(2023, "unit") is True
    # 2025 及以后没有新模板，按 2024 处理
    assert body_writes_part_number_2(2025, "department") is False
    assert body_writes_part_number_2(2026, "department") is False
    assert normative_three_public_table_name(2022, "department") == "一般公共预算财政拨款三公经费支出决算表"
    assert normative_three_public_table_name(2023, "unit") == "财政拨款三公经费支出决算表"


# ---------------------------------------------------------------------------
# D2-正文第二部分（CMM-013）
# ---------------------------------------------------------------------------


def test_d2_department_body_without_part_two_is_template_not_defect():
    issues = CMM013_TocHeadingAlignment().apply(_toc_final_doc(year=2023, scope="department"))
    assert not any("正文标题缺少“第二部分”" in i.message for i in issues)


@pytest.mark.parametrize("year", [2023, 2024, 2025])
def test_d2_unit_final_still_requires_part_two(year):
    issues = CMM013_TocHeadingAlignment().apply(_toc_final_doc(year=year, scope="unit"))
    assert any("正文标题缺少“第二部分”" in i.message for i in issues)


def test_d2_2022_department_still_requires_part_two():
    issues = CMM013_TocHeadingAlignment().apply(_toc_final_doc(year=2022, scope="department"))
    assert any("正文标题缺少“第二部分”" in i.message for i in issues)


def test_d2_numbered_body_is_clean():
    issues = CMM013_TocHeadingAlignment().apply(
        _toc_final_doc(year=2023, scope="department", body_part2="第二部分 上海市测试局2023年度部门决算表"))
    assert not any("第二部分" in i.message for i in issues)


# ---------------------------------------------------------------------------
# R3-标题措辞（CMM-013）
# ---------------------------------------------------------------------------


def test_r3_template_wording_difference_is_not_a_title_mismatch():
    # 目录“部门决算情况说明” vs 正文“部门决算说明”（只差“情况”）
    issues = CMM013_TocHeadingAlignment().apply(
        _toc_final_doc(body_part3="第三部分 上海市测试局2023年度部门决算说明"))
    assert not issues


def test_r3_department_word_insertion_is_not_a_title_mismatch():
    # 目录“年度决算表” vs 正文“年度部门决算表”（只差“部门”）
    issues = CMM013_TocHeadingAlignment().apply(
        _toc_final_doc(body_part2="第二部分 上海市测试局2023年度部门决算表"))
    assert not issues


@pytest.mark.parametrize("body", [
    "第三部分 上海市测试局局2023年度部门决算情况说明一",  # 多字 + 尾字
    "第三部分 上海市测试局 202 年度部门决算情况说明",  # 年度笔误
])
def test_r3_real_title_defects_still_reported(body):
    issues = CMM013_TocHeadingAlignment().apply(_toc_final_doc(body_part3=body))
    assert any("目录与正文标题不一致" in i.message for i in issues)


def _title_with_org(body_org: str, org: str = "上海市事业单位登记管理局"):
    pages = [
        f"{org}\n2023年度部门决算",
        f"目录\n第一部分 {org}概况 ...... 3\n第二部分 {org}2023年度部门决算表 ...... 4\n"
        f"第三部分 {org}2023年度部门决算情况说明 ...... 5",
        f"第一部分 {org}概况\n一、主要职能\n负责测试。",
        f"第二部分 {org}2023年度部门决算表\n收入支出决算总表\n收入决算表\n支出决算表",
        f"第三部分 {body_org}2023年度部门决算情况说明\n一、收入支出决算总体情况说明\n收支总计1.00万元。",
    ]
    return _final_doc(pages, year=2023, scope="department", org=org)


def test_r3_organization_name_internal_unit_word_is_not_template_wording():
    # 组织名称内部的“单位”是实质内容：事业单位登记管理局 → 事业登记管理局 必须报
    issues = CMM013_TocHeadingAlignment().apply(_title_with_org("上海市事业登记管理局"))
    assert any("目录与正文标题不一致" in i.message for i in issues)


def test_r3_same_organization_name_is_clean():
    assert CMM013_TocHeadingAlignment().apply(_title_with_org("上海市事业单位登记管理局")) == []


# ---------------------------------------------------------------------------
# D2-2022 三公表名（CMM-021）
# ---------------------------------------------------------------------------


def _apply_table_rule(title, *, year, scope):
    doc, year, scope = _final_table_doc(title, year=year, scope=scope)
    doc.report_kind = "final"
    doc.document_profile = SimpleNamespace(
        organization_name=_field("上海市测试局"),
        report_year=_field(year),
        subject_level=_field(None),
        jurisdiction=_field(None),
        writing_scope={"status": "confirmed", "value": scope},
    )
    return CMM021_TableTitleNormative().apply(doc)


def test_d2_2022_official_three_public_table_name_is_accepted():
    assert _apply_table_rule("一般公共预算财政拨款“三公”经费支出决算表", year=2022, scope="department") == []


def test_d2_2024_old_three_public_table_name_is_still_a_defect():
    issues = _apply_table_rule("一般公共预算财政拨款“三公”经费支出决算表", year=2024, scope="department")
    assert issues and "财政拨款三公经费支出决算表" in issues[0].message


def test_d2_2023_short_three_public_table_name_is_accepted():
    assert _apply_table_rule("财政拨款“三公”经费支出决算表", year=2023, scope="unit") == []


# ---------------------------------------------------------------------------
# R26-零比零（CMM-007 与三公完成率）
# ---------------------------------------------------------------------------


def _cmm007(section: str):
    doc = build_document(
        "cmm007.pdf",
        ["某某局 2025 年度部门决算公开", "三、支出决算情况说明\n" + section],
        [],
        0,
    )
    return CMM007_ZeroBaseTrendRecompute().apply(doc)


def test_r26_zero_over_zero_declared_zero_is_not_reported():
    issues = _cmm007("因公出国（境）费支出决算 0 万元。\n"
                     "因公出国（境）费支出决算减少 0 万元，下降 0%。")
    assert issues == []


def test_r26_zero_base_with_nonzero_numerator_is_still_reported():
    issues = _cmm007("因公出国（境）费支出决算 66.48 万元。\n"
                     "因公出国（境）费支出决算增加 66.48 万元，增长 100%。")
    assert len(issues) == 1 and issues[0].location["zero_base"] is True


def _three_public_doc(body, *, budget="0", actual="0"):
    doc = build_document("completion.pdf", ["甲局2025年度部门决算",
        "七、财政拨款“三公”经费支出决算情况说明\n" + body], [], 0)
    facts = []
    for indicator, stage, value in (("vehicle_purchase_operation", "年初预算", budget),
                                    ("vehicle_purchase_operation", "决算", actual),
                                    ("total_actual", "决算", "0")):
        facts.append(FiscalFact(fact_uid=f"{indicator}:{stage}", indicator=indicator,
            raw_value=value, unit="万元", normalized_value=Decimal(value), org_name="甲局",
            fiscal_year=2025, period="本年", budget_stage=stage, subject_code="total",
            subject_name="合计", table_code="FIN_07_three_public", instance_uid="table",
            extra_dims={"evidence_refs": [{"physical_page": 1}]}))
    doc.fact_context = FactContext(facts, "a" * 64, "run", org_name="甲局", fiscal_year=2025)
    doc.parse_artifact = SimpleNamespace(source_file_hash="a" * 64, parse_run_uid="run")
    return doc


def test_r26_three_public_zero_over_zero_declared_zero_is_not_a_finding():
    doc = _three_public_doc("公务用车购置及运行维护费支出决算0万元，完成年初预算的0.00%。")
    assert R33TrendCompletionRate().apply(doc) == []


def test_r26_three_public_zero_base_with_nonzero_numerator_is_still_a_finding():
    doc = _three_public_doc("公务用车购置及运行维护费支出决算0.16万元，完成年初预算的100.00%。",
                            actual="0.16")
    findings = R33TrendCompletionRate().apply(doc)
    assert len(findings) == 1 and "没有数学定义" in findings[0].message


# ---------------------------------------------------------------------------
# R16-舍入区间（CMM-007）
# ---------------------------------------------------------------------------


def test_r16_declared_value_inside_rounding_interval_is_not_reported():
    # 7.42 − 6.20 = 1.22；复算 508.20%，声明 508.09% 落在 ±0.005 区间内
    issues = _cmm007("公务接待费支出决算 7.42 万元。\n"
                     "公务接待费支出决算增加 6.20 万元，增长 508.09%。")
    assert issues == []


def test_r16_declared_value_outside_rounding_interval_is_still_reported():
    issues = _cmm007("公务接待费支出决算 7.42 万元。\n"
                     "公务接待费支出决算增加 6.20 万元，增长 520%。")
    assert len(issues) == 1 and "复算不一致" in issues[0].message


def test_r16_decrease_uses_implementable_endpoints_and_reports_2140():
    # 下降率 = D/(C+D)，区间 20.608108%–21.283784%（C=1.17±0.005，D=0.31±0.005）
    issues = _cmm007("公务接待费支出决算 1.17 万元。\n"
                     "公务接待费支出决算减少 0.31 万元，下降 21.40%。")
    assert len(issues) == 1 and "复算不一致" in issues[0].message


def test_r16_decrease_inside_true_interval_is_not_reported():
    issues = _cmm007("公务接待费支出决算 1.17 万元。\n"
                     "公务接待费支出决算减少 0.31 万元，下降 20.78%。")
    assert issues == []


def test_r16_one_decimal_amount_still_uses_frozen_half_unit_0005():
    # 冻结口径为两数各 ±0.005，不因增减额原文一位小数改用 ±0.05
    issues = _cmm007("公务接待费支出决算 0.70 万元。\n"
                     "公务接待费支出决算增加 0.4 万元，增长 150%。")
    assert len(issues) == 1 and "复算不一致" in issues[0].message


# ---------------------------------------------------------------------------
# R30-泛泛原因（V33-234）
# ---------------------------------------------------------------------------


def _completion_doc(tail: str):
    return build_document("v33234.pdf", ["甲局2025年度部门决算",
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "1、社会保障和就业支出\n"
        "年初预算为1154.88万元，支出决算为1128.16万元，完成年初预算的97.69%，" + tail],
        [], 0)


@pytest.mark.parametrize("tail", [
    "支出较预算略有结余。",
    "支出\n较预算略有结余。",  # PDF 软换行
    "经费支出有所减少。",
    "根据年度实际情况，经费支出有所减少。",
])
def test_r30_any_stated_reason_suppresses_missing_reason(tail):
    issues = R33234_NarrativePercentConsistency().apply(_completion_doc(tail))
    assert not any("未说明原因" in i.message for i in issues)


def test_r30_no_reason_at_all_is_still_reported():
    issues = R33234_NarrativePercentConsistency().apply(_completion_doc(""))
    assert any("未说明原因" in i.message for i in issues)


def test_r30_result_only_cause_is_not_a_formal_issue_anymore():
    doc = build_document("quality.pdf", ["甲局2025年度部门决算",
        "五、一般公共预算财政拨款支出决算情况说明\n"
        "年初预算为234430.42万元，支出决算为233564.77万元。"
        "决算数小于预算数的主要原因是决算数与预算数基本持平。"], [], 0)
    assert check_quality(R33234_NarrativePercentConsistency(), doc) == []
