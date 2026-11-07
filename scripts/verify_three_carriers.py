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
from typing import Any, Dict, List, Tuple

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


def finding_content(finding: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in finding.items() if k not in {"created_at", "updated_at"}}


def finding_identity(finding: Dict[str, Any]) -> str:
    """身份：确定性 id 优先；UUID/无 id 用内容指纹。"""
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
        "result.rule_findings": _carrier_state(result, "rule_findings"),
        "result.issues": _carrier_state(result, "issues"),
        "review_report.issues": _carrier_state(review_report, "issues"),
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
            entry: Dict[str, Any] = {"identity": identity, "carriers": sorted(occurrences)}
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
                "count": state["count"],
                "legal_empty": state["present"] and state["count"] == 0,
            }
            for key, state in carriers.items()
        },
        "main_carrier": main_carrier_key,
        "formal_total_deduped": formal_total,
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
            "键缺失=未知；键存在且为[]=合法空。本快照不签发验收结论（待审工具）。",
        ],
    }


def _snapshot_from_status(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    status = payload.get("status") if isinstance(payload.get("status"), dict) else payload
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    coverage = meta.get("obligation_coverage") if isinstance(meta.get("obligation_coverage"), dict) else {}
    return three_carrier_snapshot(result, report, coverage)


def scan(record_path: Path) -> int:
    print(json.dumps(_snapshot_from_status(record_path), ensure_ascii=False, indent=2))
    return 0


def compare(before_path: Path, after_path: Path) -> int:
    before, after = _snapshot_from_status(before_path), _snapshot_from_status(after_path)
    identical = _canonical(before) == _canonical(after)
    print(json.dumps({
        "deterministic_identical": identical,
        "before_formal_total": before["formal_total_deduped"],
        "after_formal_total": after["formal_total_deduped"],
        "conflicts_before": before["conflicts"],
        "conflicts_after": after["conflicts"],
        "statement": ("双采一致仅证明采集确定性；不构成独立验收结论，"
                      "不得据此签发'零回归'（R2.2 §8.1）。"),
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
