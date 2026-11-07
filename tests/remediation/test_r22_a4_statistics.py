"""R2.2 附页 A4：三维度统计契约测试（两个统计样例必须可复现）。"""
from __future__ import annotations

from src.services.remediation_statistics import (
    ATTR_NEW_FINDING,
    ATTR_RECOVERED_THIS_ROUND,
    ATTR_RESOLVED_BEFORE_THIS_PLAN,
    ATTR_TECH_UNRESOLVED,
    ENGINE_COMPLETED_WITH_FINDINGS,
    ENGINE_UNKNOWN,
    MANUAL_UNPROCESSED,
    MANUAL_VERIFIED_OK,
    aggregate_three_dimensions,
    derive_attribution,
    engine_from_instance,
)


def test_sample_one_recovered_then_reported_counts_both():
    """样例1：恢复后报错 → 引擎=完成且发现问题，归因=恢复+新发现问题，
    人工=未处理 → 同时计入自动恢复与自动发现问题。"""
    before = {"status": "insufficient_data"}
    after = {"status": "completed", "findings_count": 1}
    attributions = derive_attribution(before, after)
    assert attributions == ["recovered_this_round", "new_finding"]
    report = aggregate_three_dimensions([
        {"relation_id": "R-X", "status": "completed", "findings_count": 1,
         "attributions": attributions},
    ], manual_decisions={})
    assert report["engine"][ENGINE_COMPLETED_WITH_FINDINGS] == 1
    assert report["attribution"]["recovered_this_round"] == 1
    assert report["attribution"]["new_finding"] == 1
    assert report["manual"][MANUAL_UNPROCESSED] == 1


def test_sample_two_unknown_but_manually_processed_keeps_gap_visible():
    """样例2：仍未知但人工已处理 → 引擎=未知、归因=技术仍未解、人工=verified_ok
    同时计数——自动化缺口不被人工处理掩盖。"""
    before = {"status": "insufficient_data"}
    after = {"status": "insufficient_data"}
    attributions = derive_attribution(before, after)
    assert attributions == ["tech_unresolved"]
    report = aggregate_three_dimensions([
        {"relation_id": "R-Y", "status": "insufficient_data", "attributions": attributions},
    ], manual_decisions={"R-Y": "verified_ok"})
    assert report["engine"][ENGINE_UNKNOWN] == 1
    assert report["attribution"]["tech_unresolved"] == 1
    assert report["manual"]["verified_ok"] == 1


def test_manual_processing_never_mutates_engine_dimension():
    """人工处理后引擎维度不变（读人工操作前的引擎结果）。"""
    relations = [
        {"relation_id": "R-1", "status": "insufficient_data"},
        {"relation_id": "R-2", "status": "completed"},
    ]
    untouched = aggregate_three_dimensions(relations)
    processed = aggregate_three_dimensions(
        relations, manual_decisions={"R-1": "verified_ok", "R-2": "verified_issue"})
    assert untouched["engine"] == processed["engine"]
    assert processed["manual"]["verified_ok"] == 1
    assert processed["manual"]["verified_issue"] == 1


def test_completed_in_baseline_is_resolved_before_this_plan():
    """main 已恢复的成果不计入本轮自动恢复量。"""
    assert derive_attribution({"status": "completed"}, {"status": "completed"}) == [
        ATTR_RESOLVED_BEFORE_THIS_PLAN]


def test_engine_dimension_mapping_from_instance():
    """引擎维度映射：completed 按 findings 二分；not_applicable/未知/未执行各归其位。"""
    assert engine_from_instance({"status": "completed"}) == "completed_pass"
    assert engine_from_instance({"status": "completed", "findings_count": 2}) == "completed_with_findings"
    assert engine_from_instance({"status": "not_applicable"}) == "not_applicable"
    assert engine_from_instance({"status": "insufficient_data"}) == "unknown"
    assert engine_from_instance({"status": "not_executed"}) == "not_executed"
    assert engine_from_instance({}) == "unknown"
