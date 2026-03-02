"""
QC Rule Engine for Fiscal Budget Checking.

This module executes quality control rules against fiscal data and stores findings.

Rules implemented:
- R001: Total expenditure = Basic + Project
- R002: Function L1 sum = Total
- R003: Total income = Total expenditure
- R004: Economic classification sum = Basic expenditure total
- R005: Three-public total = sum of sub-items
"""

import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
from decimal import Decimal
import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    """Represents a QC finding result."""
    rule_key: str
    status: str  # 'pass', 'fail', 'skip'
    lhs_value: str
    rhs_value: str
    diff: float
    evidence_cells: List[int]
    message: str


class QCRunner:
    """
    QC Rule Runner for fiscal document verification.
    
    Usage:
        runner = QCRunner(conn, document_version_id)
        run_id = await runner.execute()
    """
    
    def __init__(self, conn: asyncpg.Connection, document_version_id: int):
        self.conn = conn
        self.document_version_id = document_version_id
        self.run_id: Optional[int] = None
        self.tolerance = 0.01  # Default tolerance
    
    async def execute(self) -> int:
        """Execute all QC rules and return run_id."""
        # Create QC run
        self.run_id = await self.conn.fetchval("""
            INSERT INTO qc_runs_v2 (document_version_id)
            VALUES ($1) RETURNING id
        """, self.document_version_id)
        
        logger.info(f"Created QC run {self.run_id} for version {self.document_version_id}")
        
        # Execute all rules
        findings = []
        findings.append(await self._rule_r001())
        findings.append(await self._rule_r002())
        findings.append(await self._rule_r003())
        findings.append(await self._rule_r004())
        findings.append(await self._rule_r005())
        
        # Write findings
        for finding in findings:
            if finding:
                await self._write_finding(finding)
        
        # Mark run as complete
        await self.conn.execute("""
            UPDATE qc_runs_v2 SET finished_at = NOW() WHERE id = $1
        """, self.run_id)
        
        logger.info(f"QC run {self.run_id} completed with {len(findings)} findings")
        return self.run_id
    
    async def _get_fact(self, table_code: str, classification_code: str, measure: str) -> Optional[float]:
        """Get a single fact value."""
        result = await self.conn.fetchval("""
            SELECT amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1
              AND table_code = $2
              AND classification_code = $3
              AND measure = $4
            LIMIT 1
        """, self.document_version_id, table_code, classification_code, measure)
        return float(result) if result is not None else None
    
    async def _get_fact_sum(self, table_code: str, measure: str, 
                            classification_code_pattern: Optional[str] = None,
                            classification_code_length: Optional[int] = None) -> float:
        """Get sum of facts with optional filtering."""
        if classification_code_length:
            result = await self.conn.fetchval("""
                SELECT COALESCE(SUM(amount), 0) FROM fact_fiscal_line_items
                WHERE document_version_id = $1
                  AND table_code = $2
                  AND measure = $3
                  AND LENGTH(classification_code) = $4
                  AND classification_code ~ '^[0-9]+$'
            """, self.document_version_id, table_code, measure, classification_code_length)
        elif classification_code_pattern:
            result = await self.conn.fetchval("""
                SELECT COALESCE(SUM(amount), 0) FROM fact_fiscal_line_items
                WHERE document_version_id = $1
                  AND table_code = $2
                  AND measure = $3
                  AND classification_code ~ $4
            """, self.document_version_id, table_code, measure, classification_code_pattern)
        else:
            result = await self.conn.fetchval("""
                SELECT COALESCE(SUM(amount), 0) FROM fact_fiscal_line_items
                WHERE document_version_id = $1
                  AND table_code = $2
                  AND measure = $3
            """, self.document_version_id, table_code, measure)
        return float(result) if result else 0.0
    
    async def _get_evidence_cells(self, table_code: str, row_idx: Optional[int] = None) -> List[int]:
        """Get cell IDs for evidence. If row_idx is None, get summary row cells."""
        if row_idx is not None:
            rows = await self.conn.fetch("""
                SELECT id FROM fiscal_table_cells
                WHERE document_version_id = $1 AND table_code = $2 AND row_idx = $3
                ORDER BY col_idx
            """, self.document_version_id, table_code, row_idx)
        else:
            # Get last row (typically summary row) or row with "合计"
            rows = await self.conn.fetch("""
                SELECT id FROM fiscal_table_cells
                WHERE document_version_id = $1 AND table_code = $2
                  AND (raw_text LIKE '%合计%' OR row_idx = (
                    SELECT MAX(row_idx) FROM fiscal_table_cells 
                    WHERE document_version_id = $1 AND table_code = $2
                  ))
                ORDER BY row_idx, col_idx LIMIT 10
            """, self.document_version_id, table_code)
        return [row['id'] for row in rows]
    
    async def _write_finding(self, finding: Finding):
        """Write a finding to the database."""
        await self.conn.execute("""
            INSERT INTO qc_findings_v2 (run_id, rule_key, status, lhs_value, rhs_value, diff, evidence_cells, message)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        """, 
            self.run_id,
            finding.rule_key,
            finding.status,
            finding.lhs_value,
            finding.rhs_value,
            finding.diff,
            finding.evidence_cells,
            finding.message
        )
    
    def _check_equal(self, lhs: float, rhs: float, tolerance: float = None) -> Tuple[bool, float]:
        """Check if two values are equal within tolerance."""
        tol = tolerance if tolerance is not None else self.tolerance
        diff = abs(lhs - rhs)
        return diff <= tol, diff
    
    # ========================================================================
    # Rule Implementations
    # ========================================================================
    
    async def _rule_r001(self) -> Finding:
        """R001: 总支出 = 基本支出 + 项目支出"""
        table_code = 'FIN_03_expenditure'
        
        total = await self._get_fact(table_code, '合计', 'total_actual')
        basic = await self._get_fact(table_code, '合计', 'basic_actual')
        project = await self._get_fact(table_code, '合计', 'project_actual')
        
        if total is None or basic is None or project is None:
            return Finding(
                rule_key='R001', status='skip',
                lhs_value=str(total), rhs_value=f"{basic}+{project}",
                diff=0, evidence_cells=[],
                message='缺少必要数据'
            )
        
        expected = basic + project
        passed, diff = self._check_equal(total, expected)
        evidence = await self._get_evidence_cells(table_code)
        
        return Finding(
            rule_key='R001',
            status='pass' if passed else 'fail',
            lhs_value=str(total),
            rhs_value=f"{basic}+{project}={expected}",
            diff=diff,
            evidence_cells=evidence,
            message=f"总支出({total}) {'=' if passed else '≠'} 基本({basic})+项目({project})"
        )
    
    async def _rule_r002(self) -> Finding:
        """R002: 功能分类一级汇总 = 合计"""
        table_code = 'FIN_03_expenditure'
        
        # Sum of L1 classifications (3-digit codes)
        l1_sum = await self._get_fact_sum(table_code, 'total_actual', classification_code_length=3)
        total = await self._get_fact(table_code, '合计', 'total_actual')
        
        if total is None:
            return Finding(
                rule_key='R002', status='skip',
                lhs_value=str(l1_sum), rhs_value=str(total),
                diff=0, evidence_cells=[],
                message='缺少合计数据'
            )
        
        passed, diff = self._check_equal(l1_sum, total)
        evidence = await self._get_evidence_cells(table_code)
        
        return Finding(
            rule_key='R002',
            status='pass' if passed else 'fail',
            lhs_value=str(l1_sum),
            rhs_value=str(total),
            diff=diff,
            evidence_cells=evidence,
            message=f"功能分类一级汇总({l1_sum}) {'=' if passed else '≠'} 合计({total})"
        )
    
    async def _rule_r003(self) -> Finding:
        """R003: 总收入 = 总支出"""
        table_code = 'FIN_01_income_expenditure_total'
        
        # Find income total
        income = await self.conn.fetchval("""
            SELECT amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1
              AND table_code = $2
              AND classification_name LIKE '%本年收入合计%'
              AND measure = 'actual'
            LIMIT 1
        """, self.document_version_id, table_code)
        
        # Find expenditure total
        expenditure = await self.conn.fetchval("""
            SELECT amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1
              AND table_code = $2
              AND classification_name LIKE '%本年支出合计%'
              AND measure = 'actual'
            LIMIT 1
        """, self.document_version_id, table_code)
        
        if income is None or expenditure is None:
            return Finding(
                rule_key='R003', status='skip',
                lhs_value=str(income), rhs_value=str(expenditure),
                diff=0, evidence_cells=[],
                message='缺少收入或支出数据'
            )
        
        income = float(income)
        expenditure = float(expenditure)
        passed, diff = self._check_equal(income, expenditure)
        evidence = await self._get_evidence_cells(table_code)
        
        return Finding(
            rule_key='R003',
            status='pass' if passed else 'fail',
            lhs_value=str(income),
            rhs_value=str(expenditure),
            diff=diff,
            evidence_cells=evidence,
            message=f"本年收入({income}) {'=' if passed else '≠'} 本年支出({expenditure})"
        )
    
    async def _rule_r004(self) -> Finding:
        """R004: 基本支出经济分类汇总 = 基本支出合计"""
        table_code_economic = 'FIN_06_basic_expenditure'
        table_code_expenditure = 'FIN_03_expenditure'
        
        # Sum of economic L1 (3-digit codes like 301, 302, 303, 310)
        economic_sum = await self._get_fact_sum(table_code_economic, 'actual', classification_code_length=3)
        
        # Basic expenditure total from FIN_03
        basic_total = await self._get_fact(table_code_expenditure, '合计', 'basic_actual')
        
        if basic_total is None:
            return Finding(
                rule_key='R004', status='skip',
                lhs_value=str(economic_sum), rhs_value=str(basic_total),
                diff=0, evidence_cells=[],
                message='缺少基本支出合计数据'
            )
        
        passed, diff = self._check_equal(economic_sum, basic_total)
        evidence = await self._get_evidence_cells(table_code_economic)
        
        return Finding(
            rule_key='R004',
            status='pass' if passed else 'fail',
            lhs_value=str(economic_sum),
            rhs_value=str(basic_total),
            diff=diff,
            evidence_cells=evidence,
            message=f"经济分类汇总({economic_sum}) {'=' if passed else '≠'} 基本支出({basic_total})"
        )
    
    async def _rule_r005(self) -> Finding:
        """R005: 三公经费合计 = 子项之和"""
        table_code = 'FIN_07_three_public'
        
        # Get total (both budget and actual)
        await self.conn.fetchval("""
            SELECT amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
              AND classification_code = 'total' AND measure = 'budget'
        """, self.document_version_id, table_code)
        
        total_actual = await self.conn.fetchval("""
            SELECT amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
              AND classification_code = 'total' AND measure = 'actual'
        """, self.document_version_id, table_code)
        
        # Get sub-items (overseas, reception, etc.)
        sub_items_actual = await self.conn.fetch("""
            SELECT classification_code, amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
              AND classification_code != 'total' AND measure = 'actual'
        """, self.document_version_id, table_code)
        
        if total_actual is None or not sub_items_actual:
            return Finding(
                rule_key='R005', status='skip',
                lhs_value=str(total_actual), rhs_value='N/A',
                diff=0, evidence_cells=[],
                message='缺少三公经费数据'
            )
        
        # Sum sub-items (treating None as 0, but noting if any are None)
        sub_sum = sum(float(item['amount']) for item in sub_items_actual if item['amount'] is not None)
        total_actual = float(total_actual)
        
        passed, diff = self._check_equal(total_actual, sub_sum)
        evidence = await self._get_evidence_cells(table_code)
        
        return Finding(
            rule_key='R005',
            status='pass' if passed else 'fail',
            lhs_value=str(total_actual),
            rhs_value=str(sub_sum),
            diff=diff,
            evidence_cells=evidence,
            message=f"三公合计({total_actual}) {'=' if passed else '≠'} 子项之和({sub_sum})"
        )


async def run_qc_check(document_version_id: int) -> int:
    """
    Run QC check for a document version.
    
    Args:
        document_version_id: ID of the fiscal_document_versions record
        
    Returns:
        run_id: ID of the created qc_runs_v2 record
    """
    from src.db.connection import DatabaseConnection
    
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        runner = QCRunner(conn, document_version_id)
        run_id = await runner.execute()
        
    return run_id
