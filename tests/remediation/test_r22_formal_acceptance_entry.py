"""R2.2 §8.1 正式验收入口：外层门禁 + 单一故障负控的 pytest 覆盖。

入口契约与自测见 ``scripts/formal_acceptance_entry.py`` 模块文档。这里用
同一载荷工厂逐项断言八项控制，保证全量回归持续看住这些门禁行为。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

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


def test_dict_shaped_result_issues_counted_without_all_projection():
    """真实生产 dict 形态（uploads 实测）：error/warn/info 读全，all 投影不双计。"""
    payload = _payload(rule_findings=None, check_receipts=[])
    payload["result"]["issues"] = {
        "error": [{"id": "V33-109-1", "rule": "V33-109", "message": "m", "severity": "error"}],
        "warn": [], "info": [],
        "all": [{"id": "V33-109-1", "rule": "V33-109", "message": "m", "severity": "error"}],
    }
    result = entry.compare_payloads(payload, json.loads(canonical(payload)), _contract())
    assert result["verdict"] == "compared"
    assert result["snapshot_after"]["formal_total_deduped"] == 1
    carrier = result["snapshot_after"]["formal_carriers"]["result.issues"]
    assert carrier["dict_format"] is True
    assert carrier["projection_keys_excluded"] == ["all"]


def test_evidence_loss_registered_not_zero():
    before = _payload()
    after = json.loads(canonical(before))
    after["result"]["meta"].pop("check_receipts", None)
    after["review_report"].pop("check_receipts", None)
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "compared"
    lost_kinds = {(item["carrier"], item["copy"], item["kind"]) for item in result["evidence_lost"]}
    assert ("check_receipts", "result.meta", "missing_key") in lost_kinds
    assert ("check_receipts", "review_report", "missing_key") in lost_kinds
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
    lost_kinds = {(item["carrier"], item["copy"], item["kind"]) for item in result["evidence_lost"]}
    assert ("obligation_coverage", "result.meta", "missing_key") in lost_kinds
    assert result["zero_regression_claim"] is False


# ---- cross_generation 模式（gold33：main 基线 vs 候选） ------------------------


def _cross_contract():
    return {
        "contract_version": entry.CONTRACT_VERSION,
        "mode": "cross_generation",
        "expected_before": {
            "material": {"checksum_sha256": "a" * 64},
            "generation": {"analysis_revision": "rev-main-1"},
            "code_identity": {"rule_registry_version": "obligations-v12-checklist-global-20261022",
                               "rule_policy_version": "v3_3_portable"},
            "config_identity": {"use_local_rules": True, "use_ai_assist": False, "mode": "standard"},
        },
        "expected_after": {
            "material": {"checksum_sha256": "a" * 64},
            "generation": {"analysis_revision": "rev-cand-9"},
            "code_identity": {"rule_registry_version": "obligations-v12-checklist-global-20261022",
                               "rule_policy_version": "v3_3_portable"},
            "config_identity": {"use_local_rules": True, "use_ai_assist": False, "mode": "standard"},
        },
        "require_same_material": True,
        # FA-01：解析代际不声明必须有书面依据（不能靠省略豁免）
        "parse_generation_basis": {
            "generation.parse_run_id": "offline_replay_without_db",
            "generation.document_version_id": "offline_replay_without_db",
        },
        "git_commit_declared": None,
    }


def test_cross_generation_compares_across_revisions_same_material():
    before = _payload(analysis_revision="rev-main-1")
    after = _payload(analysis_revision="rev-cand-9")
    result = entry.compare_payloads(before, after, _cross_contract())
    assert result["verdict"] == "compared"
    assert result["mode"] == "cross_generation"
    assert result["gates"]["before"]["verdict"] == "PASS"
    assert result["gates"]["after"]["verdict"] == "PASS"


def test_cross_generation_rejects_wrong_material_even_with_valid_sides():
    before = _payload(analysis_revision="rev-main-1")
    after = _payload(analysis_revision="rev-cand-9", checksum="b" * 64)
    result = entry.compare_payloads(before, after, _cross_contract())
    assert result["verdict"] == "rejected"
    assert any(f["gate"] == "material.cross_side" for f in result["gates"]["after"]["gate_failures"])
    assert "snapshot_after" not in result


def test_cross_generation_rejects_side_from_wrong_generation():
    before = _payload(analysis_revision="rev-main-1")
    after = _payload(analysis_revision="rev-unexpected")  # 不等于 expected_after
    result = entry.compare_payloads(before, after, _cross_contract())
    assert result["verdict"] == "rejected"
    failures = {(f["gate"], f["kind"]) for f in result["gates"]["after"]["gate_failures"]}
    assert ("generation.analysis_revision", "identity_mismatch") in failures


def test_falsy_mode_and_optional_fields_rejected_not_defaulted():
    """假值 mode/optional_fields 必须类型拒绝，不得 falsy 短路按缺省放行。"""
    payload = _payload()
    for bad_mode in ([], 0, False, "", None):
        contract = _contract()
        contract["mode"] = bad_mode
        result = entry.compare_payloads(payload, json.loads(canonical(payload)), contract)
        assert result["verdict"] == "rejected", f"mode={bad_mode!r}"
        assert result["reject_reason"] == "contract_invalid"
    for bad_optional in ("", 0, False, None, {}):
        result = entry.compare_payloads(payload, json.loads(canonical(payload)),
                                        _contract(optional_fields=bad_optional))
        assert result["verdict"] == "rejected", f"optional_fields={bad_optional!r}"
        assert result["reject_reason"] == "contract_invalid"


# ---- M-01/02/03：合入 main 后独立核查的三组边界 ------------------------------


def test_mandatory_identity_cannot_be_marked_optional():
    """M-01：必选身份字段进 optional_fields 即契约拒绝，载荷缺失也不得放行。"""
    for field in ("material.checksum_sha256", "generation.analysis_revision",
                  "code.rule_registry_version", "config.mode"):
        contract = _contract(optional_fields=[field])
        payload = _payload()
        result = entry.compare_payloads(payload, json.loads(canonical(payload)), contract)
        assert result["verdict"] == "rejected", field
        assert result["reject_reason"] == "contract_invalid"
        assert any(e["kind"] == "mandatory_field_marked_optional" for e in result["contract_errors"])


def _payload_with_fact_evidence():
    payload = _payload()
    fact = [{"rule_id": "V33-X", "sub_check_id": "s1", "status": "pass", "formula": "10≤10",
             "operand_fact_ids": ["f1", "f2"], "operand_sources": {"left": [{"physical_page": 1}]}}]
    payload["result"]["meta"]["fact_receipts"] = json.loads(canonical(fact))
    payload["review_report"]["fact_receipts"] = json.loads(canonical(fact))
    return payload


def test_same_relation_evidence_degradation_registered():
    """M-02：关系ID保留但 operand 证据非空→空/缺失，须登记且不得零回归。"""
    before = _payload_with_fact_evidence()
    after = json.loads(canonical(before))
    for container in (after["result"]["meta"], after["review_report"]):
        fr = container["fact_receipts"][0]
        fr["operand_fact_ids"] = []
        fr["operand_sources"] = {}
        fr["formula"] = None
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "compared"
    degraded = [x for x in result["evidence_lost"] if x["kind"] == "same_relation_evidence_degraded"]
    assert degraded
    assert set(degraded[0]["degraded_relations"][0]["degraded_fields"]) == {
        "operand_fact_ids", "operand_sources", "formula"}
    assert result["zero_regression_claim"] is False


def test_same_relation_sources_key_removed_registered():
    """M-02：同关系 operand_sources 键被删除也登记。"""
    before = _payload_with_fact_evidence()
    after = json.loads(canonical(before))
    for container in (after["result"]["meta"], after["review_report"]):
        container["fact_receipts"][0].pop("operand_sources")
    result = entry.compare_payloads(before, after, _contract())
    degraded = [x for x in result["evidence_lost"] if x["kind"] == "same_relation_evidence_degraded"]
    assert degraded
    assert "operand_sources" in degraded[0]["degraded_relations"][0]["degraded_fields"]
    assert result["zero_regression_claim"] is False


def test_same_relation_diagnostic_addition_not_loss():
    """M-02 正控：同关系新增诊断字段是合法演进，不得误报证据丢失。"""
    before = _payload_with_fact_evidence()
    after = json.loads(canonical(before))
    for container in (after["result"]["meta"], after["review_report"]):
        container["fact_receipts"][0]["dh02_structured"] = {"new": "演进"}
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "compared"
    assert not result["evidence_lost"]
    assert result["zero_regression_claim"] is True


def test_extra_issue_in_all_projection_flagged():
    """M-03：all 含严重度桶之外的问题 → 双向不一致，不得签零回归。"""
    issue1 = {"id": "F1", "rule": "V33-X", "message": "m", "severity": "error", "page": 1}
    issue2 = {"id": "F2", "rule": "V33-Y", "message": "n", "severity": "error", "page": 2}
    before = _payload(rule_findings=[], report_issues=[], check_receipts=[])
    before["result"]["issues"] = {"error": [issue1], "warn": [], "info": [], "all": [issue1]}
    after = json.loads(canonical(before))
    after["result"]["issues"]["all"].append(issue2)
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "pending_review"
    assert result["zero_regression_claim"] is False


def test_damaged_carrier_not_masked_by_empty_carriers():
    """M-03：已损坏投影不能被其他空列表载体豁免（readable_any 仍 True 也待核）。"""
    issue2 = {"id": "F2", "rule": "V33-Y", "message": "n", "severity": "error", "page": 2}
    before = _payload(rule_findings=[], report_issues=[], check_receipts=[])
    before["result"]["issues"] = {"error": [], "warn": [], "info": [], "all": []}
    after = json.loads(canonical(before))
    after["result"]["issues"] = {"all": [issue2]}
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "pending_review"
    assert result["pending_reason"] == "formal_carrier_damaged"
    assert result["zero_regression_claim"] is False
    assert result["damaged_carriers"]["after"] == ["result.issues"]


@pytest.mark.parametrize("path", [
    ("result", "rule_findings"), ("result", "issues"), ("review_report", "issues"),
])
@pytest.mark.parametrize("bad_value", [None, 42, False, "broken"])
def test_present_invalid_carrier_not_masked_by_empty_carriers(path, bad_value):
    """全链验证：组件类型诊断必须到达入口，其他合法空载体不能代为担保。"""
    before = _payload(rule_findings=[], issues=[], report_issues=[], check_receipts=[])
    after = json.loads(canonical(before))
    after[path[0]][path[1]] = bad_value
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "pending_review"
    assert result["zero_regression_claim"] is False
    assert result["damaged_carriers"]["after"] == [".".join(path)]
    carrier = result["snapshot_after"]["formal_carriers"][".".join(path)]
    assert carrier["legal_empty"] is False
    if bad_value is not None:
        assert carrier["non_list"] is True


@pytest.mark.parametrize("carrier", ["result", "review_report"])
@pytest.mark.parametrize("bucket", ["error", "warn", "info", "all"])
@pytest.mark.parametrize("bad_value", [None, 42, "broken"])
def test_invalid_severity_bucket_not_treated_as_empty(carrier, bucket, bad_value):
    before = _payload(rule_findings=[], issues=[], report_issues=[], check_receipts=[])
    after = json.loads(canonical(before))
    after[carrier]["issues"] = {"error": [], "warn": [], "info": [], "all": []}
    after[carrier]["issues"][bucket] = bad_value
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "pending_review"
    assert result["zero_regression_claim"] is False
    state = result["snapshot_after"]["formal_carriers"][f"{carrier}.issues"]
    assert state["invalid_bucket_keys"] == [bucket]
    assert state["legal_empty"] is False


@pytest.mark.parametrize("path", [
    ("result", "rule_findings"), ("result", "issues"), ("review_report", "issues"),
])
def test_non_object_finding_items_are_not_silently_skipped(path):
    before = _payload(rule_findings=[], issues=[], report_issues=[], check_receipts=[])
    after = json.loads(canonical(before))
    after[path[0]][path[1]] = ["broken", 42, None]
    result = entry.compare_payloads(before, after, _contract())
    assert result["verdict"] == "pending_review"
    assert result["zero_regression_claim"] is False
    state = result["snapshot_after"]["formal_carriers"][".".join(path)]
    assert state["invalid_item_indices"] == [0, 1, 2]


def test_missing_legacy_carrier_remains_distinct_from_invalid_present_carrier():
    payload = _payload(rule_findings=[], issues=[], report_issues=[], check_receipts=[])
    payload["result"].pop("rule_findings")
    result = entry.compare_payloads(payload, json.loads(canonical(payload)), _contract())
    assert result["verdict"] == "compared"
    assert result["zero_regression_claim"] is True
    assert result["carrier_readability"]["after"]["damaged"] == []
