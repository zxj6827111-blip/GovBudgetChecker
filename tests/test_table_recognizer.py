"""
Unit Tests for Table Recognizer

Tests the 9-table recognition logic with various title and column variations.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock
from src.services.table_recognizer import TableRecognizer, TABLE_RECOGNITION_RULES


class TestTableRecognizer:
    """Test suite for table recognition engine."""
    
    @pytest.fixture
    def mock_conn(self):
        """Create mock database connection."""
        conn = AsyncMock()
        return conn
    
    @pytest.fixture
    def recognizer(self, mock_conn):
        """Create TableRecognizer instance."""
        return TableRecognizer(mock_conn)
    
    def test_table_rules_structure(self):
        """Test that all table rules have required fields."""
        for table_code, rule in TABLE_RECOGNITION_RULES.items():
            assert 'title_keywords' in rule
            assert 'required_columns' in rule
            assert 'measure_patterns' in rule
            assert isinstance(rule['title_keywords'], list)
            assert isinstance(rule['required_columns'], list)
            assert isinstance(rule['measure_patterns'], dict)
    
    def test_fin01_title_matching(self, recognizer):
        """Test FIN_01 table recognition with various titles."""
        rule = TABLE_RECOGNITION_RULES['FIN_01_income_expenditure_total']
        
        # Exact match
        headers = ['本年收入', '本年支出', '年初结转']
        score = recognizer._match_table('收入支出总表', headers, rule)
        assert score >= 0.6
        
        # Variation
        score2 = recognizer._match_table('收支决算总表', headers, rule)
        assert score2 >= 0.6
        
        # Mismatch
        score3 = recognizer._match_table('支出决算表', headers, rule)
        assert score3 < 0.6
    
    def test_fin03_column_matching(self, recognizer):
        """Test FIN_03 table column recognition."""
        rule = TABLE_RECOGNITION_RULES['FIN_03_expenditure']
        
        headers = ['合计', '基本支出', '项目支出']
        score = recognizer._match_table('支出决算表', headers, rule)
        assert score >= 0.8  # Should have high confidence
        
        # Missing one column
        headers_partial = ['合计', '基本支出']
        score2 = recognizer._match_table('支出决算表', headers_partial, rule)
        assert score2 < 1.0
    
    def test_find_column(self, recognizer):
        """Test fuzzy column finding."""
        headers = ['合计', '基本支出决算', '项目支出']
        
        assert recognizer._find_column('合计', headers)
        assert recognizer._find_column('基本', headers)
        assert recognizer._find_column('项目', headers)
        assert not recognizer._find_column('收入', headers)
    
    def test_column_mapping_patterns(self, recognizer):
        """Test column mapping creation."""
        rule = TABLE_RECOGNITION_RULES['FIN_03_expenditure']
        headers = ['合计', '基本支出', '项目支出']
        
        mappings = recognizer._create_column_mappings(headers, rule)
        
        assert len(mappings) == 3
        assert any(m.canonical_measure == 'total_actual' for m in mappings)
        assert any(m.canonical_measure == 'basic_actual' for m in mappings)
        assert any(m.canonical_measure == 'project_actual' for m in mappings)
    
    @pytest.mark.asyncio
    async def test_recognize_tables_empty(self, recognizer, mock_conn):
        """Test recognition with no cells."""
        mock_conn.fetch.return_value = []
        
        result = await recognizer.recognize_tables(999)
        
        assert result == []
        mock_conn.fetch.assert_called_once()
    
    def test_header_extraction(self, recognizer):
        """Test header row extraction from cells."""
        cells = [
            {'row_idx': 0, 'col_idx': 0, 'raw_text': '标题'},
            {'row_idx': 1, 'col_idx': 0, 'raw_text': '合计'},
            {'row_idx': 1, 'col_idx': 1, 'raw_text': '基本支出'},
            {'row_idx': 2, 'col_idx': 0, 'raw_text': '数据1'},
        ]
        
        headers = recognizer._extract_headers(cells)
        assert '合计' in headers
        assert '基本支出' in headers
