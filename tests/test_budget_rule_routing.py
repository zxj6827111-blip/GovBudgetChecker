from types import SimpleNamespace
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine import pipeline
from src.engine.budget_rules import (
    BUD001_StructureAndAnchors,
    BUD002_PlaceholderCheck,
    BUD003_YearConsistency,
    BUD101_T1Balance,
    BUD106_EmptyTableStatement,
    BUD108_PerformanceTargetConsistency,
    BUD109_FunctionalClassificationNameConsistency,
)
from src.engine.common_rules import (
    CMM001_ThreePublicNarrativeConsistency,
    CMM002_TextAnomalyRule,
    CMM003_TocCountConsistency,
    CMM004_CodeMirrorConsistency,
    CMM005_ComparativeNarrativeLogic,
    CMM006_IncomeExpenseTrendConsistency,
)
from src.engine.rules_v33 import Issue, build_document
from src.schemas.issues import AnalysisConfig, JobContext
from src.services import engine_rule_runner as runner_mod


class _DummyRule:
    def __init__(self, code: str):
        self.code = code
        self.desc = code

    def apply(self, _doc):
        return [
            Issue(
                rule=self.code,
                severity="warn",
                message=f"hit-{self.code}",
                location={"page": 1, "pos": 0},
            )
        ]


def test_pipeline_routes_budget_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "ALL_BUDGET_RULES", [_DummyRule("BUD-DUMMY")])
    monkeypatch.setattr(pipeline, "FINAL_ALL_RULES", [_DummyRule("FIN-DUMMY")])

    doc = SimpleNamespace(path="sample.pdf", page_texts=["预算"], page_tables=[[]])
    issues = pipeline.run_rules(doc, use_ai_assist=False, report_kind="budget")

    codes = {issue.rule for issue in issues}
    assert "BUD-DUMMY" in codes
    assert "FIN-DUMMY" not in codes


@pytest.mark.asyncio
async def test_engine_rule_runner_routes_budget_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner_mod, "ALL_BUDGET_RULES", [_DummyRule("BUD-DUMMY")])
    monkeypatch.setattr(runner_mod, "FINAL_ALL_RULES", [_DummyRule("FIN-DUMMY")])

    runner = runner_mod.EngineRuleRunner()
    doc = build_document(
        path="sample_budget.pdf",
        page_texts=["预算"],
        page_tables=[[]],
        filesize=1,
    )

    async def _fake_prepare(_job_context):
        return doc

    monkeypatch.setattr(runner, "_prepare_document", _fake_prepare)

    job_context = JobContext(
        job_id="job-budget",
        pdf_path="sample_budget.pdf",
        page_texts=["预算"],
        page_tables=[[]],
        meta={"report_kind": "budget"},
    )

    findings = await runner.run_rules(
        job_context=job_context, rules=[], config=AnalysisConfig()
    )
    rule_ids = {item.rule_id for item in findings}

    assert "BUD-DUMMY" in rule_ids
    assert "FIN-DUMMY" not in rule_ids
    bud_finding = next(item for item in findings if item.rule_id == "BUD-DUMMY")
    assert bud_finding.location.get("page") == 1
    assert bud_finding.page_number == 1
    assert isinstance(bud_finding.evidence, list) and bud_finding.evidence
    assert bud_finding.evidence[0].get("page") == 1


def test_budget_t1_balance_rule_detects_mismatch() -> None:
    # "部门财务收支预算总表" with mismatch between income total and expense total.
    page_text = "2025年部门财务收支预算总表\n单位：元"
    table = [
        ["本年收入", "", "本年支出", ""],
        ["项目", "预算数", "项目", "预算数"],
        ["收入总计", "100", "支出总计", "90"],
    ]
    doc = build_document(
        path="budget.pdf",
        page_texts=[page_text],
        page_tables=[[table]],
        filesize=123,
    )

    issues = BUD101_T1Balance().apply(doc)

    assert issues
    assert any(issue.rule == "BUD-101" for issue in issues)


def test_budget_missing_table_message_contains_table_name() -> None:
    doc = build_document(
        path="unit25_budget.pdf",
        page_texts=["2025年预算报告"],
        page_tables=[[]],
        filesize=11,
    )

    issues = BUD001_StructureAndAnchors().apply(doc)

    missing_t1 = [issue for issue in issues if "BUD_T1" in issue.message]
    assert missing_t1
    assert any(
        "\u90e8\u95e8\u8d22\u52a1\u6536\u652f\u9884\u7b97\u603b\u8868" in issue.message
        for issue in missing_t1
    )


def test_budget_placeholder_rule_detects_x_and_ellipsis_placeholders() -> None:
    page_text = (
        "\u4e0a\u6d77\u5e02\u666e\u9640\u533aXXXXX\u4e3b\u8981\u804c\u80fd\n"
        "\uff08\u4e5d\uff09\u3002\u3002\u3002\u3002\u3002"
    )
    doc = build_document(
        path="unit25_budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=33,
    )

    issues = BUD002_PlaceholderCheck().apply(doc)

    assert any("XXXXX" in issue.message for issue in issues)
    assert any("\u3002\u3002\u3002" in issue.message for issue in issues)


def test_budget_placeholder_rule_detects_times_placeholder_symbol() -> None:
    page_text = "\u9879\u76ee\u8d44\u91d1\u8bf4\u660e\uff1a\u00d7\u00d7\u4e07\u5143\uff0c\u5f85\u540e\u7eed\u586b\u5199\u3002"
    doc = build_document(
        path="unit25_budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=18,
    )

    issues = BUD002_PlaceholderCheck().apply(doc)

    assert any("\u00d7\u00d7" in issue.message for issue in issues)


def test_budget_year_rule_detects_wrong_year_in_budget_table_title() -> None:
    page_text = "\n".join(
        [
            "\u4e0a\u6d77\u5e02\u67d0\u5355\u4f4d2025\u5e74\u9884\u7b97",
            "1. 2025\u5e74\u90e8\u95e8\u8d22\u52a1\u6536\u652f\u9884\u7b97\u603b\u8868",
            "9. 2024\u5e74\u90e8\u95e8\u201c\u4e09\u516c\u201d\u7ecf\u8d39\u548c\u673a\u5173\u8fd0\u884c\u7ecf\u8d39\u9884\u7b97\u8868",
        ]
    )
    doc = build_document(
        path="unit25_budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=55,
    )

    issues = BUD003_YearConsistency().apply(doc)

    assert issues
    assert any("2024" in issue.message for issue in issues)


def test_budget_placeholder_rule_ignores_toc_leader_dots() -> None:
    toc_text = "\n".join(
        [
            "\u76ee\u5f55",
            "1. 2026\u5e74\u90e8\u95e8\u8d22\u52a1\u6536\u652f\u9884\u7b97\u603b\u8868...........................1",
            "2. 2026\u5e74\u90e8\u95e8\u6536\u5165\u9884\u7b97\u603b\u8868...............................2",
            "3. 2026\u5e74\u90e8\u95e8\u652f\u51fa\u9884\u7b97\u603b\u8868...............................3",
            "\u56db\u3001\u90e8\u95e8\u9884\u7b97\u7f16\u5236\u8bf4\u660e\u2026\u2026\u2026\u2026\u2026\u2026\u2026\u2026",
        ]
    )
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[toc_text],
        page_tables=[[]],
        filesize=22,
    )

    issues = BUD002_PlaceholderCheck().apply(doc)

    assert not issues


def test_budget_year_rule_does_not_parse_amount_as_year() -> None:
    page_text = "\n".join(
        [
            "\u4e0a\u6d77\u5e02\u67d0\u5355\u4f4d2026\u5e74\u9884\u7b97",
            "2026\u5e74\u9884\u7b97\u7f16\u5236\u8bf4\u660e",
            "\u6536\u5165\u9884\u7b9720765.62\u4e07\u5143\uff0c\u6bd42025\u5e74\u9884\u7b97\u589e\u52a01788.86\u4e07\u5143\u3002",
        ]
    )
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=32,
    )

    issues = BUD003_YearConsistency().apply(doc)

    assert not any("2076" in issue.message for issue in issues)


def test_budget_year_rule_ignores_historical_year_in_non_target_line() -> None:
    page_text = "\n".join(
        [
            "\u4e0a\u6d77\u5e02\u67d0\u5355\u4f4d2026\u5e74\u9884\u7b97",
            "\u5176\u4ed6\u76f8\u5173\u60c5\u51b5\u8bf4\u660e",
            "\u9879\u76ee\u5386\u53f2\u6cbf\u9769\uff1a2024\u5e74\u5b8c\u6210\u7b2c\u4e00\u9636\u6bb5\u5efa\u8bbe\u3002",
        ]
    )
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=35,
    )

    issues = BUD003_YearConsistency().apply(doc)

    assert not issues


def test_budget_empty_table_statement_detects_wrong_template_phrase() -> None:
    page_text = "\n".join(
        [
            "2026年部门国有资本经营预算支出功能分类预算表",
            "单位：万元",
            "无政府性基金预算财政拨款安排，本表为空表。",
        ]
    )
    table = [
        ["项目", "预算数"],
        ["合计", "0"],
    ]
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[table]],
        filesize=20,
    )

    issues = BUD106_EmptyTableStatement().apply(doc)

    assert issues
    assert any("套模" in issue.message for issue in issues)


def test_budget_empty_table_statement_ignores_year_header_and_requires_note() -> None:
    page_text = "\n".join(
        [
            "2026年部门政府性基金预算支出功能分类预算表",
            "单位：万元",
        ]
    )
    table = [
        ["2026年", "2025年"],
        ["项目", "预算数"],
        ["", ""],
    ]
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[table]],
        filesize=20,
    )

    issues = BUD106_EmptyTableStatement().apply(doc)

    assert issues
    assert any("缺少规范注释说明" in issue.message for issue in issues)


def test_budget_empty_table_statement_flags_t9_empty_without_note() -> None:
    page_text = "\n".join(
        [
            "2026年部门“三公”经费和机关运行经费预算表",
            "单位：万元",
        ]
    )
    table = [
        ["项目", "预算数"],
        ["合计", "0"],
    ]
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[table]],
        filesize=20,
    )

    issues = BUD106_EmptyTableStatement().apply(doc)

    assert issues
    assert any("BUD_T9" in issue.message for issue in issues)


def test_budget_empty_table_statement_accepts_t9_note_with_keywords() -> None:
    page_text = "\n".join(
        [
            "2026年部门“三公”经费和机关运行经费预算表",
            "单位：万元",
            "注：本单位无财政拨款三公经费预算安排，本表为空表。",
        ]
    )
    table = [
        ["项目", "预算数"],
        ["合计", "0"],
    ]
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[table]],
        filesize=20,
    )

    issues = BUD106_EmptyTableStatement().apply(doc)

    assert not issues


def test_budget_performance_target_consistency_detects_large_gap() -> None:
    t3_page_text = "\n".join(
        [
            "2026年部门支出预算总表",
            "单位：万元",
        ]
    )
    t3_table = [
        ["项目", "支出合计", "基本支出", "项目支出"],
        ["合计", "120.00", "80.00", "40.00"],
    ]
    perf_page_text = "绩效目标设置情况：2026年共11个项目，涉及预算资金120.00万元。"
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[t3_page_text, perf_page_text],
        page_tables=[[t3_table], []],
        filesize=30,
    )

    issues = BUD108_PerformanceTargetConsistency().apply(doc)

    assert issues
    assert any("口径" in issue.message for issue in issues)
    issue = issues[0]
    refs = issue.location.get("table_refs") or []
    assert len(refs) == 2
    assert refs[0]["role"] == "说明"
    assert refs[0]["page"] == 2
    assert refs[1]["role"] == "T3"
    assert refs[1]["page"] == 1
    assert refs[1]["row"] == "合计"
    assert refs[1]["field"] == "项目支出"


def test_budget_performance_target_consistency_allows_rounding_tolerance() -> None:
    t3_page_text = "\n".join(
        [
            "2026年部门支出预算总表",
            "单位：万元",
        ]
    )
    t3_table = [
        ["项目", "支出合计", "基本支出", "项目支出"],
        ["合计", "120.00", "80.00", "40.00"],
    ]
    perf_page_text = "绩效目标设置情况：2026年共11个项目，涉及预算资金40.08万元。"
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[t3_page_text, perf_page_text],
        page_tables=[[t3_table], []],
        filesize=30,
    )

    issues = BUD108_PerformanceTargetConsistency().apply(doc)

    assert not issues


def test_budget_functional_classification_name_rule_detects_narrative_mismatch() -> None:
    explanation_text = "\n".join(
        [
            "2026年部门预算编制说明",
            "财政拨款支出主要内容如下：",
            "11、医疗卫生与计划生育支出（210类）行政事业单位医疗（11款）行政单位医疗（01项）114.97万元。",
        ]
    )
    t5_page_text = "\n".join(
        [
            "2026年部门一般公共预算支出功能分类预算表",
            "单位：元",
            "210 卫生健康支出",
            "210 11 行政事业单位医疗",
            "210 11 01 行政单位医疗",
        ]
    )
    t5_table = [
        ["项目", None, None, None, "一般公共预算支出", None, None],
        ["功能分类科目编码", None, None, "功能分类科目名称", "合计", "基本支出", "项目支出"],
        ["类", "款", "项", None, None, None, None],
        ["210", "", "", "卫生健康支出", "1149678.00", "1149678.00", "0.00"],
        ["210", "11", "", "行政事业单位医疗", "1149678.00", "1149678.00", "0.00"],
        ["210", "11", "01", "行政单位医疗", "1149678.00", "1149678.00", "0.00"],
        ["合计", None, None, None, "1149678.00", "1149678.00", "0.00"],
    ]
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[explanation_text, "其他页面", t5_page_text],
        page_tables=[[], [], [t5_table]],
        filesize=30,
    )

    issues = BUD109_FunctionalClassificationNameConsistency().apply(doc)

    assert issues
    assert any("卫生健康支出" in issue.message for issue in issues)
    assert any("医疗卫生与计划生育支出" in issue.message for issue in issues)
    issue = issues[0]
    refs = issue.location.get("table_refs") or []
    assert len(refs) == 2
    assert refs[0]["role"] == "说明"
    assert refs[0]["page"] == 1
    assert refs[1]["role"] == "T5"
    assert refs[1]["page"] == 3
    assert issue.location["expected_name"] == "\u536b\u751f\u5065\u5eb7\u652f\u51fa"
    assert issue.location["actual_name"] == "\u533b\u7597\u536b\u751f\u4e0e\u8ba1\u5212\u751f\u80b2\u652f\u51fa"
    assert issue.location["code_level"] == "\u7c7b"
    assert issue.location["source_of_truth"] == "BUD_T5"
    assert "编码：210" in (issue.evidence_text or "")


def test_budget_functional_classification_name_rule_ignores_matching_names() -> None:
    explanation_text = "\n".join(
        [
            "2026年部门预算编制说明",
            "财政拨款支出主要内容如下：",
            "11、卫生健康支出（210类）行政事业单位医疗（11款）行政单位医疗（01项）114.97万元。",
        ]
    )
    t5_page_text = "\n".join(
        [
            "2026年部门一般公共预算支出功能分类预算表",
            "单位：元",
            "210 卫生健康支出",
            "210 11 行政事业单位医疗",
            "210 11 01 行政单位医疗",
        ]
    )
    t5_table = [
        ["项目", None, None, None, "一般公共预算支出", None, None],
        ["功能分类科目编码", None, None, "功能分类科目名称", "合计", "基本支出", "项目支出"],
        ["类", "款", "项", None, None, None, None],
        ["210", "", "", "卫生健康支出", "1149678.00", "1149678.00", "0.00"],
        ["210", "11", "", "行政事业单位医疗", "1149678.00", "1149678.00", "0.00"],
        ["210", "11", "01", "行政单位医疗", "1149678.00", "1149678.00", "0.00"],
        ["合计", None, None, None, "1149678.00", "1149678.00", "0.00"],
    ]
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[explanation_text, t5_page_text],
        page_tables=[[], [t5_table]],
        filesize=30,
    )

    issues = BUD109_FunctionalClassificationNameConsistency().apply(doc)

    assert not issues


def test_common_three_public_narrative_rule_detects_run_fee_mismatch() -> None:
    page_texts = [
        "2026年区级单位预算",
        "\n".join(
            [
                "2026年单位“三公”经费和机关运行经费预算表",
                "单位:万元",
                "2.68 0 0 2.68 0 2.68 0",
            ]
        ),
        "\n".join(
            [
                "其他相关情况说明",
                "2026年“三公”经费预算数为2.68万元。",
                "公务用车购置及运行费2.68万元，其中：公务用车购置费0万元；公务用车运行费0万元。",
                "公务接待费0万元。",
            ]
        ),
    ]
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=page_texts,
        page_tables=[[], [], []],
        filesize=100,
    )

    issues = CMM001_ThreePublicNarrativeConsistency().apply(doc)

    assert issues
    assert any(
        "公务用车运行费" in issue.message and issue.severity == "error"
        for issue in issues
    )


def test_common_text_anomaly_rule_detects_duplicate_and_english_comma() -> None:
    page_text = (
        "（一）因公出国（境）费0万元，比2025年预算预算持平。"
        "（二）公务用车购置及运行费,21.56万元，比2025年预算持平。"
    )
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=64,
    )

    issues = CMM002_TextAnomalyRule().apply(doc)

    assert any("重复词" in issue.message for issue in issues)
    assert any("英文逗号" in issue.message for issue in issues)


def test_common_toc_count_rule_detects_toc_and_body_mismatch() -> None:
    toc_text = "\n".join(
        [
            "目录",
            "1. 2026年部门财务收支预算总表",
            "2. 2026年部门收入预算总表",
            "3. 2026年部门支出预算总表",
            "4. 2026年部门财政拨款收支预算总表",
            "5. 2026年部门一般公共预算支出功能分类预算表",
            "6. 2026年部门政府性基金预算支出功能分类预算表",
            "7. 2026年部门国有资本经营预算支出功能分类预算表",
            "8. 2026年部门一般公共预算基本支出预算表",
            "9. 2026年部门“三公”经费和机关运行经费预算表",
        ]
    )
    body_text = "2026年部门财务收支预算总表\n单位：元"
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[toc_text, body_text],
        page_tables=[[], []],
        filesize=80,
    )

    issues = CMM003_TocCountConsistency().apply(doc)

    assert issues
    assert any("目录与正文表数" in issue.message for issue in issues)


def test_common_code_mirror_rule_detects_amount_mismatch() -> None:
    income_text = "\n".join(
        [
            "2026年部门收入预算总表",
            "201 一般公共服务支出 100",
            "204 公共安全支出 200",
        ]
    )
    expense_text = "\n".join(
        [
            "2026年部门支出预算总表",
            "201 一般公共服务支出 100",
            "204 公共安全支出 250",
        ]
    )
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[income_text, expense_text],
        page_tables=[[], []],
        filesize=70,
    )

    issues = CMM004_CodeMirrorConsistency().apply(doc)

    assert issues
    assert any(
        ("类款项金额不一致" in issue.message) or ("类款项编码" in issue.message)
        for issue in issues
    )


def test_common_comparative_narrative_logic_rule_detects_zero_increase_and_hold_wording() -> None:
    page_text = (
        "\u653f\u5e9c\u6027\u57fa\u91d1\u62e8\u6b3e\u652f\u51fa\u9884\u7b970\u4e07\u5143\uff0c"
        "\u6bd42025\u5e74\u9884\u7b97\u589e\u52a0\u6301\u5e73\u3002"
        "\u4e00\u822c\u516c\u5171\u9884\u7b97\u62e8\u6b3e\u652f\u51fa\u9884\u7b97100\u4e07\u5143\uff0c"
        "\u6bd42025\u5e74\u9884\u7b97\u589e\u52a010\u4e07\u5143\u3002"
        "\u653f\u5e9c\u6027\u57fa\u91d1\u62e8\u6b3e\u652f\u51fa\u9884\u7b970\u4e07\u5143\uff0c"
        "\u6bd42025\u5e74\u9884\u7b97\u589e\u52a043.84\u4e07\u5143\u3002"
    )
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=66,
    )

    issues = CMM005_ComparativeNarrativeLogic().apply(doc)

    assert issues
    assert any("\u589e\u52a0\u6301\u5e73" in issue.message for issue in issues)
    assert any("43.84" in issue.message for issue in issues)


def test_common_income_expense_trend_rule_detects_direction_conflict() -> None:
    page_text = (
        "2026\u5e74\u6536\u5165\u9884\u7b974817.87\u4e07\u5143\uff0c"
        "\u6bd42025\u5e74\u9884\u7b97\u51cf\u5c1177.37\u4e07\u5143\u3002"
        "2026\u5e74\u652f\u51fa\u9884\u7b974817.87\u4e07\u5143\uff0c"
        "\u6bd42025\u5e74\u9884\u7b97\u589e\u52a077.37\u4e07\u5143\u3002"
        "\u8d22\u653f\u62e8\u6b3e\u6536\u5165\u652f\u51fa\u51cf\u5c11\u7684\u4e3b\u8981\u539f\u56e0\u662f\u9879\u76ee\u8c03\u6574\u3002"
    )
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=70,
    )

    issues = CMM006_IncomeExpenseTrendConsistency().apply(doc)

    assert issues
    assert any("\u6536\u5165/\u652f\u51fa\u540c\u6bd4\u65b9\u5411\u77db\u76fe" in issue.message for issue in issues)
    assert any("\u53e3\u5f84\u63cf\u8ff0\u77db\u76fe" in issue.message for issue in issues)
    assert any(
        "\u8fd9\u6bb5\u6587\u5b57\u51fa\u73b0\u4e86\u9519\u8bef" in issue.message
        for issue in issues
    )
    assert any(
        "\u4e3b\u8981\u539f\u56e0\u662f\u9879\u76ee\u8c03\u6574" in (issue.evidence_text or "")
        for issue in issues
    )


def test_common_income_expense_trend_rule_quotes_summary_sentence_with_optional_dir() -> None:
    page_text = (
        "2026\u5e74\u6536\u5165\u9884\u7b977279.80\u4e07\u5143\uff0c"
        "\u6bd42025\u5e74\u9884\u7b97\u51cf\u5c111701.19\u4e07\u5143\u3002"
        "2026\u5e74\u652f\u51fa\u9884\u7b977279.80\u4e07\u5143\uff0c"
        "\u6bd42025\u5e74\u9884\u7b97\u51cf\u5c111701.19\u4e07\u5143\u3002"
        "\u8d22\u653f\u62e8\u6b3e\u6536\u5165\u652f\u51fa\u589e\u52a0\uff08\u51cf\u5c11\uff09"
        "\u7684\u4e3b\u8981\u539f\u56e0\u662f\u57fa\u5efa\u9879\u76ee\u51cf\u5c11\u3002"
    )
    doc = build_document(
        path="unit-2026-budget.pdf",
        page_texts=[page_text],
        page_tables=[[]],
        filesize=70,
    )

    issues = CMM006_IncomeExpenseTrendConsistency().apply(doc)

    assert issues
    assert any(
        "\u8fd9\u6bb5\u6587\u5b57\u51fa\u73b0\u4e86\u9519\u8bef" in issue.message
        and "\u57fa\u5efa\u9879\u76ee\u51cf\u5c11" in issue.message
        for issue in issues
    )
    assert any(
        "\u8d22\u653f\u62e8\u6b3e\u6536\u5165\u652f\u51fa\u589e\u52a0\uff08\u51cf\u5c11\uff09"
        in (issue.evidence_text or "")
        for issue in issues
    )


def test_pipeline_budget_name_mismatch_uses_specific_title_and_suggestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Budget109Only:
        code = "BUD-109"
        desc = "BUD-109"

        def apply(self, _doc):
            return [
                Issue(
                    rule="BUD-109",
                    severity="warn",
                    message="预算编制说明类级科目名称与T5不一致（210）：表格“卫生健康支出”，说明“医疗卫生与计划生育支出”",
                    location={"page": 9, "table": "BUD_T5", "row": "210"},
                )
            ]

    monkeypatch.setattr(pipeline, "ALL_BUDGET_RULES", [_Budget109Only()])
    monkeypatch.setattr(pipeline, "ALL_COMMON_RULES", [])

    doc = build_document(
        path="sample_budget.pdf",
        page_texts=["2026年部门预算"],
        page_tables=[[]],
        filesize=1,
    )

    payload = pipeline.build_issues_payload(doc, report_kind="budget")

    assert payload["issues"]["all"]
    item = payload["issues"]["all"][0]
    assert item["title"] == "预算编制说明功能分类类款项名称与T5不一致"
    assert "T5 一般公共预算支出功能分类预算表" in item["suggestion"]
    assert "第9页" in item["suggestion"]
