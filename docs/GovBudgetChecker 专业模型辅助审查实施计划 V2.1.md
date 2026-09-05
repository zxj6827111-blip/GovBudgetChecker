# GovBudgetChecker 专业模型辅助审查实施计划 V2.1

> 版本：V2.1  
> 编制日期：2026-08-26  
> 适用范围：上海市部门预算、部门决算、单位预算、单位决算  
> 当前状态：条件 GO；Phase 0 可立即启动，但批量人工标注必须等待身份契约和 ExtractionSnapshot 契约冻结  
> 基线文档：`docs/GovBudgetChecker 专业模型辅助审查实施计划 V2.md`

---

# 1. 执行摘要

## 1.1 最终判断

GovBudgetChecker 具备继续建设专业模型辅助审查能力的基础，但当前不应直接开始模型微调、采购本地推理节点或把更多文档直接送入大模型。

当前最先需要解决的不是模型参数规模，而是以下六个阻塞项：

1. 文档类型、主体层级和模板版本没有形成统一、可追溯的文档画像契约。
2. 规则引擎与结构化入库使用不同的数据通路，表格、单元格和证据坐标没有统一。
3. 正式 finding ID 使用随机 UUID、结果顺序或时间戳，现有输出身份不能跨回放稳定匹配。
4. `fiscal_table_cells` 使用自增主键作为证据引用，并采用 delete-before-parse，历史证据可能在重解析后悬空。
5. 缺少 Golden Corpus 回放、finding 匹配和准确率自动评测工具。
6. 人工审核状态不足以支持误报、漏报、争议和训练准入闭环。

因此，本计划采用以下路线：

1. 第 1 周先冻结 finding、annotation、document、table、cell 和 extraction snapshot 身份契约。
2. 同步开发最小回放工具骨架、选择种子材料并冻结现状版本，但暂不进行依赖现有 finding/cell ID 的批量标注。
3. 统一文档画像、结构化事实层和证据契约。
4. 将解析与持久化解耦，采用不可变 ExtractionSnapshot 和原子 active snapshot 切换。
5. 通过兼容适配器逐步迁移现有规则，不进行一次性大重写。
6. 清理全仓 mock finding、fail-open 和无生产入口的半成品混合子系统。
7. 建立人工确认、误报、漏报、争议、审核迁移和训练准入闭环。
8. 在结构化输入和证据校验基础上接入两阶段模型。
9. RAG 先做结构化精确过滤，再在小候选集上进行语义匹配。
10. 模型 POC 达到质量和吞吐门禁后，再决定本地硬件和 LoRA。
11. 所有正式问题必须经过确定性复算、证据校验和人工确认。

## 1.2 V2.1 相对 V2 的主要调整

V2.1 不推翻 V2 的总体方向，重点补充和调整以下内容：

- 增加五层业务身份契约：`analysis_run_id`、`finding_instance_id`、`claim_fingerprint`、`annotation_id`、`review_record_id`。
- 将 finding fingerprint 拆分为稳定业务主张 `claim_fingerprint` 和绑定具体证据快照的 `evidence_fingerprint`。
- 明确 `finding_instance_id` 可以是运行内唯一 ID，跨运行复现依赖 fingerprint，而不是复用实例 ID。
- 增加 `fingerprint_schema_version` 和 `producer_signature`，避免规范化算法或生产者版本变化后无法解释。
- 将自增 `fiscal_table_cells.id` 从业务证据身份降为数据库内部代理键。
- 引入 `logical_table_instance_id` 和 `cell_logical_id`，解决重复表号、跨页表格和重解析证据漂移。
- 引入不可变 `ExtractionSnapshot`，禁止 delete-before-parse 和边解析边覆盖 active 数据。
- 明确 canonical parser 必须脱离数据库独立运行，数据库只是可选 persistence sink。
- 将 fail-closed 从 AI mock 清理扩展为所有验证器、质量门禁和报告导出的通用原则。
- 将旧混合子系统按活、死、待判定三态盘点，避免在无生产入口路径上继续投入。
- 将 Phase 1 拆为 Phase 1A 和 Phase 1B，总工期调整为 5 至 7 周。
- 增加批量人工标注前的独立门禁；身份与 snapshot 契约冻结前，只允许种子选取和工具骨架开发。

## 1.3 GO / NO-GO 总结

| 项目 | 当前判断 | 说明 |
|---|---|---|
| 版本冻结、种子选取和 replay CLI 骨架 | GO | 可以立即实施 |
| 基于现有 finding/cell ID 的批量标注 | NO-GO | 必须先冻结身份和 snapshot 契约 |
| Finding 与 Evidence 身份契约 | GO | 第 1 周第一批 P0 |
| 不可变 ExtractionSnapshot | GO | 第 1 周第一批 P0 |
| 文档画像统一 | GO | 第一批 P0 |
| 结构化事实层收敛 | GO | 第一批 P0，阻塞规则和模型 |
| mock/fail-open 清理 | GO | 第一批 P0 |
| 人工审核闭环 | GO | 与 Golden Corpus 扩展并行 |
| 模型 POC | 条件 GO | 必须先通过事实层和证据层门禁 |
| RAG | 条件 GO | 必须先建立结构化政策元数据 |
| LoRA/QLoRA | NO-GO | 当前训练数据和评测基础不足 |
| 本地推理硬件采购 | NO-GO | 等待模型 POC、固定负载包和实机测试 |
| 上海正式试点 | NO-GO | 需完成 shadow 验收 |

---

# 2. 当前代码事实与问题定义

## 2.1 文档类型识别存在多套实现

当前并非只有两套文档类型判断，而是至少存在以下入口：

- `api/runtime.py` 的上传预检与 `normalize_report_kind`；
- `src/engine/pipeline.py` 的 `_resolve_report_kind`；
- `src/services/engine_rule_runner.py` 的 `_resolve_report_kind`；
- `src/engine/common_rules.py` 的 `_infer_report_kind`；
- `src/services/analysis_result_store.py` 的展示与持久化归一化。

已核实的问题包括：

- 多处代码优先匹配“预算”，再匹配“决算”；
- 文件名或标题同时包含“决算”和“年初预算数”时可能被判为预算；
- `pipeline.py` 未识别时返回 `unknown` 并生成人工复核问题；
- `common_rules.py` 未识别时默认 `final`，虽然当前只直接影响部分通用规则，但仍属于静默默认；
- 不同入口使用的来源优先级和回退规则不完全一致；
- 文档类型判断结果没有统一记录置信度、依据、冲突和人工确认状态。

必须建立单一 `DocumentProfileResolver`，其他代码不得自行根据文件名重新推断。

## 2.2 主体层级存在局部能力，但没有进入规则契约

当前前端上传组件已有部门汇总和单位层级选择，上传预检也能产生 `scope_hint`，组织目录本身还包含 `department/unit` 层级。

但当前存在以下断点：

- `subjectLevel` 主要停留在前端状态；
- 上传请求没有稳定提交统一的 `subject_level` 字段；
- 后端状态、JobContext 和规则 Document 没有统一 `subject_level`；
- 规则选择器无法基于已确认的部门/单位层级做强制适用性过滤；
- `doc_type` 目前仍主要使用 `dept_budget/dept_final`，没有完整表达四类材料。

因此问题不是“系统完全没有主体层级信息”，而是“主体层级没有成为端到端、可审计的业务契约”。

## 2.3 规则与结构化事实层使用不同数据通路

当前主分析路径先使用 `pdfplumber` 生成：

```text
page_texts
page_tables: 页 -> 表 -> 行 -> 列
```

随后规则引擎和 AI 使用这组数据完成分析。

结构化入库路径则在主分析之后再次解析 PDF，生成：

```text
ParsedTable
ParsedCell
fiscal_table_cells
fact_fiscal_line_items
```

后者包含：

- `table_code`
- `row_idx`
- `col_idx`
- `page_number`
- `bbox`
- `numeric_value`
- `confidence`
- `unit_hint`
- `extraction_method`
- `source_cell_ids`

但这些数据没有成为规则和模型的主输入。

直接后果是：

- 规则 finding 引用裸字符串位置；
- AI finding 主要引用字符 span 或文本片段；
- 结构化数据库使用单元格 ID 和 bbox；
- 三者无法稳定互相反查；
- 重新解析、跨页表格合并或表格顺序变化后，位置可能漂移；
- “表格和单元格是否真实存在”的证据复核无法形成可靠闭环。

## 2.4 AI 服务中存在模拟 finding 死代码

`src/services/ai_findings.py` 当前生产主路径调用 `ExtractorClient.ai_full_report_audit()`。

但文件中仍保留一套未接线旧通路：

- `_build_prompt`
- `_call_ai_with_retry`
- `_call_ai_client`
- `_get_mock_ai_response`
- `_parse_ai_response`
- `_extract_json_from_response`

其中 `_call_ai_client()` 无条件返回 `_get_mock_ai_response()`，模拟结果包含虚构页码、金额、表格和 metrics。

虽然当前主路径没有调用它，但该代码位于正式 service 内，未来维护时极易被误接入。因此必须删除或迁移到测试 fixture。

## 2.5 规则规模和元数据现状

当前规则规模约为：

- `rules_v33.py`：5238 行，52 个 Rule 子类，50 个启用规则；
- `budget_rules.py`：16 个启用规则；
- `common_rules.py`：6 个启用规则。

现有规则主要通过类属性和代码逻辑表达：

- `code`
- `severity`
- `desc`
- `apply`

尚未形成完整统一的：

- 文种适用范围；
- 主体层级；
- 资金口径；
- 模板版本；
- 生效时间；
- 必需字段；
- 证据契约；
- 解析失败策略；
- 正例、反例和边界样本目录。

V2.1 不要求第一天把所有规则重写成 DSL，而是通过外部注册表和完整性校验逐步迁移。

## 2.6 审核状态不足以形成训练闭环

当前审核状态主要是：

- `pending`
- `confirmed`
- `no_issue`
- `needs_review`
- `in_package`

它们适合整改工作流，但不足以表达模型训练所需的：

- `confirmed_issue`
- `false_positive`
- `missed_issue`
- `uncertain`
- `duplicate`
- `not_applicable`
- `evidence_error`
- `can_train`
- 误报原因；
- 漏报补录；
- 争议原因；
- 审核人；
- 修改历史；
- 二次复核结果。

## 2.7 缺少 Golden Corpus 自动评测工具

当前 `scripts/` 主要包含性能、可见性和表格识别诊断工具，尚无完整的：

- Golden Corpus 清单加载；
- 批量回放；
- finding 对齐；
- precision/recall/F1 计算；
- 分类别指标；
- 无问题文档误报率；
- 证据可定位率；
- 版本差异报告；
- 候选数量和审核负荷统计。

因此 Phase 0 必须包含工具开发，不能只写“冻结数据”。

## 2.8 正式 finding ID 当前不可重放

当前正式输出链路至少存在以下 ID 生成方式：

- `src/services/engine_rule_runner.py` 使用随机 `uuid.uuid4()`；
- `src/engine/pipeline.py` 使用 `rule_code + 结果列表下标`；
- `src/services/ai_findings.py` 使用 job、列表下标和 `int(time.time())`；
- `src/services/ai_rule_runner.py` 使用页下标和 `int(time.time())`。

`IssueItem.create_id()` 虽然是确定性配方，但当前只出现在没有生产调用者的旧代码中，并且只使用 page/section/table，无法区分同页同表中的多个不同问题。

因此：

- 当前输出 `id` 只能作为一次运行中的实例标识；
- 不能把现有 `id` 作为跨运行 finding 匹配、人工标注迁移或审核继承的稳定锚点；
- replay 工具必须使用独立的 claim/evidence fingerprint；
- 身份契约冻结前不得开始大批量依赖现有 ID 的人工标注。

## 2.9 自增 cell ID 和 delete-before-parse 会破坏历史证据

当前已核实：

- `fiscal_table_cells.id` 是 `SERIAL PRIMARY KEY`；
- `fact_fiscal_line_items.source_cell_ids` 是 `BIGINT[]`；
- `qc_findings_v2.evidence_cells` 是 `BIGINT[]`；
- `QCRunnerV2/V3` 直接查询 cell 整数 ID 并保存到 finding；
- PDF 报告通过 `get_finding_drilldown()` 使用这些整数 ID 查询证据；
- `PDFParser.parse_pdf()` 在打开和完整解析 PDF 前先删除现有 cells；
- `TableRecognizer` 和 `FiscalFactMaterializer` 同样采用先删除后逐步重建。

正常 PostgreSQL sequence 不会在普通 delete 后立即复用 ID，因此重解析后历史 finding 更常见的结果是证据悬空、无法反查，而不是必然指向错误单元格；在序列重置、恢复或 ID 复用场景下才可能误指。

该问题必须通过不可变 snapshot、逻辑证据键和历史映射解决，不能仅把 `SERIAL` 改成 UUID 后继续覆盖同一批业务记录。

## 2.10 fail-open 和旧混合子系统风险

当前除 `ai_findings.py` 外，还存在：

- `src/services/rule_findings.py` 的硬编码 mock finding；
- `src/engine/ai_validator.py` 无条件返回 `CONFIRM`，异常和未知响应同样默认确认；
- `src/services/ai_rule_runner.py` 的示例实现；
- `src/engine/hybrid_pipeline.py`、`hybrid_validator.py`、`ai_validator.py`、`intelligent_merger.py`、`rule_adapter.py` 构成的旧混合子系统。

静态调用关系显示旧混合子系统没有生产主链路入口，且 `HybridPipeline` 调用了未初始化的 `self.hybrid_engine`，异常后会静默回退。

此外，当前 PDF 报告生成会吞掉 evidence drilldown 异常并继续生成报告，属于活链路上的证据 fail-open。

V2.1 必须同时处理：

- mock 数据清零；
- 验证器 fail-closed；
- 报告和导出证据门禁；
- 活、死、待判定代码三态清单；
- 死代码删除前的静态、动态和回放证明。

---

# 3. 建设目标与非目标

## 3.1 上海阶段目标

- 稳定识别部门预算、部门决算、单位预算、单位决算；
- 不允许因混合标题或文件名关键词顺序发生静默错路由；
- 形成单一结构化事实层；
- 所有正式 finding 能定位到稳定证据；
- 数值问题由确定性代码复算；
- 规则适用范围可配置、可校验、可回放；
- 建立可追溯的人工反馈和训练准入；
- 建立可自动运行的 Golden Corpus 评测；
- 模型只负责候选发现、定性理解和复核摘要；
- 支持夜间批量处理和模型故障降级；
- 支持模型、规则、解析器、提示词和政策库版本回滚。

## 3.2 全国扩展预留

- `jurisdiction` 是所有模板、规则和政策的一级过滤条件；
- 模板、规则和政策按地区注册；
- 地区数据集互相隔离；
- 上海模型配置不得直接声明为全国通用；
- 全国推广以地区适配包和独立 Golden Corpus 为前提。

## 3.3 非目标

- 不从零训练基础模型；
- 不让模型直接决定正式问题；
- 不让模型承担确定性数值计算；
- 不在首期重写全部历史规则；
- 不在首期建设全国向量库；
- 不把历史 Excel 或 PDF 自动视为正确训练数据；
- 不把争议结论强行标注为正确答案；
- 不在模型 POC 前采购只为推理准备的新硬件；
- 不以“模型返回 JSON”替代证据校验和业务正确性。

## 3.4 核心工程原则

1. 单一事实来源。
2. 无静默默认。
3. 证据先于模型。
4. 数值归规则。
5. 不确定即降级。
6. 训练数据必须可追溯。
7. 评测工具先于模型优化。
8. 渐进迁移，不做无必要的大重构。
9. 正式报告必须经过人工确认。
10. 业务身份与数据库代理键分离。
11. 解析结果不可变，active snapshot 只能原子切换。
12. 校验器、质量门禁和报告导出必须 fail-closed。
13. 空结果和执行失败必须使用不同状态表达。
14. 人工审核不得静默继承或静默丢弃。
15. 生产改造范围必须以活、死、待判定代码清单为依据。

---

# 4. 目标总体架构

## 4.1 主链路

```text
上传 PDF
    |
    v
DocumentProfileResolver
    |
    +-- report_kind / subject_level / fiscal_year / template
    +-- confidence / source / conflicts / confirmation status
    |
    v
Canonical PDF Parser
    |
    +-- pages / sections / tables / cells
    +-- immutable ExtractionSnapshot
    +-- logical table/cell ids / bbox / confidence / semantic types
    |
    v
Structured Facts
    |
    +-- amounts / percentages / codes / names / funding scope
    +-- evidence refs / extraction snapshot id
    |
    +----------------------+----------------------+
    |                      |                      |
    v                      v                      v
Rule Engine          Model Candidate       Policy Retrieval
    |                      |                      |
    +----------------------+----------------------+
                           |
                           v
                  Evidence Verification
                           |
                           v
                    Review Workspace
                           |
                           v
              Confirm / False Positive / Missed
                           |
                           v
          Evaluation / Training Export / Shadow
```

## 4.2 统一文档画像 `DocumentProfile`

建议字段：

```text
document_id
document_version_id
jurisdiction
fiscal_year
report_kind
subject_level
report_type
template_id
template_version
organization_id
organization_name
profile_status
confidence
source_evidence
conflicts
confirmed_by
confirmed_at
resolver_version
```

标准值：

```text
report_kind: budget | final | unknown
subject_level: department | unit | unknown
report_type:
  department_budget
  department_final
  unit_budget
  unit_final
  unknown
profile_status:
  auto_confirmed
  needs_confirmation
  manually_confirmed
  conflict
```

禁止使用 `dept_budget` 同时表达“预算”和“部门层级”。迁移期允许兼容读取，但内部标准字段必须拆开。

## 4.3 统一结构化文档 `StructuredDocument`

建议包含：

```text
document_profile
pages
sections
tables
cells
facts
parse_quality
parser_version
parser_config_hash
extraction_snapshot_id
snapshot_status
extraction_hash
```

规则与模型必须从同一 `StructuredDocument` 或其只读视图获取输入。

## 4.4 稳定证据契约 `EvidenceRef`

表格证据：

```text
document_version_key
extraction_snapshot_id
logical_table_instance_id
cell_logical_id
page_number
table_code
row_idx
col_idx
bbox
raw_text
normalized_text
numeric_value
unit_hint
```

文本证据：

```text
document_version_key
extraction_snapshot_id
page_number
section_id
local_char_start
local_char_end
text_quote
bbox
```

要求：

- `cell_logical_id` 或 `section_id` 是主要引用；
- `row_idx/col_idx/char_span` 作为辅助定位；
- 全文拼接后的全局 span 不得作为唯一证据；
- 数据库 `SERIAL/BIGSERIAL` 只能作为内部代理键，不得作为业务证据身份；
- `logical_table_instance_id` 必须区分同一文档中的重复 `table_code`；
- 重新解析后 extraction snapshot 变化，旧证据必须重新校验；
- 查不到证据的模型候选不得进入正式 finding。

## 4.5 Finding 与审核身份契约

统一身份分层：

```text
analysis_run_id
finding_instance_id
claim_fingerprint
evidence_fingerprint
annotation_id
review_record_id
fingerprint_schema_version
producer_signature
```

职责：

- `analysis_run_id` 标识一次分析执行，可以使用随机 UUID/ULID；
- `finding_instance_id` 标识本次运行输出的 finding 实体，不要求跨运行复用；
- `claim_fingerprint` 表达稳定业务主张，用于跨运行去重、匹配和审核迁移；
- `evidence_fingerprint` 表达某一 extraction snapshot 下的稳定证据集合；
- `annotation_id` 标识人工标注实体；
- `review_record_id` 标识不可变审核事件；
- `producer_signature` 记录规则、模型、prompt 或 adapter 的生产者版本。

`claim_fingerprint` 明令排除：

- 时间戳；
- 随机 UUID；
- finding 发现顺序；
- 列表下标；
- `model_id`；
- `extraction_hash`。

`evidence_fingerprint` 必须包含：

- `document_version_key`；
- `extraction_snapshot_id`；
- 排序后的 evidence logical refs；
- fingerprint schema version。

禁止使用“同位判别序号”或模型返回顺序解决重复问题。多个同类 finding 应使用稳定证据集合或结构化 claim 字段区分。

## 4.6 规则元数据 `RuleSpec`

建议采用外部注册表：

```text
rule_id
rule_version
implementation
enabled
severity
jurisdictions
report_kinds
subject_levels
report_types
template_versions
funding_scopes
effective_from
effective_to
required_fields
evidence_contract
parse_failure_policy
test_case_ids
```

第一阶段允许现有规则类保持 `apply()` 不变，由 RuleAdapter 根据 RuleSpec 过滤并构建输入。

## 4.7 模型调用与 finding 溯源

调用级记录 `ModelInvocation`：

```text
invocation_id
analysis_run_id
document_version_key
extraction_snapshot_id
job_id
model_id
provider_id
prompt_version
schema_version
input_hash
input_scope
started_at
finished_at
latency_ms
token_usage
raw_output
parse_status
error
```

finding 级溯源：

```text
invocation_id
finding_instance_id
claim_fingerprint
evidence_fingerprint
model_confidence
evidence_refs
evidence_check_result
claimed_metrics
verified_metrics
verification_status
conflict_with_rules
```

`raw_output` 应存储在调用级记录中，避免在每个 IssueItem 重复保存大段原文。

---

# 5. 文档画像统一方案

## 5.1 识别来源优先级

统一优先级：

1. 人工已确认的结构化元数据；
2. 上传时明确选择且通过权限、组织层级校验的数据；
3. PDF 首页标题和“编制部门/预算单位/决算单位”等字段；
4. 模板结构特征；
5. 文件名；
6. 无法判断时返回 `unknown`。

任何入口不得在未知时默认 `final` 或 `budget`。

## 5.2 混合关键词处理

以下内容不能仅按关键词首次出现顺序判断：

```text
2024 年部门决算（含年初预算数）
2024 年决算及 2025 年预算说明
预算执行和决算情况
年初预算数与支出决算数对比
```

处理要求：

- 优先识别完整标题模式，如“部门决算”“单位决算”“部门预算”“单位预算”；
- “年初预算数”“预算执行率”“预算调整数”不得独立决定文种；
- 首页标题与上传选择冲突时阻止自动运行；
- 文件名与首页冲突时：首页优先，但必须记录冲突；
- 同时存在两个完整文种标题时进入 `needs_confirmation`；
- 所有冲突写入 `source_evidence` 和 `conflicts`。

## 5.3 主体层级处理

主体层级来源：

- 组织目录中的 `level`；
- 首页“编制部门/预算单位/决算单位”字段；
- 完整标题中的“部门/单位”；
- 操作员选择。

校验规则：

- 组织目录为 unit，但文档识别为 department 时必须人工确认；
- 部门汇总材料不得自动套用单位规则；
- 单位材料不得使用部门汇总完整性规则；
- subject level 未确认时，只运行不依赖主体层级的低风险规则。

## 5.4 文档画像验收指标

- Golden Corpus 中 `report_kind` 人工确认准确率不低于 99.5%；
- `subject_level` 人工确认准确率不低于 99.5%；
- 静默错路由数量为 0；
- 混合关键词回归样本全部进入正确类型或人工确认；
- 所有画像结果可说明来源和冲突；
- 所有入口使用同一个 resolver；
- 不允许规则层再次根据文件名自行推断。

---

# 6. 结构化事实层收敛方案

## 6.1 收敛目标

每个文档只产生一份权威解析结果，规则、模型、证据复核和持久化共享该结果。

不要求一次性删除所有裸表格规则，而是采用：

```text
Canonical Parser
    |
    +-- StructuredDocument
            |
            +-- Canonical Rule View
            +-- Legacy page_tables Adapter
            +-- Model Context Builder
            +-- Evidence Resolver
            +-- Database Persistence
```

## 6.2 渐进迁移步骤

1. 将 canonical parser 改为纯解析器，输入 PDF 和 parser config，返回完整 `ParsedDocument`。
2. parser 不得要求 `DATABASE_URL` 或数据库连接；CI、本地开发和单机部署必须能够脱库生成完整 EvidenceRef。
3. 数据库写入改为可选 persistence sink，不得在 parser 内逐单元格边解析边写库。
4. 为每次完整解析生成内容寻址的 `extraction_snapshot_id`，随机 `extraction_run_id` 只作为运行日志身份。
5. 为每个逻辑表生成 `logical_table_instance_id`，不得只用 `table_code` 区分表实例。
6. 为 table/cell/section 生成稳定 logical ID，并建立旧整数 ID 到逻辑键的一次性历史映射。
7. 先完整解析和质量校验，再在单个数据库事务中持久化 cells、table instances、column mappings 和 facts。
8. 持久化成功后以 compare-and-swap 或同等机制原子切换 active snapshot。
9. 主分析路径首先调用 canonical parser，structured ingest 只持久化同一解析结果，不再第二次独立解析。
10. 提供 `LegacyPageTablesAdapter`，把 ParsedTable.rows 投影给旧规则。
11. 新规则优先使用 typed facts 和 evidence refs。
12. 逐批迁移高风险规则，验证后再移除对应裸表格依赖。

迁移顺序必须是：先回填逻辑键和历史映射，再停止 delete/reinsert，最后迁移消费者。不得在旧 rows 已删除后再尝试建立历史映射。

## 6.3 不可变 `ExtractionSnapshot`

建议字段：

```text
extraction_snapshot_id
document_version_key
source_file_hash
parser_version
parser_config_hash
canonical_content_hash
status
parent_snapshot_id
created_by_run_id
created_at
validated_at
activated_at
failure_reason
```

状态：

```text
building
validated
active
failed
superseded
```

约束：

- snapshot 一旦进入 `validated` 后不可修改；
- 解析失败不得改变当前 active snapshot；
- active 指针只能在完整事实图持久化并通过质量门禁后切换；
- 同一 document version 的并发解析必须使用 advisory lock、幂等键或 compare-and-swap 防止旧任务覆盖新任务；
- QC run、finding、annotation、review 和报告必须固定引用具体 snapshot，禁止运行时解析“当前 active”；
- 已被 finding、审核或 Golden Corpus 引用的 snapshot 不得删除；
- 未引用的 failed/superseded snapshot 按保留策略清理。

不得继续使用：

```text
DELETE existing cells
-> 打开 PDF
-> 边解析边写入
-> 失败后保留空或部分数据
```

## 6.4 单元格语义类型

每个单元格至少支持：

```text
code
name
amount
percentage
year
date
unit
header
text
empty
unknown
```

每个类型结果记录：

- `semantic_type`
- `type_confidence`
- `type_reason`
- `numeric_value`
- `unit_hint`
- `normalization_status`

禁止：

- 仅因为内容是数字就当作金额；
- 把功能分类编码参与金额合计；
- 把普通金额或编号识别为年份；
- 把空值、横线、零值视为完全相同；
- 在资金口径不同的表格之间直接比较。

## 6.5 解析质量门禁

文档级状态：

```text
pass
warning
blocked
```

阻塞条件示例：

- report type 或 subject level 未确认；
- 关键表无法识别；
- 表格列映射置信度低于门槛；
- 数值列和编码列无法区分；
- 单位不明确；
- 证据坐标缺失；
- 解析乱码；
- 跨页表格合并存在冲突。

`blocked` 文档不得进入高风险规则和模型正式分析，只能进入人工预处理。

## 6.6 事实层验收指标

- 无 `DATABASE_URL` 环境仍可生成完整 ParsedDocument 和 EvidenceRef；
- 同一文档主链路只执行一次权威解析；
- 规则、模型、structured ingest 和 QC run 使用相同 extraction snapshot；
- 解析或持久化失败后 active snapshot 保持不变；
- 同一输入、parser version 和 config 重跑产生相同 canonical content hash；
- 重解析后历史 finding 仍能解析其原 snapshot 证据；
- evidence edge 不存在悬空逻辑引用；
- 同一文档中的重复 table code 可由不同 logical table instance 表达；
- Golden Corpus 关键表识别率不低于 98%；
- 编码被当作金额的已知回归样本为 0；
- 空值与零值已知回归样本全部通过；
- 已标注跨页表格合并准确率不低于 98%；
- deterministic finding 的证据可反查率为 100%；
- accepted AI finding 的证据可反查率为 100%。

---

# 7. 规则体系改造

## 7.1 不做一次性大重写

V2.1 推荐三步迁移：

### 第一步：建立 RuleSpec 注册表

- 保留现有规则类；
- 为所有启用规则建立外部元数据；
- 缺失元数据时启动检查失败或规则被禁用；
- 建立注册表完整性测试。

### 第二步：增加 RuleAdapter

RuleAdapter 负责：

- 校验 DocumentProfile；
- 判断规则是否适用；
- 检查 required fields；
- 构造 typed fact 或 legacy view；
- 统一处理解析失败；
- 统一生成 evidence refs；
- 统一记录 rule version。

### 第三步：按风险迁移规则实现

优先迁移：

1. 跨表金额勾稽；
2. 编码和金额区分；
3. 年份一致性；
4. 资金口径；
5. 缺表和空表；
6. 部门/单位适用性；
7. 文种残留；
8. 低风险文本规范。

## 7.2 解析失败和执行异常不得伪装成规则命中

规则结果必须区分：

```text
pass
fail
not_applicable
insufficient_data
parse_error
execution_error
```

只有 `fail` 可以成为确定性问题候选。

`insufficient_data` 和 `parse_error` 必须进入解析质量或人工复核队列，不能生成“缺表”“金额不一致”等正式问题。

`execution_error` 必须进入独立运行错误通道。无论 severity 是 hint、low、error 还是 critical，规则异常都不得进入正式 findings 数组，也不得计入 precision/recall 的业务问题分母。

## 7.3 数值计算统一

现有 `parse_number`、`tolerant_equal` 和 `calculate_dynamic_tolerance` 可以作为迁移基础，但必须统一到一个数值验证服务。

建议输出：

```text
calculation_id
operation
inputs
normalized_inputs
unit
expected_value
actual_value
difference
tolerance
result
source_evidence_refs
extraction_snapshot_id
calculator_version
```

模型输出的数值只能放入 `claimed_metrics`。

正式 finding 使用系统复算生成的 `verified_metrics`，不得静默覆盖后丢失模型原始主张。

## 7.4 规则验收指标

- 100% 启用规则存在 RuleSpec；
- 100% RuleSpec 通过完整性校验；
- 不适用规则生成正式 finding 的数量为 0；
- 解析失败被当作问题命中的数量为 0；
- 规则执行异常进入正式 findings 数组的数量为 0；
- 数值 finding 的 verified metrics 覆盖率为 100%；
- 每条 P0/P1 规则至少有正例、反例和边界样本；
- 所有规则 finding 记录 rule version、analysis run 和 extraction snapshot。

---

# 8. 专业模型接入设计

## 8.1 模型职责

模型负责：

- 语义矛盾；
- 表述方向和结构化数字方向冲突；
- 说明缺失或不完整；
- 文种和模板残留；
- 章节之间逻辑冲突；
- 规则未覆盖的新问题候选；
- 政策条款候选匹配；
- 生成供审核人员阅读的解释。

模型不负责：

- 独立决定文种、主体层级或资金口径；
- 直接完成复杂数值计算；
- 直接判断表格不存在；
- 在没有证据时生成问题；
- 直接写入正式报告；
- 直接决定训练标签。

## 8.2 结构化上下文构造器

废止把整份文档按固定字符数机械切窗作为主要输入方式。

输入单元改为：

- 一张完整表格；
- 一张逻辑合并后的跨页表格；
- 一个说明章节；
- 一个表格加其对应说明；
- 一个问题类别需要的少量相关表格和政策条款。

表格输入应包含：

```text
table_code
title
page range
unit
headers
typed rows
cell ids
parse confidence
funding scope
```

说明章节输入应包含：

```text
section_id
section title
page range
paragraphs
local spans
linked tables
```

## 8.3 两阶段模型

### 阶段一：候选发现

- 召回优先；
- 按问题类别分别调用；
- 使用结构化上下文；
- 输出严格 schema；
- 必须引用 evidence refs；
- 成功完成且没有候选时允许返回空 items，但必须同时返回明确的 completed 状态；
- confidence 阈值按问题类别配置；
- 不预设 0.3 至 0.4 为固定正确值，必须通过 Golden Corpus 校准。

### 阶段二：证据复核

系统和模型共同完成：

1. 证据 ID 是否存在；
2. 页码、表格和单元格是否匹配；
3. 引用文本是否与原文一致；
4. 数值是否可由系统复算；
5. 政策是否适用于地区、年度、文种和主体层级；
6. 是否与确定性规则冲突；
7. 是否属于解析噪声；
8. 是否重复；
9. 是否超过候选负荷预算。

## 8.4 候选处理状态

```text
candidate
evidence_verified
calculation_verified
policy_verified
conflict
manual_review
rejected
ready_for_human_review
blocked
error
```

只有 `ready_for_human_review` 可以进入审核工作台的正式候选区域。

校验失败、依赖不可用、超时或输出无法解析时，必须进入 `manual_review`、`blocked` 或 `error`，不得回退为已确认候选。

## 8.5 候选数量控制

不得简单截断超出上限的候选。

应采用：

- 风险排序；
- 问题类别配额；
- 同类去重；
- 规则和模型一致性加权；
- 低置信度候选进入 overflow；
- overflow 保留统计和抽样复核；
- P0 类问题不得因为 top-K 被丢弃。

## 8.6 失败语义与 fail-closed

所有模型、校验器、复核器、质量门禁和报告导出统一返回结果 envelope：

```text
outcome: completed | verified | rejected | needs_review | blocked | degraded | error
items
failure_code
failure_reason
dependency_status
started_at
finished_at
```

要求：

- AI 不可用时确定性规则流程继续，但 AI 阶段必须标记 `degraded` 或 `error`；
- safety-critical verifier 异常时，对应候选不得 accepted；
- parser/profile 失败时文档进入 `blocked`；
- 政策检索失败时政策类候选不得确认，但不阻断无关确定性规则；
- 不允许用空数组同时表达“无问题”和“执行失败”；
- 不允许自动切换到 mock response；
- 未知模型响应不得默认 `CONFIRM`；
- 报告 evidence drilldown 失败时不得静默生成无证据正式报告；
- 超时、重试、熔断和降级均进入 ModelInvocation；
- 模型版本升级必须支持按 job 回放和回滚。

CI 静态检查用于禁止生产代码中的 mock finding、裸 `except` 吞证据错误和 except 分支返回确认类结果，但不能替代故障注入测试。

故障注入至少覆盖：

- timeout；
- malformed output；
- parser failure；
- dependency unavailable；
- verifier exception；
- evidence lookup failure。

以上场景必须断言不会返回 pass、confirm、accepted 或伪成功空结果。

---

# 9. RAG 与政策知识库

## 9.1 第一版策略

先结构化精确过滤，再语义匹配。

第一层过滤：

```text
jurisdiction
effective_from
effective_to
report_kind
subject_level
report_type
funding_scope
policy_status
```

第二层只在过滤后的小候选集中执行：

- 关键词匹配；
- 条款标题匹配；
- embedding 检索；
- rerank；
- 模型解释。

## 9.2 政策条目字段

```text
policy_id
document_name
document_number
issuer
jurisdiction
effective_from
effective_to
report_kinds
subject_levels
funding_scopes
article_number
article_text
source_path
source_url
version
status
reviewed_by
reviewed_at
```

## 9.3 RAG 验收

- 过期政策不得进入有效候选；
- 地区不匹配政策不得进入候选；
- 文种和主体层级不匹配政策不得进入候选；
- finding 必须保存 policy id 和条款原文；
- 模型不得只凭参数知识生成政策依据；
- 政策库更新和失效有审计记录。

---

# 10. 人工审核与训练闭环

## 10.1 finding 审核状态

统一状态：

```text
pending
confirmed_issue
false_positive
missed_issue
uncertain
duplicate
not_applicable
evidence_error
```

整改包状态与问题真实性状态分开管理，不再使用同一个 status 同时表达两类业务。

## 10.2 每条审核记录

至少保存：

```text
review_record_id
annotation_id
analysis_run_id
finding_instance_id
claim_fingerprint
evidence_fingerprint
extraction_snapshot_id
status
false_positive_reason
uncertainty_reason
reviewer_note
original_severity
reviewed_severity
original_category
reviewed_category
original_evidence
reviewed_evidence
can_train
training_exclusion_reason
reviewer_id
reviewed_at
revision
previous_revision_id
second_review_status
second_reviewer_id
```

审核记录是不可变事件；修改通过新 revision 表达，不原地覆盖旧记录。

## 10.3 漏报补录

人工补录必须支持：

- 问题类别；
- 严重程度；
- 页码；
- 表格/章节；
- cell logical refs 或 section spans；
- 原文证据；
- 当前值和期望值；
- 政策依据；
- 问题说明；
- 修改建议；
- 是否存在专业争议。

漏报记录必须能参与 recall 计算。

## 10.4 训练准入

满足全部条件才允许 `can_train=true`：

- 文档画像已人工确认或高置信自动确认；
- extraction snapshot 已冻结并可反查；
- finding 状态不是 uncertain；
- evidence ref 可反查；
- 问题类别存在；
- 数值问题有 verified metrics；
- 规则或政策依据明确；
- 审核人和时间存在；
- 没有未解决的解析错误；
- 没有未解决的资金口径争议；
- 不属于 Golden Corpus；
- 不属于测试构造或 mock 数据。

## 10.5 审核状态迁移

新版本 finding 与历史审核记录对齐后，只允许三种结果：

```text
inherit
needs_revalidation
history_only
```

规则：

- claim 和 evidence fingerprint 均未变化，且生产者变更不影响结论时，允许 `inherit`；
- claim 未变化但 evidence snapshot、数值复算或政策适用性发生变化时，进入 `needs_revalidation`；
- claim 已变化、证据不可解析或无法唯一匹配时，仅保留 `history_only`；
- 禁止静默继承；
- 禁止因新 finding 未匹配而静默删除旧审核历史；
- 所有迁移记录必须保存旧、新 identity 和迁移原因。

---

# 11. Golden Corpus 与评测体系

## 11.1 分阶段建设

### 种子集：50 至 100 份

Phase 0 使用，用于：

- 跑通回放工具；
- 验证 finding 对齐；
- 复现最高频误报；
- 建立文档画像测试；
- 建立结构化解析门禁。

至少覆盖四类材料，不要求第一天严格平均。

### 正式 Golden Corpus：300 份

目标分布：

- 部门预算：75 份；
- 部门决算：75 份；
- 单位预算：75 份；
- 单位决算：75 份。

覆盖：

- 至少 3 个年度；
- 多个部门和单位；
- 无问题文档；
- 已知问题文档；
- 混合标题；
- 跨页表格；
- 空表和零值；
- 多资金口径；
- 模板变更；
- 解析异常；
- 专业争议。

## 11.2 finding 匹配规则

预测 finding 与标注 finding 视为匹配，必须同时满足：

1. 同一 document version；
2. 问题类别兼容；
3. 严重度差异不超过约定范围；
4. `claim_fingerprint` 相同或结构化 claim 字段达到约定相似度；
5. 表格问题引用相同核心 logical evidence refs，或 evidence overlap 达到约定标准；
6. 文本问题引用相同 section，且 local span 或 quote 达到相似度标准；
7. 一个预测 finding 最多匹配一个标注 finding；
8. 多个重复预测只计一个 TP，其余计重复或 FP。

现有 `finding_instance_id`、数据库自增 cell ID、时间戳和结果顺序不得参与跨运行匹配。

匹配算法、fingerprint schema version、阈值和冲突处理规则必须固定并进入评测报告。

## 11.3 指标分母

召回率分母：

- Golden Corpus 中人工标注的 confirmed issue 和 missed issue；
- 专业争议 `uncertain` 不进入主召回率；
- 通过二次抽检发现的隐藏漏报加入标注后，再进入后续版本分母。

精确率分母：

- 系统输出的全部候选或 evidence-verified finding；
- 分阶段分别计算，不能混用。

无问题文档：

- 计算每文档 FP 数；
- 计算被错误标为“存在高风险问题”的文档比例；
- 至少 10% 由第二审核人抽检，用于估计隐藏漏报。

## 11.4 分层指标

每次报告至少按以下维度拆分：

- 四类材料；
- 问题类别；
- P0/P1/P2；
- 规则 finding 和模型 finding；
- 年度；
- 模板版本；
- 有无解析 warning；
- 自动画像和人工确认画像。

## 11.5 准确率门槛

### 文档画像

| 指标 | 门槛 |
|---|---:|
| report kind 准确率 | ≥99.5% |
| subject level 准确率 | ≥99.5% |
| 静默错路由 | 0 |
| 冲突可解释率 | 100% |

### 确定性规则

| 指标 | 门槛 |
|---|---:|
| P0 召回率 | ≥98% |
| P0 精确率 | ≥95% |
| P1 召回率 | ≥95% |
| P1 精确率 | ≥90% |
| 数值复算覆盖率 | 100% |
| 正式 finding 证据可定位率 | 100% |

### 模型候选与复核

| 指标 | 候选发现 | 证据复核后 |
|---|---:|---:|
| P0/P1 总体召回率 | ≥97% | ≥95% |
| 总体召回率 | ≥92% | ≥90% |
| 精确率 | ≥70% | ≥85% |
| 证据可反查率 | ≥95% | 100% |
| 无依据 accepted finding | 不适用 | 0 |

附加规则：

- 样本数不少于 20 的主要问题类别必须单独达标；
- 任一主要类别召回率不得低于 90%，除非书面批准；
- 不以总体平均掩盖某一类别的系统性漏报；
- P0 出现系统性漏报时直接 NO-GO。

## 11.6 人工负荷指标

生产稳定期：

- 目标 100 份/天；
- 单份人工复核中位时间不超过 5 分钟；
- 人工复核分钟数作为主要 SLO；
- evidence-verified 候选中位数不超过 5 条；
- P95 不超过 12 条；
- 争议问题单独进入专家或延迟队列；
- overflow 有完整统计；
- 同时报告 TP review load、FP review load、争议处理时间和漏报补录时间。

候选数量必须与召回率、精确率和真实问题密度做一致性演算：

```text
预计候选数 = 每份真实问题数 × 召回率 ÷ 精确率
```

例如每份真实问题 5 条、召回率 95%、证据复核后精确率 85% 时，预计候选约为 5.59 条。因此“中位数不超过 5 条”只能在真实问题密度、类别配额和人工时间同时满足时作为 GO 门槛，不能与准确率指标割裂验收。

标注建设期：

- 目标 30 至 40 份/天；
- 允许更长审核时间；
- 必须补录漏报；
- 必须记录误报原因；
- 不以生产吞吐考核标注人员。

---

# 12. 回放与评测工具

## 12.1 建议新增工具

```text
scripts/replay_golden_corpus.py
scripts/evaluate_golden_corpus.py
scripts/compare_analysis_versions.py
scripts/export_training_dataset.py
scripts/validate_rule_registry.py
scripts/validate_evidence_refs.py
```

## 12.2 回放输入

```text
corpus manifest
PDF storage keys
document profile labels
finding annotations
parser version
parser config hash
extraction snapshot id
fingerprint schema version
rule registry version
model config
prompt version
policy snapshot
```

## 12.3 回放输出

```text
run manifest
per-document results
matched findings
unmatched predictions
missed annotations
precision/recall/F1
per-category metrics
evidence resolution report
candidate load report
latency and token report
version diff
GO/NO-GO summary
```

## 12.4 可重复性

- 每次回放有唯一 analysis run id；
- finding instance ID 只保证运行内唯一，跨运行对齐使用 claim/evidence fingerprint；
- 输入 manifest 计算 hash；
- extraction snapshot、fingerprint schema 和 producer signature 固定；
- 规则、模型、提示词、政策和解析器版本固定；
- 允许对 AI 非确定性结果运行多次稳定性评估；
- 输出文件不可静默覆盖；
- 评测工具本身有单元测试和小型 fixture；
- replay CLI 骨架可在身份契约冻结前开发，但正式标注匹配和基线签署必须使用已冻结 fingerprint schema。

---

# 13. 分阶段实施计划

## Phase 0：身份契约、评测基础和种子基线，2 至 3 周

### 目标

先冻结后续标注、回放和证据迁移依赖的身份与 schema，再让系统当前表现可以被重复、自动、客观地测量。

### 第 1 周必须冻结的六项

1. 五层 finding/annotation 身份契约，并补充 `evidence_fingerprint`、`fingerprint_schema_version` 和 `producer_signature`。
2. 与存储后端无关的 document/table/cell 逻辑身份，含整数 cell ID 迁移和历史映射方案。
3. 不可变 ExtractionSnapshot、先完整解析再事务持久化、active snapshot 原子切换和并发控制。
4. 审核状态迁移三态规则：`inherit / needs_revalidation / history_only`。
5. 全仓 mock finding 与 fail-open 通路清零方案，以及 fail-closed 契约测试范围。
6. 活、死、待判定代码三态表，作为后续所有 Phase 改造范围依据。

### 其他工作项

- 冻结当前代码、规则、解析器、模型和提示词版本；
- 建立 corpus manifest；
- 选择 50 至 100 份种子材料；
- 定义 finding 匹配协议；
- 开发批量回放工具骨架；
- 开发 precision/recall 和候选负荷计算；
- 增加混合标题、subject level 和 unknown 路由测试；
- 设计 DocumentProfile、EvidenceRef、ExtractionSnapshot 和 ReviewRecord schema；
- 输出当前 ID 和证据引用风险清单。

### 冻结前允许和禁止事项

允许：

- 选择种子材料；
- 开发 replay CLI 骨架；
- 冻结当前代码、规则、模型和 parser 版本；
- 增加混合标题和 unknown 回归测试；
- 使用临时 evaluation adapter 观察当前输出，但不得签署正式标注基线。

禁止：

- 依赖现有 finding ID 或整数 cell ID 开始批量人工标注；
- 宣告 Phase 0 schema 已冻结；
- 将现有 `source_cell_ids/evidence_cells` 直接作为长期证据锚点；
- 删除旧证据或旧代码子系统；
- 开始模型 POC、训练或硬件采购。

### 验收

- 身份、snapshot、审核迁移和 fail-closed schema 评审通过；
- 种子文档有唯一 document/version key；
- replay CLI 骨架可以读取 corpus manifest；
- 指标计算模块有最小 fixture；
- 当前已知误报、混合标题和不稳定 ID 问题可稳定复现；
- 活、死、待判定代码清单经技术负责人确认；
- 不要求此阶段完成 300 份全量标注。

### 交付物

- identity contract；
- ExtractionSnapshot schema 和迁移设计；
- evidence logical key 和历史映射设计；
- Golden Corpus seed manifest；
- annotation/review schema；
- replay CLI 骨架；
- evaluation report V0 结构；
- P0 回归和故障注入测试清单；
- 活、死、待判定代码清单。

## Phase 1A：统一画像、纯解析器和稳定身份，3 至 4 周

### 目标

建立单一文档画像、脱库 canonical parser、不可变 snapshot 和稳定业务身份。

### 工作项

- 实现 `DocumentProfileResolver`；
- 统一 report kind、subject level、report type；
- 消除 `common_rules` 默认 final 和各入口重复推断；
- 修复混合标题判定；
- 将 canonical parser 改为返回完整 ParsedDocument 的纯解析器；
- 数据库写入降为可选 persistence sink；
- 实现 ExtractionSnapshot schema、building/validated 状态和 active pointer 接口；
- 实现 document/table/cell/section logical IDs；
- 增加 `logical_table_instance_id`；
- 建立旧整数 cell ID 到 logical ref 的历史映射；
- 实现 claim/evidence fingerprint；
- 建立 legacy page tables adapter 第一版；
- 清理生产路径 mock finding 和 fail-open；
- 完成旧混合子系统删除或隔离判定；
- 增加 identity、profile、snapshot 和 parser regression tests。

### 验收

- 静默错路由为 0；
- 混合标题测试全部通过；
- 无 `DATABASE_URL` 环境可生成完整 ParsedDocument 和 EvidenceRef；
- parser 不再 delete-before-parse；
- 相同确定性输入重跑产生稳定 claim/evidence fingerprint；
- 同一文档可表达重复 table code 的不同表实例；
- 解析失败不改变现有 active snapshot；
- 生产 service 中不存在 mock finding 返回路径；
- fail-closed 故障契约测试通过；
- 活、死、待判定清单与实际 import/entrypoint 一致。

### 回滚

- 保留 legacy parser feature flag；
- 保留 legacy adapter；
- 新旧解析结果可在 seed corpus 上双跑比较；
- 旧证据只读保留，直到历史映射验收完成。

## Phase 1B：最小语义类型、质量门禁和原子发布，2 至 3 周

### 目标

让主链路、structured ingest、规则、QC 和报告真正消费同一 snapshot，并具备阻止错误数据发布的最小语义能力。

### 工作项

- 主分析路径改为使用 canonical parser；
- structured ingest 持久化同一 ParsedDocument；
- cells、table instances、column mappings 和 facts 在同一发布事务中写入，并在事务提交后原子切换 active pointer；
- 增加 P0/P1 规则依赖的最小单元格语义类型；
- 建立解析 pass/warning/blocked 门禁；
- 接入 legacy adapter 双跑；
- QC run 和 findings 固定引用 extraction snapshot；
- 将 `source_cell_ids/evidence_cells` 迁移到 logical evidence edge；
- 修复 report/export evidence fail-open；
- 增加并发重解析、失败回滚和历史 drilldown 测试。

### 验收

- 主链路只产生一份权威解析；
- 规则、structured ingest、QC 和报告使用同一 snapshot；
- cells/tables/facts 不会出现跨 snapshot 混合；
- 持久化任一阶段失败时 active snapshot 保持不变；
- 规则异常不进入正式 findings；
- 代码不再把已知编码列当金额；
- accepted finding 可以反查到稳定证据；
- 历史 finding 在新 snapshot 激活后仍能反查原证据；
- 报告证据读取失败时明确 blocked/degraded，不生成伪完整正式报告。

## Phase 2：审核闭环与正式 Golden Corpus，3 至 4 周

### 目标

让人工审核结果成为可追溯的业务数据和训练数据来源。

### 依赖

DocumentProfile、EvidenceRef、ExtractionSnapshot、identity contract 和 ReviewRecord schema 已冻结，Phase 1A 的稳定身份与证据解析验收已通过。

### 工作项

- 扩展 finding truth status；
- 增加误报原因；
- 增加漏报补录；
- 增加证据修正；
- 增加争议和 not applicable；
- 增加 can train 和排除原因；
- 保存审核人、二次审核和修改历史；
- 区分整改包状态与 finding truth 状态；
- 扩展 Golden Corpus 到 300 份；
- 建立 10% 二次抽检；
- 建立训练数据导出和质量扫描。

### 验收

- 人工漏报可以完整保存；
- 每次修改有 revision；
- 误报原因可统计；
- uncertain 不进入训练集；
- Golden Corpus 与训练集隔离；
- 300 份材料完成画像确认；
- 指标可按四类材料和问题类别拆分。

### 并行关系

Phase 2 只能在 Phase 1A 身份和 EvidenceRef 实现验收后开始批量标注；审核工作台开发可提前并行，但不得提前绑定现有随机 finding ID 或整数 cell ID。

## Phase 3：规则元数据与高风险规则迁移，3 至 5 周

### 目标

让现有规则具备明确适用范围、失败语义和证据契约。

### 工作项

- 建立 RuleSpec 注册表；
- 建立 registry completeness gate；
- 建立 RuleAdapter；
- 为所有启用规则补齐适用范围；
- 按风险迁移 P0/P1 规则；
- 建立统一数值验证服务；
- 清理最高频误报规则；
- 对每条 P0/P1 规则补充正反例和边界样本；
- 输出规则版本差异报告。

### 验收

- 所有启用规则有完整 RuleSpec；
- 不适用规则正式 finding 为 0；
- 解析失败不会成为规则命中；
- P0/P1 数值 finding 全部复算；
- 规则指标达到第 11 章门槛；
- seed corpus 和正式 Golden Corpus 均无系统性倒退。

## Phase 4：模型候选发现与证据复核 POC，4 至 5 周

### 目标

验证结构化输入和两阶段模型是否能在可控人工负荷下提高召回率。

### 工作项

- 建立 structured context builder；
- 按问题类别设计 prompt；
- 建立严格输出 schema；
- 建立 ModelInvocation 审计；
- 建立 evidence verifier；
- 建立 claimed/verified metrics；
- 建立规则冲突检测；
- 参数化候选阈值；
- 建立风险排序、类别配额和 overflow；
- 使用云模型、9B 级候选和 27B 级候选运行统一基准；候选 checkpoint 在 benchmark 冻结时按当期官方版本、许可和推理框架兼容性确定，不在计划中提前锁定具体型号；
- 输出质量、吞吐、token 和人工负荷报告。

### 验收

- 无证据候选不会进入 ready for human review；
- 数值候选全部经过系统复算；
- accepted finding 的证据可反查率为 100%；
- 候选和复核指标达到阶段门槛；
- 人工候选负荷不超过约定阈值；
- 9B 级、27B 级和云模型使用同一输入、schema 和 Golden Corpus 比较；
- 明确是否需要本地部署和 LoRA。

## Phase 5：政策知识库与 shadow，3 至 4 周

### 目标

验证结构化 RAG 和新旧版本并行运行能力。

### 工作项

- 建立上海政策结构化表；
- 建立有效期和适用范围过滤；
- 在过滤后候选集上进行语义检索；
- 新旧规则和模型 shadow；
- 建立版本差异、回滚和周报；
- 记录审核时间、误报、漏报和争议；
- 建立政策维护责任和更新时限。

### 验收

- 过期或不适用政策不会被引用；
- 所有政策 finding 可追溯到条款；
- shadow 不影响正式报告；
- 模型、提示词和政策版本可回滚；
- 连续多个回放批次指标稳定。

## Phase 6：本地推理节点，2 至 3 周

### 启动门禁

必须全部满足：

- Phase 4 模型 POC 达标；
- 模型和量化格式已经确定；
- structured context 输入格式已冻结；
- 固定 100 份 benchmark bundle 已准备；
- 目标 OpenAI-compatible API 已验证；
- 本地硬件预算和采购责任已批准。

### 工作项

- 完成实机或可靠远程实测；
- 比较 Mac mini M5 Pro 与 RTX 4090 方案；
- 部署 OpenAI-compatible API；
- 接入模型健康检查；
- 完成 100 份夜间批量压测；
- 完成 24 小时压力测试；
- 完成断网、超时、模型崩溃和回滚演练。

### 验收

- 100 份典型材料不超过 12 小时；
- 连续 3 批无崩溃；
- 24 小时无持续内存泄漏和异常降速；
- AI 故障状态明确；
- 不发生系统级 swap 风暴；
- 日志包含模型版本、输入 hash、耗时和错误；
- 模型不可用时不返回伪空结果。

## Phase 7：LoRA/QLoRA，可选，4 至 6 周

### 启动门禁

- 可训练高质量问题不少于 1000 条；
- hard negative 不少于 500 条；
- 人工补录漏报不少于 200 条；
- 主要问题类别至少 30 至 50 条；
- Golden Corpus 已冻结且未进入训练；
- prompt、RAG 和规则优化仍无法达到目标；
- 专业争议已隔离；
- 云训练预算批准。

### 工作项

- 数据自动清洗；
- train/validation/test 隔离；
- 9B QLoRA；
- 必要时再评估 27B LoRA；
- 量化；
- Golden Corpus 回放；
- 模型注册和回滚；
- 与未微调模型做显著性和成本比较。

### 验收

- 微调模型在目标类别上有稳定提升；
- Golden Corpus 无 P0 倒退；
- 提升足以覆盖训练和部署成本；
- 量化后仍达到指标；
- 旧模型可一键回滚。

## Phase 8：上海正式 shadow 与试点，4 至 6 周

### 工作项

- 新旧版本并行；
- 全部正式问题人工确认；
- 每周统计误报、漏报、审核时间和 overflow；
- 二次抽检无问题材料；
- 修正规则、提示词和政策库；
- 完成发布和回滚演练；
- 连续两个业务批次稳定后评估正式使用。

### 验收

- 连续 4 周满足准确率和负荷门槛；
- 没有系统性 P0 漏报；
- 生产吞吐目标经真实审核验证；
- 规则、模型、政策和解析版本全部可追溯；
- 运维、备份、恢复和回滚流程通过；
- 业务负责人签署 GO。

---

# 14. 里程碑与预计工期

在一名主要工程负责人和一名主要审核人员的前提下：

| 里程碑 | 预计时间 |
|---|---:|
| 身份契约、种子材料和回放骨架 | 2 至 3 周 |
| Phase 1A/1B 单一事实层与稳定证据 | 5 至 7 周 |
| 模型无关的可信事实层和高风险规则版本 | 10 至 15 周 |
| 模型辅助内部测试版 | 14 至 20 周 |
| 上海稳定 shadow | 21 至 29 周 |
| LoRA | 额外 4 至 6 周，按门禁决定 |

并行原则：

- 审核工作台 schema 和 UI 可与 Phase 1 并行，但批量标注必须等待 Phase 1A 身份验收；
- 规则元数据整理可与事实层开发并行，但规则实现迁移依赖 canonical adapter；
- 政策资料整理可以提前进行，但 RAG 接入依赖 DocumentProfile；
- replay CLI 骨架可提前开发，正式指标签署依赖 fingerprint schema；
- 硬件调研可以进行，但不得在 Phase 4 结论前锁定采购。

导致工期延长的主要因素：

- 只有一名审核人员；
- 历史 Excel 无法回到原 PDF；
- 300 份材料需要重新补漏报；
- 历史证据映射质量低于预期；
- 结构化解析在不同年度模板上差异较大；
- 缺少财政专业人员裁决；
- 需要迁移的高风险规则数量超过预期；
- 模型候选无法满足人工负荷门槛。

---

# 15. 人员与职责

## 15.1 技术负责人

- DocumentProfile；
- canonical parser；
- evidence contract；
- RuleAdapter；
- 回放评测；
- 模型接入；
- 发布和回滚。

## 15.2 审核人员

- 确认文种和主体层级；
- 标注确认问题、误报和漏报；
- 记录误报原因；
- 标记专业争议；
- 完成二次抽检；
- 评估人工负荷。

## 15.3 规则维护人员

- 整理 RuleSpec；
- 确认适用范围；
- 维护正反例；
- 解释资金口径；
- 审核规则版本差异。

## 15.4 政策资料责任人

- 收集政策；
- 维护有效期；
- 维护地区、文种和主体层级；
- 保存来源；
- 在政策变化后 5 个工作日内更新。

## 15.5 外部专家

建议：

- 在 Golden Corpus 冻结前抽检；
- 在争议类别形成后集中裁决；
- 在上海正式试点前复核 P0/P1 定义；
- 抽检比例 5% 至 10%。

如果没有专家：

- uncertain 不进入训练；
- 不宣称模型具有权威财政判断能力；
- 高争议政策类问题不自动进入正式报告。

---

# 16. 硬件与部署计划

## 16.1 当前采购结论

本章给出的是采购门禁和候选配置，不构成对具体型号发布状态、市场售价或供货情况的事实确认。

- 暂不下单；
- 计划等待至 2026-09-22 后再做实机或可靠独立实测；
- 在模型 POC 前不锁定平台；
- 保留 RTX 4090 工作站回退方案；
- 不采购来源不明设备，也不以降低电源、质保和内存规格压缩预算；
- 采购前必须重新核验厂商官方规格、实际报价、许可和目标模型实测。

## 16.2 Mac mini M5 Pro 候选

建议候选配置：

- M5 Pro；
- 64GB 统一内存；
- 1TB SSD；
- 有线网络；
- UPS；
- 本地 SSD 放运行模型；
- NAS 放备份、PDF、日志和 Golden Corpus。

采购条件：

- 64GB/1TB 整机不超过 2 万元；
- 真实目标模型通过测试；
- 100 份批次在 12 小时内完成；
- 24 小时压力测试通过；
- OpenAI-compatible API 稳定；
- 内存余量满足上下文和并发要求。

## 16.3 RTX 4090 回退

适用条件：

- Mac 吞吐不达标；
- 后续要求本地 LoRA；
- 需要更高并发；
- 4090 整机能够在预算和质保要求内取得；
- 现有 CUDA 推理栈明显更稳定。

建议：

- RTX 4090 24GB；
- 128GB 系统内存；
- 2TB NVMe；
- 1000W 至 1200W 金牌电源；
- Ubuntu LTS；
- 有线网络；
- UPS；
- 合法来源和完整质保。

## 16.4 固定 benchmark bundle

采购验收必须使用固定版本：

- 100 份真实脱敏材料；
- 四类材料混合；
- 20 至 30 页典型长度；
- 固定 parser 和 extraction hash；
- 固定 structured context；
- 固定 prompt 和 schema；
- 固定并发；
- 固定量化模型；
- 固定日志采集；
- 记录 P50/P95、峰值内存、总耗时和失败率。

---

# 17. 重点代码改造位置

## 17.1 三态清单原则

所有 Phase 的改造范围以活、死候选、待判定三态清单为依据。文件名相似、包含 mock 或位于 `src/` 下均不能单独证明它属于生产链路或可以删除。

## 17.2 已确认活链路

| 范围 | 文件 |
|---|---|
| 上传、画像与主分析 | `api/runtime.py`、`api/routes/upload.py`、`api/main.py` |
| 规则主链路 | `src/engine/pipeline.py`、`src/engine/common_rules.py`、`src/engine/rules_v33.py`、`src/engine/budget_rules.py`、`src/services/engine_rule_runner.py` |
| 解析与结构化事实 | `src/services/pdf_parser.py`、`src/services/structured_ingest_runner.py`、`src/services/table_recognizer.py`、`src/services/fiscal_fact_materializer.py` |
| QC 与报告 | `src/services/job_orchestrator.py`、`src/qc/runner_v2.py`、`src/qc/runner_v3.py`、`src/services/pdf_generator.py` |
| AI 与合并 | `src/services/ai_findings.py`、`src/services/analyze_dual.py`、`src/services/merge_findings.py` |
| 审核与持久化 | `src/services/issue_workflow_store.py`、`src/services/analysis_result_store.py`、`src/schemas/issues.py`、`src/db/migrations.py` |
| 前端 | `app/app/components/BatchUploadModal.tsx`、审核工作台和报告相关组件 |

## 17.3 死代码候选子系统

当前静态分析未发现生产主链路入口：

- `src/engine/hybrid_pipeline.py`
- `src/engine/hybrid_validator.py`
- `src/engine/ai_validator.py`
- `src/engine/intelligent_merger.py`
- `src/engine/rule_adapter.py`
- `src/services/rule_findings.py`
- `src/services/ai_rule_runner.py`

这些文件应作为一个旧混合子系统整体判断，避免只删除其中一个文件后留下不可导入残片。

## 17.4 待判定范围

- `src/qc/runner.py` 与 V2/V3 runner 的兼容职责；
- `src/qc/__init__.py` 对旧 runner 和 drilldown 的导出；
- 测试、脚本或部署配置中可能存在的动态导入；
- 历史 migration、报告读取和运维脚本对旧字段的依赖。

## 17.5 删除门禁

删除死代码前必须完成：

1. 静态 import/caller 图扫描；
2. 动态 import、配置、entrypoint 和反射调用扫描；
3. seed corpus 双跑；
4. 运行覆盖或调用日志确认；
5. 删除 migration/兼容说明；
6. Git 回滚路径；
7. CI 阻止死模块被重新导入。

“seed corpus 没有调用”不能单独证明代码可删除。

## 17.6 建议新增模块

```text
src/schemas/document_profile.py
src/schemas/extraction_snapshot.py
src/schemas/evidence.py
src/schemas/finding_identity.py
src/schemas/review_record.py
src/services/document_profile_resolver.py
src/services/canonical_document_parser.py
src/services/extraction_snapshot_store.py
src/services/legacy_page_tables_adapter.py
src/services/evidence_identity.py
src/services/evidence_verifier.py
src/services/numeric_verifier.py
src/services/structured_context_builder.py
src/services/rule_registry.py
src/services/model_invocation_store.py
config/rule_registry/
config/prompt_registry/
config/policy_registry/
scripts/replay_golden_corpus.py
scripts/evaluate_golden_corpus.py
scripts/compare_analysis_versions.py
scripts/export_training_dataset.py
scripts/validate_rule_registry.py
scripts/validate_evidence_refs.py
scripts/audit_dead_code_paths.py
```

数据库建议新增：

```text
extraction_snapshots
extraction_snapshot_tables
extraction_snapshot_cells
finding_evidence_refs
fact_evidence_refs
legacy_cell_id_map
review_migrations
```

`finding_evidence_refs` 和 `fact_evidence_refs` 应使用真实外键表达证据边；现有 `BIGINT[]` 只作为迁移期兼容字段。

是否使用以上精确文件名可在实施时结合现有模块边界调整，但职责不可缺失。

---

# 18. 发布、回滚与质量门禁

## 18.1 Feature Flags

建议：

```text
GBC_DOCUMENT_PROFILE_V2
GBC_CANONICAL_PARSER
GBC_EXTRACTION_SNAPSHOT_V2
GBC_LOGICAL_EVIDENCE_IDS
GBC_LEGACY_TABLE_ADAPTER
GBC_RULE_REGISTRY
GBC_MODEL_CANDIDATE_V2
GBC_EVIDENCE_VERIFIER
GBC_FAIL_CLOSED_GATES
GBC_POLICY_RAG
```

## 18.2 双跑策略

迁移期间：

- 新旧解析双跑，但旧解析和新解析写入不同 snapshot/namespace；
- 新 snapshot 未通过门禁前不得切换 active 指针；
- 新旧规则结果对比；
- 新模型 shadow；
- 新结果不自动替换正式结果；
- 差异报告按 document/rule/category/fingerprint 输出；
- 双跑必须检查 evidence resolution、orphan refs 和 snapshot 混用；
- 只有达到门禁才提高新链路流量。

## 18.3 回滚

- 文档画像 resolver 可回滚到上一版本；
- parser 可通过 flag 回退；
- active snapshot 可原子回指上一 validated snapshot；
- snapshot 和历史 evidence 不做原地修改；
- rule registry 可回滚；
- 模型和 prompt 可按版本切换；
- 政策 snapshot 可回滚；
- 数据库 migration 必须先扩展、后回填、再切换读取、最后停止旧写入；
- 新字段不得破坏历史报告读取；
- 不删除旧证据，直到迁移和历史映射验收完成；
- 死代码删除通过 Git 恢复，数据库兼容通过 migration 说明恢复。

## 18.4 最终质量门禁

任何版本存在以下情况之一即 NO-GO：

- 静默错路由；
- claim/evidence fingerprint 依赖时间戳、随机 UUID 或结果顺序；
- accepted finding 无证据；
- evidence ref 悬空或解析到错误 snapshot；
- 同一 run 混用多个未声明 snapshot；
- 数值 finding 未复算；
- P0 系统性漏报；
- 解析失败或规则异常伪装成业务 finding；
- 模型或验证器失败伪装成成功空结果；
- 报告 evidence 失败后仍生成伪完整正式报告；
- Golden Corpus 泄漏到训练集；
- 审核状态静默继承或静默丢失；
- 无法回滚；
- 人工负荷超过门槛；
- 正式结果无法追溯版本、生产者和 extraction snapshot。

---

# 19. 风险与控制

## 风险一：事实层迁移导致现有规则倒退

控制：

- legacy adapter；
- seed corpus 双跑；
- 按规则分批迁移；
- feature flag；
- 不一次性删除旧链路。

## 风险二：文档画像准确率高但覆盖率低

控制：

- 允许 unknown 和人工确认；
- 先保证零静默错路由；
- 记录冲突原因；
- 按年度和模板补充识别规则。

## 风险三：Golden Corpus 标注速度不足

控制：

- 先 50 至 100 份种子集；
- 生产吞吐与标注吞吐分开；
- 优先标注高频、高风险类别；
- 使用二次抽检估计隐藏漏报；
- 不为了数量降低证据要求。

## 风险四：规则注册表成为新的维护负担

控制：

- 使用 schema 校验；
- 生成缺失元数据报告；
- 元数据与测试 case 绑定；
- CI 阻止无 metadata 规则启用；
- 不在注册表重复实现规则逻辑。

## 风险五：模型 confidence 不可信

控制：

- 不把 confidence 当作真实性；
- 按类别校准阈值；
- 强制证据校验；
- 保留 overflow；
- shadow 阶段记录 calibration curve。

## 风险六：raw output 包含敏感信息

控制：

- 调用级存储；
- 权限隔离；
- 脱敏；
- 设置保留周期；
- 导出训练数据时二次扫描；
- 不把 raw output 复制到每条 finding。

## 风险七：没有财政专业人员

控制：

- uncertain 隔离；
- 外部专家抽检；
- 争议问题不自动训练；
- 保守描述模型能力；
- 高风险政策问题始终人工确认。

## 风险八：硬件采购后模型方案变化

控制：

- 先云端 POC；
- 固定 benchmark；
- API 解耦；
- 延后采购；
- 保留 4090 回退；
- 不把训练能力作为 Mac 方案必选项。

## 风险九：历史证据在重解析后悬空

控制：

- 不可变 ExtractionSnapshot；
- logical evidence refs；
- 历史整数 ID 映射；
- 外键化 evidence edge；
- 每次 migration 后运行 orphan 检查；
- 未完成映射前禁止删除旧 cells。

## 风险十：并发解析切换错误 snapshot

控制：

- advisory lock 或幂等键；
- active pointer compare-and-swap；
- snapshot 状态机；
- 激活前质量门禁；
- 并发和乱序完成测试。

## 风险十一：fail-closed 被实现成全系统不可用

控制：

- 按决策边界区分 blocked、needs_review、degraded 和 error；
- 可选 AI 故障不阻断确定性规则；
- safety-critical verifier 故障不得放行候选；
- 结果 envelope 区分无问题和执行失败；
- 故障注入契约测试。

## 风险十二：死代码与活代码混排导致改错路径

控制：

- 活、死、待判定三态表；
- 静态和动态入口扫描；
- seed corpus 双跑；
- 删除说明和 CI import policy；
- 不以文件名或是否包含 mock 单独判断生产状态。

---

# 20. 近期四周最小行动清单

## 第 1 周：冻结六项承重契约

1. 冻结五层 finding/annotation 身份契约，并补 `evidence_fingerprint`。
2. 冻结后端无关的 document/table/cell 逻辑身份和历史映射方案。
3. 冻结 ExtractionSnapshot、事务持久化、active pointer 和并发控制方案。
4. 冻结审核迁移三态规则。
5. 冻结 mock/fail-open 清理范围和 fail-closed 契约测试。
6. 冻结活、死、待判定代码三态表。

并行完成：

- 冻结当前代码、规则、模型和 parser 版本；
- 选择 50 份种子材料；
- 收集混合标题和 unknown 样本；
- 建立 replay CLI 骨架；
- 增加混合标题回归测试。

第 1 周不得开始：

- 批量人工标注；
- 使用现有 finding ID 或整数 cell ID 作为长期锚点；
- 宣告 schema 已冻结；
- 删除死代码候选。

## 第 2 周：schema、迁移和评测骨架

- 建立 corpus manifest；
- 完成 identity、snapshot、evidence edge 和 review migration schema；
- 设计整数 cell ID 历史回填和映射 migration；
- 开发 replay/evaluation CLI 最小版本；
- 增加 finding ID 稳定性和碰撞测试；
- 增加 timeout、malformed output、dependency unavailable 等故障注入测试；
- 完成动态 import、配置和 entrypoint 扫描；
- 确认生产期和标注期人员容量。

## 第 3 周：纯 parser 与画像第一版

- 实现 DocumentProfileResolver 第一版；
- 消除 unknown 默认 final；
- 接通 subject level 后端字段；
- 实现脱库 canonical parser 返回结构；
- 实现 extraction snapshot content hash；
- 实现 logical table/cell identity 第一版；
- 删除或隔离已确认 mock/fail-open 路径；
- 验证无 `DATABASE_URL` 环境解析。

## 第 4 周：snapshot 发布原型和第一次受控回放

- 实现 persistence sink 和 building/validated/active 状态；
- 验证解析失败不改变 active snapshot；
- 验证同输入重跑 fingerprint 稳定；
- 验证重复 table code 可区分；
- 运行种子集受控回放，不签署依赖旧 ID 的正式标注基线；
- 输出 V0 技术基线和证据风险报告；
- 确认是否达到 Phase 1A 继续实施条件。

---

# 21. Phase 门禁清单

## 进入批量人工标注前

- [ ] identity contract 已冻结
- [ ] fingerprint schema version 已冻结
- [ ] claim/evidence fingerprint 稳定性测试通过
- [ ] ExtractionSnapshot schema 已冻结
- [ ] logical table/cell refs 可反查
- [ ] 旧整数 cell ID 历史映射方案已评审
- [ ] annotation/review schema 已冻结
- [ ] 审核迁移三态规则已冻结
- [ ] replay matcher 不依赖现有 finding instance ID
- [ ] parser 失败不会删除 active evidence

## 进入模型 POC 前

- [ ] 文档画像单一入口
- [ ] 静默错路由为 0
- [ ] subject level 端到端持久化
- [ ] canonical parser 已成为主入口
- [ ] canonical parser 无数据库也可产出完整 EvidenceRef
- [ ] 不可变 ExtractionSnapshot 已生效
- [ ] 规则、structured ingest、QC 和报告共享 snapshot
- [ ] EvidenceRef 可反查且无 orphan refs
- [ ] 规则异常不进入正式 findings
- [ ] AI mock 和 fail-open 路径已删除或隔离
- [ ] 报告 evidence fail-closed 已生效
- [ ] 活、死、待判定代码表已验收
- [ ] 种子集可一键回放
- [ ] 指标可自动计算
- [ ] 解析 blocked 状态生效

## 进入本地硬件采购前

- [ ] 模型 POC 达标
- [ ] 9B 级/27B 级/云模型对比完成
- [ ] structured context 已冻结
- [ ] evidence verifier 达标
- [ ] 候选负荷达标
- [ ] 100 份 benchmark bundle 固定
- [ ] OpenAI-compatible API 验证完成
- [ ] 采购报价和质保确认

## 进入 LoRA 前

- [ ] 可训练样本达到门槛
- [ ] hard negative 达到门槛
- [ ] 漏报样本达到门槛
- [ ] Golden Corpus 与训练集隔离
- [ ] prompt/RAG/规则优化仍不足
- [ ] 专业争议隔离
- [ ] 云训练预算批准

## 进入上海正式试点前

- [ ] 连续 4 周 shadow 达标
- [ ] 无系统性 P0 漏报
- [ ] 人工负荷达标
- [ ] 100% 正式问题可追溯到固定 snapshot
- [ ] 历史 finding 和审核记录迁移抽检通过
- [ ] 备份、恢复和回滚演练通过
- [ ] 业务负责人签署

---

# 22. 启动前待确认事项

1. 谁负责批准 P0/P1/P2 定义和问题分类目录？
2. 谁负责批准 identity contract 和 fingerprint schema 的后续版本变更？
3. 唯一审核人员是否有权确认一般问题的最终标签？
4. 专业争议是否能定期邀请财政或会计专家裁决？
5. 50 至 100 份种子集是否可以关联原 PDF、页码和历史问题？
6. 300 份 Golden Corpus 的标注工期是否允许超过两周？
7. 标注建设期是否接受 30 至 40 份/天，而不是 100 份/天？
8. 哪些问题类别属于不得被 top-K 丢弃的 P0？
9. 无问题文档的二次抽检比例是否确认至少 10%？
10. PostgreSQL 是否只作为 canonical parser 的 persistence sink，而不是 parser 的运行前提？
11. ExtractionSnapshot 的保留周期、归档和清理责任如何规定？
12. 当前 31,080 个本地 cells 和历史环境数据是否需要全部回填 logical IDs？
13. 并发重解析采用 advisory lock 还是 compare-and-swap？
14. 现有 `dept_budget/dept_final` 外部接口需要兼容多久？
15. raw model output 的保留周期和访问权限如何规定？
16. 2 万元是否只包含主机，还是包含 UPS、网络和备份？
17. 本地节点是否允许白天补跑失败批次？
18. 上海正式试点的业务签署人是谁？

未回答时默认：

- 一般问题由现有审核人员确认；
- identity/fingerprint 变更必须由技术负责人书面批准并升级 schema version；
- 专业争议标记 uncertain；
- 种子集先做 50 份；
- 标注期按 30 份/天规划；
- 二次抽检 10%；
- canonical parser 必须脱库运行，PostgreSQL 作为可选 persistence sink；
- 已被 finding、审核或 Golden Corpus 引用的 snapshot 长期保留；
- 并发解析先采用数据库 advisory lock 加 active pointer compare-and-swap；
- 2 万元只用于主机；
- 旧接口至少兼容一个正式发布周期。

---

# 23. 最终结论

GovBudgetChecker 建设专业模型辅助审查能力的方向成立，但项目成败首先取决于：

1. 文档画像是否统一；
2. finding、annotation 和 evidence 是否具有稳定、分层、可版本化的业务身份；
3. canonical parser 是否可以脱库运行并生成不可变 ExtractionSnapshot；
4. 结构化事实层是否成为唯一事实来源；
5. 历史 finding 是否能固定反查原 snapshot 证据；
6. 规则适用范围和执行失败语义是否可验证；
7. 所有 verifier、质量门禁和报告导出是否 fail-closed；
8. 人工误报、漏报、争议和审核迁移是否可沉淀；
9. Golden Corpus 是否能自动回放；
10. 模型是否在不增加不可控人工负荷的前提下提高召回。

因此：

> 当前应立即启动身份契约、ExtractionSnapshot、回放工具、文档画像、事实层、证据契约和审核闭环；在六项第 1 周冻结任务完成前，不得启动批量标注，更不应训练模型或采购推理硬件。

模型、RAG、LoRA 和本地节点均是后续能力，不是当前结构化、身份和证据问题的替代方案。

当 Phase 0 至 Phase 4 达到门禁后，项目才具备选择本地模型和硬件的可靠输入；当 shadow 连续达标后，才具备上海正式试点条件。

---

# 24. 采购前外部核验清单

本计划不以硬件或模型名称作为已验证事实。进入采购或模型 benchmark 固化前，责任人必须保存以下当期官方或可审计证据：

- 目标模型的官方模型卡、许可、上下文长度、量化格式和推理框架兼容性；
- 本地推理服务的 API 兼容性、并发和故障恢复实测；
- Mac/RTX 候选设备的官方规格、实际报价、供货周期、保修和电源要求；
- 固定 100 份 benchmark bundle 的 parser、snapshot、prompt、schema 和并发配置；
- P50/P95、峰值内存、总耗时、失败率和人工候选负荷结果；
- 采购审批、回滚设备方案和验收签字。

任何一项无法提供证据时，硬件采购保持 NO-GO。
