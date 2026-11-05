"""原件数字格式缺陷：非法金额必须保留原文与准确页位，不得改名凑等式。

真实来源（2026-11-04 真实材料诊断，第 9 份）：
《支出决算表》表起始页 8、续页物理页 9，三个基本支出格（220/22001/2200150）
原文均印成 ``2.359.46``（小数点重复）。旧回执把它报成“第 8 页映射不完整”，
掩盖了“材料数值不可用”与“系统列映射未完成”的区别。
"""

from src.services.structured_ingest_runner import _reading_review_items


def _cell(uid, page, raw, fragment="t3", table="FIN_03_expenditure", reason="fact_unbound"):
    return {"source_cell_id": uid, "page": page, "bbox": [398.04, 90.36, 468.96, 113.88],
            "raw_text": raw, "reason": reason, "fragment_uid": fragment,
            "table_code": table}


def _table(page=8, count=3, arithmetic=None):
    return {"fragment_uid": "t3", "table_code": "FIN_03_expenditure", "title": "支出决算表",
            "page": page, "unresolved_money_cell_count": count,
            "reasons": {"fact_unbound": count}, "arithmetic_conflicts": arithmetic or []}


def test_double_decimal_cells_become_a_located_format_defect_on_their_own_page():
    quality = {
        "gaps": [],
        "mapping_incomplete_tables": [_table()],
        "unbound_money_cells": [
            _cell("a191b44bee5bb1e8:r0c3", 9, "2.359.46"),
            _cell("a191b44bee5bb1e8:r1c3", 9, "2.359.46"),
            _cell("a191b44bee5bb1e8:r2c3", 9, "2.359.46"),
        ],
    }
    items = _reading_review_items(quality)
    assert [item["type"] for item in items] == ["invalid_number_format"]
    defect = items[0]
    assert defect["page_number"] == 9 and defect["page_numbers"] == [9]
    assert len(defect["cells"]) == 3
    assert {c["raw_text"] for c in defect["cells"]} == {"2.359.46"}
    assert all(c["bbox"] == [398.04, 90.36, 468.96, 113.88] for c in defect["cells"])
    assert "原件数字格式缺陷" in defect["message"] and "保持材料阻断" in defect["message"]
    assert defect["recommended_action"] == "correct_original_number_format"


def test_format_defect_is_not_dressed_up_as_a_column_mapping_failure():
    quality = {
        "gaps": [],
        "mapping_incomplete_tables": [_table()],
        "unbound_money_cells": [_cell("t3:r0c3", 9, "2.359.46")],
    }
    items = _reading_review_items(quality)
    assert not [i for i in items if i["type"] == "table_mapping_incomplete"]


def test_valid_unbound_cells_keep_the_mapping_item_and_the_invalid_ones_are_split_out():
    quality = {
        "gaps": [],
        "mapping_incomplete_tables": [_table(count=2)],
        "unbound_money_cells": [
            _cell("t3:r0c3", 9, "2.359.46"),
            _cell("t3:r3c2", 9, "1,860.33"),
        ],
    }
    items = _reading_review_items(quality)
    types = [i["type"] for i in items]
    assert types == ["invalid_number_format", "table_mapping_incomplete"]
    mapping = items[1]
    assert [c["raw_text"] for c in mapping["cells"]] == ["1,860.33"]
    assert "1 个金额格" in mapping["message"]


def test_normal_decimals_are_not_mistaken_for_format_defects():
    quality = {
        "gaps": [],
        "mapping_incomplete_tables": [_table(count=2)],
        "unbound_money_cells": [
            _cell("t3:r1c2", 8, "3,143.97"),
            _cell("t3:r2c2", 8, "1860.33"),
        ],
    }
    items = _reading_review_items(quality)
    assert [i["type"] for i in items] == ["table_mapping_incomplete"]
    assert len(items[0]["cells"]) == 2


# ---------------------------------------------------------------------------
# 2026-11-04 独立复审 RF-01：分类准入与独立原因保留
# ---------------------------------------------------------------------------

def test_two_amounts_joined_by_newline_are_an_extraction_anomaly():
    """``1.00\\n2.00`` 是两个金额被拼到一起，不是纸面印了一个错数。"""
    quality = {
        "gaps": [],
        "mapping_incomplete_tables": [_table(count=1)],
        "unbound_money_cells": [_cell("t3:r0c3", 9, "1.00\n2.00")],
    }
    items = _reading_review_items(quality)
    assert not [i for i in items if i["type"] == "invalid_number_format"]
    assert [i["type"] for i in items] == ["table_mapping_incomplete"]


def test_space_joined_amounts_are_not_a_single_bad_lexeme():
    quality = {
        "gaps": [],
        "mapping_incomplete_tables": [_table(count=1)],
        "unbound_money_cells": [_cell("t3:r0c3", 9, "1.00 2.00")],
    }
    items = _reading_review_items(quality)
    assert not [i for i in items if i["type"] == "invalid_number_format"]


def test_trailing_unit_does_not_split_a_single_lexeme():
    quality = {
        "gaps": [],
        "mapping_incomplete_tables": [_table(count=1)],
        "unbound_money_cells": [_cell("t3:r0c3", 9, "2.359.46 万元")],
    }
    items = _reading_review_items(quality)
    assert [i["type"] for i in items] == ["invalid_number_format"]


def test_independent_binding_conflict_survives_format_classification():
    """格式错误与身份冲突可以并存：归类格式缺陷不得删掉独立待办。"""
    quality = {
        "gaps": [],
        "mapping_incomplete_tables": [{
            **_table(count=1), "reasons": {"fact_binding_conflict": 1}}],
        "unbound_money_cells": [
            _cell("t3:r0c3", 9, "2.359.46", reason="fact_binding_conflict")],
    }
    items = _reading_review_items(quality)
    types = [i["type"] for i in items]
    assert "invalid_number_format" not in types
    assert types == ["table_mapping_incomplete"]
    assert items[0]["reason"] == "fact_binding_conflict"
