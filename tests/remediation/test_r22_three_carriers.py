"""R2.2 §8.1 三载体数据契约（待审工具 scripts/verify_three_carriers.py）。

正式问题载体（IR05 勘误）= result.rule_findings / result.issues /
review_report.issues；check_receipts 与 obligation_coverage 是投影。
契约：主载体保守次序、跨载体身份去重、同 id 异内容冲突以主载体为准、
缺字段（未知）与合法空列表区分、三载体不直接相加；每种正式载体独占
问题都必须被计数。双采一致只证明确定性，不签发零回归。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.verify_three_carriers import _canonical, three_carrier_snapshot

TOOL = Path(__file__).resolve().parents[2] / "scripts" / "verify_three_carriers.py"


def test_missing_key_is_unknown_not_zero():
    snapshot = three_carrier_snapshot({}, {}, {})
    assert all(snapshot["formal_carriers"][key]["missing"] for key in (
        "result.rule_findings", "result.issues", "review_report.issues"))


def test_present_empty_list_is_legal_empty():
    snapshot = three_carrier_snapshot({"rule_findings": []}, {"issues": []}, {"instances": []})
    assert snapshot["formal_carriers"]["result.rule_findings"]["legal_empty"] is True
    assert snapshot["formal_carriers"]["review_report.issues"]["legal_empty"] is True


def test_single_formal_carrier_exclusive_problems_counted():
    """IR05 单载体负控：正式问题只出现在某一载体时必须被计数。"""
    # rule_findings=[] 合法空 + result.issues 独占 1 条
    snapshot = three_carrier_snapshot(
        {"rule_findings": [], "issues": [{"id": "I-1", "rule": "V33-X"}]}, {}, {})
    assert snapshot["formal_total_deduped"] == 1
    assert snapshot["main_carrier"] == "result.issues"
    # review_report.issues 独占 1 条
    snapshot = three_carrier_snapshot({"rule_findings": []}, {"issues": [{"id": "R-1"}]}, {})
    assert snapshot["formal_total_deduped"] == 1


def test_main_carrier_missing_others_still_counted():
    """真实水务局旧任务形态：rule_findings 缺失、review_report.issues=2。"""
    snapshot = three_carrier_snapshot({}, {"issues": [{"id": "a"}, {"id": "b"}]}, {})
    assert snapshot["formal_total_deduped"] == 2
    assert snapshot["formal_carriers"]["result.rule_findings"]["missing"] is True
    assert snapshot["main_carrier"] == "review_report.issues"


def test_cross_carrier_dedupe_and_content_conflict():
    finding = {"id": "F-1", "rule": "V33-X", "message": "m"}
    snapshot = three_carrier_snapshot(
        {"rule_findings": [finding]}, {"issues": [dict(finding)]}, {})
    assert snapshot["formal_total_deduped"] == 1
    assert snapshot["multi_carrier_projection"]["count"] == 1
    snapshot = three_carrier_snapshot(
        {"rule_findings": [finding]},
        {"issues": [{"id": "F-1", "rule": "V33-X", "message": "不同内容"}]}, {})
    assert len(snapshot["conflicts"]) == 1
    assert snapshot["conflicts"][0]["resolution"].startswith("以主载体")


def test_projection_mapping_against_union():
    result = {"rule_findings": [{"id": "F-1", "rule": "V33-X", "message": "m"},
                                {"id": "F-2", "rule": "V33-W", "message": "n"}]}
    report = {"check_receipts": [
        {"rule_id": "V33-X", "status": "fail", "issue_refs": ["F-1"]},
        {"rule_id": "V33-Y", "status": "fail", "issue_refs": ["F-9"]},
    ]}
    snapshot = three_carrier_snapshot(result, report, {})
    assert snapshot["receipt_projection"]["mapped_refs"] == 1
    assert snapshot["receipt_projection"]["projection_only_count"] == 1
    assert set(snapshot["unreferenced_main_findings"]) == {"id:F-2"}
    assert snapshot["no_cross_carrier_sum"] is True


def test_tool_selftest_and_real_record_smoke():
    """工具自测可运行；真实任务记录（只读）扫描不抛错。"""
    assert subprocess.run([sys.executable, str(TOOL), "selftest"],
                          capture_output=True).returncode == 0
    real = Path("/Volumes/workSSD/software development/GovBudgetChecker/uploads/"
                "79efdd8f0d7b3a2938de65cfb5aad101/status.json")
    if not real.exists():
        return  # 真实材料缺失时跳过烟测（CI 无本机数据）
    out = subprocess.run([sys.executable, str(TOOL), "scan", "--record", str(real)],
                         capture_output=True, text=True)
    assert out.returncode == 0
    import json
    snapshot = json.loads(out.stdout)
    assert snapshot["formal_total_deduped"] >= 1
    assert snapshot["no_cross_carrier_sum"] is True


def test_r204_same_id_content_change_is_never_identical():
    """R2-04 控制A（审核方形态）：同 id/rule/page、仅正文金额 10→100 →
    两份快照必须判异；快照保留规范化问题内容，内容差异可见。"""
    issue = {"id": "F1", "rule": "V33-X", "message": "合计10万元与分项8万元不一致", "page": 8}
    snap_before = three_carrier_snapshot({"rule_findings": [issue]}, {}, {})
    snap_after = three_carrier_snapshot(
        {"rule_findings": [{**issue, "message": "合计100万元与分项8万元不一致"}]}, {}, {})
    assert _canonical(snap_before) != _canonical(snap_after)
    content_before = snap_before["formal_findings"][0]["content"]
    content_after = snap_after["formal_findings"][0]["content"]
    assert content_before["message"] != content_after["message"]
    assert content_before["id"] == "F1"  # 确定性 id 保留在内容里参与比较


def test_r204_volatile_uuid_id_dedupes_to_one():
    """R2-04 控制B（审核方形态）：两正式载体承载同一内容、仅易变 id 不同
    （uuid:one/uuid:two）→ 去重计 1；id 作为来源引用另存不丢失。"""
    same = {"id": "uuid:one", "rule": "V33-X", "message": "同一问题", "page": 8}
    snapshot = three_carrier_snapshot(
        {"rule_findings": [same]}, {"issues": [{**same, "id": "uuid:two"}]}, {})
    assert snapshot["formal_total_deduped"] == 1
    assert snapshot["multi_carrier_projection"]["count"] == 1
    assert sorted(snapshot["formal_findings"][0]["sources"]) == ["uuid:one", "uuid:two"]
    # 时间戳同样不进入指纹：同一问题采集两次时间戳不同仍判同
    older = {**same, "id": "uuid:a", "created_at": "2026-11-07T08:00:00"}
    newer = {**same, "id": "uuid:b", "created_at": "2026-11-07T09:00:00"}
    snapshot_ts = three_carrier_snapshot({"rule_findings": [older]}, {}, {})
    snapshot_ts2 = three_carrier_snapshot({"rule_findings": [newer]}, {}, {})
    assert (snapshot_ts["formal_findings"][0]["content_digest"]
            == snapshot_ts2["formal_findings"][0]["content_digest"])
