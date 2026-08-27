# GovBudgetChecker 生产就绪缺口审查报告

- 审查日期：2026-08-26
- 审查分支：`codex/ux-workflow-data-retention`（含约 40 个未提交修改，审查以工作区当前代码为准）
- 审查方式：只读代码审查 + 静态论证 + 本地质量门禁实证（lint/typecheck/unit/frontend-build）
- 审查范围：既有整改计划落地核对、10 维度缺口补查、关键 P0 实证复现
- 声明：本报告每条结论附 `file:line` 或命令输出证据；证据不足者明确标注"待人工复核"，不做臆断。

---

## 1. 执行摘要

### 复评结论：**NO-GO（暂不具备正式生产审核发布条件）**

与 2026-07-31《完整整改计划》的 NO-GO 判定相比，工程化质量有**实质进步**：durable 队列、RBAC、上传流式化、前端代理错误透传、`/ready` 深度检查、report_id 唯一约束、上传去重与数据留存契约均已落地，本地全部质量门禁通过（ruff/mypy/pytest 390 项全绿，前端 build 成功）。

但整改计划的**核心业务可信度目标尚未达成**，NO-GO 的根本理由仍然成立：

1. **无任何真实 OCR 兜底**（P0-01 未落地）。扫描件页面文本为空即被静默跳过审核，`ocr_text` 是对 pdfplumber 文本的误导性命名。
2. **DB 持久化层仍把无法识别的年份落为 2000**（P0-03 仅在内存路径修复，DB 路径未修）。这直接复现历史"year=2000"症状，并放大 report_id 碰撞风险。
3. **`done` 状态仍不能区分"没有问题"与"分析未完整完成"**（P0-02/P0-05 部分落地）。缺少 `review_required` 状态；AI 返回空 findings 且无 error 时仍判 `done`。
4. **缺少真实 PDF Golden Corpus 与业务质量门禁**（P0-08、P2-06 未落地）。当前所有自动化测试基于合成数据，无法度量漏检率/召回率。
5. **观测性缺失**：结构化日志设施 `setup_logging` 从未被调用（死代码），无阶段级指标、无队列积压/慢查询监控、无告警通道。

### 前 5 个阻塞项（Top Blockers）

| # | 阻塞项 | 编号 | 证据 |
|---|---|---|---|
| 1 | 扫描 PDF 无 OCR 兜底，页面内容静默漏检 | P0-01 | `requirements.txt:7-8`（仅 PyMuPDF/pdfplumber）；`api/main.py:248` |
| 2 | 结构化入库层年份兜底仍为 2000，写入 PG `fiscal_year` | P0-03 | `src/services/structured_ingest_runner.py:702, :95`（实证 `_parse_year(None)=2000`）|
| 3 | `done` 语义未彻底区分无问题/分析不完整；无 `review_required` | P0-02/05 | `api/main.py:751`；`engine_rule_runner.py:180` |
| 4 | 无真实 PDF Golden Corpus 与业务质量门禁 | P0-08/P2-06 | 仓库无 golden_corpus_*.json；`tests/` 均为合成数据 |
| 5 | 无结构化日志/阶段指标/告警（观测性） | P2-01 | `src/utils/logging_config.py` 存在但无 `setup_logging` 调用点 |

---

## 2. 表 A · 既有计划核对表

### 2.1 《完整整改计划》P0（阻断发布）

| 编号 | 计划内容 | 状态 | 证据（file:line） | 备注 |
|---|---|---|---|---|
| P0-01 | 扫描 PDF 可靠 OCR 兜底 | **未落地** | `requirements.txt:7-8` 仅 `PyMuPDF/pdfplumber`，无 tesseract/paddle/easyocr；`api/main.py:248` `_extract_visible_text_from_page` 仅调用 `page.extract_text()`，无扫描页检测/OCR 触发；`config/app.yaml:34-37`、`rules/v3_3.yaml:223-225` 声明 `ocr.enabled/enable_ocr_if_scanned` 但无实现 | `ocr_text` 字段实为 pdfplumber 文本，命名误导 |
| P0-02 | `done` 只代表未抛异常 | **部分落地** | `api/main.py:751` `final_status = "degraded" if analysis_quality_status=="degraded" else "done"`；规则失败 `raise`→`error`（`main.py:648`）；AI 失败/fallback→`degraded`（`main.py:490`）| 已有 degraded 态，但无 `review_required`；空 findings 无 error 仍 `done` |
| P0-03 | 年份识别失败默认 2000 | **部分落地** | `api/runtime.py:625` `parse_report_year` 返回 `None`（实证 `parse_report_year(None)=None`），`main.py:325` 走此路径；**但** `src/services/structured_ingest_runner.py:702` `_parse_year` 仍 `return 2000`（实证 `_parse_year(None)=2000`），经 `:95→_upsert_report` 写入 PG `fiscal_year` | DB 层未修，症状可复现 |
| P0-04 | unknown 只跑通用规则 | **部分落地** | `src/services/engine_rule_runner.py:180` `_select_rule_set`：unknown 只返回 `ALL_COMMON_RULES`，不再误映射 budget（改进）| 但无 `review_required` 强制，unknown 仍产出 `done` 结论 |
| P0-05 | AI 失败返回空 findings | **部分落地** | `api/main.py:488-490` `ai_error or fallback`→`degraded`；`analyze_dual.py` 记录 `ai_error` | AI 成功但返回空 findings（非 error）时仍 `done`，无法与"真无问题"区分 |
| P0-06 | 前端隐藏接口异常 | **已落地** | `app/app/api/jobs/route.ts:31-38` 透传上游 status + 502；`app/app/api/jobs/[job_id]/status/route.ts:47-72` 返回真实 `upstream_status`；`viewer/gbc-ui-demo/page.tsx:163` `readErrorMessage` | 代理层不再伪装 200 空数据 |
| P0-07 | 结果无完整证据链 | **部分落地** | `src/schemas/issues.py` 含 `page/evidence/bbox` 字段；`engine_rule_runner.py:~245` `bbox_locator.locate(finding)` | 证据完整率无 Golden Corpus 度量，无法证明 100%（P0 目标）|
| P0-08 | 缺真实 PDF Golden Corpus | **未落地** | 仓库无 `golden_corpus_manifest/annotations.json`；`tests/` 全部合成数据 | 无法度量召回率/精确率/证据完整率 |
| P0-09 | report_id 可能重复 | **基本落地** | `src/db/migrations.py:677` `UNIQUE(department_id, unit_id, year, report_type)`；`ps_schema_sync.py:552` `_upsert_report` 用 `ON CONFLICT ... DO UPDATE RETURNING id`（UUID）| 残留风险：year=2000 兜底 + `match_mode=fallback_name`（`ps_schema_sync.py:~135`）时不同文档仍可能碰撞入同一行 |

### 2.2 《完整整改计划》P1（效率与可信度）

| 编号 | 计划内容 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| P1-01 | 上传/处理/复核流程割裂 | 部分落地 | 存在 `viewer/gbc-ui-demo/page.tsx`、`task/[job_id]/page.tsx`、`task-review/*` 组件 | 需前端 UAT 验证连贯性（待人工复核）|
| P1-02 | 缺处理阶段进度 | 部分落地 | `api/main.py` 多处 `_safe_write` 写 `stage/progress`；`components/PipelineStatus.tsx`、`task-review/PipelineDrawer.tsx` | 阶段为固定 progress 数字，非真实每页覆盖率 |
| P1-03 | 无法修正年份/类型/组织 | 部分落地 | `api/routes/jobs.py` 有 associate/reanalyze；`components/AssociateDialog.tsx`、`BatchRematchDialog.tsx` | 元数据人工修正入口存在 |
| P1-04 | 详情固定 30 条 | **已落地** | `api/routes/jobs.py:38-70` `list_jobs` 支持 `offset/limit` 分页；`organizations.py:579-627` 同 | 后端已服务端分页 |
| P1-05 | unknown 被适配为预算 | **部分落地** | `app/lib/uiAdapters.ts:305` `resolveReportLabel` unknown→"待复核"（已修）；**但** `:295-301` 遗留 `getReportLabel` unknown→"预算" | 需确认调用点是否已全部切到 resolve 版（待人工复核）|
| P1-06 | 缺批量重试/复核 | 已落地 | `api/routes/jobs.py` reanalyze-all；`components/ReanalyzeProgressDialog.tsx`、`BatchUploadModal.tsx` | |
| P1-07 | 缺搜索/筛选/排序 | 部分落地 | `viewer/gbc-ui-demo/page.tsx` `filterOrganizations`/`countVisibleOrganizations` | 需前端功能核实（待人工复核）|
| P1-08 | 错误信息缺建议 | 部分落地 | `viewer/gbc-ui-demo/page.tsx:166` 限流提示含建议 | 覆盖面有限 |

### 2.3 《完整整改计划》P2（长期维护与运营）

| 编号 | 计划内容 | 状态 | 证据 | 备注 |
|---|---|---|---|---|
| P2-01 | 缺阶段级指标与监控 | **未落地** | `src/utils/logging_config.py` 有 `StructuredFormatter/log_job_stage/LogContext`，但全仓无 `setup_logging(...)` 调用点 → 死代码 | 无结构化日志、无阶段指标、无监控面板 |
| P2-02 | 缺规则/模型版本记录 | 部分落地 | `src/schemas/issues.py:188` `rules_version="v3_3"`；`db/migrations.py:187` `qc_rule_versions` 表；`analyze_dual.py:154` `_load_rules(config.rules_version)` | 未见每条 finding 落 `model_version/prompt_version`；历史结果可复现性存疑（待人工复核）|
| P2-03 | 缺数据迁移与重分析 | 已落地 | `scripts/migrate_historical_uploads.py`；`docs/DATA_RETENTION.md` 迁移 0015/0016 | 见维度 5 |
| P2-04 | 缺性能基线与容量测试 | 部分落地 | `scripts/perf_baseline.py`、`Makefile: perf-baseline/perf-check`、`docs/PERF_BASELINE.md` | 基线是否反映当前代码待复核 |
| P2-05 | PDF 安全隔离不足 | 部分落地 | `api/main.py:271` `PIPELINE_TIMEOUT_SEC` 超时；`MAX_UPLOAD_MB/PAGES` 限制；上传 MIME/签名校验（`routes/upload.py`）| 无独立进程/沙箱隔离；解析仍在主事件循环 executor 内 |
| P2-06 | 缺正式发布门禁 | **未落地** | `.github/` CI 有 lint/type/unit/e2e，但无"Golden Corpus 指标达标"业务门禁 | |

---

## 3. 表 B · 新发现缺口清单

| 编号 | 维度 | 级别 | 现状 | 证据 | 影响 | 建议方案 | 验收标准 | 工作量(人日) | 依赖 |
|---|---|---|---|---|---|---|---|---|---|
| B-01 | 引擎链路 | P0 | 无 OCR，扫描/低文本页 `extract_text()=""` 后无检测直接进入规则，静默漏检 | `api/main.py:248,383-390` | 扫描件全篇漏检，用户不知情 | 引入页面文本密度检测；低于阈值页触发 OCR（tesseract/paddle 适配层）；无 OCR 能力时该页标记并整单转 `review_required` | 扫描件页覆盖率可计算；低质量页不进 `done` | 8–13 | Golden Corpus |
| B-02 | 业务正确性 | P0 | DB 入库层 `_parse_year` 无法识别→2000 | `structured_ingest_runner.py:702,95` 实证 | PG `fiscal_year=2000`，复现历史症状，放大 report_id 碰撞 | 与 `runtime.parse_report_year` 对齐，返回 `None`；`org_dept_annual_report.year` 允许 NULL 或引入 `year_unknown` 分区键 | `_parse_year(None)=None`；DB 无 2000 兜底 | 2–3 | 迁移脚本 |
| B-03 | 业务正确性 | P0 | 无 `review_required` 状态；AI 空 findings 无 error 仍 `done` | `api/main.py:751` | "分析未完整"被当成"审核通过" | 落地四态结论（`findings_detected/no_findings/incomplete/analysis_error`）+ `review_required` 任务态 + 任务级质量门禁 | 空 findings + 低覆盖率→`review_required` | 5–8 | B-01 |
| B-04 | 测试与门禁 | P0 | 无真实 PDF Golden Corpus，无业务质量门禁 | 仓库无 golden_corpus_*；`tests/` 合成 | 无法度量漏检/召回，测试通过≠质量达标 | 建 ≥150 份人工标注语料 + 回放脚本 + 指标门禁并入 CI | 一条命令产出召回/精确/证据完整率 | 15–25 | 业务标注 |
| B-05 | 可观测性 | P1 | 结构化日志设施为死代码 | `logging_config.py`（无调用点）| 生产无 `job_id` 关联日志、无阶段指标、故障定位难 | 在应用/worker 启动调用 `setup_logging(json_format=True)`；用 `LogContext`/`log_job_stage` 埋点各阶段 | 日志含 `job_id/stage`；可按阶段统计 | 3–5 | — |
| B-06 | 可观测性 | P1 | 无队列积压/慢查询/AI 失败率指标，无告警 | `job_queue.py` 仅 heartbeat 文件，无指标导出 | 无法预警 report_id 冲突/findings 归零/积压 | 暴露 metrics（Prometheus 或结构化日志聚合）+ 关键告警 | 关键指标有面板 + P0 告警 | 5–8 | B-05 |
| B-07 | 安全 | P1 | 无强制首登改密（`must_change_password`）| `user_store.py`（无该字段）；`.env.example:DEFAULT_ADMIN_PASSWORD` | 默认密码环境泄露风险 | 首次登录强制改密；黑名单已挡弱口令但不足 | 首登必须改密才放行 | 2–3 | — |
| B-08 | 安全 | P2 | 无安全响应头（HSTS/CSP/X-Content-Type-Options/X-Frame-Options）| `api/main.py:161` CORS；`src/security/__init__.py` 仅鉴权/限流 | XSS/点击劫持/嗅探风险 | 中间件补安全头，或在反代统一注入 | 响应含标准安全头 | 1–2 | — |
| B-09 | 后端可靠性 | P2 | 队列 claim 锁基于本地 FS，跨主机需共享卷；解析在主进程 executor | `job_queue.py:_acquire_claim`（文件锁）；`main.py:_sync_parse_pdf` | 多主机部署需强共享 FS；恶意大 PDF 占用 worker | 明确共享 FS 约束或迁移到 DB/Redis 锁；PDF 解析进程隔离 | 多实例无重复消费；解析隔离 | 5–8 | — |
| B-10 | 部署与运维 | P2 | `docker-compose.yml`(根) 与 `docker-compose.ai.yml` 组件覆盖不一致 | `docker-compose.yml`(807B 极简) vs `docker-compose.ai.yml`(4.7KB) | 一键起服务口径不清，易漏 worker/pg | 统一 compose，覆盖 backend+worker+frontend+postgres+ai-extractor | 单命令起全组件 + `/ready` 通过 | 2–3 | — |
| B-11 | 部署与运维 | P2 | 仓库根目录大量临时产物未清理/未入 .gitignore | 见下方"临时产物"清单 | 仓库污染、误提交风险、递归遍历被拒（os error 5） | 清理 + 补 .gitignore 规则 | 根目录洁净，.gitignore 覆盖 | 0.5 | — |
| B-12 | 数据与持久化 | P2 | 备份/恢复仅文档，无脚本与演练记录 | `docs/DATA_RETENTION.md`、`README.md`（仅"建议演练"）| 灾难恢复无保证 | 编写备份脚本 + 至少一次恢复演练记录 | 演练记录存档 | 3–5 | — |

**临时产物清单（B-11 证据，仓库根目录）**：`pip_tmp_20260427143950/`、`pip_tmp_20260427143918/`、`piptmpdl_20260427144944/`、`pytest_basetemp_*`（多个）、`pytest_tmp_*`（多个）、`.codex_pip_tmp/`、`start-8000.out/err`、`audit-8000.out/err`、`api/dev-*.out/err`、`local.db`、`api/database.db`、`tmp-cookies.txt`、`status.txt`、`debug.log`、`refactor_page_accordion.js`、`refactor_org_accordion.js`、`fix_tsx.js`、`apply_ui_patch.js`、`add_deduped_ui.js`、`tmp_wanli_budget_check.json`。

---

## 4. 表 C · 执行路线图

### M1 · 消除虚假成功与静默失败（准入：本报告确认；准出：无静默漏检/虚假 done）
- B-01 OCR 兜底 + 页覆盖率；B-02 DB 年份兜底修复；B-03 四态结论 + `review_required` + 任务级质量门禁。
- 收尾 P0-05（空 findings 判定）、P0-04（unknown 强制转复核）。
- **准出条件**：扫描件不再静默漏检；无法识别年份不落 2000；`done` 仅在质量门禁通过时出现。

### M2 · 引擎可信度与 Golden Corpus（准入：M1 完成；准出：指标可度量且达标）
- B-04 建 Golden Corpus + 回放脚本；P0-07 证据完整率度量；P2-02 规则/模型版本随结果留痕。
- **准出条件**：召回率≥95%、精确率≥90%、证据完整率 100%、report_id 唯一性 100%（对语料）。

### M3 · 运维保障（准入：M2 指标达标；准出：可观测可恢复）
- B-05 结构化日志接线；B-06 指标+告警；B-08 安全头；B-07 首登改密；B-09 隔离；B-12 备份演练；P2-04 性能基线校准。
- **准出条件**：`job_id` 全链路日志；P0 指标告警；备份恢复演练记录；性能达容量目标。

### M4 · 发布验收（准入：M3 完成；准出：门禁全绿 + 签字）
- B-10 统一 compose；B-11 清理临时产物；历史 118 份全量回放；灰度 + 回滚演练；P2-06 业务门禁并入 CI。
- **准出条件**：《完整整改计划》第 15 节 Gate 0–6 全部通过；四方签字。

---

## 5. 待人工复核清单

1. **P1-05 调用点**：`getReportLabel`（unknown→预算）与 `resolveReportLabel`（unknown→待复核）并存，需人工确认线上实际展示走哪一个（`app/lib/uiAdapters.ts:295 vs 305`）。
2. **P2-02 复现性**：`rules_version` 已入 schema，但每条 finding 是否持久化 `model_version/prompt_version` 未逐一核实；历史结果能否复现需 DB 实测。
3. **P0-07 证据完整率**：字段齐全，但"100% 完整率"须靠 Golden Corpus 度量，当前无法证实。
4. **P1-01/07 前端连贯性**：工作流/筛选/搜索需真实浏览器 UAT，静态代码无法确认体验达标。
5. **P2-04 性能基线**：`perf_baseline.py` 是否反映当前代码路径、30MB/800 页最坏耗时未实测（本次未起服务）。
6. **report_id 残留碰撞**：year=2000 + `fallback_name` 组合的碰撞需在真实多组织数据上验证。

---

## 6. 附录 · 验证命令与输出摘要

所有命令在 `E:/Software Development/GovBudgetChecker/GovBudgetChecker`，使用 `.venv/Scripts/python.exe`。

| 命令 | 结果 | 摘要 |
|---|---|---|
| `python -m ruff check .` | ✅ 通过 | `All checks passed!`（exit 0）|
| `python -m mypy api src tests` | ✅ 通过 | `Success: no issues found in 131 source files`（exit 0，仅 annotation-unchecked note）|
| `python -m pytest -q` | ✅ 通过 | `390 passed, 1 warning in 57.54s`（exit 0；warning 为 starlette testclient httpx 弃用）|
| `npm --prefix app run build` | ✅ 通过 | exit 0；产出 `app/.next/BUILD_ID`(`pi5Sd6jzdsSU0a_i7Fl3h`) 与 `build-manifest.json` |

### P0 关键实证（Python 直接调用）
```
structured_ingest._parse_year(None)     = 2000      # P0-03 DB 路径未修
structured_ingest._parse_year('无法识别') = 2000
runtime.parse_report_year(None)          = None      # P0-03 内存路径已修
runtime.parse_report_year('无法识别')     = None
```

### OCR 依赖实证
`requirements.txt` 仅含 `PyMuPDF>=1.24.0`、`pdfplumber>=0.11.0`，无 `tesseract/pytesseract/paddleocr/easyocr/rapidocr` 任何 OCR 引擎依赖；`api/main.py:248 _extract_visible_text_from_page` 仅 `page.extract_text()`，无扫描页检测与 OCR 触发分支。

### 未执行项
- 未运行 `make e2e`（Playwright，需起服务，按只读约束跳过）。
- 未起后端/前端常驻服务；未写入或删除 `uploads/`、`logs/`、`data/`、`outputs/` 既有数据。
- `docker compose` 一键起未实测（无容器运行时验证）。

---

## 7. 结论

工程化基础（鉴权、队列、持久化、错误透传、CI 门禁）已达到"可运行的生产化脚手架"水平，本地质量门禁全绿。但整改计划定义的**业务可信度红线**——OCR 完整性、年份/类型真实性、四态结论、证据可度量、Golden Corpus 门禁——尚未跨过。按《完整整改计划》第 15 节发布门禁标准，Gate 0/2/3/4 仍不满足，**维持 NO-GO**，不应替代正式人工审核。建议按 M1→M4 顺序推进，M1 与 M2 完成前不开放生产灰度。
