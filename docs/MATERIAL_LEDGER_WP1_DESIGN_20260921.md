# WP1 实施设计确认：Material Slot 数据模型

- 日期：2026-09-21
- 基线：`4a1cabf2be3fa6e05e9820a9546ee192634871e4`
- 分支：`feat/material-ledger-foundation-v1`
- 对应任务书：第三节（代码审计）、第四节（数据模型原则）、第五节（状态设计）、
  第七节（迁移安全）、第八节（历史回填策略）

本文件是**编码前的审计结论与设计决策**。它回答任务书要求先确认的 A–G 七个问题，
并逐条列出与 `PLAN_V1.0` 的偏离及理由。

---

## 一、代码审计结论

### A. 当前仓库存在四套组织/单位标识体系

| # | 模型 | 标识形态 | 持久化位置 | 实际状态 |
| --- | --- | --- | --- | --- |
| **A** | `Organization`（city→district→department→unit） | `md5("{level}:{parent_id or 'root'}:{name}")[:12]`，12 位十六进制 | `data/organizations.json`（JSON 文件，413 条） | **活跃**：上传归属、RBAC、前端过滤、PS 同步全用它 |
| B | 表 `organizations`（迁移 0001） | `SERIAL` 整数 | PostgreSQL | **休眠**：全仓库无生产写入方；`analysis_jobs.organization_id` 指向它但恒为 NULL（见 §E） |
| C | 表 `org_units`（迁移 0004） | `SERIAL` 整数，自然键 `org_name TEXT UNIQUE` | PostgreSQL | 活跃，但**只是名称维表**：没有层级列，同名部门与同名本级单位会合并成一行 |
| D | 表 `org_department` / `org_unit`（迁移 0014） | `UUID`，另有 `code TEXT UNIQUE` | PostgreSQL | 活跃（PS 天宝系共享库），由 `ps_schema_sync` 写入 |

**A 与 D 之间唯一的桥**是 `ps_schema_sync.resolve_scope(org_name, organization_id)`：
它把 Model A 的 md5 id 翻译成 Model D 的 (department, unit)。桥本身是名称/层级启发式，
不是外键。

### B. 采用 Model A 作为槽位的稳定组织引用

理由按重要性排序：

1. **只有 A 能把"部门"与"同名本级单位"分开。** 层级进了哈希，
   `规划和自然资源局（部门）` 与 `规划和自然资源局（本级单位）` 是两个 id
   （真实值：`8f773936806d` / `53f98bebfc3a`）。C 按名称归并，直接违反本轮第一目标。
2. **A 是业务链路上唯一在用的组织标识。** 上传、归属向导、RBAC、组织树页面全部消费它；
   改用 B 或 D 会让槽位与现有界面说的不是同一个世界。
3. **不造第五套主数据。** PLAN §3.5 明确要求本轮不得再造组织主数据。
   把 A 落成数据库镜像表，等于用"同步副本"的方式再造一套。

**代价与兜底**：槽位表里的 `subject_org_id` 是普通文本列，不是外键，
因此失去数据库级引用完整性。兜底手段有三层：

- 写入前必须由 `resolve_subject_org()` 在组织目录里按 **id 精确** 命中，命中不了就不建正常槽位；
- 同时保存 `subject_org_name` / `department_name` / `jurisdiction_name` 名称快照，
  组织目录故障时台账仍可读；
- 同时保存 `subject_org_code`（PS 的 `DEPT_*` / `UNIT_*`）。
  **这一点是必需的**：Model A 的 id 由名称参与哈希，组织改名就会换 id，
  而 `code` 不随改名变，是日后把槽位认回正确组织的唯一凭据。这个不稳定性在
  §六 风险段落单独列出。

### C. `fiscal_documents` / `fiscal_document_versions` 的复用结论

现状（唯一写入路径：`src/services/structured_ingest_runner._ensure_document_version`）：

```
org_units(org_name UNIQUE)
   └─ fiscal_documents(org_unit_id, COALESCE(fiscal_year,-1), doc_type)
        └─ fiscal_document_versions(document_id, file_hash)
```

| 表 | 结论 | 理由 |
| --- | --- | --- |
| `fiscal_document_versions` | **直接复用，加一列 `slot_id`** | 它已经是一份具体 PDF（按 `file_hash`）的账，"文件版本"这一层正是槽位需要的 |
| `fiscal_documents` | **复用但不改结构** | 它挂在 `org_units` 这个名称维表下，**无法区分同名部门与同名单位**：两个不同槽位的文件可能落进同一个 `fiscal_documents` 行。因此它不能承担槽位身份，只能在 `slot_id` 之外继续做它原来的事 |
| `org_units` | **不动** | 同上，名称即主键的维表没有层级能力；改造它属于组织主数据统一，不是本轮范围 |

关键取舍：**槽位与文件版本的绑定挂在 `fiscal_document_versions`，不挂在 `fiscal_documents`。**
因为 `fiscal_documents` 这一层已经丢掉了层级信息，把槽位挂在它上面会让
"部门汇总"与"本级单位"两个槽位指向同一行，重新引入串线。

### D. 不能重复造的表

本轮**不新建**：组织维表（不造 Model A 的数据库镜像）、规则表、任务表、
任何形式的"材料主表 + 材料明细表"两层结构。
`material_slots` 一表承担身份与状态，不再拆出 `material_subjects` / `material_years`。

### E. 与 `analysis_jobs` 的兼容

事实（审计所得）：

- `/api/jobs` 的真相来源是**文件系统** `uploads/<job_id>/status.json`；
- PostgreSQL 的 `analysis_jobs` / `analysis_results` 是 `persist_analysis_job_snapshot`
  写入的**尽力而为镜像**，失败只标 `pending_retry`，不阻断流程；
- 迁移 0009 建的 `jobs` 表**从未被任何生产代码写入**（`JobOrchestrator` 全仓库无实例化），
  属于死表。本轮不碰它。

设计：**`analysis_jobs` 一行不改，不加列、不加外键。**
槽位与运行记录的关系是"槽位 → 文件版本 → （既有的）结构化入库记录"，
不新增 `job_id` 到槽位的外键。理由：

- 加外键等于把槽位绑死在"尽力而为"的镜像表上，镜像失败时槽位关系跟着丢；
- `status.json` 里没有数据库 id，强行关联需要先改上传路径，超出 WP1 边界；
- "处理记录"这个 UI 需求属于 WP2/WP3，届时由 `fiscal_document_versions.slot_id`
  与既有 `structured_ingest.json` 共同支撑，不需要现在留一张没人写的表。

> 这与 PLAN §3.4"`/api/jobs` 降为运行记录"不冲突：那是对**职责**的重新定位，
> 不要求本轮改表。

### F. 旧数据不丢的具体手段

| 风险 | 手段 |
| --- | --- |
| 迁移误删既有数据 | 0019 全部是 DDL：`CREATE TABLE/INDEX IF NOT EXISTS`、`ADD COLUMN IF NOT EXISTS`。测试逐条断言"不允许出现 INSERT/UPDATE/DELETE/DROP TABLE" |
| 历史版本被强行归属 | `fiscal_document_versions.slot_id` **可空**，不确定就不写 |
| 身份不可靠的材料消失 | 允许 `subject_kind/material_scope/report_kind = 'unknown'` 与 `fiscal_year IS NULL` 入库，状态 `mapping_required`；材料照样在台账里可见 |
| 多条未知材料互相覆盖 | 身份未解决时用 `mapping_key = "doc:<sha256>"` 区分，与 `org_dept_annual_report.scope_key`（迁移 0018）同一手法 |
| 删槽位连带删文件账 | `fiscal_document_versions.slot_id ... ON DELETE SET NULL`（不是 CASCADE）；`material_slots.current_document_version_id ... ON DELETE SET NULL` |
| 组织改名后槽位失联 | 并存 `subject_org_id` 与 `subject_org_code` + 名称快照 |

### G. 迁移的可回滚与可重复执行

**可重复执行**：三种机制叠加。

1. 迁移框架本身按 `schema_migrations` 跳过已应用项（`run_migrations`）；
2. 每条语句自身幂等（`IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`），
   测试以白名单逐条校验语句形态；
3. 真库实测：把 0019 的全部语句原样再执行一遍。

**可回滚**：新增对象的回滚是"删掉新增物"，不涉及数据恢复。
完整步骤与影响面见 `docs/MIGRATION_0019_MATERIAL_SLOTS.md`。
回滚**不会**影响 `fiscal_documents` / `fiscal_document_versions` 的行数据——
0019 只给后者加了一列，删列即回到原状。

---

## 二、数据模型

### `material_slots`

一条记录 = 一份**应当存在**的材料。

| 列 | 说明 |
| --- | --- |
| `slot_key` | 身份键：六元组规范化后 sha256 前 32 位。**唯一** |
| `jurisdiction_org_id` / `jurisdiction_name` | 行政区域（区县/市），用于台账按区聚合 |
| `department_org_id` / `department_name` | 主管部门（单位材料时取上级部门） |
| `subject_org_id` / `subject_org_name` / `subject_org_code` | 具体主体 |
| `subject_kind` | `department` / `unit` / `government` / `unknown` |
| `subject_level` | 与 `subject_kind` 同值。保留独立列是为兼容 PLAN §3.1 的字段表和未来"政府级主体"细分 |
| `material_scope` | `department_summary` / `unit_self` / `government` / `unknown` |
| `caliber` | `summary` / `self` / `unknown` |
| `fiscal_year` | 可空。识别不到就是 NULL |
| `report_kind` | `budget` / `final` / `unknown` |
| `applicability_status` / `applicability_note` | `applicable` / `not_applicable` / `unresolved` + 人工依据 |
| `due_at` / `expected_source_url` | 应收截止时间 / 预期来源地址 |
| `status` / `status_reason` | 状态缓存 + 机器可读成因（见 §四） |
| `mapping_key` | 身份未确认时的文档级区分键；正常槽位为空串 |
| `current_document_version_id` | 当前文件版本指针 |
| `created_at` / `updated_at` | 时间戳 |

**唯一键**（两条，互为兜底）：

1. 列约束 `slot_key TEXT NOT NULL UNIQUE`
2. 复合唯一索引

```sql
CREATE UNIQUE INDEX uq_material_slots_identity ON material_slots (
    subject_org_id, subject_kind, material_scope, report_kind,
    COALESCE(fiscal_year, -1), mapping_key
)
```

两条表达同一件事，但作用不同：`slot_key` 是日志/URL/跨系统引用用的稳定短标识；
复合索引是**可读的**保证，用来兜住"哈希算法或序列化口径变更导致静默合并"。
两者都由同一个六元组派生，若出现不一致说明代码有 bug——此时插入直接报错（fail-closed），
而不是悄悄合并两个业务材料。

`COALESCE(fiscal_year, -1)` 是必须的：Postgres 视多个 NULL 互不冲突，
不折叠的话"年份未知"的槽位可以无限重复（迁移 0017/0018 已经踩过同一个坑）。

**外键**：

| 列 | 指向 | 删除行为 |
| --- | --- | --- |
| `current_document_version_id` | `fiscal_document_versions(id)` | `ON DELETE SET NULL` |
| `material_sources.slot_id` | `material_slots(id)` | `ON DELETE CASCADE` |

**索引**：`subject_org_id`、`department_org_id`、`jurisdiction_org_id`、
`(jurisdiction_org_id, fiscal_year, report_kind)`（区级部门矩阵）、
`status`（工作台 KPI）、`updated_at DESC`、`due_at WHERE NOT NULL`（超期排查）。

### `material_sources`

一条来源记录。**存在的唯一理由是口径隔离**：财政年度来自材料本身，
发布日期来自网页。2024 年度决算在 2025 年发布是常态，
两者若放在同一列，"哪一年的材料"必然判错。

列：`source_kind`、`source_url`、`source_page_title`、`source_site`、
`published_at`、`discovered_at`、`last_checked_at`、`source_page_hash`、`status`。
唯一键 `(slot_id, COALESCE(source_url, ''))`。

### `fiscal_document_versions.slot_id`

可空、`ON DELETE SET NULL`、带索引。**这是槽位与文件版本之间唯一的连接点。**

---

## 三、与 PLAN §3.1 的偏离（逐条说明）

| # | PLAN 原设计 | 本实现 | 理由 |
| --- | --- | --- | --- |
| 1 | 唯一键含 `caliber` | **`caliber` 不进唯一键** | `caliber` 经常是 `unknown`（画像未识别）。把它放进唯一键会让同一份材料在识别前后变成两个槽位，正是本轮要消灭的串线。改为属性字段，随最近一次识别更新，冲突时转 `mapping_required` |
| 2 | 版本表加 `is_current` | **不加**；当前版本指针只放在 `material_slots.current_document_version_id` | 两处都记"谁是当前版本"必然漂移。单一真相放在槽位一侧，更新是原子的 |
| 3 | 未明确 `unknown` 取值 | **`subject_kind`/`material_scope`/`report_kind` 均允许 `unknown` 入库** | 若拒绝 unknown，识别不出的**真实**材料就无处安放，台账会漏掉它们。允许入库 + 强制 `mapping_required`，比拒收更接近"把材料台账建起来"的目标 |
| 4 | `material_scope` 与 `subject_kind` 语义重叠 | 保留两列，**当前由层级一对一推导** | 两者确实可能不同（单位发布部门汇总材料的情况，即 `ps_schema_sync` 的 `name_unit_promoted` 模式）。当前版本先按层级推导；`caliber` 记录文档实际口径，需要时再由后续批次引入"文档口径与槽位范围不符 → mapping_required"的判定 |
| 5 | 未提 `mapping_key` | **新增 `mapping_key` 列** | 没有它，同一主体所有"年份未知"的材料会挤进同一个槽位互相覆盖。手法与迁移 0018 的 `scope_key` 一致 |
| 6 | 未提 `subject_org_code` | **新增** | Model A 的 id 由名称参与哈希，改名即换 id。`code` 是改名后仍能认回组织的凭据 |

---

## 四、状态机

```
identity_resolved = False                     -> mapping_required (identity_unresolved)
applicability_status = unresolved             -> mapping_required (applicability_unresolved)
applicability_status = not_applicable         -> not_applicable
无当前文件 & due_at 为空                       -> not_due (due_at_unknown)      ← 不是 missing
无当前文件 & now > due_at                      -> missing (due_exceeded)
无当前文件 & now <= due_at                     -> not_due (due_not_reached)
有文件 & 分析 failed                           -> failed
有文件 & 分析 processing                       -> processing
有文件 & 分析未开始                             -> uploaded
有文件 & 分析完成 & 复核完成                    -> completed
有文件 & 分析完成 & 复核中                      -> reviewing
有文件 & 分析完成 & 有待复核项                  -> review_required
```

两条最容易被写错、因而单独立了用例的口径：

1. **"没有 PDF" ≠ "缺失"。** `missing` 的完整条件是
   `已到期 AND 适用 AND 没有当前文件`。截止时间未知时**无法证明逾期**，
   停在 `not_due` 并在 `status_reason` 写明 `due_at_unknown`。
   `counts_as_missing()` 是"缺失数"这个对外数字的**唯一出口**，
   避免各页面各自判断 `status == "missing"` 时口径走偏。
2. **状态只有 10 个值，不足以区分"真没到期"和"到期时间未知"。**
   因此 `status_reason` 是必填语义的伴生列，不是调试字段，业务界面要能显示。

另外：`completed` 只能由持久化复核结论产生。分析完成且无待办时停在
`review_required`，因为"没有待办"与"人已确认"是两件事——
这条约束是为 WP3 的复核生命周期预留正确语义，避免现在就把"跑完"当成"复核完"。

---

## 五、历史回填策略

### 判定的唯一实现

`src/services/material_slot_resolver.decide_slot_allocation()` 是**纯函数**，
线上结构化入库与离线 dry-run 调用的是同一个它。
两条路径共用一个判定，是为了让"dry-run 说会怎样"就是"真跑会怎样"——
如果两处各写一份，回填报告的数字将不具备预测能力。

### 只允许可靠事实自动回填

| 分类 | 条件 | 结果 |
| --- | --- | --- |
| 可靠 | 组织 id 在组织目录中精确命中 + 年份明确 + 文种明确且各来源无冲突 | 建正常槽位（`resolved`） |
| 不可靠 | 年份未识别 / 文种未识别 / 文种冲突 / 年份冲突 / 组织不在目录中 / 任务未记录组织 | 建占位槽位，`mapping_required` |
| 无法安置 | 连文档校验和都没有 | `unallocatable`，**不建槽** |

**明确禁止**：按名称猜测主体、用默认年份填未知、按优先级挑选冲突的文种。
只给名称不给 id 时，即使目录里只有一个同名记录，也**不自动映射**
（用例 `test_missing_org_id_never_falls_back_to_name_matching` 守住这条）。

### 本机实跑结果（`uploads/`，348 个任务目录）

| 分桶 | 条数 |
| --- | --- |
| `auto_mappable` | 141 |
| `missing_subject` | 130 |
| `missing_kind` | 76 |
| `no_document` | 1 |

预计生成 **50** 个槽位、**56** 个版本绑定；`writes_performed = 0`。

进一步拆分后的**关键事实**：

- 有 `organization_id` 的任务 217 条 → 141 条 `ok`、76 条 `kind_unknown`；
- 无 `organization_id` 的 131 条中，**126 条是本机测试残留**
  （`sample_budget_2025.pdf` 63 份 + `split_mode.pdf` 63 份），
  另有 5 条 smoke/乱码文件名；
- **剔除测试文件名后，53 条真实材料全部 `ok`，归并成 41 个槽位。**

也就是说：`missing_subject` 的 130 条几乎全部来自本机 `uploads/` 的测试污染，
**不是判定过严**。这一点必须写清楚，否则"130 条缺主体"会被误读成
"系统无法识别真实材料"。

完整报告：`docs/MATERIAL_LEDGER_WP1_BACKFILL_DRYRUN_20260921.md`。

---

## 六、已知风险与边界（编码前即已知，交付时逐项复核）

1. **组织 id 不稳定**：Model A 的 id 参与名称哈希，组织改名会换 id。
   当前靠 `subject_org_code` + 名称快照兜底，但**没有自动重认机制**；
   组织改名后旧槽位会指向一个不存在的 id。这是本轮遗留项，不是本轮引入。
2. **`fiscal_documents` 仍无法区分同名部门/单位**：它的上游 `org_units` 是名称维表。
   解决它等于重做组织主数据统一，超出 WP1。
3. **占位槽位在人工确认后需要重新归位**：一条 `mapping_required` 槽位
   在人工确认年份/文种后，正确做法是把它改判到目标槽位并合并，
   本轮只提供了"建占位槽位"与"标记不适用"两个动作，
   **没有提供改判/合并**。该动作属于 WP2（槽位管理界面）。
4. **状态缓存的刷新时机**：`status` 在槽位写入与版本绑定时重算；
   分析状态、复核状态的变化目前不会自动触发重算（它们由 WP2/WP3 接入）。
   在此之前，已有文件的槽位会停在 `uploaded`。
5. **`material_scope` 尚未反映文档实际口径**：见 §三 第 4 条。
6. **本机 `uploads/` 与 `data/organizations.json` 存在测试残留**（WP0 §8）。
   它们会让手工跑回填时看到偏高的 `missing_subject` / `kind_unknown`。

---

## 七、本轮不做的事（明确边界）

- 不新增任何 HTTP 接口（`GET /api/materials/*` 等留给 WP2 一起设计，
  避免先定一个没有 UI 验证过的接口形状）；
- 不实现回填写入（dry-run 之外不提供 `--apply`）；
- 不改规则引擎、不碰 8 项 pending_checkers；
- 不改 PDF 解析、不改 AI 抽取、不改 Golden 真值；
- 不合并/重命名组织主数据。
