# 审查质量整改交付说明（fix/audit-quality-remediation-20260905）

> 对应计划：docs/GovBudgetChecker 完整整改计划（2026-09-05）
> 问题基线：docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md（v2，GPT-5.6 复核版）
> 分支基点：feat/ui-redesign-prototype@043579a（批次一证据完整率/回放指标已在此前提交）

## 0. 基线冻结与语料

- 从批次一提交创建分支 `fix/audit-quality-remediation-20260905`。
- `corpus/` 已加入 `.gitignore`（本地受保护语料，不入库）。
- 样张入库：`corpus/DOC-20260905-001/`
  - `sample.pdf`（SHA-256 `113b98bb…f912c7`，见 golden.json）
  - `sys_findings_baseline.json`（系统原始 52 条结果）
  - `sample_pdf_text.txt`（全文逐页文本）
  - `ANNOTATIONS.md` + `golden.json`（人工标签：defect×3 / rounding_hint×2+1 / manual_review×1 / acceptable 负例×1）
- `scripts/replay_analysis.py` 职责不变：只统计历史旧产物，不作为当前规则回归。

## 1. P0 假完成与配置修复（commit 91c0508）

| 缺陷 | 修复 |
|---|---|
| `Settings.get("dual_mode.enabled")` 点号误用，恒取 False，被字典式 mock 掩盖 | `api/main.py` 改用真实接口 `settings.is_dual_mode_enabled()`；测试改 patch 该接口，禁用字典式 mock |
| legacy + `use_ai_assist=true` 被静默忽略（样张"AI 未运行"根因） | 请求契约：mode ∈ {legacy, dual, structured}；legacy/structured 不请求 AI，显式 `use_ai_assist=true` → **422**；legacy 必须启用本地规则；dual 默认请求 AI。旧任务残留标志如实记 `not_run` 并转复核 |
| AI 执行不可观测 | 新增 `result.meta.ai_execution`：`requested/state/attempts/provider/model/prompt_version/finish_reason/token_usage/error_code`，五态 not_requested/not_run/succeeded/degraded/failed；ExtractorClient 调用留痕（call_ledger），analyze_dual 成功也记 provider_stats |
| 请求 AI 后未成功仍可 done | 质量门：请求 AI 后**只有 succeeded** 可通过；not_run→`ai_not_run`，failed/degraded（超时/空响应/finish_reason=length 截断/全 provider 失败）→`ai_failed` |
| 结构化缺表/表歧义/证据不完整/规则异常/报告类型错分被丢弃 | 质量门新增原因码：`missing_required_table`、`ambiguous_table_schema`、`rule_evidence_incomplete`、`rule_execution_error`、`report_type_mismatch`、`rules_not_executed`（no_findings 门禁） |
| `_normalize_report_type` 把 dept_final 错写 BUDGET | 补 dept_final/unit_final/dept_budget/unit_budget 等映射；未知类型返回 None → ps_sync 跳过入库转复核，**不再默认 BUDGET** |
| no_findings 口径 | 仅在规则全部执行 + 解析合格 + 请求的 AI succeeded + 证据门通过时产出 |
| 新增 meta 增量字段 | `ai_execution`、`parser_quality`、`rule_execution_summary`；原 provider_stats/ai_error/issues/merged 保留 |

## 2. 高频规则止血 + 说明归因（commit 339171d）

- **CMM-004**（33 条误报）：科目域隔离——收入/功能分类/经济分类互不可比时 `not_applicable`（RuleOutcomeSignal）。
- **CMM-002**（4 条）：软换行合并 + 段落级引号配对；`src/utils/narration.py` 为共享实现。
- **V33-001**：年份冲突只查封面/目录/表头（结构位置），同比/上年语境排除；新增「年度缺位」检测（`202 年度` 三位数字占位残留）→ 命中 T1/P2。
- **V33-115**：双栏总表同侧取值 + None 安全 + 舍入包络；分量缺失时跳过该侧校验，不伪造 0.0。
- **V33-117**：经济分类表类级/款级行区分（第二格为 1~2 位数字→款级），显式合计与标签同半行取值；类级=Σ款级包络校验 → T4/P15 info。
- **V33-120**：跨页列宽逻辑列重映射（`src = col + (row_len - modal_width)` 对齐尾部金额列）+ 语义表头列（合计/基本支出/项目支出）+ None 安全层级校验 + 列合计校验（→T2/P10 info）+ 跨表同口径近似差异（→T3/P10↔P14 manual_review）。
- **V33-106/110/220/244**：说明归因重构——`merge_page_texts` 软换行恢复 → `split_numbered_sections` 章节切分 → `split_clauses` 分句配对；`extract_amounts` 只认"数字+单位"，年份 token 自动排除；三公事实抽取主体沿分句继承、基数句/变化句分离。
- **新增规则**：`V33-245`（三公说明同主体"减少/增加"与"持平"逻辑矛盾，T5/P26 命中）、`V33-246`（国内公务接待批次/人次披露完整性，T6/P27 命中）。
- **RuleOutcome 六态**（`src/engine/rule_outcome.py`）：pass/fail/not_applicable/insufficient_data/parse_error/execution_error；`RuleDeferred`/`RuleNotApplicable` 继承 BaseException 穿透规则内 except Exception 兜底；只有 fail 生成 finding；legacy 与 dual 引擎路径（engine_rule_runner）都接入执行摘要。

## 3. 统一结构化解析（阶段 3）

- `src/engine/structured_rules.py`：与数据库无关的内存解析模型。
  - `ParsedCell`：number(Decimal)/text/bbox 三态，文本单元格保留 None；
  - `ParsedTable`：table_code/page_span/named_columns/column_group/canonical_measure/classification_type/row_role/confidence；
  - `materialize_table`（含双栏建模）、`merge_compatible`（跨页合并守卫+parse_error 记录）、`check_parent_children`（包络封装）；
  - 首批迁移规则 V33-115/117/120/202/203/220/241/243/244 经适配器委托修复版实现（单一实现两处消费）。
- `src/engine/amount_math.py`：金额统一 Decimal，父项与 n 子项显示舍入包络 `(n+1)×0.005 万元`；包络内→`rounding_hint`（info），超出→`mismatch`。
- legacy/shadow/structured 三态：`scripts/replay_golden_corpus.py --parse-mode`（legacy/structured/shadow 对比逐规则差异）；管线本身无解析模式开关，恒走 legacy（2026-09-06 K3 复核后修正表述：此前所写「经 `rules.input_mode` 配置位预留」与代码不符，全仓无该配置位；真正的运行时切换留待结构化迁移完成后再实现）。

## 4. AI 与环境修复（阶段 4）

- `AI_AUDIT_MAX_TOKENS` > `AI_MAX_TOKENS` > 应用配置（config/app.yaml ai.max_tokens）；写死 3200 已移除。
- 空正文、`finish_reason=length`、推理耗尽均按失败处理（不解释为"未发现问题"）。
- Makefile 移除 `dev-local-key` 默认 key 注入；`scripts/dev.cjs` 移除最后兜底默认 key；三者统一从根 `.env` 读取。认证开启但 key 缺失时后端安全模块直接拒绝启动（fail-closed，既有逻辑）。
- `tests/conftest.py` 新增 autouse `isolate_ai_and_secret_env`：统一摘除 AI_*/OPENAI_*/GEMINI_*/ZHIPU_*/DEEPSEEK_* 前缀与 AI_FALLBACK_CHAIN/GOVBUDGET_API_KEY 等；真实 AI 测试通过专用 `GOVBUDGET_TEST_AI_*` + `real_ai_env` fixture 显式启用。

## 5. 评测契约与脚本

- `scripts/replay_golden_corpus.py`：用当前解析器+规则真实重跑语料 PDF；只写 `outputs/golden_replay/`。
- `scripts/evaluate_golden_corpus.py`：对 `corpus/<DOC-ID>/golden.json` 计算 TP/FP/FN、precision/recall、严重度/页码准确率、证据可定位率、acceptable 负例违规；只写 `outputs/golden_eval/`；两脚本绝不改历史任务目录。
- 标签四类：defect / rounding_hint / manual_review / acceptable，含 annotation_id、rule_id、page、location_key、expected_severity、evidence、confidence、reviewed_by。

## 6. 样张验收结果（DOC-20260905-001）

| 验收项 | 要求 | 实测 |
|---|---|---|
| 旧 52 条误报清零 | 0 | **52 → 7 条，0 假阳性**（CMM-004×33、CMM-002×4 全消） |
| T1 目录年度缺位 | V33-001/P2 命中 | ✅ high P2 |
| T5 三公逻辑矛盾 | V33-245/P26 命中 | ✅ medium P26 |
| T6 国内接待披露缺失 | V33-246/P27 命中 | ✅ medium P27 |
| T2 表内合计差 0.01 | info | ✅ V33-120 info P10 |
| T3 两表同口径差 0.01 | manual_review | ✅ V33-120 manual_review P10 |
| T4 310 行舍入 | info | ✅ V33-117 info P15 ×2 |
| T7 绩效口径（负例） | 无 finding | ✅ P28 无任何 finding |
| 主要勾稽反证 | 全部 pass | ✅ rule_execution_summary: pass=52, not_applicable=1 |
| 证据可定位率 | 100% | ✅ 1.0 |

评测报告：`outputs/golden_eval/DOC-20260905-001-eval-*.json`（GATE-PASS，precision=1.0 recall=1.0）。

## 7. 回归基线

- 后端 pytest：**1004 passed + 1 skipped**（2026-09-06 GPT5.6 复核整改后；演进：整改前 873+1 → 941+1 → K3 重跑 945+1 → K3 整改 990+1 → 本轮含真实 ledger 链路/质量门缺口/合并守卫矩阵 1004+1，零回归）。
- 前端：18 个 jiti 单测套件全过；`npm run build` 成功。
- E2E：**137 passed**（清理残留 dev server 后全过，1.2 分钟）。
- 历史回放门禁：`scripts/replay_analysis.py` 职责未动；新 `replay_golden_corpus.py` 负责当前规则的真实重放。

## 7.1 历史 PDF 无 AI 当前规则重跑（逐规则数量变化报告）

`python scripts/replay_golden_corpus.py --historical --workers 6`（只读 uploads/，只写 outputs/）：

- 扫描 346 个历史任务（348 份 PDF），339 份有旧规则基线可比，7 份无基线排除；
- **双层聚合**：`consistent_routing`（新旧 report_kind 一致，delta 纯粹来自规则逻辑修复）与 `routing_changed`（历史代码版本对 unknown 的路由行为与当前不同，单独列出，不算本次回归）；
- 同口径净变化：**removed 342 / added 78**。主要减少：CMM-004 −169（13 docs，科目域修复）、V33-120 −55（跨页列重映射）、BUD-001 −37（3 docs，待人工抽查）、V33-115 −18、V33-110 −18、CMM-002 −13、V33-220 −10、V33-001 −8、V33-106 −6；主要增加：V33-246 +11 / V33-245 +2（新规则按设计命中）、V33-235 +14 / V33-005 +10 / V33-002 +9（抽查 V33-235 为真实的说明-金额不一致 warn，属当前规则更全面，待人工复核确认）；
- **变化最大的 30 份文档清单**已输出（top_changed_docs_for_manual_review），样张 job `303e3d95…` 名列第一（52→7）；
- 报告：`outputs/golden_replay/historical-rules-delta-*.json`。

## 7.2 FIN_05 识别缺口修复

根因：`detect_table_code` 中泛别名（FIN_03「支出决算表」）靠 `fuzz.partial_ratio` 在样张 P14 标题「一般公共预算财政拨款支出决算表」上拿满分，与精确别名 FIN_05 同分，先注册者胜 → FIN_05 缺失。且 `exact_alias_coverage` 分母被 86 字表头拼接稀释。

修复（`src/services/fiscal_table_rules.py`）：
1. coverage 按别名实际命中目标计算（命中标题用标题长度做分母，不再被 hint 稀释）；
2. 高分平局（Δscore<0.02）时按 `exact_alias_coverage` 特异性 tie-break，更特异别名胜出。

修复后样张离线验证：P8→FIN_02、P10→FIN_03、P12→FIN_04、**P14→FIN_05**、**P15→FIN_06** 全部正确；短标题「支出决算表」仍归 FIN_03（tie-break 不反向误伤）；识别相关 50 个既有测试零回归。

## 7.3 受影响规则专项测试矩阵

`tests/test_rule_matrix_20260905.py`（33 例）：逐规则覆盖正例/反例/软换行/双栏/跨页/舍入边界/解析不足——CMM-002（软换行配对反例+真未闭合正例）、CMM-004（同域正例+域混杂 not_applicable）、V33-001（T1 年度缺位+同比反例+结构性冲突）、V33-106/110/220/244（年份 token、跨句错配反例、真不一致正例、软换行断字+同比句）、V33-115/117/120（双栏、跨页窄行重映射、0.01 舍入 info、解析不足不产 0.0 假 error、真实错误）、V33-245/246 正反例、detect_table_code tie-break 三例。

## 7.4 真实 AI 调用留痕实测

用 .env 真实凭据走 ExtractorClient 完整链路（密钥不落盘，报告只记 provider/model/error/token 存在性）：
- 当前凭据状态：main/locator TPM 耗尽、gemini_main/gemini_locator 模型 404、codex_backup 鉴权失败、extractor_service 502——**全部 provider 失败**；
- 状态机实测输出：`state=failed`、error_code 传播、每次尝试均有 call_ledger 留痕、`gate_pass=False` → 任务必然 `review_required + incomplete`；
- **fail-closed 路径实证成立**；succeeded 路径由单测合成 ledger 覆盖，需凭据配额恢复后重跑留痕脚本（报告 `outputs/ai_execution_trace-*.json`）。

## 7.5 AI 输入表征增强（阶段 4 收尾）

- `src/services/ai_input_builder.py`：`build_structured_context(page_texts, page_tables)` 产出注入块——表格关系（表题/页码/列语义/合计行/明细样本/解析风险，来自与规则同源的 `materialize_table`）+ 说明金额事实（narration 归因抽取，年份 token 排除）；长度预算截断并如实标注；
- 注入链路：`AIFindingsService.analyze` 构建一次 → 每个审计窗口随 prompt 注入 `_direct_semantic_audit`；注入块显式声明「**金额勾稽以确定性规则为准，模型只负责语义候选，不负责金额复算**」；
- 边界测试（`tests/test_ai_input_builder.py`）：上下文含表题/页码/金额/边界声明、prompt 携带注入块、注入后金额勾稽仍由规则层负责（R33115 不受影响）；`fake_audit` 桩同步新契约参数；
- 真实样张产出：全文档上下文 3342 字符（14 条表格关系 + 25 条说明事实）。

## 7.6 ps_sync 端到端入库验证（本地 fiscal_db）

`run_structured_ingest` 对样张 DOC-20260905-001 实跑（metadata doc_type=dept_final, report_year=2025）：
- status=done，tables_count=17，**recognized_tables=9（九张表全部识别，含 FIN_05_general_public_expenditure）**；
- **missing_core_table review items 为空**（FIN_05 缺表信号消失）；
- **ps_sync.status=done，ps_sync.report_type=FINAL**（dept_final 正确映射）；
- facts_count=470；报告 `outputs/structured-ingest-verify-*.json`；
- 验证数据已清理（document_version_id=220：470 facts + 9 instances + 1594 cells + 版本/文档/report 级联删除；org_unit/org_unit_ref 无其他引用者删除，department 因存在其他引用保留）。

## 8. 发布与回滚

- 解析三态：管线恒走 **legacy**（本次止血与归因修复在 legacy 路径内生效，属缺陷修复而非行为切换）；structured/shadow 仅 replay 脚本可比较，切换到 structured 解析需改代码并另行评估（当前无运行时开关，见 §3 修正表述）。
- 回滚可切回 legacy 解析（解析路径本轮未改，无需回滚），但 AI 真实性状态、严格报告类型映射、fail-closed 质量门、布尔参数真值归一（§7.7）不得回退（安全修复）。
- 本轮无新增第三方依赖、无数据库结构迁移、无 UI 重构。

## 9. 遗留与后续

1. Golden Corpus 扩容至 ≥20 份并完成双人复核后，启用 P0 召回≥98%/精确≥95%、P1 召回≥95%/精确≥90% 正式门禁（`evaluate_golden_corpus.py` 的 check_gates 已具备）。
2. dual 模式真实 AI 链路（成本/超时/provider 回退）需在测试/预发用 `GOVBUDGET_TEST_AI_*` 显式验证。
3. `ps_sync.report_type` 已错分的历史数据需要一次性修正（本轮未动历史数据）。
4. V33-202/203 等表间规则的完整结构化迁移（named-column 驱动）可按 structured_rules.py 的适配器模式继续推进；迁移覆盖率达到可切换水平时再实现管线级解析模式开关。

## 9.1 K3 复核整改（2026-09-06）

外部 K3 独立核查（13 提交全量复核 + 独立重跑）确认整改主体成立，另指出 2 处代码遗留与 1 处文档失实，本轮已全部修复：

1. **`use_ai_assist`/`use_local_rules` 真值归一**（`api/runtime.py`）：新增 `normalize_request_flag`——bool 原样；字符串 `true/1/yes/on`、`false/0/no/off`（忽略大小写空白）归一为对应真值；其余取值 422（fail-closed）。修复两个缺陷：legacy/structured 下字符串 `"true"` 此前绕过 422 冲突拦截、被静默当 False 持久化；dual 下 `bool("false")` 恒真、字符串 `"false"` 被当成请求 AI。契约矩阵测试（`tests/test_audit_quality_p0.py` §2.1）：字符串真值 422、字符串假值/真值持久化类型与值、legacy 字符串假值关停规则 422。
2. **`ai_semantic_audit` 回退透传 `structured_context`**（`src/engine/ai/extractor_client.py`）：签名增加可选 `structured_context`，两处 `_direct_semantic_audit` 回退（空结果/异常）与 `ai_full_report_audit` 的抽取服务回退均透传注入块——同一文档在主链路与任意回退层级输入表征一致。透传矩阵测试（`tests/test_ai_extractor_client.py`）：直连失败→抽取服务回退、抽取服务空结果/异常→直连回退、无注入块（历史调用形态）三种路径的透传契约。
3. **交付文档表述修正**：§3「管线内开关经 `rules.input_mode` 配置位预留」与代码不符（全仓无此配置位），已改为如实描述「管线恒走 legacy，运行时切换待结构化迁移完成后实现」；§8 同步修正。

验证：`tests/test_audit_quality_p0.py` + `tests/test_ai_extractor_client.py` 97 passed；全量 pytest 见 §7 基线更新。

## 9.2 GPT5.6 复核整改（2026-09-06）

外部 GPT5.6 独立审计（对着「完整整改计划验收」口径，结论：样张治理 GO / 整体验收 NO-GO）指出 3 个 P0、2 个 P1、1 个 P2，经逐项核实全部属实，本轮已全部修复：

### P0-1 AI ledger 数据契约不一致（真实成功调用被判失败）
`record_call` 只落 `content_length`，而 `ai_execution._call_succeeded` 要求非空 `content`——dual 模式真实成功调用必被判 `state=failed / ai_empty_response`，无法进入 succeeded；既有测试手工构造带 content 的 ledger 掩盖了该不一致。修复：ledger 同时落 `content`（状态机判定证据）与 `content_length`（展示冗余）；content 含材料原文，仅进程内状态机消费、pop 后不外带。新增真实链路测试 6 例（`record_call → pop_call_ledger → build_ai_execution`）：成功/空数组正文→succeeded、空正文→ai_empty_response、截断→ai_truncated_response、异常→failed、抽取服务 hits→succeeded。

### P0-2 质量门假绿 no_findings 三缺口
1. **缺数据规则静默记 pass**：V33-115/117/119/120/121 在表缺失/行宽无法判定时 `return []` 被 pipeline 记 pass。修复：改抛 `RuleDeferred`（insufficient_data），V33-120 三表全缺同样处理。
2. **门禁缺口**：`_evaluate_quality_gate` 新增 `rules_insufficient_data` 原因码；`rule_execution_summary` 缺失/为空时无发现不再允许 no_findings（`rules_not_executed` 兜底）；`fact_materialization_empty` 从"仅标 parser_quality=poor"升级为独立 review reason 阻断完成态。
3. 旧测试以「无摘要+无发现→done」为期望的 4 个用例随契约收紧更新（test_quality_gate / test_evidence_completeness / test_pipeline_stage_progress_integration / test_pdf_parse_isolation_and_backup 的规则桩补齐 build_issues_payload 契约的 summary 字段）。

### P0-3 structured 路径诚实化
1. **merge_compatible 只记错不处置**：列宽不一致时直接 append（错位合并不重映射）。修复：新增 `_remap_continuation_rows`（尾部对齐显式重映射，与 V33-120 运行时 shift 同源），有共同语义列时重映射后合并并留 `continuation_width_remapped`；无共同语义列时拒绝合并（宁可少合并也不错位合并）。新测试 `tests/test_structured_merge_rules.py` 6 例锁定契约。
2. **structured_ready 误导**：此前"迁移列表非空即 ready"（9/52 条≈17% 也报 true）。修复：以迁移覆盖率判定（`STRUCTURED_READY_MIN_COVERAGE=0.9`），如实输出 `structured_ready=false, structured_coverage=0.1731` + ready_note 说明。§7.1 历史回放的 `routing_changed_docs` 字典重复键（计数被列表覆盖）一并修复，拆为 `routing_changed_top_docs`。
3. 需要说明：structured 与 legacy 的两套表格模型统一、`FiscalFactMaterializer` 去数据库依赖、跨页列重映射的运行时消费，属宏观《完整整改计划》Phase 3 的未完成项，不在本轮修复范围（见 §9 遗留 4）。

### P1-4 Golden 评估器只按 rule+page 匹配
"规则和页码正确但正文完全无关、evidence 为空"的 finding 也会被判命中。修复：replay 序列化带全量 `message` 与 `evidence_text`（统一 `_serialize_finding`）；评估器 `match_annotation` 增加内容重叠校验（数字 token 交集优先，其次 ≥6 字归一化片段，重叠度最高者优先消费）。实测：GPT5.6 的复现场景（无关正文 finding）被正确拒绝；样张 replay+evaluate 重跑 GATE-PASS（TP=3 FP=0 FN=0，全指标 1.0）。

### P1-5 认证密钥兜底三处不一致
`package.json` dev:backend 与 `scripts/next-dev.cjs` 各自硬编码 `dev-local-key`、`app/lib/backendAuth.ts` 兜底 `change_me_to_a_strong_secret`——分别启动前后端时与后端实际 key 不一致会静默 403。修复：三处兜底全部移除，统一从根 .env（GOVBUDGET_API_KEY / BACKEND_API_KEY）解析，缺 key 显式失败（fail-closed，与后端安全模块行为对齐）。已核实根 .env 与 app/.env.local 的 key 一致；localAuth.ts 中 `change_me_to_a_strong_secret` 属弱密码黑名单（防呆），保留。

### P2-6 工程收尾
Ruff 两错修复（`replay_golden_corpus.py` Tuple 未导入、routing_changed_docs 重复键）；全部改动随本轮两个 commit 提交，不再留未提交工作树。（勘误：实际为单个 commit a7a87cb——两轮整改在同 3 个文件交织，按文件拆分会破坏原子性。）

### 9.3 /review 自查修复（2026-09-06，同日第三轮）
对 a7a87cb 做交叉复查（RuleDeferred 穿透路径排查、no_findings 兜底误伤排查、评估器真实语料逐条验证），确认无阻断回归，另修复 3 个自查发现：
1. **🟡 evidence_overlaps 短标注盲区**（`evaluate_golden_corpus.py`）：滑窗固定 6 字，短于 6 字且无数字的标注（如「空表」）永远匹配不上。修复：窗口退化 `min(6, len)`，短标注按全串比对。回归测试 `tests/test_golden_eval_overlap.py` 8 例（短标注正反例、单字退化、数字交集优先、无关正文仍拒绝、优先消费重叠度最高者）。
2. **🟢 ledger 跨任务残留**（`ai_findings.py`）：content 含材料原文的留痕若因异常路径漏 pop 会跨任务累积、污染下次 ai_execution 判定。修复：`analyze` 入口防御性丢弃残留留痕并告警；测试覆盖（`test_ai_issue_bbox.py` 新增残留清理用例）。
3. **🟢 engine_rule_runner 死代码**：`_execute_rule`/`_convert_issue_to_item`/`_analyze_failure_reason`/`EngineRuleResult` 无任何调用方，且 `_execute_rule` 用 `except Exception` 接 RuleDeferred（与活跃路径的信号捕获语义相反，误复用会把 insufficient_data 记成 execution_error）。全部删除（-110 行）。

### 验证（2026-09-06）
- 全量 pytest：**1013 passed + 1 skipped**（9.2 轮 1004 → 本轮新增 9 测试，零回归）；
- 前端 18 个 jiti 套件全过；
- replay+evaluate 重跑：GATE-PASS（TP=3 FP=0 FN=0、hint 4/4、severity/page/locatable 全 1.0）；
- shadow replay：`structured_ready=false（coverage 17.3%）` 如实标注，legacy 7 条/structured 4 条差异逐规则列出；
- Ruff：涉及文件 All checks passed；
- e2e 137 项未重跑（前端仅改 backendAuth.ts 移除兜底 key，本地 .env.local 已配置一致 key，行为不变；`test:e2e` 包装命令的 output/e2e-webserver.log EPERM 为已知环境问题，直连临时服务可全过——GPT5.6 本轮已实测 137 passed）。

## 9.4 GPT5.6 第二轮复核整改（2026-09-06，同日第四轮）

第二轮复核指出 5 项剩余问题（1 P0 / 3 P1 / 1 P2），经逐项核实全部属实，本轮修复情况：

### P0-1 结构化解析消费链路（部分完成，试点打通）
1. **续表合并 key 永假 bug 修复**：原实现 `key=f"P{页}:{累计表数}"` 累计数只增不减 → key 永不重复 → 续表从不合并（实测 `keys=['P1:0','P2:1']`、`merge_calls=0`）。重构为 `build_parsed_tables()`：按**内容签名**识别续表（语义列交集/列宽对齐/表头文本），走 `merge_compatible` 守卫。跨页合并、不同表种隔离、三页链式续表测试锁定。语义列判定发现并修复「收入决算表/支出决算表仅共 total 被误判续表」——区分度列（basic/project/budget/final）或 ≥2 列交集才判同表。
2. **V33-115 真实消费试点**：规则挂载 `doc.parsed_tables` 时优先走 `_apply_structured`（从 ParsedRow/ParsedCell 三态取数，文本单元格不误读 0.0），无挂载回退 legacy 文本行——第一条真实结构化消费链路落地。测试覆盖：结构化路径产出平衡错误 finding、平衡表零误报、无挂载走回退。
3. **顺手修复** `_get_table_rows` 锚点页越界 IndexError（纯文本材料锚点来自全文但 page_tables 为空时崩溃）。
4. **未完成（如实记录）**：其余 8 条迁移规则仍走 legacy 路径；生产管线仍恒走 legacy（`run_rules_in_process`）；`FiscalFactMaterializer` 的数据库依赖未移除。这是宏观计划 Phase 3 的剩余工作，见 §9 遗留 4。

### P1-2 评估器假 TP 与证据质量
1. **区分度数字 token**：年份（19xx/20xx）与孤立两位数不再单独构成内容证据——GPT5.6 复现场景（同规则同页、内容相反、仅共享"2025"）实测被拒绝。金额/编码 token 仍是一级证据；4-6 字文本片段为措辞差异通道；2-3 字超短标注按全串包含兜底。
2. **location_key 纳入匹配**：锚点短语（按括号/省略号切分）计入排序加分（数字交集 > 锚点命中 > 文本片段）。三轮调参的结论如实记录：锚点做**否决**会在「标注行级措辞 vs 规则文案措辞交叉」场景误拒真命中（A-005「p14同口径表合计」vs finding「同口径列」等实测），收敛为排序项——假 TP 防线由区分度 token 通道承担，定位域一致性用于多候选择优。
3. **locatable 双证据**：可定位率从"仅页码"升级为"页码 + 非空 evidence_text/message"，缺项进入 `unlocatable_findings` 明细。样张实测 7/7 双证据齐备，locatable 1.0 保持。

### P1-3 抽取服务合法空结果契约
`_call_semantic_audit` 空命中（hits=[]，服务 200）时 ledger 落盘 `"[]"`（非空字符串构成成功证据）而非 `""`——与直连路径返回 `"[]"` 判成功的契约一致。修复前：直连失败 + 抽取服务成功且无发现的任务会被误判 `ai_empty_response` → failed/review_required。契约测试 2 例（含 mixed 失败后成功的组合场景）。

### P1-4 正式验收数据（未完成，如实记录）
Golden Corpus 仍为 1 份（目标 ≥20 份双人复核）；历史 top30 仅清单无逐份人工结论；真实外部 AI provider 成功链路未预发验证。均属计划内遗留，非本轮代码缺陷。

### P2-5 两个自动化入口
1. **replay 脚本 GBK 崩溃**：Windows 控制台默认 GBK，打印含特殊字符的文件名触发 UnicodeEncodeError 退出码 1。两个脚本（replay/evaluate）入口 `sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`。实测 exit 0。
2. **run-e2e.cjs EPERM**：`output/e2e-webserver.log` 被残留进程占用时 openSync 直接抛 EPERM、包装命令未启动即失败。改为失败回退时间戳文件名 + 提示清理（node 单测验证回退生效）。

### 验证（2026-09-06 第四轮）
- 全量 pytest：**1025 passed + 1 skipped**（第三轮 1013 → 新增 12 测试：build_parsed_tables 6 + V33-115 消费 3 + 空 hits 2 + 评估器语义更新，零回归）；
- replay+evaluate：**GATE-PASS**（TP=3 FP=0 FN=0、hint 4/4、全指标 1.0）——评估器强化后真命中无一误拒（三轮调参过程中曾出现召回 0.33/0.67 的过度收紧，均已在收敛设计下消除）；
- shadow replay：structured 路径经 P0-1 修复后行为不变（4 条 finding、`structured_ready=false` 17.3%）；
- Ruff（api/src/scripts 全目录）：All checks passed（本轮引入的 2 个新错误 F841/B905 已修）；
- node --check：run-e2e.cjs / next-dev.cjs / dev.cjs 全过。

## 9.5 GPT5.6 第三轮复核整改（2026-09-07，基于 78fbe40）

第三轮复核指出 2 P0 / 3 P1，经**官方样张真实 PDF 复现**全部属实，本轮修复：

### P0-1 build_parsed_tables 误合并与静默丢表（样张实测 14 表→4）
R2 版实现的三个缺陷在真实 PDF 上全部复现：`last_key` 不限相邻（支出决算表 span 竟达 (10,19)、10~19 页被吞并）；签名兼容但 merge_compatible 拒绝时表被静默丢弃；同页同列结构独立表被合成一张。重写为三条硬语义：**合并候选限定物理相邻**（同页或紧邻页，隔页/隔表不合并——续表在排版上必然连续）；**拒绝合并时无条件保留独立表**（宁可多表不丢表）；key 唯一性由页+序号保证。样张实测 14→9 张逻辑表（GPT5.6 预期约 10），所有 span ≤3 页、合并后行数 ≥ 原始行数（零丢表）。

### P0-2 V33-115 结构化试点未在样张生效（target_found=False）
根因：pdfplumber 的表格 bbox 不含表标题行——样张总表的 title 实为表体首行「收入支出」，表名「收入支出决算总表」在页文本里，title 片段匹配必然失败。修复：`_find_parsed_table` 两级匹配（title 片段 → 表起始页的页文本锚点，与 legacy `_get_first_anchor_page` 同源）。样张实测：target_found=True（span 7-8、51 行），`_apply_structured` 真实取到总计两侧 4733.14（平衡 → 0 finding 是正确结论，与 legacy 一致）。

### P0-2b structured 覆盖率诚实化
`STRUCTURED_MIGRATED_RULES`（适配器登记 9 条）之外新增 `STRUCTURED_PARSING_CONSUMERS`（真正消费 parsed_tables 的规则，当前仅 V33-115）。shadow 报告的覆盖率分子改为后者：**1/52 = 1.9%**（此前按登记数报 17.3% 是误导——其余 8 条输入仍是 legacy 表征）。ready_note 如实区分两级口径。

### P1-3 数字科目编码被当金额
`_cell_from_raw` 把 "301" 存为 number=301、text=None，`_row_code` 只读 text → code=None、分类域判断失效。修复：`_row_code` 对 number 为整数的单元格检查其数位形式（3/5/7 位）是否匹配编码位长。样张总表的首列「类款项」合并编码（Decimal 形态）现在可正确提取。

### P1-4 location_key 升格为条件约束（取舍有数据支撑）
GPT5.6 复现场景（唯一候选来自其他章节）实测仍匹配——但样张真值数据显示 **A-003/005/006/008 四条真命中的锚点短语零命中**（标注者行级措辞 vs 规则文案交叉），「全零命中即拒绝」会把样张召回打到 3/7。收敛为**条件约束**：锚点可区分（存在命中候选）时淘汰未命中者（挡住「错域与正域并存」的假 TP）；锚点零命中（措辞交叉）时降级纯证据排序（不误拒真命中）。GPT5.6 构造的「其他章节」文案在真实规则产出中不存在，该场景由排序语义承担。

### P1-5 locatable 证据强化
文本证据从「evidence_text 或 message」收紧为**仅非空 evidence_text**（规则侧的原文引文：表名/金额/标签行）——message 是规则生成的文案不是证据。样张 7 条 finding 的 evidence_text 全部非空，locatable 1.0 保持。

### 真实 PDF 集成测试（GPT5.6 R3 明确要求）
`tests/test_structured_sample_integration.py` 3 例：① 样张 14 张原始表 → 8-12 张逻辑表、零丢表、span ≤3 页；② V33-115 两级匹配命中总表且 `_apply_structured` 走通（span/行数/结论断言）；③ 结构化与 legacy 在样张上结论一致。

### 验证（2026-09-07 第五轮）
- 全量 pytest：**1032 passed + 1 skipped**（第四轮 1026 → 新增 6 测试，零回归）；
- replay+evaluate：**GATE-PASS**（TP=3 FP=0 FN=0、hint 4/4、全指标 1.0）；
- shadow replay：`structured_ready=false`，覆盖率按真实消费口径 1.9% 如实标注；
- Ruff 全目录通过。

### 仍未完成（如实记录，与 R3 结论一致）
- 其余 8 条适配器规则的结构化消费迁移（`_apply_structured` 复制到 117/120/202/203/220/241/243/244）——**这是 Phase 3 的主体工作，必须在 build_parsed_tables 数据正确的基础上进行**（本轮已修复基础）；
- 生产管线仍恒走 legacy（`run_rules_in_process`）；
- FiscalFactMaterializer 数据库依赖未移除；
- Golden Corpus 仍 1 份（目标 ≥20）；历史 top30 无逐份人工裁决；真实 AI provider 成功链路未预发验证。

## 9.6 GPT5.6 第四轮复核整改（2026-09-07，基于 2183b89，用户确认开工顺序）

第四轮复核 5 项指控全部复现属实并修复。本轮方法论修正：先在官方样张复现、再改代码、再以样张真值重新固化断言（R3 的教训——断言固化错误合并）。

### P0-1 表名锚约束（跨表种合并清零）
R3 版「物理相邻」挡不住相邻页的不同业务表（样张实测 P7 总表+P8 收入决算表合并为 (7,8)、P15-17、P18+19 同样误并）。R4 收敛语义：**每张新建表从起始页页文本提取独立表名行**（anchor_table_name，样张形态：表名行在页文本、不在表格 bbox 内）；合并候选必须满足三层约束——①当前页出现新表名行即无条件禁止合并（物理翻表）；②基准表有表名、续页无表名（续页不重复表名）→ 同表候选交签名守卫；③双方都有表名但不同 → 禁止。样张实测：**14 张原始表 → 12 张逻辑表**（10 个表名各归各位 + P9/P11 两个无表名续页保守独立），真续表（P12→13 财政拨款总表、P15→16 基本支出表）正确合并，**跨表种合并清零**。

### P0-1b 集成测试断言修正（R3 固化的错误被纠正）
`test_structured_sample_integration.py` 逐表对照页文本表名清单断言（收入支出决算总表 (7,7)——R3 曾错误断言 (7,8)）；V33-115 断言升级为数据级：总计行两侧必须取到 Decimal 4733.14（结论由数据支撑，GPT5.6 指出的「碰巧一致」被消除）。

### P1-2 科目编码识别限定编码列
`_row_code` 对 number 形态（PDF 抽取把 "301" 存为 Decimal）的编码识别**只认表头确认的「科目编码/功能分类/经济分类」列**（named_columns 新增 code 语义列）；无编码列信息时数字形态不识别（金额恰好 3/5/7 位整数如 "301" 万元不再误判科目，text 形态不受限）。双向测试：金额 301 → code=None；编码列 301/30101 → code 正确。

### P1-4 历史回放 parse_mode 真执行 + 检查点隔离
`replay_historical_doc` 此前无条件 run_legacy_rules（报告标 structured 实跑 legacy——虚假验证通道）。修复：structured 模式真跑迁移规则集、shadow 模式双侧留痕（shadow_structured 字段）、legacy 保持全量。**连带发现并修复检查点缓存污染**：resume 检查点文件不区分 parse_mode，structured 报告会合并 legacy 全量缓存（实测 processed=333 而 jobs_total=3、total_rules 6/22/58 混杂）——检查点文件按 parse_mode 隔离 + 缓存 parse_mode 校验双保险。修复后 structured smoke：5 份历史任务全部 total_rules=9 真实执行。

### P1-5 CI 固定夹具 fail-closed
新增 `scripts/build_sample_fixture.py`：样张解析产物（page_texts+page_tables，公开决算材料）序列化为 `tests/fixtures/sample_page_data.json` 入库（55KB，含源 PDF SHA-256 溯源）。集成测试改造为 **fail-closed**：夹具缺失即测试失败（不再 skipif 静默跳过）；本地有真实 PDF 时 SHA 交叉校验（夹具过期/篡改即失败）。测试耗时从 8s（真实解析）降到 0.55s。

### P1-3 评估器零锚点拒配（终版语义）+ 标注侧对齐
- 评估器：标注可提取锚点短语但候选**零命中** → 拒配（FN/待复核），不再降级放行——「唯一候选来自其他章节」不晋升 TP。修复过程中连带修复 **R2 起就存在的前缀剥离 bug**：`tbl:支出决算表合计行` 此前归一化成 `tbl支出决算表合计行` 整段，表锚永远零命中（样张 A-004 实测成因）。
- 标注侧配套（经用户确认采用此方案而非接受召回下降）：A-003/005/006/008 四条措辞交叉标注补充 `anchor_phrases_aligned` 字段（取自规则实际产出文案的对齐短语），修订记录在 ANNOTATIONS.md；标注本体（label/rule_id/page/evidence）未动。A-005 的对齐短语在验证中收紧（「支出决算表」对同规则两条 finding 都命中会抢占 A-004 的 finding，改为「同口径列」精确指向）。
- 诚实记录边界：GPT5.6 构造的「其他章节 finding 与正确域候选共享主题词（如『公务接待费』）」场景，靠短语命中无法区分（主题词命中即 hits>0）——该场景由「错域与正域并存时淘汰错域」（锚点条件约束）承担；真实规则产出中文案含章节标记（V33-245 message 固定含「三公说明」），构造性输入不构成真实假 TP 通道。

### 验证（2026-09-07 第六轮）
- 全量 pytest：**1038 passed + 1 skipped**（上轮 1032 → 新增 6 测试，零回归）；
- replay+evaluate：**GATE-PASS**（defect 3/3 + hint 4/4，全部在零锚点拒配的严格语义下通过）；
- shadow replay：structured 覆盖率 1.9% 真实口径保持，差异逐规则列出；
- 历史 structured smoke：5 份任务全部 total_rules=9 真实执行（预算材料如实 3 条 insufficient_data）；
- Ruff 全目录通过。

### 仍未完成（如实记录）
其余 8 条规则的结构化消费迁移（V33-117/120 的 `_apply_structured` 化是下一批，建立在已修正的表数据基础上）、生产管线切换、FiscalFactMaterializer 去 DB、Golden Corpus 扩容 ≥20、top30 人工裁决、真实 AI provider 预发验证。
