#!/usr/bin/env python3
"""第 7 轮返工：D2=A 回退、R12、R16、R27、R28、R29。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "gold"
sys.path.insert(0, str(ROOT / "scripts"))
from gold_build import annotation_frozen, scan_r28  # noqa: E402

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


def put(ann: dict, cid: str, verdict: str, items: list[dict], reason: str = "") -> None:
    c = check(ann, cid)
    c["verdict"], c["items"], c["reason"] = verdict, items, reason


def item(ann: dict, cid: str, label: str) -> dict:
    return next(i for i in check(ann, cid)["items"] if i["label"] == label)


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
    j["free"] = [{k: v for k, v in f.items() if k != "id" and k != "scan"}
                 for f in ann["free_findings"] if not f.get("scan")]
    jp.write_text(json.dumps(j, ensure_ascii=False, indent=2), encoding="utf-8")


def fix_2022_08() -> None:
    sid = "GS-2022-08"
    ann = load(sid)
    it = check(ann, "GS-S01")["items"][0]
    it["verdict"] = "pass"
    it["note"] = ("2022 年官方模板第七张表名即“一般公共预算财政拨款‘三公’经费支出决算表”，照模板写不报"
                  "（R8 按年度模板；D2=A）。其余八表齐备，政府性基金、国资经营两张空表均附说明。")
    put(ann, "GS-S01", "pass", [it], "")
    save(sid, ann)
    sync_judge(sid, ["GS-S01"])


def fix_2025_09() -> None:
    sid = "GS-2025-09"
    ann = load(sid)
    s02 = [{
        "label": "九个说明章节与第X部分", "verdict": "pass",
        "values": [V("第一部分", 4, "第一部分"), V("第三部分", 19, "第三部分"),
                   V("第四部分", 27, "第四部分"),
                   V("第九节", 25, "九、国有资本经营预算财政拨款收入支出决算情况说明")],
        "formula": "",
        "note": "2025 年部门决算用 2024 年模板，正文表格部分不写“第二部分”（R8；D2=A）；九个说明章节齐备。"}]
    put(ann, "GS-S02", "pass", s02, "")
    s03 = check(ann, "GS-S03")["items"][0]
    s03["note"] = s03["note"].rstrip("。") + "；年度笔误同时记 GS-S04（R27）。"
    put(ann, "GS-S03", "defect", [s03], check(ann, "GS-S03")["reason"])
    s04 = [{
        "label": "目录与正文年度", "verdict": "defect",
        "values": [V("封面", 1, "2025 年度部门决算"),
                   V("目录第三部分", 2, "第三部分上海虹桥国际中央商务区管理委员会202 年度部门决算情况说明"),
                   V("正文第三部分", 19, "2025 年度部门决算情况说明")],
        "formula": "",
        "note": "目录第2页第三部分标题年度误写为“202 年度”（漏“5”），与封面、正文的 2025 年度不一致（R27）；目录与正文标题不一致已记 GS-S03。"}]
    put(ann, "GS-S04", "defect", s04, "目录第三部分标题年度“202 年度”漏“5”，应为 2025 年度。")
    # R28：文号方括号
    f = next(x for x in ann["free_findings"] if "沪虹商管办[2014]" in x.get("quote", ""))
    f["scan"] = "R28"
    f["expected"] = "文号年份必须用六角括号“〔〕”，如“〔2014〕004号”"
    f["occurrences"] = scan_r28(sid)
    save(sid, ann)
    sync_judge(sid, ["GS-S02", "GS-S03", "GS-S04"])


def fix_2024_02() -> None:
    sid = "GS-2024-02"
    ann = load(sid)
    old = json.loads((GOLD / "_backup_r6" / sid / "annotation.json").read_text(encoding="utf-8"))
    s02 = next(c for c in old["checks"] if c["check_id"] == "GS-S02")
    w01 = next(c for c in old["checks"] if c["check_id"] == "GS-W01")
    put(ann, "GS-S02", s02["verdict"], s02["items"], s02.get("reason", ""))
    put(ann, "GS-W01", w01["verdict"], w01["items"], w01.get("reason", ""))
    s03 = check(ann, "GS-S03")["items"][0]
    s03["note"] = s03["note"].rstrip("。") + "；标题笔误同时记 GS-S04（R27）。"
    put(ann, "GS-S03", "defect", [s03], check(ann, "GS-S03")["reason"])
    # R12：623.91 漏“万元”归 W01，删自由发现
    ann["free_findings"] = [f for f in ann["free_findings"] if "623.91" not in f.get("quote", "")]
    renumber(ann)
    save(sid, ann)
    sync_judge(sid, ["GS-S02", "GS-S03", "GS-W01"])


def fix_2024_08() -> None:
    sid = "GS-2024-08"
    ann = load(sid)
    it = item(ann, "GS-N07", "同比")
    it["verdict"] = "pass"
    it["rate_check"] = {"value": 20.78, "low": 20.61, "high": 21.28, "diff": 0.17}
    it["note"] = ("上年数 1.48 由“1.17 + 0.31”倒推，各有 ±0.005 舍入：0.31/(1.17+0.31) 的区间约 20.61%–21.28%，"
                  "正文 20.78% 在区间内，属舍入差（R16）。总额增长 37.59%（复算 37.52%）差 0.07、"
                  "车下降 46.65%（复算 46.66%）均在容差内。")
    put(ann, "GS-N07", "pass", check(ann, "GS-N07")["items"], "")
    # R28：两处文号
    f = next(x for x in ann["free_findings"] if "沪府办外【2024】" in x.get("quote", ""))
    f["scan"] = "R28"
    f["quote"] = "沪府办外【2024】3892号"
    f["expected"] = "文号年份必须用六角括号“〔〕”，如“〔2024〕3892号”"
    f["occurrences"] = scan_r28(sid)
    save(sid, ann)
    sync_judge(sid, ["GS-N07"])


def fix_2024_07() -> None:
    sid = "GS-2024-07"
    ann = load(sid)
    it = item(ann, "GS-N04", "财政拨款总计")
    it["verdict"] = "pass"
    it["rate_check"] = {"value": 0.24, "low": 0.33, "high": 0.33, "diff": 0.09}
    it["note"] = ("按 738.37 − 2.44 = 735.93 复算增长率 0.33%，正文 0.24%，差 0.09 个百分点，"
                  "在 0.1 个百分点容差内，不判缺陷（R16）。")
    put(ann, "GS-N04", "pass", [it], "")
    save(sid, ann)
    sync_judge(sid, ["GS-N04"])


def fix_2023_08() -> None:
    sid = "GS-2023-08"
    ann = load(sid)
    it = item(ann, "GS-N05", "逐项科目名称")
    it["verdict"] = "pass"
    it["values"].append(V("第6页表内完整项名", 6, "机关事业单位基本养老保险缴费支出"))
    it["note"] = ("第10、11页表内单元格因列宽截断为“机关事业单位基本养老保险缴费”，第6、7页收入/支出决算表"
                  "及第17页说明均为完整“…缴费支出”，按 R29 以完整形态为准，不判名称不一致。")
    put(ann, "GS-N05", "pass", check(ann, "GS-N05")["items"], "")
    save(sid, ann)
    sync_judge(sid, ["GS-N05"])


def fix_2024_05() -> None:
    sid = "GS-2024-05"
    ann = load(sid)
    item5 = [{
        "label": "目录“七、一般公共预算财政拨款‘三公’经费…”与正文标题",
        "verdict": "defect",
        "values": [V("目录第七项", 3, "七、一般公共预算财政拨款“三公”经费支出决算情况说明"),
                   V("正文第七节", 21, "七、财政拨款“三公”经费支出决算情况说明")],
        "formula": "",
        "note": "2024 年单位模板目录写“七、财政拨款‘三公’经费支出决算情况说明”，正文与模板一致；目录多“一般公共预算财政拨款”，属目录笔误（R23）。"}]
    put(ann, "GS-S03", "defect", item5, "目录第七项多“一般公共预算财政拨款”，与正文标题不一致。")
    save(sid, ann)
    sync_judge(sid, ["GS-S03"])


def fix_2023_07() -> None:
    sid = "GS-2023-07"
    ann = load(sid)
    it = item(ann, "GS-N07", "同比增幅")
    it["rate_check"] = {"undefined_base": True, "r26": True}
    it["note"] = "因公出国费上年数为 0（66.48 − 66.48），增长率无定义，正文写“增长100%”；按 R26 记 defect。"
    add = {
        "id": "", "scan": "R28", "page": 29, "category": "标点",
        "quote": scan_r28(sid)[0]["quote"],
        "expected": "文号年份必须用六角括号“〔〕”，如“〔2023〕3号”",
        "confidence": 0.9, "occurrences": scan_r28(sid),
    }
    ann["free_findings"] = [f for f in ann["free_findings"]
                            if not (f.get("scan") == "R28" or "沪化管[2023]" in f.get("quote", ""))]
    ann["free_findings"].append(add)
    renumber(ann)
    save(sid, ann)
    sync_judge(sid, ["GS-N07"])


def main() -> int:
    for sid in sys.argv[1:] or ["GS-2022-08", "GS-2023-07", "GS-2023-08", "GS-2024-02",
                                "GS-2024-05", "GS-2024-07", "GS-2024-08", "GS-2025-09"]:
        if annotation_frozen(sid):
            print(f"{sid}: 已冻结，跳过")
            continue
        fn = {"GS-2022-08": fix_2022_08, "GS-2023-07": fix_2023_07, "GS-2023-08": fix_2023_08,
              "GS-2024-02": fix_2024_02, "GS-2024-05": fix_2024_05, "GS-2024-07": fix_2024_07,
              "GS-2024-08": fix_2024_08, "GS-2025-09": fix_2025_09}[sid]
        fn()
        print(f"{sid}: 已返工")
    return 0


if __name__ == "__main__":
    sys.exit(main())
