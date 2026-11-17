# 识别、事实绑定与复核流程整改交付报告（2026-11-16）

## 1. 基线与分支

| 项目 | 值 |
| --- | --- |
| 起始分支 | `feat/unified-remediation-20261115` |
| 基线提交 | `612d0c522806d6caf3bf44ed483f9a1fbd1af40c` |
| 整改分支 | `codex/recognition-review-remediation` |
| 阶段提交 | `a45c3631`（结果契约）→ `e53f4671`（识别与级联）→ `012c6d50`（页面/计数/导出）→ `18b7807e`（回放工具/报告）→ `30df1309`（测试变更说明）→ `b33a2d6b`（自检修正：真实覆盖状态/legacy_unknown）→ `7c33adc5`（覆盖状态标签修正） |
| 工作区起点 | 仅有 5 份未跟踪金标/基线文档；无已跟踪源码改动，未覆盖其他工作 |
| 隔离前端验收 | `/tmp/gbc_app_e2e`（源副本 + 主仓库 `node_modules` 软链）构建于 `127.0.0.1:3101` |
| 隔离 PostgreSQL | Docker 容器 `gbc-remediation-pg-20261116`（`127.0.0.1:55450`） |

> 说明：`app/.next` 在验证期间被生产构建与已运行的 `next dev` 共用后失效，
> 主前端开发进程（:3000）需要重启才能恢复；按“Codex 复核通过前不更新本地服务”
> 的约定，本轮未重启主服务。隔离构建已完整通过前端单测、构建和 E2E。

## 2. 整改后的产品行为

1. **业务问题队列只保留具体材料问题和有原文依据的疑点**：技术诊断、未完成检查、
   全局处理原因不再生成 `obligation:*` / `unfinished_check` / `review_reason` 业务卡片。
2. **技术异常在材料层汇总**：新报告提供 `processing_summary`（执行状态 / 覆盖状态）
   与 `diagnostics`（cause_kind、根问题关联、下一步动作）。页面只显示一条范围说明，
   不要求业务人员逐项替系统表态。
3. **已报材料缺陷与受影响计算分开记录**：正式 Issue 存在时，相关金额关系仍保留
   “未完成”事实，并关联 `root_issue_ids`；不把未完成改写成通过，也不隐藏已证实问题。
4. **识别入口收敛**：章节标题、合计行标签、空白金额格、共享“其中”分项、
   名称/编码身份冲突使用同一套解析与证据约束；修复发生在规则取数入口，不是隐藏提示。
5. **计数与签字口径统一**：列表 `queue_required_total` 只统计正式问题；内部未完成关系
   保存在 `queue_obligation_total` / `queue_diagnostic_total`，不再阻断已展示问题的复核；
   检查未完整时仍不显示“检查通过”。

## 3. 关键实现

### 3.1 结果契约（schema v3）

- 新增 `src/schemas/result_semantics.py`：`cause_kind` 固定为
  `material_defect` / `upstream_material_defect` / `extraction_failure` /
  `identity_conflict` / `execution_failure` / `legacy_unknown`。
- `src/schemas/review_report.py`：
  - `schema_version=3`，新增 `diagnostics`、`processing_summary`、`review_summary`；
  - 未完成义务、结构化识别项、全局处理原因进入 `diagnostics`；
  - `manual_review` 只保留具体候选、AI 候选和冲突记录；
  - 导出 `conclusion` 内附同一时点的处理/复核摘要（CSV `conclusion_json` 同步）。
- 根问题关联由报告层用同一次分析的正式 Issue 调 `cascade_root_issue_ids` 判定；
  找不到证据时保持 `extraction_failure`，不按中文提示猜材料缺陷。

### 3.2 识别与取数

- V33-110：半角 `(三)` 与全角 `（三）` 同等识别（`rules_v33.py`）。
- V33-106：合计行/合计列标签忽略全半角与空白（`合 计` → `合计`）。
- V33-107 / V33-223：章节选择先排除目录条目与无正文候选，再取正文；
  正常标题只剩目录时，错字标题备用路径才生效。
- V33-227：排除缺“基”的第六节标题（`本支出`），恢复第五节正文选择。
- V33-240：收入决算表整行认证空白（如 2013499）的合计列也按 U-5 约定取 0，
  横向与纵向关系实际执行；空白项仍逐格通过 `blank_term` 原页复验。
- V33-251：识别同一编号条目内多个 `（项）` 共享“其中：”分项金额的组级披露；
  支出决算分项不再误报“未披露”；只有组级年初预算时单独报“未逐项列示”。
- V33-CROSS-SAN-GONG-ECON：原件行内编码与名称指向不同业务项（如 31007 / 公务用车购置）
  交付具体材料问题；该业务项跨表金额比较保持 unknown。
- 名称/主体：单位名称多字（“局局”）仍由 CMM-017 报出，正文正确金额不再被误判为
  其他主体；V33-102 不再重复报名称错误。价格认证中心“－机关事业单位…”前导连接号
  只用于候选匹配，不影响金额身份。

### 3.3 同源缺陷归并与级联

- 同一处“万万元”由 V33-222 / V33-251 / CMM-011 命中时合并为一个主问题，
  保留成员规则、原格证据和独立回执；CMM-011 不再追加“缺少万元”误报。
- 重复单位、V33-251 披露/加总问题、单位名称问题、缺表/空表、表格读取异常
  分别按同一原件证据关联到根问题；受影响义务标为 `upstream_material_defect`。
- 独立事项（如公务接待费基期冲突、20805 明细缺失）不被根问题归并吞掉。

### 3.4 页面、计数、导出与签字

- 审核台不再把补核义务转为 `Problem`；参考区只保留具体候选和复核事项。
- 列表“必须处理”不再叠加 `obligation_total`；新增 `queue_diagnostic_total`
  仅作内部覆盖统计（`JOB_SUMMARY_SCHEMA_VERSION=5`，旧摘要自动重建）。
- 完成门禁仍校验文档版本、分析代际与正式问题处理状态；技术部分失败允许复核
  已展示问题，但 `signoff_only` 仍只对 `done` 分析成立，不显示虚假“检查通过”。
- 状态说明统一为“已发现的问题可继续核实；本次检查有 N 项内部关系未完成，
  结果尚不完整”“不需要逐项人工表态”等具体表述。

## 4. 验证命令与结果

| 层级 | 命令 | 结果 |
| --- | --- | --- |
| 后端全量 | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/pytest -p no:cacheprovider -q` | **6282 passed, 193 skipped** |
| 真实 PostgreSQL | `TESTING=true GOVBUDGET_TEST_DATABASE_URL=postgresql://govbudget_ci:govbudget_ci@localhost:55450/fiscal_ci pytest tests/test_review_lifecycle_pg.py tests/test_obligation_review_pg.py tests/test_material_slot_migration_pg.py -q -m real_database` | **97 passed** |
| 隔离库迁移 | `DATABASE_URL=...55450... .venv/bin/python scripts/run_db_migrations.py` | 45 个迁移全部应用；第二遍 0 个（幂等 PASS） |
| Ruff | `.venv/bin/python -m ruff check .` | All checks passed |
| Mypy | `.venv/bin/python -m mypy api src tests` | Success: no issues in 672 source files |
| 日志安全 | `.venv/bin/python scripts/check_log_message_safety.py` | 通过 |
| 环境变量对账 | `.venv/bin/python scripts/check_env_consistency.py` | 通过（170 / 163 / 82） |
| Compose 配置 | `docker compose -f docker-compose.yml -f docker-compose.ai.yml config --quiet` | 通过 |
| 前端单测 | `npm --prefix app run test:unit` | 全部通过 |
| 前端构建 | `npm --prefix app run build` | 通过 |
| 前端 E2E | 隔离构建 `:3101` 跑 obligation-review、review-lifecycle、review-list、full-flow-review-export、ai-scope-explanation、review-layout-batch | **30 passed** |

### 4.1 测试预期变更说明（均为产品契约变更，不是放宽断言）

| 测试 | 原预期 | 新预期 | 原因 |
| --- | --- | --- | --- |
| `tests/remediation/test_zc04.py` | schema v2；未完成义务在 `manual_review` | schema v3；未完成义务在 `diagnostics` | 本轮统一结果契约 |
| `tests/test_dh03a_legacy_fields.py`、`test_u7_same_source_display.py`、`test_obligation_cross_location_coverage.py` | 未完成检查在 `manual_review` | 同字段原样保留，但位置改为 `diagnostics` | 技术诊断退出业务卡片 |
| `tests/test_report_exports.py` | 结构化待复核在 `manual_review` | 在 `diagnostics`，仍无伪造页码 | 同上；JSON 报告仍完整导出 |
| `tests/test_review_known_regressions.py`、`tests/test_queue_summary.py` | 全局 review reason 计入参考通知 | 参考通知 0，原因进入 `diagnostics` | 泛化“系统提示”卡片退出业务区 |
| `tests/test_workflow_rbac.py` | 系统 review reason 可写 workflow 结论 | 返回 404，只保存在 `diagnostics` | 技术异常不要求业务人员逐项表态 |
| `tests/remediation/test_round6_export_chain.py` | 篡改串使用 `rule_evidence_incomplete` | 改用导出中仍存在的人工决定说明 | 该原因已按契约退出业务导出；防篡改断言不变 |
| `tests/test_cross_san_gong_econ.py` | 错码只记取数不足、不得出 finding | 仍不得给 reception 出金额比较 finding；另许一条独立编码/名称材料问题 | 方案要求“有原件证据的缺陷形成具体问题” |
| `app/tests/aiScope.test.ts`、`reviewObligationAdapters.test.ts`、`workbenchAdapters.test.ts` | “未能自动核对”文案与必处理叠加 | “内部检查未完成”范围说明；不叠加必处理 | 页面口径统一 |

所有变更均在对应测试中保留/加强原件证据与“不虚假通过”断言；没有通过扩大容差、
忽略失败或增加重试来换取通过。

新增/更新测试：

- `tests/test_result_semantics_20261116.py`（5 项）：schema v3、cause_kind、
  诊断退出业务队列、legacy_unknown、根问题 upstream 关联。
- `tests/test_recognition_review_remediation_20261116.py`（8 项）：半角章节、
  目录/错字章节、`合 计`、2013499 空白行横纵勾稽、重复单位归并、CMM-011 误报消除、
  V33-251 共享分项、主体多字。
- `tests/test_queue_summary.py`：必须处理不叠加技术诊断，内部覆盖单独计数。
- `e2e/tests/obligation-review.spec.ts`：改为验证技术诊断不出业务卡片。
- `e2e/tests/review-lifecycle.spec.ts`：改为验证技术诊断只作范围说明、不阻断签核。
- `tests/test_workflow_rbac.py`：系统处理原因不再作为业务问题进入 workflow；
  对应记录只存在于 `diagnostics`。

## 5. 真实原件回放前后对比

回放命令（只读）：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python \
  scripts/replay_recognition_remediation_20261116.py \
  --job-dir uploads/<job> [--job-dir ...] \
  --output outputs/recognition_remediation_20261116/playback_after.json
```

基线结果由同一脚本在 `612d0c52` 的临时 worktree 生成（`playback_before.json`）。
证据文件：

- `outputs/recognition_remediation_20261116/playback_before.json`
- `outputs/recognition_remediation_20261116/playback_after.json`

| 案例 | 规则 | 整改前 | 整改后 |
| --- | --- | --- | --- |
| 95c937cc 上海市审计局 2024 决算 | V33-110 | 未找到章节（半角 `(三)`） | 执行完成，按原文金额判定 |
| 05e40add 合作交流办 2024 决算 | V33-106 | 未提取到合计金额（`合 计`） | 执行完成 |
| d835cf2a 市政府办公厅 2023 决算 | V33-107 / V33-223 | 未提取到基本支出金额 | 执行完成 |
| 同上 | V33-227 | 未提取到科目提及（误选第六节） | 执行完成，16 个科目提及 |
| 0ed5318c 市教委 2024 决算 | CMM-011 | 重复单位 + “缺少万元” 2 条 | 仅重复单位 1 条；全链路 4 条同源单位问题归并为 1 条主问题 |
| edb56827 工商外国语学校 2025 决算 | V33-251 | 支出决算 2 处“未披露” | 支出决算加总 **pass**；仅保留“年初预算未逐项列示”具体问题 |
| 812ab776 市生态环境局 2024 决算 | V33-102 / CMM-017 | 主体冲突阻断取数 | CMM-017 保留名称问题；V33-102 正文金额正常执行 |
| 30907018 价格认证中心 2025 决算 | 名称级联 | 前导“－”导致不归并 | 归并到 V33-227 根问题 |
| 3581e62b 民族宗教局 2024 决算 | 名称级联 | 正常 | 保持归并到 V33-227，无退化 |
| b4e3ba95 机关事务管理局 2024 决算 | V33-CROSS-SAN-GONG-ECON | 仅取数不足，无具体问题 | 新增 1 条 31007/公务用车购置编码名称错配问题；金额比较仍 unknown |
| **34f7645b 民族宗教局 2024 决算** | **V33-240** | **2013499 total_actual 缺失，OBL-INTERNAL-T2-TOTAL 未完成** | **横向 2013499 关系 `not_applicable`（全行空白=0）、纵向 20134 关系 `pass`；V33-240 执行完成** |

### 5.1 完整规则流水线复跑（不只是单规则）

用真实 `artifact_snapshot + fact_context + 原件` 构造 `page_texts/page_tables`
后执行全部 90 条适用规则（不调 AI、不落库）：

| 案例 | 执行/适用 | pass | fail | insufficient | execution_error |
| --- | --- | --- | --- | --- | --- |
| 95c937cc 上海市审计局 | 90/90 | 75 | 13 | 0 | 0 |
| 05e40add 合作交流办 | 90/90 | 77 | 11 | 0 | 0 |
| d835cf2a 市政府办公厅 | 90/90 | 82 | 6 | 0 | 0 |
| 34f7645b 民族宗教局 | 90/90 | 78 | 10 | 0 | 0 |

- 回放使用任务持久化的 `document_profile` 与物理片段页表，四份材料均
  **90/90 规则到达终态**，无 `parse_error` / `insufficient_data` /
  `execution_error`；V33-110/106/107/223/227 不再出现在未完成规则中。
- 34f7645b 的义务账本：
  `OBL-INTERNAL-T2-TOTAL` = **completed**（V33-240=fail，失败原因是原件 20805
  明细缺失 837.63 万元的真实材料问题），不再是 `insufficient_data`；
  `OBL-TXT-T1/T2/T3/T5/FINAL-DETAIL` 均为 completed。
- 回放脚本已按物理片段还原 `doc.page_tables`，避免旧规则因回放环境缺页表
  被误判为执行异常；这属于回放工具修正，不改变生产流水线。

## 6. 目标案例 34f7645b：断点与验证

- 原件：`uploads/34f7645bc5f1cc5f09530c9d9d3cf3af/上海市民族和宗教事务局.pdf`
- 页面：第 8 页《收入决算表》，科目 2013499「其他统战事务支出」。
- 原件事实：该行 8 个金额列均无任何文字，解析网格 `presence=blank`，
  `native_blank_verified=True`；bbox 为 `[285.0, 410.18, 362.6, 433.68]`（合计列）。
- 断点：`build_facts_for_table` 只为有数值的金额格生成事实，2013499 行没有事实；
  V33-240 的横向子检查对 `total_actual` 未允许 `blank_term`，于是
  `select()` 返回“操作数缺失”，义务记为 `insufficient_data`；
  诊断位置还错借了同表首条事实的 bbox。
- 修复：
  1. 收入表横向检查对合计列同样允许认证空白按 0；
  2. `_record_operand_gap` 使用同一行同一列的源格页码/bbox，不再借邻行坐标；
  3. 纵向 `_select` 原有空白观察路径继续生效。
- 验证：横向 `FIN_02_income:2013499:income_sources` 状态 `not_applicable`
  （原件完整网格各项均为空白，无发生额关系）；纵向
  `FIN_02_income:20134:total_actual` 状态 `pass`；2013499 仍不伪造金额事实。
- 同时保留的真实发现：20805「行政事业单位养老支出」1136.48 万元 >
  已列明细 298.85 万元，差额 837.63 万元——这是原件缺明细行，属新暴露的
  真实材料问题，不是本轮误报。

## 7. 问题增减解释

| 变化 | 原因 | 原件依据 |
| --- | --- | --- |
| V33-110/106/107/223/227 的“未找到/未提取到”减少 | 章节与合计入口修复，原文本来有内容 | 95c937cc p21、05e40add p13、d835cf2a p22 |
| CMM-011“缺少万元”减少 1 条 | 同一处“万万元”不再由缺单位规则重复报 | 0ed5318c p32 |
| 同源单位问题 4 → 1 条主问题 | 按原文字面量与页码归并，成员证据保留 | 0ed5318c p32 |
| V33-251“支出决算未披露”减少 2 条 | 共享“其中：”分项金额已列示 | edb56827 p18-p19 |
| V33-251“年初预算未逐项列示”保留/具体化 | 原文只有组级预算 2064.21，未逐项列示 | edb56827 p19 |
| V33-240 未完成 → 执行完成 | 2013499 全行认证空白按 0；纵向关系已可执行 | 34f7645b p8 |
| 新增 31007 编码/名称错配 1 条 | 原件行内编码与名称指向不同业务项，属具体材料问题 | b4e3ba95 p17 |
| 新增 20805 明细缺失 1 条 | 原件只列 2080501，20805 其余明细未列 | 34f7645b p8 |

## 8. 未完成 / 剩余事项

1. **Codex 独立复核未完成**：本轮按约定停在隔离验证，未合并主分支、未推送远程、
   未重分析存量、未更新本地服务。
2. **主前端开发进程需重启**：验证期间 `app/.next` 被生产构建与运行中的 `next dev`
   共用后失效；请在复核通过后的发布步骤中重启前端（不影响后端 18743）。
3. **V33-251 预算披露**：edb56827 的“年初预算未逐项列示”已作为具体问题交付，
   受影响预算加总关系仍保持 `insufficient_data`（未把组级预算分摊到各科目）。
   是否要求原文补充逐项预算，属于业务裁定。
4. **历史 v2 结果**：读取时由 `build_review_report` 投影为 v3；原始 status.json 不改写。
   旧的 `unfinished_check` 记录在投影后进入 `diagnostics`，不会继续生成业务卡片。
5. **存量重分析**：未执行。需按冻结槽位清单、备份、串行重跑、对账后另行验收。
6. **未建立全量分类科目标准库**：b4e3ba95 的编码/名称错配依据规则内经济分类对照，
   未在本轮扩大为未授权的全量科目库。

## 9. 发布、备份与回滚步骤（已准备，未执行）

### 9.1 发布前备份

```bash
# 1) 数据库、上传目录、审计日志与版本指针（backup_all.py 必须有 create 子命令）
.venv/bin/python scripts/backup_all.py create --output backups/20261117_pre_remediation
# 2) 记录当前服务 PID、启动时间与前端构建哈希
ps -o pid,lstart,command -p $(lsof -tiTCP:18743 -sTCP:LISTEN)
shasum -a 256 api/main.py app/.next/BUILD_ID 2>/dev/null || true
# 3) 只读预览全部当前槽位，并写出冻结选集
.venv/bin/python scripts/reanalyze_current_slots.py \
  --all-current-slots --preview \
  --output backups/20261117_slots_preview.json \
  --freeze-out backups/20261117_slots_frozen.json
```

预览逐槽位输出：slot_id、当前文档版本、原件哈希、任务/revision、AI 模式、
当前状态、人工记录数、结果指纹、处置与原因；不会调用重分析或写 status.json。
冻结选集只包含 `ready` / `ready_recover_active`，每项绑定精确版本、文件哈希、
revision 与结果指纹。

### 9.2 发布

1. Codex 复核通过后，将 `codex/recognition-review-remediation` 合入本地运行分支
   （不合并 `main`、不推送远程）。
2. 重启前端：结束 :3000 的 `next dev`，重新运行 `npm --prefix app run dev`；
   后端 18743 使用 `api.main:app --reload --reload-dir api --reload-dir src` 继续热加载。
3. 用代表材料做冒烟：95c937cc、05e40add、d835cf2a、0ed5318c、edb56827、34f7645b。
4. 按冻结清单串行重分析存量（默认并发 1、AI 模式 inherit、断点续跑），生成新 revision；
   不覆盖旧结论、不手工改计数：

```bash
.venv/bin/python scripts/reanalyze_current_slots.py \
  --freeze-list backups/20261117_slots_frozen.json \
  --checkpoint backups/20261117_reanalysis.jsonl \
  --ai-policy inherit --concurrency 1 \
  --output backups/20261117_reanalysis_report.json
```

5. 冻结项缺失、文档版本/文件哈希/revision 变化会逐项留在 `results`（不会消失）；
   已完成项在 checkpoint 中跳过，未完成原因、状态与 review_reasons 均保留。

### 9.3 回滚

1. 停止新批次与前端，切回备份的旧代码/旧前端构建；
2. 保留新旧 analysis_versions 与人工记录，不删除新结果；
3. 如需回退当前版本指针，先做版本条件校验；已产生新人工决定时禁止整库还原
   （整库恢复仅用于停止业务写入且已保存新增记录的灾难恢复）；
4. 复核回滚后列表、详情、导出的计数一致性。

## 9A. 2026-11-17 独立复核 RR-01～RR-05 返工

独立复核对 `0eab8029` 提出 3 类 P1 与 2 类 P2。本轮逐项返工，反例保留为正式回归。

### RR-01 共享分项：科目绑定 + 组级算术

- 去掉“`len(per_item) >= len(subject_clauses)` 即放行”的数量兜底。
- 每个 `（项）` 科目必须由自己的声明或同名/同组候选分项绑定；无绑定科目
  保留 `group_component_unbound` 缺口，不再报“已披露”。
- 新增组级回执 `V33-251:{budget|final}:group:{item_start}`：
  - 组内分项之和与组级合计一致 → `pass`；
  - 不一致 → `fail`，组级合计标 `group_total_conflict`，不再作为章节加总数；
  - 科目无法绑定 → `insufficient_data`。
- 组内分项仍不重复计入章节加总；组级合计只有在可核对时才进入章节加总。
- 反例验证：985.78→1.00 时组回执 `fail`、章节决算回执 `insufficient_data`；
  换成“其他无关项目”时组回执 `insufficient_data`、章节回执 `insufficient_data`；
  正例仍为组 `pass`、章节 `pass`。

### RR-02 根问题级联：来源与独立原因约束

- 名称级联禁止“只有一个候选”跨页兜底；必须与义务的来源页相交。
- V33-251 级联同时要求 stage、checklist item 与来源页/科目范围一致。
- 新增 `_guarded_roots`：义务含执行/映射/行列读取等独立原因，或来源页不在
  根问题范围内时，整条义务保留在未完成清单，不被根问题移除。
- 结构化诊断根仍可用于“表格读取连带”计算，但不会因此成为业务问题。
- 反例验证：第 2 页名称问题不再归并第 40/41 页独立义务；第 5 页 V33-251
  预算问题不再归并第 40/41 页另一科目/另一表失败；未归并清单保持 1。

### RR-03 页面与后台门禁统一

- `collect_review_problems` 使用与 ReviewReport v3 相同的正式问题条件：
  `evidence_status` 已核实、`evidence_verification.status=verified`、
  `source_identity_complete=true`；缺元数据的旧 finding 只作候选。
- 结构化识别项退出业务问题集合；`_find_structured_review_item` 仍直接扫描
  原始 `review_items`，历史人工决定可读回、可投影，但不再阻塞门禁。
- 前端 `toUiProblems` 的 legacy 分支同样不再合成结构化业务卡片；
  `getDisplayIssueTotal` 不再把 `review_item_count` 加入业务问题总数。
- 反例验证：结构化技术项报告 issues=0、manual_review=0、diagnostics=1，
  后台 `pending_findings` 也为 0；旧未确证候选在页面和后台都不计正式问题。

### RR-04 review_summary 待处理数

- `pending_formal_issue_total` 复用正式问题可见集合与人工决定状态：
  - `confirmed` / `no_issue` / `in_package` 减待处理；
  - `needs_review`、缺决定、未知状态均计入；
  - `ignored_issue_ids` 中的问题不计入正式总数和待处理数。
- 反例验证：needs_review → pending=1；ignored → formal=0、pending=0。

### RR-05 源头诊断、有界恢复与发布工具

- `diagnostic_fields.rule_level_entry`：`未提取到/未识别到/未找到/未定位/
  无法绑定` 不再伪造 `text_statement_absent` 和说明章节缺数位置，改为
  `table_mapping_incomplete` 上游原因；只有明确“未披露/未写明/未明示”
  才保留说明缺失语义。
- 新增 `src/services/bounded_recovery.py` 并接入 `engine_rule_runner`：
  - 只对取数/绑定类原因尝试恢复，材料缺陷/执行异常不重试；
  - 策略指纹 + 原件哈希 + parse_run_uid + 规则/关系去重，同一路径只一次；
  - 每次最多 2 个策略；恢复后重新校验来源身份，失败保留原回执；
  - 记录策略、耗时、前后原因、是否改变、来源是否复核；
  - 默认接入既有“空表影子表”原生恢复策略。
- `reanalyze_current_slots.py`：新增只读预览 `--preview` / `--dry-run`、
  `--freeze-out` 冻结选集、`--freeze-list` 精确版本校验、`--checkpoint`
  断点续跑、`--output` 报告；默认并发改为 1、AI 策略改为 inherit。
- 交付文档命令已改为 `backup_all.py create --output ...` 和实际存在的
  预览/冻结/续跑参数；预览已在本机只读验证（157 槽位，156 ready，
  1 missing_job_dir），未重分析、未写 status.json。

### 本轮新增回归

- `tests/test_independent_review_rr01_rr04.py`（7 项）
- `tests/test_reanalyze_preview_tool_20261117.py`（4 项）
- `tests/test_bounded_recovery_20261117.py`（5 项）
- 独立复核反例脚本已重新执行：shared_groups 三例、cascade 两例、
  hidden_gate、review_counts 全部符合上述预期。

## 10. 交付物

- 代码分支：`codex/recognition-review-remediation`
- 新增契约模块：`src/schemas/result_semantics.py`
- 回放工具：`scripts/replay_recognition_remediation_20261116.py`
- 回放证据：`outputs/recognition_remediation_20261116/playback_before.json`、
  `outputs/recognition_remediation_20261116/playback_after.json`
- 测试：`tests/test_result_semantics_20261116.py`、
  `tests/test_recognition_review_remediation_20261116.py`
- 本报告：`docs/REMEDIATION_RECOGNITION_REVIEW_20261116.md`
