"""
QC (Quality Control) module for fiscal budget checking.

This module provides:
- QC rule execution engine (V1 and V2)
- Evidence-based findings storage
- Drilldown and report generation
- API endpoints for QC operations
"""

from src.qc.runner import QCRunner, run_qc_check, Finding
from src.qc.runner_v2 import QCRunnerV2, run_qc_check_v2, get_finding_drilldown, generate_qc_report_markdown

__all__ = [
    'QCRunner', 'run_qc_check', 'Finding',
    'QCRunnerV2', 'run_qc_check_v2', 'get_finding_drilldown', 'generate_qc_report_markdown'
]
