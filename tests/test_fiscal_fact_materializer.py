"""
Tests for fiscal fact materialization.
"""

from unittest.mock import AsyncMock

from src.services.fiscal_fact_materializer import FiscalFactMaterializer


def test_materializer_builds_hierarchical_facts():
    materializer = FiscalFactMaterializer(AsyncMock())
    cells = [
        {"id": 1, "row_idx": 0, "col_idx": 0, "raw_text": "支出决算表"},
        {"id": 2, "row_idx": 1, "col_idx": 0, "raw_text": "功能分类科目编码"},
        {"id": 3, "row_idx": 1, "col_idx": 1, "raw_text": "科目名称"},
        {"id": 4, "row_idx": 1, "col_idx": 2, "raw_text": "合计"},
        {"id": 5, "row_idx": 1, "col_idx": 3, "raw_text": "基本支出"},
        {"id": 6, "row_idx": 1, "col_idx": 4, "raw_text": "项目支出"},
        {"id": 7, "row_idx": 2, "col_idx": 0, "raw_text": "201"},
        {"id": 8, "row_idx": 2, "col_idx": 1, "raw_text": "教育支出"},
        {"id": 9, "row_idx": 2, "col_idx": 2, "raw_text": "100", "numeric_value": 100, "page_number": 12, "confidence": 0.96},
        {"id": 10, "row_idx": 2, "col_idx": 3, "raw_text": "60", "numeric_value": 60, "page_number": 12, "confidence": 0.96},
        {"id": 11, "row_idx": 2, "col_idx": 4, "raw_text": "40", "numeric_value": 40, "page_number": 12, "confidence": 0.96},
        {"id": 12, "row_idx": 3, "col_idx": 0, "raw_text": "20101"},
        {"id": 13, "row_idx": 3, "col_idx": 1, "raw_text": "普通教育"},
        {"id": 14, "row_idx": 3, "col_idx": 2, "raw_text": "80", "numeric_value": 80, "page_number": 12, "confidence": 0.96},
        {"id": 15, "row_idx": 3, "col_idx": 3, "raw_text": "50", "numeric_value": 50, "page_number": 12, "confidence": 0.96},
        {"id": 16, "row_idx": 3, "col_idx": 4, "raw_text": "30", "numeric_value": 30, "page_number": 12, "confidence": 0.96},
        {"id": 17, "row_idx": 4, "col_idx": 0, "raw_text": "合计"},
        {"id": 18, "row_idx": 4, "col_idx": 2, "raw_text": "180", "numeric_value": 180, "page_number": 12, "confidence": 0.96},
    ]
    mappings = [
        {"source_col_idx": 2, "source_col_name": "合计", "canonical_measure": "total_actual", "confidence": 0.95},
        {"source_col_idx": 3, "source_col_name": "基本支出", "canonical_measure": "basic_actual", "confidence": 0.95},
        {"source_col_idx": 4, "source_col_name": "项目支出", "canonical_measure": "project_actual", "confidence": 0.95},
    ]

    facts = materializer.build_facts_for_table(
        table_code="FIN_03_expenditure",
        cells=cells,
        mappings=mappings,
        table_confidence=0.94,
    )

    assert len(facts) == 7

    top_level = next(
        fact for fact in facts
        if fact.classification_code == "201" and fact.measure == "total_actual"
    )
    assert top_level.classification_level == 1
    assert top_level.parent_classification_code is None
    assert top_level.hierarchy_path == ["201"]
    assert top_level.source_page_number == 12

    second_level = next(
        fact for fact in facts
        if fact.classification_code == "20101" and fact.measure == "basic_actual"
    )
    assert second_level.classification_level == 2
    assert second_level.parent_classification_code == "201"

    total_row = next(
        fact for fact in facts
        if fact.classification_code == "total"
    )
    assert total_row.classification_name == "合计"
    assert total_row.classification_level == 0


def test_materializer_combines_split_classification_code_columns():
    materializer = FiscalFactMaterializer(AsyncMock())
    cells = [
        {"id": 1, "row_idx": 0, "col_idx": 0, "raw_text": "项目"},
        {"id": 2, "row_idx": 0, "col_idx": 1, "raw_text": "科目名称"},
        {"id": 3, "row_idx": 0, "col_idx": 2, "raw_text": "合计"},
        {"id": 4, "row_idx": 1, "col_idx": 0, "raw_text": "201"},
        {"id": 5, "row_idx": 1, "col_idx": 1, "raw_text": "03"},
        {"id": 6, "row_idx": 1, "col_idx": 2, "raw_text": "01"},
        {"id": 7, "row_idx": 1, "col_idx": 3, "raw_text": "行政运行"},
        {"id": 8, "row_idx": 1, "col_idx": 4, "raw_text": "12029501.84", "numeric_value": 12029501.84, "page_number": 9, "confidence": 0.95},
    ]
    mappings = [
        {"source_col_idx": 4, "source_col_name": "合计", "canonical_measure": "total_actual", "confidence": 0.95},
    ]

    facts = materializer.build_facts_for_table(
        table_code="FIN_02_income",
        cells=cells,
        mappings=mappings,
        table_confidence=0.93,
    )

    assert len(facts) == 1
    assert facts[0].classification_code == "2010301"
    assert facts[0].classification_name == "行政运行"
    assert facts[0].classification_level == 3
    assert facts[0].parent_classification_code == "20103"
