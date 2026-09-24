# 迁移 0021：人工补核决定（WP3-B）

迁移 id：`2026-09-23_0021_review_obligation_decisions`
新增对象：`review_obligation_decisions` 表、2 个索引
改动的既有对象：**无**（0019 / 0020 一行未改；本迁移只新增，且可重复执行）

## 这次迁移做了什么

把「检查义务的人工补核结论」从"没有入口"变成一张绑在复核会话上的表。

WP3-A 之后，覆盖台账（obligation ledger）里引擎判出的阻塞义务只能整体卡住
完成门禁（`blocking_obligations`），用户没有任何人工处理入口——复核遇上
"规则尚未实现 / 取数不足"就永远完不成。0021 之后，复核人可以对每条阻塞义务
显式表态，完成门禁承认人工结论。

## 表结构要点

```sql
CREATE TABLE review_obligation_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_session_id UUID NOT NULL REFERENCES review_sessions(id) ON DELETE RESTRICT,
    slot_id UUID NOT NULL REFERENCES material_slots(id) ON DELETE RESTRICT,
    obligation_id TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN
        ('pending', 'verified_ok', 'verified_issue', 'not_applicable')),
    note TEXT,
    evidence_reference TEXT,
    reviewer TEXT NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX uq_review_obligation_decisions_scope
    ON review_obligation_decisions (review_session_id, obligation_id);
```

## 四条设计纪律

1. **绑定 `review_session_id`，不是 `(obligation_id, job_uuid)`。**
   同一份材料可以 V1 复核、V2 复核、多个分析代际；只有"哪一次复核"能回答
   这条补核结论的上下文。会话失效后决定**保留**（审计追溯），但完成门禁
   只读当前有效会话的决定，旧结论不漏进新复核。
2. **四态口径与"已处理"集合。**
   `pending`（含"没有记录"）继续阻塞；`verified_ok` / `verified_issue` /
   `not_applicable` 视为已处理。任何未知决定值按待处理处理（与
   `review_lifecycle._problem_blockers` 同一条 fail-closed 纪律）。
3. **并发不静默覆盖。**
   唯一约束 ``(review_session_id, obligation_id)`` 兜底首次表态冲突；
   ``revision`` 乐观锁拦住"拿旧读数改写别人结论"。两者都是 409，
   宁可让人刷新后重试，不让后到者悄悄赢。
4. **审计与删除。**
   本表没有 `DELETE` 路径（失效是保留不是删除）。人工结论一律经
   `obligation.reviewed` / `obligation.updated` 审计事件留痕，
   复用同一套 JSONL 审计日志，不另开第二套。

## 升级方式

随 `run_migrations()` 自动应用；从已有 0020 的库升级只新增本表与两个索引，
重复执行幂等（`CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS`）。
CI 的 "DB migrations (fresh database + idempotency)" 步骤本轮起覆盖本迁移。

## 回滚

**顺序不能颠倒**：`review_obligation_decisions` 的外键分别指向
`review_sessions`（0020）与 `material_slots`（0019）。
如果数据库已应用到 0021，整体回滚必须严格逆序：

```
撤 0021 → 撤 0020（docs/MIGRATION_0020_REVIEW_LIFECYCLE.md）
        → 撤 0019（docs/MIGRATION_0019_MATERIAL_SLOTS.md）
```

即：**必须先撤本迁移，再回滚 0020**；直接删 `review_sessions` 或
`material_slots` 都会被 `review_obligation_decisions` 的外键以
`DependentObjectsStillExist` 拒绝（ON DELETE RESTRICT 是有意为之，
见「四条设计纪律」）。

### 回滚 SQL

```sql
-- 1) 删掉人工补核决定表（历史补核结论随之全部丢失，不可恢复）
DROP TABLE IF EXISTS review_obligation_decisions;

-- 2) 抹掉迁移记录。schema 名按实际部署的 PG_SCHEMA 替换（默认 public）
DELETE FROM public.schema_migrations
WHERE id = '2026-09-23_0021_review_obligation_decisions';
```

本迁移不改任何既有表，因此回滚不涉及 `ALTER TABLE`。索引随表一并消失。
**禁止用 `DROP TABLE ... CASCADE` 偷过**：CASCADE 会把依赖对象一起静默删除，
文档（及回填测试）将无法证明回滚者真的理解依赖顺序。

### 回滚的影响面

| 对象 | 回滚后 | 是否可恢复 |
| --- | --- | --- |
| 人工补核结论（`review_obligation_decisions` 的行，含 `decision` / `reviewer` / `reviewed_at` / `note` / `evidence_reference` / `revision`） | **全部丢失** | 否。人工结论没有任何其他存储，回滚即永久失去"谁凭什么依据处理了哪条义务" |
| `review_sessions` / `material_slots` / `fiscal_document_versions` 行数据 | **完全不变** | 不适用——回滚不触碰这些行 |
| 原始材料、分析结果、问题记录 | 完全不变 | 不适用 |

一句话：**回滚只丢人工补核结论本身，不丢复核会话、槽位台账或任何原始材料。**

### 回滚前建议

```sql
COPY (
  SELECT *
  FROM review_obligation_decisions
) TO '/tmp/review_obligation_decisions_backup.csv'
CSV HEADER;
```

### 真库验证

`tests/test_material_slot_migration_pg.py::test_rollback_restores_previous_shape_without_losing_versions`
把 0021 → 0020 → 0019 三步回滚语句逐条原样执行，断言四张表（本表 +
review_sessions + material_sources + material_slots）、0020 的两列、0019 的
`slot_id` 列与三条迁移记录全部消失、原始文件版本一条不少，且重跑
`run_migrations()` 后全部恢复。该用例已纳入 CI 真 PostgreSQL 硬门
（`-m real_database`）。
