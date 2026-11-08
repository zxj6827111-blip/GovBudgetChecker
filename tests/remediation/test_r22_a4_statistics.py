"""R2.2 附页 A4：三维度统计契约测试（两个统计样例必须可复现）。"""
from __future__ import annotations

import pathlib

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


def test_r205_degradation_surfaces_new_finding():
    """R2-05：before 已完成、after 退化出现 finding → resolved_before 不得
    单独吞掉退化，新发现同时入账；未退化时维持 resolved_before 单归因。"""
    degraded = derive_attribution({"status": "completed"},
                                  {"status": "completed", "findings_count": 1})
    assert degraded == [ATTR_RESOLVED_BEFORE_THIS_PLAN, ATTR_NEW_FINDING]
    stable = derive_attribution({"status": "completed"}, {"status": "completed"})
    assert stable == [ATTR_RESOLVED_BEFORE_THIS_PLAN]
    # before/after 都带 finding：问题持续存在，不是本轮新发现（不虚增）
    persistent = derive_attribution({"status": "completed", "has_confirmed_findings": True},
                                    {"status": "completed", "findings_count": 1})
    assert persistent == [ATTR_RESOLVED_BEFORE_THIS_PLAN]


def test_r205_after_record_missing_never_borrows_before_pass(tmp_path):
    """R2-05（审核方控制复刻）：before 关系 pass、after 关系记录缺失 →
    当前引擎维度=未知（不得用 (a or b) 沿用旧 pass），归因保留
    resolved_before + record_missing；before 历史状态在关系明细单列。"""
    import importlib.util
    import json

    script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "phase0_r22_target_relations.py"
    spec = importlib.util.spec_from_file_location("r22_probe_module", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    before = {"head_commit": "before", "targets": [{"job_uuid": "same-job", "rules": {
        "V33-X": {"status": "pass",
                  "relations": [{"relation_id": "r1", "status": "pass"}]}}}]}
    after = {"head_commit": "after", "targets": [{"job_uuid": "same-job", "rules": {
        "V33-X": {"status": "insufficient_data", "relations": []}}}]}
    before_path, after_path = tmp_path / "b.json", tmp_path / "a.json"
    before_path.write_text(json.dumps(before), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")
    out = tmp_path / "stats.json"
    mod._run_relations_stats(str(before_path), str(after_path), str(out))
    stats = json.loads(out.read_text(encoding="utf-8"))
    # 引擎维度：记录缺失=未知，绝不再显示 completed_pass=1
    assert stats["engine"]["completed_pass"] == 0
    assert stats["engine"]["unknown"] == 1
    # 归因：before 历史状态与 record_missing 并存
    assert stats["attribution"]["record_missing"] == 1
    assert stats["attribution"]["resolved_before_this_plan"] == 1
    assert stats["attribution"]["recovered_this_round"] == 0
    rel = stats["relations"][0]
    assert rel["after_record_missing"] is True
    assert rel["before_status"] == "pass"
    assert rel["engine_dimension"] == "unknown"


def test_r205_recovery_quality_splits_by_evidence_nature(tmp_path):
    """恢复质量分账：独立金额恢复只认 real_amount_comparison；适用性判断
    与未标注性质单列，不冒计独立金额核对。"""
    import importlib.util
    import json

    script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "phase0_r22_target_relations.py"
    spec = importlib.util.spec_from_file_location("r22_probe_module_q", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    before = {"head_commit": "before", "targets": [{"job_uuid": "j", "rules": {
        "V33-X": {"status": "insufficient_data", "relations": [
            {"relation_id": "r1", "status": "insufficient_data"},
            {"relation_id": "r2", "status": "insufficient_data"}]}}}]}
    after = {"head_commit": "after", "targets": [{"job_uuid": "j", "rules": {
        "V33-X": {"status": "pass", "relations": [
            {"relation_id": "r1", "status": "pass", "evidence_nature": "real_amount_comparison"},
            {"relation_id": "r2", "status": "not_applicable",
             "evidence_nature": "applicability_judgment"}]}}}]}
    b, a = tmp_path / "b.json", tmp_path / "a.json"
    b.write_text(json.dumps(before), encoding="utf-8")
    a.write_text(json.dumps(after), encoding="utf-8")
    out = tmp_path / "stats.json"
    mod._run_relations_stats(str(b), str(a), str(out))
    stats = json.loads(out.read_text(encoding="utf-8"))
    assert stats["attribution"]["recovered_this_round"] == 2
    q = stats["recovery_quality"]
    assert q["independent_amount_comparison"] == 1
    assert q["applicability_judgment"] == 1
    assert q["unspecified"] == 0


def test_r302_before_done_after_unknown_keeps_current_gap_reason():
    """R3-02：before 关系 pass、after 同一关系 unknown → 归因不得只有
    resolved_before——当前未解（tech_unresolved）必须同时入账。"""
    attributions = derive_attribution({"status": "completed"},
                                      {"status": "insufficient_data"})
    assert ATTR_RESOLVED_BEFORE_THIS_PLAN in attributions
    assert ATTR_TECH_UNRESOLVED in attributions


def test_r302_parent_rule_pass_never_completes_new_subrelation(tmp_path):
    """R3-02（审核方真实形态）：before 无子回执、父规则曾 pass、after 子关系
    unknown → 归因=仍未解+纠正旧误通过，不得标 resolved_before；
    after 完成时历史标签语义不变。"""
    import importlib.util
    import json

    script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "phase0_r22_target_relations.py"
    spec = importlib.util.spec_from_file_location("r3_probe_module", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    before = {"head_commit": "before", "targets": [{"job_uuid": "j", "rules": {
        "V33-X": {"status": "pass", "relations": []}}}]}
    after = {"head_commit": "after", "targets": [{"job_uuid": "j", "rules": {
        "V33-X": {"status": "insufficient_data", "relations": [
            {"relation_id": "r1", "status": "insufficient_data"}]}}}]}
    b, a = tmp_path / "b.json", tmp_path / "a.json"
    b.write_text(json.dumps(before), encoding="utf-8")
    a.write_text(json.dumps(after), encoding="utf-8")
    out = tmp_path / "stats.json"
    mod._run_relations_stats(str(b), str(a), str(out))
    stats = json.loads(out.read_text(encoding="utf-8"))
    rel = stats["relations"][0]
    assert rel["engine_dimension"] == "unknown"
    assert "corrected_previous_skip" in rel["attributions"]
    assert "tech_unresolved" in rel["attributions"]
    assert "resolved_before_this_plan" not in rel["attributions"]
    assert stats["attribution"]["corrected_previous_skip"] == 1

    # after 完成（pass）时：main 规则级完成 + after 完成 → resolved_before 不变
    after_done = {"head_commit": "after", "targets": [{"job_uuid": "j", "rules": {
        "V33-X": {"status": "pass", "relations": [
            {"relation_id": "r1", "status": "pass"}]}}}]}
    a2 = tmp_path / "a2.json"
    a2.write_text(json.dumps(after_done), encoding="utf-8")
    mod._run_relations_stats(str(b), str(a2), str(out))
    stats2 = json.loads(out.read_text(encoding="utf-8"))
    assert stats2["relations"][0]["attributions"] == ["resolved_before_this_plan"]


def test_r302_no_real_unknown_left_with_historical_only_label(tmp_path):
    """R3-02 收口判据：真实交付统计里不得再有 engine=unknown 且归因仅为
    resolved_before 的关系（审核方 actual_unknown_historical_only 控制对应）。"""
    import importlib.util
    import json

    script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "phase0_r22_target_relations.py"
    spec = importlib.util.spec_from_file_location("r3_probe_module2", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    before = {"head_commit": "before", "targets": [{"job_uuid": "j", "rules": {
        "V33-X": {"status": "pass", "relations": [
            {"relation_id": "r1", "status": "pass"}]}}}]}
    after = {"head_commit": "after", "targets": [{"job_uuid": "j", "rules": {
        "V33-X": {"status": "insufficient_data", "relations": [
            {"relation_id": "r1", "status": "insufficient_data"}]}}}]}
    b, a = tmp_path / "b.json", tmp_path / "a.json"
    b.write_text(json.dumps(before), encoding="utf-8")
    a.write_text(json.dumps(after), encoding="utf-8")
    out = tmp_path / "stats.json"
    mod._run_relations_stats(str(b), str(a), str(out))
    stats = json.loads(out.read_text(encoding="utf-8"))
    offenders = [r for r in stats["relations"]
                 if r["engine_dimension"] == "unknown"
                 and r["attributions"] == ["resolved_before_this_plan"]]
    assert offenders == []
