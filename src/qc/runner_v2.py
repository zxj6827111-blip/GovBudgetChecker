"""
QC Rule Engine V2 - Enhanced with Row-Level Evidence and Versioning.

Changes from V1:
- Rule parameters from qc_rule_versions table
- Row-level evidence collection (not just summary rows)
- Drilldown support
- Report generation
"""

import logging
import json
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from decimal import Decimal
import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class Finding:
    """Represents a QC finding result with row-level evidence."""
    rule_key: str
    status: str  # 'pass', 'fail', 'warn', 'skip'
    lhs_value: str
    rhs_value: str
    diff: float
    evidence_cells: List[int]
    evidence_facts: List[int] = field(default_factory=list)  # fact IDs for drilldown
    message: str = ""
    skip_reason: Optional[str] = None  # Reason for SKIP status (e.g., 'table_not_found', 'table_empty')



@dataclass
class RuleParams:
    """Rule parameters from qc_rule_versions."""
    rule_key: str
    version: str
    tolerance: float
    table_code: Optional[str]
    null_as_zero: bool
    extra: Dict[str, Any]


class QCRunnerV2:
    """
    Enhanced QC Rule Runner with versioning and row-level evidence.
    """
    
    def __init__(self, conn: asyncpg.Connection, document_version_id: int):
        self.conn = conn
        self.document_version_id = document_version_id
        self.run_id: Optional[int] = None
        self.rule_params: Dict[str, RuleParams] = {}
    
    async def load_rule_params(self):
        """Load active rule versions and parameters."""
        rows = await self.conn.fetch("""
            SELECT rule_key, version, params_json
            FROM qc_rule_versions
            WHERE is_active = true
            ORDER BY rule_key, created_at DESC
        """)
        
        for row in rows:
            if row['rule_key'] not in self.rule_params:
                params = json.loads(row['params_json']) if row['params_json'] else {}
                self.rule_params[row['rule_key']] = RuleParams(
                    rule_key=row['rule_key'],
                    version=row['version'],
                    tolerance=params.get('tolerance', 0.01),
                    table_code=params.get('table_code'),
                    null_as_zero=params.get('null_as_zero', False),
                    extra=params
                )
        
        logger.info(f"Loaded {len(self.rule_params)} rule versions")
    
    async def execute(self) -> int:
        """Execute all QC rules and return run_id."""
        # Load rule parameters
        await self.load_rule_params()
        
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
    
    def _get_params(self, rule_key: str) -> RuleParams:
        """Get parameters for a rule, with defaults."""
        if rule_key in self.rule_params:
            return self.rule_params[rule_key]
        return RuleParams(
            rule_key=rule_key, version='default', tolerance=0.01,
            table_code=None, null_as_zero=False, extra={}
        )
    
    async def _get_fact(self, table_code: str, classification_code: str, measure: str) -> Tuple[Optional[float], Optional[int]]:
        """Get a single fact value and its ID."""
        row = await self.conn.fetchrow("""
            SELECT id, amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1
              AND table_code = $2
              AND classification_code = $3
              AND measure = $4
            LIMIT 1
        """, self.document_version_id, table_code, classification_code, measure)
        if row:
            return float(row['amount']) if row['amount'] is not None else None, row['id']
        return None, None
    
    async def _get_facts_by_code_length(self, table_code: str, measure: str, 
                                         code_length: int) -> List[Dict]:
        """Get facts with classification codes of specific length."""
        rows = await self.conn.fetch("""
            SELECT id, classification_code, classification_name, amount, row_order
            FROM fact_fiscal_line_items
            WHERE document_version_id = $1
              AND table_code = $2
              AND measure = $3
              AND LENGTH(classification_code) = $4
              AND classification_code ~ '^[0-9]+$'
            ORDER BY classification_code
        """, self.document_version_id, table_code, measure, code_length)
        return [dict(r) for r in rows]
    
    async def _get_cells_for_facts(self, fact_ids: List[int]) -> List[int]:
        """Get cell IDs that correspond to facts (via table_code and row_order)."""
        if not fact_ids:
            return []
        
        # Get fact details
        facts = await self.conn.fetch("""
            SELECT DISTINCT table_code, row_order 
            FROM fact_fiscal_line_items 
            WHERE id = ANY($1) AND row_order IS NOT NULL
        """, fact_ids)
        
        cell_ids = []
        for fact in facts:
            # Map row_order to row_idx (row_order is 0-indexed same as row_idx for data rows)
            row_idx = int(fact['row_order']) + 2  # Offset for header rows
            cells = await self.conn.fetch("""
                SELECT id FROM fiscal_table_cells
                WHERE document_version_id = $1 
                  AND table_code = $2 
                  AND row_idx = $3
            """, self.document_version_id, fact['table_code'], row_idx)
            cell_ids.extend([c['id'] for c in cells])
        
        return cell_ids
    
    async def _get_evidence_cells_for_summary(self, table_code: str) -> List[int]:
        """Get cell IDs for summary/total rows."""
        rows = await self.conn.fetch("""
            SELECT id FROM fiscal_table_cells
            WHERE document_version_id = $1 AND table_code = $2
              AND (raw_text LIKE '%合计%' OR raw_text LIKE '%总计%')
            ORDER BY row_idx, col_idx LIMIT 20
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
    
    def _check_equal(self, lhs: float, rhs: float, tolerance: float) -> Tuple[bool, float]:
        """Check if two values are equal within tolerance."""
        # Convert to float to handle Decimal types
        lhs_f = float(lhs) if lhs is not None else 0.0
        rhs_f = float(rhs) if rhs is not None else 0.0
        diff = abs(lhs_f - rhs_f)
        return diff <= tolerance, diff

    
    # ========================================================================
    # Rule Implementations with Row-Level Evidence
    # ========================================================================
    
    async def _rule_r001(self) -> Finding:
        """R001: 总支出 = 基本支出 + 项目支出"""
        params = self._get_params('R001')
        table_code = params.extra.get('table_code', 'FIN_03_expenditure')
        
        total, total_id = await self._get_fact(table_code, '合计', 'total_actual')
        basic, basic_id = await self._get_fact(table_code, '合计', 'basic_actual')
        project, project_id = await self._get_fact(table_code, '合计', 'project_actual')
        
        fact_ids = [fid for fid in [total_id, basic_id, project_id] if fid]
        
        if total is None or basic is None or project is None:
            return Finding(
                rule_key='R001', status='skip',
                lhs_value=str(total), rhs_value=f"{basic}+{project}",
                diff=0, evidence_cells=[], evidence_facts=fact_ids,
                message='缺少必要数据'
            )
        
        expected = basic + project
        passed, diff = self._check_equal(total, expected, params.tolerance)
        evidence = await self._get_cells_for_facts(fact_ids)
        if not evidence:
            evidence = await self._get_evidence_cells_for_summary(table_code)
        
        return Finding(
            rule_key='R001',
            status='pass' if passed else 'fail',
            lhs_value=str(total),
            rhs_value=f"{basic}+{project}={expected}",
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=fact_ids,
            message=f"总支出({total}) {'=' if passed else '≠'} 基本({basic})+项目({project})"
        )
    
    async def _rule_r002(self) -> Finding:
        """R002: 功能分类一级汇总 = 合计 (with row-level evidence)"""
        params = self._get_params('R002')
        table_code = params.extra.get('table_code', 'FIN_03_expenditure')
        code_length = params.extra.get('classification_length', 3)
        
        # Get all L1 classification facts
        l1_facts = await self._get_facts_by_code_length(table_code, 'total_actual', code_length)
        l1_sum = sum(f['amount'] for f in l1_facts if f['amount'] is not None)
        l1_fact_ids = [f['id'] for f in l1_facts]
        
        total, total_id = await self._get_fact(table_code, '合计', 'total_actual')
        if total_id:
            l1_fact_ids.append(total_id)
        
        if total is None:
            return Finding(
                rule_key='R002', status='skip',
                lhs_value=str(l1_sum), rhs_value=str(total),
                diff=0, evidence_cells=[], evidence_facts=l1_fact_ids,
                message='缺少合计数据'
            )
        
        passed, diff = self._check_equal(l1_sum, total, params.tolerance)
        evidence = await self._get_cells_for_facts(l1_fact_ids)
        if not evidence:
            evidence = await self._get_evidence_cells_for_summary(table_code)
        
        return Finding(
            rule_key='R002',
            status='pass' if passed else 'fail',
            lhs_value=str(l1_sum),
            rhs_value=str(total),
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=l1_fact_ids,
            message=f"功能分类一级汇总({l1_sum}) {'=' if passed else '≠'} 合计({total})"
        )
    
    async def _rule_r003(self) -> Finding:
        """R003: 总收入 = 总支出"""
        params = self._get_params('R003')
        table_code = params.extra.get('table_code', 'FIN_01_income_expenditure_total')
        
        # Find income total
        income_row = await self.conn.fetchrow("""
            SELECT id, amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1
              AND table_code = $2
              AND classification_name LIKE '%本年收入合计%'
              AND measure = 'actual'
            LIMIT 1
        """, self.document_version_id, table_code)
        
        # Find expenditure total
        exp_row = await self.conn.fetchrow("""
            SELECT id, amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1
              AND table_code = $2
              AND classification_name LIKE '%本年支出合计%'
              AND measure = 'actual'
            LIMIT 1
        """, self.document_version_id, table_code)
        
        fact_ids = []
        if income_row:
            fact_ids.append(income_row['id'])
        if exp_row:
            fact_ids.append(exp_row['id'])
        
        if not income_row or not exp_row:
            return Finding(
                rule_key='R003', status='skip',
                lhs_value=str(income_row['amount'] if income_row else None),
                rhs_value=str(exp_row['amount'] if exp_row else None),
                diff=0, evidence_cells=[], evidence_facts=fact_ids,
                message='缺少收入或支出数据'
            )
        
        income = float(income_row['amount'])
        expenditure = float(exp_row['amount'])
        passed, diff = self._check_equal(income, expenditure, params.tolerance)
        evidence = await self._get_cells_for_facts(fact_ids)
        if not evidence:
            evidence = await self._get_evidence_cells_for_summary(table_code)
        
        return Finding(
            rule_key='R003',
            status='pass' if passed else 'fail',
            lhs_value=str(income),
            rhs_value=str(expenditure),
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=fact_ids,
            message=f"本年收入({income}) {'=' if passed else '≠'} 本年支出({expenditure})"
        )
    
    async def _rule_r004(self) -> Finding:
        """R004: 基本支出经济分类汇总 = 基本支出合计 (with row-level evidence)"""
        params = self._get_params('R004')
        table_code_economic = params.extra.get('economic_table', 'FIN_06_basic_expenditure')
        table_code_expenditure = params.extra.get('expenditure_table', 'FIN_03_expenditure')
        
        # Get all economic L1 facts
        l1_facts = await self._get_facts_by_code_length(table_code_economic, 'actual', 3)
        economic_sum = sum(f['amount'] for f in l1_facts if f['amount'] is not None)
        fact_ids = [f['id'] for f in l1_facts]
        
        # Basic expenditure total from FIN_03
        basic_total, basic_id = await self._get_fact(table_code_expenditure, '合计', 'basic_actual')
        if basic_id:
            fact_ids.append(basic_id)
        
        if basic_total is None:
            return Finding(
                rule_key='R004', status='skip',
                lhs_value=str(economic_sum), rhs_value=str(basic_total),
                diff=0, evidence_cells=[], evidence_facts=fact_ids,
                message='缺少基本支出合计数据'
            )
        
        passed, diff = self._check_equal(economic_sum, basic_total, params.tolerance)
        evidence = await self._get_cells_for_facts(fact_ids)
        if not evidence:
            evidence = await self._get_evidence_cells_for_summary(table_code_economic)
        
        return Finding(
            rule_key='R004',
            status='pass' if passed else 'fail',
            lhs_value=str(economic_sum),
            rhs_value=str(basic_total),
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=fact_ids,
            message=f"经济分类汇总({economic_sum}) {'=' if passed else '≠'} 基本支出({basic_total})"
        )
    
    async def _rule_r005(self) -> Finding:
        """R005: 三公经费合计 = 子项之和 (with row-level evidence)"""
        params = self._get_params('R005')
        table_code = params.extra.get('table_code', 'FIN_07_three_public')
        
        # Get total
        total_row = await self.conn.fetchrow("""
            SELECT id, amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
              AND classification_code = 'total' AND measure = 'actual'
        """, self.document_version_id, table_code)
        
        # Get sub-items
        sub_items = await self.conn.fetch("""
            SELECT id, classification_code, amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
              AND classification_code != 'total' AND measure = 'actual'
        """, self.document_version_id, table_code)
        
        fact_ids = [s['id'] for s in sub_items]
        if total_row:
            fact_ids.append(total_row['id'])
        
        if not total_row or not sub_items:
            return Finding(
                rule_key='R005', status='skip',
                lhs_value=str(total_row['amount'] if total_row else None), 
                rhs_value='N/A',
                diff=0, evidence_cells=[], evidence_facts=fact_ids,
                message='缺少三公经费数据'
            )
        
        # Handle null values based on null_as_zero param
        null_as_zero = params.null_as_zero
        sub_sum = 0.0
        for item in sub_items:
            if item['amount'] is not None:
                sub_sum += float(item['amount'])
            elif null_as_zero:
                sub_sum += 0.0
            # If not null_as_zero and value is None, we skip it (don't add)
        
        total_actual = float(total_row['amount'])
        passed, diff = self._check_equal(total_actual, sub_sum, params.tolerance)
        evidence = await self._get_cells_for_facts(fact_ids)
        if not evidence:
            evidence = await self._get_evidence_cells_for_summary(table_code)
        
        return Finding(
            rule_key='R005',
            status='pass' if passed else 'fail',
            lhs_value=str(total_actual),
            rhs_value=str(sub_sum),
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=fact_ids,
            message=f"三公合计({total_actual}) {'=' if passed else '≠'} 子项之和({sub_sum})"
        )


async def run_qc_check_v2(document_version_id: int) -> int:
    """
    Run QC check V2 for a document version.
    """
    from src.db.connection import DatabaseConnection
    
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        runner = QCRunnerV2(conn, document_version_id)
        run_id = await runner.execute()
        
    return run_id


async def get_finding_drilldown(finding_id: int) -> Dict[str, Any]:
    """
    Get detailed drilldown for a finding, including cells and facts.
    """
    from src.db.connection import DatabaseConnection
    
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        # Get finding
        finding = await conn.fetchrow("""
            SELECT f.*, r.description as rule_description, r.severity
            FROM qc_findings_v2 f
            LEFT JOIN qc_rule_definitions_v2 r ON f.rule_key = r.rule_key
            WHERE f.id = $1
        """, finding_id)
        
        if not finding:
            return None
        
        # Get run info for document_version_id
        run = await conn.fetchrow("""
            SELECT document_version_id FROM qc_runs_v2 WHERE id = $1
        """, finding['run_id'])
        
        result = {
            "finding": dict(finding),
            "cells": [],
            "facts": []
        }
        
        # Get cells
        if finding['evidence_cells']:
            cells = await conn.fetch("""
                SELECT id, table_code, row_idx, col_idx, raw_text
                FROM fiscal_table_cells
                WHERE id = ANY($1)
                ORDER BY table_code, row_idx, col_idx
            """, list(finding['evidence_cells']))
            result["cells"] = [dict(c) for c in cells]
        
        # Get related facts based on table_code from cells
        if result["cells"] and run:
            table_codes = list(set(c['table_code'] for c in result["cells"]))
            facts = await conn.fetch("""
                SELECT id, table_code, statement_code, classification_code, 
                       classification_name, measure, amount, row_order
                FROM fact_fiscal_line_items
                WHERE document_version_id = $1 AND table_code = ANY($2)
                ORDER BY table_code, row_order
            """, run['document_version_id'], table_codes)
            result["facts"] = [dict(f) for f in facts]
        
        return result


def generate_qc_report_markdown(run_info: Dict, findings: List[Dict], drilldowns: Dict[int, Dict]) -> str:
    """
    Generate a Markdown QC report.
    """
    lines = []
    
    # Header
    lines.append("# QC 检查报告")
    lines.append("")
    lines.append(f"- **Run ID**: {run_info['id']}")
    lines.append(f"- **Document Version ID**: {run_info['document_version_id']}")
    lines.append(f"- **开始时间**: {run_info['started_at']}")
    lines.append(f"- **结束时间**: {run_info['finished_at']}")
    lines.append("")
    
    # Summary
    pass_count = sum(1 for f in findings if f['status'] == 'pass')
    fail_count = sum(1 for f in findings if f['status'] == 'fail')
    skip_count = sum(1 for f in findings if f['status'] == 'skip')
    
    lines.append("## 概览")
    lines.append("")
    lines.append("| 状态 | 数量 |")
    lines.append("|------|------|")
    lines.append(f"| ✅ 通过 | {pass_count} |")
    lines.append(f"| ❌ 失败 | {fail_count} |")
    lines.append(f"| ⏭️ 跳过 | {skip_count} |")
    lines.append("")
    
    # Rule details
    lines.append("## 规则检查详情")
    lines.append("")
    
    for f in findings:
        status_icon = "✅" if f['status'] == 'pass' else ("❌" if f['status'] == 'fail' else "⏭️")
        lines.append(f"### {f['rule_key']}: {f.get('rule_description', '')} {status_icon}")
        lines.append("")
        lines.append(f"- **状态**: {f['status'].upper()}")
        lines.append(f"- **严重程度**: {f.get('severity', 'N/A')}")
        lines.append(f"- **左值 (LHS)**: {f['lhs_value']}")
        lines.append(f"- **右值 (RHS)**: {f['rhs_value']}")
        lines.append(f"- **差值**: {f['diff']}")
        lines.append(f"- **结论**: {f['message']}")
        lines.append("")
        
        # Evidence cells
        if f['id'] in drilldowns and drilldowns[f['id']].get('cells'):
            cells = drilldowns[f['id']]['cells'][:10]  # Limit to 10
            lines.append("**证据单元格**:")
            lines.append("")
            lines.append("| 表号 | 行 | 列 | 内容 |")
            lines.append("|------|----|----|------|")
            for c in cells:
                text = str(c['raw_text'])[:30] if c['raw_text'] else ""
                lines.append(f"| {c['table_code']} | {c['row_idx']} | {c['col_idx']} | {text} |")
            lines.append("")
    
    return "\n".join(lines)
