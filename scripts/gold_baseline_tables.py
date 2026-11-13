#!/usr/bin/env python3
"""由 analysis/*.json 生成基线报告里的表格（避免手抄数字）。输出 outputs/gold_baseline_20261102/analysis/report_tables.md。"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import gold_baseline_match as gm  # noqa: E402

OUT = gm.OUT
A = gm.ANALYSIS


def pct(x):
    return "—" if x is None else f"{x * 100:.1f}%"


def frac(n, d):
    return f"{n}/{d}（{pct(n / d if d else None)}）"


def md(rows, header):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("\n", " ").replace("|", "／") for c in r) + " |")
    return "\n".join(out)


def main() -> int:
    if gm.REV != "r2":
        raise SystemExit("只能以 GOLD_BASELINE_REV=r2 运行（v1 文件不覆盖）")
    m = json.loads((A / "metrics.json").read_text(encoding="utf-8"))
    rows = json.loads((A / "final_rows.json").read_text(encoding="utf-8"))
    docs = json.loads((A / "gold_docs.json").read_text(encoding="utf-8"))
    units = json.loads((A / "gold_unit_status.json").read_text(encoding="utf-8"))
    cov = json.loads((A / "coverage_matrix.json").read_text(encoding="utf-8"))
    work = json.loads((A / "workload.json").read_text(encoding="utf-8"))
    F = [r for r in rows if r["tier"] == "F"]
    P = [r for r in rows if r["tier"] == "P"]
    parts = []


    # 0 v1 vs r2
    v1m = json.loads((gm.RUN_ROOT / "analysis" / "metrics.json").read_text(encoding="utf-8"))
    v1u = json.loads((gm.RUN_ROOT / "analysis" / "gold_unit_status.json").read_text(encoding="utf-8"))

    def f2(d):
        return frac(d["num"], d["den"])

    cmp_rows = [
        ["误报率（头条）", f2(v1m["FPR_headline"]), f2(m["FPR_headline"])],
        ["误报率（严格金标口径）", f2(v1m["FPR_strict_gold"]), f2(m["FPR_strict_gold"])],
        ["误报率（剔除“单位错连带”）", f2(v1m["FPR_excl_cascade"]), f2(m["FPR_excl_cascade"])],
        ["误报率（再剔除标准冲突/舍入/模板原写法，仅敏感性）", f2(v1m["FPR_excl_standard_conflicts"]), f2(m["FPR_excl_standard_conflicts"])],
        ["误报率（界面口径，含 O 层）", f2(v1m["FPR_with_O"]), f2(m["FPR_with_O"])],
        ["正式问题 TP / FP_金标 / 复核判真 / 复核判假", " / ".join(str(v1m["F_final"].get(k, 0)) for k in ("TP", "FP_GOLD", "ADJ_TRUE", "ADJ_FALSE")),
         " / ".join(str(m["F_final"].get(k, 0)) for k in ("TP", "FP_GOLD", "ADJ_TRUE", "ADJ_FALSE"))],
        ["金标单位总数（24 项检查条目 + 自由发现）", f"{v1m['gold_units']['CU']} + {v1m['gold_units']['FU_total']}", f"{m['gold_units']['CU']} + {m['gold_units']['FU_total']}"],
        ["漏报率（混合）", f2(v1m["MR_overall"]), f2(m["MR_overall"])],
        ["**漏报率·24 项检查条目**", f2(v1m["MR_CU"]), f2(m["MR_CU"])],
        ["**漏报率·穷尽性格式类自由发现**", f2(v1m["MR_FU_exhaustive"]), f2(m["MR_FU_exhaustive"])],
        ["静默漏报率（字面，混合）", f2(v1m["MR_silent_literal"]), f2(m["MR_silent_literal"])],
        ["　24 项检查条目静默", frac(v1m["MR_CU"]["silent_literal"], v1m["MR_CU"]["den"]), frac(m["MR_CU"]["silent_literal"], m["MR_CU"]["den"])],
        ["　穷尽性自由发现静默", frac(v1m["MR_FU_exhaustive"]["silent_literal"], v1m["MR_FU_exhaustive"]["den"]), frac(m["MR_FU_exhaustive"]["silent_literal"], m["MR_FU_exhaustive"]["den"])],
        ["尽力类自由发现命中（不计入漏报率）", f"{v1m['best_effort']['hit_F']}/{v1m['best_effort']['total']}", f"{m['best_effort']['hit_F']}/{m['best_effort']['total']}"],
        ["若把 spec 未点名的尽力类也计入分母的漏报率", f2(v1m["MR_sensitivity_incl_unlisted_best_effort"]), f2(m["MR_sensitivity_incl_unlisted_best_effort"])],
        ["出现点级召回（穷尽类，不含 R21）", frac(v1m["occurrence_recall"]["hit"], v1m["occurrence_recall"]["total"]), frac(m["occurrence_recall"]["hit"], m["occurrence_recall"]["total"])],
        ["待核实命中率（P 层，剔除 CMM-004）", frac(v1m["PendingHit"]["HIT"], v1m["PendingHit"]["in_scope_den"]), frac(m["PendingHit"]["HIT"], m["PendingHit"]["in_scope_den"])],
        ["待核实命中率（P 层，全部 73 条）", frac(v1m["PendingHit"]["HIT"], v1m["PendingHit"]["all_P"]), frac(m["PendingHit"]["HIT"], m["PendingHit"]["all_P"])],
        ["待核实命中率（P+O，全部）", f2(v1m["PendingHit_P_plus_O"]), f2(m["PendingHit_P_plus_O"])],
        ["待核实命中率（P+O，剔除无金标对应）", frac(v1m["PendingHit_P_plus_O"]["num"], v1m["PendingHit_P_plus_O"]["in_scope_den"]), frac(m["PendingHit_P_plus_O"]["num"], m["PendingHit_P_plus_O"]["in_scope_den"])],
        ["人工工作量（每份 F+O+P 均值 / 中位 / 合计）", f"{v1m['workload']['mean']} / {v1m['workload']['median']} / {v1m['workload']['total']}", f"{m['workload']['mean']} / {m['workload']['median']} / {m['workload']['total']}"],
        ["自动化覆盖", frac(v1m["coverage"]["with_verdict"], v1m["coverage"]["total"]), frac(m["coverage"]["with_verdict"], m["coverage"]["total"])],
        ["24 项里“自认通过但金标有缺陷”的 (份,检查) 对", f"{v1m['false_pass_checks']['count']}/{v1m['false_pass_checks']['of_gold_defect_checks']}", f"{m['false_pass_checks']['count']}/{m['false_pass_checks']['of_gold_defect_checks']}"],
    ]
    parts.append("## T0 v1 与 r2 并排\n" + md(cmp_rows, ["指标", "v1（原金标）", "r2（修订金标）"]))

    def ck_counts(us):
        out = defaultdict(Counter)
        for u in us:
            if u["kind"] == "CU":
                out[u["key"]]["den"] += 1
                out[u["key"]][u["state"]] += 1
        return out

    c1, c2 = ck_counts(v1u), ck_counts(units)
    rows_c = []
    for chk in sorted(set(c1) | set(c2)):
        a, b = c1.get(chk, Counter()), c2.get(chk, Counter())
        flag = "◀" if (a["den"], a["F"], a["PEND"], a["SILENT"]) != (b["den"], b["F"], b["PEND"], b["SILENT"]) else ""
        rows_c.append([chk, f"{a['den']} / {a['F']} / {a['PEND']} / {a['SILENT']}", f"{b['den']} / {b['F']} / {b['PEND']} / {b['SILENT']}", flag])
    d1 = sum(1 for d in json.loads((gm.RUN_ROOT / "analysis" / "gold_docs.json").read_text(encoding="utf-8")).values() if d["verdicts"]["GS-N05"] == "defect")
    d2 = sum(1 for d in docs.values() if d["verdicts"]["GS-N05"] == "defect")
    rows_c.append(["GS-N05 有 defect 的份数", d1, d2, "◀"])
    parts.append("## T0b 24 项检查按检查：金标 defect 条目 / 正式问题命中 / 仅待核实或未能核对 / 静默（v1 → r2；◀=有变化）\n" + md(rows_c, ["检查", "v1", "r2", ""]))

    # 1 headline
    h = m["FPR_headline"]
    parts.append("## T1 头条\n" + md([
        ["误报率（头条：FP_金标 + 复核判假 ÷ 有结论的正式问题）", frac(h["num"], h["den"])],
        ["误报率（严格金标口径：只算有金标对应的正式问题）", frac(m["FPR_strict_gold"]["num"], m["FPR_strict_gold"]["den"])],
        ["误报率（剔除 %d 条“单位错连带”重复条目）" % m["FPR_excl_cascade"]["cascade_items"], frac(m["FPR_excl_cascade"]["num"], m["FPR_excl_cascade"]["den"])],
        ["误报率（剔除标准冲突/连带/舍入/模板原写法等 %d 条）" % sum(m["FPR_excl_standard_conflicts"]["excluded"].values()), frac(m["FPR_excl_standard_conflicts"]["num"], m["FPR_excl_standard_conflicts"]["den"])],
        ["误报率（界面口径：把未能自动核对 O 层也算进必须处理）", frac(m["FPR_with_O"]["num"], m["FPR_with_O"]["den"])],
        ["漏报率（混合：24 项检查条目 + 穷尽性自由发现类）", frac(m["MR_overall"]["num"], m["MR_overall"]["den"])],
        ["**漏报率·24 项检查条目**", frac(m["MR_CU"]["num"], m["MR_CU"]["den"])],
        ["**漏报率·穷尽性格式类自由发现（R9/R21/R22/R24/R28）**", frac(m["MR_FU_exhaustive"]["num"], m["MR_FU_exhaustive"]["den"])],
        ["静默漏报率（字面：既无正式问题也无待核实/未能核对）", frac(m["MR_silent_literal"]["num"], m["MR_silent_literal"]["den"])],
        ["　其中仅出现在“参考”提示里", m["MR_silent_literal"]["of_which_REF_only"]],
        ["待核实命中率（P 层，剔除 CMM-004 金标范围外）", frac(m["PendingHit"]["HIT"], m["PendingHit"]["in_scope_den"])],
        ["待核实命中率（P 层，全部 73 条）", frac(m["PendingHit"]["HIT"], m["PendingHit"]["all_P"])],
        ["待核实命中率（P+O，spec 口径“待核实/未能自动核对”，全部）", frac(m["PendingHit_P_plus_O"]["num"], m["PendingHit_P_plus_O"]["den"])],
        ["待核实命中率（P+O，剔除无金标对应项）", frac(m["PendingHit_P_plus_O"]["num"], m["PendingHit_P_plus_O"]["in_scope_den"])],
        ["人工工作量（每份 F+O+P）", f"均值 {m['workload']['mean']}，中位 {m['workload']['median']}，最小 {m['workload']['min']}，最大 {m['workload']['max']}，合计 {m['workload']['total']}"],
        ["自动化覆盖（24 项 × 33 份，系统账本给出结论）", frac(m["coverage"]["with_verdict"], m["coverage"]["total"])],
    ], ["指标", "数值"]))

    # 2 FP by rule
    fr = defaultdict(Counter)
    for r in F:
        fr[r["rule"]][r["final"]] += 1
    rr = []
    for rule, c in sorted(fr.items(), key=lambda kv: -(kv[1]["FP_GOLD"] + kv[1]["ADJ_FALSE"])):
        n = sum(c.values())
        fp = c["FP_GOLD"] + c["ADJ_FALSE"]
        rr.append([rule, n, c["TP"], c["ADJ_TRUE"], c["FP_GOLD"], c["ADJ_FALSE"], pct(fp / n)])
    parts.append("## T2 正式问题按规则\n" + md(rr, ["规则", "条数", "TP", "复核为真(无金标)", "FP_金标", "复核判假(无金标)", "误报占比"]))

    # 3 FP by cause
    cc = Counter()
    ex = defaultdict(list)
    for r in F:
        if r["final"] in {"FP_GOLD", "ADJ_FALSE"}:
            cc[r["cause"] or "其他"] += 1
            ex[r["cause"] or "其他"].append(f"{r['doc'][3:]} {r['raw_id']}")
    names = {"D2": "D2：部门决算正文无“第二部分”（照当年模板）", "R3": "R3：目录/正文标题只差“情况/部门”", "CASCADE": "单位错（“元”）折算出的重复/连带条目",
             "R004": "年初预算为0写“完成预算的0.00%”（AGENTS R004 vs 金标 pass）", "STD_CONFLICT": "“原因仅重述结果”标准差异", "ROUNDING": "舍入包络内的差额（R1）",
             "R16": "同比增长率落在 R16 舍入区间内", "TEMPLATE_NATIVE": "模板原写法（R8）", "BIND_ERR": "系统把相邻数字/占比绑错对象",
             "PARSE_ERR": "系统解析失败（换页/漏识别写法）", "PYMUPDF_DROP": "PyMuPDF 丢字页（R14）", "NOTICE_ONLY": "“无法完整加总”提示被当作正式问题",
             "NAME_VARIANT": "漏列判断被名称异写打乱", "WRONG_SCOPE": "部门/单位口径判反（按组织层级而非封面）", "GOLD_MISS": "金标漏标（系统说对了）",
             "GOLD_INCONSISTENT": "金标前后不一致（系统说对了）", "R26": "R26 零基数同比（系统说对了，金标漏标）", "R004_RULED": "预算0、决算0写“完成预算的0.00%”（R26 细化：0/0 不算缺陷）", "R30": "R30：泛泛原因也算写明原因", "R26_ZERO_OVER_ZERO": "上年0、本年0写“下降0%”（R26 细化：0/0 不算缺陷）", "REASON_PRESENT": "原因其实写了，系统说“未说明原因”", "R26_BORDERLINE": "零基数“下降0%”（R26 字面边界）", "其他": "其他"}
    parts.append("## T3 误报原因\n" + md([[names.get(k, k), v, "；".join(ex[k][:6]) + ("…" if len(ex[k]) > 6 else "")] for k, v in cc.most_common()], ["原因", "条数", "例"]))

    # 4 FP list
    fl = []
    for r in F:
        if r["final"] in {"FP_GOLD", "ADJ_FALSE"}:
            fl.append([r["doc"], r["raw_id"], r["rule"], " ".join(map(str, r["pages"])), r["final"], names.get(r["cause"], r["cause"] or ""), ("是" if r["amend"] else ""),
                       r["message"][:110], r["evidence"][:150], r["why"][:150]])
    parts.append("## T4 全部误报（FP_金标 + 复核判假）\n" + md(fl, ["样本", "条目", "规则", "页", "类别", "原因", "金标可能漏标", "系统所报", "PDF 原文", "判定理由"]))

    # 5 ADJ_TRUE
    at = [[r["doc"], r["raw_id"], r["rule"], " ".join(map(str, r["pages"])), r["message"][:100], r["evidence"], r["why"][:120]] for r in F if r["final"] == "ADJ_TRUE"]
    parts.append("## T5 无金标对应、复核为真\n" + md(at, ["样本", "条目", "规则", "页", "系统所报", "PDF 原文", "理由"]))

    # 6 amendment candidates
    am = [[r["doc"], r["tier"], r["raw_id"], " ".join(map(str, r["pages"])), r["evidence"], r["why"][:160]] for r in rows if r.get("amend") and r["tier"] in {"F", "P"}]
    parts.append("## T6 金标修订候选（不改金标）\n" + md(am, ["样本", "层", "条目", "页", "PDF 原文", "理由"]))

    # 7 misses CU
    cu_miss = [[u["doc"], u["key"], u["unit_id"].split("|")[3], u["label"] or "", " ".join(map(str, u["pages"])), u["quote"][:120], u["note"][:180], u["state"], u.get("diag", ""),
                ",".join(x.split("|", 2)[2] for x in u["hit_P"][:3] + u["hit_O"][:3])]
               for u in units if u["kind"] == "CU" and u["state"] != "F"]
    parts.append("## T7 24 项检查里的漏报（金标 defect 未被正式问题命中）\n" + md(cu_miss, ["样本", "检查", "条目#", "金标条目", "金标页", "金标原文", "PDF 所见（金标 note）", "状态", "诊断", "待核实/未能核对命中"]))
    fu_miss = [[u["doc"], u["cls"], u["unit_id"].split("|")[2], " ".join(map(str, u["pages"][:4])), u["quote"][:90], u["n_occ"], (u["note"] or "")[:70], u["state"], u.get("diag", "")]
               for u in units if u["kind"] == "FU" and u["exhaustive"] and u["state"] != "F"]
    parts.append("## T16 穷尽性自由发现里的漏报（金标原文取前 3 处）\n" + md(fu_miss, ["样本", "真值类", "编号", "页", "金标原文", "出现点数", "应为", "状态", "诊断"]))

    # 8 FU by class
    fc = defaultdict(Counter)
    for u in units:
        if u["kind"] == "FU":
            fc[u["cls"]][u["state"]] += 1
    exh = gm.EXH_FREE
    rows_fc = []
    for cls, c in sorted(fc.items(), key=lambda kv: (kv[0] not in exh, kv[0])):
        n = sum(c.values())
        rows_fc.append([cls, "是" if cls in exh else ("否（R25 点名）" if cls in gm.BEST_LISTED else "否（未点名，保守）"), n, c["F"], c["PEND"], c["REF"], c["SILENT"]])
    parts.append("## T8 自由发现按真值类\n" + md(rows_fc, ["真值类", "计入漏报率分母", "条数", "正式问题命中", "仅待核实/未能核对", "仅参考提示", "静默"]))

    # 9 per check
    ck = defaultdict(lambda: Counter())
    for u in units:
        if u["kind"] == "CU":
            ck[u["key"]]["den"] += 1
            ck[u["key"]][u["state"]] += 1
    covc = defaultdict(Counter)
    for c in cov:
        covc[c["check"]]["v" if c["system"] != "none" else "n"] += 1
        covc[c["check"]]["gold_" + c["gold"]] += 1
    prim = defaultdict(Counter)
    for r in F:
        key = None
        if r["final"] == "TP" and r["links"]:
            key = r["links"][0].split("|")[2] if "|C|" in r["links"][0] else "free"
        else:
            for t, _ in gm.COMPAT[r["ct"]]:
                if t.startswith("GS-"):
                    key = t
                    break
            key = key or ("free" if any(t.startswith("free:") for t, _ in gm.COMPAT[r["ct"]]) else "NOGOLD")
        prim[key]["TP" if r["final"] == "TP" else "ADJ_TRUE" if r["final"] == "ADJ_TRUE" else "FP"] += 1
    all_checks = sorted(covc)
    rows_ck = []
    for c in all_checks:
        k = ck.get(c, Counter())
        rows_ck.append([c, covc[c]["gold_defect"], k["den"], k["F"], k["PEND"], k["REF"], k["SILENT"], prim[c]["TP"], prim[c]["FP"], f"{covc[c]['v']}/33"])
    rows_ck.append(["自由发现(穷尽类)", "—", m["MR_FU_exhaustive"]["den"], sum(1 for u in units if u["kind"] == "FU" and u["exhaustive"] and u["state"] == "F"),
                    sum(1 for u in units if u["kind"] == "FU" and u["exhaustive"] and u["state"] == "PEND"), sum(1 for u in units if u["kind"] == "FU" and u["exhaustive"] and u["state"] == "REF"),
                    sum(1 for u in units if u["kind"] == "FU" and u["exhaustive"] and u["state"] == "SILENT"), prim["free"]["TP"], prim["free"]["FP"], "—"])
    rows_ck.append(["无金标对应(CT 无目标)", "—", "—", "—", "—", "—", "—", prim["NOGOLD"]["TP"], prim["NOGOLD"]["FP"], "—"])
    parts.append("## T9 按检查（金标 defect 条目 / 命中 / 误报；覆盖=系统有结论的份数）\n" + md(rows_ck, ["检查", "金标 defect 的份数", "金标 defect 条目", "正式问题命中", "仅待核实/未能核对", "仅参考", "静默", "正式问题 TP(主检查)", "正式问题误报(主检查)", "自动化覆盖"]))

    # 10 per year/scope
    yr = defaultdict(lambda: Counter())
    for r in F:
        d = docs[r["doc"]]
        for key in (f"{d['year']}", f"{d['scope']}", f"{d['year']}|{d['scope']}", "全部"):
            yr[key][r["final"]] += 1
            yr[key]["F"] += 1
    um = defaultdict(Counter)
    for u in units:
        if u["exhaustive"]:
            d = docs[u["doc"]]
            for key in (f"{d['year']}", f"{d['scope']}", f"{d['year']}|{d['scope']}", "全部"):
                um[key]["den"] += 1
                um[key]["miss"] += u["state"] != "F"
                um[key]["silent"] += u["state"] in {"SILENT", "REF"}
    wk = defaultdict(list)
    for w in work:
        for key in (f"{w['year']}", f"{w['scope']}", f"{w['year']}|{w['scope']}", "全部"):
            wk[key].append(w["workload"])
    nd = Counter()
    for d in docs.values():
        for key in (f"{d['year']}", f"{d['scope']}", f"{d['year']}|{d['scope']}", "全部"):
            nd[key] += 1
    keys = ["2022", "2023", "2024", "2025", "department", "unit"] + [f"{y}|{s}" for y in (2022, 2023, 2024, 2025) for s in ("department", "unit")] + ["全部"]
    rows_y = []
    for k in keys:
        c = yr[k]
        n = c["F"]
        den = c["TP"] + c["FP_GOLD"] + c["ADJ_TRUE"] + c["ADJ_FALSE"]
        fp = c["FP_GOLD"] + c["ADJ_FALSE"]
        rows_y.append([k.replace("department", "部门").replace("unit", "单位"), nd[k], n, frac(fp, den) if den else "—", f"{um[k]['miss']}/{um[k]['den']}（{pct(um[k]['miss'] / um[k]['den'] if um[k]['den'] else None)}）",
                       um[k]["silent"], f"{sum(wk[k]) / len(wk[k]):.1f}" if wk[k] else "—"])
    parts.append("## T10 按年份 / 口径（金标 material_scope）\n" + md(rows_y, ["分组", "份数", "正式问题", "误报率", "漏报（穷尽类）", "静默", "人均工作量"]))

    # 11 P and O
    pr = defaultdict(Counter)
    for r in P:
        pr[r["rule"]][r["final"]] += 1
    parts.append("## T11 待核实（P 层）按规则\n" + md([[k, sum(v.values()), v["HIT"], v["TRUE_NOT_IN_GOLD"], v["NOISE"], v["OUT_OF_SCOPE"]] for k, v in sorted(pr.items(), key=lambda kv: -sum(kv[1].values()))],
                                                          ["规则", "条数", "命中金标 defect", "真但金标没有", "噪声", "金标范围外"]))
    orr = defaultdict(Counter)
    for r in rows:
        if r["tier"] == "O":
            orr[r["rule"]][r["final"]] += 1
    parts.append("## T12 未能自动核对（O 层）按义务\n" + md([[k, sum(v.values()), v["O_HIT"], v["O_NOISE"], v["NOGOLD"]] for k, v in sorted(orr.items(), key=lambda kv: -sum(kv[1].values()))],
                                                              ["义务", "条数", "所属检查在金标有 defect", "无 defect", "无金标对应"]))

    # 13 workload
    parts.append("## T13 各份工作量\n" + md([[w["doc"], w["year"], "部门" if w["scope"] == "department" else "单位", w["F"], w["O"], w["P"], w["workload"], w["R_tip"], w["R_notice"]] for w in work],
                                          ["样本", "年份", "口径", "F 正式问题", "O 未能核对", "P 待核实", "F+O+P", "R 提示", "R 复核事项"]))

    # 14 unresolved obligations (coverage gaps)
    parts.append("## T14 自动化覆盖缺口：未完成的义务（按出现的 文档×检查 对数）\n" + md(sorted(([k, v] for k, v in m["coverage_by_unresolved_obligation"].items()), key=lambda x: -x[1]), ["义务", "对数"]))

    # 15 silent list
    sl = [[u["doc"], u["key"], u["unit_id"].split("|", 2)[2], (u["label"] or "")[:40], " ".join(map(str, u["pages"][:4])), u.get("diag", "")] for u in units if u["state"] == "SILENT" and u["kind"] == "CU"]
    parts.append("## T15 静默漏报（24 项检查条目）\n" + md(sl, ["样本", "检查", "条目", "金标条目", "页", "诊断"]))
    (A / "report_tables.md").write_text("\n\n".join(parts) + "\n", encoding="utf-8")
    print("written", A / "report_tables.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
