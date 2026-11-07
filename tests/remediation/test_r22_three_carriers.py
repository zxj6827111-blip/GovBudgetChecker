"""R2.2 §8.1 三载体数据契约（待审工具 scripts/verify_three_carriers.py）。

契约：主载体=rule_findings；投影=check_receipts/obligation_coverage；
投影命中去重、fail-缺主载体冲突、缺字段（未知）与合法空列表区分、
三载体不直接相加。双采一致只证明确定性，不签发零回归。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.verify_three_carriers import three_carrier_snapshot

TOOL = Path(__file__).resolve().parents[2] / "scripts" / "verify_three_carriers.py"


def test_missing_key_is_unknown_not_zero():
    snapshot = three_carrier_snapshot({}, {}, {})
    assert snapshot["main_carrier"]["missing"] is True
    assert snapshot["main_carrier"]["count"] is None
    assert snapshot["main_carrier"]["legal_empty"] is False


def test_present_empty_list_is_legal_empty():
    snapshot = three_carrier_snapshot({"rule_findings": []}, {"check_receipts": []}, {"instances": []})
    assert snapshot["main_carrier"]["legal_empty"] is True
    assert snapshot["main_carrier"]["count"] == 0


def test_projection_refs_dedupe_against_main():
    result = {"rule_findings": [{"id": "F-1", "rule": "V33-X", "message": "m"}]}
    report = {"check_receipts": [
        {"rule_id": "V33-X", "status": "fail", "issue_refs": ["F-1"]},
        {"rule_id": "V33-W", "status": "fail", "issue_refs": ["F-1"]},
        {"rule_id": "V33-Y", "status": "fail", "issue_refs": ["F-9"]},
    ]}
    snapshot = three_carrier_snapshot(result, report, {})
    assert snapshot["receipt_projection"]["mapped_refs"] == 2
    assert snapshot["receipt_projection"]["projection_only_count"] == 1
    # V33-X/V33-W 的 fail 都有主载体依据（F-1）；V33-Y 的 F-9 主载体缺席 → 冲突。
    assert [c["rule_id"] for c in snapshot["conflicts"]] == ["V33-Y"]


def test_fail_without_main_finding_is_conflict_resolved_by_main():
    result = {"rule_findings": [{"id": "F-1", "rule": "V33-X", "message": "m"}]}
    report = {"check_receipts": [
        {"rule_id": "V33-Y", "status": "fail", "issue_refs": ["F-9"]},
        {"rule_id": "V33-Z", "status": "pass", "issue_refs": []},
    ]}
    snapshot = three_carrier_snapshot(result, report, {})
    assert len(snapshot["conflicts"]) == 1
    assert snapshot["conflicts"][0]["rule_id"] == "V33-Y"
    assert snapshot["conflicts"][0]["resolution"].startswith("以主载体为准")


def test_main_finding_without_receipt_reference_is_projection_gap():
    result = {"rule_findings": [{"id": "F-1", "rule": "V33-X", "message": "m"}]}
    snapshot = three_carrier_snapshot(result, {"check_receipts": []}, {})
    assert snapshot["unreferenced_main_findings"] == ["id:F-1"]
    assert snapshot["conflicts"] == []


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
    assert snapshot["main_carrier"]["present"] is True
    assert snapshot["no_cross_carrier_sum"] is True
