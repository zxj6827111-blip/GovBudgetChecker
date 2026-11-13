"""Original-PDF arithmetic audit, with no imports from the application.

Page ranges and money columns were identified by reading the original pages.
Blank cells remain None; sums of printed values are explicitly limited checks.
"""
from pathlib import Path
from decimal import Decimal
import json
import re

BASE = Path(__file__).resolve().parent
raw = json.loads((BASE / "independent_raw_grid_rows.json").read_text())
pages = {p["physical_page"]: p["tables"][0] for p in raw}
checks = []

def amount(value):
    if value is None or not str(value).strip():
        return None
    cleaned = str(value).strip().replace(",", "")
    return Decimal(cleaned) if re.fullmatch(r"\d+(?:\.\d+)?", cleaned) else None

def compare(name, left, right, source_pages, partial=False, tolerance="0.5", note=""):
    difference = left - right
    checks.append({"relation": name, "pages": sorted(set(source_pages)),
                   "actual": str(left), "expected": str(right),
                   "difference": str(difference), "tolerance": tolerance,
                   "status": ("printed_values_consistent" if partial else "consistent")
                   if abs(difference) <= Decimal(tolerance) else "mismatch",
                   "partial": partial, "note": note})

def functional(source_pages, columns, name):
    result = {}
    for page in source_pages:
        for row in pages[page]:
            key = (row[0] or "").strip()
            if key == "合计" or re.fullmatch(r"\d{3}(?:\d{2})?(?:\d{2})?", key):
                result[key] = {"name": row[1], "page": page,
                               "values": [amount(row[c]) for c in columns]}
    for key, entry in result.items():
        values = entry["values"]
        printed = [v for v in values[1:] if v is not None]
        if values[0] is not None and printed:
            compare(f"{name} {key}横向已填分项", values[0], sum(printed), [entry["page"]],
                    partial=any(v is None for v in values[1:]),
                    note="仅核对已印金额；空白单元格没有赋值为零")
        children = [k for k in result if k.isdigit() and
                    ((key == "合计" and len(k) == 3) or
                     (key.isdigit() and k.startswith(key) and len(k) == len(key) + 2))]
        if children:
            for c, total in enumerate(values):
                printed_children = [result[k]["values"][c] for k in children
                                    if result[k]["values"][c] is not None]
                if total is not None and printed_children:
                    compare(f"{name} {key}第{c + 1}金额列直接下级", total,
                            sum(printed_children), [entry["page"]] + [result[k]["page"] for k in children],
                            partial=len(printed_children) != len(children),
                            note="只加直接下级，避免父子重复；空白仍为None")
    return result

t2 = functional([8, 9], [2, 3, 4, 5, 6, 7, 8], "收入决算表")
t3 = functional([10, 11], [2, 3, 4, 5, 6, 7], "支出决算表")
t5 = functional([14, 15], [2, 3, 4], "一般公共预算财政拨款支出决算表")

t1 = {r[0]: amount(r[1]) for r in pages[7]}
t1_out = {r[2]: amount(r[3]) for r in pages[7]}
compare("T1收入平衡", t1["总计"], t1["本年收入合计"] + t1["使用非财政拨款结余"] + t1["年初结转和结余"], [7])
compare("T1支出平衡", t1_out["总计"], t1_out["本年支出合计"] + t1_out["结余分配"] + t1_out["年末结转和结余"], [7])
compare("T1两侧总计", t1["总计"], t1_out["总计"], [7])
compare("T1本年收入与T2合计", t1["本年收入合计"], t2["合计"]["values"][0], [7, 8])
compare("T1财政拨款收入与T2", t1["一、一般公共预算财政拨款收入"], t2["合计"]["values"][1], [7, 8])
compare("T1其他收入与T2", t1["八、其他收入"], t2["合计"]["values"][-1], [7, 8])
compare("T1本年支出与T3", t1_out["本年支出合计"], t3["合计"]["values"][0], [7, 10])
root_labels = {"201": "一、一般公共服务支出", "208": "八、社会保障和就业支出",
               "210": "九、卫生健康支出", "216": "十五、商业服务业等支出", "221": "十九、住房保障支出"}
for code, label in root_labels.items():
    compare(f"T1与T3 {code}", t1_out[label], t3[code]["values"][0], [7, t3[code]["page"]])

t4_rows = pages[12] + pages[13]
t4_income = {r[0]: amount(r[1]) for r in t4_rows}
t4_out = {r[2]: [amount(v) for v in r[3:]] for r in t4_rows}
compare("T4收入平衡", t4_income["总计"], t4_income["本年收入合计"] + t4_income["年初财政拨款结转和结余"], [12, 13])
compare("T4支出平衡", t4_out["总计"][0], t4_out["本年支出合计"][0] + t4_out["年末财政拨款结转和结余"][0], [13])
compare("T4两侧总计", t4_income["总计"], t4_out["总计"][0], [13])
compare("T2财政拨款收入与T4", t2["合计"]["values"][1], t4_income["本年收入合计"], [8, 13])
compare("T4一般公共预算本年支出与T5", t4_out["本年支出合计"][1], t5["合计"]["values"][0], [13, 15])
for code, label in root_labels.items():
    compare(f"T4与T5 {code}", t4_out[label][1], t5[code]["values"][0], [12 if code in ["201", "208", "210"] else 13, t5[code]["page"]])

econ = {}
for row in pages[16][1:]:
    for start in [0, 3]:
        code = row[start]
        if code:
            econ[code] = {"name": row[start + 1], "value": amount(row[start + 2])}
for code in ["301", "303", "302", "310"]:
    descendants = [v["value"] for k, v in econ.items() if k.startswith(code) and len(k) == 5]
    printed = [v for v in descendants if v is not None]
    compare(f"T6 {code}已填明细合计", econ[code]["value"], sum(printed), [16],
            partial=len(printed) != len(descendants), note="不把空白经济科目赋零")
personnel = amount(pages[17][0][1]); public = amount(pages[17][0][3])
compare("T6人员经费跨页合计", personnel, econ["301"]["value"] + econ["303"]["value"], [16, 17])
compare("T6公用经费跨页合计", public, econ["302"]["value"] + econ["310"]["value"], [16, 17])
compare("T6人员加公用与T5基本支出", t5["合计"]["values"][1], personnel + public, [15, 17])
t7 = [amount(v) for v in pages[18][-1]]
for stage, offset in [("预算", 0), ("决算", 1)]:
    compare(f"T7{stage}总额与三项", t7[offset], t7[2 + offset] + t7[4 + offset] + t7[10 + offset], [18])
    compare(f"T7{stage}车辆小计与购置运行", t7[4 + offset], t7[6 + offset] + t7[8 + offset], [18])
compare("T6与T7车辆运行维护费", econ["30231"]["value"], t7[9], [16, 18])
compare("T6与T7接待费", econ["30217"]["value"], t7[11], [16, 18])

ratios = []
def ratio(name, numerator, denominator, declared, source_pages):
    calculated = Decimal(numerator) / Decimal(denominator) * 100
    ratios.append({"relation": name, "pages": source_pages, "numerator": numerator,
                   "denominator": denominator, "declared_pct": declared,
                   "calculated_pct": str(calculated), "difference_pp": str(Decimal(declared) - calculated),
                   "status": "consistent" if abs(Decimal(declared) - calculated) <= Decimal("0.1") else "mismatch"})

for name, n, d, p, pg in [
    ("财政拨款收入占比", "233085.87", "233238.38", "99.93", [8,21]),
    ("其他收入占比", "152.51", "233238.38", "0.07", [8,21]),
    ("基本支出占比", "24663.89", "233737.15", "10.55", [10,21]),
    ("项目支出占比", "209073.27", "233737.15", "89.45", [10,21]),
    ("一般公共预算占本年支出", "233564.77", "233737.15", "99.93", [10,15,21,22]),
    ("一般公共服务财政支出占比", "226550.41", "233564.77", "97.00", [14,15,22]),
    ("社会保障财政支出占比", "2879.88", "233564.77", "1.23", [14,15,22]),
    ("卫生健康财政支出占比", "1057.84", "233564.77", "0.45", [14,15,22]),
    ("商业服务财政支出占比", "1993.52", "233564.77", "0.85", [14,15,22]),
    ("住房保障财政支出占比", "1083.13", "233564.77", "0.46", [15,22]),
    ("一般公共预算完成率", "233564.77", "234430.42", "99.63", [22]),
    ("三公总额完成率", "244.48", "324.04", "75.45", [18,26]),
    ("三公出国完成率", "199.23", "209.14", "95.26", [18,26]),
    ("三公车辆完成率", "43.34", "67.30", "95.26", [18,26]),
    ("三公接待完成率", "1.90", "47.60", "3.99", [18,26]),
    ("三公出国结构占比", "199.23", "244.48", "81.49", [18,27]),
    ("三公车辆结构占比", "43.34", "244.48", "17.73", [18,27]),
    ("三公接待结构占比", "1.90", "244.48", "0.78", [18,27]),
]:
    ratio(name, n, d, p, pg)
for name, current, increase, percent, pg in [
    ("收支总计同比内部算术", "235305.25", "21258.91", "9.93", [21]),
    ("财政拨款收支总计同比内部算术", "234283.60", "21305.97", "10.00", [21]),
    ("一般公共预算支出同比内部算术", "233564.77", "25294.27", "12.14", [22]),
    ("三公总额同比内部算术", "244.48", "29.59", "13.77", [26]),
    ("三公出国同比内部算术", "199.23", "26.78", "15.53", [26]),
    ("三公车辆同比内部算术", "43.34", "1.11", "2.63", [26]),
    ("三公接待同比内部算术", "1.90", "1.69", "804.76", [26]),
    ("机关运行经费同比内部算术", "1649.72", "1234.67", "297.48", [28,29]),
]:
    ratio(name, increase, str(Decimal(current) - Decimal(increase)), percent, pg)

other_checks = []
for name, actual, parts, source_pages, unit in [
    ("政府采购货物工程服务合计", "153573.16", ["107.76", "0.00", "153465.40"], [29], "万元"),
    ("国有资产车辆分类合计", "13", ["0", "0", "1", "1", "0", "4", "0", "7"], [29], "辆"),
    ("绩效项目评级数量合计", "23", ["14", "9", "0", "0"], [28], "个"),
    ("绩效发现问题整改状态数量合计", "34", ["34", "0"], [28], "个"),
]:
    expected = sum(Decimal(v) for v in parts)
    difference = Decimal(actual) - expected
    other_checks.append({"relation": name, "actual": actual, "parts": parts,
                         "sum": str(expected), "difference": str(difference),
                         "pages": source_pages, "unit": unit,
                         "status": "consistent" if difference == 0 else "mismatch"})

result = {"source": "independent original page grids and literal narrative; no system facts used",
          "blank_policy": "None preserved, printed-value-only checks separately marked",
          "amount_checks": checks, "percentage_checks": ratios, "other_narrative_checks": other_checks,
          "totals": {"amount_checks": len(checks), "amount_mismatches": sum(c["status"] == "mismatch" for c in checks),
                     "partial_amount_checks": sum(c["partial"] for c in checks),
                     "percentage_checks": len(ratios), "percentage_mismatches": sum(c["status"] == "mismatch" for c in ratios),
                     "other_narrative_checks": len(other_checks)},
          "limits": ["空白金额没有补零，已填分项一致不证明空白分项为零", "同比基数只检查本材料内部算术，未核验2024年原公开材料",
                     "因公出国基本支出99.34与三公199.23口径范围不同，不按相等关系误报", "未用完整官方科目库检查全部功能分类名称"]}
(BASE / "independent_arithmetic.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(json.dumps(result["totals"], ensure_ascii=False))
print(json.dumps([r for r in ratios if r["status"] == "mismatch"], ensure_ascii=False))
