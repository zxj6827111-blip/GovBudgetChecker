#!/usr/bin/env python3
"""R2.2 §8.1 三载体数据契约核查工具（待审——未经独立核验不得签发验收结论）。

背景（计划勘误6）：正式问题有三个载体——

- **主载体**：``result.rule_findings``（以结果代际 schema 声明为准）；
- **投影 1**：``review_report.check_receipts``（关系级回执）；
- **投影 2**：``result.meta.obligation_coverage``（义务台账）。

契约（本工具实现的唯一事实来源）：

1. **主载体优先**：正式问题计数只读主载体；投影不是独立问题源。
2. **重复投影去重**：回执里指向主载体 finding 的引用（issue_id → 指纹）
   记为"投影命中"，不重复计数；主载体缺席而投影出现的引用记为
   ``projection_only``（投影差异，不是新问题）。
3. **冲突处理**：回执判定 fail 但其引用在主载体全部缺失 → 冲突（以主载体
   为准并登记）；主载体 finding 无任何回执引用 → 投影缺口（登记，不算冲突）。
4. **缺字段 vs 合法空列表**：键不存在 = 缺失（未知，不得当 0 参与任何求和）；
   键存在且为 [] = 合法空（计 0）。三载体**不直接相加**，本工具不输出跨
   载体合计。

纪律：本工具是 R2.2 §8.1 的待审交付，未通过独立核验前，其输出只能作为
问题登记，不能作为"compare 不变量已具备"或"零回归"的签发依据；双采一致
只证明确定性。冻结基线验收工具仍是 main 上的 ``scripts/verify_pending_review.py``
（@0420d472，KIT v1-regression-r2），本工具不改写它。

用法：
    python scripts/verify_three_carriers.py scan   --record status.json
    python scripts/verify_three_carriers.py compare --before a.json --after b.json
    python scripts/verify_three_carriers.py selftest

（--record/--before/--after 接**原始任务状态 JSON**；冻结验收器的 collect
记录格式不在本工具输入口径内，见 three_carrier_snapshot 文档。）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

_MAIN_CARRIER = "rule_findings"
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


def finding_identity(finding: Dict[str, Any]) -> str:
    """主载体 finding 身份：确定性 id 优先，UUID/无 id 用内容指纹。"""
    finding_id = str(finding.get("id") or "")
    if finding_id and not finding_id.startswith("uuid:"):
        return f"id:{finding_id}"
    stable = {k: v for k, v in finding.items()
              if k not in {"id", "created_at", "updated_at"}}
    return "fp:" + hashlib.sha1(_canonical(stable).encode("utf-8")).hexdigest()[:16]


def three_carrier_snapshot(result: Any, review_report: Any, meta_coverage: Any) -> Dict[str, Any]:
    """按契约产出三载体快照（纯函数，可独立核验）。

    输入口径：**原始任务状态 JSON**（含 result / review_report /
    result.meta.obligation_coverage），不是冻结验收器的 collect 记录——
    collect 记录里回执的 issue_refs 已被验收器替换为内容指纹（sha256），
    与本工具的 id 映射口径不同；采集格式适配属于独立核验后的后续工作，
    本工具不猜映射。
    """
    main = _carrier_state(result, _MAIN_CARRIER)
    receipts = _carrier_state(review_report, _RECEIPT_CARRIER)
    coverage = _carrier_state(meta_coverage, "instances")

    main_identities = {
        finding_identity(f): f for f in main["items"] if isinstance(f, dict)
    } if main["present"] else {}

    receipt_total = 0
    mapped_refs = 0
    projection_only: List[str] = []
    conflicts: List[Dict[str, Any]] = []
    for receipt in receipts["items"] if receipts["present"] else []:
        if not isinstance(receipt, dict):
            continue
        receipt_total += 1
        refs = [str(ref) for ref in (receipt.get("issue_refs") or [])]
        # 引用与主载体对接：回执里的 issue_ref 是主载体 finding 的 id。
        mapped = [ref for ref in refs if f"id:{ref}" in main_identities]
        mapped_refs += len(mapped)
        unmapped = [ref for ref in refs if f"id:{ref}" not in main_identities]
        projection_only.extend(f"receipt:{receipt.get('rule_id', '?')}:{ref}" for ref in unmapped)
        if str(receipt.get("status")) == "fail" and refs and not mapped:
            conflicts.append({
                "kind": "fail_without_main_finding",
                "rule_id": str(receipt.get("rule_id") or ""),
                "unmapped_refs": unmapped,
                "resolution": "以主载体为准：该关系正式问题在主载体缺席，登记冲突待人工复核",
            })

    receipted_ids = set()
    for receipt in receipts["items"] if receipts["present"] else []:
        if isinstance(receipt, dict):
            receipted_ids.update(str(ref) for ref in (receipt.get("issue_refs") or []))
    unreferenced_findings = sorted(
        identity for identity in main_identities
        if identity.removeprefix("id:") not in receipted_ids
    )

    return {
        "main_carrier": {
            "present": main["present"],
            "missing": main["missing"],
            "null_value": main.get("null_value", False),
            "count": main["count"] if main["count"] is not None else None,
            "legal_empty": main["present"] and main["count"] == 0,
        },
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
            "instance_total": coverage["count"] if coverage["count"] is not None else None,
        },
        "conflicts": conflicts,
        "unreferenced_main_findings": unreferenced_findings[:50],
        "no_cross_carrier_sum": True,
        "notes": [
            "主载体=rule_findings；投影=check_receipts/obligation_coverage；三载体不直接相加。",
            "键缺失=未知；键存在且为[]=合法空。本快照不签发验收结论（待审工具）。",
        ],
    }


def scan(record_path: Path) -> int:
    """对一份 collect 记录（或直接含 result/review_report 的状态 JSON）产出快照。"""
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    status = payload.get("status") if isinstance(payload.get("status"), dict) else payload
    result = status.get("result") if isinstance(status.get("result"), dict) else {}
    report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    coverage = meta.get("obligation_coverage") if isinstance(meta.get("obligation_coverage"), dict) else {}
    snapshot = three_carrier_snapshot(result, report, coverage)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


def compare(before_path: Path, after_path: Path) -> int:
    """双采比较：只声明确定性一致/差异，**不签发零回归**（纪律写进输出）。"""
    def _snapshot(path: Path) -> Dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        status = payload.get("status") if isinstance(payload.get("status"), dict) else payload
        result = status.get("result") if isinstance(status.get("result"), dict) else {}
        report = status.get("review_report") if isinstance(status.get("review_report"), dict) else {}
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        coverage = meta.get("obligation_coverage") if isinstance(meta.get("obligation_coverage"), dict) else {}
        return three_carrier_snapshot(result, report, coverage)

    before, after = _snapshot(before_path), _snapshot(after_path)
    identical = _canonical(before) == _canonical(after)
    print(json.dumps({
        "deterministic_identical": identical,
        "before_main_count": before["main_carrier"]["count"],
        "after_main_count": after["main_carrier"]["count"],
        "conflicts_before": before["conflicts"],
        "conflicts_after": after["conflicts"],
        "statement": ("双采一致仅证明采集确定性；不构成独立验收结论，"
                      "不得据此签发'零回归'（R2.2 §8.1）。"),
    }, ensure_ascii=False, indent=2))
    return 0


def selftest() -> int:
    """契约自测：缺字段 vs 合法空、投影去重、冲突、不直接相加。"""
    checks: List[Tuple[str, bool]] = []

    # 缺字段 vs 合法空
    snapshot = three_carrier_snapshot({}, {}, {})
    checks.append(("主载体缺失记 missing", snapshot["main_carrier"]["missing"] is True
                   and snapshot["main_carrier"]["count"] is None))
    snapshot = three_carrier_snapshot({"rule_findings": []}, {"check_receipts": []}, {"instances": []})
    checks.append(("合法空列表计 0", snapshot["main_carrier"]["legal_empty"] is True
                   and snapshot["main_carrier"]["count"] == 0))

    # 投影命中去重 + fail-without-main 冲突
    result = {"rule_findings": [{"id": "F-1", "rule": "V33-X", "message": "金额不一致"}]}
    report = {"check_receipts": [
        {"rule_id": "V33-X", "status": "fail", "issue_refs": ["F-1"]},
        {"rule_id": "V33-Y", "status": "fail", "issue_refs": ["F-9"]},
    ]}
    snapshot = three_carrier_snapshot(result, report, {})
    checks.append(("投影命中去重", snapshot["receipt_projection"]["mapped_refs"] == 1))
    checks.append(("投影独有登记", snapshot["receipt_projection"]["projection_only_count"] == 1))
    checks.append(("fail 缺主载体 → 冲突", len(snapshot["conflicts"]) == 1
                   and snapshot["conflicts"][0]["rule_id"] == "V33-Y"))

    # pass 回执不产生冲突；主载体 finding 无回执引用 → 投影缺口
    report_ok = {"check_receipts": [{"rule_id": "V33-Z", "status": "pass", "issue_refs": []}]}
    snapshot = three_carrier_snapshot(result, report_ok, {})
    checks.append(("pass 不冲突", snapshot["conflicts"] == []))
    checks.append(("无引用 finding 登记", snapshot["unreferenced_main_findings"] == ["id:F-1"]))

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
