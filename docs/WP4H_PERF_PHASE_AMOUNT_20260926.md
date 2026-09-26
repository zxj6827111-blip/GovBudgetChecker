# WP4-H：项目绩效阶段金额披露一致性（V33-PERF-PHASE-AMOUNT）

- 日期：2026-09-26
- 分支：`fix/obligation-performance-phase-amount`（基线 = main 28d7ebb，即 PR #56 WP4-G 合并后）
- obligation：`OBL-PERF-PHASE-AMOUNT`（GROUP_PERFORMANCE，**budget only**）
- checker：`V33-PERF-PHASE-AMOUNT` / `R33PerfPhaseAmount`（src/engine/budget_rules.py，注册进 `ALL_BUDGET_RULES`）
- 覆盖缺口：1 → **0**（全部确定性义务收口；obligations-v8 → obligations-v9）
- 台账：pending_checkers `BUD-PERF-PHASE-AMOUNT` → `checkers_by_kind=_budget("V33-PERF-PHASE-AMOUNT")`

## 一、Truth Discovery（REAL，非自造）

官方预算检查表通道（司法局部门/单位预算 2026 两份 xlsx）没有绩效金额行——
本义务真值不在检查表里，来自**真实预算样张逐字核验 + 原页渲染人工判定**
（与石泉/长风决算对照 md 同一取证方式；tmp/wp4h_evidence 渲染件存证）。

**Truth PA-1（REAL 正例，漏报）**

- 文档：`上海市普陀区文化和旅游局26部门预算.pdf`（27 页）
- SHA：`19447c3cec309322ad786b14f5cb751424a1bfe2a1f3c4c3a96d035440665cf9`
- 主体：上海市普陀区文化和旅游局；年度：2026；文种：budget（部门预算）
- 页码：P27「真如海心剧院精装修工程项目经费情况说明」

> 六、年度预算安排
> 本项目可研批复总投资为13526.06万元，其中建筑安装工程费为11668.67万元，
> 工程建设其他费用1213.29万元，预备费644.10万元。2025年安排建设资金5000万元。

章节标题承诺**年度**口径，内容只有**累计**（可研批复总投资 13526.06 万元，
11668.67+1213.29+644.10=13526.06 内部自洽）与**往年**（2025 年 5000 万元）
口径金额——2026 年度金额整体缺失、无口径说明。人工判定（原页渲染逐字核
验）：阶段/累计口径与年度口径混写且本年度金额缺失，违反义务 basis
"不一致时须给出解释"。historical_system_result：**miss**（义务自 v6 台账
建立以来一直是 pending，无任何 checker 执行过此项检查）。

**Truth 锚 2（REAL 负例，同项目年度一致）**

- 建管委 2026 部门预算（SHA `73528f0b78d178bd…`）P23-24：兰溪路-真南路
  下立交工程，项目性质=**阶段性项目**（2026-01-01 至 2027-12-31）。说明
  「2026年度预算安排22,614.51万元」↔ 申报表年度资金申请总额/当年财政拨款
  **226,145,100.00 元**（归一后一致）；申报表另载项目资金总额
  **771,325,800.00 元**（=77,132.58 万元，跨年累计口径）——禁止比较项。

**Truth 锚 3（REAL 负例，单位归一）**

- 城管执法局 2026 部门预算（SHA `c56f4368be5dbf51…`）P23-24：拆违经费，
  项目性质=**阶段性项目**（2024-01-01 至 2026-12-31）。说明「本年度拆违
  经费预算金额为349.80万元」↔ 申报表年度资金申请总额 **3,498,000.00 元**
  （归一后一致）；项目资金总额 10,308,000.00 元（三年累计，禁止比较项）。

全语料扫描：uploads 全部唯一预算文档 46 份，新规则恰好 1 条命中（文旅局
真值）、0 误报（2026-09-26 现场复核，tmp/wp4h_corpus_sweep.py）。

## 二、Truth 规则冻结（检查对象不混用）

聚合面与项目面的既有分工：

| 既有义务 | 覆盖面 | 本轮不重复 |
|---|---|---|
| OBL-PERF-TARGET（BUD-108） | 绩效涉及资金总额 vs T3 项目支出合计（聚合面，warn+建议补说明） | 聚合软比较 |
| OBL-SG-* / CMM-007 / V33-245 | 三公、同比、占比等其他文面 | 非绩效阶段金额 |
| golden A-007/T7 | 绩效口径金额 vs 项目支出决算**允许不等** | 反过度泛化护栏 |

本规则两条确定性检查面：

- **PA-1 阶段口径错配**（medium）：项目经费情况说明「年度预算安排」小节
  存在金额，但全部为累计/往年口径（总投资/批复/概算/估算/资金总额/累计/
  分期 + 显式往年年份），本年度金额缺失且无等效说明（"不再安排/已在
  上年安排"豁免）。小节无金额（民政局"按季度拨付"形态）不判——金额一致
  性无检查面。
- **PA-2 同项目年度金额不一致**（high）：说明「年度预算安排」金额 ↔ 同项
  目申报表年度资金申请总额/当年财政拨款，Decimal 归一（元→万元）后超
  `compute_dynamic_envelope` 显示舍入包络且说明侧无解释 → finding。

阶段身份红线（义务 basis"不得直接判等"的具体化，全部 fail-closed 记
insufficient_data，不产出确定性结论）：

- 申报表「项目资金总额」在阶段性项目上是跨年累计口径，禁止与年度金额比较；
- 同项目多张申报表（一期/二期等）阶段归属不可确认时不比较；
- 项目身份（名称归一相等/互相包含；单说明×单申报表允许无名称兜底）无法
  确认时不比较；**禁止按位置/顺序配对**；
- 申报表金额单位未声明（缺「项目资金（元）」标注）时归一比较不可完成。

## 三、夹具

`tests/fixtures/perf_phase_amount_truth_page_data.json`（全页冻结，SHA 双锁
`feaa4bf7d3bcc832bf4184db543d800748cb1e9355d48608fdd1418bc77f1c02`；三份源
PDF SHA 逐字锁定）。**教训**：稀疏页冻结会把财政年度投票扭曲（真值页
2025 年份密度高于封面 2026，`_resolve_fiscal_year` 返回 2025 导致真值不
命中）——必须全页冻结保真。

## 四、测试（tests/test_perf_phase_amount.py，22 条）

- REAL 真值：文旅局 P27 恰好 1 条 medium finding（page/project/section/
  predicate/fiscal_year 全字段断言）；真值小节逐字冻结双保险；
- REAL 负例锚：建管委/城管执法局全页跑 0 finding、pipeline pass；
- Case A-F：同项目同阶段一致 0 finding / 100 vs 120 → 1 条 high（带
  project/phase/amount_A/amount_B/unit/diff 可复核字段）/ 一期二期不比较 /
  累计 1000 vs 阶段 100 不比较 / 万元↔元归一 0 finding / 单位未知
  insufficient_data；
- 解释豁免双测（PA-1"已在上年安排"、PA-2 说明侧差异原因）；
- 混合结局：PA-1 finding + 配对面 unresolved → RuleDeferred partial 不丢
  finding；两形态全缺 → not_applicable；
- Mutation A（项目身份删除→按位置配对，命中的 project 翻转必红）、
  Mutation B（阶段身份忽略→累计当年度，真实建管委凭空出现 54,518.07 万
  差异必红）、Mutation C（撤注册表→义务回 not_implemented 必红）；
- 注册表 budget-only、管线执行、台账收口与回执驱动。

## 五、验证

| 检查 | 结果 |
|---|---|
| `pytest tests/`（全量） | 见最终汇报 |
| `ruff check src/ tests/ api/ scripts/` | All checks passed |
| `mypy api src tests` | Success: no issues found in 254 source files |
| `check_coverage_baseline.py --assert-gaps 0` | 退出码 0；obligations-v9 |

## 六、过程教训

1. **表单固定字段不是解释**：申报表「上年结转资金」含"结转"，解释豁免若
   扫表单文本，PA-2 在真实材料上永不触发——豁免只看说明侧散文。
2. **章节边界必须含申报表终止符**：最后一个说明章节若放任到文末，会把
   申报表页吸进章节文本（同 1 的根源）。
3. **稀疏夹具扭曲年度投票**：`_resolve_fiscal_year` 按页年份计数投票，
   夹具只冻结真值页会让真值页的往年年份密度压过封面年度。
4. **smoke 的裸 except 会吞 NameError**：漏 import 导致整个扫描静默空转
   （tmp 脚本教训，与规则代码无关但复用了同款防御式写法）。
