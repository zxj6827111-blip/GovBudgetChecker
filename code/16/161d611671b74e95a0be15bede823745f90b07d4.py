import json
import re
from pathlib import Path

SP = Path("/private/tmp/claude-501/-Volumes-workSSD-software-development-GovBudgetChecker/280d7499-41da-4e15-8a5e-f71ca60d4692/scratchpad")
REPO = Path("/Volumes/workSSD/software development/GovBudgetChecker")
R1 = REPO / "outputs/gold_baseline_20261102"
R2 = R1 / "rev2"

t = (R2 / "analysis/report_tables.md").read_text(encoding="utf-8")
secs = {}
for m in re.finditer(r"^## (T\d+b?) ([^\n]*)\n(.*?)(?=^## T\d+b? |\Z)", t, re.S | re.M):
    secs[m.group(1)] = (m.group(2), m.group(3).strip())

a = {r["id"]: r for r in json.loads((R1 / "analysis/final_rows.json").read_text(encoding="utf-8"))}
b = {r["id"]: r for r in json.loads((R2 / "analysis/final_rows.json").read_text(encoding="utf-8"))}
assert a.keys() == b.keys()
names = {"": "—", "R004": "R004（零预算完成率）", "R004_RULED": "R26 细化：0/0 不算缺陷", "R26_BORDERLINE": "R26 边界", "R26_ZERO_OVER_ZERO": "R26 细化：0/0 不算缺陷",
         "STD_CONFLICT": "标准冲突", "R30": "R30", "GOLD_MISS": "金标漏标", "R26": "R26", "GOLD_INCONSISTENT": "金标不一致"}
rows = []
for k in a:
    x, y = a[k], b[k]
    if (x["final"], sorted(x["links"]), x["cause"]) != (y["final"], sorted(y["links"]), y["cause"]):
        link = "；".join(l.split("|", 1)[1] for l in y["links"]) or "—"
        rows.append([x["doc"], f"{x['tier']} {x['raw_id']}", x["rule"], f"{x['final']}（{names.get(x['cause'], x['cause'])}）", f"{y['final']}（{names.get(y['cause'], y['cause'])}）", link])
head = ["样本", "层 条目", "规则", "v1", "r2", "r2 连到的金标单位"]
tbl = "| " + " | ".join(head) + " |\n|" + "|".join("---" for _ in head) + "|\n" + "\n".join("| " + " | ".join(r) + " |" for r in rows)

# O 层变化另列：O 条目状态也变了（文档级）
o_changed = [r for r in rows if r[1].startswith("O ")]

sec = (SP / "r2_section.md").read_text(encoding="utf-8")
sec = sec.replace("{{T0}}", secs["T0"][1]).replace("{{T0b}}", secs["T0b"][1]).replace("{{T1}}", secs["T1"][1]).replace("{{T3}}", secs["T3"][1])
sec = sec.replace("{{NCHANGED}}", str(len(rows))).replace("{{CHANGES}}", tbl)
sec = sec.replace("v1 §7 提出的 10 处（8 条 F 层 + 1 条 P 层 + 1 处不连边的重复诊断），金标现状：", "v1 §7 提出的 10 处位置（对应 11 行系统条目，因为 GS-2024-03 p32 同处有两条），金标现状：")
sec = sec.replace("我在 r2 的 36 条 `FP_金标` 复核行里没有发现新的金标漏标", "我在 r2 的 44 条 `FP_金标` 行（v1 时已逐条回 PDF）里没有发现新的金标漏标")

doc = (REPO / "docs/GOLD_BASELINE_20261102.md").read_text(encoding="utf-8")
assert "## 修订版 r2" not in doc, "已插入过 r2 段"
marker = "## 1. 结论速览"
i = doc.index(marker)
doc = doc[:i] + sec + "\n" + doc[i:]
# 前言补一句；v1 §7 补一句
doc = doc.replace("> **AI 关是主条件**：", "> **本文含两版**：最前面是 r2（2026-11-03，金标按 `gold/REVIEW_R9.md` 修订后重算），其后是 v1（2026-11-02，原金标，原文不动）。\n> **AI 关是主条件**：", 1)
doc = doc.replace("## 7. 金标修订候选与“系统比金标多发现”\n", "## 7. 金标修订候选与“系统比金标多发现”\n\n> **r2 更新**：这里的 10 处已处置——9 处进入金标（修订后的金标见 R2.0），1 处（GS-2022-06 “下降0%”）被 R26 细化裁定为不是缺陷。v1 的“若采纳修订候选”敏感性行因此作废，r2 头条表中已删除；以下为 v1 原文。\n", 1)
doc = doc.replace("## 1. 结论速览\n", "## 1. 结论速览（v1，原金标）\n", 1)
(REPO / "docs/GOLD_BASELINE_20261102.md").write_text(doc, encoding="utf-8")

# r2 完整表格
parts = []
for k in ["T2", "T4", "T5", "T7", "T15", "T16", "T8", "T9", "T10", "T11", "T12", "T13", "T14"]:
    parts.append(f"## {k} {secs[k][0]}\n\n{secs[k][1]}")
hdr = "# 金标基线 r2 完整表格（2026-11-03，修订金标）\n\n主文见 `docs/GOLD_BASELINE_20261102.md` 的“修订版 r2”。本文件由 `scripts/gold_baseline_tables.py`（`GOLD_BASELINE_REV=r2`）生成，与 `outputs/gold_baseline_20261102/rev2/analysis/report_tables.md` 同源。\n\n"
(REPO / "docs/GOLD_BASELINE_20261102_r2_tables.md").write_text(hdr + "\n\n".join(parts) + "\n", encoding="utf-8")
print(len(rows), "rows changed;", len(o_changed), "O rows;", len(doc))
