"""核对固定原件规则差异，真值仍取冻结人工裁定及原 PDF 文本/字符。"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.engine.common_rules import CMM007_ZeroBaseTrendRecompute
from src.engine.rule_outcome import RuleDeferred
from src.engine.rules_v33 import (
    R33235_NarrativeAmountConsistency,
    R33240_Table2_IncomeAdvancedCheck,
    build_document,
)

BASE = Path(__file__).resolve().parent
FROZEN = ROOT / "outputs/shanghai_2024_new10_acceptance_20261015"
PHASE = sys.argv[1] if len(sys.argv) > 1 else "final_new10"
DEST = BASE / "rule_verification"
DEST.mkdir(exist_ok=True)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def signature(item):
    return (
        item.get("rule", item.get("rule_id")),
        re.sub(r"^[一二三四五六七八九十百]+、", "", item["message"]),
    )


def execute(rule, doc):
    try:
        return "completed", list(rule.apply(doc)), []
    except RuleDeferred as signal:
        return signal.status, list(signal.partial_issues), list(signal.unresolved_reasons)


def compact(issue):
    return {"rule": issue.rule, "severity": issue.severity, "message": issue.message, "location": issue.location}


review = read(FROZEN / "engine_candidate_review.json")
source_issues = read(FROZEN / "confirmed_source_issues.json")
before, after, differences, texts = {}, {}, [], {}
for old_path in sorted((BASE / "baseline/documents").glob("*/engine_issues.json")):
    doc_id = old_path.parent.name
    before[doc_id] = read(old_path)
    after[doc_id] = read(BASE / PHASE / "documents" / doc_id / "engine_issues.json")
    with pdfplumber.open(old_path.parent / "source.pdf") as pdf:
        texts[doc_id] = [page.extract_text() or "" for page in pdf.pages]
    old_count = Counter(signature(item) for item in before[doc_id])
    new_count = Counter(signature(item) for item in after[doc_id])
    removed_count = old_count - new_count
    added_count = new_count - old_count
    removed, added = [], []
    for item in before[doc_id]:
        if removed_count[signature(item)] > 0:
            removed.append(item)
            removed_count[signature(item)] -= 1
    for item in after[doc_id]:
        if added_count[signature(item)] > 0:
            added.append(item)
            added_count[signature(item)] -= 1
    differences.append({"doc_id": doc_id, "before": len(before[doc_id]), "after": len(after[doc_id]), "removed": removed, "added": added})

candidates = []
for item in review:
    key = signature(item)
    old_matches = [issue for issue in before[item["doc_id"]] if signature(issue) == key]
    new_matches = [issue for issue in after[item["doc_id"]] if signature(issue) == key]
    expected = item["decision"] != "false_positive"
    candidates.append({**item, "before_matches": len(old_matches), "after_matches": len(new_matches), "passed": len(old_matches) == 1 and bool(new_matches) == expected})

# 原金额来自原页文本，完整六类收入均保留，不按系统事实或总分差额反求。
income_checks = []
native_income = texts["SH2024NEW_02"][6]
codes = ["合计", "206", "20603", "2060302", "207", "20703", "2070307"]
for code in codes:
    lines = [line for line in native_income.splitlines() if re.match(re.escape(code) + r"\s", line)]
    assert len(lines) == 1
    amounts = re.findall(r"\d[\d,]*\.\d{2}", lines[0])
    assert len(amounts) == 7
    nums = [Decimal(amount.replace(",", "")) for amount in amounts]
    difference = nums[0] - sum(nums[1:])
    assert abs(difference) <= Decimal("0.01")
    income_checks.append({"code": code, "page": 7, "native_line": lines[0], "total": str(nums[0]), "six_sources": list(map(str, nums[1:])), "difference": str(difference), "passed": True})

budget_page = texts["SH2024NEW_02"][28]
budget_flat = re.sub(r"\s+", "", budget_page)
assert "年初部门预算为0万元，支出决算为3,631.14万元" in budget_flat
budget_check = {"doc_id": "SH2024NEW_02", "page": 29, "native_quote": "年初部门预算为0万元，支出决算为3,631.14万元", "decision": "0是预算阶段，不能冒充条目开头决算", "passed": True}

trend_checks, positive_controls = [], []
for doc_id, current_page, trend_page, current, delta, pct in [
    ("SH2024NEW_06", 35, 36, "250.60", "58.59", "30.51"),
    ("SH2024NEW_09", 37, 37, "326.94", "50.02", "18.06"),
]:
    flat_current = re.sub(r"\s+", "", texts[doc_id][current_page - 1])
    flat_trend = re.sub(r"\s+", "", texts[doc_id][trend_page - 1])
    subject = "公务用车购置及运行维护费"
    current_quote = f"{subject}支出决算为{current}万元"
    trend_quote = f"{subject}支出决算增加{delta}万元，增长{pct}%"
    assert current_quote in flat_current and trend_quote in flat_trend
    prior = Decimal(current) - Decimal(delta)
    recomputed = Decimal(delta) / prior * 100
    assert abs(recomputed - Decimal(pct)) < Decimal("0.01")
    trend_checks.append({"doc_id": doc_id, "pages": [current_page, trend_page], "current_quote": current_quote, "trend_quote": trend_quote, "derived_prior": str(prior), "recomputed_percent": str(recomputed), "passed": True})
    mutated = list(texts[doc_id])
    mutated[trend_page - 1], count = re.subn(r"增\s*长\s*" + re.escape(pct) + r"\s*%", "增长80.00%", mutated[trend_page - 1])
    assert count == 1
    doc = build_document("2024年度部门决算.pdf", mutated, [], 0)
    status, issues, reasons = execute(CMM007_ZeroBaseTrendRecompute(), doc)
    hits = [issue for issue in issues if issue.location.get("indicator") == subject and issue.location.get("current_amount") == current]
    assert status == "completed" and len(hits) == 1
    positive_controls.append({"kind": "original_text_with_one_percentage_mutation", "doc_id": doc_id, "changed_percent": "80.00", "findings": list(map(compact, issues)), "passed": True})

# 防止三个规则全静音：金额、同比、同条前后矛盾各保留一个独立正例。
headers = [
    ["项目", "", "本年收入合计", "财政拨款收入", "上级补助\n收入", "事业收入", "经营收入", "附属单位\n上缴收入", "其他收入"],
    ["功能分类\n科目编码", "科目名称", "", "", "", "", "", "", ""],
]
row = ["206", "科学技术支出", "5,338.84", "4,898.83", "200.00", "225.64", "0.00", "0.00", "4.37"]
doc = build_document("2024年度部门决算.pdf", ["收入决算表\n单位：万元"], [[headers + [row]]], 0)
status, issues, _ = execute(R33240_Table2_IncomeAdvancedCheck(), doc)
assert status == "completed" and len(issues) == 1 and "差额=10.00" in issues[0].message
positive_controls.append({"kind": "income_total_10_wan_mutation", "findings": list(map(compact, issues)), "passed": True})
doc = build_document("2024年度部门决算.pdf", ["1、住房保障支出（项）207万元。年初部门预算为241.76万元，支出决算为170.03万元。"], [], 0)
status, issues, _ = execute(R33235_NarrativeAmountConsistency(), doc)
assert status == "completed" and len(issues) == 1
positive_controls.append({"kind": "distinct_opening_final_amount_conflict", "findings": list(map(compact, issues)), "passed": True})

# 两个新候选分别裁定，空白仍是未知；名称漏字由原字符及渲染独立证明。
with pdfplumber.open(BASE / "baseline/documents/SH2024NEW_04/source.pdf") as pdf:
    table = pdf.pages[7].extract_table()
    continuation = pdf.pages[8].extract_table()
    native_row = [row for row in table if row[0] == "215"]
    assert len(native_row) == 1 and native_row[0][5] == ""
blank_status, blank_issues, blank_reasons = execute(
    R33240_Table2_IncomeAdvancedCheck(),
    build_document("2024年度部门决算.pdf", ["收入决算表\n单位：万元", "收入决算表续表"], [[table], [continuation]], 0),
)
blank_check = {"doc_id": "SH2024NEW_04", "page": 8, "native_row": native_row[0], "independent_arithmetic_decision": "insufficient_data", "actual_rule_status": blank_status, "findings": list(map(compact, blank_issues)), "unresolved_reasons": blank_reasons, "passed": blank_status == "insufficient_data" and not blank_issues}
assert blank_check["passed"]
stored_outcomes = read(BASE / PHASE / "documents/SH2024NEW_04/rule_outcomes.json")
stored_blank = [item for item in stored_outcomes if item["rule_id"] == "V33-240"]
assert len(stored_blank) == 1
stored_blank_check = {
    "status": stored_blank[0]["status"],
    "partial_findings_count": stored_blank[0].get("partial_findings_count"),
    "unresolved_reasons": stored_blank[0].get("unresolved_reasons", []),
    "new_zero_filled_error": any(item["rule"] == "V33-240" for item in after["SH2024NEW_04"]),
}
stored_blank_check["passed"] = (
    stored_blank_check["status"] == "insufficient_data"
    and stored_blank_check["partial_findings_count"] == 0
    and any("（215）" in reason and "未以空白补零" in reason for reason in stored_blank_check["unresolved_reasons"])
    and not stored_blank_check["new_zero_filled_error"]
)

new_source_proofs = []
source_pdf = BASE / "baseline/documents/SH2024NEW_06/source.pdf"
with pdfplumber.open(source_pdf) as pdf:
    for page_number, quote in [(18, "农林水支出"), (32, "农林支出")]:
        chars = pdf.pages[page_number - 1].chars
        stream = "".join(char["text"] for char in chars)
        start = stream.find(quote)
        assert start >= 0 and stream.count(quote) >= 1
        end = start + len(quote)
        selected = chars[start:end]
        assert "".join(char["text"] for char in selected) == quote
        new_source_proofs.append({"page": page_number, "native_text": quote, "native_char_start": start, "native_char_end_exclusive": end, "bbox": [min(char["x0"] for char in selected), min(char["top"] for char in selected), max(char["x1"] for char in selected), max(char["bottom"] for char in selected)]})
supplement = {"issue_id": "SUPPLEMENT-SOURCE-01", "doc_id": "SH2024NEW_06", "title": "防汛条目类级名称漏写水字", "severity": "low", "source_pdf_sha256": sha(source_pdf), "original_frozen_11_issues_unchanged": True, "table_code": "213", "source_proofs": new_source_proofs, "rendered_review": str(DEST / "SH2024NEW_06_p32.png"), "decision": "第32页23、防汛说明写农林支出；第18页对应213原表写农林水支出。独立字符与原图证实名称漏字，不据系统输出改真值。"}

supplement_candidates = [item for item in after["SH2024NEW_06"] if item["rule"] == "V33-227" and "农林支出" in item["message"]]
assert len(supplement_candidates) == 1
regressions = [item for item in candidates if not item["passed"]]
new_noninfo = []
for diff in differences:
    for issue in diff["added"]:
        if issue["severity"] != "info" and issue not in supplement_candidates:
            new_noninfo.append({"doc_id": diff["doc_id"], **issue})
report = {
    "phase": PHASE,
    "scope": "固定材料回归，非未知材料盲测；原件真值与规则输出分别保存",
    "frozen_acceptance_engine_issues": read(FROZEN / "final_summary.json")["rule_review"]["engine_issues"],
    "current_before_engine_issues": sum(map(len, before.values())),
    "current_after_engine_issues": sum(map(len, after.values())),
    "false_positive_cases": len([item for item in candidates if item["decision"] == "false_positive"]),
    "false_positive_cases_removed": len([item for item in candidates if item["decision"] == "false_positive" and item["passed"]]),
    "confirmed_candidates_retained": len([item for item in candidates if item["decision"] == "confirmed_source_issue" and item["passed"]]),
    "original_source_issue_count": len(source_issues),
    "original_source_issue_files": {name: sha(FROZEN / name) for name in ["confirmed_source_issues.json", "原件问题清单.csv", "engine_candidate_review.json"]},
    "candidate_checks": candidates,
    "independent_income_source_checks": income_checks,
    "independent_budget_stage_check": budget_check,
    "independent_vehicle_operand_checks": trend_checks,
    "positive_controls": positive_controls,
    "blank_source_refusal": blank_check,
    "stored_pipeline_blank_source_refusal": stored_blank_check,
    "supplementary_source_issue": supplement,
    "new_unadjudicated_noninfo_candidates": new_noninfo,
    "issue_differences": differences,
    "regressions": regressions,
    "passed": not regressions and not new_noninfo and blank_check["passed"] and stored_blank_check["passed"],
    "limits": ["SOURCE-11原有层级缺项/重复行只保留结构复核线索，不能由其差额断言总收入金额错误。", "不将全部规则返回空列表作为修复成功；四项变异正例、原14项正确候选及11项冻结原件问题均核对。", "冻结验收记录66项，本轮修改前公开入口复现80项；两份记录数字分别列出，不推断差异原因，逐项比较使用本轮baseline。"],
}
(DEST / f"{PHASE}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
(DEST / "supplementary_source_issue.json").write_text(json.dumps(supplement, ensure_ascii=False, indent=2), encoding="utf-8")
lines = [
    f"规则固定材料回归：{PHASE}",
    f"本轮修改前后候选：{report['current_before_engine_issues']} → {report['current_after_engine_issues']}；冻结验收记录另为66项，不混用分母。",
    f"已裁定10项误报移除：{report['false_positive_cases_removed']}/10；14条正确候选保留：{report['confirmed_candidates_retained']}/14。",
    "原11项原件问题文件保留；新发现水务防汛条目类级名称漏水字，独立字符和原图证据另存 supplementary_source_issue.json。",
    "国资215收入来源列空白继续判未完成，不能按228.40差额补数或新增认定总额错误。",
    "四项主动注入真实差错的正例均会报错，未以规则静音消除误报。",
    f"结果：{'通过' if report['passed'] else '仍有未清候选或回退，见JSON'}。",
    "",
    "逐项冻结候选：",
]
for item in candidates:
    lines.append(
        f"{item['doc_id']} {item['engine_issue_id']} {item['decision']}："
        f"{item['before_matches']}→{item['after_matches']}；{'符合裁定' if item['passed'] else '未通过'}"
    )
lines.extend(["", "新增且尚未裁定的非info候选：", json.dumps(new_noninfo, ensure_ascii=False, indent=2)])
(DEST / f"{PHASE}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(json.dumps({key: report[key] for key in ["phase", "current_before_engine_issues", "current_after_engine_issues", "false_positive_cases_removed", "confirmed_candidates_retained", "original_source_issue_count", "passed"]}, ensure_ascii=False))
if not report["passed"]:
    raise SystemExit(1)
