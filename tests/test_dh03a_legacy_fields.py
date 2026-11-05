"""DH-03a：旧兼容字段（legacy_same_source_keys / legacy_coverage_terms）的冻结算法与透传。

只验证三件事：
1. 算法与 2026-11-01 旧实现逐字一致（不扩大简称/表代号，泛化词不参与）；
2. 每个义务实例（含已完成/不适用）都带两个列表，缺省 []；
3. 字段从台账贯通到覆盖块、复核条目与约定导出块，且不参与关闭/门禁判断。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.check_obligations import (  # noqa: E402
    LEGACY_SAME_SOURCE_KEY,
    ObligationInstance,
    build_obligation_ledger,
    legacy_coverage_terms,
    legacy_same_source_keys,
)
from src.schemas.review_report import build_review_report  # noqa: E402
from src.services.material_detail_query_service import build_coverage  # noqa: E402


def _instance(obligation_id: str, *, title: str = "", detail: str = "") -> ObligationInstance:
    return ObligationInstance(
        obligation_id=obligation_id,
        group_id="STRUCT",
        group_title="结构",
        title=title,
        status="completed",
        detail=detail,
    )


def test_fields_frozen_after_detail_is_filled():
    """detail 由规则摘要落定后再冻结：不能因为实例刚展开时 detail 为空而写 []。"""
    summary = {
        "rule_statuses": {
            "V33-114": "insufficient_data", "V33-109": "insufficient_data",
            "V33-122": "insufficient_data", "CMM-026": "insufficient_data",
        },
        "unresolved_rules": [{
            "rule_id": "V33-109",
            "detail": "“财政拨款收入支出决算总表”读取未完成",
            "reason_codes": [],
        }],
    }
    ledger = build_obligation_ledger(_profile(), report_kind="final",
                                     rule_execution_summary=summary)
    item = next(entry for entry in ledger["instances"]
                if entry["obligation_id"] == "OBL-STRUCT-EMPTY-STATEMENT")
    assert item["status"] == "insufficient_data"
    assert "财政拨款收入支出决算总表" in item["detail"]
    assert item["legacy_same_source_keys"] == [LEGACY_SAME_SOURCE_KEY]
    assert item["legacy_coverage_terms"] == ["财政拨款收入支出决算总表"]


def test_same_source_key_algorithm_is_frozen():
    key = [LEGACY_SAME_SOURCE_KEY]
    assert legacy_same_source_keys(_instance(
        "OBL-STRUCT-EMPTY-STATEMENT", detail="本表与财政拨款收入支出决算总表一致")) == key
    assert legacy_same_source_keys(_instance(
        "OBL-STRUCT-EMPTY-STATEMENT", detail="本表无数据")) == []
    # 不扩大简称：只写“财政拨款收支总”不能命中。
    assert legacy_same_source_keys(_instance(
        "OBL-STRUCT-EMPTY-STATEMENT", detail="财政拨款收支总")) == []
    assert legacy_same_source_keys(_instance(
        "OBL-CROSS-FISCAL-TOTAL", title="财政拨款收支总表 与 支出决算表一致")) == key
    assert legacy_same_source_keys(_instance(
        "OBL-CROSS-FISCAL-TOTAL", detail="与财政拨款收入支出决算总表核对")) == key
    assert legacy_same_source_keys(_instance(
        "OBL-CROSS-FISCAL-TOTAL", detail="与收入决算表核对")) == []
    assert legacy_same_source_keys(_instance(
        "OBL-TXT-T3", detail="提到财政拨款收入支出决算总表也不算")) == []
    # 唯一合法键。
    assert LEGACY_SAME_SOURCE_KEY == "FIN_04_fiscal_grant_total"


def test_coverage_terms_follow_old_regex():
    assert legacy_coverage_terms('说明里“行政事业单位离退休”与表内“行政事业单位养老支出”不一致') == [
        "行政事业单位养老支出", "行政事业单位离退休"]
    # 少于 4 字、泛化词、无引号短语都不算。
    assert legacy_coverage_terms('“一般公共预算”“合计”“经费”') == []
    assert legacy_coverage_terms("没有任何引号短语") == []
    # 去重与排序。
    assert legacy_coverage_terms('“行政事业单位养老支出”“行政事业单位养老支出”') == [
        "行政事业单位养老支出"]


def test_every_instance_carries_lists():
    ledger = build_obligation_ledger(_profile(), report_kind="final")
    instances = ledger["instances"]
    assert instances
    for item in instances:
        assert isinstance(item["legacy_same_source_keys"], list)
        assert isinstance(item["legacy_coverage_terms"], list)
        assert all(isinstance(value, str) for value in item["legacy_same_source_keys"])
        assert all(isinstance(value, str) for value in item["legacy_coverage_terms"])
        if item["obligation_id"] not in {"OBL-STRUCT-EMPTY-STATEMENT", "OBL-CROSS-FISCAL-TOTAL"}:
            assert item["legacy_same_source_keys"] == []


def test_fields_transport_to_coverage_block():
    ledger = build_obligation_ledger(_profile(), report_kind="final")
    coverage = build_coverage(ledger)
    assert coverage.available
    by_oid = {item.obligation_id: item for item in coverage.items}
    for item in ledger["instances"]:
        covered = by_oid[item["obligation_id"]]
        assert covered.legacy_same_source_keys == item["legacy_same_source_keys"]
        assert covered.legacy_coverage_terms == item["legacy_coverage_terms"]


def test_unfinished_check_export_carries_fields():
    ledger = build_obligation_ledger(_profile(), report_kind="final")
    report = build_review_report({
        "check_coverage": ledger,
        "rule_findings": [],
        "issues": {"critical": [], "error": [], "warn": [], "info": [], "tips": []},
    })
    unfinished = [record for record in report["manual_review"]
                  if record.get("record_type") == "unfinished_check"]
    assert unfinished, "决算画像应至少有一条未完成检查"
    for record in unfinished:
        assert isinstance(record["legacy_same_source_keys"], list)
        assert isinstance(record["legacy_coverage_terms"], list)


def test_fields_are_inert_for_coverage_gate():
    """两个字段只作提示：同一条义务改变列表值不改变是否被问题覆盖的判断。"""
    from src.services.obligation_finding_coverage import obligation_covered_by_finding

    ledger = build_obligation_ledger(_profile(), report_kind="final")
    target = next(item for item in ledger["instances"]
                  if item["obligation_id"] == "OBL-STRUCT-EMPTY-STATEMENT")
    coverage_item = next(item for item in build_coverage(ledger).items
                         if item.obligation_id == target["obligation_id"])
    result_row = {"rule_findings": []}
    baseline = obligation_covered_by_finding(coverage_item, result_row, visible_issue_ids=set())
    coverage_item.legacy_same_source_keys = ["FIN_04_fiscal_grant_total"]
    coverage_item.legacy_coverage_terms = ["任意短语"]
    assert obligation_covered_by_finding(coverage_item, result_row, visible_issue_ids=set()) == baseline


def _profile():
    from src.services.document_profile_resolver import resolve_document_profile

    profile = resolve_document_profile(
        doc_type="dept_final",
        filename="2024年度部门决算.pdf",
        page_texts=["2024年度部门决算"],
    )
    assert profile.kind == "final"
    return profile


# ---------------------------------------------------------------------------
# DH-03a 消费方迁移：同源覆盖只读冻结短语列表，[] 不回退 detail
# ---------------------------------------------------------------------------
from types import SimpleNamespace

from src.services.obligation_finding_coverage import obligation_covered_by_finding


def _finding_row():
    return {
        "rule_findings": [
            {
                "id": "finding-1",
                "rule_id": "V33-227",
                "tags": ["V33-227"],
                "severity": "low",
                "title": "说明款级科目名称不一致",
                "message": "表格“行政事业单位养老支出”，说明“行政事业单位离退休”",
                "location": {"actual_name": "行政事业单位离退休"},
            }
        ]
    }


def _item(**overrides):
    # DH-03a 只验证“冻结短语 vs detail 回退”的消费路径，用非金额名称义务；
    # 金额义务无操作数必须保持未完成由 R-01 测试单独固定。
    base = {
        "status": "insufficient_data",
        "checkers": ["V33-227"],
        "detail": "无引号原文",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_consumer_reads_frozen_terms_instead_of_detail():
    item = _item(legacy_coverage_terms=["行政事业单位离退休"])
    assert obligation_covered_by_finding(item, _finding_row()) is True


def test_empty_frozen_terms_do_not_fall_back_to_detail():
    item = _item(
        detail="说明中的款“行政事业单位离退休”在表内找不到同名科目",
        legacy_coverage_terms=[],
    )
    assert obligation_covered_by_finding(item, _finding_row()) is False


def test_missing_field_keeps_historical_detail_parsing():
    item = _item(detail="说明中的款“行政事业单位离退休”在表内找不到同名科目")
    assert obligation_covered_by_finding(item, _finding_row()) is True
