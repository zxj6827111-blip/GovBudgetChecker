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


def test_multipage_primary_cannot_reassign_other_page_quote():
    # 多页条目即使带 location.page=5，页 6 的精确引文也不能覆盖页 5。
    payload = item("项目支出10.00万元", ["项目支出10.00万元"], [5, 6],
                   refs=[ref(6, "项目支出100.00万元")])
    payload["location"]["page"] = 5
    assert not cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


def test_neighbor_amount_cannot_cover_local_relation():
    # 名称与金额必须同一局部关系：不能拿“基本支出”的 100 万元覆盖“项目支出”。
    payload = item("项目支出10.00万元；基本支出100.00万元",
                   ["项目支出10.00万元；基本支出100.00万元"], [5])
    assert not cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


def test_local_relation_same_clause_positive():
    payload = item("项目支出100.00万元；基本支出10.00万元",
                   ["项目支出100.00万元；基本支出10.00万元"], [5])
    assert cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


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


def _ref_full(page, text, block, span, row="", table=""):
    payload = {"page": page, "text": text, "source_block_id": block,
               "source_span": list(span), "row": row}
    if table:
        payload["table_id"] = table
    return payload


def test_discontinuous_refs_cannot_form_amount_occurrence():
    # R4-01：同页、异行、source_span 不连续的两个片段不能拼出金额出现点。
    payload = item("", [], [5], refs=[
        _ref_full(5, "项目支出", "native:p5:s10:e18", (10, 18), "项目支出"),
        _ref_full(5, "100.00万元", "native:p5:s90:e100", (90, 100), "基本支出")])
    assert not cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


def test_contiguous_refs_with_same_row_can_join():
    # 真实连续片段正控：同一行、span 首尾相接（空白间隙 0）仍应命中。
    payload = item("", [], [5], refs=[
        _ref_full(5, "项目支出", "native:p5:s10:e18", (10, 18), "项目支出"),
        _ref_full(5, "100.00万元", "native:p5:s18:e28", (18, 28), "项目支出")])
    assert cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


def test_same_page_different_blocks_without_span_cannot_join():
    payload = item("", [], [5], refs=[
        {"page": 5, "text": "项目支出", "source_block_id": "native:p5:b1"},
        {"page": 5, "text": "100.00万元", "source_block_id": "native:p5:b2"}])
    assert not cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


def test_same_block_same_row_without_span_can_join():
    payload = item("", [], [5], refs=[
        {"page": 5, "text": "项目支出", "source_block_id": "native:p5:b1", "row": "项目支出"},
        {"page": 5, "text": "100.00万元", "source_block_id": "native:p5:b1", "row": "项目支出"}])
    assert cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


def test_same_block_without_row_cannot_join():
    # R4-01：同 block 但缺行身份时 fail-closed，避免只靠 block id 拼金额。
    payload = item("", [], [5], refs=[
        {"page": 5, "text": "项目支出", "source_block_id": "native:p5:b1"},
        {"page": 5, "text": "100.00万元", "source_block_id": "native:p5:b1"}])
    assert not cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)


def test_adjacent_span_with_conflicting_row_cannot_join():
    # 位置相邻也不能跨行拼：名称和金额必须属于同一行/同一关系。
    payload = item("", [], [5], refs=[
        _ref_full(5, "项目支出", "native:p5:s10:e18", (10, 18), "项目支出"),
        _ref_full(5, "100.00万元", "native:p5:s18:e28", (18, 28), "基本支出")])
    assert not cmp._anchor_covered(payload, "项目支出100.00万元", ev, page=5)
