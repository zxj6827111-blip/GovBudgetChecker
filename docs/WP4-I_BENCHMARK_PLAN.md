# WP4-I 真实材料 Benchmark 验证方案

- 制定日期：2026-09-27
- 阶段定位：WP1–WP4（规则体系第一阶段）已完成，coverage gap = 0、CI 全绿。
  该结论只证明「每条检查义务都有 checker 且没有静默失败」，**不证明面对真实
  政府公开材料时的检出能力**。WP4-I 用第一套真实材料基准集回答四个问题：
  1. 能发现多少问题（检出量与召回）；2. 哪些是真问题（精确率）；
  3. 哪些规则误报（规则级误报归因）；4. 哪些问题遗漏（缺口归因）。
- 本文件是**方案文档**（第一阶段产物，不含任何代码改动）。经确认后进入
  工具开发（第五节所列，全部为新增脚本，不动 checker/finding/DB/前端）。

## 〇、硬约束（本工作包全程有效）

| 禁止项 | 落实方式 |
|---|---|
| 修改 checker 规则 | benchmark 只读引擎：重放走 `src/engine` 现有入口，发现的规则缺陷只进《规则改进清单》，不在 WP4-I 修 |
| 修改已有 finding 语义 | 评测消费现有 `rule_findings` 结构（rule/severity/page/evidence_text/section_id），不新增不改名字段 |
| 修改数据库结构 | 全程离线：零 DB 写入、不依赖 API 服务；产物只落 `corpus/`（标注）与 `outputs/`（评测） |
| 修改前端 | 无前端改动 |

## 一、当前已有 checker 覆盖能力分析

### 1.1 规则注册表实测（2026-09-27，HEAD = a5d52a9）

运行时导入三个注册表（`src/engine/pipeline.py` 按 `report_kind` 装配）：

| 注册表 | 条数 | 规则码 |
|---|---|---|
| 决算 `ALL_RULES` | 57 | V33-001~005、101~115/117/119~122、200~204、210/211/214、220~227、230/232~236、240~246、V33-CROSS-SAN-GONG-ECON、V33-TXT-FUND-DETAIL、V33-NARRATIVE-INDICATOR-REPEAT、V33-SG-COMPLETION、V33-TREND-COMPLETION-RATE |
| 预算 `ALL_BUDGET_RULES` | 17 | BUD-001~003、BUD-101~113、V33-PERF-PHASE-AMOUNT |
| 通用 `ALL_COMMON_RULES`（两种文种都跑） | 8 | CMM-001~007、V33-DISCLOSURE-PERCENT-UNIT |

装配规则：文种解析（`document_profile_resolver`）判 `final` → 决算 57 + 通用 8；
判 `budget` → 预算 17 + 通用 8；判 `unknown` → **只跑通用 8 条**并出
`DOC-TYPE-UNKNOWN`（manual_review）。文种识别因此是能力总闸门。

### 1.2 义务目录（obligations-v9）与覆盖分组

coverage 台账（`docs/baselines/wp4h_coverage_current_20260926.json`，commit 6fa3710）：

- 决算：44 项适用义务，结构性缺口 0，ceiling_rate = 1.0
- 预算：23 项适用义务，结构性缺口 0，ceiling_rate = 1.0

义务分 10 组：DOC_STRUCTURE（文档结构与必备要素）、CORE_TABLES（核心表完整性）、
TABLE_INTERNAL（表内关系）、TABLE_CROSS（表间关系）、TABLE_TEXT（表文关系）、
NARRATIVE（文内关系）、RATIO_TREND（比例与变动）、DISCLOSURE（披露与表达）、
SAN_GONG（三公经费）、PERFORMANCE（绩效披露）。

**重要区分**：结构性缺口 = 0 是「每条义务都注册了 checker」的静态轴。同一快照里
运行时轴（对具体材料逐实例完成度） unresolved = 44/23、auto_completion_rate ≈ 2%~4%
——这是基线生成时按「未跑真实检查」口径记录的。**WP4-I 的核心价值就是补上运行时轴
的真实证据**：真实材料上这些义务实际完成了多少、命中了多少。

### 1.3 能力分层画像（按检查类型 → 代表规则）

| 能力域 | 决算侧 | 预算侧 | 真实材料验证状态 |
|---|---|---|---|
| 文档结构/要素 | V33-001 年份单位、V33-002 九张表定位、V33-003 页数阈值、V33-112/113/114/236 | BUD-001 九张表+必备章节、BUD-002 占位符、BUD-003 年份 | samples 4 份验证过；外地区未验证 |
| 表内勾稽 | V33-004/005/115/117/119~122/210/211/214/233/240~244 | BUD-101~104/110 | 仅宜川样张验证过 T1/T5/T6 三组真值 |
| 表间勾稽 | V33-200~204、V33-CROSS-SAN-GONG-ECON（WP4-A） | BUD-105 | 单样张验证 |
| 表文一致性 | V33-101~110、220~227、V33-TXT-FUND-DETAIL（WP4-B） | BUD-107/109/112 | 单样张验证 |
| 文内叙事 | V33-234/235、V33-NARRATIVE-INDICATOR-REPEAT（WP4-C）、CMM-007 零基数同比（WP4-D） | BUD-111/112 | 单样张验证 |
| 比例与变动 | V33-230/232、V33-TREND-COMPLETION-RATE（WP4-E） | —（BUD-111 承担） | 单样张验证 |
| 披露完整性 | V33-109 空表说明、V33-246、V33-SG-COMPLETION（WP4-G）、V33-DISCLOSURE-PERCENT-UNIT（WP4-F） | BUD-106、V33-PERF-PHASE-AMOUNT（WP4-H） | 单样张验证 |
| 通用文本 | CMM-001~004/006（镜像/标点/连续句号等） | 同左 | 历史任务有命中 |

### 1.4 已知薄弱面（Benchmark 设计的直接输入）

1. **预算侧显著弱于决算侧**（17 vs 57）：预算无专项的表内经济分类勾稽、
   表文逐项比对、文内重复披露、完成率口径复算等对应物。测试集里预算材料
   的 Recall 上限预期低于决算，须分层报告，不能混算。
2. **所有「验证过」的结论都来自 1 份样张 + 4 份 samples**（上海普陀区，
   DOC-20260905-001 即宜川路街道样张）。地区、模板版式、年度泛化均无证据。
3. **文种识别是单点闸门**：2026-09-17 曾有 kind_disagreement 反例（文件名与
   正文不一致时两环节判出互斥文种）。Benchmark 必须逐份记录 resolved kind
   并与真值比对，单列「文种识别准确率」。
4. **AI 语义义务默认未执行**（`ai_not_run`）：为可复现，第一期纯规则模式重放；
   AI 边际价值留作后续可选项，不进首期指标。
5. **零触发规则不可见**：82 个规则码中哪些在真实材料上从未触发（规则过严或
   测试集无该缺陷型），目前没有任何统计——Rule Hit Rate 指标（第四节）专门回答。

### 1.5 可复用的既有资产

| 资产 | 位置 | 复用方式 |
|---|---|---|
| 单样张评测链（9 轮评审语义） | `scripts/replay_golden_corpus.py`、`scripts/evaluate_golden_corpus.py` | 新工具 import 其公共函数（真值组匹配、证据重叠、锚点约束），不改其行为 |
| Golden 标注 schema | `corpus/DOC-20260905-001/golden.json`（doc_id/sha256/annotation_version/labels，label ∈ defect/rounding_hint/manual_review/acceptable，truth_id 组 + allowed_rule_ids + location_key + evidence） | 第一期语料直接沿用该 schema，评测语义零成本迁移 |
| 语料模板与流程 | `docs/GOLDEN_CORPUS_TEMPLATE.md`（2026-08-29） | manifest/labels 字段、盲标纪律、材料配比建议继承 |
| 历史任务池 | `uploads/`（351 个任务目录） | 可选补充来源：PDF 重新登记入语料（旧输出仅作预标注候选，评测一律用当前代码重放） |
| samples | `samples/good|bad/`（4 份） | 直接入集（bad 版已知问题，标注成本最低） |
| 义务账本 | `src/engine/check_obligations.py`（obligations-v9） | 重放时逐材料导出运行时账本，作运行时覆盖指标数据源 |

## 二、PDF 测试集设计

### 2.1 数量建议：第一期 30 份（±5）

依据：`GOLDEN_CORPUS_TEMPLATE` 建议 20–50；30 份可支撑规则级统计下限
（每条高频规则至少有 3–5 份材料可触发），标注工作量可控（约 60–90 人时）。

| 子集 | 数量 | 配比理由 |
|---|---|---|
| 决算主集 | 18 | 系统能力重心；义务 44 项需要足够材料面 |
| 预算主集 | 9 | 预算侧规则少，9 份足以画基线 |
| 扫描件观察集 | ≤3 | 不进主指标（OCR 风险规则要求 manual_review），单列观察 |

**干净对照比例 ≥ 1/3（主集 27 份中至少 9 份「无问题或问题很少」）**——没有足量
干净材料，误报率会被问题富集样本扭曲（模板第五节既定原则）。

### 2.2 类型划分

- **行政层级**：区级委办局（主体）≥ 60%；街道/乡镇 ≥ 20%（样张已验证该版式）；
  其余为区政府本级或事业单位，探层级泛化。
- **条件性表格覆盖**（每类至少 2 份材料覆盖）：有政府性基金 / 无（空表说明路径）；
  有国有资本经营 / 无；三公经费有支出 / 全零（空表+说明路径）；有绩效目标表 /
  绩效并入说明。空表与「无此项」路径是 V33-109/BUD-106 的唯一触发面，必须覆盖。
- **问题密度**：已知问题版（samples-bad 与用户确认含问题的材料）约 1/3；
  疑似干净约 1/3；其余未知（盲标后确定）。
- **版式难度**：至少 5 份带已知难点（表格跨页断裂、续页盲行、合并单元格错位、
  目录页码偏移），验证解析层的鲁棒性边界。

### 2.3 地区覆盖：第一期同分布为主，泛化为探针

- **锚定分布（约 70%，≈21 份）**：上海普陀区及上海其他区。理由：规则锚点词表
  按上海区级材料打磨，第一期目标是测**能力上限**（同分布下召回/精确是多少），
  而不是把「地区模板差异」与「规则缺陷」混在一个指标里无法归因。
- **泛化探针（约 30%，≈9 份）**：外省市 2–3 个（如华东非沪城市、中西部区县
  各若干），**单独分层报告**。若探针指标显著低于锚定分布，结论是「泛化缺口」
  而非直接判规则缺陷。
- 来源合规：仅收录政府网站公开发布材料或用户确认有权使用的内部评审版；
  登记时记录来源 URL/渠道（manifest 字段）。

### 2.4 年份覆盖

| 年度 | 决算（对应公开年） | 预算 |
|---|---|---|
| 2023 年度（2024 公开） | 3–4 份（同比基期引用路径） | — |
| 2024 年度（2025 公开） | **主力 ≈10 份** | — |
| 2025 年度（2026 公开） | 4–5 份（最新模板） | **主力 ≈6 份** |
| 2026 年度预算 | — | 3 份 |

跨年度的意义：同比/较上年类规则（V33-234、CMM-007、V33-TREND-COMPLETION-RATE）
的引用口径随年度变化，需至少两个年度切片。

### 2.5 材料准入与登记

- 准入：文本层 PDF 优先（文本层字符量探测通过）；页数 15–60 页为宜；
  每份登记 sha256（评测绑定的防错源机制，沿用 R8 P1）。
- 登记：`corpus/<DOC-ID>/sample.pdf` + `manifest.csv` 一行（沿用模板字段并扩展
  `report_kind_true`、`来源URL`、`子集标记[final-main|budget-main|scan-watch|
  clean-contrast|probe-region]`）。
- DOC-ID 规则：`DOC-B1-###`（B1 = 第一期 benchmark），扁平目录与现有
  `corpus/DOC-20260905-001/` 同构，保证 `evaluate_golden_corpus.evaluate()`
  的路径约定可直接复用。

## 三、Golden Dataset 设计

### 3.1 目录与格式：沿用单样张 schema，不改评测语义

```
corpus/DOC-B1-001/
├─ sample.pdf            # gitignored
├─ golden.json           # 入库（无原始材料内容，只有元数据+标注）
├─ ANNOTATIONS.md        # 入库：标注修订记录、分歧仲裁
└─ manifest 行           # 入库：材料元数据
```

`golden.json` 沿用既有字段：`doc_id / sha256 / annotation_version /
annotated_at / truth_frozen_at / report_kind / fiscal_year / organization /
labels[]`；每条 label：`annotation_id / truth_id / label / page /
allowed_rule_ids / location_key(sec|toc|tbl|xtbl:锚文本) /
expected_severity / evidence / confidence`。
**新增仅一个标注侧字段** `obligation_group`（10 组之一）：标注时可空，
评测时由规则码反查——这是标注分类维度，不触碰 finding 语义。

### 3.2 人工标注方式

1. **盲标优先**：标注人只看材料本身，按 `AGENTS.md` v3_3_portable 五线检查法
   （结构/完整性/勾稽/口径/证据）逐项排查，**不得先看系统输出再抄**
   （否则永远测不出漏检——模板第七节铁律）。
2. **预标注限历史任务**：来自 `uploads/` 的材料，其**旧版系统输出**可作候选
   清单发给标注人做「确认/否决/补充」，但当前代码输出一律不可见。
3. **两级标注深度**：
   - L1 全量（30 份）：结构完整性、文种/单位/年份、表清单、空表说明、明显文种
     表述问题——约 0.5–1 小时/份；
   - L2 深标（其中 20 份问题富集材料）：全量勾稽复算、表文逐项、文内一致性、
     同比口径——约 2–3 小时/份。
   指标按 L1 集 / L2 集分层报告，分母不混。
4. **复核与冻结**：每 10 份抽 1 份第二人复核；分歧条目进 ANNOTATIONS.md 仲裁记录；
   全部完成后写 `truth_frozen_at` 冻结。冻结后任何修订必须递增
   `annotation_version` 并记录原因（预期修订主因：FP 归因发现「其实是标注遗漏」）。
5. **标注工具**：PDF 阅读器 + 复算表格（xlsx 辅助算勾稽）即可；evidence 字段
   必须摘录原文片段（评测的证据重叠匹配依赖它）。

### 3.3 问题分类（标注侧 taxonomy）

- 按 **义务组** 10 类（DOC_STRUCTURE … PERFORMANCE）主分类；
- 按 **缺陷性质** 辅分类：math（勾稽数学错）/ structure（缺表缺章节）/
  consistency（表文/文文/表表矛盾）/ disclosure（该披露未披露、空表无说明）/
  expression（文字表述、标点、单位口径）。

### 3.4 判定定义（真问题 / 误报 / 漏检 / 无法判断）

**真问题（defect，进 Recall 分母）** —— 材料客观存在、人工可给出独立于系统的
证据（页码 + 原文摘录 + 复算过程），满足以下任一：

- a. **数学性**：勾稽/合计/占比/同比复算不闭合，且超出容差（金额 0.5 万元、
  百分比 0.1pp、四舍五入级差异不算）；
- b. **规范性**：违反公开规范必备要素（核心表缺失、必备说明章节缺失、
  空表无「无此项」等效说明）；
- c. **一致性**：同一材料内两处披露矛盾（表↔文、文↔文、表↔表）。

写入 golden.json 时 `label=defect`；`confidence < 0.7` 的标 `label=manual_review`
（不进分母，单独列）。

**提示类（rounding_hint，进预期输出、不算误报也不算真问题）**：取整/容差内
差异的提示性输出。系统报出 → 消费该标注（非 FP）；未报出 → 计入 hint_missed
单独统计，不进 Recall 分母。

**误报（FP，评测产物）** —— 系统 finding 未被任何 defect/rounding_hint 标注
消费。FP 不是终判，须经人工归因为四类之一（第五节归因工作表）：

- `rule-logic`：规则逻辑缺陷（所指问题不存在，如把科目编码当金额）；
- `parsing`：解析错误（表格错位/跨页拼接/文本层脏字符导致）；
- `exemption-gap`：口径豁免缺失（占位符、空表说明等应豁免未豁免）；
- `annotation-miss`：实为标注遗漏（人工复核确认是真问题）→ 回修标注
  （annotation_version+1），修订后重算指标。

**漏检（FN，评测产物）** —— defect 真值组未被任何 finding 命中。归因三类：

- `no-rule`：无对应规则（能力缺口，如预算侧表文逐项比对）；
- `rule-defect`：有规则未触发（实现缺陷，阈值/锚点/文种装配问题）；
- `parsing`：上游解析失败导致规则拿不到输入（从 rule_outcomes 的
  insufficient_data/parse_error 佐证）。

**无法判断（manual_review，双向出口）**：

- 标注侧：OCR 不清、口径两可、需外部信息（上年数据不可得、政策口径存疑）——
  标 `label=manual_review`，不进任何分母；
- 系统侧 severity 已为 manual_review 的 finding：只统计量，不进 Precision 分子
  分母（沿用现有评测对 hint 的处理思想）。

**干净负例（acceptable）**：明确核过无问题的页/表（如某表整页勾稽全闭合）。
其上出现任何 finding 即记「负例违规」，作为不依赖标注完整性的误报代理指标。

## 四、测试指标设计

### 4.1 命中判定口径（复用既有 evaluator，不重造）

TP 判定 = 规则一致（`allowed_rule_ids`）+ 页码一致 + 证据内容重叠（区分度数字
token 交集或长文本片段）+ 锚点约束（零命中拒配）+ sec 域章节标识校验；
按 `truth_id` 组计（组内任一证据面命中即该真值命中）。与
`evaluate_golden_corpus.py` R4–R9 语义完全一致，通过 import 复用。

### 4.2 主指标

| 指标 | 定义 | 说明 |
|---|---|---|
| **Precision** | TP / (TP + FP) | TP/FP 按 4.1 口径；FP 为未被 defect/hint 消费的 finding；分母剔除系统侧 manual_review 级 |
| **Recall** | TP / (TP + FN) | 按 defect 真值组计；分母剔除 confidence<0.7 与 manual_review 标注 |
| **FPR（操作性定义）** | FP / (TP + FP)，即 1 − Precision | 经典 FPR = FP/(FP+TN) 需要 TN 计数，文档审计场景「没报的没问题点」不可数，故不采用；另报两个可解读代理：**FP 密度** = FP 总数 / 材料数（每份材料误报几条）；**负例违规率** = 出现 finding 的 acceptable 页数 / acceptable 页总数 |
| **Rule Hit Rate** | 触发该规则的材料数 / 适用材料数 | 「适用」按 report_kind 分母（决算规则只对决算材料计）；配套输出**零触发规则清单**与**规则级 precision**（该规则 TP / 该规则 findings） |

### 4.3 辅助指标（全部已有实现或低成本推导）

- severity 准确率、page 准确率（TP 中与 expected_severity/page 一致比例）；
- 证据可定位率（页码 + 非空 evidence_text，沿用现有双证据口径）；
- **文种识别准确率**（resolved report_kind vs `report_kind_true`）——单列，
  错判直接换掉整套专项规则；
- **运行时义务完成率**（重放导出的 obligation ledger：completed / applicable，
  按 10 组分层）——回应 1.2 节运行时轴空缺，是 WP4-I 独有的结构性产出；
- 规则六态分布（pass/fail/not_applicable/insufficient_data/parse_error/
  execution_error）——parse_error/insufficient_data 高的材料标记解析风险。

### 4.4 分层报告维度

report_kind（final/budget）× 子集（clean-contrast/problem-rich）× 地区
（锚定/探针）× 标注深度（L1/L2）× 义务组（10 组）× 规则（82 码）。
任何总体指标必须能下钻到规则级定位责任。

### 4.5 门禁策略：第一期只观测，不拦截

- 第一期产出参考线（**报警锚点，非门禁**）：Precision < 0.60 或 Recall < 0.50
  （L2 集）→ 判「当前规则体系不可直接用于真实场景」；期望区间 P ≥ 0.75、
  R ≥ 0.65。
- 依据 `GOLDEN_CORPUS_TEMPLATE` 第八节：数字稳定 ≥3 批后脚本化进 CI 观测位
  （只报告不拦人），再议正式阈值。WP4-I 不设 CI 门禁。

## 五、需要新增的测试工具（第二阶段开发，待本方案确认）

均为**新增脚本**，不改 `evaluate_golden_corpus.py` /
`replay_golden_corpus.py` / `src/**` 的任何现有行为；只写 `corpus/`（登记与
标注）与 `outputs/`（评测产物）。

| # | 工具 | 职责 | 关键设计 |
|---|---|---|---|
| 1 | `scripts/bench_register.py` | 材料登记：生成 DOC-ID 目录、sha256、manifest.csv 追加行、文本层/页数探测、子集标记 | 重复 sha256 拒绝登记；PDF 永不入 git |
| 2 | `scripts/run_benchmark.py` | 批量重放：遍历语料，逐份以当前代码纯规则模式执行（与 replay_golden_corpus 同一引擎入口 `run_rules_with_outcomes`），产出 `outputs/benchmark/<ts>/DOC-ID.json`：findings + rule_outcomes + obligation ledger + resolved kind + sha256 | 确定性（禁 AI）；产物 schema 与现有 evaluator 兼容（doc_id/sha256/findings 顶层键），保证工具 3 可直接 import `evaluate()` |
| 3 | `scripts/eval_benchmark.py` | 多文档聚合评测：逐份调 `evaluate_golden_corpus.evaluate(doc_id, replay_path)`（自动跳过无 golden.json 的登记未标材料），聚合 4.2/4.3 全部指标与分层报告，输出 JSON + Markdown 汇总 | 指标快照落 `docs/baselines/bench1_metrics_<date>.json`（新增文件，不改旧 baseline） |
| 4 | `scripts/bench_fpfn_sheet.py` | FP/FN 人工归因工作表：导出 CSV（每条含 doc/rule/page/evidence/摘要 + `归因[rule-logic|parsing|exemption-gap|annotation-miss|no-rule|rule-defect]` 空列） | 归因回填后由工具 3 重算修订指标（消费 annotation_version） |
| 5 | 单测（`tests/`） | 工具 1–4 的单元测试：指标计算反例（TP 判定、FPR 口径、truth 组去重）、登记幂等、产物 schema | 沿用仓库 ruff + pytest 门禁 |

配套微改（不属于禁改四项，但一并列出待确认）：`.gitignore` 为每个新 DOC 目录
增加 golden.json/ANNOTATIONS.md/manifest 白名单行（沿用 DOC-20260905-001 的
既有模式）。

## 六、与现有 WP1–WP4 的兼容方案

1. **对 checker/finding 的零接触**（禁改项 1/2）：所有重放走只读引擎调用；
   规则缺陷的处置路径是《规则改进清单》（WP4-I 交付物之一），修改变更留待
   WP5 或专项工作包——与「暂停 WP5 开发」不冲突（改清单不是改代码）。
2. **对 DB/前端的零接触**（禁改项 3/4）：不启动 API、不连库。与 WP3 复核
   生命周期（review_obligations）的关系：FP/FN 人工归因是**评测侧**活动，
   不进生产复核流程、不写 `review_obligation_decisions` 表；产物只是
   outputs/ 下 CSV/JSON。
3. **对材料台账（WP0–WP2 Material Slot）的关系**：benchmark 材料走
   corpus/ 独立通道，不占 material_slot、不触发回填；两者数据不通，
   避免测试数据污染生产台账。
4. **对 coverage 台账（WP4-A~H）的关系**：`docs/baselines/wp4*_coverage_*.json`
   全部不动；WP4-I 新增的是另一类快照（bench1_metrics_*），标注引擎版本与
   规则指纹，与结构轴快照并行不悖。coverage 门禁脚本无感知。
5. **对既有评测语义的保护**：`DOC-20260905-001` 服务于 9/5 整改验收门禁，
   其 golden.json 与评测语义冻结不动；新语料 import 复用而非修改。若发现
   必须改公共函数才能支持多文档，单独微 PR 论证（预期不需要——扁平目录 +
   兼容 schema 已覆盖）。
6. **分支与基线**：建议待 PR #57 合并后自 main 拉 `feat/bench-wp4i`
   （benchmark 不依赖 WP4-H 代码，但统一基线便于归因）；若 #57 短期不合并，
   也可直接自 main 开分支，记录实际引擎指纹即可。工具开发 + 标注 + 评测
   产物分批提交，PDF 永不入库。
7. **可复现性**：每份评测报告绑定 doc_id + sha256 + 引擎指纹（沿用 R8 P1
   防错源机制）；纯规则模式 + 固定代码版本 ⇒ 同输入同输出，支持后续回归对比。

## 七、执行阶段（确认后推进）

| 阶段 | 内容 | 出口判据 |
|---|---|---|
| S0 | 本方案确认 | 用户批准 |
| S1 | 工具 1–5 开发 + 单测 + CI 绿 | ruff/pytest 全绿；单文档干跑（用 DOC-20260905-001 验证链路） |
| S2 | 材料收集登记（用户提供 PDF；uploads/samples 可选补充） | 30 份登记齐，子集配比达标 |
| S3 | 盲标（L1 全量 + L2 深标 20 份）→ 抽检 → 冻结 | 全部 golden.json 带 truth_frozen_at |
| S4 | 首跑评测 + FP/FN 人工归因（回修标注后终算） | bench1_metrics 快照 + 归因工作表全回填 |
| S5 | 《WP4-I_BENCHMARK_REPORT.md》：四问回答 + 规则改进清单（移交 WP5 重启决策） | 用户验收 |

## 八、风险与开放问题

- **标注完整性风险**（最大）：Recall 的可信度取决于人工找出多少真问题；
  缓解：FP 归因回修（annotation-miss 通道）+ 负例违规率作独立信号。
- **「干净材料」判定难**：只能证明「按 v3_3 口径未发现问题」，不等于绝对
  无错；报告须声明口径限定。
- **扫描件**：OCR 误报/漏检会污染指标，故隔离为观察集，只出问题清单不进
  主指标。
- **泛化与缺陷混淆**：外省材料指标差可能来自模板差异而非规则缺陷——分层
  报告 + 人工抽检归因缓解。
- **历史任务池代表性**：uploads 材料曾被旧版规则跑过，但评测全部用当前代码
  重放，旧输出仅作预标注候选，不存在旧结论污染。
- **开放问题（请确认）**：
  1. 30 份规模与子集配比（2.1/2.2）是否认可；
  2. 地区策略（第一期上海同分布为主、外省为探针）是否认可；
  3. 标注人力与两级深度（L1/L2）安排在谁——用户自标 / 我辅助产出标注
     草稿由用户复核（若由我出草稿，须在冻结前完成且标注过程不可见系统
     当前输出，流程上需要用户把关盲标纪律）；
  4. `obligation_group` 标注字段与 `.gitignore` 白名单两条微改是否随 S1
     一并批准。
