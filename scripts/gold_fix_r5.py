#!/usr/bin/env python3
"""第 5 轮返工：R14 重判 GS-2025-03 第28页、R8 删“支出收入”、R19 删括号/其它、R20 同步 note。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
sys.path.insert(0, str(ROOT / "scripts"))
from gold_build import annotation_frozen  # noqa: E402


def V(n, p, q, u="万元", src=None):
    d = {"name": n, "page": p, "quote": q, "unit": u}
    if src:
        d["source"] = src
    return d


def put(ann: dict, cid: str, verdict: str, items: list[dict], reason: str = "") -> None:
    for c in ann["checks"]:
        if c["check_id"] == cid:
            c["verdict"] = verdict
            c["items"] = items
            c["reason"] = reason
            return
    raise KeyError(cid)


def note_replace(ann: dict, cid: str, label: str, old: str, new: str) -> None:
    for c in ann["checks"]:
        if c["check_id"] != cid:
            continue
        for it in c.get("items", []):
            if it.get("label") == label:
                if old not in it.get("note", ""):
                    print(f"  !! note 未匹配：{cid} / {label} / {old[:30]}")
                else:
                    it["note"] = it["note"].replace(old, new)
                return
    print(f"  !! 未找到：{cid} / {label}")


def drop_free(ann: dict, needle: str) -> None:
    before = len(ann["free_findings"])
    ann["free_findings"] = [f for f in ann["free_findings"] if needle not in f.get("quote", "")]
    if len(ann["free_findings"]) == before:
        print(f"  !! 未删除：{needle}")


def renumber(ann: dict) -> None:
    ann["free_findings"].sort(key=lambda f: (f["page"], f.get("category", "")))
    for i, f in enumerate(ann["free_findings"], 1):
        f["id"] = f"F{i:02d}"


def fix_2025_03(ann: dict) -> None:
    put(ann, "GS-S02", "pass", [
        {"label": "九个说明章节与第X部分", "verdict": "pass",
         "values": [
             V("第一部分", 4, "第一部分", ""),
             V("第二部分页首", 8, "上海市财政局2025 年度部门决算表", ""),
             V("第三部分", 21, "第三部分", ""),
             V("第七节", 27, "七、财政拨款“三公”经费支出决算情况说明", ""),
             V("第八节", 28, "八、政府性基金预算财政拨款收入支出决算情况说明", "", "pdfplumber"),
             V("第九节", 28, "九、国有资本经营预算财政拨款收入支出决算情况说明", "", "pdfplumber"),
             V("第四部分", 31, "第四部分", ""),
         ],
         "formula": "",
         "note": "四个部分标题齐备；九个说明章节齐备（一至五21页，六26页，七27页起、第28页续，八、九在28页）。第28页 PyMuPDF 只读出页尾第九节，pdfplumber 与渲染图显示第七节后半和第八节都在（R14，source=pdfplumber）。"}], "")

    put(ann, "GS-S03", "pass", [
        {"label": "目录与正文标题", "verdict": "pass",
         "values": [
             V("目录第八项", 3, "八、政府性基金预算财政拨款收入支出决算情况说明", ""),
             V("正文第八项", 28, "八、政府性基金预算财政拨款收入支出决算情况说明", "", "pdfplumber"),
             V("目录第十一项", 3, "十一、其他重要事项说明", ""),
             V("正文第十一项", 29, "十一、其他重要事项的情况说明", ""),
         ],
         "formula": "",
         "note": "九表九章名称与目录一致；目录“十一、其他重要事项说明”与正文“十一、其他重要事项的情况说明”属模板措辞差（R3）。第八节第28页 quote 按 R14 取自 pdfplumber。"}], "")

    put(ann, "GS-N07", "pass", [
        {"label": "金额与表一致", "verdict": "pass",
         "values": [
             V("年初预算", 27, "“三公”经费财政拨款支出年初预算为370.02 万元"),
             V("支出决算", 27, "支出决算为192.01 万元"),
             V("完成率", 27, "完成预算的51.89%", "%"),
             V("出国决算", 27, "因公出国（境）费决算为146.55 万元"),
             V("车决算", 27, "公务用车购置及运行维护费支出决算为30.00 万元"),
             V("接待决算", 27, "公务接待费支出决算为15.45 万元"),
         ],
         "formula": "192.01 / 370.02 = 51.89%",
         "note": "三公表合计预算 370.02、决算 192.01 与正文一致；出国 146.55/293.70=49.90%、车 30.00/41.80=71.77%（正文 71.79%）、接待 15.45/34.52=44.76%。"},
        {"label": "数量要素", "verdict": "pass",
         "values": [
             V("出国团组人次", 28, "团组 2 个、累计 25 人次", "", "pdfplumber"),
             V("购置", 28, "公务用车购置支出为 0.00 万元", "万元", "pdfplumber"),
             V("保有量", 28, "保有量为 17 辆", "", "pdfplumber"),
             V("车占比", 28, "占 15.63%", "%", "pdfplumber"),
             V("接待批次人次", 28, "全年公务接待 58 批次、415 人次", "", "pdfplumber"),
             V("外宾批次人次", 28, "接待外宾 8 批次、155 人次", "", "pdfplumber"),
         ],
         "formula": "",
         "note": "第28页补读第七节后半（R14，source=pdfplumber）：出国 2 团组、25 人次；购置 0.00 万元；保有量 17 辆；接待 58 批次、415 人次（外宾 8 批次、155 人次）。车辆完成率正文 71.79%，按 30.00/41.80 复算 71.77%，差 0.02 个百分点；占比正文 15.63%，按 30.00/192.01 复算 15.62%，差 0.01 个百分点，均在 0.1 容差内（R16）。"}], "")

    put(ann, "GS-N08", "pass", [
        {"label": "政府性基金说明", "verdict": "pass",
         "values": [V("政府性基金", 28, "上海市财政局 2025 年度无政府性基金预算财政拨款收入和支出", "", "pdfplumber")],
         "formula": "",
         "note": "第28页第八节写明无政府性基金预算财政拨款收入和支出，与第19页空表说明一致。"},
        {"label": "国资经营说明", "verdict": "pass",
         "values": [V("国资经营", 28, "上海市财政局 2025 年度无国有资本经营预算财政拨款收入和支出", "", "pdfplumber")],
         "formula": "",
         "note": "第28页第九节写明无国有资本经营预算财政拨款收入和支出，与第20页空表说明一致。"}], "")

    note_replace(ann, "GS-N09", "增减变化写明原因",
                 "三公三项原因在第27页中断前已列明", "三公三项原因在第27页至第28页已列明")


def fix_stale_notes(ann: dict, sid: str) -> None:
    if sid == "GS-2022-01":
        note_replace(ann, "GS-W01", "金额单位",
                     "第23页“0.73万”漏“元”字，按错别字归入自由发现，不影响口径。", "")
    if sid == "GS-2023-01":
        note_replace(ann, "GS-W01", "金额单位",
                     "第38页“45.85 万”漏“元”字归入自由发现。", "")
        note_replace(ann, "GS-S03", "目录与正文标题",
                     "第二部分标题缺失已计入 GS-S02，此处不重复计。",
                     "第二部分按 2023 年部门模板不写（目录中有；D2=A）。")
    if sid == "GS-2024-01":
        note_replace(ann, "GS-S03", "目录与正文标题",
                     "第二部分标题缺失已计入 GS-S02。",
                     "第二部分按 2024 年部门模板不写（目录中有；D2=A）。")
    if sid == "GS-2025-01":
        note_replace(ann, "GS-W01", "金额单位",
                     "第22页“0.14 万”漏“元”字归入自由发现。", "")
        note_replace(ann, "GS-S04", "单位名称与年度一致",
                     "措辞差异另列自由发现。", "")
        note_replace(ann, "GS-N01", "收入支出总计",
                     "已列自由发现 F01", "已列自由发现 F02")
    if sid == "GS-2025-02":
        note_replace(ann, "GS-N06", "基本支出 = 人员经费 + 公用经费",
                     "（该项与款之和不符，见 GS-T08）",
                     "（该项与款之和已在 T08 复算并记 pass）")
    if sid == "GS-2024-02":
        note_replace(ann, "GS-W01", "金额单位",
                     "“自然资源海洋气象等支出623.91”漏“万元”按漏字计入自由发现。",
                     "结构情况漏“万元”已计入本项第 2 子项。")


def fix(sid: str) -> None:
    if annotation_frozen(sid):
        print(f"{sid}: 已冻结，跳过 fix")
        return
    path = GOLD / sid / "annotation.json"
    ann = json.loads(path.read_text(encoding="utf-8"))

    if sid == "GS-2025-03":
        fix_2025_03(ann)
    if sid == "GS-2022-03":
        drop_free(ann, "支出收入388.96")
        note_replace(ann, "GS-N08", "政府性基金与国资经营",
                     "正文“支出收入388.96”“支出预算为388.96”两处措辞错误另列自由发现。",
                     "正文“支出预算为388.96”措辞错误另列自由发现（“支出收入”系模板原文，R8 不报）。")
    if sid == "GS-2023-05":
        drop_free(ann, "支出收入19,263.96")
        note_replace(ann, "GS-N08", "政府性基金与国资经营",
                     "正文“支出收入19,263.96”措辞错误另列自由发现。",
                     "正文“支出收入”系模板原文（R8），不报。")
    if sid == "GS-2023-01":
        drop_free(ann, "支出收入")
    if sid == "GS-2023-03":
        drop_free(ann, "(上海市知识产权发展研究中心)")
        note_replace(ann, "GS-N07", "金额与表一致",
                     "同比增幅按表列增减额复算为 540.99%、106.31%、508.20%，与正文 541.09%、106.37%、508.09% 差 0.06–0.11 个百分点，系上年度数取整。",
                     "按 R16，以本年数、增减额各 ±0.005 倒推上年数：总额 540.4%–541.6%、车 106.1%–106.5%、接待 503.7%–512.8%，正文 541.09%/106.37%/508.09% 均落在区间内，记 pass（舍入差）。")
    if sid == "GS-2024-03":
        drop_free(ann, "(上海市智慧城市建设促进中心)")
    if sid == "GS-2022-05":
        drop_free(ann, "其它事项")
    if sid == "GS-2025-05":
        for c in ann["checks"]:
            if c["check_id"] != "GS-N05":
                continue
            for it in c["items"]:
                if it["label"].startswith("逐项"):
                    it["formula"] = ("192.28 + 854.05 + 427.02 + 3.52 + 12,317.19 + 427.51 + 650.72"
                                     " = 14,872.29")

    fix_stale_notes(ann, sid)
    renumber(ann)
    path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sid}: 已返工，free={len(ann['free_findings'])}")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        fix(s)
