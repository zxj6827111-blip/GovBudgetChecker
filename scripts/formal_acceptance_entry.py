#!/usr/bin/env python3
"""R2.2 §8.1 正式验收入口：外层身份门禁 + 三载体比较（待独立核验——核验通过前不得签发验收结论）。

定位与边界
==========
本入口是 R2.2 计划 §8.1 "可信验收入口" 的**外层门禁层**。内层问题载体
统计组件（``scripts/verify_three_carriers.py``）保持为组件复用：三正式
问题载体读取、身份去重、内容判异、冲突登记、缺字段 vs 合法空列表的
纪律全部由组件实现，本入口不重写、不绕过。

本入口补齐的是组件明确不做的事：**材料 / 代际 / 代码 / 配置身份门禁**。
只有全部门禁 PASS，比较输出才产生；任何门禁失败都输出结构化拒绝
（含观察值 vs 期望值），绝不在身份不明时输出问题载体比较结论。

输入契约（比较双方各一份原始任务状态 JSON + 一份门禁契约）
==========================================================

状态 JSON 身份字段（从真实 status.json 载荷提取；缺失与 null 区分）：

- 材料：``checksum``（顶层或 status.checksum，源 PDF sha256）
- 分析代际：``analysis_revision``（顶层或 status.analysis_revision）
- 解析代际：``result.meta.structured_ingest.parse_run_id`` 与
  ``.document_version_id``（status.structured_ingest 为旧位置，两者都试）
- 代码/配置身份：``result.meta.rule_registry_version`` /
  ``result.meta.rule_policy_version`` / 顶层 ``use_local_rules`` /
  ``use_ai_assist`` / ``mode``

契约 JSON（本入口实现的唯一事实来源）::

    {
      "contract_version": "r22-formal-acceptance-v1",
      "expected": {
        "material":  {"checksum_sha256": "<64hex>"},
        "generation": {"analysis_revision": "<rev>",
                        "parse_run_id": "<id 或 null>",
                        "document_version_id": <int 或 null>},
        "code_identity": {"rule_registry_version": "<str>",
                           "rule_policy_version": "<str>"},
        "config_identity": {"use_local_rules": true,
                             "use_ai_assist": false, "mode": "<str>"}
      },
      "optional_fields": ["parse_run_id", "document_version_id"],
      "git_commit_declared": "<40hex，声明值；载荷不携带，不冒称已核验>"
    }

门禁语义（缺字段不当空、不当匹配）：

1. 契约声明的每个身份字段，载荷里**必须存在**（键缺失 = REJECT
   ``missing_identity_field``）；值为 null 时按可选/必选分别处理。
2. ``optional_fields`` 列出的字段：载荷键缺失 → 登记 ``optional_missing``，
   门禁放行但输出可见；载荷键存在则必须与契约相等。
3. 其余字段：键缺失即拒绝；键存在且与契约不等即拒绝（材料/代际/配置错）。
4. ``git_commit_declared`` 是**声明值**：载荷不携带代码身份，入口只在
   ``--verify-code`` 给出代码树时用 ``git rev-parse HEAD`` 复核；不给时
   输出里标记 ``declared_not_verified``，不得对外宣称"已核验代码身份"。

比较输出（两侧门禁全 PASS 才产生）：

- 两侧各自的三载体快照（组件原样输出：去重并集、multi_carrier、冲突、
  投影对接、missing/legal_empty）。
- ``added_identities`` / ``removed_identities``：并集身份差集。
- ``removed_identities`` 中，若 after 侧回执 ``issue_refs`` 仍引用该确定性
  id → ``withdrawn_with_evidence``（疑似错误撤销，显式登记）；无任何残留
  引用 → ``removed_unexplained``。两种都不计入"无变化"。
- ``evidence_lost``：check_receipts / obligation_coverage / fact_receipts
  在 before 存在、after 缺键或 null → 显式登记（不得当 0、不得当空）。
- 结论字段只有三态：``compared``（门禁全过，比较已产出） /
  ``rejected``（任一门禁失败，附 per-gate 明细） /
  ``rejected_selftest``（自测模式）。不存在"身份不明但已比较"的第四态。

单一故障负控（``selftest``，与 QP-06 负控范围一一对应）：

- 正控：身份全匹配 + 合法双载 → compared。
- 错材料：契约 checksum 与载荷不符 → rejected(material_mismatch)。
- 错代际：analysis_revision 不符 → rejected(generation_mismatch)。
- 真实格式漏读：rule_findings 键缺失而 review_report.issues 有 2 条 →
  门禁照常、载体并集仍计数 2（漏读由组件自测另行覆盖，这里验证入口
  不因主载体缺失而拒绝合法旧格式）。
- 证据丢失：after 缺 check_receipts 键 → evidence_lost 登记，verdict 不是
  "零回归"。
- 错误撤销：before 有 F-1，after 移除 F-1 但回执仍引用 →
  withdrawn_with_evidence 登记。
- 缺字段不当空：契约声明 rule_policy_version 而载荷缺键 → rejected
  （missing_identity_field），不得当匹配。

纪律：本入口 selftest 通过只证明实现自洽；对真实材料的正式验收结论
仍须独立核验方复核后签发。双采一致仅证明确定性，不构成独立验收。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONTRACT_VERSION = "r22-formal-acceptance-v1"
#: 证据载体（投影）：before 存在而 after 缺键/null 时登记 evidence_lost。
_EVIDENCE_CARRIERS = ("check_receipts", "obligation_coverage", "fact_receipts")

_COMPONENT_PATH = Path(__file__).resolve().parent / "verify_three_carriers.py"


def _load_component():
    spec = importlib.util.spec_from_file_location("r22_verify_three_carriers", _COMPONENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


component = _load_component()
_three_carrier_snapshot = component.three_carrier_snapshot
_canonical = component._canonical


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---- 载荷身份提取（缺失与 null 严格区分） ------------------------------------


def _identity_value(*containers: Any, keys: Tuple[str, ...]) -> Tuple[bool, Any]:
    """按序在容器里找第一个出现的键；返回 (present, value)。"""
    for container in containers:
        if isinstance(container, dict):
            for key in keys:
                if key in container:
                    return True, container.get(key)
    return False, None


def extract_payload_identity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """从原始任务状态 JSON 提取门禁身份字段（present 缺失明确登记）。"""
    status = payload.get("status") if isinstance(payload.get("status"), dict) else payload
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    ingest = meta.get("structured_ingest") if isinstance(meta.get("structured_ingest"), dict) else {}
    legacy_ingest = status.get("structured_ingest") if isinstance(status.get("structured_ingest"), dict) else {}
    identity: Dict[str, Any] = {}
    present, value = _identity_value(status, keys=("checksum",))
    identity["material.checksum_sha256"] = {"present": present, "value": str(value) if present else None}
    present, value = _identity_value(status, keys=("analysis_revision",))
    identity["generation.analysis_revision"] = {"present": present, "value": str(value) if present else None}
    present, value = _identity_value(ingest, legacy_ingest, keys=("parse_run_id", "parse_run_uid"))
    identity["generation.parse_run_id"] = {"present": present, "value": value if present else None}
    present, value = _identity_value(ingest, legacy_ingest, keys=("document_version_id",))
    identity["generation.document_version_id"] = {"present": present, "value": value if present else None}
    present, value = _identity_value(meta, keys=("rule_registry_version",))
    identity["code.rule_registry_version"] = {"present": present, "value": str(value) if present else None}
    present, value = _identity_value(meta, keys=("rule_policy_version",))
    identity["code.rule_policy_version"] = {"present": present, "value": str(value) if present else None}
    present, value = _identity_value(status, keys=("use_local_rules",))
    identity["config.use_local_rules"] = {"present": present, "value": value if present else None}
    present, value = _identity_value(status, keys=("use_ai_assist",))
    identity["config.use_ai_assist"] = {"present": present, "value": value if present else None}
    present, value = _identity_value(status, keys=("mode",))
    identity["config.mode"] = {"present": present, "value": str(value) if present else None}
    return identity


def _flat_expected(contract: Dict[str, Any]) -> Dict[str, Any]:
    """契约 expected 树展平成与 extract_payload_identity 相同的键名。"""
    expected = contract.get("expected") if isinstance(contract.get("expected"), dict) else {}
    material = expected.get("material") if isinstance(expected.get("material"), dict) else {}
    generation = expected.get("generation") if isinstance(expected.get("generation"), dict) else {}
    code = expected.get("code_identity") if isinstance(expected.get("code_identity"), dict) else {}
    config = expected.get("config_identity") if isinstance(expected.get("config_identity"), dict) else {}
    flat: Dict[str, Any] = {}
    if "checksum_sha256" in material:
        flat["material.checksum_sha256"] = material.get("checksum_sha256")
    if "analysis_revision" in generation:
        flat["generation.analysis_revision"] = generation.get("analysis_revision")
    if "parse_run_id" in generation:
        flat["generation.parse_run_id"] = generation.get("parse_run_id")
    if "document_version_id" in generation:
        flat["generation.document_version_id"] = generation.get("document_version_id")
    if "rule_registry_version" in code:
        flat["code.rule_registry_version"] = code.get("rule_registry_version")
    if "rule_policy_version" in code:
        flat["code.rule_policy_version"] = code.get("rule_policy_version")
    if "use_local_rules" in config:
        flat["config.use_local_rules"] = config.get("use_local_rules")
    if "use_ai_assist" in config:
        flat["config.use_ai_assist"] = config.get("use_ai_assist")
    if "mode" in config:
        flat["config.mode"] = config.get("mode")
    return flat


def _git_head(code_tree: Optional[Path]) -> Tuple[bool, Optional[str]]:
    if code_tree is None:
        return False, None
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(code_tree),
            capture_output=True, text=True, timeout=30, check=True,
        )
        return True, completed.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return False, None


# ---- 门禁 -------------------------------------------------------------------


def check_gates(
    payload: Dict[str, Any],
    contract: Dict[str, Any],
    *,
    side: str,
    verify_code_tree: Optional[Path] = None,
) -> Dict[str, Any]:
    """对单侧载荷执行全部身份门禁；输出结构化裁决，绝不静默放行。"""
    identity = extract_payload_identity(payload)
    expected = _flat_expected(contract)
    optional = set(contract.get("optional_fields") or [])
    observed = {key: entry.get("value") for key, entry in identity.items()}
    gate_failures: List[Dict[str, Any]] = []
    optional_missing: List[str] = []
    for key, want in sorted(expected.items()):
        entry = identity.get(key)
        if entry is None:
            gate_failures.append({"gate": key, "kind": "undeclared_key", "expected": want})
            continue
        if not entry["present"]:
            if key in optional:
                optional_missing.append(key)
                continue
            gate_failures.append({
                "gate": key, "kind": "missing_identity_field",
                "expected": want, "observed": None,
                "note": "契约声明该字段而载荷缺键：缺字段不当空、不当匹配",
            })
            continue
        got = entry["value"]
        if _canonical(got) != _canonical(want):
            gate_failures.append({
                "gate": key, "kind": "identity_mismatch",
                "expected": want, "observed": got,
            })
    git_declared = contract.get("git_commit_declared")
    git_verified: Any = None
    if git_declared:
        ok, head = _git_head(verify_code_tree)
        if verify_code_tree is None:
            git_verified = "declared_not_verified"
        elif ok and head == git_declared:
            git_verified = "verified_match"
        else:
            git_verified = "verified_mismatch"
            gate_failures.append({
                "gate": "code.git_commit", "kind": "identity_mismatch",
                "expected": git_declared, "observed": head,
            })
    return {
        "side": side,
        "verdict": "PASS" if not gate_failures else "REJECT",
        "observed_identity": observed,
        "optional_missing": optional_missing,
        "gate_failures": gate_failures,
        "git_commit": git_verified,
    }


# ---- 载体提取与证据丢失登记 --------------------------------------------------


def _evidence_state(status: Dict[str, Any]) -> Dict[str, Any]:
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    states: Dict[str, Any] = {}
    for key in _EVIDENCE_CARRIERS:
        found: Tuple[bool, Any] = (False, None)
        for container in (report, meta):
            if isinstance(container, dict) and key in container:
                found = (True, container.get(key))
                break
        present, value = found
        states[key] = {
            "present": present,
            "null_value": present and value is None,
            "count": len(value) if present and isinstance(value, list) else None,
        }
    return states


def _receipt_refs(status: Dict[str, Any]) -> set:
    refs: set = set()
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    for container in (report, meta):
        if not isinstance(container, dict):
            continue
        for receipt in container.get("check_receipts") or []:
            if isinstance(receipt, dict):
                refs.update(str(ref) for ref in (receipt.get("issue_refs") or []))
    return refs


def _status(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload.get("status") if isinstance(payload.get("status"), dict) else payload


def compare_payloads(
    before: Dict[str, Any],
    after: Dict[str, Any],
    contract: Dict[str, Any],
    *,
    verify_code_tree: Optional[Path] = None,
) -> Dict[str, Any]:
    """门禁全过才比较；任何拒绝都返回 rejected 且不含比较结论。"""
    gate_before = check_gates(before, contract, side="before", verify_code_tree=verify_code_tree)
    gate_after = check_gates(after, contract, side="after", verify_code_tree=verify_code_tree)
    gates = {"before": gate_before, "after": gate_after}
    if gate_before["verdict"] != "PASS" or gate_after["verdict"] != "PASS":
        return {
            "entry": "r22-formal-acceptance",
            "contract_version": CONTRACT_VERSION,
            "verdict": "rejected",
            "gates": gates,
            "statement": "身份门禁未全过：不产生问题载体比较结论。",
        }

    status_before, status_after = _status(before), _status(after)
    result_before = status_before.get("result") if isinstance(status_before.get("result"), dict) else {}
    result_after = status_after.get("result") if isinstance(status_after.get("result"), dict) else {}
    report_before = status_before.get("review_report") if isinstance(status_before.get("review_report"), dict) else {}
    report_after = status_after.get("review_report") if isinstance(status_after.get("review_report"), dict) else {}
    coverage_before = result_before.get("meta", {}).get("obligation_coverage") if isinstance(result_before.get("meta"), dict) else {}
    coverage_after = result_after.get("meta", {}).get("obligation_coverage") if isinstance(result_after.get("meta"), dict) else {}
    snap_before = _three_carrier_snapshot(result_before, report_before, coverage_before)
    snap_after = _three_carrier_snapshot(result_after, report_after, coverage_after)

    ids_before = {f["identity"] for f in snap_before["formal_findings"]}
    ids_after = {f["identity"] for f in snap_after["formal_findings"]}
    added = sorted(ids_after - ids_before)
    removed = sorted(ids_before - ids_after)
    receipt_refs_after = _receipt_refs(status_after)
    withdrawn_with_evidence = sorted(
        identity for identity in removed
        if identity.startswith("id:") and identity[3:] in receipt_refs_after
    )
    removed_unexplained = sorted(set(removed) - set(withdrawn_with_evidence))

    evidence_before, evidence_after = _evidence_state(status_before), _evidence_state(status_after)
    evidence_lost: List[Dict[str, Any]] = []
    for key in _EVIDENCE_CARRIERS:
        was = evidence_before[key]
        now = evidence_after[key]
        if (was["present"] and not was["null_value"]) and (not now["present"] or now["null_value"]):
            evidence_lost.append({"carrier": key, "before_count": was["count"], "after": "missing_or_null"})

    content_changed = sorted(
        identity
        for identity in ids_before & ids_after
        if _canonical(next(f["content"] for f in snap_before["formal_findings"] if f["identity"] == identity))
        != _canonical(next(f["content"] for f in snap_after["formal_findings"] if f["identity"] == identity))
    )

    zero_regression_claim = (
        not added and not removed and not content_changed and not evidence_lost and not snap_after["conflicts"]
    )
    return {
        "entry": "r22-formal-acceptance",
        "contract_version": CONTRACT_VERSION,
        "verdict": "compared",
        "gates": gates,
        "snapshot_before": snap_before,
        "snapshot_after": snap_after,
        "added_identities": added,
        "removed_identities": {
            "withdrawn_with_evidence": withdrawn_with_evidence,
            "removed_unexplained": removed_unexplained,
        },
        "content_changed_identities": content_changed,
        "evidence_lost": evidence_lost,
        "zero_regression_claim": zero_regression_claim,
        "statement": (
            "门禁全过，比较已产出。removed/withdrawn/evidence_lost 均为显式登记，"
            "不计入无变化；zero_regression_claim 只描述本次比较的载体层观察，"
            "不构成独立验收结论。"
        ),
    }


# ---- 载荷工厂（selftest 与 pytest 共用） -------------------------------------


def _payload(
    *,
    checksum: str = "a" * 64,
    analysis_revision: str = "rev-test-1",
    parse_run_id: Optional[str] = "parse-1",
    document_version_id: Any = 7,
    rule_registry_version: str = "obligations-v12-checklist-global-20261022",
    rule_policy_version: str = "v3_3_portable",
    use_local_rules: bool = True,
    use_ai_assist: bool = False,
    mode: str = "standard",
    rule_findings: Optional[List[Dict[str, Any]]] = None,
    issues: Optional[List[Dict[str, Any]]] = None,
    report_issues: Optional[List[Dict[str, Any]]] = None,
    check_receipts: Optional[List[Dict[str, Any]]] = None,
    with_evidence: bool = True,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "meta": {
            "obligation_coverage": {"instances": [], "applicable_total": 0, "completed_total": 0,
                                     "not_applicable_total": 0, "unresolved_total": 0, "blocking_total": 0},
            "rule_registry_version": rule_registry_version,
            "rule_policy_version": rule_policy_version,
        },
    }
    if rule_findings is not None:
        result["rule_findings"] = rule_findings
    if issues is not None:
        result["issues"] = issues
    if with_evidence and check_receipts is not None:
        result["meta"]["check_receipts"] = check_receipts
    report: Dict[str, Any] = {}
    if report_issues is not None:
        report["issues"] = report_issues
    if with_evidence and check_receipts is not None:
        report["check_receipts"] = check_receipts
    payload: Dict[str, Any] = {
        "checksum": checksum,
        "analysis_revision": analysis_revision,
        "use_local_rules": use_local_rules,
        "use_ai_assist": use_ai_assist,
        "mode": mode,
        "result": result,
        "review_report": report,
    }
    ingest: Dict[str, Any] = {"document_version_id": document_version_id}
    if parse_run_id is not None:
        ingest["parse_run_id"] = parse_run_id
    payload["structured_ingest"] = ingest
    return payload


def _contract(checksum: str = "a" * 64, analysis_revision: str = "rev-test-1") -> Dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "expected": {
            "material": {"checksum_sha256": checksum},
            "generation": {"analysis_revision": analysis_revision,
                            "parse_run_id": "parse-1", "document_version_id": 7},
            "code_identity": {"rule_registry_version": "obligations-v12-checklist-global-20261022",
                               "rule_policy_version": "v3_3_portable"},
            "config_identity": {"use_local_rules": True, "use_ai_assist": False, "mode": "standard"},
        },
        "optional_fields": [],
        "git_commit_declared": None,
    }


# ---- selftest ----------------------------------------------------------------


def selftest() -> Tuple[int, List[Dict[str, Any]]]:
    """单一故障负控 + 合法正控（QP-06 范围）。返回 (exit_code, 明细)。"""
    findings = lambda ident, message: {"id": ident, "rule": "V33-X", "message": message, "page": 8}
    receipts = [{"rule_id": "V33-X", "status": "fail", "issue_refs": ["F-1"]}]
    checks: List[Dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Dict[str, Any]) -> None:
        checks.append({"control": name, "ok": bool(ok), **detail})

    # 正控
    base = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    result = compare_payloads(base, json.loads(_canonical(base)), _contract())
    record("正控：身份全匹配+合法双载", result["verdict"] == "compared"
           and result["zero_regression_claim"] is True, {"verdict": result["verdict"]})

    # 错材料
    wrong_material = _payload(checksum="b" * 64, rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    result = compare_payloads(base, wrong_material, _contract())
    kinds = {f["gate"] + ":" + f["kind"] for f in result["gates"]["after"]["gate_failures"]}
    record("错材料负控：checksum 不符即拒", result["verdict"] == "rejected"
           and "material.checksum_sha256:identity_mismatch" in kinds,
           {"gates_after": kinds})

    # 错代际
    wrong_generation = _payload(analysis_revision="rev-test-2", rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    result = compare_payloads(base, wrong_generation, _contract())
    kinds = {f["gate"] + ":" + f["kind"] for f in result["gates"]["after"]["gate_failures"]}
    record("错代际负控：analysis_revision 不符即拒", result["verdict"] == "rejected"
           and "generation.analysis_revision:identity_mismatch" in kinds,
           {"gates_after": kinds})

    # 真实格式漏读：主载体键缺失，review_report.issues 独占 2 条，入口不得拒绝合法旧格式
    legacy = _payload(report_issues=[{"id": "a", "rule": "V33-Y", "message": "m"},
                                      {"id": "b", "rule": "V33-Y", "message": "n"}], check_receipts=[])
    contract = _contract()
    result = compare_payloads(legacy, json.loads(_canonical(legacy)), contract)
    total = result["snapshot_after"]["formal_total_deduped"] if result["verdict"] == "compared" else None
    record("真实格式漏读负控：旧格式（rule_findings 缺失）照常读全并计数",
           result["verdict"] == "compared" and total == 2,
           {"verdict": result["verdict"], "formal_total_deduped": total})

    # 证据丢失：after 缺 check_receipts 键 → evidence_lost，不得当 0
    before_e = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    after_e = json.loads(_canonical(before_e))
    after_e["result"]["meta"].pop("check_receipts", None)
    after_e["review_report"].pop("check_receipts", None)
    result = compare_payloads(before_e, after_e, _contract())
    record("证据丢失负控：after 回执缺键显式登记",
           result["verdict"] == "compared"
           and result["evidence_lost"] == [{"carrier": "check_receipts", "before_count": 1,
                                             "after": "missing_or_null"}]
           and result["zero_regression_claim"] is False,
           {"evidence_lost": result.get("evidence_lost")})

    # 错误撤销：before 有 F-1，after 移除 F-1 但回执仍引用
    before_w = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    after_w = _payload(rule_findings=[], check_receipts=receipts)
    result = compare_payloads(before_w, after_w, _contract())
    record("错误撤销负控：移除仍有回执引用的问题必须显式登记",
           result["verdict"] == "compared"
           and result["removed_identities"]["withdrawn_with_evidence"] == ["id:F-1"]
           and result["zero_regression_claim"] is False,
           {"removed": result.get("removed_identities")})

    # 缺字段不当空：契约声明 rule_policy_version，载荷缺键 → 拒绝
    missing_field = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    missing_field["result"]["meta"].pop("rule_policy_version", None)
    result = compare_payloads(missing_field, json.loads(_canonical(missing_field)), _contract())
    kinds = {f["gate"] + ":" + f["kind"] for f in result["gates"]["before"]["gate_failures"]}
    record("缺字段不当空负控：声明字段缺键即拒（不当空、不当匹配）",
           result["verdict"] == "rejected"
           and "code.rule_policy_version:missing_identity_field" in kinds,
           {"gates_before": kinds})

    # 可选字段：optional_fields 声明后缺键放行且可见
    contract_opt = _contract()
    contract_opt["optional_fields"] = ["generation.parse_run_id"]
    payload_opt = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    payload_opt["structured_ingest"].pop("parse_run_id", None)
    result = compare_payloads(payload_opt, json.loads(_canonical(payload_opt)), contract_opt)
    record("可选字段缺键：放行但登记 optional_missing",
           result["verdict"] == "compared"
           and result["gates"]["before"]["optional_missing"] == ["generation.parse_run_id"],
           {"optional_missing": result["gates"]["before"]["optional_missing"]})

    failed = [c["control"] for c in checks if not c["ok"]]
    for item in checks:
        print(("PASS " if item["ok"] else "FAIL ") + item["control"])
    if failed:
        print(f"selftest FAILED: {failed}")
        return 1, checks
    print("selftest PASS（仅证明门禁与登记自洽；不构成独立验收）")
    return 0, checks


# ---- CLI --------------------------------------------------------------------


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="R2.2 §8.1 正式验收入口（外层身份门禁）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_verify = sub.add_parser("verify", help="门禁+（可选）比较")
    p_verify.add_argument("--contract", required=True, type=Path)
    p_verify.add_argument("--record", type=Path, help="单侧门禁扫描")
    p_verify.add_argument("--before", type=Path)
    p_verify.add_argument("--after", type=Path)
    p_verify.add_argument("--verify-code", type=Path, help="用该代码树 git HEAD 复核 git_commit_declared")
    sub.add_parser("selftest")
    args = parser.parse_args()

    if args.command == "selftest":
        code, _ = selftest()
        return code

    contract = _load_json(args.contract)
    if contract.get("contract_version") != CONTRACT_VERSION:
        print(json.dumps({"verdict": "rejected", "reason": "contract_version_mismatch",
                          "expected": CONTRACT_VERSION,
                          "observed": contract.get("contract_version")}, ensure_ascii=False))
        return 2
    verify_tree = args.verify_code if getattr(args, "verify_code", None) else None
    if args.record:
        payload = _load_json(args.record)
        gate = check_gates(payload, contract, side="record", verify_code_tree=verify_tree)
        print(json.dumps({"entry": "r22-formal-acceptance", "contract_version": CONTRACT_VERSION,
                          "verdict": "compared" if gate["verdict"] == "PASS" else "rejected",
                          "gates": {"record": gate}}, ensure_ascii=False, indent=2))
        return 0 if gate["verdict"] == "PASS" else 3
    if args.before and args.after:
        result = compare_payloads(_load_json(args.before), _load_json(args.after), contract,
                                  verify_code_tree=verify_tree)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["verdict"] == "compared" else 3
    parser.error("verify 需要 --record 或 --before/--after")
    return 2


if __name__ == "__main__":
    sys.exit(main())
