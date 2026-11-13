#!/usr/bin/env python3
"""第 4 轮返工：第三节脚本修正后的 12 项返工 + D2=A。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
sys.path.insert(0, str(ROOT / "scripts"))
from gold_build import scan_format  # noqa: E402

V = lambda n, p, q, u="万元": {"name": n, "page": p, "quote": q, "unit": u}  # noqa: E731

D2_PASS = {
    "GS-2022-01": ("GS-S01", "2022 年官方模板第七张表即“一般公共预算财政拨款‘三公’经费支出决算表”（R8 按年度模板；D2=A），照模板写不报。"),
    "GS-2022-02": ("GS-S01", "2022 年官方模板第七张表即“一般公共预算财政拨款‘三公’经费支出决算表”（R8 按年度模板；D2=A），照模板写不报。"),
    "GS-2022-04": ("GS-S01", "2022 年官方模板第七张表即“一般公共预算财政拨款‘三公’经费支出决算表”（R8 按年度模板；D2=A），照模板写不报。"),
    "GS-2023-01": ("GS-S02", "2023 年部门决算官方模板正文表格部分标题本身不写“第二部分”（目录中有；R8 按年度模板；D2=A），照模板写不报。"),
    "GS-2024-01": ("GS-S02", "2024 年部门决算官方模板正文表格部分标题本身不写“第二部分”（目录中有；R8 按年度模板；D2=A），照模板写不报。"),
    "GS-2023-02": ("GS-S02", "2023 年部门决算官方模板正文表格部分标题本身不写“第二部分”（目录中有；R8 按年度模板；D2=A），照模板写不报。"),
    "GS-2024-02": ("GS-S02", "2024 年部门决算官方模板正文表格部分标题本身不写“第二部分”（目录中有；R8 按年度模板；D2=A），照模板写不报。"),
    "GS-2025-02": ("GS-S02", "2025 年沿用 2024 年部门决算模板，正文表格部分标题本身不写“第二部分”（目录中有；R8 按年度模板；D2=A），照模板写不报。"),
}

FORMAT_SPEC = [
    ("千分位", "格式", "金额未按半角逗号分千分位（或用了全角逗号）"),
    ("小数位", "格式", "金额/百分比小数位不是两位"),
    ("半角冒号", "标点", "中文语境使用半角冒号"),
    ("标点连用", "标点", "标点符号连用"),
    ("双小数点", "标点", "数字出现双小数点"),
]


def by_id(ann: dict) -> dict[str, dict]:
    return {c["check_id"]: c for c in ann["checks"]}


def set_verdict(c: dict, verdict: str, reason: str = "") -> None:
    c["verdict"] = verdict
    c["reason"] = reason
    for it in c.get("items", []):
        it["verdict"] = verdict


def fix_t08(ann: dict) -> None:
    for c in ann["checks"]:
        if c["check_id"] != "GS-T08":
            continue
        for it in c["items"]:
            if not it["label"].startswith("310 "):
                continue
            if any(v["quote"] == "2.98" and v["page"] == 15 for v in it["values"]):
                continue
            pos = next((i for i, v in enumerate(it["values"]) if v["name"].startswith("31099 ")),
                       len(it["values"]))
            it["values"].insert(pos, V("31022 无形资产购置（款）", 15, "2.98"))
            it["formula"] = ("0.00 + 93.09 + 0.00 + 0.00 + 0.00 + 0.00 + 0.00 + 0.00 + 2.98"
                             " = 96.07")
            it["verdict"] = "pass"
            it["note"] = ("类合计 96.07，款之和复算 96.07，差 0.00。"
                          "R13：表格解析漏 31022 无形资产购置（2.98），已按逐行文字补全。")
        if all(it["verdict"] == "pass" for it in c["items"]):
            c["verdict"] = "pass"
            c["reason"] = ""


def fix_s04_2024_02(ann: dict) -> None:
    c = by_id(ann)["GS-S04"]
    if not any(v["quote"] == "上海市生态环境保护局（本级）" for it in c["items"] for v in it["values"]):
        c["items"].append({
            "label": "单位构成表单位名称多字",
            "verdict": "defect",
            "values": [V("单位构成表第1行", 8, "上海市生态环境保护局（本级）", "")],
            "formula": "",
            "note": ("第8页单位构成表第1行写“上海市生态环境保护局（本级）”，规范全称为"
                     "“上海市生态环境局”，多“保护”二字（R5：全部出现位置为第8页 1 处）。"),
        })
    c["verdict"] = "defect"
    c["reason"] = "第25页第三部分标题“局局”多一字；第8页单位构成表“生态环境保护局”多“保护”二字。"


def fix_w01_2024_02(ann: dict) -> None:
    c = by_id(ann)["GS-W01"]
    q = "自然资源海洋气象等支出623.91，占0.57%"
    if not any(v["quote"] == q for it in c["items"] for v in it["values"]):
        c["items"].append({
            "label": "结构情况金额漏单位",
            "verdict": "defect",
            "values": [V("结构情况金额", 26, q)],
            "formula": "",
            "note": ("第26页结构情况写“自然资源海洋气象等支出623.91，占0.57%”，"
                     "同段其余项均带“万元”，此处漏“万元”（R12：正文金额漏单位归 GS-W01）。"),
        })
    c["verdict"] = "defect"
    c["reason"] = "结构情况“自然资源海洋气象等支出623.91”漏“万元”。"
    n05 = by_id(ann)["GS-N05"]
    for it in n05["items"]:
        if it["label"] == "结构情况":
            it["note"] = it["note"].replace("其中“自然资源海洋气象等支出623.91”漏“万元”，见自由发现。",
                                            "其中“自然资源海洋气象等支出623.91”漏“万元”归 GS-W01（R12）。")


def fix_n05_2025_04(ann: dict) -> None:
    c = by_id(ann)["GS-N05"]
    q = "卫生健康（类）60.56 万元"
    if not any(v["quote"] == q for it in c["items"] for v in it["values"]):
        c["items"].append({
            "label": "结构情况类名漏“支出”",
            "verdict": "defect",
            "values": [V("正文结构段", 17, q)],
            "formula": "",
            "note": ("第17页结构段写“卫生健康（类）60.56 万元”，表内类名为“卫生健康支出”，"
                     "漏“支出”二字（R12：一般公共预算支出说明的科目名称错误归 GS-N05）。"),
        })
    for it in c["items"]:
        if it["label"] == "结构情况":
            it["note"] = it["note"].replace("“卫生健康（类）”漏“支出”二字，属类名简写，与逐项科目名无关，列自由发现。",
                                            "“卫生健康（类）”漏“支出”二字见本项“结构情况类名”子项。")
    c["verdict"] = "defect"
    c["reason"] = "逐项说明第4项“其它”与表内“其他”不一致；结构段“卫生健康（类）”漏“支出”。"


def fix_n05_note_2024_04(ann: dict) -> None:
    c = by_id(ann)["GS-N05"]
    for it in c["items"]:
        if it["label"] == "逐项年初预算合计":
            it["note"] = ("12 项与表一致、未漏列；逐项合计比总额少 300.00，"
                          "PDF 内无法判断是某项少计还是总额多计。")


def rebuild_format(ann: dict, sid: str) -> None:
    cats = scan_format(sid)
    kept = [f for f in ann["free_findings"] if f.get("category") not in ("格式", "标点")]
    for key, cat, desc in FORMAT_SPEC:
        occ = cats.get(key) or []
        if not occ:
            continue
        kept.append({
            "id": "",
            "page": occ[0]["page"],
            "category": cat,
            "quote": occ[0]["quote"],
            "expected": f"{desc}；共 {len(occ)} 处，见 occurrences",
            "confidence": 0.9,
            "occurrences": occ,
        })
    ann["free_findings"] = kept


def drop_free(ann: dict, predicate) -> None:
    ann["free_findings"] = [f for f in ann["free_findings"] if not predicate(f)]


def fix(sid: str) -> None:
    path = GOLD / sid / "annotation.json"
    ann = json.loads(path.read_text(encoding="utf-8"))

    if sid in D2_PASS:
        cid, note = D2_PASS[sid]
        c = by_id(ann)[cid]
        set_verdict(c, "pass", "")
        for it in c["items"]:
            it["note"] = note

    if sid == "GS-2025-02":
        fix_t08(ann)
    if sid == "GS-2024-02":
        fix_s04_2024_02(ann)
        fix_w01_2024_02(ann)
        drop_free(ann, lambda f: f.get("quote", "").startswith("自然资源海洋气象等支出623.91"))
    if sid == "GS-2025-04":
        fix_n05_2025_04(ann)
        drop_free(ann, lambda f: f.get("quote", "").startswith("卫生健康（类）60.56"))
    if sid == "GS-2024-04":
        fix_n05_note_2024_04(ann)
    if sid == "GS-2022-02":
        drop_free(ann, lambda f: "购臵" in f.get("quote", ""))

    rebuild_format(ann, sid)

    ann["free_findings"].sort(key=lambda f: (f["page"], f.get("category", "")))
    for i, f in enumerate(ann["free_findings"], 1):
        f["id"] = f"F{i:02d}"
    path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sid}: free={len(ann['free_findings'])}")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        fix(s)
