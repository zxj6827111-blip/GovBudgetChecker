"""
Unit Tests for QC Rules (R001-R010)

Tests core QC rule logic with various scenarios including edge cases.
"""

import pytest
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from src.qc.runner_v3 import QCRunnerV3


class TestQCRules:
    """Test suite for QC rules R001-R010."""
    
    @pytest.fixture
    def mock_conn(self):
        """Create mock database connection."""
        conn = AsyncMock()
        return conn
    
    @pytest.fixture
    def runner(self, mock_conn):
        """Create QC runner instance."""
        runner = QCRunnerV3(mock_conn, document_version_id=1)
        runner.run_id = 1
        return runner
    
    def test_check_equal_within_tolerance(self, runner):
        """Test _check_equal with values within tolerance."""
        passed, diff = runner._check_equal(100.00, 100.005, tolerance=0.01)
        assert passed
        assert diff < 0.01
    
    def test_check_equal_outside_tolerance(self, runner):
        """Test _check_equal with values outside tolerance."""
        passed, diff = runner._check_equal(100.00, 100.05, tolerance=0.01)
        assert not passed
        assert diff > 0.01
    
    def test_check_equal_decimal_support(self, runner):
        """Test _check_equal handles Decimal types."""
        passed, diff = runner._check_equal(Decimal('100.00'), 100.005, tolerance=0.01)
        assert passed
    
    def test_check_equal_none_handling(self, runner):
        """Test _check_equal with None values."""
        passed, diff = runner._check_equal(None, 0.0, tolerance=0.01)
        assert passed  # None treated as 0
        
        passed2, diff2 = runner._check_equal(100.0, None, tolerance=0.01)
        assert not passed2
    
    @pytest.mark.asyncio
    async def test_check_table_exists_true(self, runner, mock_conn):
        """Test table existence check when table exists."""
        mock_conn.fetchval.return_value = 5  # 5 facts found
        
        exists = await runner._check_table_exists('FIN_03_expenditure')
        
        assert exists
        mock_conn.fetchval.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_check_table_exists_false(self, runner, mock_conn):
        """Test table existence check when table missing."""
        mock_conn.fetchval.return_value = 0
        
        exists = await runner._check_table_exists('FIN_99_missing')
        
        assert not exists
    
    @pytest.mark.asyncio
    async def test_rule_r009_table_not_found(self, runner, mock_conn):
        """Test R009 returns SKIP when gov fund table missing."""
        # Mock: table doesn't exist
        async def mock_fetchval(query, *args):
            if 'COUNT(*)' in query:
                return 0
            return None
        
        mock_conn.fetchval.side_effect = mock_fetchval
        
        finding = await runner._rule_r009()
        
        assert finding.status == 'skip'
        assert finding.skip_reason == 'table_not_found'
        assert '政府性基金表不存在' in finding.message
    
    @pytest.mark.asyncio
    async def test_rule_r009_table_empty(self, runner, mock_conn):
        """Test R009 returns SKIP when table exists but is empty."""
        call_count = [0]
        
        async def mock_fetchval(query, *args):
            call_count[0] += 1
            if call_count[0] == 1:  # First call: check existence
                return 5  # Table exists
            else:  # Second call: check if empty
                return 0  # But it's empty
        
        mock_conn.fetchval.side_effect = mock_fetchval
        
        finding = await runner._rule_r009()
        
        assert finding.status == 'skip'
        assert finding.skip_reason == 'table_empty'
        assert '为空' in finding.message
    
    def test_tolerance_parameter_loading(self, runner):
        """Test that tolerance parameters are loaded correctly."""
        # Mock rule params
        runner.rule_params = {
            'R001': MagicMock(tolerance=0.01),
            'R006': MagicMock(tolerance=0.05)
        }
        
        params1 = runner._get_params('R001')
        assert params1.tolerance == 0.01
        
        params2 = runner._get_params('R006')
        assert params2.tolerance == 0.05
