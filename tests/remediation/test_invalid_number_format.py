"""原件数字格式缺陷：非法金额必须保留原文与准确页位，不得改名凑等式。

真实来源（2026-11-04 真实材料诊断，第 9 份）：
《支出决算表》表起始页 8、续页物理页 9，三个基本支出格（220/22001/2200150）
原文均印成 ``2.359.46``（小数点重复）。旧回执把它报成“第 8 页映射不完整”，
掩盖了“材料数值不可用”与“系统列映射未完成”的区别。
"""

from src.services.structured_ingest_runner import _reading_review_items


def _cell(uid, page, raw, fragment="t3", table="FIN_03_expenditure"):
    return {"source_cell_id": uid, "page": page, "bbox": [398.04, 90.36, 468.96, 113.88],
            "raw_text": raw, "reason": "fact_unbound", "fragment_uid": fragment,
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
