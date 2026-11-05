"""同源问题覆盖补核的纯逻辑测试。

红线：
- 只覆盖 `insufficient_data`；
- 只认正式 finding，且必须在当前可见问题集合里；
- 必须同时满足 checker 交集 + 可辨识引号短语命中，泛化词不算。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.services.obligation_finding_coverage import (
    distinctive_terms,
    obligation_covered_by_finding,
)


def _item(
    detail: str = "说明中的款“行政事业单位离退休”在表内找不到同名科目",
    *,
    status: str = "insufficient_data",
    checkers: list[str] | None = None,
    operands: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        obligation_id="OBL-TXT-T5",
        status=status,
        detail=detail,
        checkers=list(checkers or ["V33-222", "V33-227"]),
        missing_operands=list(operands or []),
    )


def _finding(**overrides) -> dict:
    finding = {
        "id": "finding-1",
        "rule_id": "V33-227",
        "tags": ["V33-227"],
        "severity": "medium",
        "title": "说明5款级科目名称与T5不一致",
        "message": "表格“行政事业单位养老支出”，说明“行政事业单位离退休”",
        "location": {
            "actual_name": "行政事业单位离退休",
            "expected_name": "行政事业单位养老支出",
        },
    }
    finding.update(overrides)
    return finding


def _result(*findings: dict) -> dict:
    return {"rule_findings": list(findings)}


def test_same_finding_covers_obligation():
    """U-7：义务含金额子检查（V33-222）时，名称问题（V33-227）不得关闭整条义务。"""
    assert obligation_covered_by_finding(
        _item(), _result(_finding()), visible_issue_ids={"finding-1"}
    ) is False


def test_amount_obligation_without_operands_is_not_closed_by_phrase():
    """R-01：金额义务没有缺数位置时，不得凭同规则+同名短语关闭。"""
    assert obligation_covered_by_finding(
        _item(),
        _result(_finding(rule_id="V33-222", tags=["V33-222"],
                         message="说明5金额与T5不一致（行政事业单位离退休）：表内差额 0.01 万元")),
        visible_issue_ids={"finding-1"},
    ) is False


def test_amount_obligation_with_same_identity_is_closed():
    """正控：同一关系+同一操作数身份+金额结论时仍可关闭。"""
    operands = [{
        "source": "text", "role": "narrative_amount", "page": 8,
        "row_key": "2080502", "column": "决算数", "stage": "决算", "year": 2024,
        "check_relation_id": "V33-222:amount:1",
    }]
    finding = _finding(
        rule_id="V33-222", tags=["V33-222"],
        message="说明5金额与T5不一致（行政事业单位离退休）：表内差额 0.01 万元",
        location={"page": 8, "subject_code": "2080502", "column": "决算数",
                  "year": 2024, "stage": "决算",
                  "check_relation_id": "V33-222:amount:1"},
    )
    assert obligation_covered_by_finding(
        _item(operands=operands), _result(finding), visible_issue_ids={"finding-1"}
    ) is True


def test_same_rule_unit_or_unknown_subcheck_does_not_cover_amount_obligation():
    for finding in (
        _finding(rule_id="V33-222", tags=["V33-222"],
                 message="住房公积金金额单位口径错误：原文444.71元折合0.044471万元"),
        _finding(rule_id="V33-222", tags=["V33-222"], message="说明5科目名称与T5不一致",
                 check_relation_id="V33-222:name"),
        _finding(rule_id="V33-222", tags=["V33-222"], message="一般公共预算支出 100 万元"),
    ):
        assert obligation_covered_by_finding(
            _item(), _result(finding), visible_issue_ids={"finding-1"}
        ) is False


def test_hidden_or_informal_finding_cannot_cover():
    assert obligation_covered_by_finding(
        _item(), _result(_finding()), visible_issue_ids=set()
    ) is False
    assert obligation_covered_by_finding(
        _item(), _result(_finding(severity="manual_review")), visible_issue_ids={"finding-1"}
    ) is False
    assert obligation_covered_by_finding(
        _item(),
        _result(_finding(evidence_status="degraded_missing_evidence")),
        visible_issue_ids={"finding-1"},
    ) is False


def test_different_checker_or_generic_term_does_not_cover():
    assert obligation_covered_by_finding(
        _item(checkers=["V33-106"]), _result(_finding()), visible_issue_ids={"finding-1"}
    ) is False
    assert obligation_covered_by_finding(
        _item(detail="说明中的款“一般公共”在表内找不到同名科目"),
        _result(_finding(message="一般公共预算支出 100 万元")),
        visible_issue_ids={"finding-1"},
    ) is False


def test_only_insufficient_data_is_eligible():
    assert obligation_covered_by_finding(
        _item(status="not_executed"), _result(_finding()), visible_issue_ids={"finding-1"}
    ) is False


def test_distinctive_terms_filters_short_and_generic_phrases():
    assert distinctive_terms("款“行政事业单位离退休”与“一般公共”") == {"行政事业单位离退休"}
    assert distinctive_terms("没有引号短语") == set()
