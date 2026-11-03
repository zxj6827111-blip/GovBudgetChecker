# -*- coding: utf-8 -*-
"""生成 B1 目标的初标文件（PENDING_REVIEW；未冻结）。

只覆盖目标检查；其余检查如实记 cannot_determine，留给独立复核补充。
"""
import json, os, sys
from datetime import datetime, timezone, timedelta

OUT = sys.argv[1]
CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST).isoformat(timespec="seconds")

CHECKS = ["GS-S01","GS-S02","GS-S03","GS-S04","GS-T01","GS-T02","GS-T03","GS-T04","GS-T05","GS-T06","GS-T07","GS-T08","GS-X01","GS-X02","GS-N01","GS-N02","GS-N03","GS-N04","GS-N05","GS-N06","GS-N07","GS-N08","GS-N09","GS-W01"]
SECTIONS = ["一、收入支出决算总体情况说明","二、收入决算情况说明","三、支出决算情况说明","四、财政拨款收入支出决算总体情况说明","五、一般公共预算财政拨款支出决算情况说明","六、一般公共预算财政拨款基本支出决算情况说明","七、财政拨款“三公”经费支出决算情况说明","八、政府性基金预算财政拨款收入支出决算情况说明","九、国有资本经营预算财政拨款收入支出决算情况说明"]
TABLES = ["收入支出决算总表","收入决算表","支出决算表","财政拨款收入支出决算总表","一般公共预算财政拨款支出决算表","一般公共预算财政拨款基本支出决算表","财政拨款“三公”经费支出决算表","政府性基金预算财政拨款收入支出决算表","国有资本经营预算财政拨款收入支出决算表"]

BASE = {
 "B1-2023-D01": {
   "org":"上海市交通委员会","cover_quote":"上海市交通委员会","cover_year_quote":"2023 年度部门决算","year":2023,"scope":"department","pages":40,
   "table_pages":[8,9,13,17,19,21,23,24,25],
   "section_pages":[26,26,26,26,26,34,34,36,36],
   "sha":"081cb59322f9cc37be02553b76cdac10890c56c43d996367533a125480bfdb98",
 },
 "B1-2025-D01": {
   "org":"上海市人力资源和社会保障局","cover_quote":"上海市人力资源和社会保","cover_year_quote":"2025 年度部门决算","year":2025,"scope":"department","pages":35,
   "table_pages":[8,9,11,14,16,18,20,21,22],
   "section_pages":[23,23,23,23,23,29,30,31,32],
   "sha":"0dc0ada1d69e14f2db6a23435be0dc86eb711c3c607c319138065a9c24a10e07",
 },
 "B1-2025-U01": {
   "org":"上海商业会计学校","cover_quote":"上海商业会计学校","cover_year_quote":"2025 年度决算","year":2025,"scope":"unit","pages":24,
   "table_pages":[5,6,7,9,11,12,14,15,16],
   "section_pages":[17,17,17,17,17,19,20,21,21],
   "sha":"0c2a165f93dcd6b09ebd752c06b058d5d1795fc62b7fa5c6693f29d64eac399c",
 },
 "B1-2025-U02": {
   "org":"上海航宇科普中心（上海市工业文化发展中心）","cover_quote":"上海航宇科普中心","cover_year_quote":"2025 年度决算","year":2025,"scope":"unit","pages":24,
   "table_pages":[5,6,8,10,12,13,14,15,16],
   "section_pages":[17,17,17,17,17,19,19,21,21],
   "sha":"51e1da9597488644e2802c2cd7c134150a74a884509d9042e6666a07349bb876",
 },
 "B1-2024-D02": {
   "org":"上海市民族和宗教事务局","cover_quote":"上海市民族和宗教事务局","cover_year_quote":"2024 年度部门决算","year":2024,"scope":"department","pages":30,
   "table_pages":[7,8,10,12,14,15,17,18,19],
   "section_pages":[20,20,20,20,20,24,24,26,26],
   "sha":"44a74e0562e97fbfcd553ded8ba02268c8524ca637cb988eab4b3fc3d9449444",
 },
}

def base_annotation(sid, meta):
    return {
        "sample_id": sid, "sha256": meta["sha"],
        "annotator": "deepseek-v4.1-flash via ZCode",
        "annotated_at": NOW,
        "basic": {
            "report_kind": "final", "material_scope": meta["scope"],
            "fiscal_year": meta["year"], "organization": meta["org"], "pages": meta["pages"],
            "source_kind": "real", "source_url": None, "published_at": None,
            "tables": [{"name": n, "page": p, "status": "data"} for n, p in zip(TABLES, meta["table_pages"])],
            "sections": [{"name": n, "page": p} for n, p in zip(SECTIONS, meta["section_pages"])],
        },
        "review_status": "PENDING_REVIEW",
        "review_note": "初标由实施方完成；尚未经独立复核/冻结，不得作为正式真值。非目标检查留 cannot_determine，待复核补充。",
        "checks": [], "free_findings": [],
    }

def cd(cid, reason="定向初标仅覆盖目标检查；本项待独立复核补充"):
    return {"check_id": cid, "verdict": "cannot_determine", "items": [], "reason": reason}

def main():
    anns = {sid: base_annotation(sid, m) for sid, m in BASE.items()}
    # S01/S02/S04：结构项按 TOC/封面确认
    for sid, a in anns.items():
        m = BASE[sid]
        s01 = {"check_id":"GS-S01","verdict":"pass","items":[{"label":"九张核心表齐全、表名与规范一致","verdict":"pass",
            "values":[{"name":"目录","page":2,"quote":"一、收入支出决算总表","unit":""}],
            "formula":"","note":"目录列九表，正文表页见 basic.tables。"}],"reason":""}
        s02 = {"check_id":"GS-S02","verdict":"pass","items":[{"label":"四个部分标题与九个说明章节","verdict":"pass",
            "values":[{"name":"目录","page":2,"quote":"第三部分","unit":""}],
            "formula":"","note":"第三部分及九个说明章节见 basic.sections。"}],"reason":""}
        s04 = {"check_id":"GS-S04","verdict":"pass","items":[{"label":"封面单位名称与年度","verdict":"pass",
            "values":[{"name":"封面单位名称","page":1,"quote":m["cover_quote"],"unit":""},{"name":"封面年度","page":1,"quote":m["cover_year_quote"],"unit":""}],
            "formula":"","note":f"封面为 {m['org']} {m['year']} 年度决算。"}],"reason":""}
        a["checks"] = [s01, s02, cd("GS-S03"), s04] + [cd(c) for c in CHECKS[4:]]
    # B1-2023-D01：非目标 C-02 真实层级缺陷，单列自由发现供 B2 使用
    anns["B1-2023-D01"]["free_findings"] = [{
        "id": "F01", "page": 11, "category": "其他",
        "quote": "286,236.11",
        "expected": "21401 公路水路运输 款级金额应等于所属项级之和 286,297.57（差 61.46）",
        "confidence": 0.95,
        "note": "对应清单 C-02，不属本轮 13 个 GS 目标；供 B2/C-02 使用。",
        "occurrences": [{"page": 11, "quote": "286,236.11"}]
    }]
    # 目标缺陷/边界
    # B1-2025-D01: T07 defect; T03 cannot_determine (borderline)
    a = anns["B1-2025-D01"]
    t07 = {"check_id":"GS-T07","verdict":"defect","items":[{"label":"三公经费决算数合计","verdict":"defect","values":[
        {"name":"合计","page":20,"quote":"808.46","unit":"万元"},
        {"name":"因公出国（境）费","page":20,"quote":"457.69","unit":"万元"},
        {"name":"公务用车购置及运行费","page":20,"quote":"328.11","unit":"万元"},
        {"name":"公务接待费","page":20,"quote":"22.71","unit":"万元"}],
        "formula":"457.69 + 328.11 + 22.71 = 808.51 ≠ 808.46，差 0.05",
        "note":"3 项加总，R1 包络 (3+1)×0.005=0.02；0.05 超出包络。"}],
        "reason":"三公表合计与三项之和不一致。"}
    t03 = {"check_id":"GS-T03","verdict":"cannot_determine","items":[{"label":"支出决算表合计=基本+项目","verdict":"cannot_determine","values":[
        {"name":"本年支出合计","page":11,"quote":"274,528.36","unit":"万元"},
        {"name":"基本支出","page":11,"quote":"106,641.23","unit":"万元"},
        {"name":"项目支出","page":11,"quote":"167,887.11","unit":"万元"}],
        "formula":"106,641.23 + 167,887.11 = 274,528.34 ≠ 274,528.36，差 0.02",
        "note":"2 项加总，R1 包络 0.015；0.02 刚超出，需复核确认是否为舍入/取整。"}],
        "reason":"差额 0.02 刚超出 2 项 R1 包络，证据两可，待复核。"}
    for c in a["checks"]:
        if c["check_id"] == "GS-T07": c.clear(); c.update(t07)
        if c["check_id"] == "GS-T03": c.clear(); c.update(t03)
    # B1-2025-U01: N02 defect
    a = anns["B1-2025-U01"]
    n02 = {"check_id":"GS-N02","verdict":"defect","items":[{"label":"收入决算说明财政拨款收入与表一致","verdict":"defect","values":[
        {"name":"表内财政拨款收入","page":6,"quote":"13,859.21","unit":"万元"},
        {"name":"说明财政拨款收入","page":17,"quote":"17976.00","unit":"万元"},
        {"name":"说明本年收入合计","page":17,"quote":"17975.85","unit":"万元"}],
        "formula":"说明 17,976.00 − 表内 13,859.21 = 4,116.79；17,976.00 接近本年收入合计 17,975.85，疑将总额误作财政拨款收入。",
        "note":"说明分项（财政拨款 17,976.00 + 事业 1,363.30 + 其他 2,753.34）合计 22,092.64 ≠ 本年收入合计 17,975.85。"}],
        "reason":"收入说明的财政拨款收入与收入决算表不一致。"}
    for c in a["checks"]:
        if c["check_id"] == "GS-N02": c.clear(); c.update(n02)
    # B1-2025-U02: T07 defect (预算数)
    a = anns["B1-2025-U02"]
    t07b = {"check_id":"GS-T07","verdict":"defect","items":[{"label":"三公经费预算数合计","verdict":"defect","values":[
        {"name":"预算合计","page":14,"quote":"7.40","unit":"万元"},
        {"name":"因公出国（境）费预算","page":14,"quote":"0.00","unit":"万元"},
        {"name":"公务用车购置及运行费预算小计","page":14,"quote":"7.40","unit":"万元"},
        {"name":"公务接待费预算","page":14,"quote":"1.00","unit":"万元"}],
        "formula":"0.00 + 7.40 + 1.00 = 8.40 ≠ 7.40，差 1.00",
        "note":"3 项加总，R1 包络 0.02；1.00 远超包络。决算数 1.70 = 0+1.70+0，成立。"}],
        "reason":"三公经费预算数合计与三项之和不一致。"}
    for c in a["checks"]:
        if c["check_id"] == "GS-T07": c.clear(); c.update(t07b)
    # B1-2024-D02: N06 defect
    a = anns["B1-2024-D02"]
    n06 = {"check_id":"GS-N06","verdict":"defect","items":[{"label":"基本支出说明与基本支出表一致","verdict":"defect","values":[
        {"name":"人员经费合计","page":16,"quote":"5,350.25","unit":"万元"},
        {"name":"公用经费合计","page":16,"quote":"1,395.40","unit":"万元"},
        {"name":"说明基本支出","page":24,"quote":"6,745.63","unit":"万元"}],
        "formula":"5,350.25 + 1,395.40 = 6,745.65 ≠ 6,745.63，差 0.02",
        "note":"表内人员+公用=6,745.65；说明写 6,745.63。差额 0.02 超过 0.01 容差。"}],
        "reason":"基本支出说明金额与基本支出表不一致。"}
    for c in a["checks"]:
        if c["check_id"] == "GS-N06": c.clear(); c.update(n06)

    for sid, a in anns.items():
        d = os.path.join(OUT, "annotations", sid); os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "annotation.json"), "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False, indent=2)
    print("wrote", len(anns))

if __name__ == "__main__":
    main()
