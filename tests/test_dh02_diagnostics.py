"""DH-02 结构化卡点：字段往返、汇总一致性、根因键与兼容文本。

这些测试只锁定“新增字段”的契约，不改动任何检查结论：
- 回执保存/读回后五个字段不丢失；
- 上游原因必须有阻断项，缺数/多候选必须有位置；
- 义务汇总取子回执并集，根因键来自最上游回执；
- 兼容文本（detail/incomplete_reason）不由本层改写。
"""

from __future__ import annotations

import pytest

from src.engine.diagnostic_fields import (
    REASON_POLICY,
    aggregate,
    canonical,
    choose_next_action,
    fields_for_text,
    finalize,
    record_failure,
    root_cause_key,
    root_signature,
    table_candidate,
    table_missing_operand,
    blocker,
)
from src.schemas.check_receipt import CheckReceipt


def _missing(page=5):
    return table_missing_operand(
        table_code="FIN_03_expenditure", role="决算操作数", page=page,
        row_key="208", column="project_actual", stage="决算", year=2024,
    )


def test_receipt_roundtrip_keeps_dh02_fields():
    receipt = CheckReceipt(
        rule_id="V33-241",
        sub_check_id="OBL-INTERNAL-T3-TOTAL:V33-241",
        reason_codes=["operand_missing"],
        blocked_by=[],
        missing_operands=[_missing()],
        candidates=[],
        next_action="system_recheck",
        root_cause_key="rc:" + "0" * 64,
    )
    again = CheckReceipt.from_dict(receipt.to_dict())
    assert again.reason_codes == ["operand_missing"]
    assert again.missing_operands == receipt.missing_operands
    assert again.next_action == "system_recheck"
    assert again.root_cause_key == receipt.root_cause_key


def test_upstream_reason_without_blocker_is_rejected():
    with pytest.raises(ValueError):
        finalize({"reason_codes": ["row_code_missing"]})


def test_blocker_code_must_appear_in_reason_codes():
    with pytest.raises(ValueError):
        finalize({
            "reason_codes": ["operand_missing"],
            "blocked_by": [blocker(dimension="row", ref="x", reason_code="row_code_missing")],
            "missing_operands": [_missing()],
        })


def test_operand_missing_requires_location():
    with pytest.raises(ValueError):
        finalize({"reason_codes": ["operand_missing"]})


def test_operand_ambiguous_requires_two_distinct_candidates():
    one = table_candidate(table_code="FIN_07_three_public", page=8, cell_id="c1", value="1")
    with pytest.raises(ValueError):
        finalize({
            "reason_codes": ["operand_ambiguous", "cell_unbound"],
            "blocked_by": [blocker(dimension="cell", ref="x", reason_code="cell_unbound")],
            "candidates": [one],
        })
    two = table_candidate(table_code="FIN_07_three_public", page=8, cell_id="c2", value="2")
    result = finalize({
        "reason_codes": ["operand_ambiguous", "cell_unbound"],
        "blocked_by": [blocker(dimension="cell", ref="x", reason_code="cell_unbound")],
        "candidates": [one, two],
    })
    assert result["next_action"] in ("human_confirm", "system_recheck", "tech_fix")


def test_policy_actions_are_consistent_with_frozen_table():
    for code, (_, actions) in REASON_POLICY.items():
        assert choose_next_action([code]) in actions, code


def test_root_key_same_point_same_key_and_different_point_different_key():
    first = {"reason_codes": ["operand_missing"], "missing_operands": [_missing(5)]}
    second = {"reason_codes": ["operand_missing"], "missing_operands": [_missing(5)]}
    third = {"reason_codes": ["operand_missing"], "missing_operands": [_missing(6)]}
    assert root_cause_key(first) == root_cause_key(second)
    assert root_cause_key(first) != root_cause_key(third)


def test_aggregate_is_union_and_key_taken_from_rollup_fields():
    child_a = finalize({"reason_codes": ["operand_missing"], "missing_operands": [_missing(5)]})
    child_b = finalize({"reason_codes": ["operand_missing"], "missing_operands": [_missing(6)]})
    summary = aggregate([child_a, child_b], obligation_id="OBL-X")
    assert sorted(item["page"] for item in summary["missing_operands"]) == [5, 6]
    assert summary["reason_codes"] == ["operand_missing"]
    # 汇总自身签名与承载同一汇总的汇总回执一致（验收 T14 的前提）
    assert summary["root_cause_key"] == root_cause_key(summary)


class _Doc:
    pass


def test_detail_text_is_not_rewritten_by_diagnostics():
    doc = _Doc()
    detail = "total的overseas/决算操作数缺失或不唯一"
    record_failure(doc, detail, {
        "reason_codes": ["operand_missing"],
        "missing_operands": [_missing(9)],
    })
    fields = fields_for_text(doc, detail, rule_id="V33-224", sub_check_id="x:y")
    assert detail == "total的overseas/决算操作数缺失或不唯一"
    assert fields["reason_codes"] == ["operand_missing"]
    assert fields["missing_operands"][0]["page"] == 9
    assert canonical(fields["missing_operands"][0]) == canonical(_missing(9))
