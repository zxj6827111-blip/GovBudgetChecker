# WP4-C：文内同一指标重复披露一致性（V33-NARRATIVE-INDICATOR-REPEAT）

日期：2026-09-25。基线：main@df05f7a（PR #51 合并后）。
义务：`OBL-NARRATIVE-INDICATOR-REPEAT`（文内关系组，final only），清偿后 coverage gaps 6 → 5。

## 一、Truth Discovery（编码前冻结，禁止凭数字接近自行推断）

真值来源：`outputs/sample_validation_20260916/`（2026-09-16 人工复核批次，
19 组确定性问题中 5 组命中、14 组漏报）。该批次对本义务相关的是两组
**真实漏报**，都是"同一材料内同一指标两处披露不一致、系统当时未报告"：

### TRUTH_ID: S03（主真值，人工判定 高 → 本规则应报 error）

```
TRUTH_ID:           S03（sample_validation_20260916，人工判定"高"，系统 miss）
document:           上海市普陀区人民政府石泉路街道办事处 2025 年度决算.pdf（44 页，
                    uploads/putuo_final_samples/ 在库）
sha:                e8315830f800e58038b19bfd0f1e36f5930d9da72a22c3e79f781036bd5289a4
subject:            上海市普陀区人民政府石泉路街道办事处
fiscal_year:        2025
report_kind:        final（部门/单位决算公开）

indicator:          机关事业单位职业年金缴费支出（项级功能分类业务项）
unit:               万元

occurrence A:
  page:             32
  section:          五、一般公共预算财政拨款支出决算情况说明（标题在该材料 P27）
  sentence:         "社会保障和就业支出（类）行政事业单位离退休（款）
                    机关事业单位职业年金缴费支出（项）"257.14 万元，主要
                    用于：缴纳机关事业单位在职干部职业年金。
  amount:           257.14
  measure:          本年支出决算（条目首句项金额，决算支出明细条目模板）
  period:           2025 年度（本年）

occurrence B:
  page:             33（与 occurrence A 同一条目，PDF 跨页断开）
  section:          同上（同一节、同一条目）
  sentence:         （年初预算为）274.82 万元，支出决算为 245.53 万元，
                    完成年初预算的 93.57%。
  amount:           245.53
  measure:          本年支出决算（显式"支出决算为"）
  period:           2025 年度（本年）

expected:           表内比较：257.14/274.82 = 93.57% 与同句完成率吻合；
                    245.53/274.82 = 89.34% 与 93.57% 不吻合。两处披露同一
                    指标的本年支出决算金额相差 11.61 万元，不能同时成立 →
                    必须报 finding（error）。
why_same_indicator: 两处都在"「类/款/项」三级完整身份"的职业年金条目内：
                    类=社会保障和就业支出、款=行政事业单位离退休、
                    项=机关事业单位职业年金缴费支出，逐级全同，单条目单主体。
why_comparable:     同一年度（同一份 2025 年度决算）、同一主体（单主体材料）、
                    同一 measure（A 是决算支出明细条目模板的条目首句金额，
                    B 是显式"支出决算为"）、同一期间（均无上年/异年标记）、
                    同一单位（万元）。表侧佐证：P9/P14 功能分类表 2080506 与
                    P23 经济分类表 30109 均为 257.14——但本规则只做文内↔文内
                    比较，不依赖表侧。
```

### TRUTH_ID: Y07（次真值，人工判定 低 → 本规则应报 info）

```
TRUTH_ID:           Y07（sample_validation_20260916，人工判定"低"，系统 miss）
document:           上海市普陀区人民政府宜川路街道办事处 2025 年度决算.pdf（41 页）
sha:                f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03
subject:            上海市普陀区人民政府宜川路街道办事处
fiscal_year:        2025

indicator:          机关事业单位职业年金缴费支出（项级功能分类业务项）
unit:               万元

occurrence A:
  page:             31
  section:          五、一般公共预算财政拨款支出决算情况说明（条目 15）
  sentence:         机关事业单位职业年金缴费支出（项）269.85 万元，主要用于:
  amount:           269.85
  measure/period:   本年支出决算 / 2025 年度
occurrence B:
  page:             31（同一段落、同一条目内）
  sentence:         年初预算为 285.33 万元，支
                    出决算为 269.86 万元。
  amount:           269.86
  measure/period:   本年支出决算 / 2025 年度

expected:           同条目两处差 0.01 万元。人工判定明确："属于同一指标抄写
                    不一致，不是合计舍入尾差"（程度：低）。0.01 恰在两侧
                    两位小数显示精度的动态舍入包络内（0.005+0.005），本规则
                    按 info 报告（"可能为取整误差或抄写差异，需人工复核"），
                    不得静默，也不得升为 error。
```

人工判定原文（`comparison.md`）：

- S03：**职业年金同段金额与比例冲突**：前段 257.14、后段 245.53 万元，差 11.61。
  预算 274.82；93.57% 对应 257.14；245.53/274.82 仅 89.34%。| 漏报；未报告
- Y07：**职业年金同段金额矛盾**：首句 269.85、后文决算 269.86 万元；相差 0.01。
  属于同一指标抄写不一致，不是合计舍入尾差。| 漏报；未报告

两处真值都来自 `adjudication.json` confirmed_groups（S03: doc 2, severity 高,
system_match=miss；Y07: doc 1, severity 低, system_match=miss）。

## 二、现有能力审计（先审计，不重造）

对当前 main@df05f7a 的引擎把两份真值材料实跑一遍（2026-09-25）：

- `R33235_NarrativeAmountConsistency`（V33-235"同条决算说明前后金额一致性"）：
  石泉 0 条、宜川 0 条。结构性盲区三条：
  1. `_iter_final_narrative_segments` **按页切段**——S03 条目跨 P32/P33 断开，
     任何一段都凑不齐"开头金额+支出决算金额"配对；
  2. `_FINAL_ITEM_START_RE` 只认 `N、` 编号形态——石泉条目是 `6."…"` 形态，
     整页不产段；
  3. `tolerant_equal(atol=0.05, rtol=0.0005)` 自造容差——Y07 的 0.01 差被吞。
- `R33234_NarrativePercentConsistency`（V33-234 完成率复算）：两份材料均 0 条
  （S03 的 93.57% 角度当前也未命中；该角度属 OBL-NARRATIVE-AMOUNT，本轮不动）。
- 全量 final 管线：两份材料上职业年金相关 finding 均为 0。
- V33-101~108 系是"说明 ↔ 表格"的单点/总额比对（WP4-B 已审计），与文内↔文内
  重复披露无重叠。

**边界决定**：V33-235 是 `OBL-NARRATIVE-AMOUNT`（已清偿义务）的登记 checker
（`check_obligations.py:568`，与 V33-234/V33-232 并列），按"不碰其它义务"纪律
**保持原样**。本规则与其在"同页 + `N、`形态 + 差>0.05"的窄区存在有界重叠
（该区可能同时产出 V33-235 warn 与本规则 error/info，报告层按同组归并），
在 PR 里如实声明，不做跨规则去重（规则间互不知情，去重应由报告聚合层做）。

## 三、实现口径（fail-closed）

**身份模型（Indicator Identity）**——比较组内必须逐项相同：

| 要素 | 取值方式 |
|---|---|
| subject | 同一材料单主体（final 材料默认） |
| fiscal_year / period | `_resolve_fiscal_year`；句前缀/名称段出现异年 → 不绑定 |
| indicator | **三级身份**（类\|款\|项 归一拼接）或**裸名身份**（角色词紧邻的完整指标词串，仅限同章节内互比） |
| measure | 仅 `current actual`（本年支出决算）参与比较；预算/上年/增减额/百分比/数量一律只识别不参与 |
| unit | 每处金额自带单位字样（万/亿/元，容忍 PDF 软换行插空白），归一到万元；缺单位不绑定 |

**绑定语法（deterministic）**：

- **P1 条目首句**：`X（类）Y（款）Z（项）[”』」"]?[，,]? 金额 单位`
  （沿用 WP4-B 的类款项正则形态，放宽闭引号以覆盖石泉 `"（项）"257.14` 形态；
  名称段允许内部换行）。**measure 锚**：条目内必须存在"支出决算为"显式决算
  金额（决算支出明细条目模板自证），否则条目首句金额不绑定——文旅局
  "开头无金额"形态由构造上不匹配自然免疫。
- **P2 显式决算金额**：`支\s*出\s*决\s*算\s*为 金额 单位`（字符间容忍软换行
  空白——宜川 Y07 原文即"支\n出决算为"），绑定到同条目最近的 P1 三级身份。
  前缀出现"上年/上年度/以前年度"→ 期间=上年，不参与比较。
- **P3 裸名 + 角色/期间词**（合同负例/正例路径）：
  - 期间前缀式：`本年[X]金额单位` / `上年[X]金额单位`；
  - 角色后缀式：`[X](年初预算|预算|支出决算|决算|支出)(为)? 金额单位`；
  - 身份 = 完整指标词串（去"本年"前缀后归一），**仅同章节内互比**（越跨章
    越严：裸名不允许跨章节聚合）；
  - **通用结构词拒绝表**（防止"基本支出/项目支出/工资福利支出/人员经费/
    公用经费/其他支出…"这类结构性类目名在文内合法地多次出现不同数值时被
    误配），外加动态拒绝：裸名等于本文档任一 P1 条目的类/款级名称 → 拒绝
    （类/款级名称的重复披露天然可能指不同下级口径）。
- **歧义（parse_ambiguity）**：P3 裸名绑定后，同句内还存在**无角色词**的
  第二个"金额+单位"候选（如"…245.53万元和257.14万元"）→ 该处不绑定并记
  unresolved（禁止取最近/首个/最大）。P1/P2 槽位由结构定义，天然无歧义。

**比较**：同一身份的 `current actual` 披露 ≥2 处时两两比较（全程 Decimal，
万元口径；`compute_dynamic_envelope([(scale_a, unit_a), (scale_b, unit_b)])`）：

- 差 = 0 → 不报；
- 0 < 差 ≤ 包络 → **info**（"可能为取整误差或抄写差异，需人工复核"——Y07）；
- 差 > 包络 → **error**（S03：差 11.61）。
- 同组取差值最大的一对作为 finding 的 A/B。

**证据契约（双向可定位）**：每条 finding 带 `value_a/value_b/difference/
unit/indicator/measure/period/fiscal_year/obligation_id` + `table_refs`
两角：`披露A`（页/章节/命中原文 span）与 `披露B`（同）。措辞只说"两处披露
不能同时成立，需人工复核底稿"，不判断哪一侧正确。

**Deferred 语义**（沿用 WP4-B）：出现 parse_ambiguity 或"槽位匹配但单位不在
归一口径"等取数不足时，`RuleDeferred(code, partial_issues=已确认冲突,
unresolved_reasons=…)`；已确认冲突不被部分取数不足吞掉。

**不做**：本年↔上年、决算↔预算、指标↔增减额的跨口径比较；全指标 NLP 泛化；
STRUCTURED_PARSING_CONSUMERS / STRUCTURED_MIGRATED_RULES 登记（WP5 未接入，
沿用诚实口径）。

## 四、真值与测试计划

- **真实正例**：石泉 S03（冻结夹具 `tests/fixtures/shiquan_narrative_truth_page_data.json`，
  SHA 双锁：源 `e8315830…` + 夹具 `be8abd46…`）→ 必须报 1 条 error
  （257.14 vs 245.53，差 11.61，页 32/33，章节五）。石泉全文档其余条目
  首句==支出决算为（252.74/220.79/545.84/5.43/200.18/177.17/7.11…）→
  0 finding（真实同值负例）。宜川 Y07（复用 WP4-A/B 冻结夹具）→ 必须报 1 条
  info（269.85 vs 269.86，差 0.01）。
- **合同负例/正例（合成 contract fixture，非真实 truth）**：A 本年vs上年（0）、
  B 本年vs增减额（0）、C 预算vs决算（0）、D 单位归一同值（0）、E 不同指标
  （0）、F 同指标同值（0）、G 同指标同口径不同值（正式 finding）。
- **Mutation A/B/C**：去掉期间/measure 身份 → Case A 红；只按关键词聚合 →
  Case C 红；撤出注册表 → truth pipeline / final-only 注册 / 台账收口红。
- **生态真实负例**：生态环境局样张跑 0 finding（其说明条目自洽）。
- `registered_rule_ids("final")` 含、`("budget")` 不含；`--assert-gaps 6 → 5`。

## 五、登记

- `ALL_RULES` 追加 `R33NarrativeIndicatorRepeat()`（final 注册表 60 → 61；
  budget 不含）。V33-235 原样保留（属 OBL-NARRATIVE-AMOUNT，重叠区在 PR 里
  如实声明）。
- `OBL-NARRATIVE-INDICATOR-REPEAT`：`pending_checkers` → `checkers_by_kind=
  _final("V33-NARRATIVE-INDICATOR-REPEAT")`；清单版本 obligations-v4 → v5。
- 不动 WP5/其余 5 缺口/Golden 扩容/UI/新 migration。
