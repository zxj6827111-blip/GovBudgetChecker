-- ============================================================
-- Fiscal Data Sample Queries for GovBudgetChecker
-- ============================================================

-- 设置版本 ID（根据实际导入结果调整）
-- 假设：version_id = 2

-- ============================================================
-- Query 1: 查询该单位 2024 决算"本年支出合计/基本支出/项目支出"
-- ============================================================
SELECT 
    f.classification_name,
    f.measure,
    f.amount
FROM fact_fiscal_line_items f
JOIN fiscal_document_versions v ON f.document_version_id = v.id
JOIN fiscal_documents d ON v.document_id = d.id
JOIN org_units o ON d.org_unit_id = o.id
WHERE o.org_name = '上海市普陀区人民政府办公室'
  AND d.fiscal_year = 2024
  AND f.table_code = 'FIN_03_expenditure'
  AND f.classification_code = '合计'
ORDER BY f.measure;
-- 预期结果：total_actual=10724.72, basic_actual=2493.19, project_actual=8231.53


-- ============================================================
-- Query 2: 按功能分类（201/208/210/221）汇总支出
-- ============================================================
SELECT 
    f.classification_code,
    f.classification_name,
    SUM(f.amount) as total_amount
FROM fact_fiscal_line_items f
WHERE f.document_version_id = 2
  AND f.table_code = 'FIN_03_expenditure'
  AND f.measure = 'total_actual'
  AND LENGTH(f.classification_code) = 3  -- 只取一级分类
  AND f.classification_code ~ '^[0-9]+$' -- 排除"合计"
GROUP BY f.classification_code, f.classification_name
ORDER BY f.classification_code;
-- 预期结果：201=9819.74, 208=433.03, 210=87.21, 221=384.74


-- ============================================================
-- Query 3: 基本支出按经济分类（301/302/303/310）汇总
-- ============================================================
SELECT 
    f.classification_code,
    f.classification_name,
    SUM(f.amount) as total_amount
FROM fact_fiscal_line_items f
WHERE f.document_version_id = 2
  AND f.table_code = 'FIN_06_basic_expenditure'
  AND f.measure = 'actual'
  AND LENGTH(f.classification_code) = 3  -- 一级经济分类
GROUP BY f.classification_code, f.classification_name
ORDER BY f.classification_code;
-- 预期结果：301=2194.77, 302=154.16, 303=130.23, 310=14.03


-- ============================================================
-- Query 4: 三公经费预算 vs 决算（合计及子项）
-- ============================================================
SELECT 
    f.classification_code,
    f.classification_name,
    MAX(CASE WHEN f.measure = 'budget' THEN f.amount END) as budget,
    MAX(CASE WHEN f.measure = 'actual' THEN f.amount END) as actual,
    MAX(CASE WHEN f.measure = 'budget' THEN f.amount END) - 
        MAX(CASE WHEN f.measure = 'actual' THEN f.amount END) as diff
FROM fact_fiscal_line_items f
WHERE f.document_version_id = 2
  AND f.table_code = 'FIN_07_three_public'
GROUP BY f.classification_code, f.classification_name
ORDER BY f.classification_code;
-- 预期结果：total(867.50 vs 592.15), overseas(839 vs 587.6), reception(28.5 vs 4.55)


-- ============================================================
-- Query 5: 列出该文档包含的 table_code 清单与是否有数据
-- ============================================================
SELECT 
    table_code,
    COUNT(*) as row_count,
    COUNT(DISTINCT statement_code) as statement_count,
    CASE WHEN COUNT(*) > 0 THEN '有数据' ELSE '无数据' END as status
FROM fact_fiscal_line_items
WHERE document_version_id = 2
GROUP BY table_code
ORDER BY table_code;


-- ============================================================
-- Query 6: Drilldown 查询 - 从 fact 定位到对应 cells
-- 示例：查找"一般公共服务支出"(201) 对应的原始单元格
-- ============================================================
-- Step 1: 找到 fact
SELECT 
    f.id as fact_id,
    f.table_code,
    f.classification_code,
    f.classification_name,
    f.measure,
    f.amount,
    f.row_order
FROM fact_fiscal_line_items f
WHERE f.document_version_id = 2
  AND f.classification_code = '201'
  AND f.table_code = 'FIN_03_expenditure'
  AND f.measure = 'total_actual';

-- Step 2: 根据 table_code 和 row_order 找到对应的 cells
-- 假设 row_order=1 对应 row_idx=3 (需要根据表结构映射)
SELECT 
    c.table_code,
    c.row_idx,
    c.col_idx,
    c.raw_text
FROM fiscal_table_cells c
WHERE c.document_version_id = 2
  AND c.table_code = 'FIN_03_expenditure'
  AND c.row_idx = 3  -- 对应分类 201 的行
ORDER BY c.col_idx;
-- 返回该行所有列，作为证据链


-- ============================================================
-- Bonus: 验证勾稽 - 总支出 = 基本 + 项目
-- ============================================================
SELECT 
    f.classification_name,
    MAX(CASE WHEN f.measure = 'total_actual' THEN f.amount END) as total,
    MAX(CASE WHEN f.measure = 'basic_actual' THEN f.amount END) as basic,
    MAX(CASE WHEN f.measure = 'project_actual' THEN f.amount END) as project,
    MAX(CASE WHEN f.measure = 'total_actual' THEN f.amount END) - 
        COALESCE(MAX(CASE WHEN f.measure = 'basic_actual' THEN f.amount END), 0) -
        COALESCE(MAX(CASE WHEN f.measure = 'project_actual' THEN f.amount END), 0) as diff
FROM fact_fiscal_line_items f
WHERE f.document_version_id = 2
  AND f.table_code = 'FIN_03_expenditure'
  AND f.classification_code = '合计'
GROUP BY f.classification_name;
-- 验证：diff 应该为 0
