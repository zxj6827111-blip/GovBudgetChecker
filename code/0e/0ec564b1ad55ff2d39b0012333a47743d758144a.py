#!/usr/bin/env python3
"""最终 114 台账（R-03 版）：只按当前证据重算，不按目标编号赋 fixed。

- MISS：读取 evaluate_114_targets.py 的 ``anchor_entries``（已按版本化
  occurrence_contract 剔除正确对照句）。剩余缺陷出现点必须逐条由 F 层、
  证据合格、同检查规则、同页码的条目覆盖；缺一条就不 fixed。
- FP：读取 ``best_current_match``。只有当前条目的正文数字与基线不同、
  或已降为非正式层时，才认定旧缺陷已消失/降级；同一正式条目以相同
  正文数字仍在，则保持未完成。
- 待裁定：原 5 条 + GS-2024-06 主体口径来源冲突；只改状态依据，不把
  pending 包装成 fixed。禁止 target_id 白名单直接 fixed。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

#: 原 5 条待裁定 + 1 个根因（2 个目标）。理由已按独立复核 20261104 修正。
KNOWN_PENDING = {
    "FP|GS-2022-03|V33-202-16": (
        "跨表 0.02 差额：财政拨款总表一般公共预算本年支出 3,247,963.53 "
        "vs 功能分类表合计 3,247,963.55，差 -0.02；是否属显示舍入待独立裁定。"
    ),
    "FP|GS-2024-02|V33-202-9": (
        "跨表 0.02 差额：财政拨款总表一般公共预算本年支出 109,202.37 "
        "vs 功能分类表合计 109,202.39，差 -0.02；是否属显示舍入待独立裁定。"
    ),
    "MISS|GS-2022-06|C|GS-N05|1": "N05 名称差异存在真实语义歧义，候选待人工确认",
    "MISS|GS-2023-05|C|GS-N05|2": "N05 名称差异存在真实语义歧义，候选待人工确认",
    "MISS|GS-2024-08|C|GS-N05|2": "N05 名称差异存在真实语义歧义，候选待人工确认",
    "FP|GS-2024-06|CMM-018-33": (
        "GS-2024-06 主体口径来源冲突：writing_scope.archive_basis 来自 upload_selection"
        "（department），封面为无“部门”的年度决算；自动上传选择不等于已授权人工写作口径，"
        "需追溯实际上传参数/人工确认/封面契约后裁定。"
    ),
    "MISS|GS-2024-06|U|F01": (
        "与 FP|GS-2024-06|CMM-018-33 同根因：archive upload_selection 与封面单位口径冲突，"
        "待口径来源裁定后重算。"
    ),
}


def _amounts(item) -> list:
    return sorted(str(value) for value in (item or {}).get("amounts") or [])


def decide(row: dict) -> tuple[str, str]:
    if row["type"] == "FP":
        state = row.get("current_state")
        if state == "removed":
            return "fixed", "原正式条目已从当前运行移除，且无同规则同页同正文残留。"
        if state == "baseline_identity_missing":
            return "strict_partial", "冻结基线中找不到原条目，无法证明移除。"
        best = row.get("best_current_match") or {}
        if not best:
            return "strict_partial", "当前运行仍有同规则同页正文匹配，但未定位到具体实体。"
        same_amounts = _amounts({"amounts": best.get("amounts")}) == \
            _amounts({"amounts": row.get("baseline_amounts")})
        similarity = float(best.get("similarity") or 0)
        if best.get("tier") == "F" and similarity >= 0.9 and same_amounts:
            return "strict_partial", (
                "同一正式条目的正文数字与基线一致（相似度 "
                f"{similarity:.2f}），旧缺陷仍以正式问题存在。"
            )
        return "fixed", (
            "当前匹配条目与基线不是同一实体：金额 {base}→{now}、层级 {tier}、"
            "相似度 {sim:.2f}；旧缺陷已消失/降级为非正式。"
        ).format(base=row.get("baseline_amounts"), now=best.get("amounts"),
                 tier=best.get("tier"), sim=similarity)

    entries = row.get("anchor_entries") or []
    if not entries:
        return "strict_partial", "没有可核验的缺陷出现点（不得凭目标编号判 fixed）。"
    covered = [entry for entry in entries if entry.get("covered")]
    if len(covered) == len(entries):
        return "fixed", "剩余缺陷出现点全部由 F 层、证据合格、同规则同页码的条目覆盖。"
    missing = [entry.get("quote") for entry in entries if not entry.get("covered")]
    return "strict_partial", f"仍有 {len(missing)} 个缺陷出现点未覆盖：{missing[:3]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    payload = json.loads(args.ledger.read_text(encoding="utf-8"))
    rows = payload["rows"]

    final_rows, pending_rows = [], []
    for row in rows:
        tid = row["target_id"]
        state, basis = decide(row)
        excluded = row.get("excluded_control_entries") or []
        if tid in KNOWN_PENDING:
            state = "pending_adjudication"
            basis = KNOWN_PENDING[tid]
        record = {
            "target_id": tid,
            "stage": row.get("stage"),
            "type": row["type"],
            "sample_id": row["sample_id"],
            "rule_or_check": row.get("rule_or_check"),
            "gold_units": row.get("gold_units"),
            "evidence_pages": row.get("evidence_pages"),
            "final_state": state,
            "current_state": row.get("current_state"),
            "anchors_total": row.get("anchors_total"),
            "anchors_covered": row.get("anchors_covered"),
            "excluded_controls": " | ".join(str(item.get("quote")) for item in excluded),
            "covering_f_items": " | ".join(row.get("covering_f_items") or []),
            "new_f_items": " | ".join(row.get("new_f_items") or []),
            "historical_status": row.get("historical_status"),
            "strict_status": row.get("strict_status"),
            "basis": basis,
            "supersedes_note": (
                "本行按 occurrence-binding-20261104 与当前候选运行重算；"
                "旧 106/7/1 与 88/21/5 均只作历史对照。"
            ),
        }
        final_rows.append(record)
        if state == "pending_adjudication":
            pending_rows.append({
                "target_id": tid, "type": row["type"], "sample_id": row["sample_id"],
                "reason": basis, "blocks": "不计入 fixed；待独立裁定",
            })

    columns = list(final_rows[0].keys())
    with (args.out / "target_status.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(final_rows)
    with (args.out / "occurrence_coverage.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["target_id", "sample_id", "anchors_total", "anchors_covered",
                         "excluded_controls", "missing_anchors", "covering_f_items"])
        for row in rows:
            if row["type"] != "MISS":
                continue
            writer.writerow([
                row["target_id"], row["sample_id"], row.get("anchors_total", ""),
                row.get("anchors_covered", ""),
                " | ".join(str(item.get("quote")) for item in row.get("excluded_control_entries") or []),
                " | ".join(row.get("missing_anchors") or []),
                " | ".join(row.get("covering_f_items") or []),
            ])
    with (args.out / "known_pending.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["target_id", "type", "sample_id", "reason", "blocks"])
        writer.writeheader()
        writer.writerows(pending_rows)

    counts = {}
    for row in final_rows:
        counts[row["final_state"]] = counts.get(row["final_state"], 0) + 1
    (args.out / "target_status_summary.json").write_text(
        json.dumps({"run": payload["run"], "baseline_run": payload["baseline_run"],
                    "targets": len(final_rows), "final_state_counts": counts,
                    "pending": len(pending_rows),
                    "note": "不按目标编号赋 fixed；对照句按版本化 quote 剔除后重算。"},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"counts": counts, "pending": len(pending_rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
