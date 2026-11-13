#!/usr/bin/env python3
"""生成最终 114 项台账（target_status.csv / occurrence_coverage.csv / known_pending.csv）。

输入是 evaluate_114_targets.py 在最终候选运行上的逐项结果。人工决定只用于
区分“同一缺陷已消失 / 被降级为非正式 / 原出现点含正确对照句 / 真待裁定”，
每条都写明依据；不按目标编号改变比较语义，也不把待裁定包装成 fixed。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

#: FP 目标：当前运行仍能按规则+页码+正文匹配到条目时的逐条定性。
FP_DECISIONS = {
    "FP|GS-2022-08|V33-110-14": {
        "state": "fixed",
        "basis": "原缺陷金额（预算数144.71/决算数0.01）已不在当前运行；同规则同页新条目为不同金额（3559.82/3497.37）的另一处比较，不是原缺陷残留。",
    },
    "FP|GS-2024-04|V33-251-8": {
        "state": "fixed",
        "basis": "原“支出决算合计与所列总额差1176.68万元”已消失；当前同规则条目改为年初预算口径（差300.00万元），属不同检查关系。",
    },
    "FP|GS-2024-06|CMM-018-33": {
        "state": "pending_adjudication",
        "basis": "同一“本单位/本部门”措辞仍在（现 CMM-018-40）。上传归档层级为 department、封面为“2024年度决算”（无“部门”），金标按单位口径标注；归档选择与封面独立性冲突，需独立裁定口径来源优先级。",
    },
    "FP|GS-2022-06|V33-234-9": {
        "state": "fixed",
        "basis": "原条目（一般公共服务支出 1276.81/861.89，完成率67.50%）已消失；当前 P 条目是社会保障和就业支出的另一处完成率，未说明原因的对象不同。",
    },
    "FP|GS-2022-03|V33-117-18": {
        "state": "fixed",
        "basis": "同一 0.03 万元差额现由 R 层“可能为取整误差”提示承接，不再作为正式问题；原 FP 的正式层残留已消除。",
    },
    "FP|GS-2022-03|V33-251-20": {
        "state": "fixed",
        "basis": "原条目现为 R 层提示（NOTICE_ONLY），不再计入正式问题。",
    },
    "FP|GS-2023-01|V33-251-14": {
        "state": "fixed",
        "basis": "原条目现为 R 层提示（NOTICE_ONLY），不再计入正式问题。",
    },
    "FP|GS-2023-05|V33-251-16": {
        "state": "fixed",
        "basis": "原条目现为 R 层提示（NOTICE_ONLY），不再计入正式问题；同页 F 条目为另一检查关系（年初预算合计）。",
    },
}

#: MISS 目标：出现点覆盖不足时的逐条定性。
MISS_DECISIONS = {
    "MISS|GS-2024-06|C|GS-W01|0": {
        "state": "fixed",
        "basis": "缺陷出现点“自然资源海洋气象等支出1,659.14”（漏万元）已由 CMM-011 正式命中；未覆盖的“住房保障支出9,783.06 万元”是带单位的正确对照句，按“正确对照句不作缺陷”不计。",
        "control_anchors": ["住房保障支出9,783.06 万元"],
    },
    "MISS|GS-2024-04|U|F01": {
        "state": "strict_partial",
        "basis": "10 处相反口径中 9 处已正式命中；剩余“2024年度本部门面向中小企业预留政府采购项目预算金额0万元”未覆盖，属真实出现点缺口，保留严格未完成。",
    },
    "MISS|GS-2024-05|C|GS-S01|0": {
        "state": "fixed",
        "basis": "目录与正文两处表名均已由 CMM-021 正式命中；未覆盖的两句是“本年度没有政府性基金/国有资本经营预算…故本表无数据”的正确空表说明，按正确对照句不计。",
        "control_anchors": [
            "说明：上海药品审评核查中心本年度没有政府性基金预算财政拨款收入和支出，故本表无数据。",
            "说明：上海药品审评核查中心本年度没有国有资本经营预算财政拨款收入和支出，故本表无数据。",
        ],
    },
    "MISS|GS-2024-06|C|GS-N03|0": {
        "state": "fixed",
        "basis": "缺陷出现点“项目支出217,7380.31 万元”（千分位错位）已由 CMM-020/CMM-010/V33-222 正式命中；未覆盖的“本年支出合计2,296,588.24 万元”“基本支出119,207.93 万元”是正确金额对照，按正确对照句不计。",
        "control_anchors": [
            "本年支出合计2,296,588.24 万元",
            "基本支出119,207.93 万元",
        ],
    },
    "MISS|GS-2024-06|U|F01": {
        "state": "pending_adjudication",
        "basis": "与 FP|GS-2024-06|CMM-018-33 同一根因：上传归档层级为 department、封面为无“部门”的年度决算，金标按单位口径要求正文不得出现“部门”表述。需先裁定口径来源，再决定本目标。",
    },
}

KNOWN_PENDING = {
    "FP|GS-2022-03|V33-202-16": "V33-202 上年预算为 0 的完成率/舍入证据待裁定",
    "FP|GS-2024-02|V33-202-9": "V33-202 上年预算为 0 的完成率/舍入证据待裁定",
    "MISS|GS-2022-06|C|GS-N05|1": "N05 名称差异存在真实语义歧义，候选待人工确认",
    "MISS|GS-2023-05|C|GS-N05|2": "N05 名称差异存在真实语义歧义，候选待人工确认",
    "MISS|GS-2024-08|C|GS-N05|2": "N05 名称差异存在真实语义歧义，候选待人工确认",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    payload = json.loads(args.ledger.read_text(encoding="utf-8"))
    rows = payload["rows"]

    final_rows = []
    pending_rows = []
    for row in rows:
        tid = row["target_id"]
        state = None
        basis = ""
        if row["type"] == "FP":
            if row["current_state"] == "removed":
                state, basis = "fixed", "原正式条目已从当前运行移除，且无同规则同页同正文残留。"
            elif row["current_state"] == "baseline_identity_missing":
                state, basis = "strict_partial", "冻结基线中找不到原条目，无法证明移除。"
            else:
                decision = FP_DECISIONS.get(tid)
                if decision:
                    state, basis = decision["state"], decision["basis"]
                else:
                    state, basis = "strict_partial", "当前运行仍有同规则同页正文匹配，未逐条定性。"
        else:
            if row["current_state"] == "satisfied":
                state, basis = "fixed", "全部出现点均由 F 层且证据合格的正式条目覆盖。"
            else:
                decision = MISS_DECISIONS.get(tid)
                if decision:
                    state, basis = decision["state"], decision["basis"]
                else:
                    state, basis = "strict_partial", "出现点覆盖不足，未逐条定性。"
        if tid in KNOWN_PENDING:
            state, basis = "pending_adjudication", KNOWN_PENDING[tid]
        record = {
            "target_id": tid,
            "stage": row["stage"],
            "type": row["type"],
            "sample_id": row["sample_id"],
            "rule_or_check": row.get("rule_or_check"),
            "gold_units": row.get("gold_units"),
            "evidence_pages": row.get("evidence_pages"),
            "final_state": state,
            "current_state": row["current_state"],
            "anchors_total": row.get("anchors_total"),
            "anchors_covered": row.get("anchors_covered"),
            "covering_f_items": " | ".join(row.get("covering_f_items") or []),
            "new_f_items": " | ".join(row.get("new_f_items") or []),
            "historical_status": row.get("historical_status"),
            "strict_status": row.get("strict_status"),
            "basis": basis,
            "superseded_history_note": (
                "旧台账 88 fixed / 21 strict_partial 基于 s4b/gold/final（93ea0768 前）。"
                "本行按候选 HEAD 双跑重评；旧状态仅作历史对照，不再作为当前结论。"
            ),
        }
        final_rows.append(record)
        if state == "pending_adjudication":
            pending_rows.append({
                "target_id": tid,
                "type": row["type"],
                "sample_id": row["sample_id"],
                "reason": basis,
                "blocks": "该目标不得计入 fixed；其余目标继续完成",
            })

    columns = list(final_rows[0].keys())
    with (args.out / "target_status.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(final_rows)

    with (args.out / "occurrence_coverage.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["target_id", "sample_id", "anchors_total", "anchors_covered",
                         "missing_anchors", "covering_f_items"])
        for row in rows:
            if row["type"] != "MISS":
                continue
            writer.writerow([
                row["target_id"], row["sample_id"], row.get("anchors_total", ""),
                row.get("anchors_covered", ""),
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
        json.dumps({
            "run": payload["run"],
            "baseline_run": payload["baseline_run"],
            "targets": len(final_rows),
            "final_state_counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "pending": len(pending_rows),
            "note": "严格未完成只保留有明确原文依据的缺口；待裁定不计入 fixed。",
        }, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"counts": counts, "pending": len(pending_rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
