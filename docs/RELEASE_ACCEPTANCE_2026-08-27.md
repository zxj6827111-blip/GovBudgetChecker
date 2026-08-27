# GovBudgetChecker 发布验收报告（M4 收口）

- 日期：2026-08-27
- 分支：`feat/prod-readiness-m1`，HEAD `fc840f6`
- 验收依据：`docs/PRODUCTION_REMEDIATION_PLAN_2026-08-26.md`（M1–M4）、
  `docs/PRODUCTION_GAP_AUDIT_2026-08-26.md`（缺口表 A / 表 B）、
  《GovBudgetChecker 完整整改计划》第 15 节 Gate 0–6
- CI 证据：run [`33033745299`](https://github.com/zxj6827111-blip/GovBudgetChecker/actions/runs/33033745299)
  **success**，18 个步骤全绿，4m36s

---

## 0. 发布结论

### 有条件 GO（限定用途），按《完整整改计划》第 15 节口径则为 NO-GO

必须把"用途"说清楚，否则这个结论会被误读：

| 用途 | 结论 | 理由 |
|---|---|---|
| 作为**人工审核的辅助工具**，在受控范围内灰度使用 | **有条件 GO** | 虚假成功、静默漏检、年份兜底、身份碰撞这四条业务红线在代码侧已关闭并有机器门禁把守；工程化基础（鉴权、队列、隔离、日志、指标、备份）齐备且 CI 全绿 |
| 作为**替代人工审核**的正式生产发布 | **NO-GO** | Gate 0（Golden Corpus）与 Gate 2（OCR）按既定决策本轮不做，Gate 4 的召回率/精确率**从未被度量**。没有语料就无法回答"该查出的问题查出了几个"，此时宣布可替代人工审核是没有依据的 |

### 有条件 GO 的四个前置条件

1. **完成历史遗留清理**：按 `docs/LEGACY_DATA_CLEANUP_PLAN_2026-08-27.md` 执行第 0 节
   （回滚本轮误写的 3+16+374+1 行）、第 1 节（`split_mode.pdf` 残留）、第 5 节
   （`migcheck_tmp` schema）；第 2 节（2 组 `report_id` 冲突）需先确认归属再执行。
2. **完成灰度 + 回滚演练**并留档：`docs/RELEASE_RUNBOOK.md` 第 2/4 节有步骤，但**没有执行记录**。
3. **扫描件走人工**：低文本页/扫描页会转 `review_required`，运营侧必须承接这条队列，
   否则"转复核"等于"没人管"。
4. **明确对外口径**：输出是"待复核问题清单"，不是"审核结论"。

---

## 1. 对照 M4 准出条件

PLAN 对 M4 的准出定义是：「发布门禁全绿；历史数据完成回放/标记；灰度+回滚步骤可执行」。

| 准出条件 | 结论 | 证据 |
|---|---|---|
| 发布门禁全绿 | **通过** | CI run `33033745299` success：Ruff / 日志 message 安全 / 环境变量三方对账 / compose 校验 / Mypy / Pytest **761 passed** / DB 迁移（空库 18 个 + 第二遍 0，PASS）/ 业务门禁 5/5 PASS / 前端 build 34.5s / E2E **33 passed** |
| 历史数据完成回放/标记 | **部分通过** | 766 个历史任务已全量只读回放（`docs/HISTORICAL_REPLAY_2026-08-27.md`），指标与冲突已标记；但**未做批量重跑**，历史产物仍缺 `page_coverage` / `analysis_conclusion`，按本轮决策不追溯 |
| 灰度 + 回滚步骤可执行 | **部分通过** | `docs/RELEASE_RUNBOOK.md` 有部署顺序、回滚方案、故障处置与演练模板，且本轮已补 worker 相关步骤；但**灰度与回滚均无实际演练记录**（备份恢复已演练，见 `docs/BACKUP_RESTORE_DRILL_2026-08-27.md`）|

---

## 2. 逐条对照 Gate 0–6

判定口径：**通过** = 有可复现证据；**部分通过** = 机制到位但目标值未达成或未度量；
**未通过** = 缺失或按决策不做。

### Gate 0：基线门禁 → 未通过

| 条目 | 结论 | 证据 / 说明 |
|---|---|---|
| Golden Corpus 完成 | 未通过 | 按用户决策本轮不建语料（PLAN「用户已确认的关键决策」）|
| 人工标注完成 | 未通过 | 同上 |
| 基线指标可重复计算 | 部分通过 | `scripts/replay_analysis.py` 一条命令可重算全部**结构性**指标，本轮实测 766 个任务；但没有召回率/精确率 |
| 失败样本完成分类 | 部分通过 | `error` 8 个任务有 `analysis_error` + `parse_error_code` 分类口径（`src/services/pdf_parse_process.py:parse_error_code`），但没有做人工归因分析 |

### Gate 1：状态门禁 → 部分通过（代码侧全部通过，存量数据未清）

| 条目 | 结论 | 证据 |
|---|---|---|
| 不存在虚假 completed | **通过** | 同输入前后对比实证：0 文本页材料整改前 `done`+0 问题，整改后 `review_required`+`incomplete`+`page_coverage=0.0`（`docs/HISTORICAL_REPLAY_2026-08-27.md` 第 3.2 节）；测试 `tests/test_quality_gate.py`、`tests/test_page_extraction_assessment.py` |
| 不存在 AI 失败返回正常空结果 | **通过** | AI 失败 → `degraded`；`AI_ASSIST_REQUIRED=true` 时 → `review_required`（`api/main.py` 质量门禁）；测试 `tests/test_analysis_status_model.py`、`tests/test_evidence_completeness.py` |
| 不存在接口异常被转换为空列表 | **通过** | 审查阶段已确认落地（`app/app/api/jobs/route.ts` 透传上游状态 + 502）；E2E `upload-fallback.spec.ts` |
| report_id 唯一性通过 | **部分通过** | 新增侧已关闭：`_resolve_report_scope_key` 在 fallback 匹配或年份未识别时按 checksum 建身份（真实库实证 `scope_key=2a6d822a…`），测试 `tests/test_report_identity_collision.py`；CI 业务门禁把冲突数=0 做成红线。**但历史存量仍有 2 组冲突**（3 job 共用 `1a94158a…`、2 job 共用 `3d54ad55…`），修复方案待确认 |

### Gate 2：提取门禁 → 未通过

| 条目 | 结论 | 证据 / 说明 |
|---|---|---|
| 扫描件 OCR 可用 | **未通过** | 本轮决策明确"仅检测 + 转复核，不做自动 OCR"；`requirements.txt` 无任何 OCR 引擎 |
| 页面覆盖率达到 99% | **未通过** | 覆盖率现在**可计算**（`_assess_page_extraction`，`detector_version=page-extraction-v1`），但实测真实 34 页材料为 0.9706（封面为图片页），未达 99%；历史 766 个任务全部无此字段 |
| 低质量提取进入人工复核 | **通过** | 门禁阈值 `PAGE_COVERAGE_MIN_RATIO=0.8`、`SCANNED_PAGE_MIN_CHARS=50`；实证见 Gate 1 第一条；CI 业务门禁额外卡"`done` 任务覆盖率 ≥ 0.8" |
| 表格提取达到验收目标 | **未通过（未度量）** | 无标注语料，无法给出表格提取准确率 |

### Gate 3：元数据门禁 → 部分通过

| 条目 | 结论 | 证据 / 说明 |
|---|---|---|
| 年份准确率 98% | 未度量 | 无标注语料。可观测的是"未识别比例"：历史 56.01% 未识别 |
| 类型准确率 95% | 未度量 | 同上，历史 `unknown` 占 56.01%；CI 门禁把 unknown 比例上限做成红线（默认 ≤0.35）|
| 组织准确率 98% | 未度量 | 无标注语料 |
| 未识别值不再被默认值掩盖 | **通过** | `src/utils/report_year.py` 是唯一权威实现，识别失败返回 `None`；DB 路径同步（`docs/MIGRATION_0017_NULLABLE_YEAR.md`）。真实库实证：本轮新写入的 3 行 `year=NULL`，而历史行还留着 `year=2000`。测试 `tests/test_runtime_year_parsing.py`、`tests/test_report_year_db_path.py` |

### Gate 4：审核门禁 → 部分通过

| 条目 | 结论 | 证据 / 说明 |
|---|---|---|
| P0/P1 问题召回率 95% | **未通过（未度量）** | 无 Golden Corpus，本轮不做 |
| 问题精确率 90% | **未通过（未度量）** | 同上 |
| 证据完整率 100% | **部分通过** | 机制到位：`src/services/evidence_guard.py` 缺证据的 AI finding 降级为待复核、不计入正式问题；实测真实材料重跑后正式问题证据完整率 **1.0（5/5）**；CI 门禁阈值 ≥0.99。历史存量为 0.4861（缺证据的 1404 条全是规则告警类）|
| 规则和模型版本可追踪 | **通过** | 实测重跑后每条 finding 带 `rule_version=v3_3`、`engine_version=0.1.0`（AI 关闭时 `model_version`/`prompt_version` 为 null 属正确行为）；`src/utils/provenance.py`；测试 `tests/test_finding_version_provenance.py` |

### Gate 5：体验门禁 → 部分通过（自动化全绿，人工 UAT 未做）

| 条目 | 结论 | 证据 |
|---|---|---|
| 上传、处理、复核、导出流程可用 | **通过** | 本轮新增 `e2e/tests/full-flow-review-export.spec.ts`：上传 → 轮询等待（processing→done）→ 确认问题 → 导出 PDF，全链断言；CI E2E 33 passed |
| 错误状态和恢复操作完整 | **部分通过** | `review_required` / `degraded` 在任务页与列表页如实展示（e2e 反例断言"不得出现分析完成"）；有 reanalyze / associate / batch-rematch 恢复入口。但没有做完整人工 UAT |
| 历史任务分页完整 | **通过** | 服务端分页（`api/routes/jobs.py` offset/limit）；E2E `report-actions.spec.ts` 分页用例 |
| PDF 证据跳转可用 | **通过** | E2E `report-actions.spec.ts` 断言 `task-pdf-highlighter` 打开、`href` 指向对应 job |
| 浏览器 E2E 全部通过 | **通过** | CI E2E **33 passed (52.0s)**，无重试；本机新用例 `--repeat-each=3` 连跑 6 passed |

### Gate 6：生产门禁 → 部分通过

| 条目 | 结论 | 证据 / 说明 |
|---|---|---|
| 监控和告警可用 | **部分通过** | 结构化日志全链 `job_id`/`stage`（`src/utils/logging_config.py`，Task C 又补齐了 message 字段脱敏与静态门禁）；`/metrics` 端点含 `report_id_uniqueness` 等关键指标；阈值文档 `docs/OBSERVABILITY_AND_ALERTS.md`。**但未接入真实告警通道**（无 Alertmanager/webhook 配置与验证）|
| 性能测试通过 | **部分通过** | 有 `scripts/perf_baseline.py` + `scripts/perf_thresholds.json` + `make perf-check` + `docs/PERF_BASELINE.md`；**本轮 M4 未实跑性能测试**，30MB/800 页最坏耗时仍未实测 |
| 安全测试通过 | **部分通过** | 鉴权默认开启并有生产模式矩阵验证、RBAC、限流、安全响应头、首登强制改密、上传 MIME/签名校验、路径穿越测试、PDF 解析进程隔离（超时/页数/文本量/单元格上限）、日志脱敏（extras + message 双重）。**无第三方渗透测试报告**|
| 备份恢复通过 | **通过** | `scripts/backup_all.py`（UPLOAD_DIR + PG + 审计日志三件套备份/校验/恢复）+ 演练记录 `docs/BACKUP_RESTORE_DRILL_2026-08-27.md` |
| 灰度发布通过 | **未通过** | Runbook 有灰度顺序（canary → full），**无执行记录** |
| 回滚演练通过 | **未通过** | Runbook 第 4 节有回滚方案，**无演练记录** |

---

## 3. 表 A 最终状态（《完整整改计划》P0 / P1 / P2）

### 3.1 P0

| 编号 | 内容 | 最终状态 | 证据 / 说明 |
|---|---|---|---|
| P0-01 | 扫描 PDF 可靠 OCR 兜底 | **按决策部分关闭** | 落地"检测 + 转复核"，不做 OCR（Task 2/3）|
| P0-02 | `done` 只代表未抛异常 | **已关闭** | 四态结论 + 任务级质量门禁（Task 1/3）|
| P0-03 | 年份识别失败落 2000 | **已关闭** | 内存与 DB 双路径统一返回 `None`（Task 4），真实库实证 |
| P0-04 | unknown 只跑通用规则 | **已关闭** | unknown 强制转 `review_required`（Task 3）|
| P0-05 | AI 失败返回空 findings | **已关闭** | AI 失败/空结果与"真无问题"已可区分（Task 1/3）|
| P0-06 | 前端隐藏接口异常 | **已关闭** | 审查阶段即已落地 |
| P0-07 | 结果无完整证据链 | **已关闭（目标值仅新任务口径实测）** | `evidence_guard` + `evidence_completeness` + CI 阈值（Task 8）|
| P0-08 | 缺真实 PDF Golden Corpus | **未做（既定决策）** | 召回率/精确率因此无法度量 |
| P0-09 | report_id 可能重复 | **代码侧已关闭，存量未清** | Task 5 + CI 门禁；历史 2 组冲突方案待确认 |

### 3.2 P1

| 编号 | 内容 | 最终状态 | 说明 |
|---|---|---|---|
| P1-01 | 上传/处理/复核流程割裂 | **部分关闭** | 新增全链 E2E 证明流程贯通；人工 UAT 未做 |
| P1-02 | 缺处理阶段进度 | **部分关闭** | 有 stage/progress 与 `page_coverage`；进度仍是阶段化数值而非逐页百分比 |
| P1-03 | 无法修正年份/类型/组织 | **已关闭** | associate / reanalyze / batch-rematch |
| P1-04 | 详情固定 30 条 | **已关闭** | 服务端分页 |
| P1-05 | unknown 被适配为预算 | **已关闭** | 调用点统一到 `resolveReportLabel`（unknown→待复核，Task 6）|
| P1-06 | 缺批量重试/复核 | **已关闭** | reanalyze-all + 批量工作流动作 |
| P1-07 | 缺搜索/筛选/排序 | **部分关闭** | 前端有筛选与搜索；覆盖度未做 UAT |
| P1-08 | 错误信息缺建议 | **部分关闭** | 关键路径有建议文案，覆盖面有限 |

### 3.3 P2

| 编号 | 内容 | 最终状态 | 证据 |
|---|---|---|---|
| P2-01 | 缺阶段级指标与监控 | **已关闭** | 结构化日志接线（Task 10）+ Task C 的 message 脱敏门禁 |
| P2-02 | 缺规则/模型版本记录 | **已关闭** | 版本留痕（Task 7），实测 finding 带版本 |
| P2-03 | 缺数据迁移与重分析 | **已关闭** | 迁移并入 CI（空库 + 幂等断言）+ 历史全量回放报告（Task 15.1/15.2）|
| P2-04 | 缺性能基线与容量测试 | **部分关闭** | 脚本与阈值在，本轮未实跑 |
| P2-05 | PDF 安全隔离不足 | **已关闭** | 独立可终止子进程 + 超时/内存/页数/体积上限（Task 13）|
| P2-06 | 缺正式发布门禁 | **已关闭（仅结构性）** | CI 业务门禁 5 条红线 + 局限声明（Task 15.2）|

---

## 4. 表 B 最终状态（B-01 ～ B-12）

| 编号 | 缺口 | 最终状态 | 证据 |
|---|---|---|---|
| B-01 | 无 OCR，扫描页静默漏检 | **按决策部分关闭** | 检测 + 转复核，`ocr_applied=false` 如实记录 |
| B-02 | DB 层年份兜底 2000 | **已关闭** | 迁移 0017 + 真实库实证 `year=NULL` |
| B-03 | 无 `review_required`，空 findings 仍 `done` | **已关闭** | 四态结论 + 门禁，前后对比实证 |
| B-04 | 无 Golden Corpus 与业务质量门禁 | **框架关闭，语料未做** | 回放脚本 + CI 结构性门禁；召回/精确未度量 |
| B-05 | 结构化日志为死代码 | **已关闭** | `configure_logging_from_env` 在 api/worker 接线；Task C 补 message 字段红线 |
| B-06 | 无关键指标与告警 | **部分关闭** | `/metrics` + 阈值文档；告警通道未接入 |
| B-07 | 无强制首登改密 | **已关闭** | `must_change_password` + 生产默认开启 |
| B-08 | 无安全响应头 | **已关闭** | HSTS/CSP/X-Frame-Options 等 + 开关与覆盖变量 |
| B-09 | 队列锁依赖本地 FS、解析在主进程 | **已关闭** | 进程隔离；compose 里 backend/worker 用同一锚点共享 `UPLOAD_DIR`，共享约束显式化并有测试 |
| B-10 | compose 组件覆盖不一致 | **已关闭** | 补 worker 服务、一键起 5 组件、`config` 校验进 CI、环境变量三方对账 0 违规 |
| B-11 | 仓库临时产物未清理 | **已关闭（6 个目录受 ACL 限制未删）** | 清理 32 项 + `.gitignore` 补漏 + `git check-ignore` 覆盖度测试 |
| B-12 | 备份/恢复仅文档 | **已关闭** | 备份脚本 + 演练记录 |

---

## 5. 本轮新发现的缺口（不在原表内）

| 编号 | 发现 | 状态 | 证据 |
|---|---|---|---|
| N-1 | 日志 `message` 字段不经脱敏：`logger.warning(f"...issue={issue}")` 会把 `evidence_text`（PDF 原文）落盘；`raise Exception(f"格式错误: {result}")` 让 AI 响应体顺着 `{e}` 落盘 | **已修** | `scripts/check_log_message_safety.py`（AST 门禁，含 raise 与 traceback 规则）+ `describe_exception`/`fingerprint_for_log` + `tests/test_log_message_safety.py`（34 用例，正反对照）|
| N-2 | `docker-compose.ai.yml` 传 `RULES_FILE_PATH`，代码读的是 `RULES_FILE` —— 配了等于没配 | **已修** | 改为 `RULES_FILE=/app/rules/v3_3.yaml`；`scripts/check_env_consistency.py` 把这类"死变量"变成 CI 红线 |
| N-3 | **测试会往仓库 `uploads/` 写任务目录**（每次全量 pytest +5 个），污染被回放度量的历史语料，也是真实库里 `split_mode.pdf` 反复重现的原因 | **已修** | `tests/conftest.py:isolate_upload_root`（autouse）+ `tests/test_upload_root_isolation.py`；实测修复后全量 pytest 前后 `uploads/` 新增 0（修复前 +5）|
| N-4 | 本轮"整改前后对比"脚本误写开发库：`os.environ.pop("DATABASE_URL")` 被 `api/main.py` 导入链上的 `load_dotenv()` 抵消，写入 3+16+374+1 行 | **待回滚（已给 SQL）** | `docs/LEGACY_DATA_CLEANUP_PLAN_2026-08-27.md` 第 0 节；`uploads/` 未受影响（sha256 全量比对 0 变化）|
| N-5 | 历史回放语料含测试生成任务（766 → 783 增长与 N-3 同源），会稀释"真实材料"的指标口径 | **已标记** | `docs/HISTORICAL_REPLAY_2026-08-27.md` 第 2.1 / 4 节；N-3 修复后不再增长 |

---

## 6. 本轮整改**未覆盖**的内容（发布决策必须知道）

1. **OCR 仅检测、未实现自动识别**。扫描件与低文本页只会被标记并转 `review_required`，
   系统不会读出这些页的内容。运营侧必须承接这条复核队列，否则这些材料等于没审。
2. **无 Golden Corpus，因此召回率与精确率从未被度量**。所有质量门禁都是结构性的
   （覆盖率、unknown 比例、证据完整率、`report_id` 唯一性、终态分布）。
   它们能证明"没有静默失败、没有虚假成功"，**不能证明"该查出的问题都查出来了"**。
   这是 Gate 0 与 Gate 4 未通过的根本原因，也是本报告不给"无条件 GO"的根本原因。
3. **AI 真实模型链路未端到端验证**。本轮所有验证都在 `AI_ASSIST_ENABLED=false`
   或 mock 下完成：仓库自带的 `ai_extractor_service.py` 是最小实现，
   真实 provider（Ark / Gemini / Codex 网关）的可用性、超时、限流、返回质量均未实测。
   因此 `model_version` / `prompt_version` 的留痕能力已验证，但**真实模型的产出质量未验证**。
4. **性能与容量未实测**。`scripts/perf_baseline.py` 与阈值文件在位，本轮未跑；
   30MB / 800 页最坏耗时、并发与队列吞吐没有数据。
5. **灰度与回滚未演练**。只有 Runbook 步骤，没有执行记录（备份恢复已演练）。
6. **告警通道未接入**。指标端点与阈值文档就绪，但没有任何告警真正被触发验证过。
7. **前端体验未做人工 UAT**。E2E 覆盖关键路径，但筛选/搜索/错误提示的可用性
   仍是静态与自动化推断。
8. **历史数据不追溯**。766 个历史任务保持原样（只读），仍缺 `page_coverage` /
   `analysis_conclusion`，2 组 `report_id` 冲突未修；批量重跑属独立变更。
9. **本地环境残留**：6 个临时目录因 NTFS ACL 删不掉（需提权）、`api/database.db` 未处理、
   `migcheck_tmp` schema 待清理，均在遗留清理方案里。
10. **多主机部署未验证**。队列 claim 锁是 `UPLOAD_DIR` 下的文件锁，compose 内已保证
    backend/worker 共享同一卷，但**跨主机共享 FS 的场景没有实测**。

---

## 7. 剩余风险与建议

| 风险 | 级别 | 缓解建议 |
|---|---|---|
| 漏检率未知（无语料） | **高** | 上线后按批次做人工抽检（建议每批 ≥10%），把抽检结果沉淀为 Golden Corpus 第一版，再把召回率门禁接进 CI |
| 扫描件复核队列无人承接 | 高 | 上线前明确复核责任人与 SLA；用 `/metrics` 的 `review_required` 比例做告警 |
| 真实 AI 链路未验证 | 中高 | 灰度首日先 `AI_ASSIST_ENABLED=false` 只跑规则，确认稳定后再开 AI，并用 `AI_ASSIST_REQUIRED=true` 保证 AI 失败不产出 `done` |
| 历史存量身份冲突 | 中 | 按遗留清理方案第 2 节修复并保留 `report_id_remap` 映射；修复前查询侧不要按 `report_id` 做唯一性假设 |
| 性能未知 | 中 | 灰度前跑一次 `make perf-check`，并按 `MAX_UPLOAD_MB`/`MAX_UPLOAD_PAGES` 与反代限制对齐 |
| 回滚未演练 | 中 | 灰度前做一次完整回滚演练（含数据库回退与 `UPLOAD_DIR` 恢复），填 Runbook 第 6 节模板 |
| 多主机部署共享 FS | 中 | 单机部署优先；确需多主机时先验证共享卷的文件锁语义，或改用 DB/Redis 锁 |

---

## 8. 本轮（M4）提交与验证记录

| 提交 | 内容 | 关闭缺口 |
|---|---|---|
| `beb2359` | 日志 message 敏感信息整改与静态门禁 | Task C（M3 复核遗留）|
| `963867b` | compose 统一为一键起全组件并补 worker | B-10 |
| `704f986` | 清理本地临时产物并补全 `.gitignore` | B-11 |
| `c81e861` | 历史任务全量回放与整改前后对比 | P2-03 |
| `7e059dd` | 迁移步骤与回放业务门禁并入 CI | P2-03 / P2-06 |
| `a45ec90` | 上传→等待→复核→导出关键流程 E2E | Gate 5 |
| `c495061` | 历史遗留清理方案（未执行）| — |
| `fc840f6` | 隔离任务产物目录，测试不再写入 `uploads/` | N-3 |

### 本机门禁（Windows）

```
ruff check .                        All checks passed!
check_log_message_safety.py         exit 0（0 违规）
check_env_consistency.py            exit 0（代码 124 / .env.example 120 / compose 55，0 违规）
mypy api src tests                  Success: no issues found in 157 source files
pytest -q                           760 passed, 1 skipped
npm --prefix app run build          exit 0（Compiled successfully）
npm --prefix app run test:e2e       33 passed (28.1s)
docker compose ... config --quiet   exit 0；config --services = 5 个服务
```

`1 skipped` 是 Windows 无 `RLIMIT_AS` 时主动跳过的内存上限测试；Linux CI 上该用例执行，
因此 CI 显示 761 passed。

### CI（Linux, run `33033745299`, success）

```
Ruff                                All checks passed!
Log message safety                  ✔
Env var consistency                 ✔
Compose config check                ✔（services diff 通过）
Mypy                                ✔
Pytest                              761 passed, 3 warnings in 26.33s
DB migrations                       18 个迁移全部应用；第二遍新增 0；PASS（幂等）
Business gate                       5/5 PASS，并打印"无 Golden Corpus，不度量召回率"局限
Frontend build                      ✓ Compiled successfully in 34.5s
E2E                                 33 passed (52.0s)
```

### 数据安全自证

| 项 | 结果 |
|---|---|
| `uploads/` 只读（回放期间） | 2563 个文件 sha256+size+mtime 全量比对，新增/删除/变更 **0** |
| `uploads/` 不被测试写入（修复后） | 全量 pytest 前后目录条目差 **0**（修复前每次 +5）|
| 测试不写真实库 | 带真实 `DATABASE_URL` 跑全量 pytest（760 passed / 1 skipped），30 张表 44622 行**行数 delta 全为 0** |
| 唯一一次真实库写入 | 本轮"整改前后对比"脚本的隔离失效（N-4），已定位范围并给出回滚 SQL，**待确认后执行** |
