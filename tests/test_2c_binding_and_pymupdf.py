"""2C：三公声明主体绑定与 pdfplumber 未映射字形占位（GS-2022-02 / GS-2025-03）。

GS-2022-02 的扫描件把「公务用车购置及运行维护费」印成「购臵」；旧的主体
识别只认「购置」，于是「完成预算的55.62%」「占78.83%」被绑到前一个主体
「因公出国（境）费」。GS-2025-03 的 pdfplumber 文本在数字与量词之间夹了
``(cid:1)`` 占位，数量要素匹配前必须剔除。两处都只做识别归一，不改原文数字。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.engine.native_three_public_ratios import subject_before  # noqa: E402
from src.engine.three_public_counts import (  # noqa: E402
    _COUNT_SPECS,
    _compact_text,
)


def test_ocr_confusable_purchase_char_still_binds_vehicle_subject():
    text = (
        "因公出国（境）费决算为0.00万元，完成预算的0.00%；"
        "公务用车购臵及运行维护费支出决算为26.92万元，完成预算的"
    )
    indicator, _ = subject_before(text)
    assert indicator == "vehicle_purchase_operation"


def test_each_completion_claim_binds_its_own_nearest_subject():
    prefix = (
        "三公经费财政拨款支出年初预算为111.90万元，支出决算为34.15万元，"
        "完成预算的30.52%，其中：因公出国（境）费决算为0.00万元，完成预算的0.00%；"
    )
    assert subject_before(prefix)[0] == "overseas"
    vehicle = prefix + "公务用车购臵及运行维护费支出决算为26.92万元，完成预算的"
    assert subject_before(vehicle)[0] == "vehicle_purchase_operation"
    reception = vehicle + "55.62%；公务接待费支出决算为7.23万元，完成预算的"
    assert subject_before(reception)[0] == "reception"


def test_share_claim_binds_vehicle_after_ocr_variant():
    prefix = (
        "三公经费财政拨款支出决算中，因公出国（境）费支出决算0.00万元，占0.00%；"
        "公务用车购臵及运行维护费支出决算26.92万元，占"
    )
    assert subject_before(prefix)[0] == "vehicle_purchase_operation"


def test_cid_placeholder_removed_before_quantity_matching():
    compact = _compact_text("全年安排因公出国（境）团组(cid:1)2 个、累计(cid:1)25 人次")
    assert compact == "全年安排因公出国（境）团组2个、累计25人次"
    groups = {key: pattern for key, _label, pattern in _COUNT_SPECS[1][3]}["overseas_groups"]
    people = {key: pattern for key, _label, pattern in _COUNT_SPECS[1][3]}["overseas_people"]
    assert groups.search(compact)
    assert people.search(compact)


def test_cid_removal_keeps_disclosure_text_missing_when_absent():
    compact = _compact_text("公务接待费支出决算(cid:1)15.45 万元。主要用于公务接待。")
    assert compact == "公务接待费支出决算15.45万元。主要用于公务接待。"
    batches = {key: pattern for key, _label, pattern in _COUNT_SPECS[2][3]}["reception_batches"]
    assert not batches.search(compact)


def test_structure_class_check_importable_and_noops_without_fact_context():
    from src.engine.rules_v33 import _structure_class_name_issues

    assert _structure_class_name_issues(None, object(), {}) == []


def test_narrative_name_similarity_locates_small_differences_only():
    from src.engine.rules_v33 import _functional_names_similar

    assert _functional_names_similar("政府办公厅及相关机构事务", "政府办公厅（室）及相关机构事务")
    assert _functional_names_similar("其他行政事业单位离退休支出", "其他行政事业单位养老支出")
    assert _functional_names_similar("卫生及健康支出", "卫生健康支出")
    assert _functional_names_similar("行政运行费", "行政运行")
    assert _functional_names_similar("其它行政事业单位养老支出", "其他行政事业单位养老支出")
    assert not _functional_names_similar("一般公共服务支出", "公共安全支出")
