# WP4-D：同比基期/本期/增减额与增长百分比的确定性复算（CMM-007，零基数）

日期：2026-09-25。基线：main@f15ac0b（PR #52 合并后）。
义务：`OBL-TREND-ZERO-BASE`（比例与变动组，budget+final 通用），清偿后 coverage gaps 5 → 4。

## 一、Truth Discovery（编码前冻结）

真值来源：`outputs/plan_independent_review_20260917/独立验收报告.md`（2026-09-17
独立验收真实反例，登记原文："宜川接待费 0.30、增加 0.30 却写增长 100%；文旅接待费
0.40、增加 0.40 却写增长 100%，仍未报告"）。本会话把两条反例追溯回 PDF 原文并
逐字核实（生产管线同口径提取）。

### TRUTH_A_ID: YICUAN-SANGONG-JIEDAI-ZEROBASE（宜川，验收登记"仍未报告"）

```
TRUTH_ID:           2026-09-17 独立验收反例 · 宜川
document:           上海市普陀区人民政府宜川路街道办事处 2025 年度决算.pdf（41 页）
sha:                f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03
subject:            上海市普陀区人民政府宜川路街道办事处
fiscal_year:        2025
report_kind:        final
page:               36
section:            七、财政拨款"三公"经费支出决算情况说明（（一）总体情况段）

indicator:          公务接待费（支出决算）
current_amount:     0.3 万元（P36 同节三处披露一致："公务接待费支出决算为 0.3 万元，
                    完成预算的 37.5%" / "公务接待费支出决算 0.3 万元，占 1.51%" /
                    "3、公务接待费支出 0.3 万元"）
delta_direction:    增加
delta_amount:       0.3 万元（原句："公务接待费支出决算增
                    加 0.3 万元，增长 100%。"——谓语软换行"增\n加"）
declared_percent:   100%（增长）
unit:               万元

derived_prior:      0.3 − 0.3 = 0（两数显示精度两位小数，显示包络 0.01 内相等
                    → 基期确定为 0，进入零基数正式判断）
expected_result:    必须形成正式 finding：基期为 0，"增长100%"的分母为 0，
                    同比百分比不存在有限定义；本期金额、增减额、增长百分比
                    三者不能同时成立。
```

### TRUTH_B_ID: WENLV-SANGONG-JIEDAI-ZEROBASE（文旅，验收登记"仍未报告"）

```
TRUTH_ID:           2026-09-17 独立验收反例 · 文旅
document:           上海市普陀区文化和旅游局 2025 年度部门决算.pdf（33 页）
sha:                74e6afd06669098f57391a13dad63a6f2a15cac620ac9f32018e79649fe6a367
subject:            上海市普陀区文化和旅游局
fiscal_year:        2025
report_kind:        final
page:               28
section:            七、财政拨款"三公"经费支出决算情况说明（（一）总体情况段）

indicator:          公务接待费（支出决算）
current_amount:     0.40 万元（P28 同节："公务接待费支出决算
                    为 0.40 万元，完成预算的 18.96%"）
delta_direction:    增加
delta_amount:       0.40 万元（原句："公务接待费支出决算增加 0.40 万元，
                    增长 100%。"）
declared_percent:   100%（增长）
unit:               万元

derived_prior:      0.40 − 0.40 = 0（显示包络内相等 → 基期为 0）
expected_result:    同 TRUTH_A，必须报正式 finding。
```

### 三数同属一个同比关系的证明（任务 §五）

两份材料中 current / delta / declared% 满足全部身份要素：

1. **同主体**：同一份材料单主体；
2. **同年度**：均为 2025 年度决算，比较基期为 2024 年度（宜川句"比 2024 年度"）；
3. **同一指标**：current 与 delta 句的指标名逐字对应"公务接待费（支出决算）"，
   同节内无歧义；
4. **同一期间/口径**：current 是"支出决算为"、delta 是"支出决算增加"——都是
   本年支出决算口径的同比变化；
5. **同一单位**：万元；
6. **同一同比关系**：delta 句本身即"比上年增加 X，增长 Y%"的同比句式，percent
   谓词（增长）与方向词（增加）同现一个句读单元。
   文旅同页其余同比句全部自洽（总额 3.04→prior 22.70→86.61% ✓；车辆
   2.64→prior 22.70→88.37% ✓；出国"持平"无数值不绑定）——证明本规则的
   复算在自洽材料上不误报。宜川同页"公务用车…减少 3.59 万元，下降 15.31%"
   复算 prior=23.15→15.51%（该 15.31% 实为总额 19.86+3.59=23.45 的正确值，
   条目行照抄了总额的百分比），但 |15.51−15.31|=0.20pp **在仓库统一百分比
   容差 abs 0.5pp 内 → 按策略不报**；接待费零基数 finding 不受影响（100%
   对无定义值不是容差问题）。宜川预期恰好 1 条 finding。

TRUTH_EVIDENCE_MISSING 不成立，允许实现。

## 二、现有能力审计（先审计，不重造）

- **CMM-005**（common_rules.py:747，OBL-TREND-COMPARATIVE-LOGIC 的 checker）：
  模板残留 / "增加（减少）"异常表述 / "当前为 0 却写比上年增加"（`_ZERO_INCREASE_
  PATTERN`）/ 预决算方向比对。**没有**基期复算、没有百分比复算——9/17 验收
  报告的结论成立。CMM-007 不复制这些。
- **V33-234**（final，OBL-NARRATIVE-AMOUNT）与 **BUD-111**（budget）：
  都是"显式基期 + 本期 + 百分比"复算（`prev <= 0` 时 V33-234 跳过、BUD-111 记
  unresolved）——**零基数无人处理**。两者统一使用 `tolerant_equal(atol=0.5,
  rtol=0.01)` 的百分比容差（即 abs 0.5pp / 相对 1%），按任务 §十七这是仓库
  统一 percentage tolerance，CMM-007 复用同一策略值（Decimal 镜像实现）。
- **BUD-111 的金额容差**是 float 0.05 自造值——CMM-007 不复制，金额侧一律
  Decimal + `compute_dynamic_envelope`（显示半步长包络，同 WP4-A/B/C）。

**边界**：CMM-007 绑定"本期 + 增减额 + 同比百分比"语义单元（V33-234/BUD-111
绑定"显式基期 + 本期 + 百分比"），互补不冲突；四元同现（上年+本年+增减+百分比）
时 CMM-007 做显式基期 vs 推导基期交叉验证（任务 §二十八），与 V33-234/BUD-111
的发现可能在重叠区并存，报告层归并，规则间互不去重。

## 三、实现口径（fail-closed）

**语义单元**（同节内绑定，禁止跨句拼接）：

1. **同比变化句**：`指标(支出决算|决算|支出)?(比 20XX 年度)?(增\s*加|减\s*少|增\s*长|下\s*降)(幅|了)? 金额 单位` + 同句读单元内 `(增长|下降|提高|降低)(幅|了)? X%`。
   谓词字符间容忍 PDF 软换行（宜川原文"增\n加"）。基期年份若显式出现必须等于
   `fiscal_year − 1`，否则该句不绑定（不跨期）。
2. **本期金额**：同节内同指标（名称去"支出决算/决算/支出"后缀归一）的
   `(指标)(支出决算|决算|支出)?(为|是)? 金额 单位` 披露。同节多处披露一致取用；
   不一致 → parse_ambiguity（unresolved，不取就近/首个，任务 §二十一/§J）。
   年初预算/完成率/占比金额因形态不符自然不绑定。
3. **显式基期**：同节 `(上年|上年度|20XX 年度)(的)?指标…(为|是)? 金额 单位`。

**数学**（全程 Decimal）：

- 增加：`derived_prior = current − delta`；减少：`derived_prior = current + delta`。
- **零基数判定**：`|derived_prior| ≤ compute_dynamic_envelope([(scale_c, unit), (scale_d, unit)])`
  （显示半步长之和；0.3/0.3 → 包络 0.01，|0| ≤ 0.01 → 基期确定为 0）→
  若声明了有限百分比 → **正式 finding（error）**："复算基期为 0，'增长X%'的
  分母为 0，同比百分比不存在有限定义；三者不能同时成立，需人工复核底稿"。
  |derived_prior| > 包络 → 非零，进入复算。不把包络外的小残值（如 0.05）当 0。
- **非零复算**：`expected = |current − prior| / |prior| × 100`（Decimal）；方向
  由 delta 符号决定，与声明谓词（增长/下降）矛盾 → finding（error）；
  百分比用仓库统一策略值比较（Decimal 镜像 abs 0.5pp / 相对 1%，超出即 finding；
  |声明−复算| > 1pp → error，否则 warn——沿用 BUD-111/V33-234 的分级）。
- **显式基期交叉验证**：`|explicit_prior − derived_prior| > 金额包络` → 四元矛盾
  finding（error，"不能同时成立"）；一致则并入复算。
- **不判断哪一侧正确**（任务 §三十）：所有 finding 只声明矛盾事实。

**不处理**（其它义务/其它规则）：完成率（"完成预算的X%"）、占比（"占X%"）、
持平句（无数值）、模板残留与"当前为0却写增加"（CMM-005）。

**Deferred 语义**：parse_ambiguity / 槽位金额不可解析 → RuleDeferred(partial_issues)，
已确认 finding 不被吞（沿用 WP4-B/C 模式）。

## 四、真值与测试计划

- **真实正例**：宜川（复用冻结夹具，SHA 双锁）→ 接待费零基数 finding（error，
  P36，0.3/增加0.3/100%，derived_prior=0）；同页车辆行百分比复算 finding
  （15.51% vs 15.31%，额外语义发现，如实冻结）。文旅（新冻结夹具
  `tests/fixtures/wenlv_narrative_truth_page_data.json`，源 SHA 74e6afd0…，
  夹具 SHA 07813ae6…）→ 恰好 1 条零基数 finding（0.40/增加0.40/100%），
  其余同比句自洽不报——干净的真值文档。
- **预算 contract fixture**（CONTRACT，非真实样张）："本年预算 50 万元，比上年
  预算增加 50 万元，增长100%" → 零基数 finding；budget 注册表执行路径。
- **负例 A~J**（任务 §二十七）：只有增减额无百分比（0）/ "由0增加至0.30"（0）/
  正常增长 120/20/20%（0）/ 百分比错误 120/20/50%（finding）/ 方向错误（finding）/
  正常下降 80/20/20%（0）/ 完成率不处理 / 占比不处理 / 不同指标不拼接 /
  多候选 parse_ambiguity。
- **显式基期四元交叉**：一致（0 finding）与冲突（finding）。
- **Mutation A/B/C/D**（任务 §三十八~四十一）现场打红→还原全绿。

## 五、登记

- `src/engine/common_rules.py` 新增 `CMM007_ZeroBaseTrendRecompute`（code
  CMM-007）进 `ALL_COMMON_RULES` → budget 与 final 两个注册表都含（复用
  `check_obligations._registry_rule_ids` 对 ALL_COMMON_RULES 的既有合流）。
- `OBL-TREND-ZERO-BASE`：`pending_checkers` → `checkers_by_kind=_both(
  ("CMM-007",), ("CMM-007",))`；清单版本 obligations-v5 → **v6**。
- 复用既有 registry↔catalog invariant（test_declared_checkers_exist_in_the_real_
  registry），不新造。
- 不动 WP5/其余 4 缺口/V33-234/BUD-111/CMM-005。
