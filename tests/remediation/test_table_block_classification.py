"""表格阻断分类：原件值非法（材料补正）与真实绑定/读取失败（技术处理）分开。

真实来源（2026-11-04 复审 RF-02a，第 9 份）：物理页 9 三个基本支出格原文
``2.359.46``，旧口径在 OBL-INTERNAL-T3-TOTAL 里仍写“第 8 页映射失败/tech_fix”，
与复核项里的“原件格式缺陷”互相矛盾。
"""

from types import SimpleNamespace

from src.engine.table_admission_policy import (
    build_message,
    classify_blocked_tables,
    register_material_format_gap,
)
from src.engine.diagnostic_fields import lookup_failure, finalize


def _receipt(cells, table_reasons, arithmetic=None):
    return {
        "mapping_incomplete_tables": [{
            "fragment_uid": "t3", "table_code": "FIN_03_expenditure", "title": "支出决算表",
            "page": 8, "unresolved_money_cell_count": sum(table_reasons.values()),
            "reasons": dict(table_reasons), "arithmetic_conflicts": arithmetic or [],
        }],
        "unbound_money_cells": [
            {"source_cell_id": uid, "fragment_uid": "t3", "table_code": "FIN_03_expenditure",
             "raw_text": raw, "page": page, "bbox": [1, 2, 3, 4], "reason": reason,
             "row": index, "column": 3}
            for index, (uid, raw, page, reason) in enumerate(cells)
        ],
    }


def test_original_number_format_only_is_material_not_technical():
    receipt = _receipt(
        [("t3:r0c3", "2.359.46", 9, "fact_unbound"),
         ("t3:r1c3", "2.359.46", 9, "fact_unbound"),
         ("t3:r2c3", "2.359.46", 9, "fact_unbound")],
        {"fact_unbound": 3},
    )
    blocked = receipt["mapping_incomplete_tables"]
    material, technical, empty = classify_blocked_tables(receipt, blocked)
    assert len(material) == 1 and not technical and not empty
    message = build_message(receipt, material, technical)
    assert "原件金额格式非法" in message
    assert "第9页FIN_03_expenditure" in message
    assert "a191b44bee5bb1e8:r0c3" in message or "t3:r" in message
    assert "来源表映射/读取未完成" not in message


def test_material_format_gap_registers_cell_level_reason_and_human_action():
    receipt = _receipt([("t3:r0c3", "2.359.46", 9, "fact_unbound")], {"fact_unbound": 1})
    material, technical, empty = classify_blocked_tables(receipt, receipt["mapping_incomplete_tables"])
    message = build_message(receipt, material, technical)
    document = SimpleNamespace()
    register_material_format_gap(document, material, message)
    entries = lookup_failure(document, message)
    assert entries, "材料格式缺陷必须登记为可查询的结构化事实"
    final = finalize(entries[0])
    assert final["reason_codes"] == ["cell_unbound"]
    assert final["blocked_by"][0]["dimension"] == "cell"
    assert final["blocked_by"][0]["ref"].startswith("第9页FIN_03_expenditure:")
    assert final["next_action"] == "human_confirm"


def test_independent_conflict_keeps_technical_blocking_even_with_format_cells():
    receipt = _receipt(
        [("t3:r0c3", "2.359.46", 9, "fact_unbound"),
         ("t3:r1c3", "1,860.33", 9, "fact_binding_conflict")],
        {"fact_unbound": 1, "fact_binding_conflict": 1},
    )
    material, technical, empty = classify_blocked_tables(receipt, receipt["mapping_incomplete_tables"])
    assert not material and len(technical) == 1 and not empty
    message = build_message(receipt, material, technical)
    assert "来源表映射/读取未完成" in message


def test_unresolved_count_mismatch_is_not_treated_as_pure_format_defect():
    """上游计数与格数对不上时不能宣布材料缺陷，避免掩盖未列出的卡点。"""
    receipt = _receipt([("t3:r0c3", "2.359.46", 9, "fact_unbound")], {"fact_unbound": 4})
    material, technical, empty = classify_blocked_tables(receipt, receipt["mapping_incomplete_tables"])
    assert not material and len(technical) == 1 and not empty


def test_cross_process_text_recovers_cell_level_material_reason():
    """规则在子进程登记的结构化事实不会跨进程，父进程须能从文本恢复。"""
    from src.engine.diagnostic_fields import rule_level_entry

    detail = ("原件金额格式非法：第9页FIN_03_expenditure 3 个金额格原文「2.359.46」"
              "无法解析为数值，数值不确定，需回原件补正；依赖勾稽保持材料阻断；"
              "结构：阻断 第9页FIN_03_expenditure:a191b44bee5bb1e8:r0c3（单元格未绑定唯一事实）")
    entry = rule_level_entry(detail, rule_id="V33-241")
    assert entry["reason_codes"] == ["cell_unbound"]
    assert entry["blocked_by"][0]["dimension"] == "cell"
    assert entry["blocked_by"][0]["ref"] == "第9页FIN_03_expenditure:a191b44bee5bb1e8:r0c3"
    final = finalize(entry)
    assert final["next_action"] == "human_confirm"


def test_pipeline_boundary_rewrites_material_deferral_and_registers_cell_gap():
    """冻结文件不可改：改写发生在 pipeline 捕获 RuleDeferred 之后。"""
    from src.engine.diagnostic_fields import finalize, lookup_failure
    from src.engine.table_admission_policy import reclassify_admission_deferral

    receipt = _receipt([("t3:r0c3", "2.359.46", 9, "fact_unbound")], {"fact_unbound": 1})
    document = SimpleNamespace(_table_admission_receipt=(("k"), receipt))
    original = "来源表映射/读取未完成：第8页FIN_03_expenditure {'fact_unbound': 1}"
    rewritten = reclassify_admission_deferral(document, "V33-241", original)
    assert rewritten and rewritten.startswith("原件金额格式非法")
    assert "第9页FIN_03_expenditure" in rewritten
    entries = lookup_failure(document, rewritten)
    assert entries, "改写后的文本必须能取回单元格级结构化事实"
    final = finalize(entries[0])
    assert final["reason_codes"] == ["cell_unbound"]
    assert final["next_action"] == "human_confirm"


def test_pipeline_boundary_keeps_technical_wording_for_mixed_conflict():
    from src.engine.table_admission_policy import reclassify_admission_deferral

    receipt = _receipt(
        [("t3:r0c3", "2.359.46", 9, "fact_unbound"),
         ("t3:r1c3", "1,860.33", 9, "fact_binding_conflict")],
        {"fact_unbound": 1, "fact_binding_conflict": 1},
    )
    document = SimpleNamespace(_table_admission_receipt=(("k"), receipt))
    original = "来源表映射/读取未完成：第8页FIN_03_expenditure {'fact_unbound': 1}"
    rewritten = reclassify_admission_deferral(document, "V33-241", original)
    assert rewritten and "来源表映射/读取未完成" in rewritten
    assert "第9页FIN_03_expenditure" in rewritten


def test_empty_table_without_cells_is_material_not_technical():
    """第 4/1 份形态：没有任何未绑定格与逐格原因，却被标映射不完整。"""
    from src.engine.diagnostic_fields import finalize, lookup_failure
    from src.engine.table_admission_policy import reclassify_admission_deferral

    receipt = {"mapping_incomplete_tables": [{
        "fragment_uid": "t7", "table_code": "FIN_07_three_public", "title": "三公经费表",
        "page": 11, "unresolved_money_cell_count": 0, "reasons": {},
        "arithmetic_conflicts": []}], "unbound_money_cells": []}
    document = SimpleNamespace(_table_admission_receipt=(("k"), receipt))
    rewritten = reclassify_admission_deferral(
        document, "V33-114", "来源表映射/读取未完成：第11页FIN_07_three_public {}")
    assert rewritten and rewritten.startswith("原件表格无可用金额数据")
    final = finalize(lookup_failure(document, rewritten)[0])
    assert final["reason_codes"] == ["table_missing"]
    assert final["next_action"] == "human_confirm"
    assert final["blocked_by"][0]["dimension"] == "instance_uid"


def test_no_usable_amount_facts_is_material_not_mapping_failure():
    """第 1/4 份：规则说“表没有可用金额事实”，应归材料侧 table_missing。"""
    from src.engine.diagnostic_fields import finalize, rule_level_entry

    entry = rule_level_entry(
        "一般公共预算财政拨款“三公”经费支出决算表 没有可用金额事实", rule_id="CMM-001")
    assert entry["reason_codes"] == ["table_missing"]
    assert entry["blocked_by"][0]["dimension"] == "instance_uid"
    assert finalize(entry)["next_action"] == "human_confirm"
