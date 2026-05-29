-- ============================================
-- GovBudgetChecker 数据库初始化脚本
-- 用于本地原生 PostgreSQL 安装
-- ============================================
-- 使用方法:
-- 1. 以 postgres 超级用户连接
-- 2. 执行此脚本: psql -U postgres -f scripts/init_db.sql
-- ============================================

-- 创建数据库（如果不存在）
-- 注意：CREATE DATABASE 不能在事务中运行，需要单独执行
-- 如果数据库已存在，此命令会报错，可以忽略

SELECT 'CREATE DATABASE fiscal_db'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'fiscal_db')\gexec

-- 可选：创建专用用户（如果不想用 postgres 用户）
-- CREATE USER govbudget_local WITH PASSWORD 'change_me_local_postgres_password';
-- GRANT ALL PRIVILEGES ON DATABASE fiscal_db TO govbudget_local;

-- 连接到 fiscal_db 后执行以下命令授权（可选）
-- \c fiscal_db
-- GRANT ALL ON SCHEMA public TO govbudget_local;

\echo '=========================================='
\echo 'fiscal_db 数据库创建完成！'
\echo '=========================================='
\echo ''
\echo '下一步：'
\echo '1. 编辑 .env 文件，设置 DATABASE_URL'
\echo '2. 启动后端: python -m uvicorn api.main:app --reload'
\echo '3. 表结构将在启动时自动创建'
