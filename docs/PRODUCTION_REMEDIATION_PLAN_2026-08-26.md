# GovBudgetChecker 生产就绪整改实施计划（M1–M4）

- 编制日期：2026-08-26
- 关联审查报告：`docs/PRODUCTION_GAP_AUDIT_2026-08-26.md`
- 审查分支：`codex/ux-workflow-data-retention`（以工作区当前代码为准）
- 复评结论：NO-GO；本计划系统性关闭全部 P0/P1/P2 缺口，直至发布验收。

## 用户已确认的关键决策
- **范围**：全量 M1–M4（直到发布验收）。
- **OCR（B-01）**：本轮仅做"扫描页/低文本页检测 + 转 `review_required`"，不做自动 OCR。
- **Golden Corpus（B-04）**：本轮不做人工标注语料，只做代码侧修复；质量度量搭框架、指标口径以结构性指标为主，召回率/精确率类度量另行排期。

## 问题陈述
核心业务可信度红线未跨过：无 OCR/扫描检测导致静默漏检；DB 层年份兜底 2000；`done` 状态无法区分"无问题/分析不完整"；无质量度量框架；观测性缺失。

## Background（基于代码核实）
- 状态写入集中在 `api/main.py:_run_pipeline_inner`，最终态判定在 `api/main.py:751`（degraded/done/error）。
- 年份双路径：`api/runtime.py` `parse_report_year`→`None`（已修）；`src/services/structured_ingest_runner.py:702` `_parse_year`→`2000`（未修）。
- 规则选择：`src/services/engine_rule_runner.py:180` `_select_rule_set`，unknown 只跑 `ALL_COMMON_RULES`。
- report_id：唯一约束 `src/db/migrations.py:677` + `src/services/ps_schema_sync.py:552` `_upsert_report` `ON CONFLICT`。
- 前端状态归一：`app/lib/uiAdapters.ts:190` `normalizeUiTaskStatus`（`degraded`→`completed`，无 `review_required` 分支）；`Task["status"]` 仅 3 值；`getReportLabel`(:295 unknown→预算) 与 `resolveReportLabel`(:305 unknown→待复核) 并存。
- 结构化日志：`src/utils/logging_config.py` 存在但 `setup_logging` 无调用点（死代码）。
- 队列：`api/job_queue.py` 文件 claim 锁 + heartbeat + 重启 resume。
- `/ready`（`api/routes/health.py`）已深度检查；RBAC（`api/auth_utils.py`）已完善；弱口令黑名单 `src/services/user_store.py:28`。

## 约束与原则
- 每个 Task 产出可运行、可演示的增量；先测试后接线；不做大爆炸式重构。
- 每步运行 `make lint typecheck unit`；涉及前端运行 `frontend-build`；e2e 在 M4 统一跑。
- 状态模型变更提供向后兼容（旧任务 `done` 仍可读）。
- DB 迁移提供前向修复/回滚说明；涉及 `DATABASE_URL` 的迁移在真实 PG 环境实测。

---

## M1 · 消除虚假成功与静默失败

准入：审查报告确认。准出：扫描件不再静默漏检（转复核）；无法识别年份不落 2000；`done` 仅在质量门禁通过时出现；前端展示真实四态。

### Task 1: 统一分析结论枚举与状态模型（基础，无行为变更）
- 内容：`src/schemas/issues.py` 新增 `AnalysisConclusion`（`findings_detected/no_findings/incomplete/analysis_error`）与任务态常量（含 `review_required`）；`api/runtime.py` 的 `JOB_STATUS_CONTEXT_FIELDS` 增补 `analysis_conclusion`、`quality_status`、`page_coverage`、`scanned_page_count`。
- 测试：枚举取值；状态归一兼容映射（旧 `done`/`degraded` 不破坏）。
- Demo：`pytest` 通过；新字段可序列化，无行为改动。

### Task 2: 扫描页/低文本页检测（B-01，不自动 OCR）
- 内容：`api/main.py` 抽取 `_assess_page_extraction(page_texts, page_tables)`，按文本密度/字符数阈值判定每页是否"疑似扫描/低质量"，计算 `page_coverage`、`scanned_page_count`、`low_text_pages`；阈值走环境变量（如 `SCANNED_PAGE_MIN_CHARS`）。
- 测试：全文本 PDF→coverage≈1；扫描空文本→标记低质量页；边界（空 PDF、单页）。
- Demo：对合成"空文本页"输入，返回结构含被标记页码。

### Task 3: 任务级质量门禁与 `review_required`（B-03 + P0-04/05 收尾）
- 内容：`_run_pipeline_inner` 结束前加 `_evaluate_quality_gate()`：当（page_coverage 低于阈值 / 存在扫描页 / `report_kind==unknown` / 年份无法识别 / AI 配置为必需却失败）→ `status=review_required` + `analysis_conclusion=incomplete`；AI 成功但 0 findings 且门禁通过→`no_findings`；`degraded` 保留为"部分能力降级但结论有效"。替换 `api/main.py:751` 二态逻辑为门禁驱动多态。
- 测试：各分支输入断言最终 status/conclusion；规则失败仍→error。
- Demo：扫描件样本→`review_required`；正常无问题件→`done`+`no_findings`。

### Task 4: 修复 DB 年份兜底 2000（B-02 / P0-03）
- 内容：`structured_ingest_runner._parse_year` 对齐 `runtime.parse_report_year`，无法识别返回 `None`；`_ensure_document_version`/`_upsert_report` 支持 `year IS NULL`。DB 迁移：`org_dept_annual_report.year` 允许 NULL，唯一约束改为对 NULL 安全（Postgres 需部分索引或 `COALESCE` 表达式索引，避免多 NULL 视为不冲突而重复——需实测取舍）；提供回滚说明。
- 测试：`_parse_year(None)=None`；未知年份不写 2000；同 org 未知年份多文档不误合并。
- Demo：实证脚本输出 `None`；迁移可正向执行。

### Task 5: report_id 残留碰撞收敛（P0-09）
- 内容：`_upsert_report` 前，当 `match_mode==fallback_name` 或 year 为 NULL 时，改为按 `checksum`/`storage_key` 维度建报告，而非强制并入 (dept,unit,year,type) 行；补充冲突审计日志。
- 测试：两份不同 checksum、同 fallback org、未知年份→两个不同 report_id。
- Demo：构造碰撞场景断言不再串行合并。

### Task 6: 前端状态贯通 review_required/degraded（P0-06 收尾 + P1-05）
- 内容：`app/lib/uiAdapters.ts` 的 `Task["status"]` 增加 `review_required`；`normalizeUiTaskStatus` 增加分支（`degraded`→completed 但带质量标记；`review_required`→独立态）；`getReportLabel`（unknown→预算）调用点全部切到 `resolveReportLabel`（unknown→待复核）。相关组件（`ResultCard`/`JobSidebar`/`PipelineStatus`）展示"需人工复核/部分降级"徽标与原因。
- 测试：`app/tests` 状态映射、unknown 标签。
- Demo：`frontend-build` 通过；review_required 任务在列表显示独立状态。

---

## M2 · 引擎可信度与版本留痕（不含 Golden Corpus）

准入：M1 完成。准出：结果可复现（版本留痕）；证据完整率可度量；有回放指标脚本（待后续接 Golden Corpus）。

### Task 7: 规则/模型版本随结果留痕（P2-02）
- 内容：findings 数据结构与 DB 结果表补 `rules_version`、`ai_model`、`prompt_version`、`recognizer_version`；`analyze_dual`/`engine_rule_runner`/`ai_findings` 写入实际使用值。
- 测试：结果 payload 含版本字段；DB 快照可读回。
- Demo：一次分析结果 JSON 含完整版本三元组。

### Task 8: 证据链完整性校验（P0-07）
- 内容：结果落库前对每条正式 finding 校验 `page_number` 与 `evidence_text/bbox` 非空；缺证据的 AI finding 降级为"待复核"而非正式问题；输出 `evidence_completeness` 到 meta。
- 测试：无证据 finding 被降级；完整证据保留。
- Demo：混合输入下 meta 显示完整率与降级计数。

### Task 9: 质量度量框架脚手架（B-04 框架部分，无标注数据）
- 内容：新增 `scripts/replay_analysis.py`，对 `UPLOAD_DIR` 现有任务批量回放并汇总（覆盖率/unknown 比例/空 findings 数/report_id 唯一性/证据完整率），产出 JSON 报告，不依赖人工标注。
- 测试：脚本对 fixture 目录产出正确统计。
- Demo：对现有 118 份产物回放，输出指标对比表。

---

## M3 · 运维保障

准入：M2 指标达标。准出：全链路 job_id 日志；关键指标+告警；首登改密+安全头；解析隔离；备份恢复演练记录。

### Task 10: 结构化日志接线（B-05 / P2-01）
- 内容：`api/main.py`、`api/worker.py` 启动处调用 `setup_logging(json_format=env)`；用 `LogContext`/`log_job_stage` 在各 pipeline 阶段埋点，关联 `job_id`；敏感原文不入日志。
- 测试：日志记录含 `job_id/stage` 字段。
- Demo：运行分析，日志为结构化 JSON 且可按 job_id 检索。

### Task 11: 关键指标与告警钩子（B-06）
- 内容：暴露阶段耗时、队列积压（复用 heartbeat 目录）、AI 失败率、unknown/review_required 比例、report_id 冲突计数（结构化日志聚合或 `/metrics`）；文档化告警阈值。
- 测试：指标采集函数返回结构正确。
- Demo：`/ready` 或 metrics 端点显示队列与阶段指标。

### Task 12: 安全加固（B-07 首登改密 + B-08 安全头）
- 内容：`src/services/user_store.py` 增 `must_change_password`，默认管理员首登强制改密；`api/main.py` 或反代注入 HSTS/CSP/X-Content-Type-Options/X-Frame-Options。
- 测试：首登未改密被拦；响应含安全头。
- Demo：首登流程与响应头验证。

### Task 13: PDF 解析资源隔离与备份演练（B-09 + B-12）
- 内容：解析迁移到受限 executor/子进程（超时+内存上限），恶意大 PDF 不打爆 worker；编写备份脚本（UPLOAD_DIR+PG+审计日志）并记录一次恢复演练。
- 测试：超时/超大页数被拒并转 error/review。
- Demo：超大 PDF 被限制；备份脚本产出可恢复归档。

---

## M4 · 发布验收

准入：M3 完成。准出：发布门禁全绿；历史数据完成回放/标记；灰度+回滚步骤可执行。

### Task 14: 统一部署与仓库清理（B-10 + B-11）
- 内容：合并/校正 `docker-compose.yml` 覆盖 backend+worker+frontend+postgres+ai-extractor 一键起；清理根目录临时产物并补 `.gitignore`（`pip_tmp_*`/`pytest_basetemp*`/`*.out`/`*.err`/`local.db` 等）。
- 验证：`docker compose up` 后 `/ready` 全绿（如环境允许）。
- Demo：单命令起全组件；仓库根目录洁净。

### Task 15: 历史回放、迁移与业务门禁并入 CI（P2-03/P2-06 收尾）
- 内容：对历史 118 份执行 `replay_analysis.py` 全量回放，产出整改前后对比；将回放结构性指标阈值作为 CI 业务门禁（覆盖率/unknown/report_id 唯一性达标才通过）；补齐 e2e 关键流程。
- 验证：CI 含 lint+type+unit+migrate+e2e+回放门禁全链。
- Demo：CI 全绿 + 回放指标达标；对照《完整整改计划》Gate 0–6 逐项核对。

---

## 缺口 → Task 映射（覆盖对照）

| 缺口/编号 | 关闭 Task |
|---|---|
| P0-01 / B-01 OCR 兜底（本轮=检测+转复核）| Task 2, 3 |
| P0-02/05 done 语义、空 findings / B-03 | Task 1, 3 |
| P0-03 / B-02 年份 2000 | Task 4 |
| P0-04 unknown 规则跳过 | Task 3 |
| P0-06 前端透明 / P1-05 unknown 标签 | Task 6 |
| P0-07 证据链 | Task 8 |
| P0-08 Golden Corpus | 本轮不做（框架 Task 9，标注另排）|
| P0-09 report_id 碰撞 | Task 5 |
| P2-01 / B-05 结构化日志 | Task 10 |
| B-06 指标与告警 | Task 11 |
| P2-02 版本留痕 | Task 7 |
| P2-03 迁移与重分析 | Task 15 |
| P2-04 性能基线 | Task 13（隔离）+ 复核 |
| P2-05 / B-09 解析隔离 | Task 13 |
| P2-06 / B-04 业务门禁/度量框架 | Task 9, 15 |
| B-07 首登改密 / B-08 安全头 | Task 12 |
| B-10 统一部署 / B-11 仓库清理 | Task 14 |
| B-12 备份演练 | Task 13 |

## 跨阶段验证策略
- 每个 Task 完成后：`make lint typecheck unit`（前端任务加 `frontend-build`）。
- M2/M4 涉及 DB 的任务在配置 `DATABASE_URL` 的环境实测迁移与回滚。
- e2e 在 M4 统一跑。

## 主要风险
- Postgres NULL 年份唯一约束语义（Task 4/5 需实测部分索引方案）。
- 解析子进程隔离在 Windows/容器行为差异（Task 13）。
- 无 Golden Corpus 下 CI 业务门禁只能用"结构性指标"而非召回率（Task 15 局限，须在文档中说明）。

## 说明
- 本计划不覆盖《完整整改计划》要求的"真实 PDF 召回率/精确率"类质量目标（依赖人工标注 Golden Corpus，本轮按用户决策不做）。在 Golden Corpus 建立前，发布门禁只能保证"无静默失败、无虚假成功、结构性指标达标"，不能证明业务召回率达标——这一局限须在发布决策时明确告知。
