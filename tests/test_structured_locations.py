from src.engine.budget_rules import BUD105_CrossTableChecks
from src.engine.rules_v33 import R33225_Narrative1_T1, build_document
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
