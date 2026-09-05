# GovBudgetChecker 专业模型辅助审查实施计划 V2

> 版本：V2.0  
> 编制日期：2026-08-26  
> 适用范围：上海市部门预算、部门决算、单位预算、单位决算  
> 当前状态：方案修订完成，尚未进入本计划对应的代码实施、模型训练与硬件采购  
> 基线文档：`docs/GovBudgetChecker 专业模型辅助审查实施计划.md`

---

# 1. 执行摘要

## 1.1 最终判断

GovBudgetChecker 具备继续建设专业模型辅助审查能力的基础，但当前不应直接开始模型微调、采购本地推理节点或把更多文档直接送入大模型。

当前最先需要解决的不是模型参数规模，而是以下四个阻塞项：

1. 文档类型、主体层级和模板版本没有形成统一、可追溯的文档画像契约。
2. 规则引擎与结构化入库使用不同的数据通路，表格、单元格和证据坐标没有统一。
3. 缺少 Golden Corpus 回放、finding 匹配和准确率自动评测工具。
4. 人工审核状态不足以支持误报、漏报、争议和训练准入闭环。

因此，本计划采用以下路线：

1. 先建立最小回放工具和种子 Golden Corpus，冻结现状基线。
2. 统一文档画像、结构化事实层和证据契约。
3. 通过兼容适配器逐步迁移现有规则，不进行一次性大重写。
4. 删除生产服务中的模拟 AI finding 路径。
5. 建立人工确认、误报、漏报、争议和训练准入闭环。
6. 在结构化输入和证据校验基础上接入两阶段模型。
7. RAG 先做结构化精确过滤，再在小候选集上进行语义匹配。
8. 模型 POC 达到质量和吞吐门禁后，再决定本地硬件和 LoRA。
9. 所有正式问题必须经过确定性复算、证据校验和人工确认。

## 1.2 V2 相对 V1 的主要调整

V2 不推翻 V1 的总体方向，重点补充和调整以下内容：

- 将“统一文档画像”列为独立 P0，而不是只在模板或规则元数据中隐含处理。
- 将结构化事实层收敛和证据契约合并为同一 P0，不再分散到不同阶段。
- 明确现有裸 `page_tables` 与 `ParsedTable/ParsedCell` 双通路必须收敛。
- 将 AI mock 伪造路径清理纳入第一批 P0。
- Phase 0 明确包含回放评测工具开发，不再把“一键回放”只写成验收结果。
- Golden Corpus 改为先建立 50 至 100 份种子集，再扩展到 300 份。
- 明确准确率分母、finding 匹配、分类别召回率和隐藏漏报估计方式。
- 将生产吞吐与标注期吞吐分开。
- 规则元数据采用外部注册表加兼容适配器的渐进迁移方式。
- 将模型 raw output 放到调用级审计记录，不直接塞入每个 IssueItem。
- 本地硬件采购增加事实层、证据层、模型 POC 和固定基准包四项前置门禁。
- 将首个可信内部版本和稳定试点工期调整到更现实的范围。

## 1.3 GO / NO-GO 总结

| 项目 | 当前判断 | 说明 |
|---|---|---|
| 基线冻结与回放工具 | GO | 可以立即实施 |
| 文档画像统一 | GO | 第一批 P0 |
| 结构化事实层收敛 | GO | 第一批 P0，阻塞规则和模型 |
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

V2 不要求第一天把所有规则重写成 DSL，而是通过外部注册表和完整性校验逐步迁移。

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
    +-- stable evidence ids / bbox / confidence / semantic types
    |
    v
Structured Facts
    |
    +-- amounts / percentages / codes / names / funding scope
    +-- source cell ids / extraction version
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
extraction_hash
```

规则与模型必须从同一 `StructuredDocument` 或其只读视图获取输入。

## 4.4 稳定证据契约 `EvidenceRef`

表格证据：

```text
document_version_id
extraction_hash
cell_id
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
document_version_id
extraction_hash
page_number
section_id
local_char_start
local_char_end
text_quote
bbox
```

要求：

- `cell_id` 或 `section_id` 是主要引用；
- `row_idx/col_idx/char_span` 作为辅助定位；
- 全文拼接后的全局 span 不得作为唯一证据；
- 重新解析后 extraction hash 变化，旧证据必须重新校验；
- 查不到证据的模型候选不得进入正式 finding。

## 4.5 规则元数据 `RuleSpec`

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

## 4.6 模型调用与 finding 溯源

调用级记录 `ModelInvocation`：

```text
invocation_id
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
candidate_id
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

1. 让 `PDFParser` 返回完整 ParsedDocument，而不只是统计计数。
2. 为每个 table/cell 分配稳定 ID。
3. 主分析路径首先调用 canonical parser。
4. structured ingest 直接持久化同一解析结果，不再第二次独立解析。
5. 提供 `LegacyPageTablesAdapter`，把 ParsedTable.rows 投影给旧规则。
6. 新规则优先使用 typed facts 和 evidence refs。
7. 逐批迁移高风险规则，验证后再移除对应裸表格依赖。

## 6.3 单元格语义类型

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

## 6.4 解析质量门禁

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

## 6.5 事实层验收指标

- 同一文档主链路只执行一次权威解析；
- 规则、模型和 structured ingest 使用相同 extraction hash；
- Golden Corpus 关键表识别率不低于 98%；
- 编码被当作金额的已知回归样本为 0；
- 空值与零值已知回归样本全部通过；
- 已标注跨页表格合并准确率不低于 98%；
- deterministic finding 的证据可反查率为 100%；
- accepted AI finding 的证据可反查率为 100%。

---

# 7. 规则体系改造

## 7.1 不做一次性大重写

V2 推荐三步迁移：

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

## 7.2 解析失败不得伪装成规则命中

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
source_cell_ids
calculator_version
```

模型输出的数值只能放入 `claimed_metrics`。

正式 finding 使用系统复算生成的 `verified_metrics`，不得静默覆盖后丢失模型原始主张。

## 7.4 规则验收指标

- 100% 启用规则存在 RuleSpec；
- 100% RuleSpec 通过完整性校验；
- 不适用规则生成正式 finding 的数量为 0；
- 解析失败被当作问题命中的数量为 0；
- 数值 finding 的 verified metrics 覆盖率为 100%；
- 每条 P0/P1 规则至少有正例、反例和边界样本；
- 所有规则 finding 记录 rule version 和 extraction hash。

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
- 没有证据返回空；
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
```

只有 `ready_for_human_review` 可以进入审核工作台的正式候选区域。

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

## 8.6 模型故障策略

- AI 不可用时规则流程继续；
- AI 故障必须明确标记 degraded 或 failed；
- 不允许用空数组伪装成功；
- 不允许自动切换到 mock response；
- 超时、重试、熔断和降级均进入 ModelInvocation；
- 模型版本升级必须支持按 job 回放和回滚。

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
finding_id
job_id
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

## 10.3 漏报补录

人工补录必须支持：

- 问题类别；
- 严重程度；
- 页码；
- 表格/章节；
- cell ids 或 section spans；
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
- extraction hash 固定；
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
4. 表格问题引用相同核心 cell ids，或证据位置达到约定重合标准；
5. 文本问题引用相同 section，且 local span 或 quote 达到相似度标准；
6. 一个预测 finding 最多匹配一个标注 finding；
7. 多个重复预测只计一个 TP，其余计重复或 FP。

匹配算法、版本和阈值必须固定并进入评测报告。

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
- evidence-verified 候选中位数不超过 5 条；
- P95 不超过 12 条；
- 争议问题单独进入专家或延迟队列；
- overflow 有完整统计。

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

- 每次回放有唯一 run id；
- 输入 manifest 计算 hash；
- 规则、模型、提示词、政策和解析器版本固定；
- 允许对 AI 非确定性结果运行多次稳定性评估；
- 输出文件不可静默覆盖；
- 评测工具本身有单元测试和小型 fixture。

---

# 13. 分阶段实施计划

## Phase 0：评测基础和种子基线，2 至 3 周

### 目标

先让系统的当前表现可以被重复、自动、客观地测量。

### 工作项

- 冻结当前代码、规则、解析器、模型和提示词版本；
- 建立 corpus manifest；
- 建立 50 至 100 份种子集；
- 定义 finding 匹配协议；
- 开发批量回放工具；
- 开发 precision/recall 和候选负荷计算；
- 建立当前误报、漏报和证据可定位率基线；
- 增加混合标题、subject level 和 unknown 路由测试；
- 设计 DocumentProfile、EvidenceRef 和 ReviewRecord schema。

### 验收

- 种子文档有唯一 document/version ID；
- 可一键完成规则和模型回放；
- 指标自动生成；
- 相同确定性版本重复运行结果一致；
- 当前已知误报和混合标题问题可稳定复现；
- schema 评审通过；
- 不要求此阶段完成 300 份全量标注。

### 交付物

- Golden Corpus seed manifest；
- annotation schema；
- replay CLI；
- evaluation report V0；
- 当前版本基线报告；
- P0 回归测试清单。

## Phase 1：统一文档画像、事实层和证据契约，4 至 6 周

### 目标

建立后续规则、模型和训练都能依赖的单一事实基础。

### 工作项

- 实现 `DocumentProfileResolver`；
- 统一 report kind、subject level、report type；
- 消除 `common_rules` 默认 final；
- 消除各入口重复推断；
- 修复混合标题判定；
- 实现 canonical ParsedDocument 返回值；
- 主分析路径改为使用 canonical parser；
- structured ingest 持久化同一解析结果；
- 建立 stable cell/section evidence IDs；
- 建立 legacy page tables adapter；
- 增加单元格语义类型；
- 建立解析质量门禁；
- 删除 AI mock 旧通路；
- 增加 profile、evidence 和 parser regression tests。

### 验收

- 静默错路由为 0；
- 混合标题测试全部通过；
- 主链路只产生一份权威解析；
- 规则和 structured ingest extraction hash 一致；
- 关键表识别率达到阶段门槛；
- 代码不再把已知编码列当金额；
- accepted finding 可以反查到稳定证据；
- 生产 service 中不存在 mock finding 返回路径。

### 回滚

- 保留 legacy parser feature flag；
- 保留 legacy adapter；
- 新旧解析结果可在 seed corpus 上双跑比较；
- 新解析不达标时不得移除旧入口。

## Phase 2：审核闭环与正式 Golden Corpus，3 至 4 周

### 目标

让人工审核结果成为可追溯的业务数据和训练数据来源。

### 依赖

DocumentProfile、EvidenceRef 和 ReviewRecord schema 已冻结。

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

Phase 2 可在 Phase 1 schema 冻结后与 Phase 1 后半段并行，但不得在 evidence ID 未稳定前大量标注。

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
- 使用云模型、Qwen3.5-9B 和 27B 运行统一基准；
- 输出质量、吞吐、token 和人工负荷报告。

### 验收

- 无证据候选不会进入 ready for human review；
- 数值候选全部经过系统复算；
- accepted finding 的证据可反查率为 100%；
- 候选和复核指标达到阶段门槛；
- 人工候选负荷不超过约定阈值；
- 9B、27B 和云模型使用同一输入、schema 和 Golden Corpus 比较；
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
| 可重复基线和种子回放 | 2 至 3 周 |
| 模型无关的可信事实层和高风险规则版本 | 9 至 14 周 |
| 模型辅助内部测试版 | 13 至 19 周 |
| 上海稳定 shadow | 20 至 28 周 |
| LoRA | 额外 4 至 6 周，按门禁决定 |

并行原则：

- Phase 2 可在 Phase 1 schema 冻结后开始；
- 规则元数据整理可与事实层开发并行，但规则实现迁移依赖 canonical adapter；
- 政策资料整理可以提前进行，但 RAG 接入依赖 DocumentProfile；
- 硬件调研可以进行，但不得在 Phase 4 结论前锁定采购。

导致工期延长的主要因素：

- 只有一名审核人员；
- 历史 Excel 无法回到原 PDF；
- 300 份材料需要重新补漏报；
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

截至 2026-08-26：

- 暂不下单；
- 等待 2026-09-22 后的实机或可靠独立实测；
- 在模型 POC 前不锁定平台；
- 保留 RTX 4090 工作站回退方案；
- 不采购来源不明显卡或降低电源、质保和内存规格。

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

## 17.1 现有文件

- `api/runtime.py`
- `api/routes/upload.py`
- `api/main.py`
- `src/engine/pipeline.py`
- `src/engine/common_rules.py`
- `src/engine/rules_v33.py`
- `src/engine/budget_rules.py`
- `src/services/engine_rule_runner.py`
- `src/services/pdf_parser.py`
- `src/services/structured_ingest_runner.py`
- `src/services/fiscal_fact_materializer.py`
- `src/services/ai_findings.py`
- `src/services/issue_workflow_store.py`
- `src/schemas/issues.py`
- `src/db/migrations.py`
- `app/app/components/BatchUploadModal.tsx`
- 审核工作台相关组件

## 17.2 建议新增模块

```text
src/schemas/document_profile.py
src/schemas/evidence.py
src/schemas/review_record.py
src/services/document_profile_resolver.py
src/services/canonical_document_parser.py
src/services/legacy_page_tables_adapter.py
src/services/structured_context_builder.py
src/services/evidence_verifier.py
src/services/numeric_verifier.py
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
```

是否使用以上精确文件名可在实施时结合现有模块边界调整，但职责不可缺失。

---

# 18. 发布、回滚与质量门禁

## 18.1 Feature Flags

建议：

```text
GBC_DOCUMENT_PROFILE_V2
GBC_CANONICAL_PARSER
GBC_LEGACY_TABLE_ADAPTER
GBC_RULE_REGISTRY
GBC_MODEL_CANDIDATE_V2
GBC_EVIDENCE_VERIFIER
GBC_POLICY_RAG
```

## 18.2 双跑策略

迁移期间：

- 新旧解析双跑；
- 新旧规则结果对比；
- 新模型 shadow；
- 新结果不自动替换正式结果；
- 差异报告按 document/rule/category 输出；
- 只有达到门禁才提高新链路流量。

## 18.3 回滚

- 文档画像 resolver 可回滚到上一版本；
- parser 可通过 flag 回退；
- rule registry 可回滚；
- 模型和 prompt 可按版本切换；
- 政策 snapshot 可回滚；
- 数据库 migration 必须向后兼容；
- 新字段不得破坏历史报告读取；
- 不删除旧证据，直到迁移验收完成。

## 18.4 最终质量门禁

任何版本存在以下情况之一即 NO-GO：

- 静默错路由；
- accepted finding 无证据；
- 数值 finding 未复算；
- P0 系统性漏报；
- 解析失败伪装成规则命中；
- 模型失败伪装成成功空结果；
- Golden Corpus 泄漏到训练集；
- 无法回滚；
- 人工负荷超过门槛；
- 正式结果无法追溯版本。

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

---

# 20. 近期四周最小行动清单

## 第 1 周

- 确认 V2 计划；
- 冻结当前代码、规则、模型和 parser 版本；
- 定义 DocumentProfile、EvidenceRef 和 ReviewRecord；
- 选择 50 份种子材料；
- 收集混合标题和 unknown 样本；
- 定义 finding 匹配协议；
- 删除 AI mock 路径的实施任务进入 P0 队列。

## 第 2 周

- 建立 corpus manifest；
- 开发 replay CLI 最小版本；
- 输出当前规则和 AI finding 基线；
- 增加混合标题回归测试；
- 建立 rule registry schema；
- 设计 canonical parser 返回结构；
- 确认生产期和标注期人员容量。

## 第 3 周

- 完成 precision/recall 和候选负荷计算；
- 实现 DocumentProfileResolver 第一版；
- 消除 unknown 默认 final；
- 接通 subject level 后端字段；
- 开始 canonical parser 与 legacy adapter；
- 建立第一版解析质量门禁。

## 第 4 周

- 运行种子集第一次完整回放；
- 输出 V0 基线报告；
- 验证混合标题和主体层级；
- 验证 stable evidence ID；
- 确认 Phase 1 剩余迁移范围；
- 决定是否允许进入正式 Golden Corpus 扩展。

---

# 21. Phase 门禁清单

## 进入模型 POC 前

- [ ] 文档画像单一入口
- [ ] 静默错路由为 0
- [ ] subject level 端到端持久化
- [ ] canonical parser 已成为主入口
- [ ] 规则和结构化入库共享 extraction hash
- [ ] EvidenceRef 可反查
- [ ] AI mock 路径已删除
- [ ] 种子集可一键回放
- [ ] 指标可自动计算
- [ ] 解析 blocked 状态生效

## 进入本地硬件采购前

- [ ] 模型 POC 达标
- [ ] 9B/27B/云模型对比完成
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
- [ ] 100% 正式问题可追溯
- [ ] 备份、恢复和回滚演练通过
- [ ] 业务负责人签署

---

# 22. 启动前待确认事项

1. 谁负责批准 P0/P1/P2 定义和问题分类目录？
2. 唯一审核人员是否有权确认一般问题的最终标签？
3. 专业争议是否能定期邀请财政或会计专家裁决？
4. 50 至 100 份种子集是否可以关联原 PDF、页码和历史问题？
5. 300 份 Golden Corpus 的标注工期是否允许超过两周？
6. 标注建设期是否接受 30 至 40 份/天，而不是 100 份/天？
7. 哪些问题类别属于不得被 top-K 丢弃的 P0？
8. 无问题文档的二次抽检比例是否确认至少 10%？
9. structured ingest 数据库是否继续作为 canonical evidence 的权威持久化层？
10. 现有 `dept_budget/dept_final` 外部接口需要兼容多久？
11. raw model output 的保留周期和访问权限如何规定？
12. 2 万元是否只包含主机，还是包含 UPS、网络和备份？
13. 本地节点是否允许白天补跑失败批次？
14. 上海正式试点的业务签署人是谁？

未回答时默认：

- 一般问题由现有审核人员确认；
- 专业争议标记 uncertain；
- 种子集先做 50 份；
- 标注期按 30 份/天规划；
- 二次抽检 10%；
- 2 万元只用于主机；
- 旧接口至少兼容一个正式发布周期。

---

# 23. 最终结论

GovBudgetChecker 建设专业模型辅助审查能力的方向成立，但项目成败首先取决于：

1. 文档画像是否统一；
2. 结构化事实层是否成为唯一事实来源；
3. finding 是否具有稳定证据；
4. 规则适用范围是否可验证；
5. 人工误报、漏报和争议是否可沉淀；
6. Golden Corpus 是否能自动回放；
7. 模型是否在不增加不可控人工负荷的前提下提高召回。

因此：

> 当前应立即启动回放工具、文档画像、事实层、证据契约和审核闭环，不应立即训练模型或采购推理硬件。

模型、RAG、LoRA 和本地节点均是后续能力，不是当前结构化问题的替代方案。

当 Phase 0 至 Phase 4 达到门禁后，项目才具备选择本地模型和硬件的可靠输入；当 shadow 连续达标后，才具备上海正式试点条件。

---

# 24. 外部资料

- Apple Mac mini M5 Pro 发布信息：  
  `https://www.apple.com.cn/newsroom/2026/08/apple-unveils-powerful-mac-mini-with-m6-and-m5-pro/`
- Apple Mac mini 技术规格：  
  `https://www.apple.com.cn/mac-mini/specs/`
- NVIDIA RTX 4090 官方规格：  
  `https://www.nvidia.com/en-us/geforce/graphics-cards/40-series/rtx-4090/`
- Qwen3.5 官方发布说明：  
  `https://qwen.ai/blog?id=qwen3.5`
- Qwen3.5-9B 官方模型页：  
  `https://huggingface.co/Qwen/Qwen3.5-9B`
- Qwen3.5-27B 官方模型页：  
  `https://huggingface.co/Qwen/Qwen3.5-27B`
- MLX-LM：  
  `https://github.com/ml-explore/mlx-lm`
