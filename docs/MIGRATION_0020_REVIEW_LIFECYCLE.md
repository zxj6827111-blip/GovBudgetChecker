# 迁移 0020：人工复核生命周期（WP3-A）

迁移 id：`2026-09-22_0020_review_lifecycle`
新增对象：`review_sessions` 表、`analysis_jobs` 两列、4 个索引
改动的既有对象：**无结构改动**（`analysis_jobs` 只加列，既有列一行未改）

## 这次迁移做了什么

只做两类**新增**，不改动任何既有表的数据：

1. 新建 `review_sessions`（人工复核会话）；
2. 给 `analysis_jobs` 增加两列：`analysis_revision`（分析代际）与
   `analysis_result_fingerprint`（当前已落库分析结果的内容指纹）。

### 为什么复核会话必须是独立业务对象

一条材料可以 **V1 分析 → 复核完成 → V2 分析 → 再次复核**。`issue_workflow` 只回答
"某一条问题被怎么处理了"，`analysis_jobs` 只回答"跑过几次分析"，两者都回答不了
"**哪一版文件、哪一代分析**上的复核结论"。把复核结论挂在问题记录上，等于让
"这份材料复核完成了吗"这个事实随问题条数变化而漂移。

会话绑四元组，缺一不可：

```
slot_id + document_version_id + analysis_job_uuid + analysis_basis_token
```

### 为什么必须有分析代际

`_upsert_analysis_job` 是 `ON CONFLICT (job_uuid) DO UPDATE`，
`_upsert_analysis_result` 是 `ON CONFLICT (job_id) DO UPDATE`——
**同一个 job_uuid 重新分析时，两行都是被原地覆盖的**。
因此 `slot_id + document_version_id + job_uuid` 不足以区分
"分析结果 A"与"同一 job_uuid 上的重新分析结果 B"：旧复核会静默继承新结论，
一份没被人看过的分析被算作"已复核完成"。

`analysis_revision` 的语义：**该 job 上已经落库过结果的分析代际数**，
`0` 表示还没有任何结果落库。默认值取 0 而不是 1，是为了让"首次落库结果"
恰好完成 `0 → 1`，不出现"第一次分析就是第 2 代"这种看不出所以然的编号。
既有历史行保持 0（代际未知），它们下一次真正产出结果时归为第 1 代。

### 代际只在两种情况下变化

| 情形 | 是否换代 | 机制 |
| --- | --- | --- |
| 重新分析产出新结果 | **是**（内容逐字相同也换） | 作业被重置为 `queued` 时清空指纹 → 结果落库时指纹为空 ⇒ 递增 |
| 同一个结果的重复落库 / 断线重放 | 否 | 指纹相同 ⇒ 不递增 |
| 进度更新、metadata 修复 | 否 | 不带结果的那次落库不参与代际判定 |
| 状态重放、持久化补记 | 否 | 同上（因此 `updated_at` **不能**当 generation） |
| **首次写库就带结果**（初始 queued 快照因数据库不可用没入库） | 初值为 **1** | INSERT 的初值按"是否带结果"取 1 / 0，不会造出"revision = 0 且指纹非空"的行 |

`analysis_basis_token = "<job_uuid>:<analysis_revision>"`。

### 一条槽位同时只能有一个进行中的复核

```sql
CREATE UNIQUE INDEX uq_review_sessions_active
    ON review_sessions (slot_id) WHERE status = 'in_progress';
```

这是业务不变式，不只是性能优化：两个浏览器同时点"进入审核"若能各建一条活动会话，
两边的确认/忽略就会写进两条互不知情的记录里，"复核完成"到底以哪一条为准无法回答。
用 partial unique index 而不是"应用层先查再写"——后者在并发下一定漏
（两个事务都查到"没有活动会话"）。

### 终态自洽约束

```sql
CONSTRAINT ck_review_sessions_completed CHECK (
    status <> 'completed'
    OR (completed_at IS NOT NULL AND completed_by IS NOT NULL AND invalidated_at IS NULL)
),
CONSTRAINT ck_review_sessions_invalidated CHECK (
    status <> 'invalidated' OR (invalidated_at IS NOT NULL AND invalidated_reason IS NOT NULL)
)
```

没有这两条，一次写错状态的代码就能造出"已完成但没有完成人"这种
无法追溯、却看起来一切正常的记录。

## 幂等性：三层保证

### 第一层——迁移框架跳过已应用项

`src/db/migrations.py:run_migrations()` 先查 `schema_migrations`，已记录的整条跳过。

### 第二层——每条语句自身可重复执行

0020 的语句全部落在四种可重放形态内：

| 形态 | 用途 |
| --- | --- |
| `CREATE EXTENSION IF NOT EXISTS pgcrypto` | 扩展（通常已存在，为"单独重放本迁移"兜底） |
| `CREATE TABLE IF NOT EXISTS` | 新建会话表 |
| `CREATE INDEX / UNIQUE INDEX IF NOT EXISTS` | 索引 |
| `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` | `analysis_jobs` 的两个新列 |

`tests/test_material_slot_migration.py::test_every_statement_is_idempotent`
逐条检查"最新一条迁移"的语句形态是否在白名单内（该用例的参数化范围始终是
`MIGRATIONS[-1]`，因此永久覆盖最新迁移）。

### 第三层——真库重放

`tests/test_material_slot_migration_pg.py` 在真库上把每条语句原样再执行一遍。

## 回滚 SQL

**先看依赖链**：如果数据库已应用 `2026-09-23_0021_review_obligation_decisions`，
**必须先执行 0021 的回滚**（`review_obligation_decisions` 的外键指向
`review_sessions`，直接走下文的 `DROP TABLE review_sessions` 会被
`DependentObjectsStillExist` 拒绝），再执行本页回滚。三条迁移的整体顺序是
`0021 → 0020 → 0019`——见 `docs/MIGRATION_0021_REVIEW_OBLIGATION_DECISIONS.md`
的「回滚」一节，本页不重复那段 SQL。

**本页语句的顺序也不能颠倒**：`review_sessions` 的两个外键指向 `material_slots` 与
`fiscal_document_versions`，必须先撤 0020 再撤 0019，否则
`DROP TABLE material_slots` 会被 `DependentObjectsStillExist` 拒绝。

```sql
-- 1) 删掉复核会话表（历史复核记录随之丢失，不可恢复）
DROP TABLE IF EXISTS review_sessions;

-- 2) 删掉分析代际两列
ALTER TABLE analysis_jobs DROP COLUMN IF EXISTS analysis_result_fingerprint;
ALTER TABLE analysis_jobs DROP COLUMN IF EXISTS analysis_revision;

-- 3) 抹掉迁移记录。schema 名按实际部署的 PG_SCHEMA 替换（默认 public）
DELETE FROM public.schema_migrations
WHERE id = '2026-09-22_0020_review_lifecycle';
```

`tests/test_material_slot_migration_pg.py::test_rollback_restores_previous_shape_without_losing_versions`
把这段 SQL（连同 0019 的回滚）逐条原样执行，并断言原始文件版本一条不少。
文档里写的回滚语句如果从没跑过，就只是"看起来能回滚"。

### 回滚的影响面

| 对象 | 回滚后 | 是否可恢复 |
| --- | --- | --- |
| 复核会话（`review_sessions` 的行） | **全部丢失** | 否。复核结论没有任何其他存储，回滚即永久失去"谁在什么时候复核过" |
| `analysis_jobs.analysis_revision` | 列被删除 | 是。重新执行迁移后从 0 开始累积 |
| `analysis_jobs` / `analysis_results` / `issues` 行数据 | **完全不变** | 不适用——回滚不触碰这些行 |
| `material_slots` / `fiscal_document_versions` 行数据 | **完全不变** | 不适用 |
| 原始 PDF、分析结果、问题记录 | 完全不变 | 不适用 |

一句话：**回滚只丢复核记录本身，不丢任何原始材料、分析结果或问题记录。**

### 回滚前建议

```sql
COPY (SELECT * FROM review_sessions) TO '/tmp/review_sessions_backup.csv' CSV HEADER;
```

## 升级到 0020 的前置条件

- 数据库里已应用 `2026-09-21_0019_material_slots` 及其之前全部 19 条迁移
  （0020 的 `review_sessions.slot_id` 外键指向 0019 建的 `material_slots`）；
- 数据库角色具备 `CREATE`（建表/建索引）与 `ALTER TABLE` 权限；
- 本次实测环境（PostgreSQL 15.17）中 `pgcrypto` 已安装于 `public`，
  因此 `CREATE EXTENSION IF NOT EXISTS pgcrypto` 是 no-op。

## 实测验证记录

| 用例 | 覆盖内容 | 结果 |
| --- | --- | --- |
| `test_review_lifecycle_pg.py::test_upgrade_path_adds_only_0020_objects` | 升级路径只新增；`analysis_jobs` 既有行保留、新列取默认值 | ✅ |
| `test_material_slot_migration_pg.py::test_upgrade_from_existing_database_applies_only_0019` | 停在 0018 的库跑迁移只新增未应用的迁移 | ✅ |
| `test_material_slot_migration_pg.py::test_rollback_restores_previous_shape_without_losing_versions` | 0020 + 0019 的回滚 SQL 原样可执行、原始版本一条不少 | ✅ |
| `test_review_lifecycle_pg.py::test_two_concurrent_starts_produce_one_session` | `uq_review_sessions_active` 真的拦得住并发 start | ✅ |
| `test_review_lifecycle_pg.py::test_result_replay_does_not_bump_generation_but_reanalysis_does` | 代际在真库上的递增/不递增 | ✅ |

跑法：

```bash
GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db \
    python -m pytest tests/test_review_lifecycle_pg.py tests/test_material_slot_migration_pg.py -v
```

不设该变量时这些用例全部 skip，CI 与默认开发环境不受影响。
