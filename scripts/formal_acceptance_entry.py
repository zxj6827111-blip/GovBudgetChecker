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

0. 模式：``mode`` 缺省 ``same_identity``（两侧同一份 expected）；
   ``cross_generation`` 时用 ``expected_before`` / ``expected_after`` 各自
   门禁（main 基线 vs 候选的代际与代码身份本就不同），且默认
   ``require_same_material=true`` 强制两侧同一材料 checksum，不一致按
   错材料拒绝。
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
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONTRACT_VERSION = "r22-formal-acceptance-v1"
#: 证据载体（投影）：before 存在而 after 缺键/null 时登记 evidence_lost。
_EVIDENCE_CARRIERS = ("check_receipts", "obligation_coverage", "fact_receipts")
#: M-02：回执的关键取数/来源证据字段。同关系身份保留但这些字段非空→空/缺失
#: 仍属证据退化；新增诊断字段（如 DH-02 结构化）不在此列，属合法演进。
#: 字段若发生版本迁移，必须在此显式更新映射，不能只剩关系 ID 即认定证据完整。
_KEY_EVIDENCE_FIELDS = ("operand_fact_ids", "operand_sources", "formula")
#: FA-01：契约每侧**必选**身份字段与类型校验器。缺一即契约无效——身份未成立的
#: "门禁全过"不是兼容，是放行未知输入。
_MANDATORY_CONTRACT_FIELDS: Tuple[Tuple[str, Any], ...] = (
    ("material.checksum_sha256", lambda v: isinstance(v, str) and len(v) == 64 and all(c in "0123456789abcdefABCDEF" for c in v)),
    ("generation.analysis_revision", lambda v: isinstance(v, str) and bool(v.strip())),
    ("code.rule_registry_version", lambda v: isinstance(v, str) and bool(v.strip())),
    ("code.rule_policy_version", lambda v: isinstance(v, str) and bool(v.strip())),
    ("config.use_local_rules", lambda v: isinstance(v, bool)),
    ("config.use_ai_assist", lambda v: isinstance(v, bool)),
    ("config.mode", lambda v: isinstance(v, str) and bool(v.strip())),
)
#: 解析代际字段（离线无库重放可能拿不到 DB 侧代际）：必须**声明**（含显式 null）
#: 或给出书面依据（parse_generation_basis），不允许靠省略字段豁免。
_CONDITIONAL_CONTRACT_FIELDS = ("generation.parse_run_id", "generation.document_version_id")
_VALID_MODES = ("same_identity", "cross_generation")

_COMPONENT_PATH = Path(__file__).resolve().parent / "verify_three_carriers.py"


def _tool_identity() -> Dict[str, str]:
    """工具自身版本分账绑定：入口脚本与三载体组件的 SHA256。"""

    def _sha(path: Path) -> str:
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return "unavailable"
    return {
        "entry_script_sha256": _sha(Path(__file__).resolve()),
        "component_script_sha256": _sha(_COMPONENT_PATH),
        "contract_version": CONTRACT_VERSION,
    }


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


# ---- 契约完整性校验（FA-01：先验契约，再谈门禁） ------------------------------


def _expected_identity_errors(expected: Any, *, side: str, basis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """校验单侧 expected：必选身份字段存在、类型/非空合规、解析代际有据。"""
    errors: List[Dict[str, Any]] = []
    if not isinstance(expected, dict) or not expected:
        return [{"field": "*", "kind": "expected_empty",
                 "message": f"{side}: expected 身份树缺失或为空——材料/代际/代码/配置身份未成立"}]
    flat = _flat_expected({"expected": expected})
    for field, validator in _MANDATORY_CONTRACT_FIELDS:
        if field not in flat:
            errors.append({"field": field, "kind": "mandatory_field_undeclared",
                           "message": f"{side}: 必选身份字段未声明，不能靠省略字段豁免"})
        else:
            value = flat[field]
            if value is None or not validator(value):
                errors.append({"field": field, "kind": "invalid_value",
                               "expected": "符合类型的非空值", "observed": value})
    for field in _CONDITIONAL_CONTRACT_FIELDS:
        if field not in flat and not str(basis.get(field, "")).strip():
            errors.append({
                "field": field, "kind": "conditional_field_unjustified",
                "message": (f"{side}: 解析代际字段 {field} 未声明——离线无库可写"
                            "parse_generation_basis 有据不适用，但不能靠省略豁免"),
            })
    return errors


def validate_contract(contract: Any) -> Dict[str, Any]:
    """FA-01：契约模式与每侧必选身份字段先验证；无效契约拒绝比较。

    - ``contract_version`` 必须匹配；
    - ``mode`` ∈ {same_identity, cross_generation}；
    - same_identity：``expected`` 必选字段齐全且类型合规；
    - cross_generation：``expected_before``/``expected_after`` 各自齐全；
    - 解析代际字段：声明（含显式 null）或在 ``parse_generation_basis``
      给出书面依据（如 offline_replay_without_db）；
    - ``optional_fields`` 只能引用已声明字段（载荷缺键放行，但期望必须存在）；
    - ``git_commit_declared`` 若声明须为 40 位十六进制。
    """
    errors: List[Dict[str, Any]] = []
    if not isinstance(contract, dict):
        return {"valid": False, "errors": [{"field": "*", "kind": "not_a_dict"}]}
    if contract.get("contract_version") != CONTRACT_VERSION:
        errors.append({"field": "contract_version", "kind": "version_mismatch",
                       "expected": CONTRACT_VERSION,
                       "observed": contract.get("contract_version")})
    mode = contract.get("mode", "same_identity")
    if mode not in _VALID_MODES:
        errors.append({"field": "mode", "kind": "invalid_mode",
                       "expected": list(_VALID_MODES), "observed": mode})
    basis = contract.get("parse_generation_basis")
    if not isinstance(basis, dict):
        basis = {}
    if mode == "cross_generation":
        for side in ("before", "after"):
            errors.extend(_expected_identity_errors(
                contract.get(f"expected_{side}"), side=f"expected_{side}", basis=basis))
    else:
        errors.extend(_expected_identity_errors(
            contract.get("expected"), side="expected", basis=basis))
    optional = contract.get("optional_fields", [])
    if not isinstance(optional, list):
        errors.append({"field": "optional_fields", "kind": "invalid_type"})
    else:
        if mode == "cross_generation":
            declared = set(_flat_expected({"expected": contract.get("expected_before")})) | \
                set(_flat_expected({"expected": contract.get("expected_after")}))
        else:
            declared = set(_flat_expected({"expected": contract.get("expected")}))
        mandatory_names = {field for field, _ in _MANDATORY_CONTRACT_FIELDS}
        for field in optional:
            if field not in declared:
                errors.append({"field": field, "kind": "optional_field_undeclared",
                               "message": "optional_fields 只能引用已在 expected 声明的字段"})
            elif field in mandatory_names:
                errors.append({"field": field, "kind": "mandatory_field_marked_optional",
                               "message": "必选身份字段不得标为可选——载荷缺失即身份未成立，不能放行"})
    git_declared = contract.get("git_commit_declared")
    if git_declared is not None and (
            not isinstance(git_declared, str) or len(git_declared) != 40
            or any(c not in "0123456789abcdef" for c in git_declared.lower())):
        errors.append({"field": "git_commit_declared", "kind": "invalid_value",
                       "expected": "40 位十六进制提交哈希或 null", "observed": git_declared})
    return {"valid": not errors, "errors": errors}


# ---- 门禁 -------------------------------------------------------------------


def check_gates(
    payload: Dict[str, Any],
    contract: Dict[str, Any],
    *,
    side: str,
    verify_code_tree: Optional[Path] = None,
    expected_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对单侧载荷执行全部身份门禁；输出结构化裁决，绝不静默放行。

    ``expected_override`` 供 cross_generation 模式按侧取各自的期望身份；
    默认（same_identity 模式）用契约的 ``expected``。
    """
    identity = extract_payload_identity(payload)
    expected = expected_override if expected_override is not None else _flat_expected(contract)
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


def _copies(status: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """返回 (result.meta, review_report) 两个容器（可能为空 dict）。"""
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    return meta, report


def _relation_identities(items: Any, *, carrier: str) -> Optional[Counter]:
    """回执载体的**关系身份**多重集（FA-02：按关系比较，不按内容指纹）。

    - ``check_receipts``：规则级关系 = ``rule_id``；
    - ``fact_receipts``：关系级 = ``(rule_id, sub_check_id)``。
    同一关系的内容增补（如 DH-02 结构化字段）是合法 schema 演进，
    不算证据丢失；只有关系身份**消失**才算丢失。
    """
    if not isinstance(items, list):
        return None
    counter: Counter = Counter()
    for item in items:
        if not isinstance(item, dict):
            counter[("non_dict",)] += 1
            continue
        rule_id = str(item.get("rule_id") or "")
        sub = item.get("sub_check_id")
        if carrier == "fact_receipts" and sub is not None:
            counter[(rule_id, str(sub))] += 1
        else:
            counter[(rule_id,)] += 1
    return counter


def _evidence_field_nonempty(field: str, value: Any) -> bool:
    """关键证据字段的"有效非空"判定（缺失/None/空容器/空串都算空）。"""
    if field == "operand_fact_ids":
        return isinstance(value, list) and len(value) > 0
    if field == "operand_sources":
        return isinstance(value, dict) and any(bool(v) for v in value.values())
    if field == "formula":
        return isinstance(value, str) and bool(value.strip())
    return False


def _relation_evidence(items: Any, *, carrier: str) -> Dict[Tuple, Dict[str, bool]]:
    """同关系身份下关键证据字段的非空状态（M-02：ID 保留 ≠ 证据完整）。

    关系键与 ``_relation_identities`` 一致；同键多条按 any() 聚合，
    避免顺序敏感。只回答"该关系是否仍持有有效证据"，不比内容指纹
    （内容增补是合法演进，不在这里误报）。
    """
    if not isinstance(items, list):
        return {}
    out: Dict[Tuple, Dict[str, bool]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or "")
        sub = item.get("sub_check_id")
        key = (rule_id, str(sub)) if carrier == "fact_receipts" and sub is not None else (rule_id,)
        fields = out.setdefault(key, {f: False for f in _KEY_EVIDENCE_FIELDS})
        for f in _KEY_EVIDENCE_FIELDS:
            if _evidence_field_nonempty(f, item.get(f)):
                fields[f] = True
    return out


def _identity_set(items: Any) -> Tuple[Optional[set], Optional[int], Optional[str]]:
    """载体内容的身份集合：条目级 canonical 指纹（FA-02 按内容比较）。

    返回 (identity_set, count, kind)；非 list/非 dict 投影返回 kind=type 名。
    """
    if items is None:
        return None, None, "null"
    if isinstance(items, list):
        return ({_canonical(item) for item in items if isinstance(item, dict)}
                if items else set()), len(items), "list"
    if isinstance(items, dict):
        # dict 形态（如 obligation_coverage）：其语义内容由调用方下钻 instances。
        return None, len(items), "dict"
    return None, None, type(items).__name__


def _coverage_instance_state(coverage: Any) -> Dict[str, Any]:
    """obligation_coverage 的实例级状态（FA-02：{}与删实例都要显式登记）。"""
    if not isinstance(coverage, dict):
        return {"kind": type(coverage).__name__ if coverage is not None else "null",
                "present": coverage is not None, "instance_count": None, "ids": None}
    raw = coverage.get("instances")
    if raw is None:
        return {"kind": "dict_without_instances", "present": True,
                "instance_count": None, "ids": None}
    if not isinstance(raw, list):
        return {"kind": "instances_wrong_type", "present": True,
                "instance_count": None, "ids": None}
    ids = {str(item.get("obligation_id")) for item in raw if isinstance(item, dict) and item.get("obligation_id")}
    return {"kind": "instances", "present": True, "instance_count": len(raw), "ids": ids}


def _evidence_content_state(status: Dict[str, Any]) -> Dict[str, Any]:
    """FA-02：证据载体的**关系级**状态（两份投影各自取，键缺失与空分清）。

    回执载体用关系身份多重集（``_relation_identities``）——内容增补是合法
    schema 演进；覆盖载体用义务 id 集合。
    """
    meta, report = _copies(status)
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    states: Dict[str, Any] = {}
    for key in _EVIDENCE_CARRIERS:
        copies: Dict[str, Any] = {}
        for name, container in (("result.meta", meta), ("review_report", report)):
            if key in container:
                value = container.get(key)
                if key == "obligation_coverage":
                    state = _coverage_instance_state(value)
                    state["raw_kind"] = type(value).__name__ if value is not None else "null"
                else:
                    ids, count, kind = _identity_set(value)
                    state = {"kind": kind, "present": value is not None,
                             "count": count, "ids": ids,
                             "relations": _relation_identities(value, carrier=key),
                             "evidence": _relation_evidence(value, carrier=key)}
                copies[name] = state
            else:
                copies[name] = {"kind": "missing_key", "present": False,
                                "count": None, "ids": None, "relations": None, "evidence": None}
        states[key] = copies
    states["_containers"] = {"result_present": isinstance(result, dict),
                              "review_report_present": isinstance(status.get("review_report"), dict)}
    return states


def compare_evidence(before_status: Dict[str, Any],
                     after_status: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """FA-02：按内容/关系/义务身份比较证据丢失；任何静默清空都不允许。

    登记种类：missing_key / null_value / emptied / type_changed /
    partial_loss（带丢失身份，截断展示）/ coverage_emptied /
    coverage_instances_dropped（带丢失 obligation_id）/ projection_mismatch。
    合法关系变化不算丢失——关系级变化由正式问题比较显式归因，这里只对
    **证据容器**（回执/覆盖）负责。
    """
    before = _evidence_content_state(before_status)
    after = _evidence_content_state(after_status)
    lost: List[Dict[str, Any]] = []
    summary: Dict[str, Any] = {}
    for key in _EVIDENCE_CARRIERS:
        for copy_name in ("result.meta", "review_report"):
            b = before[key][copy_name]
            a = after[key][copy_name]
            summary[f"{key}@{copy_name}"] = {
                "before_count": b.get("count", b.get("instance_count")),
                "after_count": a.get("count", a.get("instance_count")),
                "before_kind": b.get("kind"), "after_kind": a.get("kind"),
            }
        # 投影一致性：两侧同容器拷贝之间的差异
        for copy_name in ("result.meta", "review_report"):
            b, a = before[key][copy_name], after[key][copy_name]
            if b.get("kind") == "missing_key" and a.get("kind") == "missing_key":
                continue
            if a.get("kind") == "missing_key":
                lost.append({"carrier": key, "copy": copy_name, "kind": "missing_key",
                             "before_count": b.get("count", b.get("instance_count"))})
                continue
            if a.get("kind") == "null" and b.get("kind") != "null":
                lost.append({"carrier": key, "copy": copy_name, "kind": "null_value",
                             "before_count": b.get("count", b.get("instance_count"))})
                continue
            # obligation_coverage：实例级比较优先于泛化 type_changed
            if key == "obligation_coverage":
                if b.get("kind") == "instances" and a.get("kind") in (
                        "dict_without_instances", "instances_wrong_type"):
                    lost.append({"carrier": key, "copy": copy_name, "kind": "coverage_schema_lost",
                                 "before_count": b.get("instance_count")})
                    continue
                if b.get("kind") == "instances" and a.get("kind") == "instances":
                    b_ids, a_ids = b.get("ids") or set(), a.get("ids") or set()
                    dropped = sorted(b_ids - a_ids)
                    if b.get("instance_count") and not a.get("instance_count"):
                        lost.append({"carrier": key, "copy": copy_name, "kind": "coverage_emptied",
                                     "before_count": b.get("instance_count"),
                                     "after_count": a.get("instance_count")})
                    elif dropped:
                        lost.append({"carrier": key, "copy": copy_name,
                                     "kind": "coverage_instances_dropped",
                                     "before_count": b.get("instance_count"),
                                     "after_count": a.get("instance_count"),
                                     "dropped_obligation_ids": dropped[:20],
                                     "dropped_total": len(dropped)})
                    continue
            if b.get("kind") != a.get("kind") and b.get("kind") not in ("missing_key", None) \
                    and a.get("kind") not in ("null",):
                lost.append({"carrier": key, "copy": copy_name, "kind": "type_changed",
                             "before_kind": b.get("kind"), "after_kind": a.get("kind"),
                             "before_count": b.get("count", b.get("instance_count")),
                             "after_count": a.get("count", a.get("instance_count"))})
                continue
            # list 型：关系身份多重集比较（内容增补不算丢失，关系消失才算）
            if key != "obligation_coverage":
                b_rel, a_rel = b.get("relations"), a.get("relations")
                if b_rel is not None and a_rel is not None:
                    lost_pairs = b_rel - a_rel
                    lost_total = sum(lost_pairs.values())
                    if b.get("count") and a.get("count") == 0:
                        lost.append({"carrier": key, "copy": copy_name, "kind": "emptied",
                                     "before_count": b.get("count"), "after_count": a.get("count")})
                    elif lost_total:
                        lost.append({"carrier": key, "copy": copy_name, "kind": "partial_loss",
                                     "before_count": b.get("count"), "after_count": a.get("count"),
                                     "lost_relations": [list(k) for k in sorted(lost_pairs)][:20],
                                     "lost_relations_total": lost_total})
                    # M-02：同关系身份保留，但关键取数/来源证据非空→空/缺失
                    # 仍须登记（ID 存在不等于证据完整）；新增诊断字段不触发。
                    b_ev, a_ev = b.get("evidence") or {}, a.get("evidence") or {}
                    degraded = []
                    for rel_key in sorted(set(b_ev) & set(a_ev)):
                        fields = [f for f in _KEY_EVIDENCE_FIELDS
                                  if b_ev[rel_key].get(f) and not a_ev[rel_key].get(f)]
                        if fields:
                            degraded.append({"relation": list(rel_key), "degraded_fields": fields})
                    if degraded:
                        lost.append({"carrier": key, "copy": copy_name,
                                     "kind": "same_relation_evidence_degraded",
                                     "degraded_relations": degraded[:20],
                                     "degraded_total": len(degraded)})
        # 两份投影互相不一致（一份清空一份保留）
        kinds_after = {name: after[key][name].get("kind") for name in ("result.meta", "review_report")}
        if kinds_after.get("result.meta") == kinds_after.get("review_report") == "list":
            ids_meta = after[key]["result.meta"].get("ids") or set()
            ids_report = after[key]["review_report"].get("ids") or set()
            if ids_meta != ids_report:
                lost.append({"carrier": key, "kind": "projection_mismatch",
                             "result_meta_count": after[key]["result.meta"].get("count"),
                             "review_report_count": after[key]["review_report"].get("count")})
    return lost, summary


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


def _carrier_readability(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """FA-03：单侧三正式载体可读性评估——合法空/缺失/不可读分开。

    M-03：已出现但为 null、错类型、坏条目或损坏投影的载体单独登记
    damaged；未知状态也须待核。合法旧格式的"键缺失"不算损坏。
    """
    carriers = snapshot.get("formal_carriers", {})
    details: Dict[str, Any] = {}
    readable_any = False
    damaged: List[str] = []
    for key, state in carriers.items():
        if state.get("missing") or not state.get("present"):
            verdict = "missing"
        elif state.get("null_value"):
            verdict = "null"
        elif ((state.get("non_list") and not state.get("dict_format"))
              or state.get("invalid_items") or state.get("invalid_bucket_keys")):
            verdict = "unreadable_type"
        elif state.get("unreadable_projection") or state.get("projection_inconsistent"):
            verdict = "unreadable_projection"
        elif state.get("count") is not None:
            # 合法空（legal_empty）与有内容都算"可靠读取"
            verdict = "readable"
            readable_any = True
        else:
            verdict = "unknown"
        # 缺键可属于合法旧格式；已经出现但为 null/不可读/未知的载体
        # 不能被另一份空列表豁免。兜底 unknown 也保留为待核，防诊断漏传。
        if verdict in ("null", "unknown", "unreadable_type", "unreadable_projection"):
            damaged.append(key)
        details[key] = {"verdict": verdict, "count": state.get("count"),
                         "legal_empty": state.get("legal_empty", False),
                         "dict_format": state.get("dict_format", False)}
    return {"readable_any": readable_any, "damaged": damaged, "carriers": details}


def compare_payloads(
    before: Dict[str, Any],
    after: Dict[str, Any],
    contract: Dict[str, Any],
    *,
    verify_code_tree: Optional[Path] = None,
) -> Dict[str, Any]:
    """先验契约完整性（FA-01），门禁全过才比较；结论三态外新增待核。

    结论取值：

    - ``rejected``：契约无效或身份门禁未全过——不产生比较结论；
    - ``pending_review``：身份全过但任一侧无可读正式载体，或已出现的
      载体为 null、错类型、含坏条目/损坏投影——不能出"零回归"，转待核；
    - ``compared``：可读且比较已产出。``zero_regression_claim`` 只有在
      内容无变化、证据零丢失（FA-02 内容级）、投影一致且代码身份
      （若声明）已核验时才为 True；任何 False 都带 ``claim_reasons``。

    工具自身版本（入口脚本+组件 SHA256）随输出分账绑定。
    """
    contract_check = validate_contract(contract)
    mode = str(contract.get("mode") or "same_identity") if isinstance(contract, dict) else "?"
    if not contract_check["valid"]:
        return {
            "entry": "r22-formal-acceptance",
            "contract_version": CONTRACT_VERSION,
            "mode": mode,
            "tool_identity": _tool_identity(),
            "verdict": "rejected",
            "reject_reason": "contract_invalid",
            "contract_errors": contract_check["errors"],
            "statement": "契约完整性校验未过：身份未成立的比较一律拒绝。",
        }

    if mode == "cross_generation":
        expected_before = _flat_expected({"expected": contract.get("expected_before")})
        expected_after = _flat_expected({"expected": contract.get("expected_after")})
        gate_before = check_gates(before, contract, side="before",
                                  verify_code_tree=verify_code_tree, expected_override=expected_before)
        gate_after = check_gates(after, contract, side="after",
                                 verify_code_tree=verify_code_tree, expected_override=expected_after)
        if contract.get("require_same_material", True):
            material_before = gate_before["observed_identity"].get("material.checksum_sha256")
            material_after = gate_after["observed_identity"].get("material.checksum_sha256")
            if material_before != material_after or material_before is None:
                for gate in (gate_before, gate_after):
                    gate["verdict"] = "REJECT"
                    gate["gate_failures"].append({
                        "gate": "material.cross_side", "kind": "identity_mismatch",
                        "expected": material_before, "observed": material_after,
                        "note": "跨代际比较要求两侧为同一材料；checksum 不一致即错材料",
                    })
    else:
        gate_before = check_gates(before, contract, side="before", verify_code_tree=verify_code_tree)
        gate_after = check_gates(after, contract, side="after", verify_code_tree=verify_code_tree)
    gates = {"before": gate_before, "after": gate_after}
    if gate_before["verdict"] != "PASS" or gate_after["verdict"] != "PASS":
        return {
            "entry": "r22-formal-acceptance",
            "contract_version": CONTRACT_VERSION,
            "mode": mode,
            "tool_identity": _tool_identity(),
            "verdict": "rejected",
            "reject_reason": "identity_gate_failed",
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

    # FA-02：证据丢失按关系/义务/内容身份比较（两份投影各自取 + 投影一致性）。
    evidence_lost, evidence_summary = compare_evidence(status_before, status_after)

    content_changed = sorted(
        identity
        for identity in ids_before & ids_after
        if _canonical(next(f["content"] for f in snap_before["formal_findings"] if f["identity"] == identity))
        != _canonical(next(f["content"] for f in snap_after["formal_findings"] if f["identity"] == identity))
    )

    # FA-03：任一侧三正式载体无一可靠读取 → 结论只能"待核"，不得零回归。
    # M-03：已出现且被判损坏/不一致的载体，不能被其他空列表载体豁免——
    # 局部损坏 + 另一份合法空仍须待核；合法旧格式的键缺失不算损坏。
    readability = {"before": _carrier_readability(snap_before),
                    "after": _carrier_readability(snap_after)}
    damaged = {side: readability[side]["damaged"]
               for side in ("before", "after") if readability[side]["damaged"]}
    unreadable = not readability["before"]["readable_any"] or not readability["after"]["readable_any"]
    if unreadable or damaged:
        reason = "formal_carrier_damaged" if damaged else "formal_carriers_unreadable"
        return {
            "entry": "r22-formal-acceptance",
            "contract_version": CONTRACT_VERSION,
            "mode": mode,
            "tool_identity": _tool_identity(),
            "verdict": "pending_review",
            "pending_reason": reason,
            "damaged_carriers": damaged,
            "gates": gates,
            "carrier_readability": readability,
            "snapshot_before": snap_before,
            "snapshot_after": snap_after,
            "evidence_lost": evidence_lost,
            "evidence_summary": evidence_summary,
            "zero_regression_claim": False,
            "claim_reasons": [reason],
            "statement": ("身份门禁全过，但正式问题载体不可读（全缺失/null/错类型）"
                           "或已出现损坏/不一致投影——无法证明'零回归'，转待核。"),
        }

    claim_reasons: List[str] = []
    if added or removed or content_changed:
        claim_reasons.append("formal_issue_content_changed")
    if evidence_lost:
        claim_reasons.append("evidence_lost_registered")
    if snap_after["conflicts"]:
        claim_reasons.append("carrier_conflicts_in_after")
    if any(gate.get("git_commit") == "declared_not_verified" for gate in gates.values()):
        claim_reasons.append("code_identity_declared_not_verified")
    zero_regression_claim = not claim_reasons
    return {
        "entry": "r22-formal-acceptance",
        "contract_version": CONTRACT_VERSION,
        "mode": mode,
        "tool_identity": _tool_identity(),
        "verdict": "compared",
        "gates": gates,
        "carrier_readability": readability,
        "snapshot_before": snap_before,
        "snapshot_after": snap_after,
        "added_identities": added,
        "removed_identities": {
            "withdrawn_with_evidence": withdrawn_with_evidence,
            "removed_unexplained": removed_unexplained,
        },
        "content_changed_identities": content_changed,
        "evidence_lost": evidence_lost,
        "evidence_summary": evidence_summary,
        "zero_regression_claim": zero_regression_claim,
        "claim_reasons": claim_reasons,
        "statement": (
            "契约完整性、身份门禁、载体可读性全部通过后比较已产出。"
            "removed/withdrawn/evidence_lost 均为显式登记，不计入无变化；"
            "zero_regression_claim 只描述本次比较的载体层观察，不构成独立验收结论。"
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
    def findings(ident, message):
        return {"id": ident, "rule": "V33-X", "message": message, "page": 8}

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

    # 真实生产 dict 形态：result.issues={"error":[...],"all":投影}——all 不读，
    # error/warn/info 读全（uploads 旧任务实测形态，QP-06 漏读负控）。
    dict_payload = _payload(check_receipts=[])
    dict_payload["result"]["issues"] = {
        "error": [{"id": "V33-109-1", "rule": "V33-109", "message": "m", "severity": "error"}],
        "warn": [], "info": [],
        "all": [{"id": "V33-109-1", "rule": "V33-109", "message": "m", "severity": "error"}],
    }
    result = compare_payloads(dict_payload, json.loads(_canonical(dict_payload)), _contract())
    total = result["snapshot_after"]["formal_total_deduped"] if result["verdict"] == "compared" else None
    carriers = result["snapshot_after"]["formal_carriers"]["result.issues"] if result["verdict"] == "compared" else {}
    record("真实格式漏读负控：dict 形态 result.issues 读全且 all 投影不双计",
           result["verdict"] == "compared" and total == 1
           and carriers.get("dict_format") is True
           and carriers.get("projection_keys_excluded") == ["all"],
           {"verdict": result["verdict"], "formal_total_deduped": total})

    # 证据丢失：after 缺 check_receipts 键 → evidence_lost，不得当 0
    before_e = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    after_e = json.loads(_canonical(before_e))
    after_e["result"]["meta"].pop("check_receipts", None)
    after_e["review_report"].pop("check_receipts", None)
    result = compare_payloads(before_e, after_e, _contract())
    lost_kinds = {(item["carrier"], item["copy"], item["kind"]) for item in result["evidence_lost"]}
    record("证据丢失负控：after 回执缺键显式登记",
           result["verdict"] == "compared"
           and ("check_receipts", "result.meta", "missing_key") in lost_kinds
           and ("check_receipts", "review_report", "missing_key") in lost_kinds
           and result["zero_regression_claim"] is False,
           {"evidence_lost": result.get("evidence_lost")})

    # FA-02：非空回执清成 []（缺键仍存在）→ emptied 登记，不得静默零丢失
    after_cleared = json.loads(_canonical(before_e))
    after_cleared["result"]["meta"]["check_receipts"] = []
    after_cleared["review_report"]["check_receipts"] = []
    result = compare_payloads(before_e, after_cleared, _contract())
    lost_kinds = {(item["carrier"], item["copy"], item["kind"]) for item in result["evidence_lost"]}
    record("FA-02 负控：非空回执清成 [] 必须 emptied 登记",
           result["verdict"] == "compared"
           and ("check_receipts", "result.meta", "emptied") in lost_kinds
           and result["zero_regression_claim"] is False,
           {"evidence_lost": result.get("evidence_lost")})

    # FA-02：回执部分丢失（两条删一条）→ partial_loss 带丢失身份
    before_p = _payload(rule_findings=[findings("F-1", "m")],
                        check_receipts=[{"rule_id": "V33-X", "status": "fail", "issue_refs": ["F-1"]},
                                        {"rule_id": "V33-Y", "status": "fail", "issue_refs": []}])
    after_p = json.loads(_canonical(before_p))
    after_p["result"]["meta"]["check_receipts"] = before_p["result"]["meta"]["check_receipts"][:1]
    after_p["review_report"]["check_receipts"] = after_p["result"]["meta"]["check_receipts"]
    result = compare_payloads(before_p, after_p, _contract())
    partial = [item for item in result["evidence_lost"] if item.get("kind") == "partial_loss"]
    record("FA-02 负控：删一条回执 → partial_loss 登记丢失身份",
           result["verdict"] == "compared" and partial
           and partial[0]["lost_relations_total"] == 1
           and result["zero_regression_claim"] is False,
           {"partial": partial[:1]})

    # FA-02：obligation_coverage 改 {} / 删实例 → 登记
    base_cov = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    base_cov["result"]["meta"]["obligation_coverage"] = {
        "instances": [{"obligation_id": "OBL-A", "status": "insufficient_data"},
                       {"obligation_id": "OBL-B", "status": "insufficient_data"}],
        "applicable_total": 2, "completed_total": 0, "not_applicable_total": 0,
        "unresolved_total": 2, "blocking_total": 2,
    }
    after_cov_empty = json.loads(_canonical(base_cov))
    after_cov_empty["result"]["meta"]["obligation_coverage"] = {}
    result = compare_payloads(base_cov, after_cov_empty, _contract())
    kinds = {item.get("kind") for item in result["evidence_lost"]}
    after_cov_partial = json.loads(_canonical(base_cov))
    after_cov_partial["result"]["meta"]["obligation_coverage"]["instances"] = \
        base_cov["result"]["meta"]["obligation_coverage"]["instances"][:1]
    result2 = compare_payloads(base_cov, after_cov_partial, _contract())
    kinds2 = {item.get("kind") for item in result2["evidence_lost"]}
    record("FA-02 负控：coverage 改 {} 或删实例必须登记",
           result["verdict"] == "compared" and "coverage_schema_lost" in kinds
           and result2["verdict"] == "compared" and "coverage_instances_dropped" in kinds2
           and result["zero_regression_claim"] is False and result2["zero_regression_claim"] is False,
           {"cov_empty": result.get("evidence_lost"), "cov_partial": result2.get("evidence_lost")})

    # FA-02：只清空一份投影（meta 清空、review_report 保留）→ projection_mismatch
    after_proj = json.loads(_canonical(before_e))
    after_proj["result"]["meta"]["check_receipts"] = []
    result = compare_payloads(before_e, after_proj, _contract())
    kinds = {item.get("kind") for item in result["evidence_lost"]}
    record("FA-02 负控：只清空一份投影 → projection_mismatch 登记",
           result["verdict"] == "compared" and "projection_mismatch" in kinds,
           {"evidence_lost": result.get("evidence_lost")})

    # FA-01：空 expected 契约 → contract_invalid 拒绝（身份未成立不得"门禁全过"）
    result = compare_payloads(_payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts),
                              json.loads(_canonical(_payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts))),
                              {"contract_version": CONTRACT_VERSION, "expected": {}})
    record("FA-01 负控：空 expected 契约拒绝（contract_invalid）",
           result["verdict"] == "rejected" and result.get("reject_reason") == "contract_invalid",
           {"reject_reason": result.get("reject_reason"),
            "errors": (result.get("contract_errors") or [])[:2]})

    # FA-01：解析代际未声明且无书面依据 → 契约无效；有 parse_generation_basis 放行
    contract_no_parse = _contract()
    for field in ("parse_run_id", "document_version_id"):
        contract_no_parse["expected"]["generation"].pop(field, None)
    result = compare_payloads(_payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts),
                              json.loads(_canonical(_payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts))),
                              contract_no_parse)
    no_basis_ok = result["verdict"] == "rejected" and result.get("reject_reason") == "contract_invalid"
    contract_basis = json.loads(_canonical(contract_no_parse))
    contract_basis["parse_generation_basis"] = {
        "generation.parse_run_id": "offline_replay_without_db",
        "generation.document_version_id": "offline_replay_without_db",
    }
    result2 = compare_payloads(_payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts),
                               json.loads(_canonical(_payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts))),
                               contract_basis)
    record("FA-01 负控：解析代际不声明即契约无效；书面依据后有据放行",
           no_basis_ok and result2["verdict"] == "compared",
           {"no_basis": result.get("reject_reason"), "with_basis": result2.get("verdict")})

    # FA-03：两侧三正式载体全部缺失 → pending_review，不得零回归
    # （解析代际字段保持完整——身份门禁的"缺字段不当空"必须仍然生效）
    carriers_absent = _payload(rule_findings=None, check_receipts=[])
    carriers_absent["review_report"].pop("issues", None)
    carriers_absent["structured_ingest"] = {"parse_run_id": "parse-1", "document_version_id": 7}
    result = compare_payloads(carriers_absent, json.loads(_canonical(carriers_absent)), _contract())
    record("FA-03 负控：三载体全缺失 → pending_review 不出零回归",
           result["verdict"] == "pending_review"
           and result.get("pending_reason") == "formal_carriers_unreadable"
           and result["zero_regression_claim"] is False,
           {"verdict": result["verdict"]})

    # FA-03：dict 载体只有 all 投影且非空 → 损坏投影不得读成 0/合法空
    proj_only = _payload(check_receipts=[])
    proj_only["result"]["issues"] = {"all": [{"id": "F-2", "rule": "V33-Y", "message": "x"}]}
    result = compare_payloads(proj_only, json.loads(_canonical(proj_only)), _contract())
    carriers = (result.get("carrier_readability") or {}).get("before", {}).get("carriers", {}).get("result.issues", {})
    record("FA-03 负控：只有 all 投影 → unreadable_projection → pending_review",
           result["verdict"] == "pending_review"
           and carriers.get("verdict") == "unreadable_projection",
           {"verdict": result["verdict"], "carrier": carriers})

    # FA-03：严重度桶在而 all 被清空 → projection_inconsistent → pending_review
    proj_partial = _payload(check_receipts=[])
    proj_partial["result"]["issues"] = {"error": [{"id": "F-1", "rule": "V33-X", "message": "m"}],
                                         "warn": [], "info": [], "all": []}
    result = compare_payloads(proj_partial, json.loads(_canonical(proj_partial)), _contract())
    carriers = (result.get("carrier_readability") or {}).get("before", {}).get("carriers", {}).get("result.issues", {})
    record("FA-03 负控：all 被清空而严重度桶有内容 → pending_review",
           result["verdict"] == "pending_review"
           and carriers.get("verdict") == "unreadable_projection",
           {"verdict": result["verdict"], "carrier": carriers})

    # FA-01/分账：git_commit_declared 只声明未核验 → compared 但零回归被迫 False
    raw = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    contract_git = _contract()
    contract_git["git_commit_declared"] = "0" * 40
    result = compare_payloads(raw, json.loads(_canonical(raw)), contract_git)
    record("FA-01 负控：代码身份只声明未核验 → 零回归 False 并登记原因",
           result["verdict"] == "compared"
           and result["zero_regression_claim"] is False
           and "code_identity_declared_not_verified" in result["claim_reasons"],
           {"claim_reasons": result.get("claim_reasons")})

    # 工具版本分账：输出携带入口脚本与组件 SHA256
    record("工具身份分账绑定：输出携带 tool_identity 双哈希",
           bool(result.get("tool_identity", {}).get("entry_script_sha256"))
           and result["tool_identity"]["entry_script_sha256"]
           != result["tool_identity"]["component_script_sha256"],
           {"tool_identity": result.get("tool_identity")})

    # FA-01 补强：假值 mode / 非 list optional_fields 不得按缺省放行
    type_ok = True
    for bad_mode in ([], 0, False, "", None):
        bad_contract = _contract()
        bad_contract["mode"] = bad_mode
        rr = compare_payloads(base, json.loads(_canonical(base)), bad_contract)
        type_ok = type_ok and rr["verdict"] == "rejected" and rr.get("reject_reason") == "contract_invalid"
    for bad_optional in ("", 0, False, None, {}):
        bad_contract = _contract()
        bad_contract["optional_fields"] = bad_optional
        rr = compare_payloads(base, json.loads(_canonical(base)), bad_contract)
        type_ok = type_ok and rr["verdict"] == "rejected" and rr.get("reject_reason") == "contract_invalid"
    record("FA-01 负控：假值 mode/optional_fields 类型拒绝（不按缺省放行）", type_ok, {})

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

    # M-01：必选身份字段标 optional → 契约拒绝（不能借可选绕过身份门禁）
    c_m01 = _contract()
    c_m01["optional_fields"] = ["material.checksum_sha256"]
    no_checksum = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    no_checksum.pop("checksum", None)
    result = compare_payloads(no_checksum, json.loads(_canonical(no_checksum)), c_m01)
    record("M-01 负控：必选身份标optional即契约拒绝",
           result["verdict"] == "rejected" and result.get("reject_reason") == "contract_invalid"
           and any(x["kind"] == "mandatory_field_marked_optional" for x in result.get("contract_errors", [])),
           {"errors": [x["kind"] for x in result.get("contract_errors", [])]})

    # M-02：同关系身份保留但关键取数/来源证据非空→空 → 登记，不得零回归
    fact_ev = [{"rule_id": "V33-X", "sub_check_id": "s1", "status": "pass", "formula": "10≤10",
                "operand_fact_ids": ["f1", "f2"], "operand_sources": {"left": [{"physical_page": 1}]}}]
    before_m2 = _payload(rule_findings=[findings("F-1", "m")], check_receipts=receipts)
    before_m2["result"]["meta"]["fact_receipts"] = json.loads(_canonical(fact_ev))
    before_m2["review_report"]["fact_receipts"] = json.loads(_canonical(fact_ev))
    after_m2 = json.loads(_canonical(before_m2))
    for cont in (after_m2["result"]["meta"], after_m2["review_report"]):
        fr = cont["fact_receipts"][0]
        fr["operand_fact_ids"] = []
        fr["operand_sources"] = {}
        fr["formula"] = None
    result = compare_payloads(before_m2, after_m2, _contract())
    deg = [x for x in result.get("evidence_lost", []) if x["kind"] == "same_relation_evidence_degraded"]
    record("M-02 负控：同关系operand证据清空登记+零回归False",
           result["verdict"] == "compared" and deg and result["zero_regression_claim"] is False,
           {"degraded": deg[:1]})
    after_m2b = json.loads(_canonical(before_m2))
    for cont in (after_m2b["result"]["meta"], after_m2b["review_report"]):
        cont["fact_receipts"][0]["dh02_structured"] = {"new": "合法演进"}
    result = compare_payloads(before_m2, after_m2b, _contract())
    record("M-02 正控：同关系新增诊断字段零丢失（不误报）",
           result["verdict"] == "compared" and not result.get("evidence_lost"),
           {"lost": result.get("evidence_lost")})

    # M-03：all 新增严重度桶之外的问题 → 双向不一致 → 不得零回归
    i1 = {"id": "F1", "rule": "V33-X", "message": "m", "severity": "error", "page": 1}
    i2 = {"id": "F2", "rule": "V33-Y", "message": "n", "severity": "error", "page": 2}
    before_m3 = _payload(rule_findings=[], report_issues=[], check_receipts=[])
    before_m3["result"]["issues"] = {"error": [i1], "warn": [], "info": [], "all": [i1]}
    after_m3 = json.loads(_canonical(before_m3))
    after_m3["result"]["issues"]["all"].append(i2)
    result = compare_payloads(before_m3, after_m3, _contract())
    record("M-03 负控：all新增桶外问题 → pending不得零回归",
           result["verdict"] == "pending_review" and result["zero_regression_claim"] is False,
           {"verdict": result["verdict"], "reason": result.get("pending_reason")})
    # M-03：已损坏投影不能被其他空列表载体豁免（readable_any 仍 True 也须待核）
    before_m3b = _payload(rule_findings=[], report_issues=[], check_receipts=[])
    before_m3b["result"]["issues"] = {"error": [], "warn": [], "info": [], "all": []}
    after_m3b = json.loads(_canonical(before_m3b))
    after_m3b["result"]["issues"] = {"all": [i2]}
    result = compare_payloads(before_m3b, after_m3b, _contract())
    record("M-03 负控：损坏投影不被其他空载体掩盖",
           result["verdict"] == "pending_review"
           and result.get("pending_reason") == "formal_carrier_damaged"
           and result["zero_regression_claim"] is False,
           {"verdict": result["verdict"], "reason": result.get("pending_reason"),
            "damaged": result.get("damaged_carriers")})

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
    check = validate_contract(contract)
    if not check["valid"]:
        print(json.dumps({"entry": "r22-formal-acceptance", "verdict": "rejected",
                          "reject_reason": "contract_invalid",
                          "contract_errors": check["errors"],
                          "tool_identity": _tool_identity()}, ensure_ascii=False, indent=2))
        return 2
    verify_tree = args.verify_code if getattr(args, "verify_code", None) else None
    if args.record:
        payload = _load_json(args.record)
        gate = check_gates(payload, contract, side="record", verify_code_tree=verify_tree)
        print(json.dumps({"entry": "r22-formal-acceptance", "contract_version": CONTRACT_VERSION,
                          "tool_identity": _tool_identity(),
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
