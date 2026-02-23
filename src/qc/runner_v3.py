"""
QC Rules Extension: R006-R010

Adds 5 new rules with WARN/SKIP states for comprehensive fiscal checking.
"""

from src.qc.runner_v2 import QCRunnerV2, Finding, RuleParams
from typing import Optional, List, Dict
import asyncpg
import logging

logger = logging.getLogger(__name__)


class QCRunnerV3(QCRunnerV2):
    """
    Extended QC Runner with R006-R010 rules and four-state logic.
    
    Supports: PASS, FAIL, WARN, SKIP
    """
    
    async def execute(self) -> int:
        """Execute all QC rules including R001-R015."""
        # Load rule parameters
        await self.load_rule_params()
        
        # Create QC run
        self.run_id = await self.conn.fetchval("""
            INSERT INTO qc_runs_v2 (document_version_id)
            VALUES ($1) RETURNING id
        """, self.document_version_id)
        
        logger.info(f"Created QC run {self.run_id} for version {self.document_version_id}")
        
        # Execute all rules (R001-R015)
        findings = []
        findings.append(await self._rule_r001())
        findings.append(await self._rule_r002())
        findings.append(await self._rule_r003())
        findings.append(await self._rule_r004())
        findings.append(await self._rule_r005())
        findings.append(await self._rule_r006())
        findings.append(await self._rule_r007())
        findings.append(await self._rule_r008())
        findings.append(await self._rule_r009())
        findings.append(await self._rule_r010())
        findings.append(await self._rule_r011())
        findings.append(await self._rule_r012())
        findings.append(await self._rule_r013())
        findings.append(await self._rule_r014())
        findings.append(await self._rule_r015())
        
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
    
    async def _check_table_exists(self, table_code: str) -> bool:
        """Check if a table exists for this document version."""
        count = await self.conn.fetchval("""
            SELECT COUNT(*) FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
        """, self.document_version_id, table_code)
        return count > 0
    
    # ========================================================================
    # New Rules: R006-R010
    # ========================================================================
    
    async def _rule_r006(self) -> Finding:
        """R006: 本年收入 = 财政拨款 + 事业收入 + 经营收入 + 其他收入"""
        params = self._get_params('R006')
        table_code = 'FIN_02_income'
        
        # Check table exists
        if not await self._check_table_exists(table_code):
            return Finding(
                rule_key='R006',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='收入表不存在',
                skip_reason='table_not_found'
            )
        
        # Get total income
        total, total_id = await self._get_fact(table_code, '合计', 'total_actual')
        
        # Get components
        fiscal, fiscal_id = await self._get_fact(table_code, '合计', 'fiscal_allocation')
        business, business_id = await self._get_fact(table_code, '合计', 'business_income')
        operational, operational_id = await self._get_fact(table_code, '合计', 'operational_income')
        other, other_id = await self._get_fact(table_code, '合计', 'other_income')
        
        fact_ids = [fid for fid in [total_id, fiscal_id, business_id, operational_id, other_id] if fid]
        
        # Handle missing components
        components = []
        missing = []
        
        if fiscal is not None:
            components.append(fiscal)
        else:
            missing.append('财政拨款')
        
        if business is not None:
            components.append(business)
        else:
            missing.append('事业收入')
        
        if operational is not None:
            components.append(operational)
        
        if other is not None:
            components.append(other)
        
        if total is None or not components:
            return Finding(
                rule_key='R006',
                status='skip',
                lhs_value=str(total),
                rhs_value='缺少必要数据',
                diff=0,
                evidence_cells=[],
                evidence_facts=fact_ids,
                message=f'缺少必要数据: {", ".join(missing)}' if missing else '总收入数据缺失'
            )
        
        expected = sum(components)
        passed, diff = self._check_equal(total, expected, params.tolerance)
        evidence = await self._get_cells_for_facts(fact_ids)
        if not evidence:
            evidence = await self._get_evidence_cells_for_summary(table_code)
        
        return Finding(
            rule_key='R006',
            status='pass' if passed else 'fail',
            lhs_value=str(total),
            rhs_value=f"{'+'.join(map(str, components))}={expected}",
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=fact_ids,
            message=f"本年收入({total}) {'=' if passed else '≠'} 各项收入之和({expected})"
        )
    
    async def _rule_r007(self) -> Finding:
        """R007: 支出决算表合计 = 收入支出总表支出"""
        params = self._get_params('R007')
        
        # Check both tables exist
        has_fin03 = await self._check_table_exists('FIN_03_expenditure')
        has_fin01 = await self._check_table_exists('FIN_01_income_expenditure_total')
        
        if not has_fin03 or not has_fin01:
            missing = []
            if not has_fin03:
                missing.append('FIN_03')
            if not has_fin01:
                missing.append('FIN_01')
            
            return Finding(
                rule_key='R007',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message=f'缺少必要表格: {", ".join(missing)}',
                skip_reason='table_not_found'
            )
        
        # Get FIN_03 total
        fin03_total, fin03_id = await self._get_fact('FIN_03_expenditure', '合计', 'total_actual')
        
        # Get FIN_01 expenditure
        fin01_exp = await self.conn.fetchrow("""
            SELECT id, amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1 
              AND table_code = $2
              AND classification_name LIKE '%本年支出%'
              AND measure = 'expenditure_actual'
            LIMIT 1
        """, self.document_version_id, 'FIN_01_income_expenditure_total')
        
        fact_ids = [fin03_id] if fin03_id else []
        if fin01_exp:
            fact_ids.append(fin01_exp['id'])
        
        if fin03_total is None or not fin01_exp:
            return Finding(
                rule_key='R007',
                status='skip',
                lhs_value=str(fin03_total) if fin03_total else 'N/A',
                rhs_value=str(fin01_exp['amount']) if fin01_exp else 'N/A',
                diff=0,
                evidence_cells=[],
                evidence_facts=fact_ids,
                message='缺少支出数据'
            )
        
        fin01_total = float(fin01_exp['amount'])
        passed, diff = self._check_equal(fin03_total, fin01_total, params.tolerance)
        evidence = await self._get_cells_for_facts(fact_ids)
        
        return Finding(
            rule_key='R007',
            status='pass' if passed else 'fail',
            lhs_value=str(fin03_total),
            rhs_value=str(fin01_total),
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=fact_ids,
            message=f"FIN_03支出({fin03_total}) {'=' if passed else '≠'} FIN_01支出({fin01_total})"
        )
    
    async def _rule_r008(self) -> Finding:
        """R008: 项目支出汇总 = 项目支出明细表总计 (if exists)"""
        params = self._get_params('R008')
        
        # Check FIN_03 exists
        if not await self._check_table_exists('FIN_03_expenditure'):
            return Finding(
                rule_key='R008',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='支出表不存在',
                skip_reason='table_not_found'
            )
        
        # Get FIN_03 project total
        project_total, project_id = await self._get_fact('FIN_03_expenditure', '合计', 'project_actual')
        
        # Check if FIN_04 exists
        has_fin04 = await self._check_table_exists('FIN_04_project_expenditure')
        
        if not has_fin04:
            # FIN_04 is optional, return INFO
            return Finding(
                rule_key='R008',
                status='skip',
                lhs_value=str(project_total) if project_total else 'N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='项目支出明细表不存在（可选表）',
                skip_reason='optional_table_not_found'
            )
        
        # Get FIN_04 details sum
        fin04_sum = await self.conn.fetchval("""
            SELECT SUM(amount) FROM fact_fiscal_line_items
            WHERE document_version_id = $1
              AND table_code = $2
              AND measure = 'project_amount'
              AND classification_code != 'total'
        """, self.document_version_id, 'FIN_04_project_expenditure')
        
        if project_total is None or fin04_sum is None:
            return Finding(
                rule_key='R008',
                status='skip',
                lhs_value=str(project_total),
                rhs_value=str(fin04_sum),
                diff=0,
                evidence_cells=[],
                message='缺少项目支出数据'
            )
        
        fin04_sum_f = float(fin04_sum)
        passed, diff = self._check_equal(project_total, fin04_sum_f, params.tolerance)
        
        return Finding(
            rule_key='R008',
            status='pass' if passed else 'fail',
            lhs_value=str(project_total),
            rhs_value=str(fin04_sum_f),
            diff=diff,
            evidence_cells=[],
            message=f"项目支出汇总({project_total}) {'=' if passed else '≠'} 明细之和({fin04_sum_f})"
        )
    
    async def _rule_r009(self) -> Finding:
        """R009: 政府性基金支出 = 基本 + 项目 (空表跳过)"""
        params = self._get_params('R009')
        table_code = 'FIN_08_gov_fund'
        
        # Check table exists
        if not await self._check_table_exists(table_code):
            return Finding(
                rule_key='R009',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='政府性基金表不存在（单位无此业务）',
                skip_reason='table_not_found'
            )
        
        # Check if table is empty
        fact_count = await self.conn.fetchval("""
            SELECT COUNT(*) FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
        """, self.document_version_id, table_code)
        
        if fact_count == 0:
            return Finding(
                rule_key='R009',
                status='skip',
                lhs_value='0',
                rhs_value='0',
                diff=0,
                evidence_cells=[],
                message='政府性基金表为空（单位无此业务）',
                skip_reason='table_empty'
            )
        
        # Normal check
        total, total_id = await self._get_fact(table_code, '合计', 'total_actual')
        basic, basic_id = await self._get_fact(table_code, '合计', 'basic_actual')
        project, project_id = await self._get_fact(table_code, '合计', 'project_actual')
        
        fact_ids = [fid for fid in [total_id, basic_id, project_id] if fid]
        
        if total is None:
            return Finding(
                rule_key='R009',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                evidence_facts=fact_ids,
                message='基金支出数据缺失'
            )
        
        # Allow None values for basic/project (treat as 0)
        basic_v = basic if basic is not None else 0.0
        project_v = project if project is not None else 0.0
        expected = basic_v + project_v
        
        passed, diff = self._check_equal(total, expected, params.tolerance)
        evidence = await self._get_cells_for_facts(fact_ids)
        
        return Finding(
            rule_key='R009',
            status='pass' if passed else 'fail',
            lhs_value=str(total),
            rhs_value=f"{basic_v}+{project_v}={expected}",
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=fact_ids,
            message=f"基金支出({total}) {'=' if passed else '≠'} 基本({basic_v})+项目({project_v})"
        )
    
    async def _rule_r010(self) -> Finding:
        """R010: 国有资本支出 = 基本 + 项目 (空表跳过)"""
        params = self._get_params('R010')
        table_code = 'FIN_09_state_capital'
        
        # Check table exists
        if not await self._check_table_exists(table_code):
            return Finding(
                rule_key='R010',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='国有资本表不存在（单位无此业务）',
                skip_reason='table_not_found'
            )
        
        # Check if table is empty
        fact_count = await self.conn.fetchval("""
            SELECT COUNT(*) FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
        """, self.document_version_id, table_code)
        
        if fact_count == 0:
            return Finding(
                rule_key='R010',
                status='skip',
                lhs_value='0',
                rhs_value='0',
                diff=0,
                evidence_cells=[],
                message='国有资本表为空（单位无此业务）',
                skip_reason='table_empty'
            )
        
        # Normal check
        total, total_id = await self._get_fact(table_code, '合计', 'total_actual')
        basic, basic_id = await self._get_fact(table_code, '合计', 'basic_actual')
        project, project_id = await self._get_fact(table_code, '合计', 'project_actual')
        
        fact_ids = [fid for fid in [total_id, basic_id, project_id] if fid]
        
        if total is None:
            return Finding(
                rule_key='R010',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                evidence_facts=fact_ids,
                message='国资支出数据缺失'
            )
        
        # Allow None values
        basic_v = basic if basic is not None else 0.0
        project_v = project if project is not None else 0.0
        expected = basic_v + project_v
        
        passed, diff = self._check_equal(total, expected, params.tolerance)
        evidence = await self._get_cells_for_facts(fact_ids)
        
        return Finding(
            rule_key='R010',
            status='pass' if passed else 'fail',
            lhs_value=str(total),
            rhs_value=f"{basic_v}+{project_v}={expected}",
            diff=diff,
            evidence_cells=evidence,
            evidence_facts=fact_ids,
            message=f"国资支出({total}) {'=' if passed else '≠'} 基本({basic_v})+项目({project_v})"
        )
    
    # ========================================================================
    # Additional Rules: R011-R015
    # ========================================================================
    
    async def _rule_r011(self) -> Finding:
        """R011: 功能分类一级科目汇总 = 各二级科目之和"""
        params = self._get_params('R011')
        table_code = 'FIN_05_general_public_expenditure'
        
        if not await self._check_table_exists(table_code):
            return Finding(
                rule_key='R011',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='功能分类支出表不存在',
                skip_reason='table_not_found'
            )
        
        # Get level-1 categories (3-digit codes like 201, 208)
        level1_items = await self.conn.fetch("""
            SELECT classification_code, classification_name, amount
            FROM fact_fiscal_line_items
            WHERE document_version_id = $1 
              AND table_code = $2
              AND LENGTH(classification_code) = 3
              AND measure = 'total_actual'
        """, self.document_version_id, table_code)
        
        if not level1_items:
            return Finding(
                rule_key='R011',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='无一级功能分类数据',
                skip_reason='no_data'
            )
        
        # Check each level-1 category
        errors = []
        for item in level1_items:
            code = item['classification_code']
            parent_amount = float(item['amount']) if item['amount'] else 0
            
            # Get children (5-digit codes starting with this code)
            children_sum = await self.conn.fetchval("""
                SELECT COALESCE(SUM(amount), 0)
                FROM fact_fiscal_line_items
                WHERE document_version_id = $1
                  AND table_code = $2
                  AND classification_code LIKE $3
                  AND LENGTH(classification_code) = 5
                  AND measure = 'total_actual'
            """, self.document_version_id, table_code, f"{code}%")
            
            children_sum = float(children_sum) if children_sum else 0
            
            if children_sum > 0:  # Only check if children exist
                passed, diff = self._check_equal(parent_amount, children_sum, params.tolerance)
                if not passed:
                    errors.append(f"{code}: {parent_amount}≠{children_sum}")
        
        if errors:
            return Finding(
                rule_key='R011',
                status='fail',
                lhs_value=str(len(errors)),
                rhs_value='0',
                diff=len(errors),
                evidence_cells=[],
                message=f"功能分类层级不平衡: {'; '.join(errors[:3])}"
            )
        
        return Finding(
            rule_key='R011',
            status='pass',
            lhs_value='层级汇总',
            rhs_value='一致',
            diff=0,
            evidence_cells=[],
            message=f"功能分类层级汇总正确 ({len(level1_items)}个一级科目)"
        )
    
    async def _rule_r012(self) -> Finding:
        """R012: 三公经费决算数 ≤ 预算数 (决算超预算警告)"""
        params = self._get_params('R012')
        table_code = 'FIN_07_three_public'
        
        if not await self._check_table_exists(table_code):
            return Finding(
                rule_key='R012',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='三公经费表不存在',
                skip_reason='table_not_found'
            )
        
        # Get budget and actual for total
        budget = await self.conn.fetchval("""
            SELECT amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
              AND classification_name LIKE '%合计%' AND measure = 'budget'
        """, self.document_version_id, table_code)
        
        actual = await self.conn.fetchval("""
            SELECT amount FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND table_code = $2
              AND classification_name LIKE '%合计%' AND measure = 'actual'
        """, self.document_version_id, table_code)
        
        if budget is None or actual is None:
            return Finding(
                rule_key='R012',
                status='skip',
                lhs_value=str(actual),
                rhs_value=str(budget),
                diff=0,
                evidence_cells=[],
                message='三公经费预决算数据缺失',
                skip_reason='no_data'
            )
        
        budget_f = float(budget)
        actual_f = float(actual)
        diff = actual_f - budget_f
        
        if diff > params.tolerance:
            # Over budget - warning
            return Finding(
                rule_key='R012',
                status='warn',
                lhs_value=str(actual_f),
                rhs_value=str(budget_f),
                diff=diff,
                evidence_cells=[],
                message=f"三公经费决算({actual_f})超预算({budget_f})，超支{diff:.2f}万元"
            )
        
        return Finding(
            rule_key='R012',
            status='pass',
            lhs_value=str(actual_f),
            rhs_value=str(budget_f),
            diff=diff,
            evidence_cells=[],
            message=f"三公经费决算({actual_f})≤预算({budget_f})"
        )
    
    async def _rule_r013(self) -> Finding:
        """R013: 预算调整率检测 (调整超30%警告)"""
        params = self._get_params('R013')
        
        # Get total budget and actual
        budget = await self.conn.fetchval("""
            SELECT SUM(amount) FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND measure = 'total_budget'
        """, self.document_version_id)
        
        actual = await self.conn.fetchval("""
            SELECT SUM(amount) FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND measure = 'total_actual'
        """, self.document_version_id)
        
        if budget is None or actual is None or float(budget) == 0:
            return Finding(
                rule_key='R013',
                status='skip',
                lhs_value='N/A',
                rhs_value='N/A',
                diff=0,
                evidence_cells=[],
                message='预决算数据缺失',
                skip_reason='no_data'
            )
        
        budget_f = float(budget)
        actual_f = float(actual)
        adjustment_rate = abs(actual_f - budget_f) / budget_f * 100
        
        if adjustment_rate > 30:
            return Finding(
                rule_key='R013',
                status='warn',
                lhs_value=f"{adjustment_rate:.1f}%",
                rhs_value='≤30%',
                diff=adjustment_rate - 30,
                evidence_cells=[],
                message=f"预算调整率{adjustment_rate:.1f}%超过30%阈值"
            )
        
        return Finding(
            rule_key='R013',
            status='pass',
            lhs_value=f"{adjustment_rate:.1f}%",
            rhs_value='≤30%',
            diff=adjustment_rate,
            evidence_cells=[],
            message=f"预算调整率{adjustment_rate:.1f}%在合理范围内"
        )
    
    async def _rule_r014(self) -> Finding:
        """R014: 异常数值检测 (负数/超大值)"""
        params = self._get_params('R014')
        
        # Check for negative amounts
        negative_count = await self.conn.fetchval("""
            SELECT COUNT(*) FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND amount < 0
        """, self.document_version_id)
        
        # Check for very large amounts (>100亿)
        large_count = await self.conn.fetchval("""
            SELECT COUNT(*) FROM fact_fiscal_line_items
            WHERE document_version_id = $1 AND amount > 10000000
        """, self.document_version_id)
        
        issues = []
        if negative_count > 0:
            issues.append(f"负数{negative_count}个")
        if large_count > 0:
            issues.append(f"超大值{large_count}个")
        
        if issues:
            return Finding(
                rule_key='R014',
                status='warn',
                lhs_value=str(negative_count + large_count),
                rhs_value='0',
                diff=negative_count + large_count,
                evidence_cells=[],
                message=f"发现异常数值: {', '.join(issues)}"
            )
        
        return Finding(
            rule_key='R014',
            status='pass',
            lhs_value='0',
            rhs_value='0',
            diff=0,
            evidence_cells=[],
            message='未发现异常数值'
        )
    
    async def _rule_r015(self) -> Finding:
        """R015: 必填表存在性检查 (FIN_01/03/05必须存在)"""
        required_tables = [
            ('FIN_01_income_expenditure_total', '收入支出总表'),
            ('FIN_03_expenditure', '支出决算表'),
            ('FIN_05_general_public_expenditure', '一般公共预算支出表')
        ]
        
        missing = []
        for table_code, table_name in required_tables:
            exists = await self._check_table_exists(table_code)
            if not exists:
                missing.append(table_name)
        
        if missing:
            return Finding(
                rule_key='R015',
                status='fail',
                lhs_value=str(len(missing)),
                rhs_value='0',
                diff=len(missing),
                evidence_cells=[],
                message=f"缺少必填表: {', '.join(missing)}"
            )
        
        return Finding(
            rule_key='R015',
            status='pass',
            lhs_value='3',
            rhs_value='3',
            diff=0,
            evidence_cells=[],
            message='所有必填表均存在'
        )
