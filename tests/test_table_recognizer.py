"""
Unit tests for canonical table recognition.
"""

from unittest.mock import AsyncMock

import pytest

from src.services.fiscal_table_rules import NINE_TABLE_RULES, detect_table_code, normalize_text
from src.services.table_recognizer import (
    TABLE_RECOGNITION_RULES,
    TableRecognizer,
)


class TestTableRecognizer:
    @pytest.fixture
    def mock_conn(self):
        return AsyncMock()

    @pytest.fixture
    def recognizer(self, mock_conn):
        return TableRecognizer(mock_conn)

    def test_table_rules_structure(self):
        for table_code, rule in TABLE_RECOGNITION_RULES.items():
            assert "title_keywords" in rule
            assert "required_columns" in rule
            assert "measure_patterns" in rule
            assert isinstance(rule["title_keywords"], list)
            assert isinstance(rule["required_columns"], list)
            assert isinstance(rule["measure_patterns"], dict)
            assert table_code.startswith("FIN_")

    def test_fin01_title_matching(self, recognizer):
        rule = TABLE_RECOGNITION_RULES["FIN_01_income_expenditure_total"]
        headers = ["本年收入", "本年支出", "年初结转和结余"]

        score = recognizer._match_table("收入支出决算总表", headers, rule)
        assert score >= 0.8

        score2 = recognizer._match_table("收支决算总表", headers, rule)
        assert score2 >= 0.75

        score3 = recognizer._match_table("支出决算表", headers, rule)
        assert score3 < 0.75

    def test_fin03_column_matching(self, recognizer):
        rule = TABLE_RECOGNITION_RULES["FIN_03_expenditure"]
        headers = ["合计", "基本支出", "项目支出"]

        score = recognizer._match_table("支出决算表", headers, rule)
        assert score >= 0.8

        headers_partial = ["合计", "基本支出"]
        score2 = recognizer._match_table("支出决算表", headers_partial, rule)
        assert score2 < score

    def test_find_column(self, recognizer):
        headers = ["合计", "基本支出决算数", "项目支出"]

        assert recognizer._find_column("合计", headers)
        assert recognizer._find_column("基本支出", headers)
        assert recognizer._find_column("项目支出", headers)
        assert not recognizer._find_column("财政拨款收入", headers)

    def test_fin06_budget_title_matching(self, recognizer):
        rule = TABLE_RECOGNITION_RULES["FIN_06_basic_expenditure"]
        headers = ["合计", "人员经费", "公用经费"]

        score = recognizer._match_table(
            "2025年部门一般公共预算基本支出部门预算经济分类预算表",
            headers,
            rule,
        )
        assert score >= 0.8

    def test_column_mapping_patterns(self, recognizer):
        headers = ["合计", "基本支出", "项目支出"]

        mappings = recognizer._create_column_mappings(
            headers,
            NINE_TABLE_RULES["FIN_03_expenditure"],
        )

        assert len(mappings) == 3
        assert any(m.canonical_measure == "total_actual" for m in mappings)
        assert any(m.canonical_measure == "basic_actual" for m in mappings)
        assert any(m.canonical_measure == "project_actual" for m in mappings)

    def test_column_mapping_uses_real_measure_columns(self, recognizer):
        header_columns = [
            (0, "财政拨款收入 财政拨款支出"),
            (3, "功能分类科目名称"),
            (4, "合计"),
            (5, "财政拨款收入"),
            (6, "事业收入"),
            (7, "事业单位经营收入"),
            (8, "其他收入"),
        ]

        mappings = recognizer._create_column_mappings(
            header_columns,
            NINE_TABLE_RULES["FIN_02_income"],
            measure_columns={4, 5, 6, 7, 8},
        )

        assert [mapping.source_col_idx for mapping in mappings] == [4, 5, 6, 7, 8]
        assert all(mapping.source_col_idx not in {0, 3} for mapping in mappings)

    @pytest.mark.asyncio
    async def test_recognize_tables_empty(self, recognizer, mock_conn):
        mock_conn.fetch.return_value = []

        result = await recognizer.recognize_tables(999)

        assert result == []
        mock_conn.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_recognize_tables_preserves_page_number(self, recognizer, mock_conn):
        mock_conn.fetch.return_value = [
            {
                "table_code": "UNKNOWN_P27_T0",
                "row_idx": 0,
                "col_idx": 0,
                "raw_text": "2025年部门一般公共预算基本支出部门预算经济分类预算表",
                "page_number": 27,
            },
            {
                "table_code": "UNKNOWN_P27_T0",
                "row_idx": 1,
                "col_idx": 0,
                "raw_text": "合计",
                "page_number": 27,
            },
            {
                "table_code": "UNKNOWN_P27_T0",
                "row_idx": 1,
                "col_idx": 1,
                "raw_text": "人员经费",
                "page_number": 27,
            },
            {
                "table_code": "UNKNOWN_P27_T0",
                "row_idx": 1,
                "col_idx": 2,
                "raw_text": "公用经费",
                "page_number": 27,
            },
        ]

        result = await recognizer.recognize_tables(1)

        assert len(result) == 1
        assert result[0].table_code == "FIN_06_basic_expenditure"
        assert result[0].page_number == 27
        assert "page_number" in mock_conn.fetch.call_args[0][0]

    def test_header_extraction(self, recognizer):
        cells = [
            {"row_idx": 0, "col_idx": 0, "raw_text": "支出决算表"},
            {"row_idx": 1, "col_idx": 0, "raw_text": "合计"},
            {"row_idx": 1, "col_idx": 1, "raw_text": "基本支出"},
            {"row_idx": 2, "col_idx": 0, "raw_text": "教育支出"},
            {"row_idx": 2, "col_idx": 1, "raw_text": "100"},
        ]

        headers = recognizer._extract_headers(cells)
        assert "合计" in headers
        assert "基本支出" in headers

    def test_normalize_text_handles_cjk_radicals_in_titles(self):
        title = "2026年预算单位⽀出预算总表"
        headers = ["合计", "基本支出", "项目支出"]

        assert normalize_text(title).endswith("支出预算总表")
        table_code, confidence = detect_table_code(title=title, headers=headers, source_hint="")

        assert table_code == "FIN_03_expenditure"
        assert confidence >= 0.8

    def test_detect_table_code_prefers_exact_fin01_title_over_generic_expenditure_headers(self):
        table_code, confidence = detect_table_code(
            title="2026年预算单位财务收支预算总表",
            headers=["项目", "预算数", "项目", "基本支出", "项目支出"],
            source_hint="项目 预算数 项目 基本支出 项目支出",
        )

        assert table_code == "FIN_01_income_expenditure_total"
        assert confidence >= 0.85

    def test_detect_table_code_skips_budget_explanation_pages(self):
        table_code, confidence = detect_table_code(
            title="2026年单位预算编制说明",
            headers=["编制单位：上海市普陀区国防动员办公室 单位：元"],
            source_hint="编制单位：上海市普陀区国防动员办公室",
        )

        assert table_code is None
        assert confidence == 0.0
