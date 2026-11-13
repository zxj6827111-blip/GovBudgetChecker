#!/usr/bin/env python3
"""Build the human-verification ledger for the real-material diagnostic.

Only claims listed with an original page/number here are treated as verified.
Everything else keeps its receipt status and is labelled receipt_only.
"""
from __future__ import annotations

import json
from pathlib import Path

RUN = Path(__file__).resolve().parents[1]

# (order, obligation_id) -> verification entry
EVIDENCE = {
    (3, "OBL-STRUCT-EMPTY-STATEMENT"): ("completed_with_legacy_noise", "p17/p18",
        "p17「说明:上海市文化和旅游局执法总队本年度没有政府性基金预算财政拨款收入和支出，故本表无数据」；p18 同类说明（国有资本经营）。",
        "空表说明存在；V33-122=pass。但 V33-109/V33-114 仍以“空表缺少说明”进入 manual_review，属旧规则与新规则冲突的噪音。"),
    (3, "OBL-INTERNAL-T1-BALANCE"): ("verified", "p6",
        "「本年支出合计 4,713.48；结余分配 0.00；年末结转和结余 176.62；总计 4,890.10」",
        "4,713.48+0.00+176.62=4,890.10；收入侧 4,763.42+126.68=4,890.10。"),
    (3, "OBL-INTERNAL-T2-TOTAL"): ("verified", "p7-8",
        "「合计 4763.42 / 财政拨款收入 4743.85 / 其他收入 19.57」",
        "4,743.85+19.57=4,763.42；207 类 3,689.59=3,670.02+19.57。"),
    (3, "OBL-INTERNAL-T3-TOTAL"): ("verified_with_rounding", "p9",
        "「合计 4713.48；基本支出 4191.22；项目支出 522.27」",
        "4,191.22+522.27=4,713.49，与印刷合计差 0.01 万元，在金额容差内。"),
    (3, "OBL-INTERNAL-BASIC-EXPENSE"): ("verified", "p15",
        "「人员经费合计 3,540.52；公用经费合计 650.70」",
        "3,540.52+650.70=4,191.22，等于 T3/T5 基本支出。"),
    (3, "OBL-INTERNAL-DETAIL-ROW"): ("verified_representative", "p9",
        "「行政运行 3222.92 / 3128.19 / 94.73」等明细行",
        "3,128.19+94.73=3,222.92；3639.65=3128.19+511.46。代表行复算成立。"),
    (3, "OBL-INTERNAL-TABLE-TOTAL"): ("verified_representative", "p7-10",
        "T2/T3 各行与合计（如 268.54=258.33+10.21；132.10=131.50+0.60）",
        "合计 4,763.42 / 4,713.48 与分项和差 0.01 万元（显示取整）。"),
    (3, "OBL-CROSS-T1-T2"): ("verified", "p6/p7",
        "T1 本年收入合计 4,763.42；T2 合计 4763.42",
        "两表相等。"),
    (3, "OBL-CROSS-T1-T3"): ("verified", "p6/p9",
        "T1 本年支出合计 4,713.48；T3 合计 4713.48",
        "两表相等。"),
    (3, "OBL-CROSS-T4-T5"): ("verified_with_rounding", "p12/p13",
        "T4 本年支出合计 4,713.48；T5 合计 4,713.49",
        "差 0.01 万元，在金额容差内。"),
    (3, "OBL-CROSS-T5-T6"): ("verified", "p13/p15",
        "T5 基本支出 4,191.22；T6 人员 3,540.52+公用 650.70",
        "3,540.52+650.70=4,191.22。"),
    (3, "OBL-CROSS-T2-T4"): ("verified", "p7/p11",
        "T2 财政拨款收入 4,743.85；T4 一般公共预算财政拨款 4,743.85",
        "两表相等。"),
    (3, "OBL-CROSS-FISCAL-TOTAL"): ("verified", "p12",
        "「本年收入合计 4,743.85；年初结转 0.01；本年支出合计 4,713.48；年末结转 30.38」",
        "4,743.85+0.01=4,743.86；4,713.48+30.38=4,743.86。"),
    (3, "OBL-CROSS-T1-IDENTITY"): ("verified", "p6",
        "「总计 4,890.10 / 总计 4,890.10」",
        "收入 4,763.42+126.68 = 支出 4,713.48+176.62 = 4,890.10。"),
    (3, "OBL-CROSS-SAN-GONG-ECON"): ("receipt_only", "p16",
        "T7 合计 17.27=因公出国 0.00+公车 17.27+接待 0.00",
        "三公表内部成立；与基本支出经济分类表的资金来源一致性未独立复算（回执 legacy pass）。"),
    (3, "OBL-TXT-T1"): ("verified", "p19",
        "「收入支出总计4,890.10 万元」",
        "等于 T1 总计 4,890.10。"),
    (3, "OBL-TXT-T2"): ("verified", "p19",
        "「本年收入合计4,763.42 万元，其中：财政拨款收入4,743.85 万元，占99.59%；其他收入19.57 万元，占0.41%」",
        "4,743.85/4,763.42=99.589%→99.59%；19.57/4,763.42=0.411%→0.41%。"),
    (3, "OBL-TXT-T3"): ("verified_with_rounding", "p19",
        "「本年支出合计4,713.48 万元，其中：基本支出4,191.22 万元，占88.92%；项目支出522.26 万元，占11.08%」",
        "4,191.22+522.26=4,713.48；表内项目支出为 522.27，差 0.01 万元。"),
    (3, "OBL-TXT-T4"): ("verified", "p19",
        "「财政拨款收入支出总计4,743.86 万元」",
        "等于 T4 4,743.85+年初结转 0.01。"),
    (3, "OBL-TXT-T5"): ("completed_with_defect", "p13/p20",
        "表 p13「机关事业单位基本养老保险缴费支出 165.80」；说明 p20「年初预算为165.80 万元，支出决算为141.12 万元」",
        "表内决算 165.80 与说明 141.12 相差 24.68 万元；高严重度 V33-222 成立。"),
    (3, "OBL-TXT-T6"): ("verified_representative", "p15/p19",
        "T6 基本支出 4,191.22；说明 p19 基本支出 4,191.22",
        "总额一致；人员/公用分项见 p15 3,540.52+650.70。"),
    (3, "OBL-TXT-T7"): ("verified", "p16/p22",
        "T7 17.27=0+17.27+0；说明 p22「支出决算为17.27 万元…公务用车17.27」",
        "表文三公合计与分项一致。"),
    (3, "OBL-NARRATIVE-AMOUNT"): ("verified_representative", "p19-22",
        "p19 4,890.10/4,763.42/4,713.48；p20 4,713.49；p22 17.27",
        "抽查的叙述金额均与对应表合计一致（取整差 0.01 已注明）。"),
    (3, "OBL-TREND-COMPLETION-RATE"): ("verified", "p20",
        "「完成年初预算的99.79%」",
        "4,713.49/4,723.60=99.785%→99.79%。"),
    (3, "OBL-SG-TOTAL"): ("verified", "p16",
        "T7 合计 17.27；因公出国 0.00；公车 17.27；接待 0.00",
        "0.00+17.27+0.00=17.27。"),
    (3, "OBL-SG-ITEMS"): ("verified", "p16",
        "T7 列出因公出国（境）费、公务用车购置及运行费、公务接待费三项",
        "三项齐备。"),
    (3, "OBL-SG-TABLE-TEXT"): ("verified_representative", "p16/p22",
        "表 17.27/0.00/17.27/0.00；说明同值",
        "抽查三公总额与两项零值一致。"),
    (1, "OBL-STRUCT-TERMINOLOGY"): ("receipt_only", "全文",
        "全文未出现「本部门」或「本单位」",
        "无口径混用可反驳；规则 pass 未被推翻，但属弱证据完成。"),
    (4, "OBL-TXT-T5"): ("verified", "p7/p15",
        "T3 合计 12518.13；说明 p15「一般公共预算财政拨款支出 12,518.13 万元」",
        "总额一致。"),
    (6, "OBL-TXT-T5"): ("completed_with_defect", "p12/p19",
        "表内款名「行政事业单位养老支出」；说明 p19「行政事业单位离退休（款）」",
        "V33-227 low 成立：说明款名与表不一致（离退休 vs 养老支出）。"),
    (6, "OBL-TXT-FUND-DETAIL"): ("receipt_only", "—",
        "回执 V33-248 not_applicable；无独立金额可复算",
        "未独立核验；保留回执级结论。"),
    (6, "OBL-NARRATIVE-AMOUNT"): ("verified", "p23",
        "「政府采购金额…8.50 万元，其中：货物2.50 万元、服务6 万元」",
        "2.50+6.00=8.50。"),
    (7, "OBL-STRUCT-TERMINOLOGY"): ("verified_text", "全文",
        "全文使用「本单位」1 次，无「本部门」",
        "单位材料口径一致。"),
    (7, "OBL-NARRATIVE-AMOUNT"): ("verified", "p17-18",
        "「本年支出合计192.77 万元，其中：基本支出191.18 万元，占99.18%；项目支出1.60 万元，占0.83%」",
        "191.18/192.77=99.18%；1.60/192.77=0.83%；191.18+1.60=192.78（0.01 取整）。"),
    (8, "OBL-NARRATIVE-AMOUNT"): ("verified", "p20",
        "「本年支出合计30,064.85 万元，其中：基本支出15,756.92 万元，占52.41%；项目支出13,378.00 万元，占44.50%；经营支出929.94 万元，占0.03%」",
        "15,756.92+13,378.00+929.94=30,064.86（0.01）；但经营支出占比 929.94/30,064.85=3.09%，说明 0.03% 为真实材料错误（CMM-020 已报）。"),
    (9, "OBL-TXT-FUND-DETAIL"): ("verified", "p21/T8",
        "说明 p21「政府性基金预算财政拨款收入 991.47 万元」；T8 本年收入 991.47",
        "表文一致。"),
    (10, "OBL-STRUCT-TERMINOLOGY"): ("verified_text", "全文",
        "全文使用「本单位」4 次，无「本部门」",
        "单位材料口径一致。"),
    (10, "OBL-NARRATIVE-AMOUNT"): ("verified", "p24",
        "「政府采购金额…401.4 万元，其中：货物5.56 万元、工程0 万元、服务395.84 万元」",
        "5.56+0+395.84=401.40。"),
    (10, "OBL-TREND-DIRECTION"): ("verified", "p22",
        "三公 65.23 比 2023 增加48.11，增长281.02%",
        "基期 65.23-48.11=17.12；48.11/17.12=281.02%，方向与幅度一致。"),
    (10, "OBL-TREND-COMPARATIVE-LOGIC"): ("verified", "p22",
        "公车减少2.59，下降23.40%；接待减少2.44，下降40.33%",
        "2.59/(8.48+2.59)=23.40%；2.44/(3.61+2.44)=40.33%。"),
    (10, "OBL-TREND-ZERO-BASE"): ("verified", "p22",
        "因公出国增加53.14（未写百分比）；2023 年未开支",
        "基期为 0 时未写有限百分比，符合规则。"),
    (10, "OBL-SG-YOY"): ("verified", "p22",
        "三公总额 65.23、增加48.11、增长281.02%",
        "三个数字可同时成立（基期 17.12）。"),
}

HIGH_ISSUES = [
    {"order": 3, "rule_id": "V33-222", "page": "13/20", "verdict": "true_positive",
     "original": "表 p13 机关事业单位基本养老保险缴费支出 165.80；说明 p20 支出决算 141.12。",
     "numbers": "165.80 vs 141.12；差 24.68 万元。", "note": "表文不一致成立。"},
    {"order": 5, "rule_id": "CMM-007", "page": "22", "verdict": "false_positive",
     "original": "「公务接待费支出决算减少 0.08 万元，下降 100%」；后文「公务接待费支出决算 0 万元」。",
     "numbers": "本期 0，减少 0.08 → 基期 0.08；0.08/0.08=100%，百分比有定义。",
     "note": "规则把“0 万元”（整数显示）的舍入包络当成零基数，吞掉了 0.08 的减少额。基期并非 0。"},
    {"order": 8, "rule_id": "CMM-007", "page": "26", "verdict": "true_positive",
     "original": "「因公出国（境）费支出决算增加 99.16 万元，增长 100%」；同段「支出决算99.16 万元」。",
     "numbers": "本期 99.16，增加 99.16 → 基期 0；零基数不能写有限百分比 100%。",
     "note": "三个数字不能同时成立；建议改为“上年无支出”。"},
    {"order": 10, "rule_id": "V33-110", "page": "19", "verdict": "true_positive",
     "original": "「年初预算为16.71 万元，支出决算为14.93 万元。决算数大于预算数的主要原因…」",
     "numbers": "14.93 < 16.71，方向应为“小于”。", "note": "方向词错误成立。"},
    {"order": 10, "rule_id": "V33-222", "page": "12/19", "verdict": "true_positive",
     "original": "表 p12 退役士兵能力提升 258.34；说明 p19 支出决算 583.34。",
     "numbers": "583.34 vs 258.34；差 325.00 万元。", "note": "表文不一致成立（且教育支出小计 258.34 与表一致）。"},
]

MEDIUM_SAMPLES = [
    {"order": 4, "rule_id": "V33-SG-COMPLETION", "page": "16", "verdict": "true_positive",
     "original": "p16「上海市农场管理局2023 年度无“三公”经费财政拨款支出。」；全文未出现车辆保有量。",
     "note": "无三公支出仍需披露车辆保有量；medium 成立。"},
    {"order": 6, "rule_id": "CMM-011", "page": "23", "verdict": "true_positive",
     "original": "「采购金额8.50 元」；同页前文为 8.50 万元。", "note": "漏“万”字成立。"},
    {"order": 8, "rule_id": "CMM-020", "page": "20", "verdict": "true_positive",
     "original": "基本52.41%+项目44.50%+经营0.03%=96.94%；929.94/30,064.85=3.09%。",
     "note": "占比合计与经营支出占比均不成立。"},
    {"order": 8, "rule_id": "CMM-017", "page": "27", "verdict": "true_positive",
     "original": "「上海市国有资产监督管理委员 202…」漏“会”。", "note": "单位名称漏字成立。"},
]


def main() -> int:
    results = json.loads((RUN / "painpoint_results.json").read_text())
    rows = []
    unmapped = []
    for row in results["a_layer"]["rows"]:
        if row["classification"] != "completed_current":
            continue
        key = (row["order"], row["obligation_id"])
        entry = EVIDENCE.get(key)
        if entry is None:
            unmapped.append(key)
            entry = ("receipt_only", "—", "未在人工核验表中映射", "仅回执级结论，未独立复算。")
        verdict, page, original, note = entry
        rows.append({
            "order": row["order"],
            "obligation_id": row["obligation_id"],
            "historical_reason": row["historical_reason"],
            "current_status": row["current_status"],
            "has_fail_receipt": row["has_fail_receipt"],
            "evidence_strength": row["evidence_strength"],
            "verdict": verdict,
            "page": page,
            "original_evidence": original,
            "verification_note": note,
            "receipt_status": row["receipt_status"],
            "evidence_dir": row["evidence_dir"],
        })
    ledger = {
        "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scope": "42 historical unresolved records whose current obligation is completed",
        "verdict_counts": {},
        "recoveries": rows,
        "unmapped": [{"order": o, "obligation_id": i} for o, i in unmapped],
        "high_issues": HIGH_ISSUES,
        "medium_samples": MEDIUM_SAMPLES,
        "coverage_note": "All 5 high issues were checked against originals. 4 of 31 medium issues were sampled; low/info were not individually verified. 15 of 42 recoveries rely on legacy pass receipts without exposed operands; those are marked receipt_only/verified_text/verified_representative where independent numbers were available.",
        "human_participation": {
            "manual_org_selection": "10/10 via org_selection.json (mirrors batch-upload UI choice; not an automatic recovery)",
            "manual_recheck_or_retry_clicks": 0,
            "manual_cell_corrections": 0,
            "reanalyze_for_reproduction": "order 2 only, diagnostic reproduction; not counted as automatic recovery",
            "automatic_recoveries_counted": "only checks that reached completed in the normal pipeline run",
        },
        "not_executed": {
            "ai_semantic_checks": "5 A-layer records + 9 B-layer AI obligations (AI disabled; 0 calls)",
            "ocr": "0/10 documents triggered OCR; no OCR claim",
        },
    }
    counts = {}
    for row in rows:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    ledger["verdict_counts"] = counts
    (RUN / "VERIFICATION_LEDGER.json").write_text(json.dumps(ledger, ensure_ascii=False, indent=2))

    lines = ["# 复核证据：历史 131 条未完成记录中当前已完成的 42 条", "",
             "口径：只有在本表给出原件页码/数字的条目才算独立核验；其余标注 receipt_only。", "",
             "人工参与：仅组织选择（org_selection.json，模拟批量上传界面）；未点击补核/重试，未做人工单元格修正；第 2 份 reanalyze 是缺陷复现，不计自动补救。", "",
             "未执行：AI 语义检查（A 层 5 条 + B 层 9 条，0 调用）；OCR 0/10 触发，不作 OCR 结论。", "",
             "| 序 | 义务 | 原状态 | 当前 | 结论 | 证据页码 | 原件数字/引文 | 复核说明 |",
             "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        lines.append("| {order} | {obligation_id} | {historical_reason} | {current_status} | {verdict} | {page} | {original_evidence} | {verification_note} |".format(**row))
    lines += ["", "## 高严重度问题逐条核验", "",
              "| 序 | 规则 | 页码 | 结论 | 原件 | 数字 | 说明 |", "|---|---|---|---|---|---|---|"]
    for item in HIGH_ISSUES:
        lines.append("| {order} | {rule_id} | {page} | {verdict} | {original} | {numbers} | {note} |".format(**item))
    lines += ["", "## Medium 抽查", "",
              "| 序 | 规则 | 页码 | 结论 | 原件 | 说明 |", "|---|---|---|---|---|---|"]
    for item in MEDIUM_SAMPLES:
        lines.append("| {order} | {rule_id} | {page} | {verdict} | {original} | {note} |".format(**item))
    (RUN / "VERIFIED_RECOVERIES.md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"verdict_counts": counts, "unmapped": ledger["unmapped"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
