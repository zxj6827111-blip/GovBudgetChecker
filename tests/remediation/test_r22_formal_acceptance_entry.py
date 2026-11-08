"""R2.2 §8.1 正式验收入口：外层门禁 + 单一故障负控的 pytest 覆盖。

入口契约与自测见 ``scripts/formal_acceptance_entry.py`` 模块文档。这里用
同一载荷工厂逐项断言八项控制，保证全量回归持续看住这些门禁行为。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "r22_formal_acceptance_entry",
    ROOT / "scripts" / "formal_acceptance_entry.py",
)
entry = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(entry)

canonical = entry._canonical


def _payload(**overrides):
    """入口自带的载荷工厂，仅透传覆盖项。"""
    return entry._payload(
        checksum=overrides.get("checksum", "a" * 64),
        analysis_revision=overrides.get("analysis_revision", "rev-test-1"),
        rule_findings=overrides.get("rule_findings",
                                    [{"id": "F-1", "rule": "V33-X", "message": "m", "page": 8}]),
        issues=overrides.get("issues"),
        report_issues=overrides.get("report_issues"),
        check_receipts=overrides.get(
            "check_receipts", [{"rule_id": "V33-X", "status": "fail", "issue_refs": ["F-1"]}]),
    )


def _contract(**overrides):
    contract = entry._contract()
    if "optional_fields" in overrides:
        contract["optional_fields"] = overrides["optional_fields"]
    for key, value in overrides.items():
        if "__" in key:
            section, field = key.split("__", 1)
            contract["expected"][section][field] = value
    return contract


def test_positive_control_matches_and_compares():
    payload = _payload()
    result = entry.compare_payloads(payload, json.loads(canonical(payload)), _contract())
    assert result["verdict"] == "compared"
    assert result["zero_regression_claim"] is True
    assert result["gates"]["before"]["verdict"] == "PASS"
    assert result["gates"]["after"]["verdict"] == "PASS"


def test_wrong_material_rejected():
    wrong = _payload(checksum="b" * 64)
    result = entry.compare_payloads(_payload(), wrong, _contract())
    assert result["verdict"] == "rejected"
    failures = {(f["gate"], f["kind"]) for f in result["gates"]["after"]["gate_failures"]}
    assert ("material.checksum_sha256", "identity_mismatch") in failures
    assert "snapshot_after" not in result


def test_wrong_generation_rejected():
    wrong = _payload(analysis_revision="rev-other")
    result = entry.compare_payloads(_payload(), wrong, _contract())
    assert result["verdict"] == "rejected"
    failures = {(f["gate"], f["kind"]) for f in result["gates"]["after"]["gate_failures"]}
    assert ("generation.analysis_revision", "identity_mismatch") in failures


def test_legacy_format_without_rule_findings_still_counted():
    """主载体键缺失是合法旧格式：入口不得拒绝，载体并集仍读全。"""
    legacy = _payload(rule_findings=None,
                      report_issues=[{"id": "a", "rule": "V33-Y", "message": "m"},
                                     {"id": "b", "rule": "V33-Y", "message": "n"}],
                      check_receipts=[])
    result = entry.compare_payloads(legacy, json.loads(canonical(legacy)), _contract())
    assert result["verdict"] == "compared"
    assert result["snapshot_after"]["formal_total_deduped"] == 2
    assert result["snapshot_after"]["formal_carriers"]["result.rule_findings"]["missing"] is True


def test_evidence_loss_registered_not_zero():
    before = _payload()
    after = json.loads(canonical(before))
    after["result"]["meta"].pop("check_receipts", None)
    after["review_report"].pop("check_receipts", None)
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "compared"
    assert result["evidence_lost"] == [
        {"carrier": "check_receipts", "before_count": 1, "after": "missing_or_null"}
    ]
    assert result["zero_regression_claim"] is False


def test_wrong_withdrawal_flagged_with_receipt_refs():
    before = _payload()
    after = _payload(rule_findings=[])
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "compared"
    assert result["removed_identities"]["withdrawn_with_evidence"] == ["id:F-1"]
    assert result["zero_regression_claim"] is False


def test_declared_field_missing_in_payload_rejected():
    payload = _payload()
    payload["result"]["meta"].pop("rule_policy_version", None)
    result = entry.compare_payloads(payload, json.loads(canonical(payload)), _contract())
    assert result["verdict"] == "rejected"
    failures = {(f["gate"], f["kind"]) for f in result["gates"]["before"]["gate_failures"]}
    assert ("code.rule_policy_version", "missing_identity_field") in failures


def test_optional_field_missing_tolerated_but_visible():
    payload = _payload()
    payload["structured_ingest"].pop("parse_run_id", None)
    result = entry.compare_payloads(payload, json.loads(canonical(payload)),
                                    _contract(optional_fields=["generation.parse_run_id"]))
    assert result["verdict"] == "compared"
    assert result["gates"]["before"]["optional_missing"] == ["generation.parse_run_id"]


def test_obligation_coverage_generation_gap_is_evidence_too():
    """obligation_coverage 在 after 缺键同样登记 evidence_lost（R2 main 漏读形态之一）。"""
    before = _payload()
    after = json.loads(canonical(before))
    after["result"]["meta"].pop("obligation_coverage", None)
    result = entry.compare_payloads(before, after, _contract())
    assert {"carrier": "obligation_coverage", "before_count": None,
            "after": "missing_or_null"} in result["evidence_lost"]
    assert result["zero_regression_claim"] is False
