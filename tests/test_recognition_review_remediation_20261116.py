"""2026-11-16 识别、事实绑定与规则取数回归。

真实原件对照（不作为测试运行时依赖）：
- 34f7645b（上海市民族和宗教事务局 2024 决算）第 8 页 FIN_02_income：
  2013499 行各金额格为认证空白；横向 total=0 vs 各来源 0，纵向
  20134 = 2013404 + 2013499。修复前 total_actual 不接受空白项，
  OBL-INTERNAL-T2-TOTAL 记 insufficient_data；修复后两条关系均执行。
- d835cf2a（市政府办公厅）第 3 页目录有“基本支出”，第 22 页正文标题漏“基”。
- 05e40add（合作交流办）第 13 页合计行写作“合 计”。
- 0ed5318c（市教委）第 32 页“28,146.97 万万元”。
- edb56827（工商外国语学校）第 18—19 页多个养老项共享“其中：”分项金额。
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.engine.checklist_final_rules import R33251_FunctionalDetailCompleteness
from src.engine.checklist_text_rules import CMM011_AmountUnitWan
from src.engine.fact_context import build_fact_context_from_artifact
from src.engine.native_issue_dedup import deduplicate_native_issues
from src.engine.rule_outcome import RuleDeferred
from src.engine.native_narrative_checks import _foreign_owner
from src.engine.native_table_arithmetic import select
from src.engine.rules_v33 import (
    R33110_BudgetVsFinal_TextConsistency,
    R33240_Table2_IncomeAdvancedCheck,
    _extract_table_total,
    _final_section_scope,
    build_document,
)
from src.schemas.parse_artifact import (
    LogicalTableInstance,
    ParseArtifact,
    ParsedCell,
    ParsedTable,
)


# ---------------------------------------------------------------------------
# 章节与合计标签入口
# ---------------------------------------------------------------------------


def test_halfwidth_section_number_is_recognized():
    text = (
        "(三)一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为 100.00 万元，支出决算为 120.00 万元，"
        "完成年初预算的 120%。决算数小于预算数。\n"
        "（四）一般公共预算财政拨款基本支出决算情况说明\n"
    )
    doc = build_document("sample.pdf", [text], [], 1000)
    issues = R33110_BudgetVsFinal_TextConsistency().apply(doc)
    assert len(issues) == 1
    assert "实际大于" in issues[0].message and "表述为小于" in issues[0].message


def test_section_scope_skips_toc_and_uses_typo_body():
    toc = (
        "目 录\n六、一般公共预算财政拨款基本支出决算情况说明\n"
        "七、一般公共预算财政拨款“三公”经费支出决算情况说明\n"
    )
    body = (
        "六、一般公共预算财政拨款本支出决算情况说明\n"
        "一般公共预算财政拨款基本支出 100.00 万元。其中：人员经费 80.00 万元，"
        "公用经费 20.00 万元。\n"
        "七、一般公共预算财政拨款“三公”经费支出决算情况说明\n三公内容。\n"
    )
    doc = build_document("sample.pdf", [toc, body], [], 1000)
    assert _final_section_scope(doc, ("基本支出决算情况说明",)) is None
    scope = _final_section_scope(doc, ("财政拨款本支出决算情况说明",))
    assert scope is not None and "11" not in scope[:4]
    assert "基本支出 100.00 万元" in scope


def test_total_row_accepts_spaced_heji_label():
    table = [
        ["项目", "", "合计", "基本支出", "项目支出"],
        ["合 计", "", "7709.62", "6215.79", "1493.83"],
    ]
    assert _extract_table_total(table) == 7709.62


# ---------------------------------------------------------------------------
# 真实案例 34f7645b：第 8 页 2013499 空白行参与横向与纵向勾稽
# ---------------------------------------------------------------------------


def _income_artifact_with_blank_row():
    header = [
        "功能分类科目编码", "科目名称", "本年收入合计", "财政拨款收入",
        "上级补助收入", "事业收入", "经营收入", "附属单位上缴收入", "其他收入",
    ]
    rows = [
        header,
        ["合计", "", "9470.18", "9381.78", "", "", "", "", "88.40"],
        ["201", "一般公共服务支出", "7320.19", "7231.78", "", "", "", "", "88.40"],
        ["20134", "统战事务", "3472.09", "3384.61", "", "", "", "", "87.47"],
        ["2013404", "宗教事务", "3472.09", "3384.61", "", "", "", "", "87.47"],
        # 原件第 8 页该行所有金额格均为认证空白（2013499 其他统战事务支出）。
        ["2013499", "其他统战事务支出", "", "", "", "", "", "", ""],
    ]
    cells = []
    for row_idx, row in enumerate(rows):
        for col_idx, value in enumerate(row):
            blank = row_idx >= 1 and col_idx >= 2 and value == ""
            number = (
                Decimal(value.replace(",", ""))
                if value and col_idx >= 2 and value.replace(",", "").replace(".", "").isdigit()
                else None
            )
            cells.append(ParsedCell(
                row_idx, col_idx, value, value,
                number,
                8 if row_idx < 4 else 9,
                (col_idx * 70.0, row_idx * 25.0, (col_idx + 1) * 70.0, (row_idx + 1) * 25.0),
                row_idx == 0,
                0.99,
                "万元",
                "lines",
                native_blank_verified=blank,
            ))
    table = ParsedTable(
        "FIN_02_income", "收入决算表", 8, 0, (0.0, 0.0, 630.0, 150.0), 1, 0.99,
        "lines", "万元", rows, cells, "frag-fin02",
    )
    instance = LogicalTableInstance(
        "87a2058a74ba7292", "FIN_02_income", "收入决算表", ["frag-fin02"], 8, 9,
        org_name="上海市民族和宗教事务局", fiscal_year=2024,
    )
    artifact = ParseArtifact(
        source_file_hash="a" * 64, parse_run_uid="run-34f7645b",
        table_fragments=[table], physical_fragments=[table], logical_tables=[instance],
    )
    return artifact


def test_blank_income_row_2013499_executes_horizontal_and_vertical():
    artifact = _income_artifact_with_blank_row()
    context = build_fact_context_from_artifact(
        artifact, org_name="上海市民族和宗教事务局", fiscal_year=2024,
        report_kind="final", doc_type="dept_final",
    )
    assert context is not None
    doc = build_document("sample.pdf", ["收入决算表"] * 9, [], 1000)
    doc.parse_artifact = artifact
    doc.fact_context = context
    R33240_Table2_IncomeAdvancedCheck().apply(doc)
    receipts = {row["sub_check_id"]: row for row in doc.fact_receipts}
    # 横向：total_actual 也是认证空白，按 U-5 约定取 0，整行无发生额。
    assert receipts["FIN_02_income:2013499:income_sources"]["status"] == "not_applicable"
    # 纵向：20134 = 2013404 + 2013499(0)，关系实际执行并通过。
    assert receipts["FIN_02_income:20134:total_actual"]["status"] == "pass"
    # 空白项只作运行时观察，不伪造金额事实。
    assert not any(fact.subject_code == "2013499" for fact in context.facts)


# ---------------------------------------------------------------------------
# 0ed5318c：同一处“万万元”只保留一个主问题，缺单位误报消除
# ---------------------------------------------------------------------------


def _duplicate_unit_issues():
    page = 32
    return page, [
        {
            "id": "V33-222-17", "rule_id": "V33-222", "severity": "warn",
            "message": "说明第32页科目“其他广播电视教育支出”决算金额单位重复："
                       "“28,146.97万万元”，请核对并修正原文单位（不作猜测换算）。",
            "location": {"page": page, "bad_unit": "万万元"},
        },
        {
            "id": "V33-251-24", "rule_id": "V33-251", "severity": "warn",
            "message": "支出决算具体情况中金额单位重复：“28,146.97万万元”，请核对并修正原文单位。",
            "location": {"page": page},
        },
        {
            "id": "CMM-011-31", "rule_id": "CMM-011", "severity": "warn",
            "message": "金额单位书写有误（重复单位）：“28,146.97\n万万元”，请核对并修正原文单位。",
            "location": {"page": page},
        },
        {
            "id": "CMM-011-32", "rule_id": "CMM-011", "severity": "warn",
            "message": "金额缺少单位“万元”：“支出决算为 28,146.97”，同段其余金额均以万元列示。",
            "location": {"page": page},
        },
    ]


def test_duplicate_unit_issues_merge_and_missing_unit_false_positive_is_suppressed():
    page, issues = _duplicate_unit_issues()
    doc = build_document("sample.pdf", [] , [], 1000)
    doc.page_texts = [""] * (page - 1) + [
        "支出决算为 28,146.97\n万万元。决算数大于预算数的主要原因：项目调整。"
    ]
    doc.fact_context = SimpleNamespace(file_hash="a" * 64)
    merged = deduplicate_native_issues(issues, doc)
    assert len(merged) == 1
    assert merged[0]["rule_id"] == "V33-222"
    location = merged[0]["location"]
    assert location["defect_type"] == "amount_unit_duplicated"
    assert set(location["member_rules"]) == {"V33-222", "V33-251", "CMM-011"}
    assert "缺少单位" not in str(merged[0]["message"])


def test_cmm011_does_not_report_missing_unit_for_duplicated_unit_literal():
    text = "支出决算为 28,146.97 万万元。决算数大于预算数的主要原因：项目调整。"
    doc = build_document("sample.pdf", [text], [], 1000)
    issues = CMM011_AmountUnitWan().apply(doc)
    messages = [issue.message for issue in issues]
    assert any("重复单位" in message for message in messages)
    assert not any("缺少单位" in message for message in messages)


# ---------------------------------------------------------------------------
# edb56827：多个（项）共享“其中：”分项金额
# ---------------------------------------------------------------------------


def test_v33_251_shared_item_amounts_are_not_reported_as_missing_final():
    text = (
        "（三）一般公共预算财政拨款支出决算具体情况\n"
        "一般公共预算财政拨款支出年初预算为 11654.43 万元，支出决算为 11685.76 万元，"
        "完成年初预算的 100.27%。决算数大于预算数的主要原因：年中追加。其中：\n"
        "1、教育支出（类）职业教育（款）中等职业教育（项）。主要用于：中专教育支出。"
        "年初预算为 8603.44 万元，支出决算为 8643.84 万元。\n"
        "2、社会保障和就业支出（类）行政事业单位养老支出（款）机关事业单位基本养老保险缴费支出（项）、"
        "机关事业单位职业年金缴费支出（项）、其他行政事业单位养老支出（项）。主要用于教职工养老。"
        "其中：机关事业单位基本养老保险缴费支出 985.78 万元，机关事业单位职业年金 492.39 万元，"
        "其他行政事业单位离退休支出 10.2 万元，事业单位离退休 566.77 万元。"
        "年初预算数总计 2064.21 万元；支出决算为 2055.14 万元，完成年初预算的 99.56%。\n"
        "3、卫生健康支出（类）行政事业单位医疗（款）事业单位医疗（项）。年初预算为 556.19 万元，"
        "支出决算为 556.19 万元。\n"
        "4、住房保障支出（类）住房改革支出（款）住房公积金（项）。年初预算为 430.59 万元，"
        "支出决算为 430.59 万元。\n"
    )
    doc = build_document("sample.pdf", [text], [], 1000)
    try:
        issues = R33251_FunctionalDetailCompleteness().apply(doc)
    except RuleDeferred as caught:
        issues = caught.partial_issues or []
    # 预期只剩预算披露缺口；支出决算不得再被报成未披露。
    receipts = {row["sub_check_id"]: row for row in doc.fact_receipts}
    assert receipts["V33-251:final:item_sum"]["status"] == "pass"
    # 2026-11-25 起：组级年初预算合计本身纳入章节加总，预算侧不再以 insufficient_data 阻断，
    # 改以正式 A-09 warn 提示“未逐项列示年初预算”。
    assert receipts["V33-251:budget:item_sum"]["status"] == "pass"
    assert any(
        (issue.location or {}).get("checklist_item") == "A-09"
        and "未逐项列示年初预算" in issue.message
        for issue in issues
    )


# ---------------------------------------------------------------------------
# 主体名称多字不阻断正文取数
# ---------------------------------------------------------------------------


def test_duplicated_owner_tail_is_not_treated_as_foreign_subject():
    assert _foreign_owner("第三部分上海市生态环境局局2024年度部门决算情况说明一",
                          "上海市生态环境局") is False
    assert _foreign_owner("上海市水务局2024年度部门决算", "上海市生态环境局") is True
