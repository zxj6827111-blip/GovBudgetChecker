from __future__ import annotations

import multiprocessing
import signal
import time

import pytest

from src.engine.rules_v33 import Issue, build_document
from src.services.rule_process import RuleExecutionTimeout, run_rules_in_process


class SlowRule:
    code = "V33-001"

    def apply(self, doc):
        doc.fact_receipts = [{"rule_id": self.code, "sub_check_id": "unfinished", "status": "pass"}]
        doc.delivery_suppression_ledger = [{"rule_id": "V33-113", "reason": "half-written"}]
        time.sleep(5)
        return []


class SubsequentRule:
    code = "V33-113"

    def apply(self, doc):
        assert not getattr(doc, "fact_receipts", [])
        assert not getattr(doc, "delivery_suppression_ledger", [])
        return [Issue(rule=self.code, severity="warn", message="标点异常",
                      location={"page": 1}, evidence_text="。。")]


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="requires POSIX rule worker deadline")
async def test_one_slow_rule_does_not_discard_later_findings():
    doc = build_document(path="material.pdf", page_texts=["公开材料。。"], page_tables=[[]], filesize=10)
    result = await run_rules_in_process(doc, False, "final", 3, [SlowRule(), SubsequentRule()])
    summary = result["rule_execution_summary"]
    assert summary["rule_statuses"] == {"V33-001": "execution_error", "V33-113": "fail"}
    assert summary["execution_error"] == 1
    timeout, = summary["unresolved_rules"]
    assert timeout["rule_id"] == "V33-001" and "1 秒" in timeout["detail"]
    assert [finding["rule_id"] for finding in result["issues"]["all"]] == ["V33-113"]
    assert not result.get("fact_receipts")
    assert not result.get("delivery_suppressions")


@pytest.mark.asyncio
async def test_rule_process_returns_unknown_manual_review() -> None:
    doc = build_document(
        path="material.pdf",
        page_texts=["公开材料"],
        page_tables=[[]],
        filesize=10,
    )

    payload = await run_rules_in_process(doc, False, "unknown", 30)

    issues = payload["issues"]["all"]
    assert any(item["rule_id"] == "DOC-TYPE-UNKNOWN" for item in issues)
    assert not any(item["rule_id"].startswith("FIN-") for item in issues)


@pytest.mark.asyncio
async def test_rule_process_timeout_terminates_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = build_document(
        path="material.pdf",
        page_texts=["公开材料"],
        page_tables=[[]],
        filesize=10,
    )
    # Force the worker to block well past the deadline so the timeout path is
    # exercised deterministically regardless of host OS or start method
    # (fork on Linux would otherwise finish a trivial document instantly).
    monkeypatch.setenv("RULES_PROCESS_TEST_DELAY_SECONDS", "10")
    started = time.monotonic()

    with pytest.raises(RuleExecutionTimeout):
        await run_rules_in_process(doc, False, "unknown", 0.2)

    assert time.monotonic() - started < 8
    assert not any(
        child.name == "govbudget-rule-evaluator" and child.is_alive()
        for child in multiprocessing.active_children()
    )
