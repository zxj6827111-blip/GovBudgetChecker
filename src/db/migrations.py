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
            description = migration.get("description", "No description")
            logger.info(f"Applying migration: {migration_id} - {description}")
            
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
