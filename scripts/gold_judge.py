#!/usr/bin/env python3
"""把人工判定（S/N/W + 非格式自由发现）注入骨架产物。

用法：python scripts/gold_judge.py GS-2022-02
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
sys.path.insert(0, str(ROOT / "scripts"))
from gold_build import (  # noqa: E402
    scan_format, annotation_frozen, is_template_phrase, scan_r21, scan_r22, scan_r24, scan_r28,
)

V = lambda n, p, q, u="万元": {"name": n, "page": p, "quote": q, "unit": u}  # noqa: E731

JUDGE = {
    "GS-2022-02": {
        "s01": ("defect", [{"label": "第7张表表名", "verdict": "defect",
                            "values": [V("表名", 17, "一般公共预算财政拨款“三公”经费支出决算表", "")],
                            "formula": "",
                            "note": "规范表名为“财政拨款‘三公’经费支出决算表”，不加“一般公共预算”（AGENTS.md E-06）。其余 8 张表名与规范一致；两张空表均附说明并写明本单位名称。"}],
                 "第17页三公经费表表名多出“一般公共预算”五字。"),
        "s02": ("pass", [{"label": "九个说明章节与第X部分", "verdict": "pass",
                          "values": [V("第二部分", 6, "第二部分", ""), V("第四部分", 28, "第四部分", "")],
                          "formula": "", "note": "四个部分标题齐备；九个说明章节齐备（一至五在第20页，六、七在第24页，八、九在第25页）。"}], ""),
        "s03": ("pass", [{"label": "目录与正文标题", "verdict": "pass",
                          "values": [V("目录第二项", 2, "二、部门决算单位构成", ""),
                                     V("正文第二项", 5, "二、部门决算单位构成", "")],
                          "formula": "",
                          "note": "九表九章名称与目录一致；目录“十、预算绩效管理情况说明”与正文“十、预算绩效管理情况”属模板措辞差（R3），不计。"}], ""),
        "s04": ("pass", [{"label": "单位名称与年度", "verdict": "pass",
                          "values": [V("封面", 1, "上海市人民政府外事办公室", ""),
                                     V("表头", 6, "上海市人民政府外事办公室2022 年度部门决算表", "")],
                          "formula": "", "note": "封面、表头、正文、单位构成表 4 个单位名称一致，年度全篇为 2022。"}], ""),
        "n01": ("pass", [{"label": "收入支出总计", "verdict": "pass",
                          "values": [V("正文金额", 20, "收入支出总计15,595.85 万元"),
                                     V("同比减少额", 20, "减少452.45 万元")],
                          "formula": "452.45 / 16,048.30 = 2.82%",
                          "note": "与总表总计 15,595.85 一致。上年数 16,048.30 = 15,595.85 + 452.45。"}], ""),
        "n02": ("pass", [{"label": "收入金额与占比", "verdict": "pass",
                          "values": [V("本年收入合计", 20, "本年收入合计14,041.79 万元"),
                                     V("财政拨款收入", 20, "财政拨款收入14,021.89 万元"),
                                     V("财政拨款占比", 20, "占99.86%", "%"),
                                     V("其他收入占比", 20, "占0.14%", "%")],
                          "formula": "14,021.89 / 14,041.79 = 99.86%",
                          "note": "其他收入 19.90/14,041.79=0.14%，两项合计 100.00%。"}], ""),
        "n03": ("pass", [{"label": "支出金额与占比", "verdict": "pass",
                          "values": [V("本年支出合计", 20, "本年支出合计14,790.84 万元"),
                                     V("基本支出", 20, "基本支出12,744.34 万元"),
                                     V("基本支出占比", 20, "占86.16%", "%"),
                                     V("项目支出占比", 20, "占13.84%", "%")],
                          "formula": "12,744.34 / 14,790.84 = 86.16%",
                          "note": "2,046.50/14,790.84=13.84%，两项合计 100.00%。"}], ""),
        "n04": ("pass", [{"label": "财政拨款总计", "verdict": "pass",
                          "values": [V("正文金额", 20, "财政拨款收入支出总计"),
                                     V("正文金额明细", 20, "15,076.91 万元"),
                                     V("同比减少额", 20, "减少452.09"),
                                     V("表列总计", 13, "15,076.91")],
                          "formula": "452.09 / 15,529.00 = 2.91%",
                          "note": "与财政拨款总表总计 15,076.91 一致。上年数 15,529.00 = 15,076.91 + 452.09。"}], ""),
        "n05": ("defect", [
            {"label": "总体情况", "verdict": "pass",
             "values": [V("一般公共预算支出", 20, "一般公共预算财政拨款支出14,770.55 万元"),
                        V("同比增加额", 20, "增加296.56 万元"),
                        V("本年支出合计", 20, "本年支出合计14,790.84 万元")],
             "formula": "14,770.55 / 14,790.84 = 99.86%",
             "note": "同比 296.56/14,473.99=2.05%，上年数 14,473.99 = 14,770.55 - 296.56。"},
            {"label": "结构情况", "verdict": "pass",
             "values": [V("一般公共服务", 21, "占76.13%", "%"), V("社会保障和就业", 21, "占9.14%", "%"),
                        V("卫生健康", 21, "占3.25%", "%"), V("住房保障", 21, "占11.49%", "%"),
                        V("一般公共服务支出", 21, "一般公共服务支出（类）11,244.24 万元"),
                        V("一般公共预算支出", 21, "一般公共预算财政拨款支出14,770.55 万元")],
             "formula": "11,244.24 / 14,770.55 = 76.13%",
             "note": "四项占比合计 100.01%，与 100% 差 0.01 个百分点，在 0.1 容差内。"},
            {"label": "逐项科目名称", "verdict": "defect",
             "values": [V("正文款名", 21, "政府办公厅及相关机构事务"),
                        V("表内款名", 14, "政府办公厅（室）及相关机构事务"),
                        V("完成率", 21, "完成年初预算的116.35%", "%")],
             "formula": "14,770.55 / 12,694.81 = 116.35%",
             "note": "第1至3项正文写“政府办公厅及相关机构事务（款）”，表内为“政府办公厅（室）及相关机构事务”，漏“（室）”。13 项支出决算合计 14,770.55、年初预算逐项合计 12,694.82 对正文 12,694.81 差 0.01，均在容差内。"}],
                 "逐项说明中“政府办公厅及相关机构事务”漏“（室）”，与表内科目名称不一致。"),
        "n06": ("pass", [{"label": "基本支出 = 人员经费 + 公用经费", "verdict": "pass",
                          "values": [V("基本支出", 24, "基本支出12,724.06 万元"),
                                     V("人员经费", 24, "人员经费\n10,379.43 万元"),
                                     V("公用经费", 24, "公用经费2,344.62 万元")],
                          "formula": "10,379.43 + 2,344.62 = 12,724.05",
                          "note": "与第15页两张合计行一致；复算 12,724.05 对正文 12,724.06 差 0.01，在容差内。"}], ""),
        "n07": ("pass", [
            {"label": "金额与表一致", "verdict": "pass",
             "values": [V("年初预算", 24, "年初预算为111.90 万元"), V("支出决算", 24, "支出决算为"),
                        V("支出决算明细", 24, "34.15 万元"), V("车决算", 24, "26.92"),
                        V("接待决算", 24, "7.23"), V("完成率", 24, "完成预算的30.52%", "%")],
             "formula": "34.15 / 111.90 = 30.52%",
             "note": "车 26.92/48.40=55.62%、接待 7.23/37.50=19.28%，与正文一致；构成占比 78.83%+21.17%=100.00%。"},
            {"label": "数量要素", "verdict": "pass",
             "values": [V("出国团组人次", 25, "团组0 个、累计0 人次", ""),
                        V("接待批次人次", 25, "共97 批次、383", ""),
                        V("保有量", 25, "保有量为14 辆", "")],
             "formula": "", "note": "购置费为 0，无须购置数量；接待费 7.23>0 已写批次与人次。"}], ""),
        "n08": ("pass", [{"label": "两表无收支的说明", "verdict": "pass",
                          "values": [V("政府性基金", 25, "无政府性基金预算财政拨款收入", ""),
                                     V("国资经营", 25, "无国有资本经营预算财政拨款收", "")],
                          "formula": "", "note": "与第18、19页空表说明一致（该说明写明了本单位名称）。"}], ""),
        "n09": ("pass", [{"label": "增减变化写明原因", "verdict": "pass",
                          "values": [V("收支总计减少原因", 20, "2022 年较2021 年减少了建党100 周年外事系列活动项目支出", "")],
                          "formula": "109.50 / 2,302.76 = 4.76%",
                          "note": "机关运行经费 2,193.26 万元、减少 109.50（4.76%）已写原因；上年数 2,302.76 = 2,193.26 + 109.50；13 个科目变动项均有原因。"}], ""),
        "w01": ("defect", [{"label": "逐项金额单位", "verdict": "defect",
                            "values": [V("第4项支出决算", 22, "支出决算为218.20 元")],
                            "formula": "",
                            "note": "第22页第4项写“支出决算为218.20 元”，同句年初预算为 151.35 万元，量级应为万元（AGENTS.md C-14 / R001）。"}],
                 "逐项说明第4项金额单位错写为“元”，应为“万元”。"),
        "free": [
            {"page": 18, "category": "模板残留", "quote": "无政府性基金预算财政拨款收入支出的，仍保留该表格式，并在本表下方作下述说明：",
             "expected": "删除该模板填写指引句", "confidence": 0.95},
            {"page": 25, "category": "错别字", "quote": "公务用车购臵及运行维护费", "expected": "公务用车购置及运行维护费",
             "confidence": 0.85},
        ],
    },
}


def apply(sid: str) -> None:
    if annotation_frozen(sid):
        print(f"{sid}: 已冻结，跳过 judge")
        return
    path = GOLD / sid / "annotation.json"
    ann = json.loads(path.read_text(encoding="utf-8"))
    jf = GOLD / sid / "judge.json"
    j = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else JUDGE[sid]

    def put(cid: str, entry) -> None:
        verdict, items, reason = entry
        for c in ann["checks"]:
            if c["check_id"] != cid:
                continue
            c["items"] = items
            c["verdict"] = verdict
            c["reason"] = reason

    for k, cid in (("s01", "GS-S01"), ("s02", "GS-S02"), ("s03", "GS-S03"), ("s04", "GS-S04"),
                   ("n01", "GS-N01"), ("n02", "GS-N02"), ("n03", "GS-N03"), ("n04", "GS-N04"),
                   ("n05", "GS-N05"), ("n06", "GS-N06"), ("n07", "GS-N07"), ("n08", "GS-N08"),
                   ("n09", "GS-N09"), ("w01", "GS-W01")):
        if k in j:
            put(cid, j[k])
    for cid, entry in (j.get("checks") or {}).items():
        put(cid, entry)
    for cid, note in (j.get("notes") or {}).items():
        for c in ann["checks"]:
            if c["check_id"] == cid and c.get("items"):
                c["items"][0]["note"] = note
    for c in ann["checks"]:
        if c.get("verdict") == "pass":
            c["reason"] = ""

    ann["free_findings"] = [f for f in j.get("free", [])
                             if not is_template_phrase(f.get("quote", ""))]
    cats = scan_format(sid)
    spec = [("千分位", "格式", "金额未按半角逗号分千分位（或用了全角逗号）"),
            ("小数位", "格式", "金额/百分比小数位不是两位"),
            ("半角冒号", "标点", "中文语境使用半角冒号"),
            ("标点连用", "标点", "标点符号连用"),
            ("双小数点", "标点", "数字出现双小数点")]
    for key, cat, desc in spec:
        occ = cats.get(key) or []
        if not occ:
            continue
        ann["free_findings"].append({"id": "", "page": occ[0]["page"], "category": cat,
                                     "quote": occ[0]["quote"],
                                     "expected": f"{desc}；共 {len(occ)} 处，见 occurrences",
                                     "confidence": 0.9, "occurrences": occ})

    def flat(x: str) -> str:
        return re.sub(r"\s+", "", x or "")

    # R21/R22/R24：程序全文扫描；同类的旧条目已并入这一条（R4/R22）
    scans = [
        ("R21", "其他", "材料口径与封面不一致", scan_r21(sid)),
        ("R22", "标点", "中文语境使用半角逗号、半角分号或句末半角句点", scan_r22(sid)),
        ("R24", "其他", "同一列表序号体例混用", scan_r24(sid)),
        ("R28", "标点", "文号年份未用六角括号“〔〕”", scan_r28(sid)),
    ]
    for rule, cat, desc, occ in scans:
        if not occ:
            continue
        ann["free_findings"] = [
            f for f in ann["free_findings"]
            if not any(flat(f.get("quote")) and flat(f.get("quote")) in flat(o["quote"]) for o in occ)
        ]
        ann["free_findings"].append({"id": "", "scan": rule, "page": occ[0]["page"], "category": cat,
                                     "quote": occ[0]["quote"],
                                     "expected": f"{desc}；共 {len(occ)} 处，见 occurrences",
                                     "confidence": 0.9, "occurrences": occ})
    ann["free_findings"].sort(key=lambda f: (f["page"], f.get("category", "")))
    for i, f in enumerate(ann["free_findings"], 1):
        f["id"] = f"F{i:02d}"
    path.write_text(json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sid}: 人工判定已注入，free={len(ann['free_findings'])}")


if __name__ == "__main__":
    for s in sys.argv[1:]:
        apply(s)
