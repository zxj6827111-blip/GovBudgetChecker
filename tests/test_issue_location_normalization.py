from src.engine.rules_v33 import Issue, build_document
from src.schemas.issues import IssueItem
from src.services.engine_rule_runner import EngineRuleRunner


def test_rule_runner_normalizes_intertable_pages_and_refs() -> None:
    doc = build_document(
        path="final.pdf",
        page_texts=[
            "收入支出决算总表\n单位：万元",
            "收入决算表\n单位：万元",
        ],
        page_tables=[
            [[["项目", "决算数"], ["本年收入合计", "100"]]],
            [[["项目", "决算数"], ["合计", "90"]]],
        ],
        filesize=128,
    )
    issue = Issue(
        rule="V33-200",
        severity="error",
        message="T1↔T2收入合计不一致：T1=100.00, T2=90.00",
        evidence_text="表1(总表) 本年收入合计: 100\n表2(收入表) 合计: 90",
        location={"t1": 100.0, "t2": 90.0},
    )

    finding = EngineRuleRunner()._issue_to_finding(issue, rule_id="V33-200", document=doc)

    assert finding.location["page"] == 1
    assert finding.location["pages"] == [1, 2]
    assert finding.location["table"] == "收入支出决算总表 / 收入决算表"
    assert finding.location["field"] == "本年收入合计"
    assert len(finding.location["table_refs"]) == 2
    assert finding.location["table_refs"][0]["role"] == "T1"
    assert finding.location["table_refs"][0]["page"] == 1
    assert finding.location["table_refs"][0]["value"] == 100.0
    assert finding.location["table_refs"][1]["role"] == "T2"
    assert finding.location["table_refs"][1]["page"] == 2
    assert finding.location["table_refs"][1]["value"] == 90.0


def test_rule_runner_normalizes_table6_advanced_location() -> None:
    doc = build_document(
        path="final.pdf",
        page_texts=[
            "封面",
            "一般公共预算财政拨款基本支出决算表\n单位：万元",
        ],
        page_tables=[
            [],
            [[["项目", "决算数"], ["人员经费合计", "10"]]],
        ],
        filesize=128,
    )
    issue = Issue(
        rule="V33-243",
        severity="error",
        message="【表六】人员经费口径错误：人员总计(10.00) != 301(6.00) + 303(1.00) = 7.00",
        evidence_text="表内人员经费合计：10\n计算：301(6) + 303(1) = 7",
        location={"type": "personnel"},
    )

    finding = EngineRuleRunner()._issue_to_finding(issue, rule_id="V33-243", document=doc)

    assert finding.location["page"] == 2
    assert finding.location["table"] == "一般公共预算财政拨款基本支出决算表"
    assert finding.location["row"] == "人员经费合计"
    assert finding.location["field"] == "人员经费"
    assert finding.location["table_refs"][0]["page"] == 2
    assert finding.display is not None
    assert "表: 一般公共预算财政拨款基本支出决算表" in finding.display.location_text


def test_issue_item_normalizes_legacy_row_evidence_without_document() -> None:
    item = IssueItem(
        id="rule:v33-210:test",
        source="rule",
        rule_id="V33-210",
        severity="medium",
        title="T2 第4行行内合计不一致",
        message="T2 第4行行内合计不一致",
        evidence=[
            {
                "page": 6,
                "text": "表格：收入决算表\n第4行内容：财政拨款收入 10 20 40\n行内最大值(合计): 40\n其余项之和: 30",
            }
        ],
        location={},
        metrics={},
        tags=[],
    )

    assert item.location["page"] == 6
    assert item.location["table"] == "收入决算表"
    assert item.location["row"] == "第4行"
    assert item.location["field"] == "行内合计"


def test_rule_runner_applies_rule_specific_title_and_suggestion() -> None:
    doc = build_document(
        path="final.pdf",
        page_texts=[
            "五、一般公共预算财政拨款支出决算情况说明",
            "一般公共预算财政拨款支出决算表\n单位：万元",
        ],
        page_tables=[[], []],
        filesize=128,
    )
    issue = Issue(
        rule="V33-227",
        severity="warn",
        message="说明5类级科目名称与T5不一致（210）：表格“卫生健康支出”，说明“医疗卫生与计划生育支出”",
        evidence_text="编码：210\n表格名称：卫生健康支出\n说明名称：医疗卫生与计划生育支出",
        location={
            "page": 1,
            "table": "一般公共预算财政拨款支出决算表",
            "section": "说明5（一般公共预算财政拨款支出决算具体情况）",
            "row": "210",
            "field": "功能分类科目名称",
        },
    )

    finding = EngineRuleRunner()._issue_to_finding(issue, rule_id="V33-227", document=doc)

    assert finding.title == "说明5功能分类类款项名称与T5不一致"
    assert finding.suggestion is not None
    assert "说明5中同编码的类/款/项名称" in finding.suggestion
    assert "第1页" in finding.suggestion
    assert finding.display is not None
    assert finding.display.summary == "说明5功能分类类款项名称与T5不一致"
