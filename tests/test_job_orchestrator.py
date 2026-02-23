"""
Unit Tests for Job Orchestrator

Tests the 4-stage job pipeline and status tracking.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from src.services.job_orchestrator import JobOrchestrator


class TestJobOrchestrator:
    """Test suite for job orchestration."""
    
    @pytest.fixture
    def mock_conn(self):
        """Create mock database connection."""
        conn = AsyncMock()
        return conn
    
    @pytest.fixture
    def orchestrator(self, mock_conn):
        """Create JobOrchestrator instance."""
        return JobOrchestrator(mock_conn)
    
    @pytest.mark.asyncio
    async def test_create_job(self, orchestrator, mock_conn):
        """Test job creation."""
        mock_conn.fetchval.return_value = 123  # job_id
        
        job_id = await orchestrator.create_job(document_version_id=1)
        
        assert job_id == 123
        mock_conn.fetchval.assert_called_once()
        # Verify INSERT query
        call_args = mock_conn.fetchval.call_args[0][0]
        assert 'INSERT INTO jobs' in call_args
    
    @pytest.mark.asyncio
    async def test_update_job_status(self, orchestrator, mock_conn):
        """Test job status update."""
        await orchestrator.update_job_status(job_id=1, status='running')
        
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        assert 'UPDATE jobs' in call_args[0]
        assert call_args[1] == 'running'
    
    @pytest.mark.asyncio
    async def test_complete_job(self, orchestrator, mock_conn):
        """Test job completion."""
        await orchestrator.complete_job(job_id=1, report_path='/path/to/report.pdf')
        
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        assert 'completed' in call_args[0]
        assert '/path/to/report.pdf' in call_args
    
    @pytest.mark.asyncio
    async def test_fail_job(self, orchestrator, mock_conn):
        """Test job failure handling."""
        await orchestrator.fail_job(job_id=1, reason='Test error')
        
        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        assert 'failed' in call_args[0]
        assert 'Test error' in call_args
    
    @pytest.mark.asyncio
    async def test_get_stage_status_completed(self, orchestrator):
        """Test stage status for completed stage."""
        job_record = {
            'current_stage': 'qc',
            'status': 'running',
            'parse_log': '{"cells_count": 100}',
            'materialize_log': '{"facts_count": 50}',
            'qc_run_id': None
        }
        
        status = await orchestrator._get_stage_status(job_record, 'parse')
        
        assert status['status'] == 'completed'
    
    @pytest.mark.asyncio
    async def test_get_stage_status_current(self, orchestrator):
        """Test stage status for current stage."""
        job_record = {
            'current_stage': 'qc',
            'status': 'running',
            'parse_log': None,
            'materialize_log': None,
            'qc_run_id': None
        }
        
        status = await orchestrator._get_stage_status(job_record, 'qc')
        
        assert status['status'] == 'running'
    
    @pytest.mark.asyncio
    async def test_get_stage_status_pending(self, orchestrator):
        """Test stage status for pending stage."""
        job_record = {
            'current_stage': 'parse',
            'status': 'running',
            'parse_log': None,
            'materialize_log': None,
            'qc_run_id': None
        }
        
        status = await orchestrator._get_stage_status(job_record, 'report')
        
        assert status['status'] == 'pending'
