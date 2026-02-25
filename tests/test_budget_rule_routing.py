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
async def test_engine_rule_runner_routes_budget_rules(monkeypatch: pytest.MonkeyPatch) -> None:
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

    findings = await runner.run_rules(job_context=job_context, rules=[], config=AnalysisConfig())
    rule_ids = {item.rule_id for item in findings}

    assert "BUD-DUMMY" in rule_ids
    assert "FIN-DUMMY" not in rule_ids


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
    assert any("\u90e8\u95e8\u8d22\u52a1\u6536\u652f\u9884\u7b97\u603b\u8868" in issue.message for issue in missing_t1)


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
