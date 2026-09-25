# WP4-E：预算完成率分母口径与确定性复算（V33-TREND-COMPLETION-RATE）

- 日期：2026-09-25
- 分支：`fix/obligation-completion-rate`（基线 = main 93c0ca0，即 PR #53 WP4-D 合并后）
- obligation：`OBL-TREND-COMPLETION-RATE`（GROUP_TREND，final only）
- checker：`V33-TREND-COMPLETION-RATE` / `R33TrendCompletionRate`（src/engine/rules_v33.py）
- 覆盖缺口：4 → **3**（剩余 OBL-DISCLOSURE-PERCENT-UNIT / OBL-SG-COMPLETION / OBL-PERF-PHASE-AMOUNT）

## 一、Truth Discovery 结论

### R004 正例：POLICY_CONTRACT（非真实样张，如实登记）

对仓库全部真实样张文本做了穷尽检索（宜川/石泉/文旅/普陀/长风/9-05 生态
corpus/历史独立验收产物），「年初预算为 0 + 完成年初预算的 X%」组合**不存在
真实正例**——所有零预算条目（石泉死亡抚恤「年初预算为 0 元…支出决算为
177.17 万元」、文旅企业扶持「年初预算为 0 万元，支出决算 2349.56 万元」、
普陀垃圾分类等）都合规地写「决算数大于预算数的主要原因」，未给完成率。

因此正例冻结为政策合同（不得伪造成真实历史漏报）：

```
TRUTH_ID: WP4E-R004-PC-01
truth_source: POLICY_CONTRACT
document/sha/subject/fiscal_year/page/section: N/A
indicator: N/A（政策示例未绑定具体指标）
initial_budget: 0（披露形态「年初预算为0」，无单位后缀）
actual: 307.82 万元
declared_completion_rate: 95.89%
denominator_phrase: 完成年初预算的
expected: 报错——年初预算为 0，无法作为「完成年初预算的95.89%」的有效分母
why_invalid: 分母为 0，完成率无数学定义
policy source: AGENTS.md R004（默认 critical）；rules/v3_3.yaml R004
```

### 真实负例（REAL，fixture SHA 双锁）

| 真值 | 材料 | 位置 | 数据 | 预期 |
| --- | --- | --- | --- | --- |
| NEG-REAL-01 | DOC-20260905-001（SHA 113b98bb…） | P21 总述句 | 4628.17 / 4733.14 / 102.27% | PASS（复算 102.2696…%） |
| NEG-REAL-01b | 同上 | P26 三公总额 | 21.00 / 16.95 / 80.71% | PASS（80.714…%）；同页分项声明（82.68%/0.00%）无分项预算披露 → fail-closed 不比，不绑总额分母 |
| NEG-REAL-02 | 石泉（SHA e8315830…） | P33 公益性岗位补贴 | 256 / 200.18 / 78.20% | PASS |
| NEG-REAL-03 | 石泉 | P34 其他城市生活救助 | 30 / 30 / 100% | PASS |
| NEG-REAL-04 | 石泉 | P33→P34 跨页 死亡抚恤 | 0 元 / 177.17 万元 / 无完成率 | 0 finding（零分母无声明 → 合规） |
| NEG-REAL-05 | 宜川（SHA f809eef2…） | P28 总述句 | 20707.11 / 20218.21 / 98% | PASS（复算 97.64%，容差内） |
| NEG-REAL-06 | 文旅（SHA 74e6afd0…） | P22 总述句 | 27780.16 / 24535.67 / 88.32% | PASS |

### 交叉负例（石泉 S03，任务 §二十八）

机关事业单位职业年金条目：首句 257.14 vs「支出决算为 245.53」，完成率
93.57% **按首句自洽**（257.14/274.82=93.57%）。该矛盾归
V33-NARRATIVE-INDICATOR-REPEAT（WP4-C 的 S03 finding）报告；本规则把首句
金额纳入分子交叉验证 → 两个不一致分子候选 → parse_ambiguity fail-closed
（整份材料 deferred、partial_issues 为空），**绝不任选其一复算而误判完成率
本身**。该行为有专项测试锁定。

## 二、与 V33-234 的边界（任务 §十三~十五）

V33-234（OBL-NARRATIVE-AMOUNT 登记 checker）的完成率分支
`_FINAL_COMPLETION_PERCENT_RE` 只认「年初预算(数)?为 X 万元…决算为 Y 万元
…完成(年初)?预算的 Z%」邻近窗口三元组。已证实漏检（本规则补齐）：

1. 「年初预算为0」（无万元后缀）与「年初预算为 0 元」（元单位）——正则要求
   `\s*万元`，而这两类恰恰是 R004 零分母的典型披露形态（政策例句、石泉
   真实条目都因此不命中）；
2. 「完成全年/调整预算的X%」整句不在其匹配范围（pct 锚没有全年/调整身份
   分支）——分母身份切换后的复算无人验证；
3. 条目内同身份多个不一致预算无 parse_ambiguity 处理，120 字符窗口可跨句
   拼接。

**有界重叠**：标准三元组形态（100/90/110%）两规则都会报 error，报告层
互不去重（同 WP4-C 对 V33-235 的纪律）。本规则**不做**「完成率偏离100%
未说明原因」的 warn——那是 V33-234 既有职责（D 之外的超支/欠支原因说明
属 OBL-NARRATIVE-AMOUNT）。V33-234 的 obligation 映射（OBL-NARRATIVE-AMOUNT）
本轮未动。

## 三、数学与容差

- 完成率：`completion_rate = actual / initial_budget × 100`；只有
  `initial_budget != 0` 时才存在有限完成率。全程 **Decimal**（金额解析、
  单位归一 `元=0.0001 万元 / 亿元=10000 万元`、复算、比较）。
- **分母身份**（denominator identity）至少区分：年初预算（含"年初预算数"）、
  全年预算、调整预算；「预算/预算数」为泛身份，只在与声明同句读单元时绑定。
- **零判定**：`Decimal == 0` 精确判零（"0"/"0.0"/"0.00"/"零" 均为 0）。
  **绝不**用 `abs(budget) < 0.01` 判零——「年初预算为0.004万元」是显示出来
  的非零披露，走正常复算（任务 §十九）。
- **分母为 0 不经容差放行**：分母身份确认且披露为 0 时，任何有限完成率
  声明都是定义错误，与舍入无关（任务 §十八）。
- **percentage tolerance source**：仓库统一策略——V33-234/BUD-111 同款
  **abs 0.5pp / 相对 1%**（`tolerance = max(0.5, |recomputed|×1%, |declared|×1%)`，
  Decimal 化沿用 CMM-007 的既有实现形态），不自造第三套。金额侧显示精度
  复用 `src/engine/amount_math.py` 的口径（两位小数显示、`_txt_fund_display`
  输出）。
- **不自行换分母**：系统不替作者把「完成年初预算」改判成「完成全年预算」；
  但当声明数值与条目内其它身份的复算吻合时，报**分母身份错误** finding
  （数值证据指向真实计算分母），与任务 §九/二十五一致。

## 四、绑定纪律（防串线）

- 条目边界：类款项三级结构 +「X、…说明」章节标题 + 行首「（X）小节标题」。
- 分母/分子绑定：claim 所在**句读单元**内按子句段（逗号/顿号/冒号再切）
  **由近及远**逐段就近，本段空才扩大；跨句读单元的承接绑定要求**同章节 +
  同条目 + 指标主语对齐**（clause-head 身份：披露句头主语与声明句头主语一致，
  空主语视为通配归属条目）。
- 三公防错绑：分项完成率（82.68%）的分项预算未在叙事披露时，不把条目内
  三公**总额**预算（21.00）错绑给分项声明 → fail-closed 不比。
- 首句金额交叉验证：条目首句「（类）（款）（项）」X 万元是本年支出决算的
  合法披露，与显式「支出决算为 Y」矛盾时分子 parse_ambiguity（S03）。
- 多候选预算/决算/分子不一致 → parse_ambiguity（RuleDeferred，
  insufficient_data），禁止取首个/最近（Case J、S03 专项测试锁定）。
- 异期披露（句内出现非材料年度年份）不绑定（复用 `_nar_repeat_foreign_year`）。
- 同比（CMM-007 领域）与占比（无「完成…预算」锚）不进 matcher
  （Case H/I + matcher 结构断言锁定）。
- 软换行容忍：可选字素（数/的/的比重/为/是）一律前置 `\s*`——「决算␊为」
  「支␊出决算」都是真实形态（宜川 Y07 教训，本轮实测再验证）。

## 五、severity 映射（任务 §三十一）

AGENTS.md R004 政策级 **critical** → engine Issue schema 的最高级 **error**
（与 CMM-007 零基数、V33-234 分母无效一致；engine 的正式 finding 级别集为
error/warn/info，critical 不存在于该 schema，不得静默降成 low/warn）。
零分母/身份错误/复算不一致三类 finding 全部 error；本规则不产生 warn。

## 六、Mutation 验证（任务 §三十八~四十二，现场打红→还原全绿）

| Mutation | 变异内容 | 守卫测试 | 结果 |
| --- | --- | --- | --- |
| A | 删除 `denom["wan"] == 0` 特判 | test_r004_policy_contract_zero_denominator_is_reported | **红**（1 failed）→ 还原绿 |
| B | `_wp4e_claim_role` 忽略身份（全年当年初） | test_case_f_full_year_denominator_is_legal | **红**（1 failed）→ 还原绿 |
| C | `_pct_close` 恒 True（取消真正复算） | test_case_d_wrong_rate_is_a_finding | **红**（1 failed）→ 还原绿 |
| D | CLAIM_RE 纳入同比句（比上年增长X%） | test_case_h_yoy_percent_is_not_completion_rate | **红**（matcher 结构断言拦截）→ 还原绿 |
| E | 从 ALL_RULES 撤出 final 注册 | test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap（monkeypatch 自动化）+ truth pipeline 断言 | 常绿守卫：撤出后 registry/ledger/缺口三面全红 |

## 七、登记

- `src/engine/rules_v33.py` 新增 `R33TrendCompletionRate`（code
  V33-TREND-COMPLETION-RATE）进 `ALL_RULES` → **仅 final 注册表包含**，
  budget 注册表不含（rules_v33.ALL_RULES 只被 final 合并；任务 §三十二
  有 registry 双向断言）。
- `OBL-TREND-COMPLETION-RATE`：`pending_checkers` →
  `checkers_by_kind=_final("V33-TREND-COMPLETION-RATE")`；
  清单版本 obligations-v6 → **v7**。
- `src/utils/rule_text.py`：规则标题与修复建议映射。
- 测试：`tests/test_completion_rate_recompute.py`（27 项，含真实负例
  fixture SHA 双锁、R004 政策合同、Case A~J 与变体、跨页/多条目隔离/
  三公防错绑、truth pipeline、registry/台账收口/Mutation E）。
- 复用既有 registry↔catalog invariant（test_declared_checkers_exist_in_the_
  real_registry），不新造。
- 不动：CMM-007 / V33-234 / BUD-111 / V33-NARRATIVE-INDICATOR-REPEAT 的
  原义务映射与行为；WP5/其余 3 缺口不碰。

## 八、验证记录（本分支）

- `pytest tests/test_completion_rate_recompute.py`：27 passed。
- `python scripts/check_coverage_baseline.py --assert-gaps 3`：PASS
  （final 未实现缺口 2 项中 OBL-TREND-COMPLETION-RATE 已收口；
  剩余恰好 OBL-DISCLOSURE-PERCENT-UNIT / OBL-SG-COMPLETION / OBL-PERF-PHASE-AMOUNT）。
- `ruff check`（4 个改动文件）：All checks passed。
- `mypy`（rules_v33 / check_obligations / rule_text）：no issues。
- 完整回归（pytest 全量 / 真库硬门 / 前端 / E2E / business replay）见
  PR 描述与本文件提交后的 CI。
