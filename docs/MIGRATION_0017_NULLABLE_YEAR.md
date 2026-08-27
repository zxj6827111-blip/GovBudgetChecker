# 迁移 0017 / 0018：年份可空与报告身份维度（B-02 / P0-03 / P0-09）

- 迁移 ID：`2026-08-26_0017_nullable_report_year`、`2026-08-26_0018_report_scope_key`
- 关联缺口：B-02 / P0-03「结构化入库层把无法识别的年份兜底写成 2000」、
  P0-09「report_id 残留碰撞」
- 状态：**已在 PostgreSQL 15.17 实测通过**（结构 / 功能 / 回滚三项，
  详见下文「实测验证记录」）

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

## 实测验证记录（已完成）

- 验证环境：PostgreSQL **15.17**，库 `fiscal_db`，schema `public`，
  含真实数据（26 份 `fiscal_documents`、24 份 `org_dept_annual_report`、
  1362 行 `org_dept_line_items`）
- 验证日期：2026-08-26
- 迁移应用时间（`schema_migrations.applied_at`）：
  - `2026-08-26_0017_nullable_report_year` → `2026-08-26 12:42:13 UTC`
  - `2026-08-26_0018_report_scope_key` → `2026-08-26 12:47:14 UTC`

### 结构验证（只读）

| 检查项 | 期望 | 实测 |
|---|---|---|
| `fiscal_documents.fiscal_year` | nullable | `is_nullable=YES` ✅ |
| `org_dept_annual_report.year` | nullable | `is_nullable=YES` ✅ |
| `org_dept_table_data.year` | nullable | `is_nullable=YES` ✅ |
| `org_dept_line_items.year` | nullable | `is_nullable=YES` ✅ |
| `org_dept_annual_report.scope_key` | text NOT NULL default `''` | `text / NO / ''::text` ✅ |
| `uq_fiscal_documents_org_year_type` | COALESCE 表达式唯一索引 | `btree (org_unit_id, COALESCE(fiscal_year, '-1'::integer), doc_type)` ✅ |
| `uq_dept_report_scope_type_key` | 含 scope_key 的表达式唯一索引 | `btree (department_id, unit_id, COALESCE(year, '-1'::integer), report_type, scope_key)` ✅ |
| 原 UNIQUE 约束 | 已被 `DO` 块删除 | 两表均无 UNIQUE 约束，去重完全交给表达式索引 ✅ |
| `uq_dept_report_scope_type`（0017 建、0018 删） | 不存在 | 已不存在 ✅ |

`DO` 块按 `pg_get_constraintdef` 反查约束名的做法在真实库上生效，
证实了不硬编码自动生成名称的必要性。

### 功能验证（事务内执行后 ROLLBACK，未改动真实数据）

| 场景 | 期望 | 实测 |
|---|---|---|
| 年份 NULL 连续 3 次 upsert `fiscal_documents` | 命中同一行 | `ids=[113,113,113]`，去重后 1 行 ✅ |
| 年份 NULL 与年份 2025 | 不同行 | `113` vs `116` ✅ |
| 同 scope + 未知年份 + 不同 checksum | 两个不同 `report_id` | 两个不同 UUID ✅ |
| 同 scope + 未知年份 + 相同 checksum 重复入库 | 仍为一行 | 同一 UUID ✅ |
| 可靠 scope（`scope_key=''` + 具体年份）两个版本 | 新版本覆盖旧行 | 同一 UUID ✅ |

这组结果直接证明：放开 NOT NULL 后未知年份的材料**不会**因为
"多个 NULL 互不冲突"而重复建行，`COALESCE` 表达式索引方案有效。

### 回滚验证

`docs` 中的回滚脚本共 11 条语句，在事务内**全部执行成功**
（Postgres DDL 事务性，执行后 ROLLBACK 不落盘）：

- 两条原 UNIQUE 约束可重建；
- 三个新索引与 `scope_key` 列可删除；
- 四个 `SET NOT NULL` 均成功（前提：当前库中 NULL 年份为 0 行）；
- `schema_migrations` 记录可删除以便重新执行。

回滚后结构复核：四列 `is_nullable=NO`、两条 UNIQUE 约束回归。
ROLLBACK 后再次复核真实库：`fiscal_year is_nullable=YES`、
`schema_migrations` 中两条记录仍在、`scope_key` 列仍存在，确认未被改动。

### 历史 2000 数据现状

实测残留 **各 1 行**，已定位为测试数据泄漏而非真实材料：

```
fiscal_documents        id=9  org=split  fiscal_year=2000  doc_type=unknown
                        version file=split_mode.pdf
                        created=2026-06-08 08:14:26 UTC
org_dept_annual_report  dept=split unit=split type=BUDGET scope_key=''
                        file=split_mode.pdf  created=2026-06-08 08:14:27 UTC
```

`split` / `split_mode.pdf` 来自 `tests/test_queue_split_mode.py`。
`org_dept_table_data` 与 `org_dept_line_items` 中 `year=2000` 均为 0 行。
当前库中 NULL 年份为 0 行。

该行是否清理需人工决策，本项目不自动改写（见下节）。

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

## 已知风险：测试会写真实开发库

验证过程中发现一个与本迁移无关但必须记录的隐患：

**当环境变量 `DATABASE_URL` 有值时，运行 `pytest` 会对该库执行 `run_migrations()`。**
本次两条迁移就是在 2026-08-26 12:42 / 12:47 UTC 的两次全量 pytest 中被自动应用的，
而非人工执行。同时库中 `org_name='split'` / `split_mode.pdf` 这类残留，
来自 `tests/test_queue_split_mode.py`，说明历史上也发生过测试数据写入真实库。

本次复核确认：2026-08-26 当天 `org_units`、`fiscal_documents`、
`fiscal_document_versions`、`org_department`、`org_unit`、
`org_dept_annual_report` 新增行数均为 **0**，即本轮测试只改了结构、未写业务数据。

建议后续单独修复（不属于 M1 范围）：
- 测试默认清空 `DATABASE_URL`，需要 DB 的用例显式指向独立测试库；
- CI 与本地开发使用不同的库；
- 清理库中已有的测试残留记录。

## 待验证项

无。结构、功能、回滚三项均已在 PostgreSQL 15.17 实测通过，记录见上文
「实测验证记录」。唯一待人工决策的是历史 `year=2000` 残留行是否清理。
