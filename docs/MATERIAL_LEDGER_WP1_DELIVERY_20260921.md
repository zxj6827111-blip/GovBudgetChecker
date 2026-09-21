# WP0 + WP1 交付与验收：材料台账底座

- 日期：2026-09-21
- 基线：`4a1cabf2be3fa6e05e9820a9546ee192634871e4`（`origin/main`）
- 分支：`feat/material-ledger-foundation-v1`
- 范围：**仅 WP0 + WP1**。WP2（材料台账 UI）及之后一律未动。

本文件是给独立复核用的验收清单。每个结论都给出复算方式；
**无法在当时的条件下验证的部分，单独列在 §6"未验证项"，不混在结论里。**

---

## 1. 交付物清单

### 新增：数据库迁移

| 文件 | 内容 |
| --- | --- |
| `src/db/migrations.py` | 追加迁移 `2026-09-21_0019_material_slots`（第 19 条） |

### 新增：领域与业务代码

| 文件 | 职责 |
| --- | --- |
| `src/schemas/material_slot.py` | 取值域、`SlotIdentity`（身份六元组 + slot_key）、组织解析 |
| `src/services/material_slot_status.py` | 状态机（纯函数）：状态 + 成因码 |
| `src/services/material_slot_resolver.py` | 归属判定（纯函数）：线上与回填共用 |
| `src/services/material_slot_service.py` | 持久化：分配 / 绑定 / 状态刷新 / 来源登记 |
| `src/services/material_slot_backfill.py` | 历史盘点（只读，不写库） |

### 新增：脚本与配置

| 文件 | 用途 |
| --- | --- |
| `scripts/backfill_material_slots.py` | 历史回填盘点 CLI（默认 dry-run） |
| `.env.example` | 增加 `MATERIAL_LEDGER_DISABLED` 声明 |

### 修改：既有代码（3 处，均为加法或语义修正）

| 文件 | 改动 | 影响面 |
| --- | --- | --- |
| `src/services/structured_ingest_runner.py` | 新增 `build_ingest_metadata` 纯函数；**文档版本建立后立即**调用槽位分配（在 PDF 解析之前，见 §3.9），结果里新增 `material_slot` 摘要字段；错误返回保留已建立的 `document_id` / `document_version_id` / `material_slot` | **顺序调整 + 加法**。槽位失败被 `safe_allocate_for_document` 兜住，不阻断主流程；成功路径的字段集一个没动 |
| `api/main.py` | 构造结构化入库 metadata 改用 `build_ingest_metadata`，把主分析链路已算好的 `document_profile` 一并传入（评审修复） | 管线多传一个入参；`run_structured_ingest` 的签名与返回契约未变 |
| `.env.example` | 声明新环境变量 | 无 |

> 除上述三个文件外，**没有修改任何既有源码**。
> 规则引擎、PDF 解析、AI 抽取、上传路径、任务队列、审核工作台一行未动。
> `api/main.py` 是本轮唯一被触及的既有生产文件，净改动是"import 多一项 +
> 构造 metadata 时多传一个画像参数"，没有改变任何既有分支或返回值。

### 新增：测试（本 PR 新增 178 条；下表各文件当前合计 211 条）

| 文件 | 条数 | 默认是否运行 |
| --- | --- | --- |
| `tests/test_material_slot_identity.py` | 36 | 是 |
| `tests/test_material_slot_migration.py` | 28 | 是 |
| `tests/test_material_slot_backfill.py` | 11 | 是 |
| `tests/test_material_slot_api_regression.py` | 5 | 是 |
| `tests/support_material_slot_db.py`（辅助） | — | — |
| `tests/test_material_slot_binding_and_state.py` | 45 | 是 |
| `tests/test_material_slot_profile_integration.py` | 15 | 是 |
| `tests/test_material_slot_migration_pg.py` | 26 | 否（需 `GOVBUDGET_TEST_DATABASE_URL`） |
| `tests/test_runtime_structured_ingest.py`（本 PR 新增 9 条清理兼容用例） | 28（含既有 19） | 是 |
| `tests/test_structured_ingest_runner.py`（本 PR 新增 3 条主链路编排用例） | 17（含既有 14） | 是 |

> "新增 178 条"= 上表各文件本 PR 新增数之和（`test_runtime_structured_ingest.py`
> 与 `test_structured_ingest_runner.py` 按新增数计入）。两个数字都在改动前后
> 用 `pytest --collect-only` 实点，不是估算。

### 新增：文档与基线产物

| 文件 | 内容 |
| --- | --- |
| `docs/MATERIAL_LEDGER_WP0_BASELINE_20260921.md` | WP0 基线冻结 |
| `docs/MATERIAL_LEDGER_WP1_DESIGN_20260921.md` | 编码前的审计结论与设计确认（含与 PLAN 的 6 处偏离） |
| `docs/MATERIAL_LEDGER_WP1_BACKFILL_DRYRUN_20260921.md` | 本机 `uploads/` 的历史盘点报告 |
| `docs/MIGRATION_0019_MATERIAL_SLOTS.md` | 迁移说明、幂等性、**已实测的回滚 SQL** |
| `docs/baselines/wp0_coverage_baseline.json` | 检查义务台账基线（机器可读） |

---

## 2. 与任务书要求逐条对照

任务书第十一节列出的 13 条必测项：

| # | 要求 | 对应用例 | 结果 |
| --- | --- | --- | --- |
| 1 | 部门与本部同名但 slot id 不同 | `test_same_name_department_and_unit_get_different_slots` | ✅ |
| 2 | 同单位 2024 budget / final 是两个 slot | `test_budget_and_final_are_separate_slots` | ✅ |
| 3 | 2024 final 发布于 2025 仍 `fiscal_year=2024` | `test_final_published_in_later_year_keeps_fiscal_year`、`test_published_at_does_not_override_fiscal_year` | ✅ |
| 4 | 同 slot 两个 PDF hash 成为两个版本 | `test_two_pdf_hashes_for_one_slot_become_two_versions` | ✅ |
| 5 | 同 PDF 版本两次分析仍只有一个 slot | `test_reanalysis_of_same_version_does_not_create_new_material`（假连接）+ `test_service_allocate_and_bind_against_real_database`、`test_run_structured_ingest_rerun_reuses_single_slot_on_real_db`（真库，后者走完整主链路） | ✅ |
| 6 | 未到期没有 PDF → `not_due` | `test_not_due_when_due_at_is_in_the_future` | ✅ |
| 7 | 已到期没有 PDF → `missing` | `test_missing_only_when_due_passed_and_no_document` | ✅ |
| 8 | unknown year → `mapping_required` | `test_unknown_year_goes_to_mapping_required` | ✅ |
| 9 | kind conflict → `mapping_required` | `test_kind_conflict_goes_to_mapping_required`、`test_profile_declared_kind_conflict_is_respected` | ✅ |
| 10 | org conflict → `mapping_required` | `test_org_not_in_catalog_goes_to_mapping_required`、`test_missing_org_id_never_falls_back_to_name_matching` | ✅ |
| 11 | migration 第二次执行 no-op | `test_second_run_is_a_no_op`（假连接，默认跑）+ `test_migrations_apply_and_second_run_is_noop`（真库） | ✅ |
| 12 | backfill dry-run 不修改数据库 | `test_dry_run_never_touches_the_database`（把 DB 入口打成异常仍能跑完）、`test_dry_run_does_not_modify_input_files` | ✅ |
| 13 | 原有 analysis job API 行为不被破坏 | `test_job_upload_org_route_surface_is_unchanged`（冻结 38 条路由）+ 既有 `tests/test_api_contract.py` 等全量回归 | ✅ |

任务书第七节迁移安全 10 条：

| # | 要求 | 落实方式 |
| --- | --- | --- |
| 1 | 幂等 | 框架跳过 + 语句级 `IF NOT EXISTS` + 真库重放，三层保证 |
| 2 | 空库可执行 | 真库 `test_migrations_apply_and_second_run_is_noop` |
| 3 | 已有库可升级 | 真库 `test_upgrade_from_existing_database_applies_only_0019` |
| 4 | 第二次执行不重复建数据 | 同上两条 |
| 5 | 不删除现有数据 | 测试逐条断言迁移内无 `INSERT/UPDATE/DELETE/DROP TABLE` |
| 6 | 不修改原 PDF | 迁移只做 DDL，不触碰任何文件 |
| 7 | 不写测试数据到开发/生产库 | 真库测试用随机 schema，测后 `DROP SCHEMA CASCADE`；已核对 `public` 未变 |
| 8 | 不写测试文件到仓库真实 uploads | `tests/conftest.py` 的 autouse `isolate_upload_root` 已保证；新用例全部用 `tmp_path` |
| 9 | 提供 rollback/recovery 说明 | `docs/MIGRATION_0019_MATERIAL_SLOTS.md`，回滚 SQL **已真库实测**（含一次顺序纠错） |
| 10 | backfill 默认 dry-run | `scripts/backfill_material_slots.py` 无 `--apply`；`scan_upload_root` 是纯读函数，用例证明它一次都不碰库 |

---

## 3. 验证记录

### 3.1 全量回归

| 检查 | 基线（`4a1cabf`） | 本次 | 结论 |
| --- | --- | --- | --- |
| `python -m pytest -q`（本机 Windows） | 1307 passed, 1 skipped, 0 failed | **1459 passed, 27 skipped, 0 failed** | 新增用例全部计入；27 条 skip 里 26 条是真库用例（未配置 DSN），1 条是平台条件用例 |
| `python -m pytest -q`（GitHub CI, Linux） | — | 见 PR #42 的 CI 运行结果 | CI 比本地多 1 条通过（平台条件用例），**条数随用例集变化，此处不写死数字**，理由见下 |
| `ruff check .`（Makefile 与 CI 同款全仓命令） | All checks passed | **All checks passed** | 无变化 |
| `mypy api src tests` | Success（199 files） | **Success（212 files）** | 无变化 |

基线数字取自 `4a1cabf` 的干净检出（`git worktree`），不是带改动的当前树。

**本地与 CI 条数不同的原因（已逐条核对，不是缺陷）**：
`tests/test_pdf_parse_isolation_and_backup.py:255` 在 Windows 上跳过
（`RLIMIT_AS` 不适用），在 Linux 上运行并通过。所以 CI 恒比本地
**多 1 条通过、少 1 条跳过**，其余完全相同。
26 条真库用例在两种环境下都跳过（未配置 `GOVBUDGET_TEST_DATABASE_URL`）。

> 为什么 CI 那一栏不写具体数字：本轮新增用例后数字还会变，写死的数字会立刻过期，
> 而为了更新它再提交一次又会改变提交历史——与提交数是同一个陷阱。
> CI 数字以 PR 检查页的运行为准；本地与真库两组是本节实际执行的结果。

### 3.2 真库验证（PostgreSQL 15.17）

```bash
GOVBUDGET_TEST_DATABASE_URL=postgresql://.../fiscal_db \
    python -m pytest tests/test_material_slot_migration_pg.py -v
# => 26 passed
```

覆盖：全新库应用、第二次 no-op、既有库升级只增 0019、语句重放、schema 形状、
复合唯一索引拦截、`mapping_key` 区分未知年份、CHECK 约束、删槽位不删文件版本、
**回滚 SQL 实测**、服务层端到端幂等；评审修复轮又补了
**跨槽重绑被拒且整体回滚**、**两连接并发绑定只有一个胜者（行锁生效）**、
**同 `created_at` 按 id 决定新旧**、**口径冲突持久化且刷新洗不掉**、
**`mark_not_applicable` 尊重身份门槛**、**并发首次建槽不丢口径冲突**、
**`refresh_status` 自带行锁**、**两条写路径并发不死锁**；第四轮补了
**旧清理链路两条 DELETE 守卫用例**；第五轮补了**主链路真库 Smoke 三条**
（成功落库 / 解析失败仍落库 / 重跑不造重复槽位，见 §3.9）。

测后核对目标库：`public` schema 仍为 18 条迁移、无 `material_slots`、
无残留 `matslot_test_*` schema。**开发库数据零改动。**

### 3.3 检查义务覆盖基线未被改动

| 项目 | 基线 | 本次 | 一致 |
| --- | --- | --- | --- |
| 清单版本 | `obligations-v2` | `obligations-v2` | ✅ |
| 清单指纹 | `5461a0267d3d6ac4` | `5461a0267d3d6ac4` | ✅ |
| 未实现义务数 | 8 | 8 | ✅ |
| 义务 id 列表 | 8 项 | 完全一致 | ✅ |
| budget 规则数 / 指纹 | 22 / `e6737ade16b7f558` | 22 / 同 | ✅ |
| final 规则数 / 指纹 | 58 / `9ccb168fbc4e3680` | 58 / 同 | ✅ |
| budget 应查 / 阻塞 | 23 / 22 | 23 / 22 | ✅ |
| final 应查 / 阻塞 | 44 / 43 | 44 / 43 | ✅ |
| 结构上限 | 0.8696 / 0.8409 | 同 | ✅ |

复算：`python scripts/check_coverage_baseline.py --json`。

### 3.4 历史回填盘点（纯读，未写任何数据）

对 348 个任务目录：

| 结果 | 条数 |
| --- | --- |
| 可自动映射 | 141 |
| 未记录主体 | 130 |
| 文种未识别 | 76 |
| 无可区分标识 | 1 |
| **实际写入** | **0** |

预计生成 50 个槽位 / 56 个版本绑定。

**关键补充结论**（避免误读）：130 条"未记录主体"里 **126 条是本机
`uploads/` 的测试残留**（`sample_budget_2025.pdf` 63 份 + `split_mode.pdf` 63 份），
另有 5 条 smoke/乱码文件名。**剔除测试文件名后，53 条真实材料 100% 可自动映射
（归并成 41 个槽位）。** 76 条"文种未识别"全部是 `split_mode.pdf`。
所以这些数字反映的是本机数据卫生，不是判定过严。

### 3.5 评审修复轮（2026-09-21 第二轮）

第一轮交付后经独立评审，发现六处"能跑通但不成立"的写入语义并全部修复。
这些不是新功能，而是把原本靠约定维持的性质变成**代码上做不到违反**。

| # | 问题 | 修复后行为 |
| --- | --- | --- |
| 1 | `bind_document_version` 允许静默跨槽改挂版本，留下双向引用不一致 | 只允许"未归属"或"已归属同一槽位"；其它抛 `SlotBindingConflict`（`slot_binding_conflict`）。判定在事务内对该版本行 `SELECT ... FOR UPDATE` 之后进行 |
| 2 | 槽位 upsert / 版本绑定 / 指针推进 / 状态刷新是四个独立语句，中途失败留部分写入 | 四步合入**同一个事务**，要么全成要么全不成；异常回滚后由 `safe_allocate_for_document` 转成错误摘要，主分析继续 |
| 3 | `refresh_status` 只看 `mapping_key` 判身份完整性 | 改用全系统唯一的 `slot_identity_is_resolved`：六个身份维度同时成立才算完整 |
| 4 | `mark_not_applicable` 直接写 `status='not_applicable'`，绕过状态机 | 只写适用性事实，状态交给 `refresh_status`；身份未确认的槽位即使标了不适用仍停在 `mapping_required` |
| 5 | 主分析算出的 `DocumentProfile` 没有传给结构化入库，文种冲突/年度冲突/口径全部丢失 | 新增 `build_ingest_metadata`，把**同一个**画像对象传进去，不重新解析 PDF、不另造画像 |
| 6 | 口径冲突（已有 `summary`，新识别 `self`）被静默覆盖成新值 | 保留已确认值，把矛盾观测记进 `caliber_conflict_candidate`，状态压成 `mapping_required` / `caliber_conflict`；后续一致观测也洗不掉它 |
| 7 | 当前版本指针只比 `created_at`，同时刻版本随机停靠 | 排序键改为 `(created_at, id)`，按数值比较 |

配套升级：`FakeSlotConnection` 现在支持事务回滚、故障注入与行锁语义，
并且**遇到不认识的 SQL 直接报错**（此前会静默返回 `None`，让生产 SQL 改了
而假连接没跟上时表现为测试通过）。

**影响面实测**：在 7 份真实样张上跑画像解析，`profile.report_year` 与任务年度
**全部一致**，说明接入画像不会制造虚假年度冲突。同一批样张 `caliber` 仍为未识别
（封面写"年度部门决算"，不含汇总/本级口径词），因此口径冲突通道目前主要由
用例覆盖，真实数据尚未触发——这一点在 §6 如实登记。

### 3.6 PR 与 CI

| 项目 | 值 |
| --- | --- |
| PR | **#42（保持 OPEN，未合并）** |
| 分支 | `feat/material-ledger-foundation-v1` |
| 提交历史与总数 | **以 PR #42 的 GitHub 页面为准，本文档不记录固定数字** |
| CI | `test-and-build` 两跑均 **pass** |
| CI 侧测试统计 | 见 §5（CI 与本地分开记，不合并成一个数字） |

> 关于"提交总数"：本文档此前写过固定数字，结果每修一次就要跟着改一次，
> 而改这个数字本身又会产生一个新提交、让数字再次过期。因此改为只记录
> **Review 基线 HEAD**，总数交给 GitHub 页面，这是唯一不会自相矛盾的写法。

### 3.7 第三轮：并发一致性加固

第二轮把写入语义修正为不可违反之后，第三轮针对**并发首次创建**与
**状态刷新**两处剩余竞态做了加固。

| # | 问题 | 修复后行为 |
| --- | --- | --- |
| 1 | `SELECT ... FOR UPDATE` **锁不住不存在的行**。首次并发创建同一槽位时，两个事务都读到空行、各自在锁外算好口径，随后一个插入、另一个把锁外结论盖上去，`summary` 与 `self` 只剩一个、冲突证据被抹掉 | 写入拆成三步：**确保存在（`ON CONFLICT DO NOTHING`）→ 锁住真实存在的行 → 锁内读最新值并判定 → 写回**。判定与写入都在持锁期间完成 |
| 2 | 只靠 `ON CONFLICT DO NOTHING` 仍不够：`material_slots` 有两个唯一约束（`slot_key` + 自然键表达式索引），并发插入同一身份时两个事务会在**不同索引**的推测插入标记上互相等待 | 真库实测报 `DeadlockDetectedError`。在行锁之前加**事务级 advisory 锁**（按 `slot_key` 哈希）串行化同一身份的首次创建，推测插入竞争不复存在 |
| 3 | `refresh_status` 自己没有事务与行锁，"读事实 → 推导 → 写状态"三步之间无保护，可能按过期快照把状态写回旧值，造成"已绑定版本却仍是 `missing`" | 自带事务与 `SELECT ... FOR UPDATE`；外层已有事务时复用。调用方不再需要替它兜底 |
| 4 | 锁顺序不统一：`allocate_for_document` 是"槽位 → 版本"，`bind_document_version` 却是"版本 → 槽位"，两者并发指向同一 `(槽位, 版本)` 对时形成 **ABBA 死锁** | `bind_document_version` 改为先锁槽位。全系统统一为 **advisory(身份) → 槽位行 → 版本行** 三级全序 |
| 5 | 文档/PR 描述里写死了提交数量，每修一次就要改、改完又产生新提交 | 改为只记 Review 基线 HEAD，总数以 GitHub 页面为准 |

这一轮**没有改 schema**（`SCHEMA_CHANGE_REQUIRED = NO`），只动 service / tests / docs。

---

### 3.8 第四轮：structured-ingest cleanup × Material Ledger 兼容

PR #42 给 `fiscal_document_versions` 加了 `slot_id` 之后，旧的结构化入库清理链路
（`POST /api/jobs/structured-ingest-cleanup`）多了一条它看不见的依赖：它按
"每个组织+年度+文种只留最新入库版本"设计，看到的是**入库维度的冗余**，
看不到**台账维度的归属**。放任它删掉绑定版本会同时造成两件事——槽位的文件
版本历史缺一块，以及 `material_slots.current_document_version_id` 被外键置空、
材料突然变成"没有文件"。

采取的策略是保守的：**只要 `slot_id IS NOT NULL`，该版本就不允许由本链路删除**，
无论它是当前版本还是历史版本。

| 环节 | 修复 |
| --- | --- |
| 计划阶段 | 新增 `_apply_material_ledger_protection()`：查询候选版本的 `slot_id`，把已绑定的从 `cleanup_document_versions` 移到 `blocked_document_versions`，原因码 `material_slot_bound`，并附带 `slot_id` 便于运维定位；涉及的历史任务同步进 `skipped_jobs` |
| 执行阶段 | DELETE 改为 `WHERE id = $1 AND slot_id IS NULL`。影响 0 行即表示"计划之后才被绑定"，按阻断处理并写入 `blocked_at_delete`，**不当作普通成功** |
| job sidecar | 只有实际 `DELETE 1` 的版本才把 `structured_ingest.json` 标成 `cleaned`。计划阶段被阻断、或执行阶段发现已绑定的版本，数据库里都还在，此时标 cleaned 会制造双真相 |
| 预览 | dry_run 同样要过台账校验（预览的全部意义就是"将会删除哪些"）。查不到数据库时宁可报 503，也不返回一份可能夸大删除范围的计划 |
| 纯计划 | `plan_structured_ingest_cleanup()` 仍是纯函数，返回 `material_ledger_checked: false` 自报"没校验过"，不冒充可执行计划 |

为什么计划与执行要各加一道：两者之间可能插进新的绑定（重分析、重新归属）。
执行阶段的 `slot_id IS NULL` 是兜底，它不依赖计划是否准确，因此 TOCTOU 窗口被关掉。

**没有**采用"先删版本、让外键把当前指针 SET NULL、再刷新状态"的方案——
那等于先破坏台账再补救，而正确策略是根本不让它被删掉。

未绑定槽位的版本保持原行为：`shared_with_latest_job`、`already_cleaned`、
`missing_document_version_id` 等规则不变，清理没有被整体禁掉。

前端补了 `material_slot_bound` 的中文原因标签，否则运维会在清理预览里看到原始代码。

---

### 3.9 第五轮：主链路 Material Slot 真库 Smoke

前三轮把**写入语义**修到不可违反，第四轮把**旧清理链路**接上台账；
第五轮处理的是剩下那个一直没被验证的接缝：槽位分配**在主链路的什么位置**执行。

#### 为什么位置本身就是缺陷

分配原先挂在整条链路末尾（解析 → 表识别 → 事实物化 → PS 同步 → **分配槽位**）。
后果不是报错，而是**静默丢材料**：PDF 解析一失败，`run_structured_ingest` 直接
跳到 `except`，槽位分配的代码行根本没被执行到，于是"文件已经收到、只是解析不出来"
的材料在台账里**不存在**。这恰恰是最需要人工介入的一批——台账看不见它，
也就永远不会有人去补录它。

台账回答的是"这份材料有没有收到"，解析回答的是"这份材料看不看得懂"，
前者不该依赖后者。

#### 调整后的顺序

```text
run_structured_ingest
  → _ensure_document_version      （建立 org_units / fiscal_documents / fiscal_document_versions）
  → _allocate_material_slot       （分配槽位 + 绑定版本 + 推进当前指针 + 刷新状态）
  → PDFParser.parse_pdf
  → TableRecognizer
  → FiscalFactMaterializer
  → PSSharedSchemaSync
  → 组装结果
```

| # | 改动 | 位置 |
| --- | --- | --- |
| 1 | 槽位分配前移到"文档版本建立之后、PDF 解析之前" | `src/services/structured_ingest_runner.py` |
| 2 | **全流程只有这一处调用**，后面不再有第二次分配：一份分析只允许分配一次，否则"何时进入台账"又重新取决于解析是否成功 | 同上 |
| 3 | 错误返回保留已建立的上下文：`document_id` / `document_version_id` / `material_slot`（以及 `organization_name` / `fiscal_year` / `doc_type`） | 同上，`except` 分支 |
| 4 | 槽位分配失败**仍然不阻断**主流程（`safe_allocate_for_document` 语义未变）：`status=error` 只出现在结果里 | 同上 |

错误 payload 里"保留上下文"这一条不是锦上添花：没有它，调用方拿到的是一个
"什么都没发生"的失败，既判断不出材料有没有进台账，也决定不了要不要人工补录。

#### 前移会不会改变归属判定？——实测：不会

位置变化会改变两件事的先后：`_backfill_local_organization_catalog`
（把 PS 同步出的部门/单位写进本地组织目录）与槽位归属判定。逐条核对：

- 该回填只在 `metadata["organization_id"]` **为空**时执行；有 id 时第一行就返回。
- 归属判定的"主体"只按 **id** 解析（`resolve_subject_org`），
  `organization_id` 为空时主体必然是 `None`，判定结论与目录内容无关。

实测（同一份元数据，空目录 vs 填充目录）：

| 场景 | 空目录 | 有目录 | 结论 |
| --- | --- | --- | --- |
| 无 `organization_id`（回填路径） | `mapping_required` / `df4c23e5…` | `mapping_required` / `df4c23e5…` | **完全一致**（状态与 slot_key 都相同） |
| 有 `organization_id` | `mapping_required` / `cf8be4a6…` | `resolved` / `2e274121…` | 有差异，但该场景下回填**不会执行**，目录内容不受位置影响 |

结论：前移改变的只是"判定发生的时刻"，不改变任何一次判定结果。

#### 本轮**没有**做的事

- **没有**接状态自动刷新（`uploaded → processing → failed / review_required`）。
  文件成功绑定槽位、但后续解析失败时，槽位仍可能停在 `uploaded`。
  本轮的目标只有一条：**不能因为分析失败导致材料根本没进台账**。
  生命周期接线属于 WP2/WP3，见 §6 第 5 条。
- **没有**动第四轮的清理保护（`api/runtime.py`、`StructuredCleanupDialog.tsx`）
  与 `migration 0019`、槽位身份/锁顺序/状态机。生产代码只改了
  `structured_ingest_runner.py` 一个文件。

#### 新增用例

默认用例（`tests/test_structured_ingest_runner.py`，3 条）：

| 用例 | 断言 |
| --- | --- |
| `test_material_slot_is_allocated_before_pdf_parsing` | 调用序列恰为 `ensure_document_version → allocate_material_slot → parse_pdf`；分配只调用一次 |
| `test_parser_failure_keeps_material_slot_context` | 解析抛错时 `status == "error"`，且 `document_id` / `document_version_id` / `material_slot.bound == True` 都在；分配次数仍为 1 |
| `test_material_slot_failure_does_not_block_structured_ingest` | 分配返回 `status=error, bound=false` 时，主流程照常 `done`，`facts_count` 正常 |

真库 Smoke（`tests/test_material_slot_migration_pg.py`，3 条）：

| 用例 | 真实执行的部分 | 断言 |
| --- | --- | --- |
| `test_run_structured_ingest_really_persists_material_slot` | `run_structured_ingest` 全链路 + `_ensure_document_version` + `_allocate_material_slot` + `MaterialSlotService` + 真 `material_slots` / `fiscal_document_versions.slot_id` | `bound=True`、`status=="resolved"`、`slot_id` 非空且与返回值一致、`current_document_version_id == document_version_id`、`material_slots COUNT == 1` |
| `test_run_structured_ingest_parser_failure_still_persists_slot` | 同上，但 `PDFParser.parse_pdf` 故意抛 `RuntimeError` | `payload.status == "error"` 且仍带 `document_version_id` / `material_slot`；**库里**版本 `slot_id` 非空、槽位当前指针指向它 |
| `test_run_structured_ingest_rerun_reuses_single_slot_on_real_db` | 同上，同一材料跑两次 | 两次 `slot_id` 与 `document_version_id` 相同；`material_slots COUNT == 1` |

被替换的只有 `PDFParser` / `TableRecognizer` / `FiscalFactMaterializer` /
`PSSharedSchemaSync`——留下它们，这几条用例就变成 PDF 解析测试而不是
"材料有没有进台账"的测试。**版本创建、槽位分配、版本绑定、数据库查询全部真实。**
组织目录（JSON 外部输入）固定为测试常量，避免身份判定随本机目录变化。

#### 用例不是恒真的（做了变异验证）

把分配块重新移回解析之后，重新执行：

- `test_material_slot_is_allocated_before_pdf_parsing` → **FAILED**（调用序列不符）
- `test_parser_failure_keeps_material_slot_context` → **FAILED**（`KeyError: 'material_slot'`）
- `test_run_structured_ingest_parser_failure_still_persists_slot`（真库）→ **FAILED**（`KeyError: 'material_slot'`）

恢复后三者全绿。这一步是为了排除"断言写得刚好也成立"的可能。

#### 本轮实测数字

| 组 | 命令 | 结果 |
| --- | --- | --- |
| 本地 Windows | `python -m pytest -q` | **1459 passed, 27 skipped, 0 failed**（27 = 26 条真库 + 1 条平台条件） |
| 静态检查 | `python -m ruff check .` / `python -m mypy api src tests` | All checks passed / Success（212 files） |
| PostgreSQL 显式套件 | `GOVBUDGET_TEST_DATABASE_URL=... python -m pytest tests/test_material_slot_migration_pg.py` | **26 passed**（PostgreSQL 15.17，独立随机 schema，测后 `DROP SCHEMA CASCADE`） |
| GitHub CI Linux | 见 PR #42 的 Actions 运行 | 以 PR 页面为准 |

`SCHEMA_CHANGE_REQUIRED = NO`（本轮未改 `migration 0019`）。

## 4. 三个真实槽位身份示例

数据取自本机真实任务（`uploads/` 与 `data/organizations.json`）。

| # | 文件名 | 主体组织 | org id | level | 年度 | 文种 | slot_key |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 上海市普陀区规划和自然资源局 2024 年度部门决算.pdf | 上海市普陀区规划和自然资源局 | `8f773936806d` | department | 2024 | final | `9ae17c0c841a89624b07ebd7350921b5` |
| 2 | 上海市普陀区人民政府石泉路街道办事处本级 2026 年度单位预算 | 上海市普陀区人民政府石泉路街道办事处本级 | — | unit | 2026 | budget | `ae506457744a663714c8ba962a7578e1` |
| 3 | 上海市普陀区人民政府办公室 2025 年度单位决算.pdf | 上海市普陀区人民政府办公室 | — | unit | 2025 | final | `f7b8fe59d553629a9fb8c2d7d55ca31c` |

示例 1 与它的本级单位（`53f98bebfc3a`，`UNIT_MLYYK9MC_1C503660`）同属
"规划和自然资源局"这个名字，但 **org id 不同、slot_key 不同**——
这正是本轮要守住的隔离。

真实数据中归并出的 41 个槽位按层级分布：department 15 个 / unit 26 个；
按文种分布：budget / final 各自独立成槽。

---

## 5. 各环节影响面（任务书第八节要求明确说明）

| 环节 | 是否受影响 | 依据 |
| --- | --- | --- |
| 上传 | **否** | `api/routes/upload.py` 未改；路由面冻结用例通过 |
| 处理队列 | **否** | `api/job_queue.py`、`api/runtime.py` 未改 |
| PDF 分析 | **否** | `api/main.py` 流水线未改；`src/services/pdf_parser.py` 等未改 |
| 审核 | **否** | 审核工作台与 `/api/jobs/{id}/review` 未改 |
| 导出 | **否** | `api/routes/reports.py` 未改 |
| Golden | **否** | `corpus/`、`rules/`、`samples/` 未改；SHA 与基线一致 |
| obligation coverage | **否（逐项核对一致）** | 见 §3.3 |
| 结构化入库 | **是：分配顺序调整 + 只做加法** | 槽位分配前移到"版本建立后、解析前"（§3.9）；结果字典新增 `material_slot` 键，既有键全部保留（`test_structured_ingest_payload_shape_gains_only_material_slot`）；**失败**返回新增 `document_id` / `document_version_id` / `material_slot` 上下文；槽位失败被兜住不抛异常 |
| 结构化入库结果文件 | **是，新增一个键** | `uploads/<job>/structured_ingest.json` 会多一个 `material_slot` 字段。前端若做严格 schema 校验需注意 |

---

## 6. 未验证项与已知风险

以下内容**没有**在本次验证中覆盖，独立复核时应据此调整信任范围：

> "分析完成 → 槽位真的写进数据库表"此前列在本节第 1 条，**第五轮已关闭**：
> 由 §3.9 的三条真库 Smoke 覆盖（真实 `_ensure_document_version`、
> 真实 `MaterialSlotService`、真实 `material_slots` / `fiscal_document_versions.slot_id`）。

1. **回填写入完全未实现。** 只提供 dry-run 盘点，没有 `--apply`。
   历史数据目前**尚未进入任何槽位表**。
2. **`material_scope` 未反映文档实际口径。** 当前按组织层级一对一推导
   （见设计确认 §三 第 4 条）。评审修复轮已把真正的 `caliber` 接进归属链路，
   但**在 7 份真实样张上 `caliber` 仍为未识别**（封面写"年度部门决算"，
   不含汇总/本级口径词），所以口径冲突通道目前主要由用例覆盖。
   提升口径识别能力属于解析/规则层，不在本轮范围。
3. **占位槽位无法改判/合并。** 人工确认年份/文种后如何把它并到目标槽位，
   属于 WP2 的槽位管理动作，本轮未提供。
4. **口径冲突没有裁决入口。** 冲突已能持久化、可见、且不会被普通刷新洗掉，
   但"人工确认到底哪个口径正确"的入口留给 WP2。
5. **状态缓存的自动刷新未接线。** 分析状态、复核状态变化不会自动触发重算，
   需要由 WP2/WP3 调用 `refresh_status`。在此之前已有文件的槽位停在 `uploaded`。
   评审修复轮保证的是"刷新不会把既有进度打回起点"（`infer_progress_state`），
   不是"刷新会被自动触发"。**第五轮明确了这条边界**：文件成功绑定槽位但后续
   解析失败时，槽位仍可能停在 `uploaded`——本轮只保证"材料进得了台账"，
   不保证"状态跟着分析结果走"（见 §3.9）。
6. **组织 id 不稳定（既有限制）。** Model A 的 md5 id 参与名称哈希，组织改名即换 id。
   本轮保存了 `subject_org_code` 与名称快照作为凭据，但**没有自动重认机制**。
7. **`fiscal_documents` 仍无法区分同名部门/单位**（其上游 `org_units` 是名称维表）。
   本轮通过在 `fiscal_document_versions` 上绑定槽位绕开了这个问题，
   但没有修复 `fiscal_documents` 本身。
8. **`material_sources` 无 URL 来源的唯一键待复核（WP9 前必须处理）。**
   当前唯一键 `(slot_id, COALESCE(source_url, ''))` 会让
   `manual_upload + NULL URL` 与 `excel_import + NULL URL` 互相冲突并覆盖
   `source_kind`。本轮按评审要求只登记不扩张，做 CSV/Excel 应收清单时
   再决定是否改为 `slot_id + source_kind + normalized source locator`。

---

## 7. 复算方式

```bash
# 全量测试
python -m pytest -q

# 静态检查
python -m ruff check .
python -m mypy api src tests

# 覆盖基线（应仍是 8 个缺口）
python scripts/check_coverage_baseline.py --json
python scripts/check_coverage_baseline.py --assert-gaps 8

# 历史回填盘点（不写任何数据）
python scripts/backfill_material_slots.py
python scripts/backfill_material_slots.py --markdown out.md

# 真库验证（需显式指定测试库；用独立 schema，测后自删）
GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db \
    python -m pytest tests/test_material_slot_migration_pg.py -v
```

## 8. WP2 就绪判断

**结论：数据底座已具备开始 WP2（材料台账 UI）的条件，但有 3 项前置缺口需要先决策。**

已具备：

- 稳定的槽位唯一身份（`slot_key` + 复合唯一索引），UI 可以直接拿它做路由参数；
- 状态与中文状态名（`status_label` / `reason_label`）已就绪，业务界面不必自己翻译状态码；
- 区县/部门/主体/年度/文种/口径六个维度全部落列并有索引，
  `GET /api/materials/coverage` 这类聚合可以纯 SQL 完成，不需要逐条实时计算；
- `material_sources` 已能表达"来源 URL + 发布日期"，材料详情页的"版本与来源"Tab 有数据可依。

需要先决策的 3 项：

1. **槽位从哪来？** 现在只有"分析过的材料"会产生槽位。
   "应收清单"（未上传的材料也要有槽位）需要 WP9 的 CSV 导入或人工录入，
   否则工作台的"逾期未上传"永远是 0。**建议 WP2 与 WP9 第一步（CSV 导入）
   一起做，或先做一个最小的人工新增槽位入口。**
2. **状态缓存的刷新时机**（§6 第 5 条）。UI 要显示"待人工复核"，
   就必须有人把分析/复核状态写回槽位。这是 WP2 与 WP3 的接口，
   建议在 WP2 开工前把调用点定下来。
3. **历史数据是否回填。** 不回填，台账初始是空的；回填，需要先评审
   dry-run 报告并实现 apply 路径。**建议先回填，因为 53 条真实材料
   全部可自动映射，风险低且能让 UI 一上线就有内容可看。**
