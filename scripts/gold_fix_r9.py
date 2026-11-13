#!/usr/bin/env python3
"""第 9 轮：REVIEW_R9 第一节 8 条金标修订（7 份）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
sys.path.insert(0, str(ROOT / "scripts"))
from gold_build import annotation_frozen  # noqa: E402

V = lambda n, p, q, u="": {"name": n, "page": p, "quote": q, "unit": u}  # noqa: E731
KEY = {"GS-S01": "s01", "GS-S02": "s02", "GS-S03": "s03", "GS-S04": "s04",
       "GS-N01": "n01", "GS-N02": "n02", "GS-N03": "n03", "GS-N04": "n04",
       "GS-N05": "n05", "GS-N06": "n06", "GS-N07": "n07", "GS-N08": "n08",
       "GS-N09": "n09", "GS-W01": "w01"}


def load(sid: str) -> dict:
    return json.loads((GOLD / sid / "annotation.json").read_text(encoding="utf-8"))


def save(sid: str, ann: dict) -> None:
    (GOLD / sid / "annotation.json").write_text(
        json.dumps(ann, ensure_ascii=False, indent=2), encoding="utf-8")


def check(ann: dict, cid: str) -> dict:
    return next(c for c in ann["checks"] if c["check_id"] == cid)


def item(ann: dict, cid: str, label: str) -> dict:
    return next(i for i in check(ann, cid)["items"] if i["label"] == label)


def put(ann: dict, cid: str, verdict: str, items: list[dict], reason: str = "") -> None:
    c = check(ann, cid)
    c["verdict"], c["items"], c["reason"] = verdict, items, reason


def amend(ann: dict, change: str, reason: str) -> None:
    ann.setdefault("amendments", []).append(
        {"date": "2026-11-03", "change": change, "reason": reason})


def renumber(ann: dict) -> None:
    ann["free_findings"].sort(key=lambda f: (f["page"], f.get("category", "")))
    for i, f in enumerate(ann["free_findings"], 1):
        f["id"] = f"F{i:02d}"


def sync_judge(sid: str, cids: list[str]) -> None:
    jp = GOLD / sid / "judge.json"
    if not jp.exists():
        return
    ann = load(sid)
    j = json.loads(jp.read_text(encoding="utf-8"))
    for cid in cids:
        c = check(ann, cid)
        j[KEY[cid]] = [c["verdict"], c["items"], c.get("reason", "")]
    j["free"] = [{k: v for k, v in f.items() if k not in ("id", "scan")}
                 for f in ann["free_findings"] if not f.get("scan")]
    jp.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")


def fix_2022_04() -> None:
    sid = "GS-2022-04"
    ann = load(sid)
    it = item(ann, "GS-N05", "逐项科目名称与年初预算")
    it["verdict"] = "defect"
    it["values"] += [
        V("第6项正文款名", 21, "卫生健康支出（类）行政事业单位医疗（款）其他卫生\n健康支出（项）"),
        V("表内21099款编码", 13, "21099"),
        V("表内21099款名", 13, "其他卫生健康支出"),
    ]
    it["note"] = ("第6项正文款名写“行政事业单位医疗”，表内 21099 款名为“其他卫生健康支出”，款名错配；"
                  "其余 8 项名称与金额一致。支出决算逐项合计 7,314.02、年初预算逐项合计 7,480.76，均与正文相符。"
                  "第3项“年初预算为129.10 元”的单位错误计入 GS-W01。")
    put(ann, "GS-N05", "defect", check(ann, "GS-N05")["items"],
        "第6项款名“行政事业单位医疗”与表内“其他卫生健康支出”（21099）不一致。")
    amend(ann, "GS-N05 pass→defect；逐项科目名称子项第6项款名由“行政事业单位医疗”改为按表内“其他卫生健康支出”（21099）核对。",
          "基线 §7 第 1 处，REVIEW_R9 第 1 条")
    save(sid, ann)
    sync_judge(sid, ["GS-N05"])


def fix_2024_03() -> None:
    sid = "GS-2024-03"
    ann = load(sid)
    it = item(ann, "GS-N05", "逐项科目名称、年初预算、支出决算")
    it["verdict"] = "defect"
    it["values"] += [
        V("第16项正文款名", 27, "商业服务业等支出（类）涉外发展服务（款）其他涉外\n发展服务支出（项）"),
        V("表内21606款编码", 17, "21606"),
        V("表内21606款名", 17, "涉外发展服务支出"),
    ]
    it["note"] = ("第16项正文款名“涉外发展服务”，表内 21606 为“涉外发展服务支出”，漏“支出”二字；"
                  "其余 17 项名称与表一致。支出决算逐项合计 208,270.51 对总额 208,270.50 差 0.01、"
                  "年初预算逐项合计 184,912.11 对总额 184,912.10 差 0.01，均在容差内；各增减方向与数字一致。")
    put(ann, "GS-N05", "defect", check(ann, "GS-N05")["items"],
        "逐项说明第16项款名漏“支出”，与表内“涉外发展服务支出”不一致。")
    ann["free_findings"].append({
        "id": "", "page": 32, "category": "模板残留",
        "quote": "上海市数据局部门（单位）共有\n车辆11 辆",
        "expected": "模板待选项“部门（单位）”未选，本材料为部门决算，应写“本部门”",
        "confidence": 0.9,
    })
    renumber(ann)
    amend(ann, "GS-N05 pass→defect；逐项子项第16项款名补核，表内 21606 为“涉外发展服务支出”，正文漏“支出”。",
          "基线 §7 第 2 处，REVIEW_R9 第 2 条")
    amend(ann, "新增自由发现 F01（模板残留）：第32页“部门（单位）”待选项未选。",
          "基线 §7 第 7 处，REVIEW_R9 第 7 条")
    save(sid, ann)
    sync_judge(sid, ["GS-N05"])


def fix_2025_02() -> None:
    sid = "GS-2025-02"
    ann = load(sid)
    it = item(ann, "GS-N05", "逐项科目名称")
    it["values"] += [
        V("第12项正文款名", 23, "社会保障和就业支出（类）行政事业单位养老支出（款）\n死亡抚恤（项）"),
        V("表内20808款编码", 14, "20808"),
        V("表内20808款名", 14, "抚恤"),
        V("第15项正文款名", 24, "卫生健康支出（类）行政事业单位医疗（款）其他卫生\n健康支出（项）"),
        V("表内21099款编码", 14, "21099"),
        V("表内21099款名", 14, "其他卫生健康支出"),
    ]
    it["note"] = ("第12项正文款名写“行政事业单位养老支出”，表内 20808 为“抚恤”；"
                  "第15项正文款名写“行政事业单位医疗”，表内 21099 为“其他卫生健康支出”。"
                  "连同前 6 项类名漏“支出”，共 8 处名称不一致；17 项支出决算、年初预算合计均与正文相符。")
    put(ann, "GS-N05", "defect", check(ann, "GS-N05")["items"],
        "逐项说明第12、15项款名与表内不符（应为“抚恤”“其他卫生健康支出”），另前6项类名漏“支出”。")
    amend(ann, "GS-N05 逐项科目名称子项补两处款名：第12项应为“抚恤”（20808），第15项应为“其他卫生健康支出”（21099）。",
          "基线 §7 第 3 处，REVIEW_R9 第 3 条")
    save(sid, ann)
    sync_judge(sid, ["GS-N05"])


def fix_2024_07() -> None:
    sid = "GS-2024-07"
    ann = load(sid)
    it = {
        "label": "完成率", "verdict": "defect",
        "values": [V("年初预算", 17, "年初预算为756.6 万元"),
                   V("支出决算", 17, "支出决算为738.14 万元"),
                   V("正文完成率", 17, "完成年初预算的95.83%")],
        "formula": "738.14 / 756.6 = 97.56%（正文写 95.83%）",
        "note": "738.14/756.6=97.56%，正文写 95.83%，差 1.73 个百分点（R16 区间 97.56%–97.56%），判 defect。",
        "rate_check": {"value": 95.83, "low": 97.56, "high": 97.56, "diff": 1.73},
    }
    c = check(ann, "GS-N05")
    c["items"].append(it)
    put(ann, "GS-N05", "defect", c["items"],
        c["reason"] + "；另完成率 95.83% 按 738.14/756.6 应为 97.56%，差 1.73 个百分点。")
    amend(ann, "GS-N05 逐项子项补完成率：第17页“完成年初预算的95.83%”按 738.14/756.6 应为 97.56%，差 1.73 个百分点。",
          "基线 §7 第 4 处，REVIEW_R9 第 4 条")
    save(sid, ann)
    sync_judge(sid, ["GS-N05"])


def fix_2025_09() -> None:
    sid = "GS-2025-09"
    ann = load(sid)
    items = [{
        "label": "财政拨款收入支出总计", "verdict": "defect",
        "values": [V("正文金额", 19, "财政拨款收\n入支出总计6533.55 万元"),
                   V("表列总计", 12, "6,602.06"),
                   V("年初财政拨款结转和结余", 12, "68.51")],
        "formula": "6533.55 + 68.51 = 6602.06",
        "note": ("正文取的是本年支出合计 6,533.55，而财政拨款总表“总计”为 6,602.06，差 68.51 万元"
                 "（即年初财政拨款结转和结余）；N01 的 6,602.06 正确。"),
    }]
    put(ann, "GS-N04", "defect", items,
        "财政拨款收入支出总计少计年初结转 68.51 万元，与表内总计 6,602.06 不符。")
    amend(ann, "GS-N04 pass→defect：正文财政拨款收入支出总计写 6533.55，表内总计 6602.06，差年初结转 68.51。",
          "基线 §7 第 5 处，REVIEW_R9 第 5 条")
    save(sid, ann)
    sync_judge(sid, ["GS-N04"])


def fix_2025_04() -> None:
    sid = "GS-2025-04"
    ann = load(sid)
    c = check(ann, "GS-N07")
    c["items"].append({
        "label": "同比", "verdict": "defect",
        "values": [V("接待费同比", 20, "公务接待费支出决算增加0.16 万元，增长\n100.00%"),
                   V("接待费决算", 20, "公务接待费支出决算0.16 万元")],
        "formula": "",
        "note": ("公务接待费本年 0.16 万元、增加 0.16 万元，上年数为 0；基数为 0、分子不为 0，"
                 "增长率无定义，正文写“增长100.00%”（R26），应写“上年为 0”或不写百分比。"),
        "rate_check": {"undefined_base": True, "r26": True},
    })
    put(ann, "GS-N07", "defect", c["items"],
        "公务接待费上年数为 0，正文却写“增长100.00%”。")
    amend(ann, "GS-N07 pass→defect：第20页公务接待费增加0.16万元、上年为0，写“增长100.00%”按 R26 判 defect。",
          "基线 §7 第 6 处，REVIEW_R9 第 6 条")
    save(sid, ann)
    sync_judge(sid, ["GS-N07"])


def fix_2025_07() -> None:
    sid = "GS-2025-07"
    ann = load(sid)
    ann["free_findings"].append({
        "id": "", "page": 19, "category": "错别字",
        "quote": "公务车运行维护费",
        "expected": "公务用车运行维护费",
        "confidence": 0.8,
    })
    renumber(ann)
    amend(ann, "新增自由发现（错别字）：第19页“公务车运行维护费”应为“公务用车运行维护费”。",
          "基线 §7 第 8 处，REVIEW_R9 第 8 条")
    save(sid, ann)
    sync_judge(sid, [])


def main() -> int:
    fns = {"GS-2022-04": fix_2022_04, "GS-2024-03": fix_2024_03, "GS-2025-02": fix_2025_02,
           "GS-2024-07": fix_2024_07, "GS-2025-09": fix_2025_09, "GS-2025-04": fix_2025_04,
           "GS-2025-07": fix_2025_07}
    for sid in sys.argv[1:] or list(fns):
        if annotation_frozen(sid):
            print(f"{sid}: 已冻结，跳过")
            continue
        fns[sid]()
        print(f"{sid}: 已修订")
    return 0


if __name__ == "__main__":
    sys.exit(main())
