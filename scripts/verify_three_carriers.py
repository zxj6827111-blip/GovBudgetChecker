#!/usr/bin/env python3
"""R2.2 §8.1 三载体数据契约核查工具（待审——未经独立核验不得签发验收结论）。

载体定义（独立复核 IR05 勘误）：**正式问题载体**有三个——

- ``result.rule_findings``
- ``result.issues``
- ``review_report.issues``

主载体选择：以结果代际 schema 声明为准；schema 未声明时按保守次序
``result.rule_findings`` → ``result.issues`` → ``review_report.issues``
（main 树的结果写入方以 rule_findings 为正式主载体）。
``review_report.check_receipts`` 与 ``result.meta.obligation_coverage`` 是
**投影**（关系级回执/义务台账），不是正式问题载体，不替代后两者。

契约（本工具实现的唯一事实来源）：

1. **正式问题计数** = 三个正式载体中按身份去重后的并集；主载体缺席
   （键缺失）时其余正式载体照常计数，并登记主载体缺失。
2. **去重**：同一身份（确定性 id 或内容指纹）在多个正式载体出现 →
   multi_carrier 投影，只计一次；内容指纹一致才视为同一问题。
3. **冲突**：同一确定性 id 在不同载体携带不同内容 → 登记冲突
   （carrier_content_mismatch），以主载体内容为准并保留差异明细。
4. **缺字段 vs 合法空列表**：键缺失 = 未知（不得当 0）；键存在且为 [] =
   合法空（计 0）。跨载体不做"直接相加"式合计——并集计数自带去重说明。
5. 投影对接：回执 issue_refs 对并集身份映射；命中记 mapped，未命中记
   projection_only。

纪律：本工具是 R2.2 §8.1 的待审交付，未通过独立核验前，其输出只能作为
问题登记，不能作为"compare 不变量已具备"或"零回归"的签发依据；双采一致
只证明确定性。main 旧 r2 验收器已被独立证明漏读，其入口冻结**不等于**可信
核验通过；本工具保持待审直到独立通过。

用法（--record/--before/--after 接**原始任务状态 JSON**）：
    python scripts/verify_three_carriers.py scan   --record status.json
    python scripts/verify_three_carriers.py compare --before a.json --after b.json
    python scripts/verify_three_carriers.py selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_MAIN_ORDER = ("result.rule_findings", "result.issues", "review_report.issues")
_RECEIPT_CARRIER = "check_receipts"
_COVERAGE_CARRIER = "obligation_coverage"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _carrier_state(container: Any, key: str) -> Dict[str, Any]:
    """载体读取：区分"键缺失（未知）"与"键存在且为空列表（合法空）"。"""
    if not isinstance(container, dict) or key not in container:
        return {"present": False, "count": None, "items": [], "missing": True}
    raw = container.get(key)
    if raw is None:
        return {"present": True, "count": None, "items": [], "missing": False, "null_value": True}
    if isinstance(raw, list):
        return {"present": True, "count": len(raw), "items": raw, "missing": False}
    return {"present": True, "count": None, "items": [], "missing": False, "non_list": True}


def _validate_finding_items(state: Dict[str, Any]) -> Dict[str, Any]:
    """正式问题必须是对象列表，坏条目不能被下游去重时静默跳过。"""
    invalid = [index for index, item in enumerate(state["items"])
               if not isinstance(item, dict)]
    if invalid:
        state = dict(state)
        state.update({"invalid_items": True, "invalid_item_indices": invalid,
                      "count": None, "items": []})
    return state


#: 真实生产载荷的 ``result.issues`` 是**按严重度分键的 dict**
#: （``{"error": [...], "warn": [...], "info": [...], "all": [...]}``，
#: 见 uploads 旧任务实测）。``all`` 是 error+warn+info 的投影，读它会把
#: 同一问题双计；其余未知键若为 list 也读入（以实际内容为准，不猜语义）。
_ISSUES_DICT_PROJECTION_KEYS = {"all"}
_ISSUES_DICT_SEVERITY_KEYS = ("error", "warn", "info")


def _carrier_state_issues(container: Any, key: str) -> Dict[str, Any]:
    """``result.issues`` 载体读取：list 形态与 dict 形态都读全。

    dict 形态按严重度键取各 list（排除 ``all`` 投影防双计），登记
    ``dict_format`` 与分键计数；键缺失/None/空 与既有语义一致。

    FA-03（最终验收）：投影一致性必须可核验——
    - 只有 ``all`` 投影、没有严重度桶且 ``all`` 非空 → ``unreadable_projection``
      （损坏/不完整投影不得读成合法空 0）；
    - 严重度桶与 ``all`` 同时存在但内容不一致（双向：桶内条目不在 all 中，
      或 all 中条目不在任何严重度桶）→ ``projection_inconsistent``，
      同样不得静默计 0；
    - 合法空（桶与 all 均为空 list）照常计 0。
    """
    state = _carrier_state(container, key)
    if not state.get("present") or state.get("missing"):
        return state
    if state.get("non_list"):
        container_dict = container if isinstance(container, dict) else {}
        raw = container_dict.get(key)
        if not isinstance(raw, dict):
            return state  # 真·非 list 非 dict：保持 non_list 登记，不猜
        items: List[Any] = []
        key_counts: Dict[str, int] = {}
        for name, bucket in raw.items():
            if name in _ISSUES_DICT_PROJECTION_KEYS or not isinstance(bucket, list):
                continue
            key_counts[str(name)] = len(bucket)
            items.extend(bucket)
        state.pop("non_list", None)
        all_bucket = raw.get("all")
        # 已声明的严重度桶与 all 都必须是列表；跳过坏桶会把未读到的问题
        # 伪装成合法空。未知元数据键沿用既有兼容口径。
        invalid_bucket_keys = sorted(
            str(name) for name, bucket in raw.items()
            if name in (*_ISSUES_DICT_SEVERITY_KEYS, *_ISSUES_DICT_PROJECTION_KEYS)
            and not isinstance(bucket, list)
        )
        projection_consistent: Optional[bool] = None
        unreadable_projection = False
        projection_inconsistent = False
        if isinstance(all_bucket, list):
            severity_ids = {_canonical(dict(item)) if isinstance(item, dict) else _canonical(item)
                            for name, bucket in raw.items()
                            if name not in _ISSUES_DICT_PROJECTION_KEYS and isinstance(bucket, list)
                            for item in bucket}
            all_ids = {_canonical(dict(item)) if isinstance(item, dict) else _canonical(item)
                       for item in all_bucket}
            if not key_counts:
                # 只有 all 投影：无法区分合法空与被剥离严重度桶的损坏投影。
                if all_bucket:
                    unreadable_projection = True
                    projection_consistent = False
                else:
                    projection_consistent = True
            elif not all_bucket and severity_ids:
                # all 被清空而严重度桶仍有内容：投影被部分清空。
                projection_inconsistent = True
            elif severity_ids != all_ids:
                # 双向一致：桶有而 all 无（投影剥离），或 all 有而桶无
                # （仅 all 新增的问题被漏读），都判不一致。
                projection_inconsistent = True
            else:
                projection_consistent = True
        state.update({
            "dict_format": True,
            "dict_key_counts": key_counts,
            "projection_keys_excluded": sorted(
                str(name) for name in raw
                if name in _ISSUES_DICT_PROJECTION_KEYS and isinstance(raw.get(name), list)),
            "count": len(items),
            "items": items,
            "projection_consistent": projection_consistent,
            "unreadable_projection": unreadable_projection,
            "projection_inconsistent": projection_inconsistent,
            "invalid_bucket_keys": invalid_bucket_keys,
        })
        if unreadable_projection or projection_inconsistent or invalid_bucket_keys:
            # 损坏投影不提供可读计数：交由外层按"未知"处理，不得当 0。
            state["count"] = None
            state["items"] = []
        return state
    return state


#: 易变字段：不进入稳定内容指纹（R2-04：uuid:one→uuid:two 的同内容投影
#: 曾因指纹含 id 被计成两个问题；采集时间戳同理）。
_VOLATILE_CONTENT_KEYS = {"created_at", "updated_at"}


def finding_content(finding: Dict[str, Any]) -> Dict[str, Any]:
    """规范化问题内容 = 全字段剔除易变键（含非确定性 id）。

    确定性 id 参与身份（finding_identity），但**不进入内容指纹**：同一问题
    换发 id 后内容未变仍须视为同一问题；内容变化（如金额 10→100）则必须
    判异。id 作为来源引用单列在 finding 的 sources 里，不丢失。
    """
    finding_id = str(finding.get("id") or "")
    volatile = set(_VOLATILE_CONTENT_KEYS)
    if finding_id.startswith("uuid:"):
        volatile.add("id")
    return {k: v for k, v in finding.items() if k not in volatile}


def finding_identity(finding: Dict[str, Any]) -> str:
    """身份：确定性 id 优先；UUID/无 id 用**稳定内容**指纹（不含易变 id）。"""
    finding_id = str(finding.get("id") or "")
    if finding_id and not finding_id.startswith("uuid:"):
        return f"id:{finding_id}"
    return "fp:" + hashlib.sha1(_canonical(finding_content(finding)).encode("utf-8")).hexdigest()[:16]


def three_carrier_snapshot(result: Any, review_report: Any, meta_coverage: Any) -> Dict[str, Any]:
    """按契约产出三载体快照（纯函数，可独立核验）。

    输入口径：**原始任务状态 JSON**（含 result / review_report /
    result.meta.obligation_coverage）。
    """
    carriers = {
        "result.rule_findings": _validate_finding_items(_carrier_state(result, "rule_findings")),
        "result.issues": _validate_finding_items(_carrier_state_issues(result, "issues")),
        "review_report.issues": _validate_finding_items(_carrier_state_issues(review_report, "issues")),
    }
    # 身份并集 + 载体归属 + 内容冲突检测
    identities: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for carrier_key in _MAIN_ORDER:
        for finding in carriers[carrier_key]["items"]:
            if not isinstance(finding, dict):
                continue
            identities.setdefault(finding_identity(finding), {})[carrier_key] = finding
    multi_carrier: List[Dict[str, Any]] = []
    conflicts: List[Dict[str, Any]] = []
    main_carrier_key = next(
        (key for key in _MAIN_ORDER if carriers[key]["present"] and carriers[key]["count"]),
        _MAIN_ORDER[0])
    for identity, occurrences in identities.items():
        if len(occurrences) > 1:
            contents = {_canonical(finding_content(f)) for f in occurrences.values()}
            entry: Dict[str, Any] = {
                "identity": identity, "carriers": sorted(occurrences),
                "sources": sorted(str(f.get("id")) for f in occurrences.values() if f.get("id")),
            }
            if len(contents) > 1:
                entry.update({
                    "kind": "carrier_content_mismatch",
                    "resolution": f"以主载体（{main_carrier_key}）内容为准，差异明细保留待人工复核",
                    "contents": sorted(contents)[:4],
                })
                conflicts.append(entry)
            else:
                entry["kind"] = "duplicate_projection"
                multi_carrier.append(entry)
    formal_total = len(identities)
    formal_findings = [
        {
            "identity": identity,
            "carriers": sorted(occurrences),
            "sources": sorted({str(f.get("id")) for f in occurrences.values() if f.get("id")}),
            "content_digest": "sha1:" + hashlib.sha1(
                _canonical(finding_content(next(iter(occurrences.values())))).encode("utf-8")
            ).hexdigest()[:16],
            "content": finding_content(next(iter(occurrences.values()))),
        }
        for identity, occurrences in sorted(identities.items())
    ]

    # 投影对接（回执引用 → 并集身份）
    receipts = _carrier_state(review_report, _RECEIPT_CARRIER)
    coverage = _carrier_state(meta_coverage, "instances")
    deterministic_ids = {identity[len("id:"):] for identity in identities
                         if identity.startswith("id:")}
    receipt_total = 0
    mapped_refs = 0
    projection_only: List[str] = []
    for receipt in receipts["items"]:
        if not isinstance(receipt, dict):
            continue
        receipt_total += 1
        for ref in (receipt.get("issue_refs") or []):
            ref_text = str(ref)
            if ref_text in deterministic_ids:
                mapped_refs += 1
            else:
                projection_only.append(
                    f"receipt:{receipt.get('rule_id', '?')}:{ref_text}")
    receipted = {str(ref) for receipt in receipts["items"] if isinstance(receipt, dict)
                 for ref in (receipt.get("issue_refs") or [])}
    unreferenced = sorted(
        identity for identity in identities
        if identity.startswith("id:") and identity[len("id:"):] not in receipted)

    return {
        "formal_carriers": {
            key: {
                "present": state["present"],
                "missing": state["missing"],
                "null_value": state.get("null_value", False),
                # 外层门禁需要原始类型诊断；不能在组件投影中丢掉后再当未知放行。
                "non_list": state.get("non_list", False),
                "invalid_items": state.get("invalid_items", False),
                "invalid_item_indices": state.get("invalid_item_indices", []),
                "invalid_bucket_keys": state.get("invalid_bucket_keys", []),
                "count": state["count"],
                "legal_empty": state["present"] and state["count"] == 0,
                "dict_format": state.get("dict_format", False),
                "dict_key_counts": state.get("dict_key_counts"),
                "projection_keys_excluded": state.get("projection_keys_excluded"),
                "projection_consistent": state.get("projection_consistent"),
                "unreadable_projection": state.get("unreadable_projection", False),
                "projection_inconsistent": state.get("projection_inconsistent", False),
            }
            for key, state in carriers.items()
        },
        "main_carrier": main_carrier_key,
        "formal_total_deduped": formal_total,
        "formal_findings": formal_findings,
        "multi_carrier_projection": {
            "count": len(multi_carrier),
            "items": multi_carrier[:50],
        },
        "conflicts": conflicts,
        "receipt_projection": {
            "present": receipts["present"],
            "missing": receipts["missing"],
            "receipt_total": receipt_total,
            "mapped_refs": mapped_refs,
            "projection_only_count": len(projection_only),
            "projection_only": projection_only[:50],
        },
        "coverage_projection": {
            "present": coverage["present"],
            "missing": coverage["missing"],
            "instance_total": coverage["count"],
        },
        "unreferenced_main_findings": unreferenced[:50],
        "no_cross_carrier_sum": True,
        "notes": [
            "正式问题载体=result.rule_findings / result.issues / review_report.issues（IR05 勘误）；"
            "check_receipts 与 obligation_coverage 是投影，不替代正式载体。",
            "formal_total_deduped 是三载体按身份去重的并集，不是三者相加。",
            "去重与比较用稳定内容指纹：易变 id（uuid:*）与时间戳不进入指纹，"
            "同内容换 id 计同一问题；同 id 内容变化（如金额 10→100）判异。",
            "键缺失=未知；键存在且为[]=合法空。本快照不签发验收结论（待审工具）。",
        ],
    }


def _input_identity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """提取输入身份字段（R3 复核§五.1）：checksum / analysis_basis_token。

    本组件是**问题载体统计组件**：只比较正式问题载体的规范化内容，不做
    材料/代际/配置身份门禁——身份字段在这里仅透出供外层正式验收入口使用，
    身份不一致时本组件输出不得当作跨材料/跨代际的比较结论。
    """
    status = payload.get("status") if isinstance(payload.get("status"), dict) else payload
    identity: Dict[str, Any] = {}
    for field in ("checksum", "analysis_basis_token"):
        value = status.get(field) if isinstance(status, dict) else None
        if value is not None:
            identity[field] = str(value)
    return identity


def _snapshot_from_status(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    status = payload.get("status") if isinstance(payload.get("status"), dict) else payload
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    coverage = meta.get("obligation_coverage") if isinstance(meta.get("obligation_coverage"), dict) else {}
    snapshot = three_carrier_snapshot(result, report, coverage)
    identity = _input_identity(payload)
    if identity:
        snapshot["input_identity"] = identity
    return snapshot


def scan(record_path: Path) -> int:
    print(json.dumps(_snapshot_from_status(record_path), ensure_ascii=False, indent=2))
    return 0


def compare(before_path: Path, after_path: Path) -> int:
    before, after = _snapshot_from_status(before_path), _snapshot_from_status(after_path)
    identical = _canonical(before) == _canonical(after)
    before_contents = {f["identity"]: f["content"] for f in before["formal_findings"]}
    after_contents = {f["identity"]: f["content"] for f in after["formal_findings"]}
    content_changes = sorted(
        identity for identity in set(before_contents) & set(after_contents)
        if _canonical(before_contents[identity]) != _canonical(after_contents[identity]))
    identity_before, identity_after = before.get("input_identity"), after.get("input_identity")
    identity_fields = sorted(set(identity_before or {}) | set(identity_after or {}))
    identity_match: Any = None
    if identity_fields:
        identity_match = all(
            (identity_before or {}).get(field) == (identity_after or {}).get(field)
            for field in identity_fields)
    statement = ("双采一致仅证明采集确定性；不构成独立验收结论，"
                 "不得据此签发'零回归'（R2.2 §8.1）。"
                 "本组件是问题载体统计组件：不做材料/代际/配置身份门禁，"
                 "正式比较须由具备身份门禁并经独立核验的外层入口完成。")
    if identity_match is False:
        statement = ("输入身份字段不一致（checksum/analysis_basis_token）——"
                     "两份输入不属同一材料/代际，本组件的问题载体内容比较"
                     "**不构成**跨材料/跨代际结论；") + statement
    print(json.dumps({
        "deterministic_identical": identical,
        "input_identity_before": identity_before,
        "input_identity_after": identity_after,
        "input_identity_match": identity_match,
        "content_changed_identities": content_changes,
        "before_formal_total": before["formal_total_deduped"],
        "after_formal_total": after["formal_total_deduped"],
        "conflicts_before": before["conflicts"],
        "conflicts_after": after["conflicts"],
        "statement": statement,
    }, ensure_ascii=False, indent=2))
    return 0


def selftest() -> int:
    """契约自测：缺字段 vs 合法空、三正式载体独占、去重、冲突、投影对接。"""
    checks: List[Tuple[str, bool]] = []

    snapshot = three_carrier_snapshot({}, {}, {})
    checks.append(("三正式载体全缺失记 missing", all(
        snapshot["formal_carriers"][key]["missing"] for key in _MAIN_ORDER)))

    # 单载体独占（IR05 负控）：rule_findings=[] 而 result.issues 有 1 条
    result = {"rule_findings": [], "issues": [{"id": "I-1", "rule": "V33-X", "message": "m"}]}
    snapshot = three_carrier_snapshot(result, {}, {})
    checks.append(("result.issues 独占被计数", snapshot["formal_total_deduped"] == 1
                   and snapshot["formal_carriers"]["result.rule_findings"]["legal_empty"] is True))

    # review_report.issues 独占
    report = {"issues": [{"id": "R-1", "rule": "V33-Y", "message": "m2"}]}
    snapshot = three_carrier_snapshot({"rule_findings": []}, report, {})
    checks.append(("review_report.issues 独占被计数", snapshot["formal_total_deduped"] == 1))

    # 真实旧任务形态：rule_findings 缺失、review_report.issues=2（水务局旧任务）
    snapshot = three_carrier_snapshot({}, {"issues": [{"id": "a"}, {"id": "b"}]}, {})
    checks.append(("主载体缺失时其余载体照常计数",
                   snapshot["formal_total_deduped"] == 2
                   and snapshot["formal_carriers"]["result.rule_findings"]["missing"] is True))

    # 真实生产 dict 形态（uploads 实测）：result.issues={"error":[...],"warn":[],"info":[],"all":投影}
    # ——all 是投影不读，error/warn/info 读全；否则真实格式漏读（QP-06 负控类）。
    dict_issue_a = {"id": "D-1", "rule": "V33-X", "message": "m1"}
    dict_issue_b = {"id": "D-2", "rule": "V33-X", "message": "m2"}
    result_dict = {"issues": {"error": [dict_issue_a, dict_issue_b], "warn": [], "info": [],
                              "all": [dict_issue_a, dict_issue_b]}}
    snapshot = three_carrier_snapshot(result_dict, {}, {})
    carriers = snapshot["formal_carriers"]["result.issues"]
    checks.append(("dict形态 result.issues 独占计数且all投影不双计",
                   snapshot["formal_total_deduped"] == 2
                   and carriers.get("dict_format") is True
                   and carriers["count"] == 2
                   and carriers["dict_key_counts"] == {"error": 2, "warn": 0, "info": 0}
                   and carriers["projection_keys_excluded"] == ["all"]
                   and carriers.get("projection_consistent") is True))

    # FA-03：只有 all 投影、无严重度桶且 all 非空 → 不可读，不得计 0/合法空。
    result_all_only = {"issues": {"all": [{"id": "F-2", "rule": "V33-Y", "message": "x"}]}}
    snapshot = three_carrier_snapshot(result_all_only, {}, {})
    carriers = snapshot["formal_carriers"]["result.issues"]
    checks.append(("只有all投影且非空 → unreadable_projection 不计0",
                   carriers.get("unreadable_projection") is True
                   and carriers.get("count") is None
                   and carriers.get("legal_empty") is False))

    # FA-03：严重度桶与 all 内容不一致（all 被清空）→ projection_inconsistent。
    result_partial = {"issues": {"error": [dict_issue_a], "warn": [], "info": [], "all": []}}
    snapshot = three_carrier_snapshot(result_partial, {}, {})
    carriers = snapshot["formal_carriers"]["result.issues"]
    checks.append(("all被清空而严重度桶仍有内容 → projection_inconsistent 不计0",
                   carriers.get("projection_inconsistent") is True
                   and carriers.get("count") is None))

    # M-03：all 含严重度桶之外的问题（仅 all 新增）→ 双向不一致，不得忽略。
    result_extra_all = {"issues": {"error": [dict_issue_a], "warn": [], "info": [],
                                   "all": [dict_issue_a, {"id": "F-extra", "rule": "V33-Y", "message": "x"}]}}
    snapshot = three_carrier_snapshot(result_extra_all, {}, {})
    carriers = snapshot["formal_carriers"]["result.issues"]
    checks.append(("all新增桶外问题 → projection_inconsistent 双向检出",
                   carriers.get("projection_inconsistent") is True
                   and carriers.get("count") is None))

    # 跨载体重复投影去重
    duplicated = {"rule_findings": [{"id": "F-1", "rule": "V33-X", "message": "m"}]}
    report_dup = {"issues": [{"id": "F-1", "rule": "V33-X", "message": "m"}]}
    snapshot = three_carrier_snapshot(duplicated, report_dup, {})
    checks.append(("跨载体同内容去重计一次", snapshot["formal_total_deduped"] == 1
                   and snapshot["multi_carrier_projection"]["count"] == 1))

    # 跨载体同 id 不同内容 → 冲突（以主载体为准）
    report_conflict = {"issues": [{"id": "F-1", "rule": "V33-X", "message": "不同内容"}]}
    snapshot = three_carrier_snapshot(duplicated, report_conflict, {})
    checks.append(("同 id 异内容登记冲突", len(snapshot["conflicts"]) == 1
                   and snapshot["conflicts"][0]["kind"] == "carrier_content_mismatch"))

    # 投影对接
    result_full = {"rule_findings": [{"id": "F-1", "rule": "V33-X", "message": "m"},
                                     {"id": "F-2", "rule": "V33-W", "message": "n"}]}
    report_proj = {"check_receipts": [
        {"rule_id": "V33-X", "status": "fail", "issue_refs": ["F-1"]},
        {"rule_id": "V33-Y", "status": "fail", "issue_refs": ["F-9"]},
    ]}
    snapshot = three_carrier_snapshot(result_full, report_proj, {})
    checks.append(("投影命中/独有分账", snapshot["receipt_projection"]["mapped_refs"] == 1
                   and snapshot["receipt_projection"]["projection_only_count"] == 1))
    checks.append(("无引用正式问题登记", set(snapshot["unreferenced_main_findings"]) == {"id:F-2"}))

    # R2-04 控制A：同 id 内容变化（金额10→100）必须判异——快照未保留内容时
    # 曾被误判 deterministic_identical=true。
    base_issue = {"id": "F1", "rule": "V33-X", "message": "合计10万元与分项8万元不一致", "page": 8}
    snap_a = three_carrier_snapshot({"rule_findings": [base_issue]}, {}, {})
    snap_b = three_carrier_snapshot(
        {"rule_findings": [{**base_issue, "message": "合计100万元与分项8万元不一致"}]}, {}, {})
    checks.append(("同id内容变化判异（金额10→100）", _canonical(snap_a) != _canonical(snap_b)))

    # R2-04 控制B：同内容仅换易变 id（uuid:one→uuid:two）→ 去重计 1——
    # 内容指纹含 id 时曾被计成 2。
    same = {"id": "uuid:one", "rule": "V33-X", "message": "同一问题", "page": 8}
    snap_c = three_carrier_snapshot(
        {"rule_findings": [same]}, {"issues": [{**same, "id": "uuid:two"}]}, {})
    checks.append(("易变id同内容去重计一次", snap_c["formal_total_deduped"] == 1
                   and snap_c["multi_carrier_projection"]["count"] == 1))

    # 内容变化在 compare 维度可见：content_changed_identities 报出身份
    changed_a = three_carrier_snapshot({"rule_findings": [base_issue]}, {}, {})
    changed_b = three_carrier_snapshot(
        {"rule_findings": [{**base_issue, "message": "合计100万元与分项8万元不一致"}]}, {}, {})
    diff_ids = {f["identity"] for f in changed_a["formal_findings"]} & \
               {f["identity"] for f in changed_b["formal_findings"]}
    checks.append(("内容变化身份可见", diff_ids == {"id:F1"}
                   and [f["content"] for f in changed_a["formal_findings"]][0]["message"]
                   != [f["content"] for f in changed_b["formal_findings"]][0]["message"]))

    failed = [name for name, ok in checks if not ok]
    for name, ok in checks:
        print(("PASS " if ok else "FAIL ") + name)
    if failed:
        print(f"selftest FAILED: {failed}")
        return 1
    print("selftest PASS（仅证明契约实现自洽；不构成独立验收）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="R2.2 §8.1 三载体契约核查（待审工具）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_scan = sub.add_parser("scan")
    p_scan.add_argument("--record", required=True, type=Path)
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("--before", required=True, type=Path)
    p_cmp.add_argument("--after", required=True, type=Path)
    sub.add_parser("selftest")
    args = parser.parse_args()
    if args.command == "scan":
        return scan(args.record)
    if args.command == "compare":
        return compare(args.before, args.after)
    return selftest()


if __name__ == "__main__":
    sys.exit(main())
