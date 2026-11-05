#!/usr/bin/env python3
"""B1 第二轮修订：在初标之后补来源/行列/表头，不伪装成新的盲标。

输入：outputs/gold_b1_20261103/run_20261103T150928/annotations（只读）
输出：revisions/round2_annotation_revision/
  - annotations_revised/            初标副本 + 精确来源值（新增项逐条登记）
  - revision_added_values.json      新增了哪些值、为什么
  - decimal_spec_v4.json            16 条 Decimal 的完整来源/公式/行列/单位
  - decimal_checks_v4.csv           用 round3 decimal_checks_v2.py 复算
  - decimal_evidence_audit.json     原件页码+quote+表头复核
  - closure_evidence.csv/json       205/20503/20508 与 T03/T06 闭合
  - target_status_revision.csv      T03/T06 判定依据修订
"""
from __future__ import annotations

import csv
import importlib.util
import json
import os
import re
from decimal import Decimal
from pathlib import Path

import pymupdf

RUN = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/gold_b1_20261103/run_20261103T150928")
OUT = Path(os.environ.get("B1_REVISION_DIR",
                          str(RUN / "revisions/round6_annotation_revision")))
ANNOTATIONS = RUN / "annotations"
CHECKER = Path(__file__).with_name("decimal_checks_v2.py")
PDFS = json.loads((RUN / "b1_pdfs.json").read_text(encoding="utf-8"))

#: T06 表5（基本支出决算表）打印合计与页；用于和打印的人员/公用合计闭合。
T06_TABLE5 = {
    "B1-2024-D02": {"total": "6745.63", "page": 14},
    "B1-2025-D01": {"total": "106625.87", "page": 17},
}
#: 打印的人员经费合计/公用经费合计所在页。
T06_PRINTED_PAGES = {"B1-2024-D02": 16, "B1-2025-D01": 19}
PRINTED_TOTAL_RE = re.compile(
    r"人员经费合计\s*([\d,]+\.\d{2})\s*公用经费合计\s*([\d,]+\.\d{2})")

#: 初标里有整句 quote，但 Decimal 需要“单一数值+精确单位”的来源；这里只
#: 增加原文中真实存在的精确片段，不修改任何原有数值/判定。
ADDED_VALUES = {
    ("B1-2025-U01", "GS-N02"): [
        {"name": "说明-事业收入（精确片段）", "page": 17, "quote": "事业收入1363.30 万元", "unit": "万元"},
        {"name": "说明-其他收入（精确片段）", "page": 17, "quote": "2753.34 万元", "unit": "万元"},
    ],
    ("B1-2025-U01", "GS-N05"): [
        {"name": "2、社保支出年初预算（精确片段）", "page": 19, "quote": "3064.88 万元", "unit": "万元"},
        {"name": "3、卫生支出年初预算（精确片段）", "page": 19, "quote": "795.63 万元", "unit": "万元"},
        {"name": "年初预算总额（精确片段）", "page": 18, "quote": "年初预算为13,995.10 万元", "unit": "万元"},
    ],
    ("B1-2025-U01", "GS-N07"): [
        {"name": "公务用车费占比（精确片段）", "page": 21, "quote": "占97.11%", "unit": "%"},
        {"name": "出国费占比（精确片段）", "page": 21, "quote": "占0.00%", "unit": "%"},
        {"name": "接待费占比（精确片段）", "page": 21, "quote": "公务接待费支出决算0.00 万元，占0.00%", "unit": "%"},
    ],
}


def _load_checker():
    spec = importlib.util.spec_from_file_location("decimal_checks_v4", CHECKER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_annotations() -> None:
    added_log = []
    for path in sorted(ANNOTATIONS.glob("*/annotation.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        sid = payload["sample_id"]
        for check in payload.get("checks") or []:
            extra = ADDED_VALUES.get((sid, check.get("check_id")))
            if not extra:
                continue
            item = (check.get("items") or [{}])[0]
            values = item.setdefault("values", [])
            for value in extra:
                if any(v.get("page") == value["page"] and str(v.get("quote")) == value["quote"]
                       for v in values):
                    continue
                values.append(dict(value))
                added_log.append({"sample_id": sid, "check_id": check["check_id"],
                                  "value": value,
                                  "reason": "初标整句 quote 含多个数值；补原文中真实存在的精确片段，供单位/数值唯一绑定"})
        # 修订裁决覆盖层：T03 在修订版中与台账 PENDING_REVIEW 一致；
        # 原初标 verdict=pass 保留在 annotations/，不覆盖。
        if sid == "B1-2023-D01":
            for check in payload.get("checks") or []:
                if check.get("check_id") != "GS-T03":
                    continue
                payload.setdefault("revision_notes", {})["GS-T03"] = {
                    "original_verdict": check.get("verdict"),
                    "revised_verdict": "pending_review",
                    "reason": "p13 支出表 20503 款级 0.02 超款级 0.015 包络；完整子链可解释但需独立裁定。20508 已用 p13 实际三列 2,022.67/1,450.20/572.47 补证。",
                }
                check["verdict"] = "pending_review"
                check["revision_note"] = payload["revision_notes"]["GS-T03"]["reason"]
        dest = OUT / "annotations_revised" / sid / "annotation.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (OUT / "revision_added_values.json").write_text(
        json.dumps({"note": "初标后修订；只增加精确来源片段，不改原有数值、verdict、formula。",
                    "added": added_log}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")


def operand(value, page, quote, unit, row, column, table, unit_source=None):
    record = {"value": value, "page": page, "quote": quote, "unit": unit,
              "row": row, "column": column, "table": table}
    if unit_source:
        record["unit_source"] = unit_source
    return record


def build_spec() -> dict:
    header_wan = {"page": None, "quote": "单位：万元", "kind": "table_header"}
    rows = [
        {
            "sample_id": "B1-2025-U02", "check_id": "GS-T07", "item": "预算数合计",
            "kind": "amount", "n": 3,
            "operands": [
                operand("0.00", 14, "0.00", "万元", "因公出国（境）费 预算数", "预算数", "一般公共预算财政拨款“三公”经费支出决算表"),
                operand("7.40", 14, "7.40", "万元", "公务用车购置及运行维护费 小计 预算数", "预算数", "一般公共预算财政拨款“三公”经费支出决算表"),
                operand("1.00", 14, "1.00", "万元", "公务接待费 预算数", "预算数", "一般公共预算财政拨款“三公”经费支出决算表"),
            ],
            "expected": "8.40", "expected_unit": "万元",
            "reported": "7.40", "reported_unit": "万元",
            "reported_source": {"page": 14, "quote": "7.40", "unit": "万元",
                                "row": "合计 预算数", "column": "预算数",
                                "table": "一般公共预算财政拨款“三公”经费支出决算表"},
            "formula": "0.00 + 7.40 + 1.00 = 8.40；表列合计 7.40；差 1.00",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2025-U02", "check_id": "GS-T07", "item": "预算数车小计",
            "kind": "amount", "n": 2,
            "operands": [
                operand("0.00", 14, "0.00", "万元", "公务用车购置费 预算数", "预算数", "一般公共预算财政拨款“三公”经费支出决算表"),
                operand("6.40", 14, "6.40", "万元", "公务用车运行维护费 预算数", "预算数", "一般公共预算财政拨款“三公”经费支出决算表"),
            ],
            "expected": "6.40", "expected_unit": "万元",
            "reported": "7.40", "reported_unit": "万元",
            "reported_source": {"page": 14, "quote": "7.40", "unit": "万元",
                                "row": "小计 预算数", "column": "预算数",
                                "table": "一般公共预算财政拨款“三公”经费支出决算表"},
            "formula": "0.00 + 6.40 = 6.40；表列小计 7.40；差 1.00",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2025-D01", "check_id": "GS-T07", "item": "决算数合计",
            "kind": "amount", "n": 3,
            "operands": [
                operand("457.69", 20, "457.69", "万元", "因公出国（境）费决算数", "决算数", "一般公共预算财政拨款“三公”经费支出决算表"),
                operand("328.11", 20, "328.11", "万元", "公务用车购置及运行费决算数", "决算数", "一般公共预算财政拨款“三公”经费支出决算表"),
                operand("22.71", 20, "22.71", "万元", "公务接待费决算数", "决算数", "一般公共预算财政拨款“三公”经费支出决算表"),
            ],
            "expected": "808.51", "expected_unit": "万元",
            "reported": "808.46", "reported_unit": "万元",
            "reported_source": {"page": 20, "quote": "808.46", "unit": "万元",
                                "row": "合计决算数", "column": "决算数",
                                "table": "一般公共预算财政拨款“三公”经费支出决算表"},
            "formula": "457.69 + 328.11 + 22.71 = 808.51；表列合计 808.46；差 0.05",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2024-D02", "check_id": "GS-N06", "item": "人员+公用 vs说明",
            "kind": "amount", "n": 2,
            "operands": [
                operand("5350.25", 16, "5,350.25", "万元", "人员经费合计", "决算数",
                        "一般公共预算财政拨款基本支出决算表",
                        {"page": 15, "quote": "单位：万元", "kind": "table_header"}),
                operand("1395.40", 16, "1,395.40", "万元", "公用经费合计", "决算数",
                        "一般公共预算财政拨款基本支出决算表",
                        {"page": 15, "quote": "单位：万元", "kind": "table_header"}),
            ],
            "expected": "6745.65", "expected_unit": "万元",
            "reported": "6745.63", "reported_unit": "万元",
            "reported_source": {"page": 24, "quote": "6,745.63", "unit": "万元",
                                "row": "说明·基本支出", "column": "正文", "table": "六、一般公共预算财政拨款基本支出决算情况说明"},
            "formula": "5,350.25 + 1,395.40 = 6,745.65；说明 6,745.63；差 0.02 > 多项包络 0.015（基础容差 0.01）",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2025-U01", "check_id": "GS-N02", "item": "财政拨款收入表vs说明",
            "kind": "amount", "n": 1,
            "operands": [
                operand("13859.21", 6, "13,859.21", "万元", "收入决算表-财政拨款收入", "决算数",
                        "收入决算表", {"page": 6, "quote": "金额单位：万元", "kind": "table_header"}),
            ],
            "expected": "17976.00", "expected_unit": "万元",
            "reported": "17976.00", "reported_unit": "万元",
            "reported_source": {"page": 17, "quote": "财政拨款收入17976.00", "unit": "万元",
                                "row": "说明-财政拨款收入", "column": "正文", "table": "二、收入决算情况说明"},
            "formula": "表内财政拨款收入 13,859.21 ≠ 说明财政拨款收入 17,976.00（不是同一等式）",
            "applicable": False,
            "applicability_note": "表内 13,859.21 与说明 17,976.00 是不同口径，不能拼成一个加总等式；本行不用于 PASS/FAIL，N02 的真实缺陷由“说明分项合计vs本年收入”判定。",
        },
        {
            "sample_id": "B1-2025-U01", "check_id": "GS-N02", "item": "说明分项合计vs本年收入",
            "kind": "amount", "n": 3,
            "operands": [
                operand("17976.00", 17, "财政拨款收入17976.00", "万元", "说明-财政拨款收入", "正文", "二、收入决算情况说明"),
                operand("1363.30", 17, "事业收入1363.30 万元", "万元", "说明-事业收入", "正文", "二、收入决算情况说明"),
                operand("2753.34", 17, "2753.34 万元", "万元", "说明-其他收入", "正文", "二、收入决算情况说明"),
            ],
            "expected": "22092.64", "expected_unit": "万元",
            "reported": "17975.85", "reported_unit": "万元",
            "reported_source": {"page": 6, "quote": "17,975.85", "unit": "万元",
                                "row": "收入决算表-合计", "column": "决算数", "table": "收入决算表"},
            "formula": "17,976.00 + 1,363.30 + 2,753.34 = 22,092.64；收入决算表合计 17,975.85；差 4,116.79",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2025-U01", "check_id": "GS-N05", "item": "逐项年初预算合计",
            "kind": "amount", "n": 4,
            "operands": [
                operand("9577.58", 18, "于人员、公用、项目的中专教育运行支出。年初预算为9577.58", "万元", "1、教育支出年初预算", "正文", "五、一般公共预算财政拨款支出决算情况说明"),
                operand("3064.88", 19, "3064.88 万元", "万元", "2、社保支出年初预算", "正文", "五、一般公共预算财政拨款支出决算情况说明"),
                operand("795.63", 19, "795.63 万元", "万元", "3、卫生支出年初预算", "正文", "五、一般公共预算财政拨款支出决算情况说明"),
                operand("556.94", 19, "主要用于单位缴纳在职职工住房公积金。年初预算为556.94 万", "万元", "4、住房支出年初预算", "正文", "五、一般公共预算财政拨款支出决算情况说明"),
            ],
            "expected": "13995.03", "expected_unit": "万元",
            "reported": "13995.10", "reported_unit": "万元",
            "reported_source": {"page": 18, "quote": "年初预算为13,995.10 万元", "unit": "万元",
                                "row": "年初预算总额", "column": "正文", "table": "五、一般公共预算财政拨款支出决算情况说明"},
            "formula": "9,577.58 + 3,064.88 + 795.63 + 556.94 = 13,995.03；说明总额 13,995.10；差 0.07",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2025-U01", "check_id": "GS-N07", "item": "三公构成占比",
            "kind": "percent", "n": 3,
            "operands": [
                operand("0.00", 21, "占0.00%", "%", "出国费占比", "正文", "七、财政拨款“三公”经费支出决算情况说明"),
                operand("97.11", 21, "占97.11%", "%", "公务用车费占比", "正文", "七、财政拨款“三公”经费支出决算情况说明"),
                {**operand("0.00", 21, "公务接待费支出决算0.00 万元，占0.00%", "%", "接待费占比", "正文", "七、财政拨款“三公”经费支出决算情况说明"), "quote_value_selector": "占"},
            ],
            "expected": "97.11", "expected_unit": "%",
            "reported": "100.00", "reported_unit": "%",
            "reported_source": {"source_kind": "rule_constant",
                                "rule_constant_id": "percent_sum_100", "unit": "%"},
            "formula": "0.00 + 97.11 + 0.00 = 97.11；应有合计 100.00；差 2.89 个百分点。说明：97.11 是 4.37/4.50 的完成预算比例，不是三公内部结构占比。",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2025-D01", "check_id": "GS-N07", "item": "出国费表vs说明",
            "kind": "amount", "n": 1,
            "operands": [
                operand("457.66", 30, "457.66 万元", "万元", "说明金额（总体）", "正文", "七、财政拨款“三公”经费支出决算情况说明"),
            ],
            "expected": "457.66", "expected_unit": "万元",
            "reported": "457.69", "reported_unit": "万元",
            "reported_source": {"page": 20, "quote": "457.69", "unit": "万元",
                                "row": "表7 决算数", "column": "决算数", "table": "财政拨款“三公”经费支出决算表"},
            "formula": "说明 457.66 vs 表7 457.69；差 0.03 > 0.01",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2023-D01", "check_id": "GS-T02", "item": "2140101行政运行",
            "kind": "amount", "n": 2,
            "operands": [
                operand("29663.18", 11, "29,663.18", "万元", "2140101 财政拨款收入", "财政拨款收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
                operand("1451.96", 11, "1,451.96", "万元", "2140101 其他收入", "其他收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
            ],
            "expected": "31115.14", "expected_unit": "万元",
            "reported": "29663.18", "reported_unit": "万元",
            "reported_source": {"page": 11, "quote": "29,663.18", "unit": "万元",
                                "row": "2140101 合计", "column": "本年收入合计", "table": "收入决算表"},
            "formula": "29,663.18 + 1,451.96 = 31,115.14 ≠ 表列合计 29,663.18；差 1,451.96",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2023-D01", "check_id": "GS-T02", "item": "2140106公路养护",
            "kind": "amount", "n": 2,
            "operands": [
                operand("182493.28", 11, "182,493.28", "万元", "2140106 财政拨款收入", "财政拨款收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
                operand("871.01", 11, "871.01", "万元", "2140106 其他收入", "其他收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
            ],
            "expected": "183364.29", "expected_unit": "万元",
            "reported": "182493.28", "reported_unit": "万元",
            "reported_source": {"page": 11, "quote": "182,493.28", "unit": "万元",
                                "row": "2140106 合计", "column": "本年收入合计", "table": "收入决算表"},
            "formula": "182,493.28 + 871.01 = 183,364.29 ≠ 表列合计 182,493.28；差 871.01",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2023-D01", "check_id": "GS-T02", "item": "2140123航道维护",
            "kind": "amount", "n": 2,
            "operands": [
                operand("30802.54", 11, "30,802.54", "万元", "2140123 财政拨款收入", "财政拨款收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
                operand("79.96", 11, "79.96", "万元", "2140123 其他收入", "其他收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
            ],
            "expected": "30882.50", "expected_unit": "万元",
            "reported": "30802.54", "reported_unit": "万元",
            "reported_source": {"page": 11, "quote": "30,802.54", "unit": "万元",
                                "row": "2140123 合计", "column": "本年收入合计", "table": "收入决算表"},
            "formula": "30,802.54 + 79.96 = 30,882.50 ≠ 表列合计 30,802.54；差 79.96",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2023-D01", "check_id": "GS-T02", "item": "21462车辆通行费",
            "kind": "amount", "n": 2,
            "operands": [
                operand("60953.45", 11, "60,953.45", "万元", "21462 财政拨款收入", "财政拨款收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
                operand("498.76", 11, "498.76", "万元", "21462 其他收入", "其他收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
            ],
            "expected": "61452.21", "expected_unit": "万元",
            "reported": "60953.45", "reported_unit": "万元",
            "reported_source": {"page": 11, "quote": "60,953.45", "unit": "万元",
                                "row": "21462 合计", "column": "本年收入合计", "table": "收入决算表"},
            "formula": "60,953.45 + 498.76 = 61,452.21 ≠ 表列合计 60,953.45；差 498.76",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2023-D01", "check_id": "GS-T02", "item": "2080505养老缴费",
            "kind": "amount", "n": 3,
            "operands": [
                operand("7874.31", 10, "7,874.31", "万元", "2080505 财政拨款收入", "财政拨款收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
                operand("145.47", 10, "145.47", "万元", "2080505 事业收入", "事业收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
                operand("29.20", 10, "29.20", "万元", "2080505 其他收入", "其他收入",
                        "收入决算表", {"page": 9, "quote": "单位：万元", "kind": "table_header"}),
            ],
            "expected": "8048.98", "expected_unit": "万元",
            "reported": "8019.78", "reported_unit": "万元",
            "reported_source": {"page": 10, "quote": "8,019.78", "unit": "万元",
                                "row": "2080505 合计", "column": "本年收入合计", "table": "收入决算表"},
            "formula": "7,874.31 + 145.47 + 29.20 = 8,048.98 ≠ 表列合计 8,019.78；差 29.20",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2023-D01", "check_id": "GS-T05", "item": "2210201合计=基本+项目",
            "kind": "amount", "n": 2,
            "operands": [
                operand("5697.24", 20, "5,697.24", "万元", "2210201 基本支出", "基本支出",
                        "一般公共预算财政拨款支出决算表", {"page": 19, "quote": "单位：万元", "kind": "table_header"}),
                operand("5697.24", 20, "5,697.24", "万元", "2210201 项目支出", "项目支出",
                        "一般公共预算财政拨款支出决算表", {"page": 19, "quote": "单位：万元", "kind": "table_header"}),
            ],
            "expected": "11394.48", "expected_unit": "万元",
            "reported": "5697.24", "reported_unit": "万元",
            "reported_source": {"page": 20, "quote": "5,697.24", "unit": "万元",
                                "row": "2210201 合计", "column": "合计", "table": "一般公共预算财政拨款支出决算表"},
            "formula": "5,697.24 + 5,697.24 = 11,394.48 ≠ 表列合计 5,697.24；差 5,697.24",
            "applicable": True, "applicability_note": "",
        },
        {
            "sample_id": "B1-2023-D01", "check_id": "GS-N05", "item": "第8项表vs说明",
            "kind": "amount", "n": 1,
            "operands": [
                operand("4573.86", 19, "4,573.86", "万元", "2080502 合计", "合计",
                        "一般公共预算财政拨款支出决算表", {"page": 19, "quote": "单位：万元", "kind": "table_header"}),
            ],
            "expected": "4573.86", "expected_unit": "万元",
            "reported": "4573.66", "reported_unit": "万元",
            "reported_source": {"page": 29, "quote": "支出决算为4,573.66 万元", "unit": "万元",
                                "row": "8 事业单位离退休 说明", "column": "正文",
                                "table": "五、一般公共预算财政拨款支出决算情况说明"},
            "formula": "表 2080502 合计 4,573.86（基本 4,573.66 + 项目 0.20） vs 说明 4,573.66；差 0.20 > 0.01",
            "applicable": True, "applicability_note": "",
        },
    ]
    return {"version": "b1-decimal-v4-revision-20261104", "rows": rows}


def verify_pdf_quotes(spec: dict) -> dict:
    """逐条核对来源页码+quote、表头 quote 是否真实存在于原件。"""
    cache = {}
    results = []
    for row in spec["rows"]:
        sid = row["sample_id"]
        if sid not in cache:
            cache[sid] = pymupdf.open(PDFS[sid])
        doc = cache[sid]
        for index, op in enumerate(row.get("operands") or []):
            page = op.get("page")
            quote = op.get("quote") or ""
            ok = False
            if isinstance(page, int) and quote:
                text = re.sub(r"\s+", "", doc[page - 1].get_text())
                ok = re.sub(r"\s+", "", quote) in text
            header = op.get("unit_source") or {}
            header_ok = None
            if header.get("page") and header.get("quote"):
                text = re.sub(r"\s+", "", doc[header["page"] - 1].get_text())
                header_ok = re.sub(r"\s+", "", header["quote"]) in text
            results.append({"sample_id": sid, "check_id": row["check_id"], "item": row["item"],
                            "kind": "operand", "index": index, "page": page, "quote": quote,
                            "quote_found": ok, "unit_source": header or None,
                            "unit_source_found": header_ok})
        source = row.get("reported_source") or {}
        if source.get("source_kind") != "rule_constant":
            page, quote = source.get("page"), source.get("quote") or ""
            ok = False
            if isinstance(page, int) and quote:
                text = re.sub(r"\s+", "", doc[page - 1].get_text())
                ok = re.sub(r"\s+", "", quote) in text
            results.append({"sample_id": sid, "check_id": row["check_id"], "item": row["item"],
                            "kind": "reported", "page": page, "quote": quote,
                            "quote_found": ok, "unit_source": None, "unit_source_found": None})
    return {"total": len(results), "all_found": all(item["quote_found"] for item in results),
            "header_missing": [item for item in results if item["unit_source_found"] is False],
            "items": results}


T06_LEAF_RE = re.compile(r"(\d{3})\s*(\d{2})\s+([^\d]+?)\s+([\d,]+\.\d{2})")


def parse_printed_totals() -> dict:
    """从原件读打印的人员经费合计/公用经费合计，作为独立证据保存。"""
    printed: dict = {}
    for sid, page in T06_PRINTED_PAGES.items():
        doc = pymupdf.open(PDFS[sid])
        text = re.sub(r"\s+", " ", doc[page - 1].get_text())
        match = PRINTED_TOTAL_RE.search(text)
        if match is None:
            raise SystemExit(f"未能在 {sid} p{page} 读到打印的人员/公用经费合计")
        printed[sid] = {
            "page": page,
            "人员经费合计": match.group(1).replace(",", ""),
            "公用经费合计": match.group(2).replace(",", ""),
        }
    return printed


def build_t06_items() -> dict:
    """从基本支出表原页解析 T06 非零末级项（真实编码/名称/页/列/数值）。"""
    printed = parse_printed_totals()
    data: dict = {}
    for sid, pages in (("B1-2024-D02", [15, 16]), ("B1-2025-D01", [18, 19])):
        doc = pymupdf.open(PDFS[sid])
        items = []
        for page in pages:
            text = re.sub(r"\s+", " ", doc[page - 1].get_text())
            for match in T06_LEAF_RE.finditer(text):
                code3, code2, name, value = match.groups()
                if "合计" in name:
                    continue
                code = f"{code3}{code2}"
                if any(existing["code"] == code and existing["page"] == page
                       for existing in items):
                    continue
                items.append({
                    "code": code,
                    "name": name.strip(),
                    "page": page,
                    "column": "人员经费" if code3 in {"301", "303"} else "公用经费",
                    "category": code3,
                    "value": value.replace(",", ""),
                })
        nonzero = [item for item in items if Decimal(item["value"]) != 0]
        data[sid] = {"printed_count": len(items), "nonzero_count": len(nonzero)}
        # R4-03：printed_total 与 leaf_sum 分开存，不能用叶项和替换原件合计。
        data[sid]["printed"] = printed[sid]
        for column in ("人员经费", "公用经费"):
            rows = [item for item in nonzero if item["column"] == column]
            data[sid][column] = {
                "count": len(rows),
                "sum": str(sum((Decimal(item["value"]) for item in rows), Decimal("0"))),
                "items": rows,
            }
    (OUT / "t06_items.json").parent.mkdir(parents=True, exist_ok=True)
    (OUT / "t06_items.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return data


def build_identity_audit(spec: dict, annotations: dict, checker) -> dict:
    """逐条记录来源身份：声明行列/表、同页同数候选、核验结果与原因。

    R4-02：只引用 annotation 里已有的 name/label/page/quote，不补造行列元数据。
    """
    entries = []
    for row in spec["rows"]:
        payload = annotations.get(row["sample_id"]) or {}
        values = checker._annotation_values(payload).get(row["check_id"], [])
        sources = [("operand", index, op) for index, op in enumerate(row.get("operands") or [])]
        if row.get("reported_source") is not None:
            sources.append(("reported", None, row.get("reported_source")))
        for role, index, source in sources:
            if not isinstance(source, dict) or source.get("source_kind") == "rule_constant":
                entries.append({
                    "sample_id": row["sample_id"], "check_id": row["check_id"],
                    "item": row.get("item"), "role": role, "index": index,
                    "source_kind": (source or {}).get("source_kind") or "material",
                    "declared": {}, "candidates": [], "source_ok": True,
                    "reason": "规则常量白名单来源",
                })
                continue
            expected = checker._dec(
                row.get("reported") if role == "reported" else source.get("value"))
            ok, unit, reason = checker._match_source(
                source, expected, values, str(source.get("unit") or ""),
                role=role, kind=row.get("kind") or "amount")
            candidates = [{
                "name": value.get("name"), "page": value.get("page"),
                "quote": value.get("quote"), "unit": value.get("unit"),
                "item_label": value.get("_item_label"),
            } for value in values
                if value.get("page") == source.get("page")
                and str(value.get("quote") or "") == str(source.get("quote") or "")]
            entries.append({
                "sample_id": row["sample_id"], "check_id": row["check_id"],
                "item": row.get("item"), "role": role, "index": index,
                "source_kind": "material",
                "declared": {key: source.get(key) for key in
                             ("page", "quote", "unit", "row", "column", "table")},
                "candidates": candidates,
                "source_ok": bool(ok), "matched_unit": unit, "reason": reason,
            })
    report = {
        "total": len(entries),
        "material_sources": sum(1 for entry in entries if entry["source_kind"] == "material"),
        "rule_constant_sources": sum(1 for entry in entries if entry["source_kind"] != "material"),
        "all_source_ok": all(entry["source_ok"] for entry in entries),
        "note": "身份只用 anchor 的 name/label 与来源声明的 row/column/table 核验；"
                "编码必须在 value 的 name/label 中出现，空名称/未知列不放行。",
        "entries": entries,
    }
    (OUT / "decimal_identity_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return report


def _rounding_verdict(part: Decimal, total: Decimal, count: int) -> dict:
    """单项 0.01；多项 (n+1)*0.005；返回该链路自己的容差与判定。"""
    difference = abs(part - total)
    tolerance = Decimal("0.01") if count <= 1 else (Decimal(count) + 1) * Decimal("0.005")
    if difference <= tolerance:
        return {"difference": str(difference), "tolerance": str(tolerance),
                "within_tolerance": True,
                "verdict": "PASS" if difference <= Decimal("0.01") else "PASS_ROUNDING"}
    return {"difference": str(difference), "tolerance": str(tolerance),
            "within_tolerance": False, "verdict": "PENDING_REVIEW"}


def t06_closure_chains(t06: dict, printed: dict | None = None) -> list:
    """T06 三条闭合链路：叶项→公用合计、打印人员+公用→表5、全叶项→表5。

    每条链路只用自己的实际操作数、差额和冻结包络判定；打印合计与叶项和
    分别保存，不得互相替换。
    """
    printed = printed or parse_printed_totals()
    chains = []
    for sid in sorted(t06):
        data, totals = t06[sid], printed[sid]
        table5 = Decimal(T06_TABLE5[sid]["total"])
        people_printed = Decimal(totals["人员经费合计"])
        public_printed = Decimal(totals["公用经费合计"])
        people_leaf = Decimal(data["人员经费"]["sum"])
        public_leaf = Decimal(data["公用经费"]["sum"])
        leaf_total = people_leaf + public_leaf
        printed_total = people_printed + public_printed
        base = {"sample_id": sid, "target": "T06", "table": "一般公共预算财政拨款基本支出决算表"}

        verdict = _rounding_verdict(public_leaf, public_printed, data["公用经费"]["count"])
        chains.append({
            **base, "page": totals["page"], "level": "叶项→公用合计",
            "code": "leaf_pub_vs_printed",
            "total": str(public_printed), "part1_name": "公用经费非零末级项求和",
            "part1": str(public_leaf), "part2_name": "差额(叶项-打印)", "part2": verdict["difference"],
            "n_leaves": data["公用经费"]["count"],
            "operand_count": data["公用经费"]["count"],
            "tolerance": verdict["tolerance"], "within_tolerance": verdict["within_tolerance"],
            "formula": f"{public_leaf} - {public_printed} = {verdict['difference']}；"
                       f"{data['公用经费']['count']} 个非零末级项包络 {verdict['tolerance']}",
            "verdict": verdict["verdict"],
            "explanation": "公用经费叶项求和与原件打印合计分开核对；"
                           "差额由末级项显示舍入解释，不能用叶项和替换打印合计。",
        })

        verdict = _rounding_verdict(printed_total, table5, 2)
        chains.append({
            **base, "page": T06_TABLE5[sid]["page"], "level": "打印人员+公用→表5",
            "code": "printed_sum_vs_table5",
            "total": str(table5), "part1_name": "打印人员经费合计",
            "part1": str(people_printed), "part2_name": "打印公用经费合计",
            "part2": str(public_printed), "n_leaves": 0, "operand_count": 2,
            "tolerance": verdict["tolerance"], "within_tolerance": verdict["within_tolerance"],
            "formula": f"{people_printed} + {public_printed} = {printed_total}；"
                       f"表5 {table5}；差 {verdict['difference']}；两项打印合计包络 {verdict['tolerance']}",
            "verdict": verdict["verdict"],
            "explanation": "两项打印合计相加减表5：0.02 超过两项包络 0.015，"
                           "不能单独据此写 PASS；是否舍入差由全叶项链和冻结口径裁定，"
                           "此链路保留为待复核，不用派生值替换打印数。",
        })

        verdict = _rounding_verdict(leaf_total, table5, data["nonzero_count"])
        chains.append({
            **base, "page": T06_TABLE5[sid]["page"], "level": "全叶项→表5",
            "code": "leaf_total_vs_table5",
            "total": str(table5), "part1_name": "全部非零末级项求和",
            "part1": str(leaf_total), "part2_name": "差额(叶项-表5)", "part2": verdict["difference"],
            "n_leaves": data["nonzero_count"], "operand_count": data["nonzero_count"],
            "tolerance": verdict["tolerance"], "within_tolerance": verdict["within_tolerance"],
            "formula": f"{leaf_total} - {table5} = {verdict['difference']}；"
                       f"{data['nonzero_count']} 个非零末级项包络 {verdict['tolerance']}",
            "verdict": verdict["verdict"],
            "explanation": "全末级项直接对表5；这条链路是冻结口径里舍入结论的依据，"
                           "与打印合计链路分开记录。",
        })
    return chains


def build_closure_evidence() -> dict:
    """205/20503/20508 与 T03/T06 的完整闭合关系。"""
    t06 = build_t06_items()
    relations = [
        {"sample_id": "B1-2023-D01", "target": "T03", "page": 13, "level": "类", "code": "205",
         "table": "支出决算表",
         "total": "31118.07", "part1_name": "基本支出", "part1": "20834.59",
         "part2_name": "项目支出", "part2": "10283.50",
         "leaf_items": ["2050302", "2050305", "2050802"], "n_leaves": 3,
         "formula": "20,834.59 + 10,283.50 = 31,118.09；表列 31,118.07；差 0.02",
         "verdict": "PASS_ROUNDING",
         "explanation": "p13 支出表：20503（29,095.40/19,384.39/9,711.03）+ 20508（2,022.67/1,450.20/572.47）= 205（31,118.07/20,834.59/10,283.50），款级相加逐列精确；0.02 只发生在 20503 内部，由 2050302、2050305 各 0.01 叠加。按 3 个末级项包络 (3+1)×0.005=0.02，205 类差 0.02 在边界内。"},
        {"sample_id": "B1-2023-D01", "target": "T03", "page": 13, "level": "款", "code": "20503",
         "total": "29095.40", "part1_name": "基本支出", "part1": "19384.39",
         "part2_name": "项目支出", "part2": "9711.03",
         "leaf_items": ["2050302", "2050305"], "n_leaves": 2,
         "formula": "19,384.39 + 9,711.03 = 29,095.42；表列 29,095.40；差 0.02",
         "verdict": "PENDING_REVIEW",
         "explanation": "差额 0.02 等于 2050302、2050305 两个末级项各 0.01 的和；两个末级项各自在 (2+1)×0.005=0.015 内，但款级自身包络 (2+1)×0.005=0.015，0.02 超 0.005。按完整子链可解释，但不能写成 PASS；列待裁定。"},
        {"sample_id": "B1-2023-D01", "target": "T03", "page": 13, "level": "项", "code": "2050302",
         "total": "2396.69", "part1_name": "基本支出", "part1": "906.09",
         "part2_name": "项目支出", "part2": "1490.61",
         "leaf_items": ["2050302"], "n_leaves": 1,
         "formula": "906.09 + 1,490.61 = 2,396.70；表列 2,396.69；差 0.01",
         "verdict": "PASS_ROUNDING", "explanation": "单项比较，0.01 在 0.015 包络内。"},
        {"sample_id": "B1-2023-D01", "target": "T03", "page": 13, "level": "项", "code": "2050305",
         "table": "支出决算表",
         "total": "26698.71", "part1_name": "基本支出", "part1": "18478.30",
         "part2_name": "项目支出", "part2": "8220.42",
         "leaf_items": ["2050305"], "n_leaves": 1,
         "formula": "18,478.30 + 8,220.42 = 26,698.72；表列 26,698.71；差 0.01",
         "verdict": "PASS_ROUNDING", "explanation": "单项比较，0.01 在 0.015 包络内。"},
        {"sample_id": "B1-2023-D01", "target": "T03", "page": 13, "level": "款", "code": "20508",
         "table": "支出决算表",
         "total": "2022.67", "part1_name": "基本支出", "part1": "1450.20",
         "part2_name": "项目支出", "part2": "572.47",
         "leaf_items": ["2050802"], "n_leaves": 1,
         "formula": "1,450.20 + 572.47 = 2,022.67 = 表列 2,022.67；差 0.00",
         "verdict": "PASS", "explanation": "p13 支出表 20508 款：基本+项目精确等于合计 2,022.67；这是 T03 在同一张 p13 支出表上的 20508 三列，不是 p9 收入表或 p19 财政拨款支出。"},
        {"sample_id": "B1-2023-D01", "target": "T03", "page": 13, "level": "项", "code": "2050802",
         "table": "支出决算表",
         "total": "2022.67", "part1_name": "基本支出", "part1": "1450.20",
         "part2_name": "项目支出", "part2": "572.47",
         "leaf_items": ["2050802"], "n_leaves": 1,
         "formula": "1,450.20 + 572.47 = 2,022.67 = 表列 2,022.67；差 0.00",
         "verdict": "PASS", "explanation": "p13 支出表 2050802 项与 20508 款三列一致。"},
        {"sample_id": "B1-2023-D01", "target": "T02", "page": 9, "level": "类", "code": "205",
         "total": "29065.36", "part1_name": "财政拨款收入", "part1": "20234.93",
         "part2_name": "事业收入+其他收入", "part2": "4022.42+4808.01",
         "leaf_items": ["2050302", "2050305", "2050802"], "n_leaves": 3,
         "formula": "20,234.93 + 4,022.42 + 4,808.01 = 29,065.36 = 表列 205 合计",
         "verdict": "PASS", "explanation": "收入表行来源和成立。"},
        {"sample_id": "B1-2023-D01", "target": "T02", "page": 9, "level": "款", "code": "20503",
         "total": "27046.54", "part1_name": "财政拨款收入", "part1": "18242.67",
         "part2_name": "事业收入+其他收入", "part2": "4022.42+4781.46",
         "leaf_items": ["2050302", "2050305"], "n_leaves": 2,
         "formula": "18,242.67 + 4,022.42 + 4,781.46 = 27,046.55；表列 27,046.54；差 0.01",
         "verdict": "PASS_ROUNDING", "explanation": "三项加总包络 (3+1)×0.005=0.02，差 0.01 在内。"},
        {"sample_id": "B1-2023-D01", "target": "T02", "page": 9, "level": "款", "code": "20508",
         "total": "2018.81", "part1_name": "财政拨款收入", "part1": "2018.81",
         "part2_name": "其他收入", "part2": "0.00",
         "leaf_items": ["2050802"], "n_leaves": 1,
         "formula": "2,018.81 + 0.00 = 2,018.81；相符",
         "verdict": "PASS", "explanation": "20508 款项行内相加相符。"},
        {"sample_id": "B1-2023-D01", "target": "T05", "page": 19, "level": "类", "code": "205",
         "total": "20187.52", "part1_name": "款 20503", "part1": "18195.56",
         "part2_name": "款 20508", "part2": "1991.96",
         "leaf_items": ["2050302", "2050305", "2050802"], "n_leaves": 3,
         "formula": "18,195.56 + 1,991.96 = 20,187.52 = 类 205",
         "verdict": "PASS", "explanation": "类=款成立。"},
        {"sample_id": "B1-2023-D01", "target": "T05", "page": 19, "level": "款", "code": "20503",
         "total": "18195.56", "part1_name": "项 2050302", "part1": "1004.43",
         "part2_name": "项 2050305", "part2": "17191.13",
         "leaf_items": ["2050302", "2050305"], "n_leaves": 2,
         "formula": "1,004.43 + 17,191.13 = 18,195.56 = 款 20503",
         "verdict": "PASS", "explanation": "款=项成立。"},
        {"sample_id": "B1-2023-D01", "target": "T05", "page": 19, "level": "款", "code": "20508",
         "total": "1991.96", "part1_name": "项 2050802", "part1": "1991.96",
         "part2_name": "（无其他项）", "part2": "0.00",
         "leaf_items": ["2050802"], "n_leaves": 1,
         "formula": "1,991.96 = 1,991.96",
         "verdict": "PASS", "explanation": "20508 款项一致。"},
    ]
    # R4-03：T06 用打印合计，不拿叶项和替换原件数字；三条链路各自判定。
    relations.extend(t06_closure_chains(t06))
    relations.extend([
        {"sample_id": "B1-2024-D02", "target": "N06", "page": 24, "level": "说明vs表",
         "code": "基本支出", "total": "6745.63", "part1_name": "人员经费",
         "part1": "5350.25", "part2_name": "公用经费", "part2": "1395.40",
         "leaf_items": [], "n_leaves": 0,
         "formula": "5,350.25 + 1,395.40 = 6,745.65；说明 6,745.63；差 0.02 > 多项包络 0.015（基础容差 0.01）",
         "verdict": "DEFECT", "explanation": "N06 是说明内部人员+公用与说明总额核对；差 0.02，超过两项加总包络 0.015（基础容差 0.01），判 defect。"},
        {"sample_id": "B1-2025-U01", "target": "N07", "page": 20, "level": "完成率",
         "code": "三公合计", "total": "4.37", "part1_name": "决算数", "part1": "4.37",
         "part2_name": "预算数", "part2": "4.50",
         "leaf_items": [], "n_leaves": 0,
         "formula": "4.37 ÷ 4.50 = 97.11%",
         "verdict": "PASS",
         "explanation": "p20 的 97.11% 是完成预算比例，4.37/4.50 成立；不是结构占比缺陷。"},
        {"sample_id": "B1-2025-U01", "target": "N07", "page": 21, "level": "结构占比",
         "code": "三公三项", "total": "4.37", "part1_name": "公务用车费", "part1": "4.37",
         "part2_name": "出国+接待", "part2": "0.00+0.00",
         "leaf_items": [], "n_leaves": 0,
         "formula": "4.37 ÷ 4.37 = 100.00%；正文写 97.11%；0.00+97.11+0.00 = 97.11 ≠ 100",
         "verdict": "DEFECT",
         "explanation": "p21 结构占比误用预算数 4.50 作分母，保留 GS-N07 defect。"},
    ])
    return {"version": "b1-closure-round5-20261104",
            "note": "T03/T06 统一口径：T03=支出表行 合计=基本+项目，包络按末级项数 (n+1)*0.005；"
                    "T06 分开保存 printed_total（原件打印人员/公用合计）与 leaf_sum（非零末级项求和），"
                    "三条链路各自判定：叶项→公用合计（22 项包络 0.115）、打印人员+公用→表5"
                    "（2 项包络 0.015，0.02 超出，保留 PENDING_REVIEW）、全叶项→表5"
                    "（35 项包络 0.18，0.02 在内）。T06 最终舍入结论按冻结口径的全叶项链成立，"
                    "但不再用叶项和替换打印合计。N06=说明内部人员+公用 vs 说明总额，两项包络 0.015。",
            "printed_vs_leaf": {
                sid: {"printed": t06[sid]["printed"],
                      "leaf_sum": {column: t06[sid][column]["sum"]
                                   for column in ("人员经费", "公用经费")}}
                for sid in t06},
            "relations": relations}


def build_target_status() -> list:
    original = list(csv.DictReader((RUN / "target_status.csv").open(encoding="utf-8-sig")))
    revisions = {
        "T03": {
            "status": "PENDING_REVIEW", "effective_screened": "7", "candidate_sample": "B1-2023-D01",
            "note": "原 NOT_FOUND 6/6 修订为 PENDING_REVIEW 7/6（超1份保留）：B1-2023-D01 p13 20503 款级 0.02 超款级包络 0.015，完整子链（2050302/2050305 各 0.01）可解释但需裁定；B1-2025-D01 T03 pass（0.02 在 24 项包络 0.125 内）。20508/2050802 已用 p13 同一支出表实际三列 2,022.67/1,450.20/572.47 补证，不再用 p9/p19 代替。",
            "change_basis": "closure_evidence.csv 205/20503/2050302/2050305/20508/2050802（p13）；T03_T06_BASIS.md",
        },
        "T06": {
            "status": "NOT_FOUND", "effective_screened": "2", "candidate_sample": "",
            "note": "原 INCOMPLETE 0/6 修订为 NOT_FOUND 2/6：B1-2024-D02、B1-2025-D01 的 GS-T06 逐项证据均在。"
                    "R4-03 更正：原件打印人员/公用合计分别为 B1-2024-D02 5,350.25/1,395.40、"
                    "B1-2025-D01 89,230.64/17,395.21，叶项和另存；打印人员+公用与表5 差 0.02"
                    "（两项包络 0.015 超出，单列为待复核链），全叶项与表5 差 0.02（35 项包络 0.18 内）。"
                    "T06 舍入结论按冻结口径的全叶项链成立，但没有用派生值替换打印合计；"
                    "旧“24 项”是 T03 支出表项行数，已纠正。",
            "change_basis": "closure_evidence.csv T06 三条链 + t06_items.json（printed 与 leaf_sum 分列）",
        },
    }
    for row in original:
        revision = revisions.get(row["target_id"])
        row["change_basis"] = ""
        if revision:
            row.update(revision)
    with (OUT / "target_status_revision.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(original[0]))
        writer.writeheader()
        writer.writerows(original)
    return original


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    build_annotations()
    spec = build_spec()
    (OUT / "decimal_spec_v4.json").write_text(
        json.dumps(spec, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    checker = _load_checker()
    annotations = {}
    for path in sorted((OUT / "annotations_revised").glob("*/annotation.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        annotations[payload["sample_id"]] = payload
    rows = []
    for row in spec["rows"]:
        verdict = checker.evaluate(row, annotations)
        rows.append({
            "sample_id": row["sample_id"], "check_id": row["check_id"], "item": row["item"],
            "kind": row["kind"], "n": verdict.get("n"), "units": verdict.get("units"),
            "expected": row["expected"], "reported": row["reported"],
            "reported_unit": verdict.get("reported_unit"),
            "difference": verdict.get("difference", ""), "tolerance": verdict.get("tolerance", ""),
            "source_ok": verdict.get("source_ok"), "arithmetic_ok": verdict.get("arithmetic_ok"),
            "rule_applicable": verdict.get("rule_applicable"),
            "material_verdict": verdict.get("material_verdict"), "result": verdict.get("result"),
            "reasons": "；".join(verdict.get("reasons") or []),
        })
    with (OUT / "decimal_checks_v4.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    counts = {}
    for row in rows:
        counts[row["result"]] = counts.get(row["result"], 0) + 1
    (OUT / "decimal_checks_v4_summary.json").write_text(
        json.dumps({"rows": len(rows), "counts": counts}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    audit = verify_pdf_quotes(spec)
    (OUT / "decimal_evidence_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    identity = build_identity_audit(spec, annotations, checker)
    closure = build_closure_evidence()
    (OUT / "closure_evidence.json").write_text(
        json.dumps(closure, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with (OUT / "closure_evidence.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["sample_id", "target", "page", "level", "code", "table", "total",
                  "part1_name", "part1", "part2_name", "part2", "n_leaves",
                  "operand_count", "tolerance", "within_tolerance",
                  "formula", "verdict", "explanation"]
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(closure["relations"])
    status_rows = build_target_status()
    print(json.dumps({"decimal_counts": counts, "audit_all_found": audit["all_found"],
                      "audit_items": audit["total"],
                      "identity_all_ok": identity["all_source_ok"],
                      "identity_sources": identity["total"],
                      "closure": len(closure["relations"]),
                      "targets": len(status_rows)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
