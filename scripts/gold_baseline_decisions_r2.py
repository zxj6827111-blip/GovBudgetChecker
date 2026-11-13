"""金标基线 r2：在 v1 人工复核结论（gold_baseline_decisions.py，保持不变）的基础上，按修订后的金标（R9 轮，2026-11-03）改判。

只改两类行：
1. 金标新增/改判了对应单位的行：v1 里记为 FP_GOLD（GOLD_MISS / R26 / GOLD_INCONSISTENT）、ADJ_TRUE、TRUE_NOT_IN_GOLD 的，现在连到新的金标单位，改为 TP / HIT；
2. 新裁定（R26 细化、R30）只更新原因标签的行：零预算完成率 6 条、GS-2022-06 的“下降0%”、“原因仅重述”3 条，仍是误报。
其余沿用 v1。
"""

from __future__ import annotations

import copy

import gold_baseline_decisions as v1
from gold_baseline_decisions import C, U

D = copy.deepcopy(v1.D)


def upd(sid: str, raw: str, **changes) -> None:
    key = f"{sid}|{raw}"
    assert key in D, key
    D[key].update(changes)


# ---- 1. 金标已采纳的修订：现在有对应单位
upd("GS-2024-03", "V33-227-9", final="TP", cause="", amend=False, links=[C("GS-2024-03", "GS-N05", 2)],
    why="第16项款名“涉外发展服务”漏“支出”；金标 R9 轮已改 GS-2024-03 N05 为 defect（v1 为 FP_金标）")
upd("GS-2024-03", "CMM-008-12", final="TP", amend=False, links=[U("GS-2024-03", "F01")],
    why="模板待选项“部门（单位）”未选；金标 R9 轮新增自由发现 F01（模板残留，尽力类）")
upd("GS-2024-03", "CMM-017-11", amend=False,
    why="与金标 F01 同一处文字；系统按“单位名称多字”诊断，金标按模板残留记，诊断不同故不连边，PDF 复核为真")
upd("GS-2024-07", "V33-TREND-COMPLETION-RATE-15", final="TP", cause="", amend=False, links=[C("GS-2024-07", "GS-N05", 3)],
    why="完成率 95.83% 应为 97.56%；金标 R9 轮新增 N05 逐项子项“完成率”（v1 为 FP_金标）")
for raw in ("V33-227-7", "V33-227-8"):
    upd("GS-2025-02", raw, final="TP", cause="", amend=False, links=[C("GS-2025-02", "GS-N05", 2)],
        why="第12/15项款名错；金标 R9 轮已把两处补进 GS-2025-02 N05 逐项科目名称子项（v1 为 FP_金标）")
upd("GS-2025-04", "CMM-007-5", final="TP", cause="", amend=False, links=[C("GS-2025-04", "GS-N07", 2)],
    why="上年数为0、本年0.16却写“增长100.00%”；金标 R9 轮 GS-2025-04 N07 改 defect（v1 为 FP_金标）")
upd("GS-2025-07", "CMM-009-1", final="TP", amend=False, links=[U("GS-2025-07", "F02")],
    why="“公务车运行维护费”；金标 R9 轮新增自由发现 F02（错别字，尽力类）")
upd("GS-2025-09", "V33-105-7", final="TP", cause="", amend=False, links=[C("GS-2025-09", "GS-N04", 0)],
    why="财政拨款收入支出总计6533.55≠总表6,602.06；金标 R9 轮 GS-2025-09 N04 改 defect（v1 为 FP_金标）")
upd("GS-2022-04", "V33-227-7", final="HIT", amend=False, links=[C("GS-2022-04", "GS-N05", 2)],
    why="第6项款名错配；金标 R9 轮 GS-2022-04 N05 改 defect（v1 为 TRUE_NOT_IN_GOLD）")

# ---- 2. 新裁定只改原因标签（仍是误报）
for sid, raw in (("GS-2022-07", "V33-TREND-COMPLETION-RATE-2"), ("GS-2022-07", "V33-TREND-COMPLETION-RATE-3"),
                 ("GS-2023-04", "V33-TREND-COMPLETION-RATE-8"), ("GS-2023-08", "V33-TREND-COMPLETION-RATE-6"),
                 ("GS-2025-01", "V33-TREND-COMPLETION-RATE-10"), ("GS-2025-07", "V33-TREND-COMPLETION-RATE-2")):
    upd(sid, raw, cause="R004_RULED",
        why="预算0、决算0写“完成预算的0.00%”：R26 细化（2026-11-03）裁定 0/0 不算缺陷，AGENTS R004 收窄为“预算0而决算大于0”。系统仍报，计误报")
upd("GS-2022-06", "CMM-007-14", cause="R26_ZERO_OVER_ZERO", amend=False,
    why="上年0、本年0写“下降0%”：R26 细化裁定 0/0 不算缺陷，金标不改；系统此条计误报")
for sid, raw in (("GS-2024-04", "V33-234-10"), ("GS-2025-06", "V33-234-13"), ("GS-2025-01", "V33-234-9")):
    upd(sid, raw, cause="R30", why="R30（2026-11-03）：写了一句原因就算写明原因，金标 N09 pass；系统按“原因仅重述结果”报，计误报")
