# AI 检查链路第二轮收口验收（2026-10-25）

## 0. 结论摘要

| 层级 | 结论 | 证据 |
| --- | --- | --- |
| 环境隔离 | 完成。验收在仓库外独立 worktree、独立数据库、独立上传目录运行；主工作树与 18743 日常实例未改动 | §1 |
| 专项×窗口补试 | 完成。审计调用关闭 provider 内层重试，由专项×窗口统一补试 1 次；S1 真实 524 后补试成功，S2 连续 524 后如实保留 3/4 | §2、§4 |
| 检查范围展示 | 完成。任务页、复核台、材料详情均直接显示有效完成/失败/补试/未覆盖页；纯规则任务写明“文字语义未执行”；历史缺覆盖写“未记录” | §3、§5 |
| 解释字段与导出 | 完成。定位并修复复核报告 AI 候选丢失解释字段的适配器缺陷；真实 S1 AI 候选页面显示当前值/应为/“模型声称”；实际 CSV 含 7 列且值正确 | §3.3、§5 |
| 真实样本 | S1 4/4（补试1）、S2 3/4（失败1，页1-23）、S3 4/4；纯规则任务 18 条规则结果、AI 未执行 | §4 |
| 回归 | 全量 4068 passed / 11 failed / 189 skipped；11 项为同一批既有 API/组织数据失败 | §6 |
| 生产切换 | **未执行**。未修改生产数据库、未覆盖历史分析、未重启 18743 | §8 |

## 1. 版本、隔离与可复现边界

### 1.1 唯一版本

- 主仓库 HEAD：`57abcf86`（`codex/zcode-remediation`）。
- 验收分支：`codex/zcode-remediation-r3`，起点 `57abcf86`，checkpoint `6bed8137`（把上一轮 3 个未提交文件固化到验收分支，不改主工作树）。
- 最终改动：31 个文件（含本报告）、+2178/−59；完整补丁：
  `scratch/ai_remediation_20261025/acceptance3/final_changes.patch`
- 主工作树当前仍只有上一轮 3 个未提交文件 + 原有未跟踪文档；本轮没有写入主工作树源码。
  `git apply --check` 已对主工作树验证通过。

### 1.2 隔离实例

| 对象 | 主实例 | 验收实例 |
| --- | --- | --- |
| 源码 | `/Volumes/workSSD/software development/GovBudgetChecker` | `/Users/zhangxingjin/gbc-acceptance-r3`（仓库外 worktree） |
| 后端 | `127.0.0.1:18743`，PID 96969，`--reload` | `127.0.0.1:18745`，无 reload |
| 前端 | 日常 3000（当前未启动） | `127.0.0.1:3011`，`BACKEND_URL=18745` |
| 数据库 | `fiscal_db`（验收前后均为 285 个 analysis_jobs） | `gbc_acceptance_20261025_r3`（验收后 5 个 jobs） |
| 上传目录 | 主仓库 `uploads/` | `~/gbc-acceptance-r3/acceptance_runtime/uploads/` |
| 组织/用户数据 | 主 `data/` | `acceptance_runtime/data/`（复制后独立写入） |

- 后端进程 cwd 实测为验收 worktree；`pg_stat_activity` 同时出现 `fiscal_db` 与 `gbc_acceptance_20261025_r3`，两条连接互不混用。
- **需知悉的副作用**：主实例 18743 的 reloader 监听整个主仓库；本轮在 `scratch/` 下落盘验收
  `.py` 脚本时，其子进程在 19:55 发生了一次自动 reload。主工作树源码哈希未变，加载的仍是
  `57abcf86` + 原有 3 个未提交改动，**没有本轮新代码进入主实例**；没有手动停止或重启主实例。
  后续验收脚本与证据均写在 worktree/证据目录，不再向主工作树写 `.py`。
- 验收实例的 `.env` 显式覆盖 `DATABASE_URL/UPLOAD_DIR/ORG_DATA_DIR/USER_DATA_DIR/AI_MODEL_SETTINGS_PATH`，不依赖主实例进程内的 reload。
- 版本证据不只依赖启动时刻：提示词版本实测为
  `ai_audit_text_semantics@sha1:b2f0b751`、`ai_audit_narrative_consistency@sha1:84a9b44b`；
  trace 与 `ai_audit_diagnostics.prompt_versions` 均一致。

### 1.3 运行版本与最终源码的差异（如实说明）

真实 3 份样本由 19:40 启动的验收后端执行；该进程已加载本轮补试、覆盖计数、失败诊断与 trace 代码（S1/S2 的运行结果可证）。运行结束后又做了以下**不改变已跑结果**的收口：

- `result.meta.conclusion_scope` 同步写入（运行后只影响新任务元数据；旧任务由页面顶层/`quality_gate` 回退读取）；
- 材料详情 `ai_scope_meta` 的 `quality_gate.conclusion_scope` 回退（读取层）；
- 复核报告 AI 候选解释字段适配器修复（前端；最终页面证据已用修复后前端重拍）；
- 失败文案/HTTP 524 展示、`AI_AUDIT_UNIT_RETRIES` 环境变量声明。

规则任务 `b9734410d63a3b6a72db3f80bdacd275` 在最终后端上重跑，`result.meta.conclusion_scope` 已实测为 `findings_within_covered_checks`。

## 2. 专项×窗口级失败恢复

### 2.1 重试层级

- `AIClient` 原本已有 provider 内层重试（默认 1 次）；本轮没有在外层再叠一层。
- 审计调用通过 `max_retries_override=0` 关闭内层重试，由 `_run_direct_specialty_with_retry` 按“专项×窗口”单元统一补试。
- 默认 `AI_AUDIT_UNIT_RETRIES=1`（上限 2，0 可关闭），补试等待 `AI_AUDIT_UNIT_RETRY_DELAY=1.5`。
- 只对可恢复错误补试：`server_error/timeout/network_error/rate_limit`；认证、模型不存在、参数错误、配额超限不补试；非法/截断响应不自动补试（避免把格式问题伪装成上游抖动）。
- 成功单元不重跑；补试只针对失败专项。失败单元只有一条最终候选结果，不重复入库。
- 覆盖分母仍是“窗口数×专项数”；补试次数单独统计到 `retry_attempts`/`attempted`，不增加 `planned_calls`。

### 2.2 终失败保留什么

- 保留其他专项/窗口的有效结果。
- `coverage.failed_units` 记录专项、窗口号、物理页码数组、状态、`error_type`、`http_status`、`attempts`、`retry_count`。
- `ai_window_errors` 记录窗口起止偏移、页码、逐次尝试摘要与 `retry_history`。
- 私有 `ai_audit_trace.jsonl` 记录每次尝试的 prompt、原始响应；上游失败保存**限长 600 字、脱敏**的响应体片段（S1/S2 实测为 `524: A timeout occurred` HTML），公开 API 只保留错误类型/状态/长度/哈希，不带原文。

### 2.3 故障注入测试

`tests/test_ai_chain_remediation.py` 新增 6 条：

- 首次 502、补试成功：覆盖 1 个单元、尝试 2 次、不重复候选；
- 连续 502：停在 2 次尝试，保留失败诊断与 `retry_history`；
- 认证失败：0 补试；
- 两个专项中仅一个失败：只补试失败专项，成功专项不重跑；
- 覆盖计数按单元不按尝试：4 个计划单元、3 个成功、1 个失败、1 个重试；
- 降级门禁文案保留有效结果。

`tests/test_ai_client_config.py` 新增 1 条：审计调用 `max_retries_override=0` 时 provider 只被调用 1 次，证明不叠加重试。

## 3. 用户可见的检查范围与解释字段

### 3.1 统一组件

新增 `app/lib/aiScope.ts`（纯函数）与 `app/app/components/AiScopeBanner.tsx`：

- 明确四种状态：未执行 / 完整完成 / 部分完成 / 失败；
- 统一计数：`有效完成 succeeded/planned，失败 N，另有 M 个单元重试`；
- 列出未完成专项、物理页码、错误类型/HTTP 状态、尝试次数；
- 明确结论作用域（`结论仅适用于本次已完成检查` 等）；
- 历史缺 `ai_coverage` 显示“覆盖信息未记录”，不默认完成；
- 纯规则任务显示“本次未执行”“文字语义检查未执行”。

### 3.2 接入位置

- 复核台：`IssuesTab` 顶部常显，不再需要点两次“来源与分类说明”；
  未完成时提供“前往任务页补检”入口，不会在复核台误触一次昂贵的重新分析；
- 任务页：标题栏下常显，并提供“使用大模型重新分析”补检入口；
- 材料台账 → 材料详情 → 检查覆盖：后端 `AnalysisBlock.ai_scope_meta` 暴露受限子集，页面常显；
- `api/main.py` 将 `conclusion_scope` 同时写入 `result.meta`；旧任务从顶层/`quality_gate` 回退。

### 3.3 解释字段闭环（根因与修复）

真实 S1 页面复现并定位到最后一个缺陷：

- 现象：规则问题 V33-222 的解释块可见；复核报告里的 AI 跨段候选（AI-XSEC-005）只有正文，没有“当前值/应为/差额”，也没有“模型声称”提示。
- 根因：`toUiProblems` 在 `collectReviewReportManualIssues` 中重建 AI 候选对象时，漏拷
  `expected/actual/difference/check/table_or_section/amounts_provenance/calculation_basis`。
  后端字段存在，前端适配器把它们丢了。
- 修复：`app/lib/uiAdapters.ts` 显式转交上述字段。
- 验证：真实 S1 AI-XSEC-005 页面现在显示
  “当前值…；应为…；差额：未提供”“检查项…”“表/章节…”“数值来源：模型声称，尚未经程序核实”，
  并显示第 13/20 页两条跨段证据。
- 规则侧 V33-222 仍显示“程序按已定位事实计算（单位：万元，精度：2，规则版本：v3_3）”，与模型声称分开。

## 4. 三份真实样本与负例

### 4.1 样本1（S1）：上海市文化和旅游局执法总队 2022 年度部门决算

- PDF：26 页；SHA-256 `13837d41502033fa9f23ee255bab47fa808500ebdf949270202b35f51553bf32`。
- 任务：`07818e0da889550c3b09db2eb0c96a18`；终态 `review_required`，质量门 `review_required`。
- **页码口径纠正**：V33-222 的同口径证据是第 13 页
  `FIN_05_general_public_expenditure` 表（2080505 / total_actual = 165.80 万元）
  与第 20 页说明（年初预算 165.80、支出决算 141.12）；第 9 页是“支出决算表（全口径）”，只作旁证，不与第 13 页混用。
- V33-222：`expected=165.80`、`actual=141.12`、`difference=-24.68`、
  `amounts_provenance=program_calculated`，`calculation_basis.verified=true`，
  `location.pages=[13,20]`，两条 table_refs 均 verified。
- AI：`state=succeeded`，`coverage=complete`，`planned=4`、`succeeded=4`、`failed=0`；
  **`retried_calls=1`、`attempts=5`**：narrative 窗口1 首次上游 524，补试后成功；
  页面显示“已完整完成（4/4）；其中 1 个单元首次失败后补试成功”。
- AI 候选 7 条、规则 18 条。AI-XSEC-005 给出同科目 165.80/141.12 两个数值（模型声称，未核实），
  并保留第 13/20 页双方证据；程序结论仍以 V33-222 为准。
- CSV：V33-222 行 `expected=165.80`、`actual=141.12`、`difference=-24.68`、
  `check=final_disclosure_amount`、`table_or_section=五、一般公共预算财政拨款支出决算情况说明`、
  `amounts_provenance=program_calculated`、`calculation_basis` 为中文万元/两位/v3_3。
- 页面证据：`S1_review.png`、`S1_explanation.png`（规则）、`S1_ai_pairs.png`（AI 双方证据）。

### 4.2 样本2（S2）：上海虹桥国际中央商务区管理委员会 2024 年度部门决算

- PDF：28 页；SHA-256 `5b7ee63eb35ea14f69db357adcc1a7e2a1e8c0eebfcab30fc6060824bb89b14b`。
- 任务：`3508b5e02aebe9be6e15f1248a3ac289`；终态 `review_required`，AI `degraded`。
- 覆盖：`planned=4`、`succeeded=3`、`failed=1`、`retried_calls=1`、`attempts=5`。
  失败单元：`text_semantics`、窗口0、第 1-23 页、`server_error`、HTTP 524、尝试 2 次；
  补试仍失败，系统没有把它算成“已处理 4/4”，而是“有效完成 3/4，失败 1”。
- 保留有效结果：19 条 AI 候选、10 条规则发现；义务详情为
  “部分检查未完成，已保留 22 条有效候选；未完成的专项与页码范围见检查覆盖”。
- 目标完整句：第 21 页“管委会行政运行费用按实际情况支出。”在 AI-DESC-004 原文证据中完整出现；
  页面无“残句”标记，未因切窗被误报。
- 页面：`S2_review_final.png`、`S2_target_1.png`、`S2_task.png`、`S2_material_coverage.png`。
- CSV：目标候选在 `manual_review` 行，页码 21，`check=科目增减原因说明`，引文完整。

### 4.3 样本3（S3）：上海市文化和旅游局 2022 年度部门决算

- PDF：38 页；SHA-256 `a9ddd6ec08df3e7781dcd536fe953839a0250203ff933207eba94ec3dc710975`。
- 任务：`5cf642d3273ce70272c994923a2960f2`；终态 `review_required`，AI `succeeded`、覆盖 `complete`，4/4，无补试。
- AI 16 条、规则 18 条。
- 第 29 页目标：`AI-TEXT-004` 错别字“决算市全口径”；
  `AI-TEXT-005` 多字“其他广播影电视管理的支出”；
  页面定位到第 29 页，分类/引文正确，候选各一条（去重后）。
- CSV：两条均在 `manual_review/ai_candidate` 行，页码 29，`check=错别字/多字`，引文完整。
- 页面：`S3_review.png`、`S3_target_1.png`、`S3_target_2.png`、`S3_task.png`。
- 现有复核生命周期把该任务显示为“当前查看的是历史分析，仅供查看”；这是既有复核上下文判定，
  不影响本次检查范围条、候选阅读与导出，本轮未改该链路。

### 4.4 纯规则负例

- 任务：`b9734410d63a3b6a72db3f80bdacd275`（同一 S3 PDF，`use_ai_assist=false`）。
- 结果：`done`；AI `requested=false/not_requested`、`ai_coverage=null`；规则 18 条。
- 页面与任务页均显示：“大模型文字复核：本次未执行”“文字语义检查未执行”，没有显示“覆盖完整”。
- 页面：`NEG_rule_review.png`、`NEG_rule_task.png`。

## 5. 页面与 CSV 验收证据

- 页面截图目录：
  `scratch/ai_remediation_20261025/acceptance3/`
  （S1/S2/S3 的 review/task/target/explanation、S2 material coverage、NEG rule、e2e）。
- 页面 DOM 文本证据：`page_evidence.json`、`target_page_evidence.json`、`S1_ai_pairs_text.json`、`NEG_rule_page.json`、`S2_material_page.json`。
- 实际下载 CSV：`S1_report.csv`、`S2_report.csv`、`S3_report.csv`、`NEG_rule_report.csv`。
  - 表头包含 `expected/actual/difference/check/table_or_section/amounts_provenance/calculation_basis` 与中文列；
  - S1 V33-222 行逐值核对通过；
  - 摘要行 `conclusion_scope=incomplete_scope`；
  - 历史/空字段导出为空字符串，不补 0。
- 真实浏览器 e2e：`e2e/tests/ai-scope-explanation.spec.ts` 4 条全过：
  部分失败 3/4、纯规则未执行、复核报告 AI 候选解释字段+模型声称+双方证据、历史缺覆盖“未记录”。

## 6. 测试结果与既有失败

### 6.1 本轮定向

- 后端定向（AI 链路 + 报告导出 + 材料详情 + compose + 回归）：
  **373 passed**（命令见 §7）。
- 全量：**4068 passed / 11 failed / 189 skipped**。
- 前端：`tsc --noEmit` 通过；`npm run test:unit` 42 个脚本通过；
  新增 `aiScope.test.ts`、`reviewExplanation.test.ts` 通过。
- 真实浏览器 e2e：4 passed。

### 6.2 既有失败（与基线同名同因，非本轮回归）

1. `tests/test_api_contract.py::test_document_upload_rejects_duplicate_same_org_scope`
2. `tests/test_api_contract.py::test_organization_association_flow`
3. `tests/test_api_contract.py::test_department_stats_prefers_merged_issue_total`
4. `tests/test_api_contract.py::test_departments_list_and_tree_use_aggregated_merged_issue_total`
5. `tests/test_api_contract.py::test_organization_jobs_filters_stale_links_without_mutating_links`
6. `tests/test_api_contract.py::test_department_unit_endpoints_with_legacy_endpoints_compatible`
7. `tests/test_api_contract.py::test_organization_jobs_scope_controls_child_inclusion`
8. `tests/test_api_contract.py::test_department_stats_only_count_direct_jobs`
9. `tests/test_user_auth_api.py::test_admin_delete_preview_reports_impacted_scope`
10. `tests/test_user_auth_api.py::test_admin_sensitive_action_writes_audit_log`
11. `tests/test_write_endpoint_auth.py::TestWriteEndpointsWithValidSession::test_regular_user_can_associate`

原因：共享开发库累积的测试组织（`重复检测单位-*`、`测试单位-*`、`stale-job-org-*`）不满足
“unit must be under a department”目录校验。本轮未清理数据库。
本轮曾新增 `test_compose_deployment.py::test_env_vars_are_consistent...` 失败，已通过把
`AI_AUDIT_UNIT_RETRIES/AI_AUDIT_UNIT_RETRY_DELAY` 写入 `.env.example` 与 `docker-compose.ai.yml` 修复；
复跑后该测试 10/10 通过，全量回到 11 项既有失败。

> 计数差异说明：本轮全量在测试隔离环境中运行（`GOVBUDGET_TEST_DATABASE_URL` 未配置，PG 用例跳过），
> 因此 skipped 从基线 176 增至 189，而新增 10 条后端测试后 passed 为 4068。

## 7. 复现命令

```bash
cd /Users/zhangxingjin/gbc-acceptance-r3
# 后端定向
/Volumes/workSSD/software\ development/GovBudgetChecker/.venv/bin/python -m pytest \
  tests/test_ai_chain_remediation.py tests/test_ai_extractor_client.py \
  tests/test_check_obligations.py tests/test_ai_client_config.py \
  tests/test_report_exports.py tests/test_material_ledger_detail_api.py \
  tests/test_material_ledger_detail_query_service.py tests/test_material_ledger_detail_closure.py \
  tests/test_compose_deployment.py tests/test_audit_quality_p0.py \
  tests/test_reanalysis_failure_regressions.py -q

# 前端
cd app && npx tsc --noEmit && npm run test:unit

# e2e（验收前端 3011）
cd .. && E2E_BASE_URL=http://127.0.0.1:3011 E2E_EXTERNAL_SERVER=1 E2E_CHANNEL=chrome \
  app/node_modules/.bin/playwright test --config e2e/playwright.config.ts \
  e2e/tests/ai-scope-explanation.spec.ts
```

## 8. 未验收分支、剩余风险与切换

### 8.1 未验收/未覆盖

- **辅助定位真实场景未验收**：S1/S2/S3 的 `ai_locator_outcomes` 均为 `eligible=0, attempted=0`；
  三份样本没有“有原文但无页码”的规则 finding。单元测试通过，但不得称为真实验收。
- S2 的 `text_semantics` 连续 524，补试后仍失败；本次只能声明 3/4，不声明完整审核通过。
- 纯文本 AI 不覆盖彩色字体/横排等视觉能力；结构/缺表缺章仍由规则/完整文档结构负责。
- D-03 一般多字漏字仍是 AI 辅助、结果待人工复核，不是规则全面覆盖。
- 未运行 `next build`；`next dev`、`tsc`、组件测试与 e2e 通过。既有 `/supervision/remediation`
  路由的构建问题本轮未处理。
- 未清理旧提示词、mock、兼容分支（按本轮要求）。
- 真实样本的 AI 调用使用的是 19:40 验收构建；最终源码只多了 §1.3 列出的元数据/UI/部署声明收口。

### 8.2 正式切换前置（需另行授权）

1. 选择窗口，停止 18743 及其 reload 子进程（仅这两个 PID；不要碰其他实例）。
2. 备份：`pg_dump fiscal_db`、`uploads/`、`data/`、`.env`；记录当前 HEAD 与 `git status`。
3. 在主工作树应用 `final_changes.patch`（已 `git apply --check` 通过），不要 reset 既有 3 个未提交文件。
4. 复跑 §7 定向测试、前端单测与 e2e。
5. 按原启动约定重启 18743；重启后先用一份材料做真实调用，核对：
   - 覆盖条显示“有效完成 X/Y、失败 N、重试 M”；
   - 规则问题解释块显示“程序计算”；AI 候选显示“模型声称”；
   - CSV 7 列与结论作用域；
   - 失败时 `failed_units` 页码准确。
6. 回滚：恢复备份的 `uploads/`/`data/`/`.env`，`git checkout` 回切换前状态或恢复补丁反向应用；
   无数据库 schema 迁移；新字段为可选 JSON 字段，旧代码读取时忽略。
7. 验收隔离资源可在复核完成后停止 18745/3011，并按需删除
   `gbc_acceptance_20261025_r3` 与 `~/gbc-acceptance-r3`（删除前先保留证据）。

## 9. 关键文件

- 重试/诊断：`src/engine/ai/extractor_client.py`、`src/services/ai_client.py`、`src/providers/base.py`、`src/providers/openai_compat.py`
- 覆盖/状态：`src/services/ai_findings.py`、`src/services/ai_execution.py`、`src/engine/check_obligations.py`
- 材料详情：`src/services/material_detail_query_service.py`、`src/schemas/material_detail.py`、`api/main.py`
- 前端：`app/lib/aiScope.ts`、`app/app/components/AiScopeBanner.tsx`、`app/app/components/review-workbench/IssuesTab.tsx`、`app/app/task/[job_id]/page.tsx`、`app/app/components/materials/MaterialDetailCoverageTab.tsx`、`app/lib/uiAdapters.ts`
- 测试：`tests/test_ai_chain_remediation.py`、`tests/test_ai_client_config.py`、`tests/test_report_exports.py`、`tests/test_material_ledger_detail_query_service.py`、`app/tests/aiScope.test.ts`、`app/tests/reviewExplanation.test.ts`、`e2e/tests/ai-scope-explanation.spec.ts`
- 部署声明：`.env.example`、`docker-compose.ai.yml`
