"""U-7：同源合并只影响展示，不得关闭/覆盖/隐藏未完成的金额子检查。

覆盖判定、待办、完成门禁与导出块使用同一组函数；本文件逐层验证。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.schemas.material_detail import CoverageObligationItem  # noqa: E402
from src.services.obligation_finding_coverage import obligation_covered_by_finding  # noqa: E402
from src.services.review_lifecycle_service import blocking_coverage_obligations  # noqa: E402


def _item(**overrides) -> CoverageObligationItem:
    base = {
        "obligation_id": "OBL-TXT-T5",
        "group_id": "TABLE_TEXT",
        "title": "一般公共预算支出决算表 与 功能分类说明一致",
        "status": "insufficient_data",
        "reason": "insufficient_data",
        "detail": "说明中的款“行政事业单位离退休”在表内找不到同名科目",
        "checkers": ["V33-106", "V33-222", "V33-227"],
        "blocks_gate": True,
        "legacy_coverage_terms": ["行政事业单位离退休"],
    }
    base.update(overrides)
    return CoverageObligationItem(**base)


def _finding(rule: str = "V33-227", **overrides):
    payload = {
        "id": "finding-1",
        "rule_id": rule,
        "tags": [rule],
        "severity": "medium",
        "source": "rule",
        "message": "表格“行政事业单位养老支出”，说明“行政事业单位离退休”",
    }
    payload.update(overrides)
    return {"rule_findings": [payload]}


def test_name_finding_does_not_close_amount_check():
    item = _item()
    row = _finding("V33-227")
    assert obligation_covered_by_finding(item, row, visible_issue_ids={"finding-1"}) is False
    assert blocking_coverage_obligations([item], row, visible_issue_ids={"finding-1"}) == [item]


def test_unit_finding_does_not_close_amount_check():
    row = _finding("CMM-011")
    assert obligation_covered_by_finding(_item(), row, visible_issue_ids={"finding-1"}) is False


def test_amount_finding_can_still_close_same_amount_check():
    row = _finding("V33-222")
    assert obligation_covered_by_finding(_item(), row, visible_issue_ids={"finding-1"}) is True
    assert blocking_coverage_obligations([_item()], row, visible_issue_ids={"finding-1"}) == []


def test_name_only_obligation_still_covered_by_name_finding():
    item = _item(
        obligation_id="OBL-STRUCT-ORG-NAME",
        checkers=["CMM-017"],
        detail="说明中的“上海市人民政发展研究中心”与规范全称不一致",
        legacy_coverage_terms=["上海市人民政发展研究中心"],
    )
    row = _finding("CMM-017", message="此处“上海市人民政发展研究中心”漏“府”字")
    assert obligation_covered_by_finding(item, row, visible_issue_ids={"finding-1"}) is True


def test_hidden_when_finding_not_visible():
    item = _item()
    row = _finding("V33-222")
    assert obligation_covered_by_finding(item, row, visible_issue_ids=set()) is False


def test_todo_keeps_item_even_if_same_source_hint_exists():
    """同源提示存在时仍进待办：合并展示不等于关闭。"""
    item = _item()
    row = _finding("V33-227")
    blocking = blocking_coverage_obligations([item], row, visible_issue_ids={"finding-1"})
    assert [entry.obligation_id for entry in blocking] == ["OBL-TXT-T5"]


def test_unfinished_export_keeps_amount_obligation():
    from src.schemas.review_report import build_review_report

    ledger = {
        "applicable_total": 1, "completed_total": 0, "not_applicable_total": 0,
        "unresolved_total": 1, "blocking_total": 1, "by_reason": {"insufficient_data": 1},
        "by_group": [], "instances": [dict(_item().model_dump(), reason_label="取数不足")],
    }
    report = build_review_report({
        "check_coverage": ledger,
        "rule_findings": _finding("V33-227")["rule_findings"],
        "issues": {"critical": [], "error": [], "warn": [], "info": [], "tips": []},
    })
    unfinished = [item for item in report["manual_review"]
                  if item.get("record_type") == "unfinished_check"]
    assert [item.get("obligation_id") for item in unfinished] == ["OBL-TXT-T5"]
