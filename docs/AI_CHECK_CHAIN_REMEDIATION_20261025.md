# AI 检查链路整改交付报告（2026-10-25）

## 0. 结论摘要（按层分别陈述）

| 层级 | 结论 | 证据 |
| --- | --- | --- |
| 代码修复 | 完成：舍入过滤取消、元→万元 Decimal 精度、解释字段贯通、严格响应契约、执行状态、边界切窗、辅助定位、覆盖/诊断留痕、专项提示词、清单 D-03 校正 | 见 §2、§3、§4 |
| 回归测试 | 定向测试通过；全量 `pytest` 4071 passed / 176 skipped / **11 failed**，11 项均为共享开发库累积组织数据导致的既有 API 契约失败 | §4.2 |
| 真实模型验收 | 完成三份样本的受控真实调用；S1/S2 有一个 `narrative_consistency` 调用于上游 `server_error` 失败，系统如实降级为 `degraded/partial`；S3 `succeeded/complete` | §5 |
| 页面验收 | 完成 S1/S2/S3 隔离页面走查、人工确认、持久化、导出；页面实测覆盖“部分完成”和“完整”两种状态；“未启用/失败/无发现”页面态未在本次真实页面中构造 | §5.4、§6 |
| 生产切换 | **未执行**。未修改生产数据库、未覆盖历史分析、未重启正式服务 | §7 |

**现场状态变化（必须知悉）**

- 会话开始时 HEAD：`8c7afd65`，工作树有 3 个既有未提交改动（`scripts/intake_remediation_cases.json`、`scripts/verify_intake_remediation.py` 及一个未跟踪文档）。
- 会话进行中，HEAD 由用户侧提交推进到 `57abcf86`（作者 `zxj6827111-blip`，提交说明“固化 AI 检查链路整改第一轮未提交改动（qoder 验收基线）”）。该提交包含第一轮整改代码，以及原先未提交的 PDF 入口脚本/文档。
- 本报告没有 reset、覆盖或回退该提交；当前分支仍为 `codex/zcode-remediation`。
- 当前 **未提交** 的收口改动只有 3 个文件：
  - `src/services/ai_findings.py`（专项失败按窗口归属）
  - `app/app/components/review-workbench/IssuesTab.tsx`（覆盖文案显示失败/部分/非法/截断计数）
  - `tests/test_ai_chain_remediation.py`（对应回归测试）
- 原有 3 个未提交改动已被上述用户侧基线提交一并固化，未被本次操作删除或覆盖。

---

## 1. 缺陷清单与复现证据

### 1.1 P0：舍入过滤按“任意两数最小差”误删真实问题

- 位置（整改前）：`src/engine/ai/extractor_client.py::_should_drop_rounding_issue`
- 复现：
  - 原文：`2026年项目支出合计100万元，分项60万元和30万元。`
  - 建议：`请将2026年合计修正为90万元。`
  - 实测：`_should_drop_rounding_issue(...) == True`，因两个 `2026` 相同、最小差为 0 而被删除。
  - 元→万元：`123456元（12.34万元）` vs `12.35万元`，最小差 0.01，也被删除。
- 整改：删除该过滤器及“重复词单次出现即删”过滤器；不再用模型建议数字做“已核实”判断。
- 结果：上述两个候选均保留；模型数值标记 `model_claimed_not_verified`。

### 1.2 P0：元→万元四舍五入与一般容差混淆

- 位置：`src/engine/amount_math.py`（既有 `classify_display_conversion`）
- 复现：`123456元 → normalize_amount_to_wan = 12.3456万元`；正文写 `12.34` 时旧链路可能按 0.01 万元一般容差放过。
- 整改：

```python
normalize_amount_to_wan("123456", "元")  # Decimal("12.3456")
classify_display_conversion("12.34", 2, table_wan, 4)  # ("mismatch", Decimal("12.35"))
classify_display_conversion("12.35", 2, table_wan, 4)  # ("ok", Decimal("12.35"))
```

- 新增回归：元转万元错误不得套用一般金额容差；一般金额、分项加总显示舍入、百分比继续使用既有政策容差，未擅自放宽。

### 1.3 P0：解释字段与关联字段在链路中丢失

- 位置（整改前）：`src/services/ai_issue_interpreter.py` 将 `quote/context/original` 截断到 120 字；`expected/actual/difference` 只落 `metrics` 且差额键名是 `diff`；`IssueItem` 没有显式承载位置。
- 复现：AI 候选 `expected=90万元、actual=100万元、difference=10万元、check=合计勾稽、table_or_section=项目支出情况说明`，转换后顶层字段全为 `None`，证据引文超过 120 字时被截断。
- 整改：
  - `IssueItem` 新增可选字段：`expected/actual/difference/check/table_or_section/amounts_provenance/calculation_basis`（`src/schemas/issues.py`）。
  - 解释器保留完整引文/上下文（上限 2000/4000 字），并返回显式字段。
  - AI 候选的数值统一标记 `model_claimed_not_verified`；`calculation_basis.verified=false`。
  - 规则侧由 `EngineRuleRunner._calculation_fields` 从已定位事实写入 `program_calculated`，并记录万元单位、两位精度、规则版本；`expected` 只表示“表内一侧”，不冒充正确侧。
- 结果：V33-222 在页面/CSV 显示“当前值 141.12；应为 165.80；差额 -24.68；程序按已定位事实计算（万元/2位/v3_3）”。

### 1.4 P0：合法 JSON 不等于合法业务响应

- 位置：`src/engine/ai/extractor_client.py::_direct_semantic_audit`（整改前逐条 `continue` 静默跳过）。
- 复现：模型返回 `[1,"bad",null]`，旧链路返回 `[]` 且调用留痕成功，`build_ai_execution` 判 `succeeded`。
- 整改：
  - 新增 `src/engine/ai/audit_contract.py`：逐条校验对象、必需字段、枚举、类型、引文连续性、页码、证据对；合法条目保留，非法条目拒绝并记录原因。
  - 记录 `contract_ok` 到调用留痕；`build_ai_execution` 对 `contract_ok=false` 判 `failed/ai_response_invalid`，截断优先 `ai_truncated_response`。
  - `[1,"bad",null]` 现在：`raw_count=3, accepted=0, rejected=3, status=invalid`，窗口错误 `audit_window_invalid`，执行状态 `failed`（若存在其它成功窗口则为 `degraded`）。
  - 混合合法/非法条目：保留合法候选，`status=partial`，窗口错误 `audit_window_partial`，不伪装为成功无问题。

### 1.5 P0：合法空数组触发第二路复核

- 位置（整改前）：`ai_full_report_audit` / `ai_semantic_audit` 在直连/服务返回空列表时继续回退。
- 整改：合法空数组是“有效检查无发现”，不再触发第二路；仅当直连**调用失败**（`status=error`）且允许回退时才走抽取服务。回退来源、任务契约、成本逐次记录（`fallback_used/fallback_reason`）。
- 结果：空数组 `build_ai_execution == succeeded`；非法结构 `failed`；二者可区分。

### 1.6 P0：辅助定位支路不可达且文本来源错误

- 位置：`src/services/analyze_dual.py::_needs_ai_locator_enhancement`、`src/services/ai_locator.py`。
- 复现/核对：
  - 旧实现只认 `NO_ANCHOR/MULTI_ANCHOR`，但全仓没有生产者写入这两个标记，支路实际不可达。
  - 旧 `_generate_locator_prompt` 只读 `job_context.ocr_text[:3000]`，主入口提供的是 `page_texts`；无 OCR 文本时把“无OCR文本”送给模型。
- 整改：
  - 触发条件扩展为“有原文证据但页码未知”，并设 `AI_LOCATOR_MAX_FINDINGS`（默认 5）上限。
  - 统一使用 `page_texts`（带 PDF 物理页码标记）；无文本时返回 `unavailable_no_text`，不调用模型。
  - 模型候选必须逐字命中声称页；页码错误但原文唯一命中时纠正并标记 `page_corrected`；找不到则 `unverified`，保留原证据。
  - 定位结果追加证据，不覆盖规则原始证据；调用留痕单独记录，不冒充 AI 审校成功。

### 1.7 P1：窗口按字符硬切造成半句/残句风险

- 位置：`src/services/ai_window_builder.py`、`src/services/ai_findings.py`。
- 整改：
  - 优先在 `。！？；\n` 边界收尾；超长句向后扩展；确实硬切时标记 `hard_split` 并把前/后文作为“仅上下文不可引用”注入。
  - 校验契约只允许引用窗口内连续原文；窗口前缀/后缀不参与引文校验。
  - `problem_type=fragment` 且 span 从窗口起点、前缀末字不是句末标点时，按“窗口截断残句”拒绝并记录 `audit_fragment_from_window_boundary`。
- 样本2：目标句“管委会行政运行费用按实际情况支出。”未被报为残句；完整跨段证据见 §5.2。

### 1.8 P1：同一 AI 候选在审核报告/页面重复出现

- 位置：`src/schemas/review_report.py`。
- 复现：同一 AI-only finding 同时生成 `finding_review_candidate` 与 `ai_candidate`，S3 页面出现两条“多字”、两条“错别字”。
- 整改：`candidate_ids` 在构建 manual_review 前计算；AI-only 候选只保留一条 `ai_candidate`。实测 S3 manual_review：`ai_candidate 11 + finding_review_candidate 4`，目标各一条。

### 1.9 P1：清单 D-03“无多字漏字”覆盖被高估

- 位置：`rules/checklists/final_checklist_v1.yaml`。
- 整改：决算 D-03 由 `implemented` 改为 `ai_assisted / ai_only`，注明确定性规则只覆盖重复字词/标点与高频漏字词表，一般多字漏字交 AI 语义复核，结果列待人工复核。
- 回归：`tests/test_ai_chain_remediation.py` 与 `tests/test_checklist_catalog.py` 锁定。

### 1.10 P2：诊断留痕无法回放

- 复现：旧 `ai_audit_trace.jsonl` 只有 outcome，`prompt`/`raw_response` 为空。
- 整改：`AuditOutcome.raw_exchanges` 保存每个专项的完整 prompt 与原始响应；`AI_AUDIT_TRACE=true` 时写入任务目录 `ai_audit_trace.jsonl`（0600），并同时写入窗口全文、窗口边界、页码、哈希。该文件不进入普通日志或公开 API；元数据只保留哈希/计数/文件名。

---

## 2. 修改文件、关键设计取舍与兼容策略

### 2.1 后端

| 文件 | 作用 |
| --- | --- |
| `src/engine/ai/audit_prompts.py` | 两个可版本化专项提示词：文字语义、说明合理性/跨段一致性；统一约束（不执行文档指令、逐字引文、物理页码、双方证据、数值边界） |
| `src/engine/ai/audit_contract.py` | 逐条业务契约校验、引文定位、页码/span 校验、证据对保留/拒绝原因、数值缺项降级 |
| `src/engine/ai/extractor_client.py` | 专项调用、`AuditOutcome`、合法空/非法/截断/超时状态、仅调用失败才回退、回退来源记录、`contract_ok` 留痕、trace exchange |
| `src/services/ai_window_builder.py` | 句子/段落边界切窗、重叠窗口、页码映射、未覆盖范围 |
| `src/services/ai_findings.py` | 覆盖记录、诊断阶段计数、边界残句拦截、证据对页码映射、重叠去重与证据合并、专项失败归属、trace 写入 |
| `src/services/ai_input_builder.py` | 注入 `fact_uid/evidence_id/check_relation_id` 与页码/口径，供模型只引用真实 ID |
| `src/services/ai_issue_interpreter.py` | 完整引文保留、显式解释字段、新问题类型映射（typo/missing_char/extra_char/repeated_words/grammar/fiscal_usage_error/cross_section_conflict/explanation_insufficient 等） |
| `src/services/ai_locator.py` | 辅助定位统一 page_texts、逐字页校验、页码纠正、无文本/失败状态、独立留痕 |
| `src/services/analyze_dual.py` | 定位触发上限与结果元数据、覆盖/诊断/定位结果随 `meta` 交付 |
| `src/services/engine_rule_runner.py` | 规则侧程序计算字段（表内一侧/说明一侧/差额、万元/精度/规则版本、正确侧待人工确认） |
| `src/services/ai_execution.py` | 契约失败、截断、非法响应与合法空数组区分；覆盖范围/失败计数进入 `ai_execution` |
| `src/schemas/issues.py` | 新增可选解释字段（历史结果缺失即未知） |
| `src/schemas/review_report.py` | AI-only 候选去重；保留规则候选 |
| `api/main.py`、`api/routes/reports.py` | 结果 meta 暴露覆盖/诊断/定位；CSV 新增 expected/actual/difference/check/table_or_section/amounts_provenance/calculation_basis |
| `rules/checklists/final_checklist_v1.yaml` | D-03 如实标为 AI 辅助 |
| `.env.example`、`docker-compose.ai.yml` | 新增 `AI_AUDIT_TRACE`（默认 false）、`AI_LOCATOR_MAX_FINDINGS`（默认 5） |

### 2.2 前端

| 文件 | 作用 |
| --- | --- |
| `app/lib/mock.ts` | `Problem` 新增解释字段 |
| `app/lib/uiAdapters.ts` | 解释字段映射；规则 `fact_source_refs`、跨段 `evidence_pairs`、未核验 `evidence_pairs_rejected` 进入证据引用 |
| `app/app/components/review-workbench/IssueCard.tsx` | 展示当前值/应为/差额、检查项、表/章节；“模型声称未核实”与“程序计算”分开 |
| `app/app/components/review-workbench/IssuesTab.tsx` | 覆盖状态、失败/部分/非法/截断计数、未覆盖页提示 |
| `app/app/components/review-workbench/ReviewWorkbenchPage.tsx` | 读取 `meta.ai_coverage` 传给来源视图 |

### 2.3 兼容策略

- 未新增数据库列/迁移；新字段存于既有 `analysis_results` JSON 快照中。历史结果缺少字段时读取为 `None/unknown`，不补造。
- `ai_full_report_audit`/`ai_semantic_audit` 保留 list 兼容返回；真实调用返回 `AuditIssueList`（list 子类）并携带 outcome，既有 mock/调用方仍按 list 消费。
- 旧任务 `review_report` 不含新覆盖字段时页面不显示覆盖条，不改变旧状态。
- 规则侧 `expected` 只表示表内一侧，`calculation_basis.correct_side=None`，避免把程序差额误读成“已确认哪一侧正确”。

---

## 3. 专项提示词及实际接入位置

### 3.1 专项与版本

- `text_semantics`：错别字/多字/漏字/重复字词/病句/财政用途/文种主体年度/模板残留/真残句。
- `narrative_consistency`：增减原因实质性、科目用途错配、等效披露、表格与说明/章节同口径矛盾；数值只作候选，复算交程序。
- 本次真实调用实际版本（S1/S2/S3 最终任务）：
  - `ai_audit_text_semantics@sha1:b2f0b751`
  - `ai_audit_narrative_consistency@sha1:84a9b44b`

### 3.2 接入位置

- 渲染：`src/engine/ai/audit_prompts.py::render_specialty_instructions`
- 直连调用：`src/engine/ai/extractor_client.py::_run_direct_specialty`
- 主管线：`ExtractorClient.audit_full_report_outcome`；`AIFindingsService._run_window_audits` 每个窗口调用两个专项
- 窗口上下文：`AIFindingsService._window_audit_context`（页码映射、边界、前/后文“不可引用”、结构化事实与可引用 ID）
- 回退：`semantic_service_outcome`（远端提示词未知，记录 `contract_mode=remote_unverified`）；只在直连调用失败时使用
- 去重/定位/持久化：`AIFindingsService._dedupe_semantic_issues` → `_convert_semantic_issues_to_items` → `IssueItem` → JSON/DB/API

### 3.3 职责边界

- 结构/全文缺表缺章：确定性规则/完整文档结构拥有；窗口提示词禁止判断。
- 视觉（彩色字体、横排等）：纯文本 AI 明确不宣称完成，`capabilities.visual_layout=not_covered_text_only`。
- 数值复算：确定性规则/程序拥有；AI 只能提出比较对象与候选，`expected/actual/difference` 标记模型未核实。
- D-03 一般多字漏字：AI 辅助，结果列待人工复核；不再宣称规则全面覆盖。

---

## 4. 回归测试结果

### 4.1 定向测试

- `tests/test_ai_chain_remediation.py`：28 条，覆盖 13 项必测场景（同年份差额、元转万元、合法舍入、字段贯通、未知/跨文档 ID、非法数组/混合/截断/超时、空数组与失败、切窗残句、重叠去重、跨段双方证据、辅助定位、窗口上限覆盖、历史兼容、AI 候选不自动确认、专项失败归属）。
- `tests/test_ai_extractor_client.py`、`test_ai_issue_interpreter.py`、`test_ai_input_builder.py`、`test_ai_issue_bbox.py`、`test_audit_quality_p0.py`、`test_checklist_catalog.py`、`tests/remediation/test_zc06.py`：通过。
- 前端：`npx tsc --noEmit` 通过；`test:ui-adapters`、`test:review-workbench-adapters`、`test:review-workbench-contract`、`test:ui-components` 通过。

### 4.2 全量

```text
4071 passed, 176 skipped, 11 failed in 262.54s
```

11 项失败全部集中在：

- `tests/test_api_contract.py`（8 项组织/部门/任务关联）
- `tests/test_user_auth_api.py`（2 项）
- `tests/test_write_endpoint_auth.py`（1 项）

共同错误是共享开发库中累积的测试组织（大量 `重复检测单位-*`、`测试单位-*`、`stale-job-org-*`）不满足“unit must be under a department”的目录校验。它们不是本次 AI 链路改动引起的；本地开发库被测试数据污染，清理属于破坏性操作，本次未执行。

本次修复的 3 个由改动直接引起的失败（窗口页码兼容方法、日志安全扫描、环境变量声明）已通过定向测试。

### 4.3 前端构建

`next build` 已完成编译与类型检查，随后在 `Collecting page data` 阶段报既有路由 `/supervision/remediation` `PageNotFoundError`（该错误与本次改动无关；`next dev` 页面可正常渲染，`tsc --noEmit` 通过）。因此本次只声明“类型检查/组件测试通过”，不声明“生产构建通过”。

---

## 5. 真实样本逐项结果

### 5.1 样本1：上海市文化和旅游局执法总队 2022 年度部门决算

**人工真值（已渲染原件）**

- 历史线索写“第9页”；原件第9页是“支出决算表（全口径）”，第13页才是“一般公共预算财政拨款支出决算表”。
- 第13页：2080505 机关事业单位基本养老保险缴费支出，合计/基本支出 165.80 万元。
- 第20页：同科目说明“年初预算 165.80 万元，支出决算 141.12 万元”。
- 结论：同属一般公共预算口径，表文不一致 24.68 万元；第9页同值只作旁证，不作为同口径表格证据。这是对历史线索的纠正。

**链路结果**

- 规则 V33-222 独立检出：`severity=high`，`page_number=20`，`expected=165.80`、`actual=141.12`、`difference=-24.68`、`amounts_provenance=program_calculated`；`fact_ids`、`evidence_ids`、`check_relation_id` 保留；`evidence_status=complete`。
- 页面：第13页表格引用 + 第20页说明引用均在详情中；页面显示“当前值 141.12；应为 165.80；差额 -24.68；程序按已定位事实计算（万元/2位/v3_3）”。
- AI 受控页级调用：S1 输出 2 条跨段矛盾，其中 1 条带双方证据，模型给出 expected/actual/difference（模型声称）。
- 全任务：`review_required`；AI `degraded`（narrative 调用于上游 `server_error` 失败），覆盖 `partial`（2 窗口，4 次调用，1 次失败），保留 3 条 AI 候选、18 条规则发现。系统没有把失败窗口宣称为“无问题”。
- 人工复核：在页面点击“开始复核”，确认 V33-222；页面显示“已确认”，`/api/workflow` 持久化 `status=confirmed`、`actor=admin`、`identity_fingerprint`、`fact_ids/evidence_ids/check_relation_id`；待处理数从 9 降到 8，AI 候选仍为“待人工复核”。
- 导出：CSV 中 V33-222 行含 `expected=165.80`、`actual=141.12`、`difference=-24.68`、`amounts_provenance=program_calculated`、`calculation_basis`、`human_decision=confirmed`。

**基线对比**

- 整改前模型调用：S1 返回 `[]`，数值不一致完全未由 AI 输出；后处理过滤也未触发（因为没有候选）。因此样本1的检出主要依赖规则 V33-222，符合“数值复算由程序负责”的职责划分。
- 整改后：规则仍独立检出；AI 至少能输出跨段矛盾候选，但本次全任务 narratives 调用失败，不能宣称 AI 已完整覆盖。

### 5.2 样本2：上海虹桥国际中央商务区管理委员会（2024 年度部门决算）

**人工真值**

- 第21页完整句：“管委会行政运行费用按实际情况支出。”
- 这是“决算数小于预算数的主要原因：……”的完整分句，不是残句。

**链路结果**

- 全任务：`review_required`；AI `degraded`（narrative 调用于上游 `server_error` 失败），覆盖 `partial`（2 窗口/4 调用/1 失败），10 条 AI 候选、10 条规则发现。
- 未出现 `fragment` 候选；目标句没有被误报为残句。
- 目标句被保留为 AI-DESC-005 的第 5 条跨段证据：`第21页 · 第9项：行政运行`；页面证据引用可见 8 条跨段证据，包含该句完整原文。
- 规则独立抓到“其他行政事业单位养老支出 0.4 万元 vs 0.4 元”的单位错误（V33-222：expected=0.40、actual=0.00004、difference=-0.39996、program_calculated）。
- AI 另给出多项“说明缺少实质性原因”（如“按实际情况支出”“按实报销”“据实结算”），均为 `manual_review` 候选，不是正式问题；这是本次新增的语义覆盖，也包含较笼统的表述，需人工判断，不宣称精确率。

### 5.3 样本3：上海市文化和旅游局 2022 年度部门决算

**人工真值（第29页）**

- “决算市全口径”应为“决算是全口径”（同页第16项有“决算是全口径”）。
- “其他广播影电视管理的支出”应为“其他广播电视管理的支出”（多“影”）。

**链路结果**

- 全任务：`done`；AI `succeeded`、覆盖 `complete`（2 窗口/4 调用/0 失败）；11 条 AI 候选、18 条规则发现。
- 页面“来源与分类说明”显示：“大模型：已完成 · 11 条发现；模型 gpt-6.1-sol；检查覆盖：覆盖完整（4/4 个检查单元）”。
- “决算市全口径”候选：`AI-TEXT-002`、`manual_review`、第29页、第18项、引文完整、定位精确。
- “广播影电视”候选：`AI-TEXT-003`、`manual_review`、第29页、第19项、引文完整。
- 页面候选各一条（去重后）；人工未确认，工作流文件不存在 AI 自动确认记录。
- CSV 导出：两条目标候选均在 `manual_review/ai_candidate` 行，页码 29，含 `check`、`table_or_section`、命中文本。

### 5.4 真实模型调用与费用/用量

- 模型配置：`data/ai-model-settings.json` 当前启用 `检查模型（fast中转）`，`model=gpt-6.1-sol`，`provider_type=openai_compat`，`timeout_s=180`，`max_tokens=4000`。未输出密钥。
- 基线受控页级调用：3 次，14,713 tokens。
- 整改后受控页级调用：6 次（每份两个专项），40,448 tokens。
- 全任务最终验收（每份 2 窗口 × 2 专项 = 4 次，共 12 次）：
  - S1：38,595 tokens；4 次调用，1 次 `server_error`。
  - S2：55,534 tokens；4 次调用，1 次 `server_error`。
  - S3：76,184 tokens；4 次调用，0 次失败。
- 合计真实调用：3 + 6 + 12 = 21 次；合计 tokens：14,713 + 40,448 + 38,595 + 55,534 + 76,184 = **225,474 tokens**。
- 说明：S1/S2 的 `ai_execution.token_usage` 只保留最后一条成功调用的用量；上表为逐次诊断 calls 求和，避免低报。
- S1/S2 的 `server_error` 是上游返回 `server_error`；现有 `AIClient` 的有限重试已执行，未无限重试；系统按 `degraded/partial` 处理，没有把失败窗口当作空结果。

### 5.5 页面与导出证据位置

- 页面截图（本地 scratch，非公开）：
  - `scratch/ai_remediation_20261025/acceptance2/S1_page.png`
  - `scratch/ai_remediation_20261025/acceptance2/S2_target_page.png`
  - `scratch/ai_remediation_20261025/acceptance2/S3_page.png`
- 页面可访问性快照包含：
  - S1：`gbc-review-ai-coverage`、`gbc-review-ai-degraded`、V33-222 详情、第13/20页证据、确认按钮、自动保存。
  - S2：`跨段证据 5 · 第 21 页 · 第9项：行政运行`。
  - S3：`检查覆盖：覆盖完整（4/4 个检查单元）`、`AI-TEXT-002` 第29页详情。
- 导出：
  - `scratch/ai_remediation_20261025/acceptance2/S1_report.csv`
  - `scratch/ai_remediation_20261025/acceptance2/S3_report.csv`
- 任务状态/诊断：
  - `scratch/ai_remediation_20261025/acceptance2/S1_status.json`
  - `S2_status.json`
  - `S3_status_final.json`
- 可回放 trace（仅本地任务目录，0600）：
  - `scratch/ai_remediation_20261025/acceptance2/uploads/<job_id>/ai_audit_trace.jsonl`（含窗口全文、prompt、raw response）

---

## 6. 未覆盖能力、失败项与剩余风险

1. **S1/S2 的 AI 覆盖不完整**：`narrative_consistency` 上游 `server_error`，系统如实 `degraded/partial`。没有通过重跑挑选更好结果；如需完整覆盖，应重新分析或检查上游稳定性。现有重试为有限次，未改成无限重试。
2. **AI 数值不是系统结论**：AI 的 `expected/actual/difference` 始终标 `model_claimed_not_verified`；样本1的差额由 V33-222 程序计算。页面已区分。
3. **文本语义召回/精确率未测总体**：只报告三份标注样本的检出/定位；S2 的“说明缺少实质性原因”与 S3 的额外文字候选包含较笼统或需人工判断的项，未计算总体准确率，也不宣称。
4. **辅助定位支路未在真实全任务中触发**：S1/S2/S3 的定位 `eligible=0`（规则发现已有页码或无可定位文本），因此真实页面没有产生定位调用；辅助定位由单元测试覆盖（page_texts、页码纠正、无文本不调用）。这是“实现并测试”而非“真实任务已验证”。
5. **结构/视觉不由 AI 窗口完成**：缺表缺章、彩色字体、横排等仍由规则/完整文档结构负责或明确不覆盖；`ai_coverage.capabilities` 如实标注。
6. **D-03 仍不是规则全面覆盖**：清单已改为 `ai_assisted/ai_only`，一般多字漏字依赖 AI 语义候选并进入待人工复核。
7. **全量测试 11 项失败**：共享开发库测试组织污染，未清理；本次未修改生产库。
8. **`next build` 页面收集失败**：既有 `/supervision/remediation` 路由 `PageNotFoundError`；类型检查、组件测试、`next dev` 页面通过，但生产构建未通过。
9. **页面状态实测范围**：真实页面实测“部分完成（S1/S2）”与“完整（S3）”；“未启用（not_requested）”“失败（failed）”“无发现（no_findings）”由状态映射/回归测试覆盖，未在本次真实页面逐一构造。
10. **隔离验收数据库仍在**：`gbc_acceptance_20261025`（本地 Docker PostgreSQL）。它不属于生产库；如不再需要，可执行：
    ```bash
    docker exec fiscal_postgres psql -U govbudget_local -d fiscal_db -c 'DROP DATABASE gbc_acceptance_20261025'
    ```
    本次未自动删除，以保留页面/导出证据的可复查性。
11. **未提交收口改动**：当前 3 个文件未提交；第一轮整改已在用户侧 `57abcf86` 基线提交中。若需要形成单一交付提交，应先与用户确认提交范围，避免把既有 PDF 入口脚本/文档混入。

---

## 7. 部署步骤与回滚方案

### 7.1 部署

1. 代码：部署当前分支（含 `57abcf86` 基线 + 当前 3 个未提交文件）。如团队要求单一提交，先确认提交范围；不要 reset 既有基线提交。
2. 依赖：无新增 Python/Node 依赖；`audit_contract.py`、`audit_prompts.py`、`ai_window_builder.py` 为新增模块。
3. 环境变量（可选）：
   - `AI_AUDIT_TRACE=false`（默认关闭；仅受控验收开启，trace 写任务目录，不进入公开 API）
   - `AI_LOCATOR_MAX_FINDINGS=5`（0 表示不调用辅助定位）
   - 现有 `AI_AUDIT_WINDOW_CHARS/OVERLAP/MAX_WINDOWS/MAX_CONCURRENCY`、`AI_MIN_ISSUE_CONFIDENCE` 继续生效。
4. 数据库：**无 schema 迁移**；新字段写入既有 JSON 快照。按现有方式重启 API/Worker，让新代码生效。重启正式服务需用户另行授权。
5. 前端：部署 `app` 修改并重建/重启；`tsc --noEmit` 已通过；生产构建的既有 `/supervision/remediation` 问题需单独处理后再发布。
6. 验证顺序：先跑定向测试（§4.1），再用一份已有人工真值的材料做真实调用，确认页面覆盖/字段/导出；不要以“候选数量增加”作为验收。

### 7.2 回滚

- 代码回滚到 `57abcf86` 或 `8c7afd65`；新字段为可选，旧代码读取既有 JSON 时会忽略，不会因新字段破坏历史任务。
- 无数据库迁移需要回滚；历史 `analysis_results`/`status.json` 未被覆盖（本次只在隔离 `UPLOAD_DIR`/隔离库写入）。
- 如关闭新能力：`AI_AUDIT_TRACE=false` 停止 trace；`AI_LOCATOR_MAX_FINDINGS=0` 停止辅助定位；将 `AI_AUDIT_*` 窗口参数恢复原值即可。
- 隔离验收环境清理：停止隔离 API/前端进程；删除 `scratch/ai_remediation_20261025`；按 §6.10 删除验收库。不得用这些命令清理生产库/`upload`。

---

## 8. 关键文件定位（便于复核）

- 专项提示词：`src/engine/ai/audit_prompts.py`
- 响应契约：`src/engine/ai/audit_contract.py`
- 专项调用/状态：`src/engine/ai/extractor_client.py`（`AuditOutcome` 约 :94、`_run_direct_specialty` 约 :493、`audit_full_report_outcome` 约 :666）
- 覆盖/诊断/去重：`src/services/ai_findings.py`（`_build_coverage` 约 :279、`_build_diagnostics` 约 :373、`_is_boundary_fragment` 约 :1089、`_dedupe_semantic_issues` 约 :1173）
- 边界切窗：`src/services/ai_window_builder.py`
- 辅助定位：`src/services/ai_locator.py`
- 规则程序计算字段：`src/services/engine_rule_runner.py`（`_calculation_fields` 约 :206）
- 解释字段：`src/schemas/issues.py`、`src/services/ai_issue_interpreter.py`
- 候选去重：`src/schemas/review_report.py`（`candidate_ids` 约 :207）
- 覆盖/失败状态：`src/services/ai_execution.py`、`api/main.py`
- 导出列：`api/routes/reports.py`
- 前端：`app/lib/uiAdapters.ts`、`app/app/components/review-workbench/IssueCard.tsx`、`IssuesTab.tsx`
- 回归：`tests/test_ai_chain_remediation.py`（28 条）
- 清单：`rules/checklists/final_checklist_v1.yaml` D-03

---

## 9. 一句话交付结论

本次整改把“AI 是否查、查到什么、证据是什么、为什么被保留/丢弃、是否完成覆盖”从模型自由文本变成了可校验、可追溯、可失败的状态；样本1的差额由规则程序核实，样本2的完整句没有被切窗误报，样本3的两条文字问题进入了待人工复核并可定位/导出。但 S1/S2 的真实模型覆盖因上游 `server_error` 部分完成，结构/视觉能力和生产构建仍有明确未覆盖项，未宣称“全部完成”。
