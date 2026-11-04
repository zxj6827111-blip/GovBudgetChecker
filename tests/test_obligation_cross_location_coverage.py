"""J-01：金额义务的覆盖必须由同一关系/同一处事实证明。

红线（与独立复核意见一致）：
- 名称/单位问题、无身份的文字相似不得关闭金额义务；
- 同页不同关系、不同页、不同表实例、不同年度/主体都不得关闭；
- 旧结果缺少完整身份时保持未完成，但展示/导出仍可正常读回；
- 部分缺数位置没有逐处证明时，整条义务保持未完成。

同时验证身份字段从引擎回执 → 义务汇总 → API 投影 → 导出贯通。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.diagnostic_fields import (  # noqa: E402
    aggregate,
    fields_for_text,
    record_gap,
    text_missing_operand,
)
from src.schemas.material_detail import CoverageObligationItem  # noqa: E402
from src.services.obligation_finding_coverage import obligation_covered_by_finding  # noqa: E402
from src.services.review_lifecycle_service import blocking_coverage_obligations  # noqa: E402

RELATION = "V33-222:general:text:item:123"


def _obligation(**overrides) -> CoverageObligationItem:
    base = {
        "obligation_id": "OBL-TXT-T5",
        "group_id": "TABLE_TEXT",
        "title": "一般公共预算支出决算表 与 功能分类说明一致",
        "status": "insufficient_data",
        "reason": "insufficient_data",
        "detail": "第8页说明中的“住房公积金”决算金额无法绑定",
        "checkers": ["V33-222"],
        "blocks_gate": True,
        "legacy_coverage_terms": ["住房公积金"],
        "missing_operands": [{
            "source": "text", "section": "支出决算具体情况说明",
            "role": "narrative_amount", "page": 8,
            "row_key": "2210201", "column": "决算数",
            "stage": "决算", "year": 2024,
            "check_relation_id": RELATION,
        }],
    }
    base.update(overrides)
    return CoverageObligationItem(**base)


def _amount_finding(relation: str = RELATION, **location) -> dict:
    payload = {
        "id": "finding-amount",
        "rule_id": "V33-222",
        "tags": ["V33-222"],
        "severity": "error",
        "source": "rule",
        "message": "住房公积金表文金额不一致：说明100万元，表内90万元，相差10万元。",
        "location": {"page": 8, "subject_code": "2210201", "column": "决算数",
                     "table_code": "FIN_05_general_public_expenditure",
                     "check_relation_id": relation, **location},
    }
    return {"rule_findings": [payload]}


def _visible(result: dict) -> set:
    return {finding["id"] for finding in result["rule_findings"]}


def test_same_relation_amount_finding_covers():
    result = _amount_finding()
    assert obligation_covered_by_finding(_obligation(), result, _visible(result)) is True


def test_same_relation_binds_cross_table_narrative_operand():
    """跨表比较合法时按同一关系绑定双方：说明页与表格页不同也可覆盖。"""
    result = _amount_finding(page=8, table_page=3)
    assert obligation_covered_by_finding(_obligation(), result, _visible(result)) is True


def test_other_page_and_table_instance_cannot_close():
    result = _amount_finding(relation="", page=2, table_instance_id="T5-other")
    assert obligation_covered_by_finding(_obligation(), result, _visible(result)) is False


def test_different_relation_same_page_row_cannot_close():
    result = _amount_finding(relation="V33-222:general:text:item:999")
    assert obligation_covered_by_finding(_obligation(), result, _visible(result)) is False


def test_different_row_same_page_cannot_close():
    result = _amount_finding(relation="", page=8, row="2210202")
    assert obligation_covered_by_finding(_obligation(), result, _visible(result)) is False


def test_different_year_or_subject_cannot_close():
    item = _obligation(missing_operands=[{
        "source": "table", "table_code": "FIN_05_general_public_expenditure",
        "role": "total_actual", "page": 5, "row_key": "2210201",
        "column": "决算数", "stage": "决算", "year": 2024, "org_name": "某区财政局",
    }])
    other_year = _amount_finding(
        relation="", page=5, row="2210201", table="FIN_05_general_public_expenditure",
        year=2023, org_name="某区财政局",
    )
    assert obligation_covered_by_finding(item, other_year, _visible(other_year)) is False
    other_subject = _amount_finding(
        relation="", page=5, row="2210201", table="FIN_05_general_public_expenditure",
        year=2024, org_name="另一区财政局",
    )
    assert obligation_covered_by_finding(item, other_subject, _visible(other_subject)) is False


def test_finding_without_identity_cannot_close_identity_obligation():
    result = _amount_finding(relation="")
    result["rule_findings"][0]["location"] = {}
    assert obligation_covered_by_finding(_obligation(), result, _visible(result)) is False


def test_obligation_without_relation_cannot_use_relation_bearing_finding():
    """问题声明了关系、义务没有：无法证明同关系，不得关闭。"""
    item = _obligation(missing_operands=[{
        "source": "text", "role": "narrative_amount", "page": 8,
        "row_key": "2210201", "column": "决算数",
    }])
    result = _amount_finding()
    assert obligation_covered_by_finding(item, result, _visible(result)) is False


def test_legacy_obligation_without_operands_keeps_old_matching():
    """历史结果没有缺数位置：维持冻结短语匹配（旧审核断言不改）。"""
    item = _obligation(missing_operands=[])
    result = _amount_finding()
    assert obligation_covered_by_finding(item, result, _visible(result)) is True


def test_operand_without_any_location_stays_unfinished():
    """旧结果缺少完整身份：保持未完成，但可正常读回。"""
    item = _obligation(missing_operands=[{
        "source": "text", "role": "narrative_amount",
    }])
    result = _amount_finding()
    assert obligation_covered_by_finding(item, result, _visible(result)) is False
    assert item.missing_operands[0]["role"] == "narrative_amount"


def test_partial_coverage_of_one_operand_is_not_complete():
    item = _obligation(
        detail="“住房公积金”“公务用车运行维护费”决算金额无法绑定",
        legacy_coverage_terms=["住房公积金", "公务用车运行维护费"],
        missing_operands=[
            {"source": "text", "role": "narrative_amount", "page": 8,
             "row_key": "2210201", "column": "决算数", "check_relation_id": RELATION},
            {"source": "text", "role": "narrative_amount", "page": 9,
             "row_key": "2210203", "column": "决算数",
             "check_relation_id": "V33-222:general:text:item:456"},
        ],
    )
    first = _amount_finding()
    assert obligation_covered_by_finding(item, first, _visible(first)) is False

    both = {"rule_findings": [
        first["rule_findings"][0],
        {
            "id": "finding-amount-2", "rule_id": "V33-222", "tags": ["V33-222"],
            "severity": "error", "source": "rule",
            "message": "公务用车运行维护费表文金额不一致：相差0.5万元。",
            "location": {"page": 9, "subject_code": "2210203", "column": "决算数",
                         "check_relation_id": "V33-222:general:text:item:456"},
        },
    ]}
    assert obligation_covered_by_finding(item, both, _visible(both)) is True


def test_unit_error_alone_does_not_hide_separate_amount_difference():
    unit_only = {"rule_findings": [{
        "id": "finding-unit", "rule_id": "V33-222", "tags": ["V33-222"],
        "severity": "error", "source": "rule",
        "message": "住房公积金金额单位口径错误：原文444.71元折合0.044471万元，表内444.71万元。",
        "location": {"page": 8, "column": "决算数", "check_relation_id": RELATION},
    }]}
    assert obligation_covered_by_finding(_obligation(), unit_only, _visible(unit_only)) is False

    with_independent = {"rule_findings": (
        unit_only["rule_findings"]
        + _amount_finding()["rule_findings"]
    )}
    assert obligation_covered_by_finding(
        _obligation(), with_independent, _visible(with_independent)
    ) is True


def test_blocking_list_keeps_cross_location_obligation():
    item = _obligation()
    result = _amount_finding(relation="", page=2, table_instance_id="T5-other")
    blocking = blocking_coverage_obligations([item], result, visible_issue_ids=_visible(result))
    assert blocking == [item]


def test_receipt_obligation_api_export_keep_relation_identity():
    """引擎回执 → 义务汇总 → API 投影 → 导出块都保留关系身份。"""
    doc = SimpleNamespace()
    text = "说明中的“住房公积金”决算金额无法绑定"
    record_gap(
        doc,
        text,
        reason_codes=["operand_missing"],
        missing_operands=[text_missing_operand(
            section="支出决算具体情况说明", role="narrative_amount", page=8,
            row_key="2210201", column="决算数", stage="决算", year=2024,
        )],
    )
    fields = fields_for_text(doc, text, rule_id="V33-222",
                             sub_check_id="general:text:item:123")
    operand = fields["missing_operands"][0]
    assert operand["check_relation_id"] == RELATION
    assert operand["sub_check_id"] == "general:text:item:123"

    summary = aggregate([fields], obligation_id="OBL-TXT-T5")
    assert summary["missing_operands"][0]["check_relation_id"] == RELATION

    item = CoverageObligationItem(
        obligation_id="OBL-TXT-T5",
        status="insufficient_data",
        detail=text,
        checkers=["V33-222"],
        blocks_gate=True,
        legacy_coverage_terms=["住房公积金"],
        missing_operands=list(summary["missing_operands"]),
    )
    result = _amount_finding()
    assert obligation_covered_by_finding(item, result, _visible(result)) is True

    from src.schemas.review_report import build_review_report

    ledger = {
        "applicable_total": 1, "completed_total": 0, "not_applicable_total": 0,
        "unresolved_total": 1, "blocking_total": 1,
        "by_reason": {"insufficient_data": 1}, "by_group": [],
        "instances": [dict(item.model_dump(), reason_label="取数不足")],
    }
    report = build_review_report({
        "check_coverage": ledger,
        "rule_findings": result["rule_findings"],
        "issues": {"critical": [], "error": [], "warn": [], "info": [], "tips": []},
    })
    exported = next(
        row for row in report["manual_review"]
        if row.get("record_type") == "unfinished_check"
    )
    assert exported["missing_operands"][0]["check_relation_id"] == RELATION
