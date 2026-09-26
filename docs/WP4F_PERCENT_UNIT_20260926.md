# WP4-F：百分比写法完整性（V33-DISCLOSURE-PERCENT-UNIT）

- 日期：2026-09-26（R1 交付）/ 2026-09-26（R2 独立评审整改）
- 分支：`fix/obligation-percent-unit`（基线 = main 6538e36，即 PR #54 WP4-E 合并后）
- obligation：`OBL-DISCLOSURE-PERCENT-UNIT`（GROUP_DISCLOSURE，budget + final）
- checker：`V33-DISCLOSURE-PERCENT-UNIT` / `R33DisclosurePercentUnit`（src/engine/common_rules.py）
- 覆盖缺口：3 → **2**（剩余 OBL-SG-COMPLETION / OBL-PERF-PHASE-AMOUNT）
- catalog：obligations-v7 → **obligations-v8**（R2 不递增，见 §〇）

## 〇、R2 独立评审整改（2026-09-26，两个 P1 合并前阻塞项）

独立评审确认核心 Truth / registry / coverage / CI 均成立，但存在两个 P1，
本轮整改（不推翻 R1 已确认的谓词与防线设计）：

### P1-A：粗 dedup 吞掉同页同值的不同 occurrence（正式漏报）

R1 的 `_dedup_key = (page, predicate, numeric_text)` 把「同页、同谓词、同
数值、但不同原文位置」的第二条 finding 静默吞掉（如同一页两处
`货物支出占76.23` / `服务支出占76.23`）。dedup 的唯一合法职责是合并
**同一物理 occurrence 的主扫描与 line-break 双命中**，不能按值合并。

整改：**主扫描与 line-break 双通道合并为单通道 page 级扫描**——

- `_WP4F_PRED_RE` 改为 page 级形态：谓词与数值之间的空隙容忍「行内空白或
  **恰一次**换行」（空行不跨越，与共享 merge 纪律的空行断段语义对齐）；
  verb（为/是/达到/达）自身也可跨一次软换行。该 pattern 在页面原文上的
  匹配集与旧双通道的物理 occurrence 集合一一对应（逐形态推演见测试）。
- 每个 finding 携带 **source identity**：`location.source_start /
  source_end`（page_text 物理偏移，任务 §三/§十八）。同一 occurrence
  只扫描一次，主扫描+跨行双命中合并由匹配唯一性**结构性保证**（旧 dedup
  集合删除；任务 §六 的"占\n76.23"仍恰好 1 finding，测试锁死）。
- 主扫描的 para-local offset 不再存在（任务 §四 消除），line-break 的
  `pos=-1` 不再存在（任务 §五 消除）——dedup / 证据定位 / section 解析
  共用同一物理身份，不维护三套位置推断。

**rest 后缀语义精确复刻旧双通道**（不因重算 source offsets 把列错位/
纯数值行防线重新引入，任务 §二十/§二十一）：

- 数值所在**行内**后缀 → `_classify`（compliant / skip / finding）。
- 数值在行尾结束（行内 rest 为空）→ 看下一个非空行开头：无次行或**条目
  形态**（`_NEW_PARAGRAPH_RE`，即 merge 必断段的段边界）→ finding；行首
  数字 → 数字串延续 skip；其余按次行开头判定。这与旧 merge 段内 rest 的
  段边界语义逐场景等价（"货物支出占76.23⏎5.33%"条目断段 → finding，
  与旧一致；"占76.23⏎25万元"数字延续 → skip，与旧一致）。
- **纯数值行防线收窄到旧 line-break 通道**：仅当「跨行 + 数字行是条目
  形态 + 上一非空行以占比谓词结尾」时应用（表格线性化 fail-closed）；
  同行形态与 verb 独立行形态不受影响（旧主扫描通道语义保真）。

### P1-B：line-break finding 的 section 取整页最后一个标题（provenance 错误）

R1 把遍历完整页后**最后**一个 section 传给整页 line-break 扫描——命中在
（一）内、页尾有（二）时被记录为（二）。整改（任务 §十二/§十三）：

- `_WP4F_SECTION_HEAD_RE` 加**行首锚**（`^` + re.M）在页面原文上预计算
  标题位置列表；每个 candidate 只取 `start < source_start` 的最近标题
  （**只向前**，禁止借用后文/整页最后标题）；找不到为 None 不伪造。
- alternation 长词在前（`情况说明|情况|说明|分析`），修复「…情况说明」
  被非贪婪截断成「…情况」的既有问题（TRUTH-F01 的「…结构情况」不受影响）。
- 同行命中的 section 同样 occurrence-local（同一函数，无第二套逻辑）。

### R2 测试与 Mutation

新增 8 条（41 条全绿）：重复值 A/B/C（任务 §七/§八/§九，2 findings +
source_start 不同 + span 不同 + section 各自归属）、主+跨行同 occurrence
仍 1 finding（§六）、section test A/B/C（§十四/§十五/§十六）、同行
section occurrence-local。真实语料回归：仅宜川 1 真命中、0 误报（不变）。

| Mutation | 变异 | 结果 |
| --- | --- | --- |
| F | 恢复旧粗 dedup `(page, predicate, numeric_text)` | 重复值 A 场景 2→1，测试必红 ✓ 现场打红还原 |
| G | 恢复 page-final section | section A 场景（一）→（二）、section B 双 occurrence 串 section，测试必红 ✓ 现场打红还原 |

### R2 不变量（全部复核通过）

TRUTH-F01 恰好 1 finding（P28 / section=（二）一般公共预算财政拨款支出
决算结构情况 / 占 / 76.23 / info / fiscal_year=2025 / unit_present=False）；
财政局 2024 列错位 0 finding；纯数值行 0 finding；V33-232 边界
（占76.234%）0 finding；全角 %/中文百分数/占地/占用/个百分点/金额单位
全部 0 finding；catalog 仍 obligations-v8（R2 不递增）；coverage 仍
2 gaps。

## 摘要

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
