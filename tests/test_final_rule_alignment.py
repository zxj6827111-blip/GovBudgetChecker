from src.engine.rules_v33 import (
    R33233_DetailRowFormulaConsistency,
    R33234_NarrativePercentConsistency,
    R33235_NarrativeAmountConsistency,
    R33236_DocumentScopeTerminology,
    build_document,
)


def test_final_detail_row_formula_rule_detects_row_mismatch() -> None:
    page_text = "\n".join(
        [
            "2025年支出决算表",
            "单位：元",
        ]
    )
    table = [
        ["类", "款", "项", "功能分类科目名称", "合计", "基本支出", "项目支出"],
        ["项目", "", "", "", "决算数", "决算数", "决算数"],
        ["", "", "", "", "", "", ""],
        ["204", "06", "01", "行政运行", "14348853.34", "14348853.34", "64840547.00"],
    ]
    doc = build_document(
        path="final.pdf",
        page_texts=[page_text],
        page_tables=[[table]],
        filesize=128,
    )

    issues = R33233_DetailRowFormulaConsistency().apply(doc)

    assert issues
    issue = issues[0]
    assert issue.rule == "V33-233"
    assert "行政运行" in issue.message
    assert issue.location["table"] == "支出决算表"
    assert issue.location["row"] == "204-06-01 行政运行"


def test_final_narrative_percent_rule_detects_year_over_year_mismatch() -> None:
    page_text = "\n".join(
        [
            "五、一般公共预算财政拨款支出决算情况说明",
            "1、社会保障和就业支出（208类）行政事业单位养老支出（05款）行政单位离退休（01项）56.12万元。"
            "2024年决算数为57.43万元，2025年支出决算为56.12万元，比2024年决算数减少1.10%。",
        ]
    )
    doc = build_document(
        path="final.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=64,
    )

    issues = R33234_NarrativePercentConsistency().apply(doc)

    assert issues
    assert any("减少2.28%" in issue.message for issue in issues)
    assert any("减少1.10%" in issue.message for issue in issues)


def test_final_narrative_percent_rule_detects_completion_rate_mismatch() -> None:
    page_text = "\n".join(
        [
            "五、一般公共预算财政拨款支出决算情况说明",
            "1、卫生健康支出（210类）行政事业单位医疗（11款）行政单位医疗（01项）90万元。"
            "年初预算为100万元，支出决算为90万元，完成年初预算的110%。",
        ]
    )
    doc = build_document(
        path="final.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=64,
    )

    issues = R33234_NarrativePercentConsistency().apply(doc)

    assert issues
    assert any("90.00%" in issue.message for issue in issues)
    assert any("110.00%" in issue.message for issue in issues)


def test_final_narrative_amount_rule_detects_conflict() -> None:
    page_text = "\n".join(
        [
            "五、一般公共预算财政拨款支出决算情况说明",
            "1、住房保障支出（221类）住房改革支出（02款）购房补贴（03项）207万元。"
            "年初预算为241.76万元，支出决算为170.03万元，比2024年决算数减少29.67%。",
        ]
    )
    doc = build_document(
        path="final.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=64,
    )

    issues = R33235_NarrativeAmountConsistency().apply(doc)

    assert issues
    assert any("207.00万元" in issue.message for issue in issues)
    assert any("170.03万元" in issue.message for issue in issues)


def test_final_scope_terminology_rule_detects_department_wording_in_unit_final() -> None:
    page_texts = [
        "2025年单位决算",
        "2025年部门决算安排购置车辆0辆；部门决算安排购置单价100万元（含）以上设备0台（套）。",
    ]
    doc = build_document(
        path="unit-final.pdf",
        page_texts=page_texts,
        page_tables=[[], []],
        filesize=32,
    )

    issues = R33236_DocumentScopeTerminology().apply(doc)

    assert issues
    assert all(issue.rule == "V33-236" for issue in issues)
    assert any("单位决算" in issue.message for issue in issues)
    assert any("部门决算安排" in (issue.evidence_text or "") for issue in issues)
