"""Self-built versioned migration system for PostgreSQL.

This module provides a simple, framework-free migration system that:
1. Tracks applied migrations in a schema_migrations table
2. Runs migrations in order, each wrapped in a transaction
3. Supports schema isolation via PG_SCHEMA environment variable
4. Ensures idempotent SQL (IF NOT EXISTS, etc.)

Usage:
    from src.db.migrations import run_migrations
    await run_migrations()
"""

import logging
from typing import List, Dict, Any
import asyncpg

from src.db.connection import DatabaseConnection

logger = logging.getLogger(__name__)


# ============================================================================
# MIGRATION DEFINITIONS
# ============================================================================
# Each migration has:
#   - id: Unique identifier in format "YYYY-MM-DD_NNNN_description"
#   - description: Human-readable description
#   - sql: List of SQL statements to execute
#
# IMPORTANT: All SQL must be idempotent (safe to re-run) using:
#   - CREATE TABLE IF NOT EXISTS
#   - CREATE INDEX IF NOT EXISTS
#   - DO $$ BEGIN ... EXCEPTION WHEN ... END $$; for constraints
# ============================================================================

MIGRATIONS: List[Dict[str, Any]] = [
    {
        "id": "2026-01-14_0001_init",
        "description": "Initial schema: organizations, users, analysis_jobs, issues, analysis_results",
        "sql": [
            # ------------------------------------------------------------------
            # Organizations table - 组织架构
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS organizations (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                code VARCHAR(50),
                parent_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
                level INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_organizations_code ON organizations(code)",
            "CREATE INDEX IF NOT EXISTS idx_organizations_parent ON organizations(parent_id)",
            
            # ------------------------------------------------------------------
            # Users table - 用户表（预留，未来认证集成）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(100) UNIQUE NOT NULL,
                email VARCHAR(255),
                organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_users_org ON users(organization_id)",
            
            # ------------------------------------------------------------------
            # Analysis jobs table - 分析任务表
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                id SERIAL PRIMARY KEY,
                job_uuid VARCHAR(36) UNIQUE NOT NULL,
                filename VARCHAR(500) NOT NULL,
                file_hash VARCHAR(64),
                organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
                status VARCHAR(50) DEFAULT 'pending',
                mode VARCHAR(50) DEFAULT 'dual',
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                error_message TEXT,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_jobs_uuid ON analysis_jobs(job_uuid)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_org ON analysis_jobs(organization_id)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON analysis_jobs(status)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_created ON analysis_jobs(created_at DESC)",
            
            # ------------------------------------------------------------------
            # Issues table - 问题表
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS issues (
                id SERIAL PRIMARY KEY,
                job_id INTEGER NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
                issue_id VARCHAR(100) NOT NULL,
                source VARCHAR(20) NOT NULL,
                severity VARCHAR(20) NOT NULL,
                category VARCHAR(100),
                rule_id VARCHAR(100),
                title TEXT NOT NULL,
                message TEXT,
                evidence JSONB DEFAULT '[]',
                location JSONB DEFAULT '{}',
                suggestions JSONB DEFAULT '[]',
                auto_status VARCHAR(50) DEFAULT 'pending',
                human_status VARCHAR(50),
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_issues_job ON issues(job_id)",
            "CREATE INDEX IF NOT EXISTS idx_issues_severity ON issues(severity)",
            "CREATE INDEX IF NOT EXISTS idx_issues_source ON issues(source)",
            "CREATE INDEX IF NOT EXISTS idx_issues_rule ON issues(rule_id)",
            
            # ------------------------------------------------------------------
            # Analysis results table - 分析结果表（存储完整 JSON 响应）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS analysis_results (
                id SERIAL PRIMARY KEY,
                job_id INTEGER UNIQUE NOT NULL REFERENCES analysis_jobs(id) ON DELETE CASCADE,
                ai_findings JSONB DEFAULT '[]',
                rule_findings JSONB DEFAULT '[]',
                merged_result JSONB DEFAULT '{}',
                raw_response JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_results_job ON analysis_results(job_id)",
        ]
    },
    {
        "id": "2026-01-14_0002_constraints_and_indexes",
        "description": "Enhance constraints and indexes: file_hash index, issues composite index",
        "sql": [
            # ------------------------------------------------------------------
            # Analysis Jobs - 优化查询与排重
            # ------------------------------------------------------------------
            # 允许文件哈希重复（不同次上传），但加索引用于快速查找历史记录
            "CREATE INDEX IF NOT EXISTS idx_jobs_file_hash ON analysis_jobs(file_hash)",
            
            # ------------------------------------------------------------------
            # Issues - 优化过滤查询
            # ------------------------------------------------------------------
            # 常用查询：某个任务下特定严重程度的问题
            "CREATE INDEX IF NOT EXISTS idx_issues_job_severity ON issues(job_id, severity)",
        ]
    },
    {
        "id": "2026-01-14_0003_qc_framework",
        "description": "QC Framework: rules, versions, runs, and findings",
        "sql": [
            # ------------------------------------------------------------------
            # QC Rule Definitions - 规则定义库
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS qc_rule_definitions (
                rule_key VARCHAR(100) PRIMARY KEY,
                domain VARCHAR(50) NOT NULL,
                name VARCHAR(255) NOT NULL,
                severity VARCHAR(20) DEFAULT 'warning',
                tolerance NUMERIC DEFAULT 0,
                description TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_qc_rules_domain ON qc_rule_definitions(domain)",

            # ------------------------------------------------------------------
            # QC Rule Versions - 规则版本控制
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS qc_rule_versions (
                id SERIAL PRIMARY KEY,
                rule_key VARCHAR(100) NOT NULL REFERENCES qc_rule_definitions(rule_key) ON DELETE CASCADE,
                version VARCHAR(20) NOT NULL,
                params_json JSONB DEFAULT '{}',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(rule_key, version)
            )
            """,

            # ------------------------------------------------------------------
            # QC Runs - 执行记录
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS qc_runs (
                id SERIAL PRIMARY KEY,
                job_id INTEGER REFERENCES analysis_jobs(id) ON DELETE CASCADE,
                run_type VARCHAR(50) DEFAULT 'automated', -- manual, automated
                status VARCHAR(50) DEFAULT 'running', -- running, completed, failed
                started_at TIMESTAMPTZ DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_qc_runs_job ON qc_runs(job_id)",

            # ------------------------------------------------------------------
            # QC Findings - 具体发现
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS qc_findings (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES qc_runs(id) ON DELETE CASCADE,
                rule_key VARCHAR(100) NOT NULL REFERENCES qc_rule_definitions(rule_key),
                status VARCHAR(20) NOT NULL, -- pass, fail, warning
                lhs_value TEXT, -- 左值 (如果有对比)
                rhs_value TEXT, -- 右值 (如果有对比)
                diff NUMERIC,   -- 差值 (如果是数值对比)
                evidence_cells JSONB DEFAULT '[]', -- 相关单元格坐标
                message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_qc_findings_run ON qc_findings(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_qc_findings_rule ON qc_findings(rule_key)",
            "CREATE INDEX IF NOT EXISTS idx_qc_findings_status ON qc_findings(status)",
        ]
    },
    {
        "id": "2026-01-14_0004_fiscal_schema",
        "description": "Fiscal data schema: org_units, documents, versions, cells, facts",
        "sql": [
            # ------------------------------------------------------------------
            # org_units - 组织单位维表
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS org_units (
                id SERIAL PRIMARY KEY,
                org_name TEXT NOT NULL UNIQUE,
                region TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,

            # ------------------------------------------------------------------
            # fiscal_documents - 财政文档（按单位/年度/类型唯一）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS fiscal_documents (
                id SERIAL PRIMARY KEY,
                org_unit_id INTEGER NOT NULL REFERENCES org_units(id) ON DELETE CASCADE,
                fiscal_year INTEGER NOT NULL,
                doc_type TEXT NOT NULL,
                currency_unit TEXT DEFAULT '万元',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(org_unit_id, fiscal_year, doc_type)
            )
            """,

            # ------------------------------------------------------------------
            # fiscal_document_versions - 文档版本（支持重复上传）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS fiscal_document_versions (
                id SERIAL PRIMARY KEY,
                document_id INTEGER NOT NULL REFERENCES fiscal_documents(id) ON DELETE CASCADE,
                file_hash TEXT,
                storage_key TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(document_id, file_hash)
            )
            """,

            # ------------------------------------------------------------------
            # fiscal_table_cells - 原子层证据（单元格）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS fiscal_table_cells (
                id SERIAL PRIMARY KEY,
                document_version_id INTEGER NOT NULL REFERENCES fiscal_document_versions(id) ON DELETE CASCADE,
                table_code TEXT NOT NULL,
                row_idx INTEGER NOT NULL,
                col_idx INTEGER NOT NULL,
                raw_text TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(document_version_id, table_code, row_idx, col_idx)
            )
            """,

            # ------------------------------------------------------------------
            # fact_fiscal_line_items - 事实层（规范化行项目）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS fact_fiscal_line_items (
                id SERIAL PRIMARY KEY,
                document_version_id INTEGER NOT NULL REFERENCES fiscal_document_versions(id) ON DELETE CASCADE,
                table_code TEXT NOT NULL,
                statement_code TEXT NOT NULL,
                classification_type TEXT NOT NULL,
                classification_code TEXT,
                classification_name TEXT,
                measure TEXT NOT NULL,
                amount NUMERIC,
                extra_dims JSONB DEFAULT '{}',
                row_order NUMERIC,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
        ]
    },
    {
        "id": "2026-01-14_0005_fiscal_indexes",
        "description": "Fiscal schema indexes for common queries",
        "sql": [
            # 按单位+年度查文档
            "CREATE INDEX IF NOT EXISTS idx_docs_org_year ON fiscal_documents(org_unit_id, fiscal_year)",
            # 按版本+表号查 cells（勾稽证据）
            "CREATE INDEX IF NOT EXISTS idx_cells_version_table ON fiscal_table_cells(document_version_id, table_code)",
            # 按版本+表号查 facts
            "CREATE INDEX IF NOT EXISTS idx_facts_version_table ON fact_fiscal_line_items(document_version_id, table_code)",
            # 按分类码汇总
            "CREATE INDEX IF NOT EXISTS idx_facts_classification ON fact_fiscal_line_items(classification_code)",
            # 按指标查对比
            "CREATE INDEX IF NOT EXISTS idx_facts_measure ON fact_fiscal_line_items(measure)",
        ]
    },
    {
        "id": "2026-01-14_0006_qc_tables",
        "description": "QC rule engine tables: definitions, runs, and findings",
        "sql": [
            # ------------------------------------------------------------------
            # qc_rule_definitions - 规则定义（简化版，不依赖之前的 qc 表）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS qc_rule_definitions_v2 (
                rule_key VARCHAR(50) PRIMARY KEY,
                scope VARCHAR(50) NOT NULL,
                severity VARCHAR(20) DEFAULT 'warning',
                tolerance NUMERIC DEFAULT 0.01,
                description TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,

            # ------------------------------------------------------------------
            # qc_runs - 规则执行记录
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS qc_runs_v2 (
                id SERIAL PRIMARY KEY,
                document_version_id INTEGER NOT NULL REFERENCES fiscal_document_versions(id) ON DELETE CASCADE,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                finished_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_qc_runs_v2_version ON qc_runs_v2(document_version_id)",

            # ------------------------------------------------------------------
            # qc_findings - 规则执行结果
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS qc_findings_v2 (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES qc_runs_v2(id) ON DELETE CASCADE,
                rule_key VARCHAR(50) NOT NULL,
                status VARCHAR(20) NOT NULL,
                lhs_value TEXT,
                rhs_value TEXT,
                diff NUMERIC,
                evidence_cells BIGINT[] DEFAULT '{}',
                message TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_qc_findings_v2_run ON qc_findings_v2(run_id)",
            "CREATE INDEX IF NOT EXISTS idx_qc_findings_v2_rule ON qc_findings_v2(rule_key)",
        ]
    },
    {
        "id": "2026-01-14_0007_qc_seed_rules",
        "description": "Seed QC rule definitions for fiscal checking",
        "sql": [
            """
            INSERT INTO qc_rule_definitions_v2 (rule_key, scope, severity, tolerance, description)
            VALUES 
                ('R001', 'expenditure', 'error', 0.01, '总支出=基本支出+项目支出'),
                ('R002', 'function', 'warning', 0.01, '功能分类一级汇总=合计'),
                ('R003', 'balance', 'error', 0.01, '总收入=总支出'),
                ('R004', 'economic', 'warning', 0.01, '基本支出经济分类汇总=基本支出合计'),
                ('R005', 'three_public', 'info', 0.01, '三公经费合计=子项之和')
            ON CONFLICT (rule_key) DO NOTHING
            """,
        ]
    },
    {
        "id": "2026-01-14_0008_rule_versioning",
        "description": "Rule versioning with params_json for configurable rule execution",
        "sql": [
            # ------------------------------------------------------------------
            # qc_rule_versions - 规则版本化（支持参数外置）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS qc_rule_versions (
                id SERIAL PRIMARY KEY,
                rule_key VARCHAR(50) NOT NULL,
                version VARCHAR(20) NOT NULL DEFAULT '1.0.0',
                params_json JSONB DEFAULT '{}',
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(rule_key, version)
            )
            """,
            "ALTER TABLE qc_rule_versions ADD COLUMN IF NOT EXISTS params_json JSONB DEFAULT '{}'",
            "ALTER TABLE qc_rule_versions ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE",
            "ALTER TABLE qc_rule_versions ALTER COLUMN version SET DEFAULT '1.0.0'",
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conrelid = 'qc_rule_versions'::regclass
                      AND conname = 'qc_rule_versions_rule_key_fkey'
                ) THEN
                    ALTER TABLE qc_rule_versions DROP CONSTRAINT qc_rule_versions_rule_key_fkey;
                END IF;
            END
            $$;
            """,
            "CREATE INDEX IF NOT EXISTS idx_qc_rule_versions_active ON qc_rule_versions(rule_key, is_active)",

            # Seed initial versions for R001-R005
            """
            INSERT INTO qc_rule_versions (rule_key, version, params_json, is_active)
            VALUES 
                ('R001', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_03_expenditure", "null_as_zero": false}'::jsonb, TRUE),
                ('R002', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_03_expenditure", "classification_length": 3}'::jsonb, TRUE),
                ('R003', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_01_income_expenditure_total", "null_as_zero": false}'::jsonb, TRUE),
                ('R004', '1.0.0', '{"tolerance": 0.01, "economic_table": "FIN_06_basic_expenditure", "expenditure_table": "FIN_03_expenditure"}'::jsonb, TRUE),
                ('R005', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_07_three_public", "null_as_zero": false}'::jsonb, TRUE)
            ON CONFLICT (rule_key, version) DO NOTHING
            """,
        ]
    },
    {
        "id": "2026-01-14_0009_job_orchestration",
        "description": "Job orchestration tables: batches and jobs with 4-stage pipeline tracking",
        "sql": [
            # ------------------------------------------------------------------
            # ingestion_batches - 批次管理（可选，用于批量上传）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS ingestion_batches (
                id SERIAL PRIMARY KEY,
                batch_name TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                completed_at TIMESTAMPTZ,
                status TEXT DEFAULT 'pending'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_batches_status ON ingestion_batches(status)",

            # ------------------------------------------------------------------
            # jobs - Job 管理（四阶段流水线）
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id SERIAL PRIMARY KEY,
                document_version_id INTEGER REFERENCES fiscal_document_versions(id) ON DELETE CASCADE,
                batch_id INTEGER REFERENCES ingestion_batches(id),
                
                -- Stage tracking
                current_stage TEXT DEFAULT 'parse',
                status TEXT DEFAULT 'queued',
                
                -- Stage-specific logs and IDs
                parse_log JSONB DEFAULT '{}',
                materialize_log JSONB DEFAULT '{}',
                qc_run_id INTEGER REFERENCES qc_runs_v2(id),
                report_path TEXT,
                
                -- Timestamps
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                failed_at TIMESTAMPTZ,
                failure_reason TEXT,
                
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_jobs_version ON jobs(document_version_id)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, current_stage)",
            "CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id)",
        ]
    },
    {
        "id": "2026-01-14_0010_table_recognition",
        "description": "Table recognition and column mapping for adaptive parsing",
        "sql": [
            # ------------------------------------------------------------------
            # fiscal_table_instances - 表实例识别
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS fiscal_table_instances (
                id SERIAL PRIMARY KEY,
                document_version_id INTEGER NOT NULL REFERENCES fiscal_document_versions(id) ON DELETE CASCADE,
                
                -- 识别结果
                table_code TEXT NOT NULL,
                source_title TEXT,
                confidence FLOAT DEFAULT 0.0,
                
                -- 位置信息
                page_number INTEGER,
                row_start INTEGER,
                row_end INTEGER,
                
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(document_version_id, table_code)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_table_instances_version ON fiscal_table_instances(document_version_id)",
            "CREATE INDEX IF NOT EXISTS idx_table_instances_code ON fiscal_table_instances(table_code)",

            # ------------------------------------------------------------------
            # fiscal_column_mappings - 列映射
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS fiscal_column_mappings (
                id SERIAL PRIMARY KEY,
                table_instance_id INTEGER NOT NULL REFERENCES fiscal_table_instances(id) ON DELETE CASCADE,
                
                -- 源列信息
                source_col_idx INTEGER NOT NULL,
                source_col_name TEXT,
                
                -- 映射到规范 measure
                canonical_measure TEXT NOT NULL,
                confidence FLOAT DEFAULT 0.0,
                
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(table_instance_id, source_col_idx)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_column_mappings_instance ON fiscal_column_mappings(table_instance_id)",
        ]
    },
    {
        "id": "2026-01-14_0011_seed_rules_r006_r010",
        "description": "Seed extended QC rules R006-R010 with configurations",
        "sql": [
            # Seed rule definitions for R006-R010
            """
            INSERT INTO qc_rule_definitions_v2 (rule_key, scope, severity, tolerance, description)
            VALUES 
                ('R006', 'income', 'error', 0.01, '本年收入=财政拨款+事业+经营+其他'),
                ('R007', 'cross_table', 'error', 0.01, '支出决算表合计=收入支出总表支出'),
                ('R008', 'project', 'warning', 0.01, '项目支出汇总=项目明细总计'),
                ('R009', 'gov_fund', 'info', 0.01, '政府性基金支出=基本+项目'),
                ('R010', 'state_capital', 'info', 0.01, '国有资本支出=基本+项目')
            ON CONFLICT (rule_key) DO NOTHING
            """,
            
            # Seed rule versions with params_json
            """
            INSERT INTO qc_rule_versions (rule_key, version, params_json, is_active)
            VALUES 
                ('R006', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_02_income"}'::jsonb, TRUE),
                ('R007', '1.0.0', '{"tolerance": 0.01, "source_table": "FIN_03_expenditure", "target_table": "FIN_01_income_expenditure_total"}'::jsonb, TRUE),
                ('R008', '1.0.0', '{"tolerance": 0.01, "summary_table": "FIN_03_expenditure", "detail_table": "FIN_04_project_expenditure"}'::jsonb, TRUE),
                ('R009', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_08_gov_fund", "allow_empty": true}'::jsonb, TRUE),
                ('R010', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_09_state_capital", "allow_empty": true}'::jsonb, TRUE)
            ON CONFLICT (rule_key, version) DO NOTHING
            """,
        ]
    },
    {
        "id": "2026-01-14_0012_seed_rules_r011_r015",
        "description": "Seed extended QC rules R011-R015",
        "sql": [
            """
            INSERT INTO qc_rule_definitions_v2 (rule_key, scope, severity, tolerance, description)
            VALUES 
                ('R011', 'hierarchy', 'error', 0.01, '功能分类一级=二级之和'),
                ('R012', 'three_public', 'warning', 0.01, '三公经费决算≤预算'),
                ('R013', 'budget', 'warning', 30.0, '预算调整率≤30%'),
                ('R014', 'anomaly', 'warning', 0, '异常数值检测(负数/超大值)'),
                ('R015', 'completeness', 'error', 0, '必填表存在性检查')
            ON CONFLICT (rule_key) DO NOTHING
            """,
            """
            INSERT INTO qc_rule_versions (rule_key, version, params_json, is_active)
            VALUES 
                ('R011', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_05_general_public_expenditure"}'::jsonb, TRUE),
                ('R012', '1.0.0', '{"tolerance": 0.01, "table_code": "FIN_07_three_public"}'::jsonb, TRUE),
                ('R013', '1.0.0', '{"tolerance": 0.01, "threshold_percent": 30}'::jsonb, TRUE),
                ('R014', '1.0.0', '{"max_value": 10000000}'::jsonb, TRUE),
                ('R015', '1.0.0', '{"required_tables": ["FIN_01", "FIN_03", "FIN_05"]}'::jsonb, TRUE)
            ON CONFLICT (rule_key, version) DO NOTHING
            """,
        ]
    },
    {
        "id": "2026-03-07_0013_structured_ingest_enhancements",
        "description": "Add structured PDF ingest metadata and hierarchical fact fields",
        "sql": [
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS normalized_text TEXT",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS numeric_value DOUBLE PRECISION",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS page_number INTEGER",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS bbox JSONB",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS is_header BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS row_span INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS col_span INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS unit_hint TEXT",
            "ALTER TABLE fiscal_table_cells ADD COLUMN IF NOT EXISTS extraction_method TEXT",
            "ALTER TABLE fact_fiscal_line_items ADD COLUMN IF NOT EXISTS classification_level INTEGER",
            "ALTER TABLE fact_fiscal_line_items ADD COLUMN IF NOT EXISTS parent_classification_code TEXT",
            "ALTER TABLE fact_fiscal_line_items ADD COLUMN IF NOT EXISTS hierarchy_path TEXT[] DEFAULT '{}'",
            "ALTER TABLE fact_fiscal_line_items ADD COLUMN IF NOT EXISTS source_page_number INTEGER",
            "ALTER TABLE fact_fiscal_line_items ADD COLUMN IF NOT EXISTS source_cell_ids BIGINT[] DEFAULT '{}'",
            "ALTER TABLE fact_fiscal_line_items ADD COLUMN IF NOT EXISTS parse_confidence DOUBLE PRECISION",
            "CREATE INDEX IF NOT EXISTS idx_cells_version_page ON fiscal_table_cells(document_version_id, page_number)",
            "CREATE INDEX IF NOT EXISTS idx_cells_numeric ON fiscal_table_cells(document_version_id, numeric_value)",
            "CREATE INDEX IF NOT EXISTS idx_facts_parent_code ON fact_fiscal_line_items(parent_classification_code)",
            "CREATE INDEX IF NOT EXISTS idx_facts_level ON fact_fiscal_line_items(classification_level)",
        ]
    },
    {
        "id": "2026-03-07_0014_ps_shared_schema",
        "description": "Add PS/tianbaoxitong-aligned shared report tables",
        "sql": [
            "CREATE EXTENSION IF NOT EXISTS pgcrypto",
            """
            CREATE TABLE IF NOT EXISTS org_department (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                parent_id UUID REFERENCES org_department(id) ON DELETE SET NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS org_unit (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                department_id UUID NOT NULL REFERENCES org_department(id) ON DELETE RESTRICT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS org_dept_annual_report (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                department_id UUID NOT NULL REFERENCES org_department(id) ON DELETE CASCADE,
                unit_id UUID NOT NULL REFERENCES org_unit(id) ON DELETE RESTRICT,
                year INTEGER NOT NULL,
                report_type TEXT NOT NULL,
                file_name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_hash TEXT NOT NULL,
                file_size BIGINT NOT NULL DEFAULT 0,
                uploaded_by UUID,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(department_id, unit_id, year, report_type)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS org_dept_table_data (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                report_id UUID NOT NULL REFERENCES org_dept_annual_report(id) ON DELETE CASCADE,
                department_id UUID NOT NULL REFERENCES org_department(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                report_type TEXT NOT NULL,
                table_key TEXT NOT NULL,
                table_title TEXT,
                page_numbers INTEGER[] DEFAULT '{}',
                row_count INTEGER NOT NULL DEFAULT 0,
                col_count INTEGER NOT NULL DEFAULT 0,
                data_json JSONB NOT NULL,
                created_by UUID,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(report_id, table_key)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS org_dept_line_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                report_id UUID NOT NULL REFERENCES org_dept_annual_report(id) ON DELETE CASCADE,
                department_id UUID NOT NULL REFERENCES org_department(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                report_type TEXT NOT NULL,
                table_key TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                class_code TEXT,
                type_code TEXT,
                item_code TEXT,
                item_name TEXT,
                values_json JSONB NOT NULL,
                created_by UUID,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(report_id, table_key, row_index)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_org_department_parent_sort ON org_department(parent_id, sort_order)",
            "CREATE INDEX IF NOT EXISTS idx_org_unit_department_sort ON org_unit(department_id, sort_order)",
            "CREATE INDEX IF NOT EXISTS idx_org_unit_name ON org_unit(name)",
            "CREATE INDEX IF NOT EXISTS idx_dept_report_scope ON org_dept_annual_report(department_id, unit_id, year)",
            "CREATE INDEX IF NOT EXISTS idx_dept_table_report ON org_dept_table_data(report_id)",
            "CREATE INDEX IF NOT EXISTS idx_dept_table_year ON org_dept_table_data(department_id, year)",
            "CREATE INDEX IF NOT EXISTS idx_dept_line_report ON org_dept_line_items(report_id)",
            "CREATE INDEX IF NOT EXISTS idx_dept_line_year ON org_dept_line_items(department_id, year)",
            "CREATE INDEX IF NOT EXISTS idx_dept_line_table_key ON org_dept_line_items(table_key)",
        ]
    },
    {
        "id": "2026-07-13_0015_document_storage_metadata",
        "description": "Track durable original-document storage metadata for each fiscal version",
        "sql": [
            "ALTER TABLE fiscal_document_versions ADD COLUMN IF NOT EXISTS storage_backend TEXT NOT NULL DEFAULT 'filesystem'",
            "ALTER TABLE fiscal_document_versions ADD COLUMN IF NOT EXISTS original_filename TEXT",
            "ALTER TABLE fiscal_document_versions ADD COLUMN IF NOT EXISTS file_size_bytes BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE fiscal_document_versions ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'application/pdf'",
            "CREATE INDEX IF NOT EXISTS idx_document_versions_storage_key ON fiscal_document_versions(storage_key)",
        ]
    },
    {
        "id": "2026-07-13_0016_workflow_state_mirror",
        "description": "Mirror remediation workflow state from the durable upload volume into PostgreSQL",
        "sql": [
            """
            CREATE TABLE IF NOT EXISTS workflow_issue_records (
                workflow_key TEXT PRIMARY KEY,
                job_uuid TEXT NOT NULL,
                issue_id TEXT NOT NULL,
                status TEXT NOT NULL,
                title TEXT,
                severity TEXT,
                page_number INTEGER,
                organization_id TEXT,
                organization_name TEXT,
                note TEXT,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_workflow_issue_records_job ON workflow_issue_records(job_uuid)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_issue_records_status ON workflow_issue_records(status)",
            """
            CREATE TABLE IF NOT EXISTS workflow_state_mirror (
                mirror_id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (mirror_id),
                revision BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS workflow_remediation_packages (
                package_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                organization_id TEXT,
                organization_name TEXT,
                job_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                issue_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
        ]
    },
    {
        "id": "2026-08-26_0017_nullable_report_year",
        "description": (
            "Allow NULL fiscal year when the report year cannot be recognized (B-02 / P0-03). "
            "Postgres treats multiple NULLs as distinct in a plain UNIQUE constraint, so the "
            "affected uniqueness is re-expressed as COALESCE(year, -1) expression indexes."
        ),
        "sql": [
            # 1) 放开 NOT NULL：无法识别年份的材料不再被迫写成 2000。
            #    该方向是安全的（约束放宽），回滚说明见 docs/MIGRATION_0017_NULLABLE_YEAR.md。
            "ALTER TABLE fiscal_documents ALTER COLUMN fiscal_year DROP NOT NULL",
            "ALTER TABLE org_dept_annual_report ALTER COLUMN year DROP NOT NULL",
            "ALTER TABLE org_dept_table_data ALTER COLUMN year DROP NOT NULL",
            "ALTER TABLE org_dept_line_items ALTER COLUMN year DROP NOT NULL",
            # 2) 先建 COALESCE 表达式唯一索引，再删原 UNIQUE 约束，
            #    顺序反过来会让并发写入短暂失去唯一性保护。
            #    COALESCE(year, -1) 把"年份未知"折叠成同一个键值，
            #    否则 Postgres 视多个 NULL 互不冲突，同一单位的未知年份文档会不断新增重复行。
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_fiscal_documents_org_year_type "
            "ON fiscal_documents (org_unit_id, COALESCE(fiscal_year, -1), doc_type)",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_dept_report_scope_type "
            "ON org_dept_annual_report (department_id, unit_id, COALESCE(year, -1), report_type)",
            # 3) 删除被替代的原 UNIQUE 约束。约束名由 Postgres 自动生成且会因长度被截断，
            #    因此按约束定义反查真实名字，不硬编码猜测的名称。
            """
            DO $$
            DECLARE
                target_name text;
            BEGIN
                SELECT conname INTO target_name
                FROM pg_constraint
                WHERE conrelid = 'fiscal_documents'::regclass
                  AND contype = 'u'
                  AND pg_get_constraintdef(oid) = 'UNIQUE (org_unit_id, fiscal_year, doc_type)';
                IF target_name IS NOT NULL THEN
                    EXECUTE format(
                        'ALTER TABLE fiscal_documents DROP CONSTRAINT %I', target_name
                    );
                END IF;
            END $$;
            """,
            """
            DO $$
            DECLARE
                target_name text;
            BEGIN
                SELECT conname INTO target_name
                FROM pg_constraint
                WHERE conrelid = 'org_dept_annual_report'::regclass
                  AND contype = 'u'
                  AND pg_get_constraintdef(oid)
                      = 'UNIQUE (department_id, unit_id, year, report_type)';
                IF target_name IS NOT NULL THEN
                    EXECUTE format(
                        'ALTER TABLE org_dept_annual_report DROP CONSTRAINT %I', target_name
                    );
                END IF;
            END $$;
            """,
        ]
    },
    {
        "id": "2026-08-26_0018_report_scope_key",
        "description": (
            "Add scope_key to org_dept_annual_report so low-confidence reports "
            "(fallback_name org match or unknown year) are keyed per document "
            "instead of being merged into one (dept, unit, year, type) row (P0-09)."
        ),
        "sql": [
            # scope_key 为空串 = 按 (部门, 单位, 年度, 类型) 归并（原有行为）；
            # 非空（checksum）= 按具体文档归并，避免不同材料被强行并入同一 report_id。
            # 默认空串而非 NULL，是为了让唯一索引不必再套一层 COALESCE。
            "ALTER TABLE org_dept_annual_report ADD COLUMN IF NOT EXISTS scope_key TEXT NOT NULL DEFAULT ''",
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_dept_report_scope_type_key "
            "ON org_dept_annual_report "
            "(department_id, unit_id, COALESCE(year, -1), report_type, scope_key)",
            # 迁移 0017 建的索引被本索引取代（前者是后者去掉 scope_key 的前缀）。
            # 保留它会阻止同一 scope 下按文档区分的多行插入，必须删除。
            "DROP INDEX IF EXISTS uq_dept_report_scope_type",
            "CREATE INDEX IF NOT EXISTS idx_dept_report_scope_key "
            "ON org_dept_annual_report(scope_key) WHERE scope_key <> ''",
        ]
    },
    {
        "id": "2026-09-21_0019_material_slots",
        "description": (
            "Material ledger foundation: material_slots (地区+主管部门+主体+财政年度+文种+口径) "
            "+ material_sources (官网来源/发布日期) + fiscal_document_versions.slot_id 绑定。"
            "只新增，不修改任何既有核心表的数据。"
        ),
        "sql": [
            # 0004 已建过扩展，这里再声明一次是为了让"只重放本迁移"也能成功
            # （迁移必须能单独幂等执行，不依赖"前面一定跑过"）。
            "CREATE EXTENSION IF NOT EXISTS pgcrypto",

            # ------------------------------------------------------------------
            # material_slots - 材料槽位：本轮的核心业务对象
            #
            # 为什么要有它：当前系统只有"跑过哪些 job"，没有"哪些材料应该有"。
            # 一条 slot = 地区 + 主管部门 + 主体 + 主体层级 + 财政年度 + 文种 + 口径。
            # PDF 是 slot 下的文件版本，job 是文件版本下的一次运行。
            #
            # 关于 subject_kind / material_scope / caliber 三个维度为什么这样拆：
            #   - subject_kind  = 谁在报（department / unit / government）
            #   - material_scope= 这份材料覆盖多大范围（部门汇总 / 单位本级 / 政府）
            #   - caliber       = 材料内数字含不含下级（summary / self / unknown）
            #   前两者决定"这是哪一条应收材料"，进唯一键；caliber 描述文档内容口径，
            #   由 DocumentProfile 识别，**不进唯一键**——它经常是 unknown，
            #   放进唯一键会让同一份材料被反复建成新 slot（正是本轮要消灭的串线）。
            #   caliber 只作为属性随最近一次识别更新，并在冲突时转 mapping_required。
            #
            # 关于 subject_org_id：引用的是 `data/organizations.json` 那套组织目录的
            # md5 id（level:parent:name 的哈希）。该 id 天然把"部门"和"同名本级单位"
            # 分开，正是本轮要的隔离。它不建外键，因为组织主数据在 JSON 里而不是库里，
            # 库里造镜像等于再开第四套组织主数据（PLAN §3.5 明确禁止）。
            # 代价是丢了数据库级引用完整性，用 subject_org_code + 名称快照兜底。
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS material_slots (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                -- 身份键：由 (subject_org_id, subject_kind, material_scope, report_kind,
                -- fiscal_year, mapping_key) 规范化后取 sha256，用于日志/URL/跨系统引用。
                slot_key TEXT NOT NULL UNIQUE,

                -- 行政区划。独立于主体组织：政府级材料的主体就是区本身，
                -- 部门/单位材料的区县由上级推导。用于台账按区县聚合。
                jurisdiction_org_id TEXT,
                jurisdiction_name TEXT,

                -- 主管部门（部门汇总材料的主体；单位材料则是单位的上级部门）
                department_org_id TEXT,
                department_name TEXT,

                -- 具体主体组织
                subject_org_id TEXT NOT NULL,
                subject_org_name TEXT NOT NULL,
                -- 主体在组织目录里的稳定代码（PS 的 DEPT_/UNIT_ code），可空。
                -- 组织目录的 md5 id 由"名称"参与哈希，改名即换 id；code 不随改名变，
                -- 因此同时记录两者，便于日后组织重命名后仍能把 slot 认回来。
                subject_org_code TEXT,

                -- 'unknown' 是刻意允许的取值：识别不到主体/文种的**真实**材料
                -- 必须能在台账里被看见（状态 mapping_required 等人工确认）。
                -- 拒绝 unknown 会让这类材料无处安放，"应收未收"统计直接漏掉它们。
                subject_kind TEXT NOT NULL
                    CONSTRAINT ck_material_slots_subject_kind
                    CHECK (subject_kind IN ('department', 'unit', 'government', 'unknown')),
                subject_level TEXT NOT NULL
                    CONSTRAINT ck_material_slots_subject_level
                    CHECK (subject_level IN ('department', 'unit', 'government', 'unknown')),
                material_scope TEXT NOT NULL
                    CONSTRAINT ck_material_slots_material_scope
                    CHECK (material_scope IN (
                        'department_summary', 'unit_self', 'government', 'unknown'
                    )),
                caliber TEXT NOT NULL DEFAULT 'unknown'
                    CONSTRAINT ck_material_slots_caliber
                    CHECK (caliber IN ('summary', 'self', 'unknown')),
                -- 已确认口径与后来识别到的口径互相矛盾时，把**矛盾的观测值**记在这里。
                -- caliber 保留先确认的值不动，候选非空即表示"等人工裁决"。
                -- 没有这一列的话，口径只能靠"取最新一次识别"来决定；那等于让
                -- "两笔数字能不能相加"随分析次数漂移，而且是静默漂移。
                -- 用可空文本而不是布尔：布尔只说明有矛盾，看不出矛盾的是什么。
                caliber_conflict_candidate TEXT
                    CONSTRAINT ck_material_slots_caliber_candidate
                    CHECK (
                        caliber_conflict_candidate IS NULL
                        OR caliber_conflict_candidate IN ('summary', 'self')
                    ),

                -- 财政年度。允许 NULL：识别不到年份时不能兜底成某个具体年份，
                -- 归一为 NULL 并让 slot 停在 mapping_required 等人工确认。
                fiscal_year INTEGER,
                report_kind TEXT NOT NULL
                    CONSTRAINT ck_material_slots_report_kind
                    CHECK (report_kind IN ('budget', 'final', 'unknown')),

                applicability_status TEXT NOT NULL DEFAULT 'applicable'
                    CONSTRAINT ck_material_slots_applicability
                    CHECK (applicability_status IN ('applicable', 'not_applicable', 'unresolved')),
                -- 人工判定"不适用/待定"的依据，不允许无依据改判。
                applicability_note TEXT,

                -- 应收/公开截止时间。NULL = 未知，未知不得计为"逾期未上传"。
                due_at TIMESTAMPTZ,
                expected_source_url TEXT,

                -- 缓存状态。权威来源是 src/services/material_slot_status.py 的纯函数，
                -- 这里落库是为了台账列表能一次 SQL 聚合（PLAN §12 禁止逐条实时计算）。
                status TEXT NOT NULL DEFAULT 'mapping_required'
                    CONSTRAINT ck_material_slots_status
                    CHECK (status IN (
                        'not_due', 'missing', 'uploaded', 'processing',
                        'review_required', 'reviewing', 'completed',
                        'not_applicable', 'mapping_required', 'failed'
                    )),
                -- 状态成因（机器可读）。status 只有 10 个值，不足以表达
                -- "没到期"与"到期时间未知"的区别，后者必须在界面上可分辨，
                -- 因此单独存原因码，避免把未知当成已知。
                status_reason TEXT,

                -- 身份无法可靠确定时的区分键（与 org_dept_annual_report.scope_key 同一手法）：
                -- 空串 = 正常按身份归并；非空（如文档 sha256）= 按具体文档单独建槽，
                -- 否则同单位所有"年份未知"材料会挤进同一个 slot 互相覆盖。
                mapping_key TEXT NOT NULL DEFAULT '',

                -- 当前文件版本指针。不额外在 fiscal_document_versions 上放 is_current：
                -- 两处都记"谁是当前版本"必然漂移，指针放在 slot 上只有一个真相。
                current_document_version_id INTEGER
                    REFERENCES fiscal_document_versions(id) ON DELETE SET NULL,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,

            # 复合唯一键：与 slot_key 表达同一件事，但用可读的列组合兜住
            # "哈希算错/序列化口径变更"这类静默合并。冲突时插入报错而不是悄悄合并，
            # 属于 fail-closed。
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_material_slots_identity "
            "ON material_slots ("
            "subject_org_id, subject_kind, material_scope, report_kind, "
            "COALESCE(fiscal_year, -1), mapping_key)",

            # ------------------------------------------------------------------
            # material_sources - 材料的来源记录
            #
            # 存在的理由是口径隔离：财政年度来自材料本身，发布日期来自网页。
            # 2024 年度决算在 2025 年发布是常态，两者混用会把年份判错。
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS material_sources (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                slot_id UUID NOT NULL REFERENCES material_slots(id) ON DELETE CASCADE,

                source_kind TEXT NOT NULL DEFAULT 'manual_upload'
                    CONSTRAINT ck_material_sources_kind
                    CHECK (source_kind IN ('official_site', 'manual_upload', 'excel_import')),
                source_url TEXT,
                source_page_title TEXT,
                source_site TEXT,
                -- 网页发布日期，与 slot.fiscal_year 严格分开
                published_at TIMESTAMPTZ,
                discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_checked_at TIMESTAMPTZ,
                -- 栏目页内容指纹：用于日后判断"官网是否仍挂着同一份材料"
                source_page_hash TEXT,
                status TEXT NOT NULL DEFAULT 'active'
                    CONSTRAINT ck_material_sources_status
                    CHECK (status IN ('active', 'unreachable', 'superseded', 'retired')),

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,

            # 唯一键用 COALESCE 表达式：Postgres 视多个 NULL 互不冲突，
            # 不折叠的话"同一 slot 多条手工来源（无 URL）"会无限重复。
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_material_sources_slot_url "
            "ON material_sources (slot_id, COALESCE(source_url, ''))",

            # ------------------------------------------------------------------
            # fiscal_document_versions.slot_id - 把已有的文件版本挂到槽位上
            # 允许 NULL：历史版本、以及身份不可靠的新版本不强行归属。
            # ON DELETE SET NULL 而不是 CASCADE：删 slot 不该删掉原始 PDF 版本记录。
            # ------------------------------------------------------------------
            "ALTER TABLE fiscal_document_versions "
            "ADD COLUMN IF NOT EXISTS slot_id UUID "
            "REFERENCES material_slots(id) ON DELETE SET NULL",

            # ------------------------------------------------------------------
            # 台账查询索引（PLAN §12：万级 slot，禁止逐条实时计算）
            # ------------------------------------------------------------------
            "CREATE INDEX IF NOT EXISTS idx_material_slots_subject ON material_slots(subject_org_id)",
            "CREATE INDEX IF NOT EXISTS idx_material_slots_department ON material_slots(department_org_id)",
            "CREATE INDEX IF NOT EXISTS idx_material_slots_jurisdiction "
            "ON material_slots(jurisdiction_org_id)",
            # 区级主管部门矩阵：按区县 + 年度 + 文种成组扫描
            "CREATE INDEX IF NOT EXISTS idx_material_slots_scope "
            "ON material_slots(jurisdiction_org_id, fiscal_year, report_kind)",
            # 工作台 KPI：按状态计数
            "CREATE INDEX IF NOT EXISTS idx_material_slots_status ON material_slots(status)",
            # 最近变更列表 / 增量同步
            "CREATE INDEX IF NOT EXISTS idx_material_slots_updated ON material_slots(updated_at DESC)",
            # 超期未上传排查：只索引有截止时间的行
            "CREATE INDEX IF NOT EXISTS idx_material_slots_due "
            "ON material_slots(due_at) WHERE due_at IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_material_sources_slot ON material_sources(slot_id)",
            "CREATE INDEX IF NOT EXISTS idx_document_versions_slot "
            "ON fiscal_document_versions(slot_id)",
        ]
    },
    {
        "id": "2026-09-22_0020_review_lifecycle",
        "description": (
            "人工复核生命周期（WP3-A）：review_sessions 独立业务对象（一条槽位同时"
            "只能有一个 in_progress），把复核结论从「前端点一下就跳走」变成可持久化、"
            "可追溯、可失效的服务端事实；同时给 analysis_jobs 增加分析代际"
            "（analysis_revision + analysis_result_fingerprint），让「同一 job_uuid 的"
            "重新分析」能与「同一结果的重复落库/重放」区分开。只新增，不改历史迁移。"
        ),
        "sql": [
            # 0019 已声明过扩展；这里再声明一次是为了让"只重放本迁移"也能成功。
            "CREATE EXTENSION IF NOT EXISTS pgcrypto",

            # ------------------------------------------------------------------
            # analysis_jobs.analysis_revision —— 分析代际
            #
            # 为什么需要它（WP3-A 最关键的一条红线）：
            # ``_upsert_analysis_job`` 是 ``ON CONFLICT (job_uuid) DO UPDATE``，
            # ``_upsert_analysis_result`` 是 ``ON CONFLICT (job_id) DO UPDATE``。
            # 也就是说**同一个 job_uuid 重新分析时，analysis_jobs / analysis_results
            # 的行是被原地覆盖的**，`job_uuid` 本身不足以区分"分析结果 A"和
            # "同一 job_uuid 上的重新分析结果 B"。复核会话若只绑 job_uuid，
            # 重新分析后旧复核会静默继承新结果——一份没被人看过的分析被算作
            # "已复核完成"。因此必须有显式的代际字段。
            #
            # 语义：``analysis_revision`` = 该 job 上**已经落库过结果**的分析代际数，
            # 0 表示"还没有任何分析结果落库"。它只保证"代际变化"，不代表第几次运行，
            # 也不允许用 ``updated_at`` 顶替——状态重放、metadata 修复同样会更新
            # updated_at，用它当 generation 会让恢复动作误伤复核。
            #
            # 默认值取 0 而不是 1：这样"首次落库结果"恰好把 0 → 1，
            # 不会出现"第一次分析就是第 2 代"这种看不出所以然的编号。
            # 既有历史行保持 0（代际未知），它们下一次真正产出结果时归为第 1 代。
            "ALTER TABLE analysis_jobs "
            "ADD COLUMN IF NOT EXISTS analysis_revision INTEGER NOT NULL DEFAULT 0",

            # 当前已落库分析结果的内容指纹（sha256，只覆盖被复核的分析内容，
            # 不含 progress/timestamp 这类每次都变的字段）。
            #
            # 它区分的是两类"又一次落库"：
            #   - 同一个结果的重复落库 / 断线重放 → 指纹相同 → **不**递增代际；
            #   - 重新分析产生的新结果（内容可能与上一次逐字相同）→ 指纹在
            #     "作业被重置为 queued"时已被清空，因此一定会递增。
            # 只靠内容比较是不够的：重新分析完全可能产出与上一次逐字相同的结果，
            # 而那也必须让旧复核失效（人看的是"这一次的分析"，不是"看起来一样的字"）。
            "ALTER TABLE analysis_jobs "
            "ADD COLUMN IF NOT EXISTS analysis_result_fingerprint TEXT",

            # ------------------------------------------------------------------
            # review_sessions —— 复核会话：与 issue_workflow record、analysis job
            # 都不同的独立业务对象。
            #
            # 为什么不能复用 issue 级 workflow：一份材料可以 V1 分析→复核完成，
            # V2 分析→再次复核。issue 级记录只回答"这一条问题被怎么处理了"，
            # 回答不了"哪一代分析、哪一版文件上的复核结论"。把复核结论塞进
            # issue 记录里，就等于让"复核完成"这个事实随问题条数变化而漂移。
            #
            # 为什么四个字段必须一起绑：``slot_id`` 说是哪条应收材料；
            # ``document_version_id`` 说是哪一版文件；``analysis_job_uuid`` +
            # ``analysis_basis_token`` 说是哪一代分析。只绑 job_id 的话，
            # 同 job 重新分析会让旧复核静默继承新结论（见上）。
            # ------------------------------------------------------------------
            """
            CREATE TABLE IF NOT EXISTS review_sessions (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

                -- 外键用 RESTRICT 而不是 CASCADE：复核历史是审计材料，
                -- 不允许因为删除槽位/版本而静默消失。真要删就得先显式处理历史。
                slot_id UUID NOT NULL
                    REFERENCES material_slots(id) ON DELETE RESTRICT,
                document_version_id INTEGER NOT NULL
                    REFERENCES fiscal_document_versions(id) ON DELETE RESTRICT,

                -- 与 fiscal_document_versions.id 一样，这里存的是**字符串** job_uuid：
                -- 复核上下文是从 analysis_jobs.metadata 里读的 job_uuid，
                -- 而 analysis_jobs.id 是自增代理键，重放历史数据时不一定稳定。
                analysis_job_uuid TEXT NOT NULL
                    REFERENCES analysis_jobs(job_uuid) ON DELETE RESTRICT,

                -- 形如 `<job_uuid>:<analysis_revision>`。它必须能回答
                -- "现在看到的分析结果，与开始复核时的分析结果是否仍是同一代"。
                analysis_basis_token TEXT NOT NULL,

                status TEXT NOT NULL DEFAULT 'in_progress'
                    CONSTRAINT ck_review_sessions_status
                    CHECK (status IN ('in_progress', 'completed', 'invalidated')),

                started_by TEXT NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                completed_by TEXT,
                completed_at TIMESTAMPTZ,

                invalidated_at TIMESTAMPTZ,
                invalidated_reason TEXT,

                -- 复核快照（问题计数 + 覆盖摘要）。刻意**不**存整份 finding /
                -- raw_response：那会让复核记录随着原始分析体积膨胀，而复核
                -- 结论只需要"当时各状态各有多少条、覆盖到什么程度"。
                review_result JSONB NOT NULL DEFAULT '{}'::jsonb,

                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

                -- 终态必须自洽：completed 必须有完成人与完成时间且未被失效；
                -- invalidated 必须有失效时间与失效原因。没有这两条约束，
                -- 一次写错状态的代码就能造出"已完成但没有完成人"这种
                -- 无法追溯、却看起来一切正常的记录。
                CONSTRAINT ck_review_sessions_completed CHECK (
                    status <> 'completed'
                    OR (completed_at IS NOT NULL
                        AND completed_by IS NOT NULL
                        AND invalidated_at IS NULL)
                ),
                CONSTRAINT ck_review_sessions_invalidated CHECK (
                    status <> 'invalidated'
                    OR (invalidated_at IS NOT NULL AND invalidated_reason IS NOT NULL)
                )
            )
            """,

            # 一条槽位同时**只能有一个进行中的复核**。
            #
            # 这是业务不变式，不只是"性能优化"：两个浏览器同时点"进入审核"
            # 若能各建一条活动会话，两边的确认/忽略就会写进两条互不知情的
            # 记录里，最终"复核完成"到底以哪一条为准无法回答。
            # 用 partial unique index 而不是"应用层先查再写"：后者在并发下
            # 一定漏（两个事务都查到"没有活动会话"）。应用层照样会先查一次，
            # 但那次查询只是为了让正常路径不报错，真正的保证在数据库这一层。
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_review_sessions_active "
            "ON review_sessions (slot_id) WHERE status = 'in_progress'",

            # 历史列表 / 当前会话：按槽位取最近若干条
            "CREATE INDEX IF NOT EXISTS idx_review_sessions_slot "
            "ON review_sessions(slot_id, created_at DESC)",
            # 重分析失效钩子按 job_uuid 定位
            "CREATE INDEX IF NOT EXISTS idx_review_sessions_job "
            "ON review_sessions(analysis_job_uuid)",
            # 版本替换失效钩子按 (槽位, 版本) 定位
            "CREATE INDEX IF NOT EXISTS idx_review_sessions_version "
            "ON review_sessions(slot_id, document_version_id)",
        ]
    },
]


async def ensure_migrations_table(conn: asyncpg.Connection, schema: str):
    """Create the schema_migrations tracking table if not exists.
    
    Args:
        conn: Database connection.
        schema: Schema name to create the table in.
    """
    await conn.execute(f'''
        CREATE TABLE IF NOT EXISTS "{schema}".schema_migrations (
            id TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        )
    ''')
    logger.info(f"schema_migrations table ready in schema '{schema}'")


async def get_applied_migrations(conn: asyncpg.Connection, schema: str) -> set:
    """Get set of already applied migration IDs.
    
    Args:
        conn: Database connection.
        schema: Schema name.
        
    Returns:
        Set of applied migration IDs.
    """
    rows = await conn.fetch(f'SELECT id FROM "{schema}".schema_migrations')
    return {row['id'] for row in rows}


async def run_migrations():
    """Run all pending migrations in order.
    
    This function:
    1. Ensures the schema_migrations table exists
    2. Checks which migrations have been applied
    3. Runs pending migrations in order, each in a transaction
    4. Records each successful migration
    
    Raises:
        RuntimeError: If a migration fails.
    """
    logger.info("Starting database migration check...")
    
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    
    async with pool.acquire() as conn:
        # Set search path for this connection
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        # Ensure migrations table exists
        await ensure_migrations_table(conn, schema)
        
        # Get already applied migrations
        applied = await get_applied_migrations(conn, schema)
        logger.info(f"Found {len(applied)} previously applied migrations")
        
        # Run pending migrations
        pending_count = 0
        for migration in MIGRATIONS:
            migration_id = migration["id"]
            
            if migration_id in applied:
                logger.debug(f"Skipping already applied migration: {migration_id}")
                continue
            
            pending_count += 1
            # 变量名刻意写全：`description` 这个泛名在别处可能承载 finding 描述
            # （含材料金额），不适合进白名单；这里的值是迁移定义里写死的静态说明。
            migration_description = migration.get("description", "No description")
            logger.info(f"Applying migration: {migration_id} - {migration_description}")
            
            # Run migration in a transaction
            try:
                async with conn.transaction():
                    for sql in migration["sql"]:
                        await conn.execute(sql)
                    
                    # Record the migration
                    await conn.execute(
                        f'INSERT INTO "{schema}".schema_migrations (id) VALUES ($1)',
                        migration_id
                    )
                
                logger.info(f"✓ Migration {migration_id} applied successfully")
                
            except Exception as e:
                logger.error(f"✗ Migration {migration_id} failed: {e}")
                raise RuntimeError(
                    f"Migration {migration_id} failed: {e}. "
                    "Database may be in an inconsistent state. "
                    "Please fix the migration and restart."
                ) from e
        
        if pending_count == 0:
            logger.info("✓ Database is up to date, no migrations needed")
        else:
            logger.info(f"✓ Applied {pending_count} migration(s) successfully")


async def get_migration_status() -> List[Dict[str, Any]]:
    """Get status of all migrations (applied/pending).
    
    Returns:
        List of dicts with id, description, and applied status.
    """
    pool = await DatabaseConnection.get_pool()
    schema = DatabaseConnection.get_schema()
    
    async with pool.acquire() as conn:
        await conn.execute(f'SET search_path TO "{schema}", public')
        
        try:
            applied = await get_applied_migrations(conn, schema)
        except Exception:
            # Table might not exist yet
            applied = set()
        
        status = []
        for migration in MIGRATIONS:
            mid = migration["id"]
            status.append({
                "id": mid,
                "description": migration.get("description", ""),
                "applied": mid in applied
            })
        
        return status
