"""R2-02 出现点覆盖：页、局部引文、金额、精确单位必须同一出现点。"""
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


def item(text, quotes=(), pages=(), refs=None, occurrences=None):
    payload = {"text": text, "quotes": list(quotes), "pages": list(pages)}
    if refs is not None or occurrences is not None:
        payload["location"] = {"locations": list(refs or []),
                               "table_refs": list(occurrences or [])}
    return payload


def ref(page, text):
    return {"page": page, "text": text}


def test_generic_phrase_cannot_cover_amount_anchor():
    assert not cmp._anchor_covered(item("项目支出金额已经核对一致", ["项目支出"], [99]),
                                   "项目支出217,7380.31 万元", ev)


def test_name_only_with_wrong_amount_cannot_cover():
    assert not cmp._anchor_covered(item("住房公积金金额不一致：10.00 与20.00"),
                                   "住房公积金（项）100.00万元", ev)


def test_same_page_wrong_amount_cannot_cover():
    assert not cmp._anchor_covered(item("住房公积金10.00万元", ["住房公积金10.00万元"], [5]),
                                   "住房公积金100.00万元", ev, page=5)


def test_missing_page_cannot_cover_located_anchor():
    assert not cmp._anchor_covered(item("项目支出100.00万元", ["项目支出100.00万元"], []),
                                   "项目支出100.00万元", ev, page=5)


def test_other_page_quote_cannot_cover_target_page():
    payload = item("项目支出10.00万元", ["项目支出10.00万元"], [5, 6],
                   refs=[ref(6, "项目支出100.00万元")])
    assert not cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


def test_multi_page_refs_bind_each_occurrence():
    payload = item("说明分两页", [], [5, 6],
                   refs=[ref(5, "项目支出100.00万元"), ref(6, "项目支出10.00万元")])
    assert cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)
    assert not cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=6)


def test_same_name_other_page_cannot_cover():
    assert not cmp._anchor_covered(item("行政运行费", ["行政运行费"], [10]),
                                   "行政运行费（项）13,265.31", ev, page=11)


def test_name_contract_still_requires_same_page():
    payload = item("行政运行费", ["行政运行费"], [11])
    assert cmp._anchor_covered(payload, "行政运行费（项）13,265.31", ev, page=11,
                               amount_required=False)
    assert not cmp._anchor_covered(payload, "行政运行费（项）13,265.31", ev, page=12,
                                   amount_required=False)


def test_name_contract_is_not_default_for_amount_checks():
    # 金额/格式类默认 amount_required=True：同名同页但金额未复述不得放行。
    assert not cmp._anchor_covered(item("行政运行费", ["行政运行费"], [11]),
                                   "行政运行费（项）13,265.31", ev, page=11)


def test_same_amount_but_wrong_unit_cannot_cover():
    assert not cmp._anchor_covered(item("项目支出 100.00 元", ["项目支出 100.00 元"], [5]),
                                   "项目支出100.00万元", ev, page=5)


def test_yuan_anchor_not_satisfied_by_wanyuan_text():
    assert not cmp._anchor_covered(item("其他项目支出100.00万元", ["其他项目支出100.00万元"], [5]),
                                   "项目支出100.00元", ev, page=5)


def test_full_quote_still_covers():
    assert cmp._anchor_covered(
        item("说明写“其他行政事业单位离退休支出（项）”", ["其他行政事业单位离退休支出（项）"], [21]),
        "其他行政事业单位离退休支出（项）", ev, page=21)
