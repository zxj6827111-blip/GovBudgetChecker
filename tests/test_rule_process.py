from __future__ import annotations

import multiprocessing
import time

import pytest

from src.engine.rules_v33 import build_document
from src.services.rule_process import RuleExecutionTimeout, run_rules_in_process


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
async def test_rule_process_timeout_terminates_child() -> None:
    doc = build_document(
        path="material.pdf",
        page_texts=["公开材料"],
        page_tables=[[]],
        filesize=10,
    )
    started = time.monotonic()

    with pytest.raises(RuleExecutionTimeout):
        await run_rules_in_process(doc, False, "unknown", 0.01)

    assert time.monotonic() - started < 5
    assert not any(
        child.name == "govbudget-rule-evaluator" and child.is_alive()
        for child in multiprocessing.active_children()
    )
