"""第五轮自控：R4-02 Decimal 来源身份（编码/行/列）与 R4-03 打印合计分离。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dec = load("dec_r5", HERE / "decimal_checks_v2.py")
b1 = load("b1_r5", HERE / "build_b1_revision.py")

VALUE = {"name": "财政拨款收入", "page": 11, "quote": "29,663.18", "unit": "万元"}
SOURCE = {"page": 11, "quote": "29,663.18", "unit": "万元",
          "row": "2140101 财政拨款收入", "column": "财政拨款收入", "table": "收入决算表"}


def _ann(values=None, label="2140101 行政运行"):
    return {"s": {"basic": {"pages": 40,
                            "tables": [{"name": "收入决算表", "page": 11}],
                            "sections": []},
                  "checks": [{"check_id": "C",
                              "items": [{"label": label,
                                         "values": list(values or [VALUE])}]}]}}


def _row(source, value="29663.18", expected="29663.18", reported="29663.18"):
    return {"sample_id": "s", "check_id": "C", "kind": "amount", "n": 1,
            "operands": [dict(source, value=value)],
            "expected": expected, "reported": reported,
            "reported_unit": "万元", "reported_source": source,
            "applicable": True}


def test_correct_subject_code_and_column_positive():
    verdict = dec.evaluate(_row(SOURCE), _ann())
    assert verdict["source_ok"] and verdict["result"] == "PASS"


def test_wrong_subject_code_rejected():
    source = dict(SOURCE, row="2140106 财政拨款收入")
    verdict = dec.evaluate(_row(source), _ann())
    assert not verdict["source_ok"]
    assert verdict["result"] != "PASS"


def test_wrong_income_column_rejected():
    source = dict(SOURCE, column="事业收入")
    verdict = dec.evaluate(_row(source), _ann())
    assert not verdict["source_ok"]
    assert verdict["result"] != "PASS"


def test_empty_name_cannot_claim_row_identity():
    value = {"page": 1, "quote": "100.00", "unit": "万元"}
    ann = {"s": {"basic": {"pages": 1, "tables": [{"name": "T", "page": 1}]},
                 "checks": [{"check_id": "C", "items": [{"values": [value]}]}]}}
    source = dict(value, row="住房公积金", column="决算", table="T")
    assert not dec.evaluate(_row(source, value="100.00", expected="100.00",
                                 reported="100.00"), ann)["source_ok"]


def test_explicit_row_column_conflict_rejected():
    value = {"page": 1, "quote": "100.00", "unit": "万元",
             "table": "T", "row": "住房公积金", "column": "决算"}
    ann = {"s": {"basic": {"pages": 1, "tables": [{"name": "T", "page": 1}]},
                 "checks": [{"check_id": "C", "items": [{"values": [value]}]}]}}
    source = dict(value, row="医疗费", column="预算")
    verdict = dec.evaluate(_row(source, value="100.00", expected="100.00",
                                reported="100.00"), ann)
    assert verdict["result"] != "PASS"


def test_matching_explicit_row_column_positive():
    value = {"page": 1, "quote": "100.00", "unit": "万元",
             "table": "T", "row": "住房公积金", "column": "决算"}
    ann = {"s": {"basic": {"pages": 1, "tables": [{"name": "T", "page": 1}]},
                 "checks": [{"check_id": "C", "items": [{"values": [value]}]}]}}
    verdict = dec.evaluate(_row(value, value="100.00", expected="100.00",
                                reported="100.00"), ann)
    assert verdict["result"] == "PASS"


def test_rule_constant_only_for_reported_percent():
    constant = {"source_kind": "rule_constant", "rule_constant_id": "percent_sum_100",
                "unit": "%"}
    values = [{"name": "占比", "page": 1, "quote": "占97.11%", "unit": "%"},
              {"name": "占比2", "page": 1, "quote": "占2.89%", "unit": "%"}]
    ann = {"s": {"checks": [{"check_id": "C", "items": [{"label": "占比",
                                                         "values": values}]}]}}
    row = {"sample_id": "s", "check_id": "C", "kind": "percent", "n": 2,
           "operands": [{"value": "97.11", "page": 1, "quote": "占97.11%", "unit": "%"},
                        {"value": "2.89", "page": 1, "quote": "占2.89%", "unit": "%"}],
           "expected": "100", "reported": "100", "reported_unit": "%",
           "reported_source": constant, "applicable": True}
    verdict = dec.evaluate(row, ann)
    assert verdict["source_ok"]


def test_rule_constant_cannot_replace_material_operand():
    constant = {"source_kind": "rule_constant", "rule_constant_id": "percent_sum_100",
                "unit": "%"}
    row = {"sample_id": "s", "check_id": "C", "kind": "amount", "n": 1,
           "operands": [dict(constant, value="100.00")],
           "expected": "100.00", "reported": "100.00", "reported_unit": "万元",
           "reported_source": constant, "applicable": True}
    assert dec.evaluate(row, {})["result"] != "PASS"


def test_b1_t06_printed_total_is_not_leaf_sum():
    """R4-03：T06 必须分别保存打印合计与叶项和，不能互相替换。"""
    printed = b1.parse_printed_totals()
    data = {"B1-2025-D01": {"人员经费": {"count": 13, "sum": "89230.64"},
                            "公用经费": {"count": 22, "sum": "17395.25"},
                            "nonzero_count": 35}}
    chains = b1.t06_closure_chains(data, printed)
    labels = {(chain["sample_id"], chain["code"]): chain for chain in chains}
    leaf_pub = labels[("B1-2025-D01", "leaf_pub_vs_printed")]
    assert leaf_pub["total"] == "17395.21"
    assert leaf_pub["part1"] == "17395.25"
    assert leaf_pub["part2"] == "0.04"
    assert leaf_pub["within_tolerance"] is True
    printed_pair = labels[("B1-2025-D01", "printed_sum_vs_table5")]
    assert printed_pair["part1"] == "89230.64"
    assert printed_pair["part2"] == "17395.21"
    assert printed_pair["within_tolerance"] is False
    leaf_total = labels[("B1-2025-D01", "leaf_total_vs_table5")]
    assert leaf_total["part1"] == "106625.89"
    assert leaf_total["total"] == "106625.87"
