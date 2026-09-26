# WP4-F：百分比写法完整性（V33-DISCLOSURE-PERCENT-UNIT）

- 日期：2026-09-26（R1 交付）
- 分支：`fix/obligation-percent-unit`（基线 = main 6538e36，即 PR #54 WP4-E 合并后）
- obligation：`OBL-DISCLOSURE-PERCENT-UNIT`（GROUP_DISCLOSURE，budget + final）
- checker：`V33-DISCLOSURE-PERCENT-UNIT` / `R33DisclosurePercentUnit`（src/engine/common_rules.py）
- 覆盖缺口：3 → **2**（剩余 OBL-SG-COMPLETION / OBL-PERF-PHASE-AMOUNT）
- catalog：obligations-v7 → **obligations-v8**

## 〇、一句话

政府预决算材料中「城乡社区支出(类)15411.43 万元，占 76.23」这类**占比谓词 +
数值但缺百分号**的披露形态，此前系统全量漏报。WP4-F 新增通用 checker：
绑定「占/占比/比重/比例」四类占比谓词与其后紧邻数值，数值后无百分比单位
（%/％/百分之）时产出正式 finding；占地/占用、金额/面积/数量单位、数字列
错位串、中文百分数、个百分点全部 fail-closed 排除。

## 一、Truth Discovery（REAL，非自造）

**TRUTH-F01**：宜川路街道 2025 年度决算（41 页）P28「（二）一般公共预算
财政拨款支出决算结构情况」：

> 一般公共预算财政拨款支出 20218.21 万元，主要用于以下方面：一般公共服务
> 支出（类）207.40 万元，占 1.03%；……节能环保支出(类)125.00 万元，占
> 0.62%；**城乡社区支出(类)15411.43 万元，占 76.23**;住房保障支出(类)
> 1076.78 万元，占 5.33%。

| 项 | 值 | 来源 |
| --- | --- | --- |
| document | 上海市普陀区人民政府宜川路街道办事处 2025 年度决算.pdf | uploads/putuo_final_samples/ |
| sha256 | f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03 | 本地原件核对一致 |
| report_kind / fiscal_year | final / 2025 | 决算公开材料，正文年度 |
| page / section | P28 / （二）一般公共预算财政拨款支出决算结构情况 | pdfplumber 实测 + 人工判定 |
| full_sentence | 见上（软换行合并后整段） | 夹具 P28 逐字冻结 |
| predicate / numeric_value / following_text | 占 / 76.23 / 「;住房保障支出(类)1076.78 万元，占 5.33%。」 | 原文 |
| human_severity | 低 | adjudication.json Y08 |
| historical_system_result | **miss（未报告）** | comparison.md Y08「漏报；未报告」 |

**百分比语义三重证明**（任务 §六/§十四，非"数字后没 % 就报"）：

1. **句法**：同一枚举句 9 项功能分类结构占比中 8 项带 %（1.03%/2.75%/…/5.33%），
   唯独此项缺；谓词「占」处于标准占比枚举结构「XX支出(类) 金额 万元，占 NN.MM%」。
2. **数值复算**：15411.43 / 20218.21 × 100 = 76.2255 → **76.23**（两位小数），
   与文中分母（20218.21 万元，同页披露）精确吻合——排除金额/面积/人数/
   数量/系数/章节编号等一切其它语义。
3. **人工判定**：outputs/sample_validation_20260916/adjudication.json Y08
   「结构占比缺百分号：城乡社区支出"占76.23"缺少百分号」，severity=低，
   system_match=miss。

真实语料反例侦察：对 uploads/putuo_final_samples 全部 7 份决算 + samples/good
+ samples/bad 用草案正则扫描，**仅宜川 1 条真命中、0 误报**；另发现财政局
2024 P21 一处**提取层列错位伪形态**（「占⏎60.99 2.07% 445.69；⏎15.16%」——
数字与文字分离提取，「占」后紧邻的 60.99 是金额而非占比），据此增加了
「数字串延续 → fail-closed」防线（见 §三）。

## 二、架构决策：common registry（不复制两份）

`registered_rule_ids` 的真实架构 = 专项规则（budget→ALL_BUDGET_RULES /
final→rules_v33.ALL_RULES）+ **ALL_COMMON_RULES**（两条管线都拼入
engine_rule_runner）。本义务 report_kinds=budget+final，故 checker 落位
src/engine/common_rules.py（CMM-007 同款路径），一条实现、两个注册表
自动包含，budget/final 管线真实执行由测试锁定（任务 §二十九/§三十六）。

rule id 按任务 §三十 冻结为 `V33-DISCLOSURE-PERCENT-UNIT`（V33 前缀是
checker ID 命名空间，不限定所在文件；common_rules 中 ID 与文件解耦是既有
事实——CMM-007 的 ID 也不以所在文件命名）。

## 三、谓词与判定设计

### 支持的谓词（范围收敛，任务 §十七/§十八）

| 谓词 | 形态 | 依据 |
| --- | --- | --- |
| 占 | 占76.23 / 占 76.23 / 占为76.23 | **真实 truth 谓词**（Y08） |
| 占比 | 占比76.23 / 占比为76.23 / 占比达到76.23 | 任务 §十六 强谓词 |
| 比重 | 比重为76.23 | 任务 §十七 合同覆盖 |
| 比例 | 比例为76.23 | 任务 §十七 合同覆盖 |

**不扩展到「率」**（增长率/完成率/执行率等）：归 CMM-007、V33-234、
V33-TREND-COMPLETION-RATE 领域，避免大面积重叠（任务 §十八）。
「占比重」形态（比重前再带占字）主正则不命中，属既有形态空档，如实记录。

### 合法单位（unit present → 0 finding，任务 §十一/§十二/§十三/§二十五）

- ASCII `%`、全角 `％`、千分号 `‰`（口径完整，千分比不归本规则）
- 中文「百分之」（占百分之七十六点二三——谓词后非数字，主正则天然不命中）
- 「个百分点」（百分点是变动量单位，不得要求补 %，任务 §十三）

### 排除防线（fail-closed，任务 §八/§九/§十/§二十一/§三十二）

| 防线 | 形态 | 结果 |
| --- | --- | --- |
| 占地/占用 | 项目占地76.23平方米 / 占用资金76.23万元 | 0 finding（「地/用」阻隔 + 负前瞻 + 单位排除三层防御） |
| 非百分比单位 | 占76.23万元/元/亿元/平方米/亩/人/人次/个/项/天… | 0 finding（单位首字表，任务 §十 全列表） |
| 数字串延续 | 占⏎60.99 2.07%…（财政局 2024 列错位实测形态） | 0 finding（数值后紧跟另一数字/千分位 → 伪绑定） |
| 跨句读绑定 | 该项目占主要部分。2025年支出76.23万元。 | 0 finding（谓词后非数字即不命中，无跨句拼接） |
| 多数字同句 | 项目支出76.23万元，占一般公共预算财政拨款支出的25.00%。 | 0 finding（金额无谓词、占比带 %） |
| value > 100 | 占比达到120.5 | **finding**（任务 §十九：数值范围不能代替语义） |

### 数值范围纪律（任务 §十九/§二十）

- 不用 `0<=v<=100 => percentage` 判定：76.23万元是金额，120.5 可以是百分比。
- **小数比例 fail-closed**：`占比为0.7623` 无法证明作者意图（漏 % 的百分数
  vs 刻意的小数比例值）→ 照报 finding，但文案只说「披露口径不明确」，**
  禁止断言正确值是 76.23%**（任务 §二十/§二十七）。
- 所有 finding 文案一律「请核实原稿并补充正确的百分比单位」，不写死正确值。

### 软换行与跨页（任务 §二十二/§二十三）

- 主扫描：每页经 `merge_soft_wrapped_lines`（项目现有 soft-wrap 纪律，共享
  实现）合并为逻辑段落，段落即句读单元，**不删全文空白做全局拼接**。
- **跨行补充绑定**：项目 merge 纪律把行首「76.23」（\d+\.\d+ 条目形态）判为
  新段落起点，主扫描在该形态 fail-closed；checker 内增加「行尾谓词 + 次行
  行首数值」补充绑定（不改共享 merge 代码），绑定后走同一后缀分类，与主
  扫描按（page, predicate, numeric）去重。次行为**纯数值行**（表格线性化
  表头/数据行形态）时不绑定，fail-closed。
- **跨页**：逐页独立处理，页与页之间一律不绑定（任务 §二十三 fail-closed）。
- 已知边界：「占\n76.23」次行为纯数值行时（如表格页）不报；纯文本页可报。

### section / fiscal_year / severity

- section：当页命中段及其前序段内最近中文序号章节标题（「（二）…情况」
  形态），定位不到如实为 null，不伪造。
- fiscal_year：表达完整性检查与期间计算无关（任务 §三十五），**不设 WP4-E
  式年度门禁**；`_resolve_fiscal_year` 能确认就记录（宜川 truth 记录 2025），
  确认不了不阻塞。
- severity = **info**：人工判定 Y08 severity「低」→ AGENTS.md「low：格式类
  问题」→ 引擎谱系中表达规范类既有定级 info（V33-232 百分比精度同款）。
  引擎无 low 档，info 为最接近且同类的既有档位；无依据升 medium、无依据
  降 further（任务 §二十八）。

## 四、finding 证据字段（任务 §二十六）

每条 finding 的 location 携带：`obligation_id` / `predicate` / `numeric_text`
/ `normalized_value` / `page` / `pos` / `section`（可 null）/
`fiscal_year`（可 null）/ `report_kind`（管线注入，可 null）/
`unit_present=False` / `expected_unit="percent"` / `line_break`（主扫描
False / 跨行绑定 True）；`evidence_text` 保留原文片段（含原始空格与换行）。

## 五、测试与 Mutation

tests/test_disclosure_percent_unit.py，33 条：

- **真实 truth（REAL）**：宜川全文档恰好 1 条 finding（page=28/section/
  fiscal_year=2025/predicate=占/76.23），真值页逐字冻结文本独立复现；文案
  断言「76.23% 不在 message 中」（不伪造正确值）。
- **管线**：final 完整规则集 fail 结论保留 finding；budget 完整规则集真实
  执行（合同正例占比76.23 → 1 finding）。
- **合同正例**：占比/比重/比例、混合句仅第一处、三项枚举仅裸值一处、
  >100%、小数比例（口径不明确文案）。
- **合同负例 Case A~J**：ASCII %/全角 ％/中文百分数/占地/占用/个百分点/
  数量单位全列表/金额与占比同句/跨句数字。
- **边界**：76.234%（V33-232 领域，本规则 0 finding）、跨页 fail-closed、
  列错位伪绑定、软换行两形态、纯数值行、空文本 RuleDeferred。
- **台账收口**：budget/final 台账不再 not_implemented；fail 回执 →
  completed；insufficient_data 回执 → 不完成（阻塞人工补核）。

Mutation 现场打红还原全绿（守卫真实性验证）：

| Mutation | 变异 | 期望红 |
| --- | --- | --- |
| A | `_classify` 恒 compliant（去掉缺单位判定） | 真值 0 finding → truth 测试红 ✓ |
| B | 谓词放宽「占+数字」（容忍中间隔断 + 去单位排除） | Case D/E/J 全部误报 → 负例红 ✓ |
| C | unit present 只认 ASCII % | 占76.23％ 误报 → Case B 红 ✓ |
| D | unit present 判定失效（单位在场仍报——精度/单位混入） | 占76.234% 误报 → V33-232 边界测试红 ✓ |
| E | 从 ALL_COMMON_RULES 撤下（**自动化测试固化**） | budget/final registry + 台账回 not_implemented ✓ |

## 六、回归与证据

- 新增 33 条全绿；受影响面 CMM-007（test_zero_base_recompute）+
  V33-TREND-COMPLETION-RATE（test_completion_rate_recompute）+
  test_check_obligations = 88 passed。
- 全量 pytest / ruff / mypy / real PostgreSQL / 前端 / E2E / business replay /
  coverage --assert-gaps 2：见 PR CI 与本轮验收记录。
- 覆盖缺口：3 → 2；剩余 OBL-SG-COMPLETION（final）、OBL-PERF-PHASE-AMOUNT
  （budget）——本轮不触碰（任务 §四十七）。

## 七、已知边界与后续

1. 「占比重」形态（如「所占比重76.23」中「比重」分支可命中，「占比重」
   连写不命中）——既有形态空档，如实现测出现再议。
2. 次行为纯数值行的跨行形态 fail-closed（表格线性化防误报优先）。
3. 表格内部占比列（表头带「占比」列名的结构化表）不在本规则范围——表头
   声明单位是表格惯例，误报风险大于漏报价值；结构化表的单位检查如需覆盖
   应走 parsed_tables 通道另立义务。
4. `‰` 千分号视为口径完整（千分比），若业务要求统一百分比口径另议。
