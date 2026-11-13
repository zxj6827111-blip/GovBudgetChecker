#!/usr/bin/env python3
"""第 2 轮返工：补 R10 运算数、删 R8 模板措辞、按 R9 重建格式类自由发现。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
sys.path.insert(0, str(ROOT / "scripts"))
from gold_build import scan_format  # noqa: E402

ADD = {
    "GS-2022-01": {
        "GS-N02": [(0, "财政拨款收入", 17, "8,679.87")],
        "GS-N03": [(0, "基本支出", 17, "5,772.48")],
        "GS-N04": [(0, "同比减少额", 17, "141.5")],
        "GS-N05": [(0, "本年支出合计", 17, "9,040.76"),
                   (1, "卫生健康支出", 18, "238.12"),
                   (1, "一般公共预算支出", 18, "8,679.88")],
        "GS-N07": [(0, "车决算", 22, "26.11"), (0, "接待决算", 22, "0.73")],
    },
    "GS-2023-01": {
        "GS-N01": [(0, "同比增加额", 28, "27,768.57")],
        "GS-N02": [(0, "财政拨款收入", 28, "368,647.32")],
        "GS-N03": [(0, "基本支出", 28, "98,622.11"), (0, "项目支出", 28, "287,726.24"),
                   (0, "经营支出", 28, "1,012.06")],
        "GS-N04": [(0, "同比增加额", 29, "28,312.47")],
        "GS-N05": [(0, "本年支出合计", 28, "387,360.41"),
                   (1, "城乡社区支出", 29, "238,705.77"), (1, "一般公共预算支出", 29, "329,243.52"),
                   (2, "第9项支出决算", 31, "4,929.45"), (2, "第5项支出决算", 30, "3,435.22")],
        "GS-N07": [(1, "车决算", 36, "280.14"), (1, "车小计预算", 25, "389.40")],
        "GS-N09": [(0, "机关运行经费增加额", 39, "769.84"), (0, "机关运行经费", 39, "3,560.29")],
    },
    "GS-2024-01": {
        "GS-N01": [(0, "同比减少额", 24, "41099.89")],
        "GS-N02": [(0, "财政拨款收入", 24, "293022.01")],
        "GS-N03": [(0, "基本支出", 24, "108207.57")],
        "GS-N04": [(0, "同比减少额", 24, "55593.7")],
        "GS-N05": [(0, "本年支出合计", 24, "294165.11"),
                   (1, "社会保障和就业支出", 25, "209237.07"), (1, "一般公共预算支出", 25, "291858.57"),
                   (2, "年初预算", 25, "250203.28"), (2, "支出决算", 25, "291858.57")],
        "GS-N09": [(0, "机关运行经费增加额", 34, "1784.44"), (0, "机关运行经费", 34, "14039.33")],
    },
    "GS-2025-01": {
        "GS-N01": [(0, "同比增加额", 17, "432.96")],
        "GS-N02": [(0, "财政拨款收入", 17, "19,740.19")],
        "GS-N03": [(0, "基本支出", 17, "19,354.90"), (0, "项目支出", 17, "502.84")],
        "GS-N04": [(0, "年初财政拨款结转和结余", 10, "16.20")],
        "GS-N05": [(0, "本年支出合计", 17, "19,857.74"),
                   (1, "公共安全支出", 18, "13,616.30"), (1, "一般公共预算支出", 18, "19,705.89")],
        "GS-N09": [(0, "机关运行经费增加额", 24, "41.06"), (0, "机关运行经费", 24, "2,558.65")],
    },
}

DERIVE = {
    "GS-2022-01": {
        "GS-N04": "上年数 8,559.29 = 8,700.79 - 141.5。",
    },
    "GS-2023-01": {
        "GS-N01": "上年数 392,902.44 = 420,671.01 - 27,768.57。",
        "GS-N04": "上年数 342,212.73 = 370,525.20 - 28,312.47。",
        "GS-N09": "上年数 2,790.45 = 3,560.29 - 769.84。",
    },
    "GS-2024-01": {
        "GS-N01": "上年数 365677.50 = 324577.61 + 41099.89。",
        "GS-N04": "上年数 351166.00 = 295572.30 + 55593.7。",
        "GS-N09": "上年数 12254.89 = 14039.33 - 1784.44。",
    },
    "GS-2025-01": {
        "GS-N01": "上年数 19475.28 = 19,908.24 - 432.96。",
        "GS-N09": "上年数 2517.59 = 2,558.65 - 41.06。",
    },
}

# R8：照抄官方模板措辞，删除
DROP_FREE = {
    "GS-2022-01": ["国内公务接待支出0.73 万"],
    "GS-2023-01": ["国内公务接待支出45.85 万"],
    "GS-2024-01": ["国内公务接待支出37.2 万"],
    "GS-2025-01": ["国内公务接待支出0.14 万"],
}

FORMAT_SPEC = [
    ("千分位", "格式", "金额未按半角逗号分千分位（或用了全角逗号）", "1,234.56"),
    ("小数位", "格式", "金额/百分比小数位不是两位", "1,234.50"),
    ("半角冒号", "标点", "中文语境使用半角冒号", "："),
    ("标点连用", "标点", "标点符号连用", "。"),
    ("双小数点", "标点", "数字出现双小数点", "2.22"),
]


def patch(sid: str) -> None:
    path = GOLD / sid / "annotation.json"
    ann = json.loads(path.read_text(encoding="utf-8"))

    by_id = {c["check_id"]: c for c in ann["checks"]}
    for cid, adds in ADD.get(sid, {}).items():
        c = by_id[cid]
        for idx, name, page, quote in adds:
            if idx >= len(c["items"]):
                continue
            vals = c["items"][idx].setdefault("values", [])
            if any(v.get("quote") == quote for v in vals):
                continue
            vals.append({"name": name, "page": page, "quote": quote, "unit": "万元"})
        if cid in DERIVE.get(sid, {}):
            it = c["items"][0]
            if DERIVE[sid][cid] not in it.get("note", ""):
                it["note"] = (it.get("note", "") + DERIVE[sid][cid]).strip()

    # R8 删除
    drops = DROP_FREE.get(sid, [])
    ann["free_findings"] = [f for f in ann["free_findings"] if not any(d in f.get("quote", "") for d in drops)]

    # R9 重建格式类：先清掉所有格式/标点类，以及被 R9 覆盖的数字格式条目
    cats = scan_format(sid)
    covered_nums = set()
    for v in cats.values():
        for o in v:
            for tok in re.findall(r"-?\d[\d,]*(?:\.\d+)?", o["quote"]):
                covered_nums.add(tok.replace(",", ""))
    kept = []
    for f in ann["free_findings"]:
        if f.get("category") in ("格式", "标点"):
            continue
        q = f.get("quote", "")
        if f.get("category") in ("其他", "单位错误"):
            toks = [t2.replace(",", "") for t2 in re.findall(r"-?\d[\d,]*(?:\.\d+)?", q)]
            if toks and all(t2 in covered_nums for t2 in toks):
                continue
        kept.append(f)
    ann["free_findings"] = kept
    for key, cat, desc, sample in FORMAT_SPEC:
        occ = cats.get(key) or []
        if not occ:
            continue
        ann["free_findings"].append({
            "id": "",
            "page": occ[0]["page"],
            "category": cat,
            "quote": occ[0]["quote"],
            "expected": f"{desc}；共 {len(occ)} 处，见 occurrences",
            "confidence": 0.9,
            "occurrences": occ,
        })

    for i, f in enumerate(ann["free_findings"], 1):
        f["id"] = f"F{i:02d}"
    ann["free_findings"].sort(key=lambda f: f["page"])
    for i, f in enumerate(ann["free_findings"], 1):
        f["id"] = f"F{i:02d}"
    ann["annotator"] = "DeepSeek（Qoder 所选模型；具体版本未自证）"
    path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sid}: 补值 {sum(len(v) for v in ADD.get(sid, {}).values())}，"
          f"删 R8 {len(drops)} 条，格式类 {len(ann['free_findings'])} 条总计")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        patch(s)
