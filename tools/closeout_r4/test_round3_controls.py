"""第三轮自控：R2-03 FP 实体残留与 R2-04 单位/换算正负控。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path("/Volumes/workSSD/software development/GovBudgetChecker")
TOOLS = ROOT / "outputs/unified_closeout_round3_20261104/tools"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = load("ledger_r3", TOOLS / "build_final_ledger.py")
dec = load("dec_r3", TOOLS / "decimal_checks_v2.py")


def _ann(values):
    return {"s": {"checks": [{"check_id": "C", "items": [{"values": values}]}]}}


def _row(kind, operands, expected, reported, reported_source, **extra):
    payload = {
        "sample_id": "s", "check_id": "C", "kind": kind, "n": len(operands),
        "operands": operands, "expected": expected, "reported": reported,
        "reported_source": reported_source, "applicable": True,
    }
    payload.update(extra)
    return payload


def test_fp_same_entity_f_residual_blocks_even_if_r_more_similar():
    row = {
        "type": "FP", "current_state": "still_present",
        "baseline_amounts": ["100.00", "90.00"],
        "residual_f_entities": [{"id": "same-entity", "tier": "F"}],
        "best_current_match": {"id": "r", "tier": "R", "similarity": 0.99,
                               "amounts": ["100.00", "90.00"]},
    }
    assert ledger.decide(row)[0] != "fixed"


def test_fp_same_page_rule_different_entity_is_fixed():
    row = {
        "type": "FP", "current_state": "still_present",
        "baseline_amounts": ["100.00", "90.00"],
        "best_current_match": {"id": "other", "tier": "F", "similarity": 0.4,
                               "amounts": ["200.00"]},
    }
    assert ledger.decide(row)[0] == "fixed"


def test_fp_ambiguous_f_is_not_fixed():
    row = {
        "type": "FP", "current_state": "still_present",
        "baseline_amounts": [],
        "ambiguous_f_entities": [{"id": "amb", "tier": "F"}],
        "best_current_match": {"id": "amb", "tier": "F", "similarity": 0.99, "amounts": []},
    }
    assert ledger.decide(row)[0] != "fixed"


def test_conversion_10000_yuan_equals_1_wanyuan_passes():
    values = [{"page": 1, "quote": "10000.00元", "unit": "元"},
              {"page": 1, "quote": "1.00万元", "unit": "万元"}]
    row = _row("conversion",
               [{"value": "10000.00", "page": 1, "quote": "10000.00元", "unit": "元"}],
               "1.00", "1.00",
               {"page": 1, "quote": "1.00万元", "unit": "万元"},
               expected_unit="万元", reported_unit="万元")
    assert dec.evaluate(row, _ann(values))["result"] == "PASS"


def test_conversion_truncation_cannot_pass():
    values = [{"page": 1, "quote": "10050元", "unit": "元"},
              {"page": 1, "quote": "1.00万元", "unit": "万元"}]
    row = _row("conversion",
               [{"value": "10050", "page": 1, "quote": "10050元", "unit": "元"}],
               "1.005", "1.00",
               {"page": 1, "quote": "1.00万元", "unit": "万元"},
               expected_unit="万元", reported_unit="万元", target_precision=2)
    assert dec.evaluate(row, _ann(values))["result"] == "FAIL"


def test_mixed_units_without_conversion_is_pending():
    values = [{"page": 1, "quote": "100.00元", "unit": "元"},
              {"page": 1, "quote": "1.00万元", "unit": "万元"}]
    row = _row("amount",
               [{"value": "100.00", "page": 1, "quote": "100.00元", "unit": "元"},
                {"value": "1.00", "page": 1, "quote": "1.00万元", "unit": "万元"}],
               "1.01", "1.01",
               {"page": 1, "quote": "1.00万元", "unit": "万元"})
    assert dec.evaluate(row, _ann(values))["result"] == "PENDING"


def test_missing_unit_is_pending():
    values = [{"page": 1, "quote": "100.00", "unit": ""},
              {"page": 1, "quote": "100.00", "unit": ""}]
    row = _row("amount",
               [{"value": "100.00", "page": 1, "quote": "100.00"}],
               "100.00", "100.00", {"page": 1, "quote": "100.00"})
    verdict = dec.evaluate(row, _ann(values))
    assert verdict["result"] == "PENDING"
    assert "单位" in "；".join(verdict["reasons"])
