# 迁移 0019：材料槽位（material_slots）

- 迁移 ID：`2026-09-21_0019_material_slots`
- 关联文档：`docs/MATERIAL_LEDGER_WP1_DESIGN_20260921.md`（设计确认）、
  `docs/MATERIAL_LEDGER_WP0_BASELINE_20260921.md`（基线）
- 状态：**已在 PostgreSQL 15.17 实测通过**（全新库 / 既有库升级 / 语句重放 /
  约束行为 / 回滚，详见下文「实测验证记录」）

## 这次迁移做了什么

只做三类**新增**，不改动任何既有表的数据：

1. 新建 `material_slots`（材料槽位）与 `material_sources`（材料来源）两张表；
2. 给 `fiscal_document_versions` 增加可空列 `slot_id`（槽位绑定）；
3. 建立台账查询所需的索引。

### 关于 `caliber_conflict_candidate`

`material_slots` 上有一个为"口径矛盾"准备的可空列，含义是：
已确认口径与后来识别到的口径互相矛盾时，**保留已确认值**，
把矛盾的那个观测值记在这一列里，等人工裁决。

没有它的话，口径只能靠"取最新一次识别"来决定；那等于让"两笔数字能不能相加"
随分析次数漂移，而且是静默漂移。用可空文本而不是布尔，是因为布尔只能说明
有矛盾、看不出矛盾的是什么，人工裁决时还得回头翻日志。

### 本迁移在评审修复轮被原地修改过

`2026-09-21_0019_material_slots` 是**未发布的迁移**：PR #42 尚未合并，
且已核对过任何持久数据库（含本机开发库 `fiscal_db`）的 `public` schema
**都没有应用过它**（迁移记录停在 0018，`material_slots` 不存在）。

因此 `caliber_conflict_candidate` 直接加进了 0019 的建表语句，
而不是新开 0020。这样做的代价是：如果有人已经私下跑过 0019，
必须回滚后重跑（回滚步骤见下）。已核对本机不存在这种状态。

`fiscal_documents`、`org_units`、`org_department`、`org_unit`、`analysis_jobs`、
`issues`、`analysis_results` 的**结构一行未改**。

## 幂等性：三层保证

### 第一层——迁移框架跳过已应用项

`src/db/migrations.py:run_migrations()` 先查 `schema_migrations`，
已记录的迁移整条跳过。因此正常路径下第二次启动不会重放。

### 第二层——每条语句自身可重复执行

0019 的语句全部落在四种可重放形态内：

| 形态 | 用途 |
| --- | --- |
| `CREATE EXTENSION IF NOT EXISTS pgcrypto` | 扩展（通常已存在，为"单独重放本迁移"兜底） |
| `CREATE TABLE IF NOT EXISTS` | 两张新表 |
| `CREATE INDEX / UNIQUE INDEX IF NOT EXISTS` | 索引 |
| `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | `slot_id` 绑定列 |

这一层是必要的：第二层失效时（例如运维手工删掉 `schema_migrations` 里的一行重跑），
没有它就会直接报错而不是安全重建。

`tests/test_material_slot_migration.py` 用**白名单**逐条校验语句形态，
并对整条迁移断言"不允许出现 `INSERT` / `UPDATE` / `DELETE` / `DROP TABLE`"。
用白名单而不是黑名单，是为了让将来新增语句时必须显式想清楚它能不能重放。

### 第三层——真库重放

`tests/test_material_slot_migration_pg.py::test_every_statement_replays_cleanly`
把 0019 的全部语句在真库上原样再执行一遍。

## 回滚

### 回滚 SQL

**先看依赖链**：如果数据库已应用到 `2026-09-23_0021_review_obligation_decisions`，
整体回滚必须严格逆序：

```
撤 0021（docs/MIGRATION_0021_REVIEW_OBLIGATION_DECISIONS.md）
→ 撤 0020（docs/MIGRATION_0020_REVIEW_LIFECYCLE.md）
→ 最后撤本页 0019
```

`review_obligation_decisions` / `review_sessions` 的外键都指向
`material_slots`，越过任何一步都会被 `DependentObjectsStillExist` 拒绝。
0019 自己的回滚 SQL 不变。

**本页语句的顺序也不能颠倒。** `fiscal_document_versions.slot_id` 的外键指向
`material_slots`，先删表会被 PostgreSQL 以 `DependentObjectsStillExist` 拒绝
（本步骤已在真库实测过，第一次尝试就是按"先删子表"的直觉写的，被数据库挡下后
才改成本顺序）。

```sql
-- 1) 先解除引用：删掉指向 material_slots 的外键列。
--    这一步不会丢 fiscal_document_versions 的任何行，只少一列。
ALTER TABLE fiscal_document_versions DROP COLUMN IF EXISTS slot_id;

-- 2) 来源表（material_slots 的子表，先删）
DROP TABLE IF EXISTS material_sources;

-- 3) 槽位表（此时已无外部引用）
DROP TABLE IF EXISTS material_slots;

-- 4) 抹掉迁移记录。schema 名按实际部署的 PG_SCHEMA 替换（默认 public）
DELETE FROM public.schema_migrations
WHERE id = '2026-09-21_0019_material_slots';
```

### 回滚的影响面（必须知道的部分）

| 对象 | 回滚后 | 是否可恢复 |
| --- | --- | --- |
| 槽位数据（`material_slots` / `material_sources` 的行） | **全部丢失** | 否。槽位是由判定规则从任务元数据推导出来的，可以用 `scripts/backfill_material_slots.py` 重新盘点，但**人工录入的 `due_at`、`applicability_note` 无法找回** |
| `fiscal_document_versions.slot_id` | 列被删除 | 是。重新执行迁移后，可由结构化入库或回填重新绑定 |
| `fiscal_documents` / `fiscal_document_versions` 行数据 | **完全不变** | 不适用——回滚不触碰这些行 |
| 原始 PDF | 完全不变 | 不适用 |
| `analysis_jobs` / `issues` / `analysis_results` | 完全不变 | 不适用 |
| `org_*` 系列表 | 完全不变 | 不适用 |

一句话：**回滚只丢槽位台账本身，不丢任何原始材料、分析结果或问题记录。**

### 回滚前建议

1. 若已有人工录入的 `due_at` / `applicability_note`，先导出备份：

   ```sql
   COPY (SELECT * FROM material_slots) TO '/tmp/material_slots_backup.csv' CSV HEADER;
   COPY (SELECT * FROM material_sources) TO '/tmp/material_sources_backup.csv' CSV HEADER;
   ```

2. 确认没有正在跑的结构化入库任务（0019 的回滚会短暂持有
   `fiscal_document_versions` 上的排他锁）。

### 部分回滚（只停用、不删数据）

如果只是怀疑槽位表有问题，**优先用停用开关而不是回滚**：

```bash
MATERIAL_LEDGER_DISABLED=1   # 停掉槽位写入，保留已有数据供排查
```

停用只影响写入；读取与数据都保留，排查完直接去掉环境变量即可恢复。

## 升级到 0019 的前置条件

- 数据库里已应用 `2026-08-26_0018_report_scope_key` 及其之前全部 18 条迁移；
- 数据库角色具备 `CREATE`（建表/建索引）与 `CREATE EXTENSION` 或该扩展已安装的权限；
- 本次实测环境（PostgreSQL 15.17）中 `pgcrypto` 已安装于 `public`，
  因此 `CREATE EXTENSION IF NOT EXISTS pgcrypto` 是 no-op。

## 实测验证记录

环境：PostgreSQL 15.17（本机），隔离 schema `matslot_test_<随机>`，测后 `DROP SCHEMA CASCADE`。

| 用例 | 验证内容 | 结果 |
| --- | --- | --- |
| `test_migrations_apply_and_second_run_is_noop` | 全新库应用全部 19 条；第二次执行不新增任何记录 | ✅ |
| `test_upgrade_from_existing_database_applies_only_0019` | 先把 0018 之前的状态原样搭出，再跑迁移：`schema_migrations` 只 +1，`fiscal_documents.fiscal_year` 仍可空 | ✅ |
| `test_every_statement_replays_cleanly` | 0019 每条语句原样重放 | ✅ |
| `test_schema_shape_matches_design` | `fiscal_year`/`slot_id` 可空、`subject_org_id`/`slot_key` 非空 | ✅ |
| `test_identity_unique_index_blocks_duplicate_slot` | 同自然键、不同 `slot_key` 的插入被复合唯一索引拦下 | ✅ |
| `test_unknown_year_slots_are_distinguished_by_mapping_key` | 两条未知年份槽位共存；同 `mapping_key` 冲突 | ✅ |
| `test_status_check_constraint_rejects_unknown_value` | 非法的 `status` 被 CHECK 拒绝 | ✅ |
| `test_deleting_a_slot_keeps_document_versions` | 删槽位后文件版本行仍在，`slot_id` 置空 | ✅ |
| `test_service_allocate_and_bind_against_real_database` | 服务层端到端：分配 → 绑定 → 幂等重放 → 状态推进 | ✅ |
| `test_service_refuses_slot_whose_natural_key_collides` | 自然键冲突报错而非静默合并 | ✅ |
| `test_cross_slot_rebind_is_rejected_and_rolls_back_on_real_db` | 跨槽重绑被拒，且失败那次分配新建的槽位被整体回滚 | ✅ |
| `test_concurrent_binding_of_one_version_has_exactly_one_winner` | 两个连接并发绑定同一版本：只有一个胜者，只有一个槽位声称它是当前版本（行锁生效） | ✅ |
| `test_current_version_tie_break_uses_id_on_real_db` | 同 `created_at` 的两个版本按 id 决定新旧 | ✅ |
| `test_caliber_conflict_is_durable_on_real_db` | 口径矛盾持久化，后续一致观测与状态刷新都洗不掉 | ✅ |
| `test_mark_not_applicable_respects_identity_gate_on_real_db` | 身份未确认的槽位标不适用后仍停在 `mapping_required` | ✅ |
| `test_rollback_restores_previous_shape_without_losing_versions`（含在回滚验证内） | 回滚 SQL 原样执行后新增对象消失、原始版本一条不少、可重新迁移 | ✅ |

跑法：

```bash
GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db \
    python -m pytest tests/test_material_slot_migration_pg.py -v
```

不设该变量时这 16 条全部 skip，CI 与默认开发环境不受影响。

**实测后核对**：`fiscal_db` 的 `public` schema 未发生任何变化
（仍 18 条迁移、无 `material_slots`、无残留测试 schema）。
