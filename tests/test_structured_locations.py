from typing import Any, cast

from src.engine.budget_rules import BUD105_CrossTableChecks
from src.engine.rules_v33 import (
    R33225_Narrative1_T1,
    R33227_Narrative5_T5_NameConsistency,
    build_document,
)
from src.utils.issue_display import build_issue_display


def test_issue_display_includes_structured_location_refs() -> None:
    issue = {
        "title": "说明4↔T4总计不一致",
        "message": "说明4↔T4总计不一致: 说明=123.40, T4=100.00",
        "location": {
            "pages": [3, 8],
            "table": "财政拨款收入支出决算总表",
            "section": "说明4（财政拨款总体情况）",
            "row": "总计",
            "field": "财政拨款总计",
            "table_refs": [
                {
                    "role": "说明4",
                    "page": 3,
                    "section": "说明4（财政拨款总体情况）",
                    "field": "财政拨款总计",
                    "value": 123.4,
                },
                {
                    "role": "T4",
                    "page": 8,
                    "table": "财政拨款收入支出决算总表",
                    "row": "总计",
                    "field": "总计",
                    "value": 100.0,
                },
            ],
        },
        "evidence": [],
    }

    display = build_issue_display(issue)

    assert display["page_text"] == "第3、8页"
    assert display["location_text"] == (
        "相关页: 3, 8 / 表: 财政拨款收入支出决算总表 / 章节: 说明4（财政拨款总体情况）"
        " / 行: 总计 / 字段: 财政拨款总计"
    )
    assert "说明4: 第3页 / 章节: 说明4（财政拨款总体情况） / 字段: 财政拨款总计 / 金额: 123.40" in display["detail_lines"]
    assert "T4: 第8页 / 表: 财政拨款收入支出决算总表 / 行: 总计 / 字段: 总计 / 金额: 100" in display["detail_lines"]


def test_budget_cross_table_rule_emits_multi_page_refs() -> None:
    doc = build_document(
        path="budget.pdf",
        page_texts=[
            "2025年部门财务收支预算总表\n单位：元",
            "2025年部门财政拨款收支预算总表\n单位：元",
        ],
        page_tables=[
            [[
                ["项目", "预算数", "项目", "预算数"],
                ["收入总计", "100", "支出总计", "100"],
            ]],
            [[
                ["项目", "预算数", "项目", "预算数"],
                ["收入总计", "90", "支出总计", "100"],
            ]],
        ],
        filesize=128,
    )

    issues = BUD105_CrossTableChecks().apply(doc)

    assert issues
    income_issue = next(issue for issue in issues if "收入总计不一致" in issue.message)
    assert income_issue.location["pages"] == [1, 2]
    assert income_issue.location["field"] == "收入总计"
    assert income_issue.location["row"] == "收入总计"
    assert income_issue.location["table"] == "BUD_T1 / BUD_T4"
    assert income_issue.location["table_refs"][0]["page"] == 1
    assert income_issue.location["table_refs"][1]["page"] == 2


def test_narrative_rule_emits_section_and_table_pages() -> None:
    doc = build_document(
        path="final.pdf",
        page_texts=[
            "收入支出决算总体情况说明：收入支出总计 100 万元。",
            "收入支出决算总表\n单位：万元",
        ],
        page_tables=[
            [],
            [[
                ["项目", "决算数"],
                ["90", "总计"],
            ]],
        ],
        filesize=256,
    )

    issues = R33225_Narrative1_T1().apply(doc)

    assert issues
    issue = issues[0]
    assert issue.location["pages"] == [1, 2]
    assert issue.location["section"] == "说明1（总体情况）"
    assert issue.location["table"] == "收入支出决算总表"
    assert issue.location["field"] == "收入支出总计"
    assert issue.location["table_refs"][0]["role"] == "说明1"
    assert issue.location["table_refs"][0]["page"] == 1
    assert issue.location["table_refs"][1]["role"] == "T1"
    assert issue.location["table_refs"][1]["page"] == 2


def test_final_functional_name_rule_detects_mismatch_without_codes() -> None:
    explanation_text = "\n".join(
        [
            "五、一般公共预算财政拨款支出决算情况说明",
            "（三）一般公共预算财政拨款支出决算具体情况",
            "1、医疗卫生与计划生育支出（类）行政事业单位医疗（款）行政单位医疗（项）。年初预算为114.97万元，支出决算为114.97万元。",
        ]
    )
    t5_page_text = "\n".join(
        [
            "一般公共预算财政拨款支出决算表",
            "单位：万元",
            "210 卫生健康支出",
            "210 11 行政事业单位医疗",
            "210 11 01 行政单位医疗",
        ]
    )
    t5_table: list[list[Any]] = [
        ["项目", None, None, None, "决算数"],
        ["功能分类科目编码", None, None, "功能分类科目名称", "决算数"],
        ["类", "款", "项", None, None],
        ["210", "", "", "卫生健康支出", "114.97"],
        ["210", "11", "", "行政事业单位医疗", "114.97"],
        ["210", "11", "01", "行政单位医疗", "114.97"],
        ["合计", None, None, None, "114.97"],
    ]
    doc = build_document(
        path="final.pdf",
        page_texts=[explanation_text, t5_page_text],
        page_tables=[cast(list[list[Any]], []), [t5_table]],
        filesize=256,
    )

    issues = R33227_Narrative5_T5_NameConsistency().apply(doc)

    assert issues
    issue = issues[0]
    assert "卫生健康支出" in issue.message
    assert "医疗卫生与计划生育支出" in issue.message
    assert issue.location["table"] == "一般公共预算财政拨款支出决算表"
    assert issue.location["section"] == "说明5（一般公共预算财政拨款支出决算具体情况）"
    assert issue.location["row"] == "210"
    refs = issue.location.get("table_refs") or []
    assert len(refs) == 2
    assert refs[0]["role"] == "说明5"
    assert refs[0]["page"] == 1
    assert refs[1]["role"] == "T5"
    assert refs[1]["page"] == 2
    assert issue.location["expected_name"] == "\u536b\u751f\u5065\u5eb7\u652f\u51fa"
    assert issue.location["actual_name"] == "\u533b\u7597\u536b\u751f\u4e0e\u8ba1\u5212\u751f\u80b2\u652f\u51fa"
    assert issue.location["code_level"] == "\u7c7b"
    assert issue.location["source_of_truth"] == "T5"
    assert "编码：210" in (issue.evidence_text or "")


def test_final_functional_name_rule_ignores_matching_names_without_codes() -> None:
    explanation_text = "\n".join(
        [
            "五、一般公共预算财政拨款支出决算情况说明",
            "（三）一般公共预算财政拨款支出决算具体情况",
            "1、卫生健康支出（类）行政事业单位医疗（款）行政单位医疗（项）。年初预算为114.97万元，支出决算为114.97万元。",
        ]
    )
    t5_page_text = "\n".join(
        [
            "一般公共预算财政拨款支出决算表",
            "单位：万元",
            "210 卫生健康支出",
            "210 11 行政事业单位医疗",
            "210 11 01 行政单位医疗",
        ]
    )
    t5_table: list[list[Any]] = [
        ["项目", None, None, None, "决算数"],
        ["功能分类科目编码", None, None, "功能分类科目名称", "决算数"],
        ["类", "款", "项", None, None],
        ["210", "", "", "卫生健康支出", "114.97"],
        ["210", "11", "", "行政事业单位医疗", "114.97"],
        ["210", "11", "01", "行政单位医疗", "114.97"],
        ["合计", None, None, None, "114.97"],
    ]
    doc = build_document(
        path="final.pdf",
        page_texts=[explanation_text, t5_page_text],
        page_tables=[cast(list[list[Any]], []), [t5_table]],
        filesize=256,
    )

    issues = R33227_Narrative5_T5_NameConsistency().apply(doc)

    assert not issues
