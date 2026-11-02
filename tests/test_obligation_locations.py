"""补核位置解析的纯逻辑测试。

红线：
- 只输出当前分析代际结构化数据里的页码；没有就返回空列表；
- 长表名命中后不得再把它的短子串当成另一张表（否则会把第 5 页混进财政拨款总表）；
- 同一页同一 bbox 去重，按页码排序。
"""

from __future__ import annotations

from src.schemas.material_detail import CoverageObligationItem
from src.schemas.review_lifecycle import ObligationLocation
from src.services.obligation_locations import merge_locations, resolve_obligation_locations


def _item(
    obligation_id: str,
    *,
    title: str = "",
    detail: str = "",
    basis: str = "",
    checkers: list[str] | None = None,
) -> CoverageObligationItem:
    return CoverageObligationItem(
        obligation_id=obligation_id,
        title=title,
        status="insufficient_data",
        detail=detail,
        basis=basis,
        checkers=list(checkers or []),
    )


def _ingest() -> dict:
    return {
        "reading_import": {
            "tables": [
                {"table_code": "FIN_01_income_expenditure_total", "page": 5, "title": "收入支出决算总表", "bbox": [1, 2, 3, 4]},
                {"table_code": "FIN_04_fiscal_grant_total", "page": 10, "title": "财政拨款收入支出决算总表", "bbox": [5, 6, 7, 8]},
                {"table_code": "FIN_05_general_public_expenditure", "page": 12, "title": "一般公共预算财政拨款支出决算表", "bbox": [9, 10, 11, 12]},
                {"table_code": "FIN_08_gov_fund", "page": 15, "title": "政府性基金预算财政拨款收入支出决算表", "bbox": [13, 14, 15, 16]},
                {"table_code": "FIN_09_state_capital", "page": 16, "title": "国有资本经营预算财政拨款收入支出决算表", "bbox": [17, 18, 19, 20]},
            ]
        }
    }


def test_long_table_name_wins_over_short_substring():
    """财政拨款收入支出决算总表命中后，不能再把“收入支出决算总表”当成另一张表。"""
    item = _item(
        "OBL-CROSS-FISCAL-TOTAL",
        title="财政拨款收支总表合计一致性",
        detail="财政拨款收入支出决算总表 第21行第6列为空白",
    )
    locations = resolve_obligation_locations(item, _ingest(), None)
    assert [location.page for location in locations] == [10]
    assert locations[0].label == "财政拨款收入支出决算总表"
    assert locations[0].bbox == [5, 6, 7, 8]


def test_empty_statement_includes_both_fund_tables():
    """空表/无此项：总表 + 两张空表说明都要能点过去。"""
    item = _item(
        "OBL-STRUCT-EMPTY-STATEMENT",
        title="空表/无此项说明与总表零值确认",
        detail="财政拨款收入支出决算总表 第21行第6列为空白，不能按零计算",
    )
    locations = resolve_obligation_locations(item, _ingest(), None)
    assert [location.page for location in locations] == [10, 15, 16]


def test_t5_uses_table_page_and_finding_evidence_pages():
    """T5：表页 + V33-227 的说明页/表页；同一页同一 bbox 去重。"""
    item = _item(
        "OBL-TXT-T5",
        title="一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        detail="说明中的款“行政事业单位离退休”在表内找不到同名科目",
        checkers=["V33-106", "V33-222", "V33-227"],
    )
    result_row = {
        "rule_findings": [
            {
                "rule_id": "V33-227",
                "tags": ["V33-227"],
                "title": "说明5款级科目名称与T5不一致",
                "evidence": [
                    {"page": 18, "bbox": [21, 22, 23, 24]},
                    {"page": 12, "bbox": [9, 10, 11, 12]},
                ],
                "display": {"page_text": "第18、12页"},
            }
        ]
    }
    locations = resolve_obligation_locations(item, _ingest(), result_row)
    assert [location.page for location in locations] == [12, 18]
    # 第 12 页是表页（bbox 来自表清单），第 18 页是 finding 证据页。
    assert locations[0].label == "一般公共预算财政拨款支出决算表"
    assert locations[1].label == "说明5款级科目名称与T5不一致"


def test_cover_always_has_page_one_and_first_table_page():
    item = _item("OBL-STRUCT-COVER", title="封面要素：报告年度、单位全称、文种", detail="封面年度缺少正文独立依据")
    locations = resolve_obligation_locations(item, _ingest(), None)
    assert [location.page for location in locations] == [1, 5]
    assert locations[0].label == "封面"


def test_no_structured_data_returns_empty_except_cover():
    item = _item("OBL-TXT-T5", title="一般公共预算支出决算表", detail="找不到同名科目")
    assert resolve_obligation_locations(item, None, None) == []
    cover = _item("OBL-STRUCT-COVER", title="封面要素", detail="封面年度缺少正文独立依据")
    assert [location.page for location in resolve_obligation_locations(cover, None, None)] == [1]


def test_row_label_pins_the_continuation_page():
    """总表第 21 行在续页：detail 里的“十九、住房保障支出”要能钉到第 11 页。"""
    item = _item(
        "OBL-CROSS-FISCAL-TOTAL",
        title="财政拨款收支总表合计一致性",
        detail="十九、住房保障支出的government_fund_budget/决算操作数缺失或不唯一",
    )
    result_meta = {
        "structure_view": {
            "page_texts": [
                "收入支出决算总表 十九、住房保障支出 73.53",  # 第 1 页：另一张总表，不应混入
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "财政拨款收入支出决算总表 第10页",  # 第 10 页：表首
                "十九、住房保障支出 73.53 73.53",  # 第 11 页：第21行所在续页
            ]
        }
    }
    locations = resolve_obligation_locations(item, _ingest(), None, result_meta)
    assert [location.page for location in locations] == [10, 11]
    assert locations[1].label == "含“十九、住房保障支出”"


def test_quoted_subject_pins_the_narrative_page():
    """T5 的旧科目名在说明页：引号短语要能钉到第 18 页，而不是只给表页。"""
    item = _item(
        "OBL-TXT-T5",
        title="一般公共预算支出决算表 与 功能分类说明一致（含科目名称）",
        detail="说明中的款“行政事业单位离退休”在表内找不到同名科目",
    )
    page_texts = [""] * 17 + ["社会保障和就业支出（类）行政事业单位离退休（款）"]
    locations = resolve_obligation_locations(
        item,
        _ingest(),
        None,
        {"structure_view": {"page_texts": page_texts}},
    )
    assert [location.page for location in locations] == [12, 18]
    assert locations[1].label == "含“行政事业单位离退休”"


def test_merge_locations_keeps_first_entry_per_page():
    merged = merge_locations(
        [ObligationLocation(page=10, label="总表"), ObligationLocation(page=15, label="基金表")],
        [ObligationLocation(page=10, label="重复"), ObligationLocation(page=11, label="行页")],
    )
    assert [location.page for location in merged] == [10, 11, 15]
    assert merged[0].label == "总表"
