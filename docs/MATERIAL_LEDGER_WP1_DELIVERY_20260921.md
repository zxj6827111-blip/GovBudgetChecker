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
| `src/services/structured_ingest_runner.py` | 新增 `build_ingest_metadata` 纯函数；结构化入库后调用槽位分配，结果里新增 `material_slot` 摘要字段 | **只做加法**。槽位失败被 `safe_allocate_for_document` 兜住，不改任何既有字段、不阻断主流程 |
| `api/main.py` | 构造结构化入库 metadata 改用 `build_ingest_metadata`，把主分析链路已算好的 `document_profile` 一并传入（评审修复） | 管线多传一个入参；`run_structured_ingest` 的签名与返回契约未变 |
| `.env.example` | 声明新环境变量 | 无 |

> 除上述三个文件外，**没有修改任何既有源码**。
> 规则引擎、PDF 解析、AI 抽取、上传路径、任务队列、审核工作台一行未动。
> `api/main.py` 是本轮唯一被触及的既有生产文件，净改动是"import 多一项 +
> 构造 metadata 时多传一个画像参数"，没有改变任何既有分支或返回值。

### 新增：测试（148 条）

| 文件 | 条数 | 默认是否运行 |
| --- | --- | --- |
| `tests/test_material_slot_identity.py` | 36 | 是 |
| `tests/test_material_slot_migration.py` | 28 | 是 |
| `tests/test_material_slot_backfill.py` | 11 | 是 |
| `tests/test_material_slot_api_regression.py` | 5 | 是 |
| `tests/support_material_slot_db.py`（辅助） | — | — |
| `tests/test_material_slot_binding_and_state.py` | 37 | 是 |
| `tests/test_material_slot_profile_integration.py` | 15 | 是 |
| `tests/test_material_slot_migration_pg.py` | 16 | 否（需 `GOVBUDGET_TEST_DATABASE_URL`） |

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
| 5 | 同 PDF 版本两次分析仍只有一个 slot | `test_reanalysis_of_same_version_does_not_create_new_material`（假连接）+ `test_service_allocate_and_bind_against_real_database`（真库） | ✅ |
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
| `python -m pytest -q` | 1307 passed, 1 skipped, 0 failed | **1439 passed, 17 skipped, 0 failed** | +132 passed / +16 skipped（新真库用例默认跳过），**零失败** |
| `ruff check .`（Makefile 与 CI 同款全仓命令） | All checks passed | **All checks passed** | 无变化 |
| `mypy api src tests` | Success（199 files） | **Success（210 files）** | 无变化 |

基线数字取自 `4a1cabf` 的干净检出（`git worktree`），不是带改动的当前树。

### 3.2 真库验证（PostgreSQL 15.17）

```bash
GOVBUDGET_TEST_DATABASE_URL=postgresql://.../fiscal_db \
    python -m pytest tests/test_material_slot_migration_pg.py -v
# => 16 passed
```

覆盖：全新库应用、第二次 no-op、既有库升级只增 0019、语句重放、schema 形状、
复合唯一索引拦截、`mapping_key` 区分未知年份、CHECK 约束、删槽位不删文件版本、
**回滚 SQL 实测**、服务层端到端幂等；评审修复轮又补了
**跨槽重绑被拒且整体回滚**、**两连接并发绑定只有一个胜者（行锁生效）**、
**同 `created_at` 按 id 决定新旧**、**口径冲突持久化且刷新洗不掉**、
**`mark_not_applicable` 尊重身份门槛**。

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

---

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
| 结构化入库 | **是，但只做加法** | 结果字典新增 `material_slot` 键；既有键全部保留（`test_structured_ingest_payload_shape_gains_only_material_slot`）；槽位失败被兜住不抛异常 |
| 结构化入库结果文件 | **是，新增一个键** | `uploads/<job>/structured_ingest.json` 会多一个 `material_slot` 字段。前端若做严格 schema 校验需注意 |

---

## 6. 未验证项与已知风险

以下内容**没有**在本次验证中覆盖，独立复核时应据此调整信任范围：

1. **线上结构化入库的真实写入未做端到端验证。** 服务层已在真库上验证
   （`test_service_allocate_and_bind_against_real_database`），评审修复轮又把
   **画像接线**做成了行为级验证（真跑 `_run_pipeline_inner` 并捕获交给
   `run_structured_ingest` 的 metadata），但"分析完成 → 槽位真的写进数据库表"
   这一段仍然**没有跑过**：`run_structured_ingest` 在测试里被 mock 掉了，
   因为触发真实入库需要可用的数据库与完整的解析链路。
   **建议复核时在一个可控任务上跑一次分析，检查 `structured_ingest.json`
   的 `material_slot` 字段与 `material_slots` 表的实际行。**
2. **回填写入完全未实现。** 只提供 dry-run 盘点，没有 `--apply`。
   历史数据目前**尚未进入任何槽位表**。
3. **`material_scope` 未反映文档实际口径。** 当前按组织层级一对一推导
   （见设计确认 §三 第 4 条）。评审修复轮已把真正的 `caliber` 接进归属链路，
   但**在 7 份真实样张上 `caliber` 仍为未识别**（封面写"年度部门决算"，
   不含汇总/本级口径词），所以口径冲突通道目前主要由用例覆盖。
   提升口径识别能力属于解析/规则层，不在本轮范围。
4. **占位槽位无法改判/合并。** 人工确认年份/文种后如何把它并到目标槽位，
   属于 WP2 的槽位管理动作，本轮未提供。
5. **口径冲突没有裁决入口。** 冲突已能持久化、可见、且不会被普通刷新洗掉，
   但"人工确认到底哪个口径正确"的入口留给 WP2。
6. **状态缓存的自动刷新未接线。** 分析状态、复核状态变化不会自动触发重算，
   需要由 WP2/WP3 调用 `refresh_status`。在此之前已有文件的槽位停在 `uploaded`。
   评审修复轮保证的是"刷新不会把既有进度打回起点"（`infer_progress_state`），
   不是"刷新会被自动触发"。
7. **组织 id 不稳定（既有限制）。** Model A 的 md5 id 参与名称哈希，组织改名即换 id。
   本轮保存了 `subject_org_code` 与名称快照作为凭据，但**没有自动重认机制**。
8. **`fiscal_documents` 仍无法区分同名部门/单位**（其上游 `org_units` 是名称维表）。
   本轮通过在 `fiscal_document_versions` 上绑定槽位绕开了这个问题，
   但没有修复 `fiscal_documents` 本身。
9. **`material_sources` 无 URL 来源的唯一键待复核（WP9 前必须处理）。**
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
