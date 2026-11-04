"""R-02 出现点覆盖负控：版本/位置/金额/单位不能无条件忽略。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
TOOL = HERE / "scripts" / "batch2_gold_compare.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cmp = _load("cmp_anchor", TOOL)
ev = _load("ev_anchor", HERE / "scripts" / "gold_baseline_eval_fp_batch1.py")


def item(text, quotes=(), pages=()):
    return {"text": text, "quotes": list(quotes), "pages": list(pages)}


def test_generic_phrase_cannot_cover_amount_anchor():
    assert not cmp._anchor_covered(item("项目支出金额已经核对一致", ["项目支出"], [99]),
                                   "项目支出217,7380.31 万元", ev)


def test_name_only_with_wrong_amount_cannot_cover():
    assert not cmp._anchor_covered(item("住房公积金金额不一致：10.00 与20.00"),
                                   "住房公积金（项）100.00万元", ev)


def test_same_name_other_page_cannot_cover():
    assert not cmp._anchor_covered(item("行政运行费", ["行政运行费"], [10]),
                                   "行政运行费（项）13,265.31", ev, page=11)


def test_same_page_and_name_without_amount_is_allowed():
    assert cmp._anchor_covered(item("行政运行费", ["行政运行费"], [11]),
                               "行政运行费（项）13,265.31", ev, page=11)


def test_same_amount_but_wrong_unit_cannot_cover():
    assert not cmp._anchor_covered(item("项目支出 100.00 元", ["项目支出 100.00 元"], [5]),
                                   "项目支出100.00万元", ev, page=5)


def test_full_quote_still_covers():
    assert cmp._anchor_covered(
        item("说明写“其他行政事业单位离退休支出（项）”", ["其他行政事业单位离退休支出（项）"], [21]),
        "其他行政事业单位离退休支出（项）", ev, page=21)
