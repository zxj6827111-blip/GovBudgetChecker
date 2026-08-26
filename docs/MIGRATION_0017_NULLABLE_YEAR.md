# 迁移 0017：年份可空（B-02 / P0-03）

- 迁移 ID：`2026-08-26_0017_nullable_report_year`
- 关联缺口：B-02 / P0-03「结构化入库层把无法识别的年份兜底写成 2000」
- 状态：**代码已完成，未在真实 PostgreSQL 环境实测**（本机无可连 PG 实例）

## 为什么要改

`src/services/structured_ingest_runner._parse_year` 原来在识别失败时 `return 2000`，
该值经 `run_structured_ingest` 流向 `_ensure_document_version` 与
`PSSharedSchemaSync._upsert_report`，最终写入 PostgreSQL 的 `fiscal_year` / `year`。
后果有两层：

1. 库里出现大量假的 2000 年度数据，无法与真实年度区分；
2. 年份是 report_id 唯一键的一部分，兜底成同一个 2000 会把不同材料
   挤进同一行（P0-09 碰撞风险的放大器）。

修复后识别失败一律写 `NULL`，语义变成"年份未知"而不是"年份是 2000"。

## 关键取舍：为什么不能只放开 NOT NULL

Postgres 的普通 `UNIQUE` 约束把多个 `NULL` 视为**互不冲突**。
如果只做 `ALTER COLUMN year DROP NOT NULL`，那么
`UNIQUE(department_id, unit_id, year, report_type)` 对年份未知的记录完全失效：
同一单位每上传一份年份不明的材料，都会新插一行，`ON CONFLICT` 永远不命中。

候选方案对比：

| 方案 | 结论 | 理由 |
|---|---|---|
| 只 DROP NOT NULL | ❌ 不可行 | 多个 NULL 互不冲突，未知年份记录无限重复 |
| 部分索引（`WHERE year IS NOT NULL` + `WHERE year IS NULL`） | ⚠️ 可行但复杂 | 需要两个索引，且 `ON CONFLICT` 无法同时指向两个索引推断目标 |
| **`COALESCE(year, -1)` 表达式唯一索引** | ✅ 采用 | 单个索引即可覆盖，`ON CONFLICT (a, COALESCE(year,-1), b)` 可直接推断 |

选 `-1` 作为哨兵值是因为业务年度被限定在 2000–2099（见
`src/utils/report_year.py` 的 `MIN_REPORT_YEAR`/`MAX_REPORT_YEAR`），
`-1` 不可能与真实年度相撞。

## 迁移做了什么

1. `ALTER COLUMN ... DROP NOT NULL`：`fiscal_documents.fiscal_year`、
   `org_dept_annual_report.year`、`org_dept_table_data.year`、
   `org_dept_line_items.year`。
2. 建立表达式唯一索引：
   - `uq_fiscal_documents_org_year_type (org_unit_id, COALESCE(fiscal_year,-1), doc_type)`
   - `uq_dept_report_scope_type (department_id, unit_id, COALESCE(year,-1), report_type)`
3. 删除被替代的原 `UNIQUE` 约束。约束名是 Postgres 自动生成且会因超过 63 字节被截断，
   所以用 `DO` 块按 `pg_get_constraintdef` 反查真实名字，不硬编码猜测名称。

顺序上先建新索引再删旧约束，避免并发写入期间短暂失去唯一性保护。

## 未做的事：历史 2000 数据回填

**本迁移不会自动改写既有数据。** 库里已有的 `year = 2000` 记录保持原样。

原因：无法从数据本身区分"兜底写入的假 2000"和"真实的 2000 年度材料"，
自动 `UPDATE` 属于不可逆的数据改写。如确认库中不存在真实 2000 年度材料，
可在**备份之后**手工执行：

```sql
-- 执行前务必先备份，这一步不可逆
BEGIN;
UPDATE fiscal_documents      SET fiscal_year = NULL WHERE fiscal_year = 2000;
UPDATE org_dept_annual_report SET year       = NULL WHERE year        = 2000;
UPDATE org_dept_table_data    SET year       = NULL WHERE year        = 2000;
UPDATE org_dept_line_items    SET year       = NULL WHERE year        = 2000;
-- 确认影响行数符合预期后再 COMMIT
COMMIT;
```

先用只读语句评估影响面：

```sql
SELECT count(*) FROM fiscal_documents       WHERE fiscal_year = 2000;
SELECT count(*) FROM org_dept_annual_report WHERE year        = 2000;
```

## 回滚方式

回滚前提：`year` 列中**不存在 NULL 值**，否则 `SET NOT NULL` 会失败。
先把未知年份记录处理掉（删除或人工补齐年份），再执行：

```sql
BEGIN;

-- 1) 恢复原 UNIQUE 约束
ALTER TABLE fiscal_documents
  ADD CONSTRAINT fiscal_documents_org_unit_id_fiscal_year_doc_type_key
  UNIQUE (org_unit_id, fiscal_year, doc_type);
ALTER TABLE org_dept_annual_report
  ADD CONSTRAINT org_dept_annual_report_dept_unit_year_type_key
  UNIQUE (department_id, unit_id, year, report_type);

-- 2) 删除表达式唯一索引
DROP INDEX IF EXISTS uq_fiscal_documents_org_year_type;
DROP INDEX IF EXISTS uq_dept_report_scope_type;

-- 3) 恢复 NOT NULL（此步要求列内无 NULL）
ALTER TABLE fiscal_documents       ALTER COLUMN fiscal_year SET NOT NULL;
ALTER TABLE org_dept_annual_report ALTER COLUMN year        SET NOT NULL;
ALTER TABLE org_dept_table_data    ALTER COLUMN year        SET NOT NULL;
ALTER TABLE org_dept_line_items    ALTER COLUMN year        SET NOT NULL;

-- 4) 让迁移可重新执行
DELETE FROM schema_migrations WHERE id = '2026-08-26_0017_nullable_report_year';

COMMIT;
```

同时需要把 `structured_ingest_runner._parse_year` 与
`ps_schema_sync._upsert_report` 的 `ON CONFLICT` 一并回退，
否则代码会继续写 NULL 并撞上恢复后的 NOT NULL 约束。

## 待验证项（未实测）

本机没有可连的 PostgreSQL 实例，以下均**未实测**，需在配置了 `DATABASE_URL`
的环境补做：

- [ ] 迁移正向执行成功（含 `DO` 块能正确反查并删除自动命名的约束）
- [ ] `ON CONFLICT (org_unit_id, COALESCE(fiscal_year, -1), doc_type)` 能正确命中表达式索引
- [ ] 同一单位连续上传两份年份未知材料时不产生重复 `fiscal_documents` 行
- [ ] 上述回滚脚本可执行
