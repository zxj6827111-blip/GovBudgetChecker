"""第四轮自控：R3-03 rule_constant 白名单与身份唯一性。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

TOOLS = Path("/Volumes/workSSD/software development/GovBudgetChecker/outputs/unified_closeout_round6_review_codex_20261104/delivery_tools")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dec = load("dec_r4", TOOLS / "decimal_checks_v2.py")


def _ann(values, tables=None, sections=None, pages=30):
    return {"s": {"basic": {"tables": tables or [], "sections": sections or [], "pages": pages},
                  "checks": [{"check_id": "C", "items": [{"label": "关系", "values": values}]}]}}


def test_rule_constant_percent_sum_100_positive():
    values = [
        {"page": 21, "quote": "占97.11%", "unit": "%", "name": "公务用车费占比"},
        {"page": 21, "quote": "占0.00%", "unit": "%", "name": "出国费占比"},
        {"page": 21, "quote": "占0.00%", "unit": "%", "name": "接待费占比"},
    ]
    operands = [
        {"value": "0.00", "page": 21, "quote": "占0.00%", "unit": "%", "row": "出国费占比", "column": "正文", "table": "七、说明"},
        {"value": "97.11", "page": 21, "quote": "占97.11%", "unit": "%", "row": "公务用车费占比", "column": "正文", "table": "七、说明"},
        {"value": "0.00", "page": 21, "quote": "占0.00%", "unit": "%", "row": "接待费占比", "column": "正文", "table": "七、说明"},
    ]
    ann = _ann(values, sections=[{"name": "七、说明", "page": 20}])
    row = {
        "sample_id": "s", "check_id": "C", "kind": "percent", "n": 3,
        "operands": operands, "expected": "97.11", "expected_unit": "%",
        "reported": "100.00", "reported_unit": "%",
        "reported_source": {"source_kind": "rule_constant", "rule_constant_id": "percent_sum_100", "unit": "%"},
    }
    verdict = dec.evaluate(row, ann)
    assert verdict["source_ok"] is True
    assert verdict["arithmetic_ok"] is True
    assert verdict["result"] == "FAIL"  # 97.11 ≠ 100，差 2.89


def test_rule_constant_cannot_cover_material_operand():
    c = {"source_kind": "rule_constant", "rule_constant_id": "percent_sum_100", "unit": "%"}
    row = {
        "sample_id": "s", "check_id": "C", "kind": "percent", "n": 1,
        "operands": [dict(c, value="100.00")], "expected": "100.00",
        "reported": "100.00", "reported_unit": "%", "reported_source": c,
    }
    assert dec.evaluate(row, {})["result"] != "PASS"


def test_rule_constant_arbitrary_basis_cannot_pass():
    c = {"source_kind": "rule_constant", "rule_basis": "任意说明", "unit": "万元"}
    row = {
        "sample_id": "s", "check_id": "C", "kind": "amount", "n": 1,
        "operands": [dict(c, value="100.00")], "expected": "100.00",
        "reported": "100.00", "reported_unit": "万元", "reported_source": c,
    }
    assert dec.evaluate(row, {})["result"] != "PASS"


def test_same_page_same_number_other_row_is_pending():
    values = [
        {"page": 1, "quote": "100.00", "unit": "万元", "name": "住房公积金 决算"},
        {"page": 1, "quote": "100.00", "unit": "万元", "name": "医疗费 预算"},
    ]
    source = {"page": 1, "quote": "100.00", "unit": "万元", "row": "其他费用", "column": "决算", "table": "T"}
    ann = _ann(values, tables=[{"name": "T", "page": 1}])
    row = {
        "sample_id": "s", "check_id": "C", "kind": "amount", "n": 1,
        "operands": [dict(source, value="100.00")], "expected": "100.00",
        "reported": "100.00", "reported_unit": "万元", "reported_source": dict(source),
    }
    assert dec.evaluate(row, ann)["result"] == "PENDING"


def test_zero_hit_target_cannot_be_fixed():
    ledger = load("ledger_r4_zero", TOOLS / "build_final_ledger.py")
    assert ledger.decide({"type": "MISS", "anchor_entries": []})[0] == "strict_partial"
