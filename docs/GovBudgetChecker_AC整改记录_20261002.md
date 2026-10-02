# GovBudgetChecker AC 整改记录（对照 2026-10-02 验收报告）

整改分支：`codex/zcode-remediation`。基线：`a06f42c`（验收报告对应的提交）。
本记录逐条对应验收报告 §四 的 AC-01…AC-16，给出修复位置、进入主链说明、
正反例结果与已知限制。全部新增反例已固化为仓库测试 `tests/remediation/test_ac_remediation.py`
（12 条）与更新后的 `tests/remediation/test_counterexamples_known_defects.py`（原 3 条
xfail 全部移除标记、转正式回归并通过）。

## 本次独立验证结果

- 后端全量 `python -m pytest -q`：**2337 passed、137 skipped、0 failed**（唯一新增 skip
  为 `test_memory_limit_is_enforced` 的平台差异跳过，见 AC-16）。
- 整改专项 `tests/remediation/`：全部通过，**0 xfail 残留**。
- Ruff：通过（0 error）。环境变量三方对账：通过。
- 前端 `npm run test:unit`、`npm run build`：通过。
- ZC-07 门禁 `scripts/run_zc07_acceptance.py`：可执行，缺冻结数据集/真实 E2E 证据的
  门槛如实返回 not_computable/fail（不再把"未实现"与"没数据"混写死）。

---

## AC-01 统一财政事实进入主规则链 —— 已完成（生产代码级）

**修复位置**

- 新增 `src/engine/fact_context.py`：`FactContext`（事实快照 + 版本令牌 + 操作数纪律）、
  `build_fact_context_from_artifact`（纯函数，无数据库；与结构化入库共用同一物化实现）、
  `apply_fact_overlay` / `parse_overlays_from_records`（AC-10 覆盖层）。
- `api/main.py` `_run_pipeline_body`：解析句柄产出的 artifact 反序列化后、OCR 补识别之后，
  **规则执行前**构建 `FactContext` 并挂到 `Document.fact_context`（legacy 路径与双模式
  `JobContext.fact_context` 均传递，`engine_rule_runner._prepare_document` 消费同一快照）；
  meta 记录 `fact_context` 摘要与版本令牌。
- `src/engine/rules_v33.py` `Document` 新增 `fact_context` / `fact_receipts` 字段；
  子进程规则链通过 pickle 携带事实（`build_issues_payload` 收割回执与事实摘要）。
- `src/engine/rule_registry.py`：声明式 `FACT_INPUTS_BY_RULE`（BUD-107 / CMM-001 /
  V33-103 / V33-104 / V33-214），目录按规则给出 `fact_inputs`、`fact_migration=complete/pending`、
  `evaluation_basis=fiscal_fact/legacy_document`。
- 事实消费规则：BUD-107 表侧操作数优先取统一事实（T1 收入/支出、T9 三公预算数），
  缺事实时回退 legacy 取数并在回执中如实标记。

**正反例**：`test_ac01_fact_context_builds_from_artifact_without_database`、
`test_ac01_low_confidence_facts_are_not_definite_operands`、
`test_ac01_document_carries_fact_context_into_rules`；
`tests/remediation/test_zc03.py::test_canonical_registry_routes_every_production_family`
（更新为双口径目录契约）。

**已知限制**：82 条规则中 5 条已迁移为事实消费口径，其余仍为 legacy_document（目录如实
标注 pending，未宣称全量迁移）。操作数纪律：`ambiguous/low_confidence/missing` 不可作
确定操作数；`unverified`（解析层事实、证据门另行把关）允许参与勾稽——这是原生文本层
材料的正常路径，已在 fact_context 模块头与回执中显式声明。

## AC-02 表身份、签名错位、续页边界 —— 已完成

**修复位置**

- `src/engine/budget_rules.py`：
  - `_BUDGET_TABLE_SIGNATURES` T6–T9 与 `BUDGET_TABLE_SPECS` 对齐（T9=三公，
    T7=国资，T6=基金，T8=经济分类）——同页三公表+国资表时 T9 不再取国资表合计；
  - `_is_total_row`：合计行按**行首标签**判定，"合计"作为列头不再使续表提前停止；
  - `_get_budget_table_rows` 续页：删除"回退取本页最大表"，歧义 fail-closed；
    新增 `_starts_with_table_title`（续页内嵌独立表题=新表）与
    `_page_introduces_different_table`（续页正文出现其它表种表题=新表）两道守卫。
- `src/services/pdf_parser.py`：
  - `_is_continuation_table` 新增 `_fragment_titles_conflict`（同表种不等于同实例：
    双方都是可识别表名但表种不同→冲突；年度集合不相交→冲突；主体 token 不相交→冲突；
    噪声标题/页标不构成冲突，保持既有续页契约）；
  - `_build_logical_instance`：`page_end` 取单元格真实物理页最大值；年度/主体进入实例
    身份；`ParsedTable.source_fragment_uids` 保留被合并的原始物理片段 uid（schema 同步扩展）。

**正反例**：`test_ac02_t9_binds_three_public_table_not_state_capital_on_same_page`、
`test_ac02_conditional_tables_unknown_state_not_reported_missing`、
`test_ac02_parser_does_not_merge_same_code_fragments_with_different_titles`；
既有契约 `test_coalesce_tables_merges_consecutive_same_code`、
`test_coalesce_tables_allows_chained_continuations_after_merge`、
`tests/remediation/test_zc02.py` 全部保持通过。

## AC-03 上年叙述污染本年金额 —— 已完成（xfail 已移除）

**修复位置**：`src/engine/budget_rules.py` 新增
`_extract_text_strict_by_patterns_for_year`（年度绑定提取）：
命中句前文出现"上年/去年/历史"或明确非本年年份的一律排除；全口径候选优先
（`_FUND_SCOPE_QUALIFIER_TOKENS` 排除资金口径分项）；候选消歧确定性回退+留痕，
绝不跨年。BUD-107 的 T1/T4/T9 叙述提取全部改走该入口；报告年度来自 `_infer_report_year`。

**正反例**：`test_fixed_ac03_previous_year_narrative_misbinding`（原
`test_xfail_previous_year_narrative_misbinding`，追加"2025年收入预算213012万元"后
BUD-107 输出保持不变）；`tests/test_budget_intake_20260929.py` 三份预算夹具全绿
（本年真实差异仍检出）。

## AC-04 关系级子检查回执 —— 已完成

**修复位置**

- `src/engine/common_rules.py` CMM-001：两条路径（预算 legacy / 决算 strict）逐字段写
  关系级回执（`make_fact_receipt`：操作数事实 ID、公式、实际容差、逐关系状态）；
  **合计≠分项**——分项关系全部无可比操作数时整项转 `insufficient_data` 并携带
  已确认 partial_issues；个别分项未披露记 `not_applicable`（披露完整性归说明检查线）。
- `src/engine/check_obligations.py` `build_check_receipts`：合并规则写下的关系级回执；
  义务×规则配对回执按六态优先级聚合关系状态，事实口径的回执标记
  `evaluation_basis=fiscal_fact`、`fact_migration_status=complete`，容差记录实际政策值。
- `src/engine/pipeline.py` `build_issues_payload` 收集 `doc.fact_receipts` 进 payload；
  `api/main.py` 透传至 `meta.check_receipts`；双模式路径经 `engine_rule_runner` 同步暴露。

**正反例**：`test_fixed_ac04_partial_comparison_reported_pass`（原 xfail，移除）；
`test_ac04_cmm001_writes_relation_receipts_and_defers_partial`。

## AC-05 条件性表政策 —— 已完成（xfail 已移除）

**修复位置**：`src/engine/budget_rules.py` `BUD001_StructureAndAnchors` +
`_conditional_table_applicability`：基金/国资表三态判定——
`applicable`（资金关键词+金额证据，缺表必须检出）、`not_applicable`（明确"未安排/
不涉及/无…安排"）、`unknown`（无任何痕迹，不判缺表）。

**正反例**：`test_fixed_ac05_conditional_tables_policy`（原 xfail）+
`test_ac02_conditional_tables_unknown_state_not_reported_missing` 的"确有基金内容缺表
必须检出"正例。决算空表/等效说明检查（D-008/D-009）保持既有实现。

## AC-06 容差统一接入生产 —— 已完成

**修复位置**：`src/engine/rules_v33.py`

- `tolerant_equal` 改为 v3_3_portable 政策（绝对 0.5 万元），旧 atol/rtol 形参仅为
  调用兼容保留、不再参与计算——V33-101…108/233/235 全部收敛；
- 新增 `_amount_mismatch(a, b, rounding_envelope=…)`，以正则全量替换
  `abs(a-b) > 0.01/0.05/1.0` 手写容差（24 处），有效性门槛（`x > 0.01` 判非零）保持不动；
  V33-119 列纵向平衡改用 3 项舍入包络；
- 占比/同比比较（±1.0pp/±2.0pp）改走 `percentage_difference_exceeds_policy`（0.1pp）。

**正反例**：`test_ac06_portable_tolerance_boundaries_in_production_rules`（0.5 万元边界
内外 + 0.1pp 边界内外 + V33-103 1000 vs 1000.60 反例 + V33-214 100 vs 100.10 舍入
不报错）；`test_portable_amount_and_percentage_tolerance_boundaries` 保持通过。

## AC-07 OCR 候选转可检查事实 —— 已完成（门控晋升）

**修复位置**：新增 `src/services/ocr_fact_promotion.py` `promote_ocr_candidates`：
列身份（表头+首列行标签匹配九表规则、精确优先）、关键数字复验（金额格可解析率）、
置信度门槛三道门；通过者以与原生表**同构**的 fragment 进入统一解析对象，经
`build_fact_context_from_artifact`（同一物化实现）生成可消费事实——证据
`source_method="ocr"`、`verification_status` 保持 unverified，绝不因晋升改判 verified；
未通过者保持候选（usable_for_rules=false）。单位从该页 OCR 文本块的"单位：X"声明
探测，探测不到不猜口径。`api/main.py` 在 OCR 之后、事实构建之前接入，晋升摘要与
`usable_for_rules` 如实落账 `meta.ocr.promotion`。
`fiscal_fact_materializer` 证据 `source_method/location_level` 按片段 extraction_method 判定。

**正反例**：合格三公表晋升并产出万元归一事实（source=ocr）；乱码表
（column_identity_below_threshold）与数值不可解析表（key_number_recheck_failed）被拒。

**已知限制**：真实 OCR 模型、扫描/旋转页实跑与坐标精度未在本机验证（无 OCR 服务与
模型清单）——晋升门的输入契约由合成候选覆盖；该验证仍属 ZC-07 真实服务范围。

## AC-08 事实证据按列映射绑定 + 完整持久化 —— 已完成

**修复位置**

- `src/services/fiscal_fact_materializer.py`：`MaterializedFact.source_col_idx` 记录金额
  来源列；`build_fiscal_facts_for_table` 按 (row, col) 精确取证据单元格（仅在无列来源
  且金额唯一时回退）；证据实体（物理页/bbox/source_cell_id/quote/file_hash/定位等级）
  完整嵌入 `fact.extra_dims["evidence_refs"]`。
- `src/services/parse_artifact_store.py` `persist_facts_and_evidence`：按 evidence_refs
  完整写入 `parse_evidence`（physical_page/bbox/source_cell_id/…），不再退化为
  quote+unverified；新增 `load_parse_evidence` 读取端还原全部定位实体。

**正反例**：`test_ac08_equal_amounts_in_different_columns_get_distinct_evidence`；
数据库往返全量字段一致性需真实 PG（GOVBUDGET_TEST_DATABASE_URL），属 ZC-07 范围
（测试断言 bug 已修，见 AC-13）。

## AC-09 逐条证据真实性 + bbox 认证收紧 —— 已完成

**修复位置**

- `src/services/evidence_guard.py` `evaluate_finding_evidence`：引用核验从"任一引文×
  任一页"改为**逐条证据绑定其自称页**——任一条引文在自家页面不存在即整体
  `failed`（伪造不可被另一条真引文掩盖，互换页码不再绕过）；扫描页（无文本层）
  维持 unverified 语义与原因码；混合形态降为 unverified 并指明未核验绑定。
- `src/utils/issue_bbox.py`：exact/verified bbox 必须通过**内容锚定**
  （`_region_text_matches`：bbox 区域内抽取的原文包含该证据引文）；PDF 缺失/区域
  无文本/引文不符一律降级 region/unverified；纯坐标（无引文）不可认证为精确。
- `api/routes/corrections.py` `_sanitize_evidence`：读取端按当前原件重算——文件哈希
  不匹配→failed；引文在自称页不存在→failed；bbox 区域内容与引文不符→降级 region。
  不再原样回显存储侧 verified 标签。

**正反例**：真实+伪造双证据→failed；页码互换→failed；双真→verified（探针固化于
会话验证）；`issue_bbox` 伪造空白区 bbox 降级、真实 bbox 保留（fitz 实测）；
`tests/remediation/test_zc00.py`、`tests/test_evidence_completeness.py`、
`tests/remediation/test_zc05.py` 全部通过（夹具哈希/字体按新契约升级）。

## AC-10 校正驱动重算 + 复核状态入报告 —— 已完成（代码闭环）

**修复位置**

- `api/routes/corrections.py` `create_job_correction`：校正写入台账后触发
  `runtime.get_pipeline_runner()` 对该 job 的重分析（后台任务），响应改为
  `reanalysis_status="queued"`、`review_decisions_status="pending_reanalysis"`；
  "提交留痕"不再描述为"校正已生效"。无 runner 时如实返回 not_executed_no_pipeline_runner。
- 覆盖层：`fact_context.parse_overlays_from_records` 消费 corrections.jsonl 的
  `changes[]`（target=fact、field=raw_value 等）；`apply_fact_overlay` 校验目标事实
  **存在于当前事实版本**（未知 fact_uid 拒绝→主链记 `overlay_rejected_unknown_fact`）、
  生成新 FactContext（verified + 原值留痕 + 新版本令牌）。
- `api/main.py`：重分析时先应用覆盖层再跑规则（`fact_overlay_applied` 落账）；
  终态报告投影 `issue_workflow_store` 的人工复核决定到 `human_decisions`，
  `build_review_report` 输出到 canonical 报告（界面与导出同源消费）。

**正反例**：`test_ac10_overlay_changes_fact_values_and_version_token`、
`test_ac10_overlay_rejects_unknown_fact_target`、
`test_ac10_correction_overlay_flows_into_reanalysis_pipeline_block`；
`tests/remediation/test_zc05.py` 更新为"queued 重算"契约后全绿。

**已知限制**：重算受影响范围的"安全缩小"当前默认整链重跑（保守正确，未做增量
最小化）；旧复核决定随新版本保留但"失效范围标注"以版本令牌对比呈现，未做逐项
失效推导。

## AC-11 强规则保留 + 身份贯通 + 逐条异常隔离 —— 已完成

**修复位置**

- `src/services/merge_findings.py`：关系歧义只影响 AI 侧——规则发现保留在
  `merged_ids`（正式集合）并作为关系代表项；歧义规则项不再移入
  `manual_review_issue_ids`；relation 记录 representative/冲突原因如实留痕。
- `src/services/ai_findings.py`：AI 候选的 `check_relation_id/fact_ids/evidence_ids`
  透传到 IssueItem（不再在适配层丢弃）；`engine_rule_runner._issue_to_finding` 保留
  规则侧同名字段通道。
- `src/services/analyze_dual.py`：部分结果保全——管线后段异常时已完成 AI/规则
  结果随 fallback 一并返回（`engine_only_partial`），不再整批清空；引擎回执与
  事实摘要进入 meta。规则级异常隔离本就逐条存在（engine_rule_runner try/except）。

**正反例**：`tests/remediation/test_zc06.py::test_zc06_duplicate_relation_is_ambiguous_and_not_greedily_paired`
（更新为新契约：merged_ids=["rule-1"]，仅 AI 侧转待复核）；同关系 2×low AI + 1×critical
规则的反例形态由该测试覆盖。

## AC-12 导出同源 + 历史版本下载 —— 已完成

**修复位置**：`api/routes/reports.py`

- `_build_export_location`：主页与 bbox 同源——bbox 只取自"恰好位于主页"的
  verified-exact 来源，页码/等级/证据 ID 同对象输出（新增 evidence_ids）；
- `_build_export_conclusion`：结论优先读选定版本 canonical ReviewReport，顶层
  `analysis_conclusion` 仅作无 canonical 时的兜底，两个来源不再并存冲突；
- `/api/reports/download` 新增 `analysis_revision` 参数（目录名或版本号精确匹配、
  路径穿越拒绝、404 语义），支持下载历史分析版本。

**正反例**：`tests/remediation/test_zc04.py`（共享来源、导出剥离未核验 bbox 等）全绿；
页码/bbox 一致性探针在会话中实测（P1 主页不再配 P2 证据 bbox）。

## AC-13 真实库测试断言修复 —— 已完成（断言级）

**修复位置**：`tests/remediation/test_zc01.py` 真实库往返测试
`assert written == len(facts_a) + facts_b`（int+list TypeError）改为
`len(facts_a) + len(facts_b)`。连接隔离库实跑往返仍需
`GOVBUDGET_TEST_DATABASE_URL` 与 Docker（本机 daemon 不可达），保持 skip 语义；
64 份分层真实 PDF/100 盲测缺陷组的数据准备未完成，**AC-13 的数据与环境验收项
仍未关闭**（诚实保留，未用模拟冒充）。

## AC-14 门禁指标实现 + E2E 子校验加固 —— 已完成（评测逻辑级）

**修复位置**：`scripts/run_zc07_acceptance.py`

- 实现 8 项此前固定 not_computable 的评测：`_evaluate_evidence_authenticity`
  （逐条 evidence_verification 三态聚合）、`_evaluate_receipts`（操作数+公式覆盖率、
  关系级完成率）、`_evaluate_exact_location`（golden 提供 truth_region 时计算区域
  命中率）、`_evaluate_repeatability`（repeat_replay_path 两轮签名比对）、
  `_evaluate_export_agreement`（export_paths ≥2 的 JSON/CSV 逐项比对）；输入齐备
  即计算并按门槛 pass/fail，输入缺失如实 not_computable 并说明缺什么。
- E2E 子校验加固：自报布尔不再单独作数——新增 `source_pdf_path/export_path/
  trace_path` 必填并逐文件核验存在性与 SHA-256；trace 必须为非空 JSON 对象且
  `job_id` 与场景一致；`api_build_revision` 与 `worker_build_revision` 必须一致。

**正反例**：`tests/remediation/test_zc07.py` 夹具升级为真实可核验文件后通过
（旧夹具的"空 trace {} + 虚构哈希"在新校验下按预期 incomplete）。
门禁脚本本机实跑输出与预期一致（无数据 → not_computable，不伪造 pass）。

## AC-15 默认关 AI 覆盖模板与推荐部署 —— 已完成

**修复位置**：`.env.example` `AI_ASSIST_ENABLED=false`（含"规则优先、AI 显式开启"
注释）；`README.md` 默认值说明更正、`ready` 检查说明补充"未启用 AI 时自动跳过"。
`docker-compose.yml`（默认部署）本就不设置 AI 开关且只带本地 OCR 服务；
`docker-compose.ai.yml` 为显式 AI 专用模式。`api/config.py` 默认 false 不变。

## AC-16 内存保护平台差异 —— 已完成（可执行语义）

**修复位置**：`src/services/pdf_parse_process.py`

- 新增 `probe_memory_limit_enforceable(limit_mb)`：fork 探针子进程设置相同上限后
  尝试超限分配，真实探测内核是否强制 RLIMIT_AS（Linux 容器=强制；macOS/Darwin
  =接受设置但不强制）；解析 payload 新增 `memory_limit_enforced` 如实上报；
- `tests/test_pdf_parse_isolation_and_backup.py::test_memory_limit_is_enforced`：
  先探测，强制平台（目标部署=Linux 容器）实跑断言超限受控终止；本机
  （macOS）以明确的平台差异原因 skip——**不是删除测试**，探针路径本身有测试
  覆盖（21 passed, 1 skipped）。

**已知限制**：本机（macOS，且运行于受限沙箱）无法实跑"超限终止"断言；目标部署
（Linux 容器）的实跑证据需在容器环境执行后留存运行记录（ZC-07 真实服务范围）。

---

## 与"再次验收条件"的对照说明

- **已关闭（代码+测试级）**：AC-01、AC-02、AC-03、AC-04、AC-05、AC-06、AC-08、
  AC-09、AC-11、AC-12、AC-15；AC-07（门控晋升链路）、AC-10（重算闭环）、
  AC-14（评测逻辑）、AC-16（平台探测）的**工程与测试部分**。
- **仍需真实环境/数据才能宣布通过（与验收报告口径一致，未用模拟冒充）**：
  1. 真实 OCR 模型与扫描件准确率（AC-07 实跑）；
  2. 隔离 PG/Docker 往返与四条真实服务场景（AC-13/AC-14 的实跑记录）；
  3. 64 份分层真实 PDF、100 个盲测缺陷组冻结后按门禁计算业务指标
     （精确率/召回率/精确定位率等，AC-14 输入）；
  4. Linux 容器内内存超限受控终止的实跑留痕（AC-16）。

## 本次改动的文件清单

- 新增：`src/engine/fact_context.py`、`src/services/ocr_fact_promotion.py`、
  `tests/remediation/test_ac_remediation.py`、本记录。
- 修改：`api/main.py`、`api/routes/corrections.py`、`api/routes/reports.py`、
  `src/engine/rules_v33.py`、`src/engine/budget_rules.py`、`src/engine/common_rules.py`、
  `src/engine/pipeline.py`、`src/engine/check_obligations.py`、`src/engine/rule_registry.py`、
  `src/engine/field_extractor.py`（导入）、`src/schemas/parse_artifact.py`、
  `src/schemas/issues.py`、`src/services/pdf_parser.py`、
  `src/services/fiscal_fact_materializer.py`、`src/services/parse_artifact_store.py`、
  `src/services/structured_ingest_runner.py`、`src/services/evidence_guard.py`、
  `src/services/merge_findings.py`、`src/services/ai_findings.py`、
  `src/services/engine_rule_runner.py`、`src/services/analyze_dual.py`、
  `src/utils/issue_bbox.py`、`src/services/pdf_parse_process.py`、
  `scripts/run_zc07_acceptance.py`、`.env.example`、`README.md`、
  `tests/remediation/test_zc03.py`、`tests/remediation/test_zc05.py`、
  `tests/remediation/test_zc06.py`、`tests/remediation/test_zc07.py`、
  `tests/remediation/test_zc01.py`、
  `tests/remediation/test_counterexamples_known_defects.py`、
  `tests/test_pdf_parse_isolation_and_backup.py`、
  `tests/test_evidence_completeness.py`（未改动，验证通过）。
