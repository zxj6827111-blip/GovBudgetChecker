# WP4-A：三公经费表 × 基本支出经济分类表 跨表资金来源一致性

日期：2026-09-24
分支：`fix/obligation-cross-san-gong`
基线：`main @ 3b382fd`（PR #47 普通 merge commit）→ 本 PR 一个 obligation 一条实现
状态：**未合并**（等独立评审）

## 1. 本 PR 的唯一目标

覆盖缺口从 8 减到 7，且只减 `OBL-CROSS-SAN-GONG-ECON`。对应 checker
`V33-CROSS-SAN-GONG-ECON` 从 `pending_checkers` 变成真实注册、真实执行的规则。
其余 7 条缺口一项未动、未删、未标 `not_applicable`；未开始 WP5。

## 2. 事实审计（任务书 §五 六问六答）

| 问题 | 答案 | 证据 |
| --- | --- | --- |
| 哪份冻结真实样张证明漏报？ | 宜川路街道 2025 年度部门决算（41 页），源 PDF SHA-256 `f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03` | `outputs/sample_validation_20260916/1/metadata.json`；人工判定 `outputs/sample_validation_20260916/comparison.md` 的 **Y02**（程度：高，「漏报；未报告这组三项跨表矛盾」） |
| 冲突发生在哪两个具体表？ | 基本支出决算表（FIN_06）对 三公经费支出决算表（FIN_07） | 九表模型：FIN_06 =《一般公共预算财政拨款基本支出决算表》，FIN_07 =《财政拨款“三公”经费支出决算表》；代码侧同源（`fiscal_table_rules.py` 的 `FIN_06_basic_expenditure` / `FIN_07_three_public`） |
| 页码分别是什么？ | FIN_06 第 22 页（含续页 23），FIN_07 第 24 页；说明文字第 36 页仅作佐证，不参与比较 | 结构化解析实测：`page_span=(22,23)` / `(24,24)` |
| 哪些金额/科目构成应比较的业务事实？ | 逐业务项：因公出国（境）费 → 30212 因公出国（境）费用；公务用车购置费 → 31013 公务用车购置；公务用车运行维护费 → 30231 公务用车运行维护费；公务接待费 → 30217 公务接待费 | 样张 FIN_06 行内「编码 + 科目名称」+ FIN_07 列组主体标签，两侧都是文档自述身份 |
| 预期结果？ | 三个业务项的基本支出分项大于三公表同一业务项决算数，各报一条：出国 13.24 > 0、接待 8.31 > 0.30、车辆运行 81.11 > 19.56（万元） | 与 Y02 原文数字逐项一致 |
| 为什么是同口径可比？ | 两表都是**同一财政年度、同一财政拨款口径、同为决算数**；FIN_06 是「基本支出」部分，FIN_07 是含项目支出的**全口径**，两者是**部分与整体**的关系 | 见下一节 |

## 3. 业务关系：单向包含，不是相等（任务书 §六）

```
FIN_07 三公经费决算数（财政拨款全口径 = 基本支出 + 项目支出）
   ⊇  FIN_06 基本支出经济分类同业务项决算数（基本支出部分）
```

因此**可比关系是「部分 ≤ 整体」**：

- 要求**相等**是错的——会把「项目支出里列支的三公」误判成差异（正常口径），
  真值样张里三公表的公务接待费 0.30 完全可能全部发生在项目支出；
- 反向（部分 > 整体）在任何口径下都不成立，是**两表不能同时成立**的硬矛盾。

**明确不做的错误实现**：不得拿「三公经费合计」去比「经济分类表总计」。
这两个是不同层级的口径（总额之间差着基本/项目两个层级），没有业务依据；
只有逐业务项的包含关系可判定。规则里也没有任何按金额相近配对的逻辑。

## 4. 实现（消费结构化事实，不重造取数）

| 事实 | 来源 | 说明 |
| --- | --- | --- |
| 表身份 | `parse_parsed_tables` 的 `anchor_table_name` / 表名锚，经 `_find_parsed_table` 两级匹配 | 命中 0 张或 ≥2 张 → 取数不足，绝不取第一张 |
| FIN_07 业务项身份 | `ParsedTable.column_groups[].subject`（合计/因公出国（境）费/小计/公务用车购置费/公务用车运行维护费/公务接待费） | 主体标签是文档自述身份；同名重复出现 → 列身份歧义 → 取数不足 |
| FIN_07 期间/金额列 | 列组的 `columns['final']`（决算数列） | 只消费决算数列，不碰预算数 |
| FIN_06 车道 | `semantic_columns['final']` 的**全部**金额列位置划车道 | 样张 P22 为 `[2, 5]`（左栏第 2 列、右栏第 5 列）；同车道内编码/名称/金额同属一行业务项，跨车道数字不互相认领 |
| FIN_06 费用项身份 | 车道内科目名称（去空白 + 全半角括号统一后**精确命中**等价写法） | 「费/费用」「公务用车购置/公务用车购置费」是同一业务项在两表里的固定写法差异；不做模糊匹配 |
| 金额三态 | `ParsedCell.number`（空 → `None`，不补 0） | 空白/破折号不得直接当 0 |
| 金额单位 | `doc.units_per_page`（`extract_money_unit`） | 两侧单位都必须识别且一致才比较；不一致 → 取数不足 |
| 财政年度 | `doc.dominant_year` → 否则前 3 页 + 两表所在页的年份众数 | 并列最高频或取不到 → 取数不足 |

**fail-closed 清单**（任一项不成立就不产生正式 finding，记 `insufficient_data`/解析歧义）：
表身份、列组身份、费用项身份、车道归属、金额列身份、金额单位、财政年度、
两表任一 `parse_errors` 非空、金额单元格出现非数值文本。

**空白单元格确认为 0 的条件**（不是猜测）：三公表两条等式在显示舍入包络内成立
——`合计 = 因公出国（境）费 + 公务用车购置及运行维护费小计 + 公务接待费`、
`小计 = 公务用车购置费 + 公务用车运行维护费`。等式不成立时空白仍属未确认。

**金额计算**：全部走 `src/engine/amount_math.py`——`Decimal` 相比、
`compute_dynamic_envelope` 动态舍入包络。超出包络才是 `error`，包络内的超出
降为 `info`「可能为取整误差」。没有自造 tolerance，没有 float 相减。

## 5. 输出与证据（任务书 §十一/§十二）

每条 finding 携带：规则编号、义务编号（`OBL-CROSS-SAN-GONG-ECON`）、两侧业务项、
两侧金额、差额、单位、财政年度、两侧表身份与页码（`location.pages = [22, 24]`、
`table_refs` 逐侧带 role/table/page/row/code/field）、FIN_06 经济分类编码与科目名称、
以及「哪一侧有误无法判定、需人工复核」的明示。信息不足时不给结论性措辞。

## 6. 清单与台账（任务书 §十六/§十七）

- `OBL-CROSS-SAN-GONG-ECON`：`pending_checkers` 删除，改为
  `checkers_by_kind=_final("V33-CROSS-SAN-GONG-ECON")`；
- `depends_on` 由 `("table:FIN_07",)` 修正为 `("table:FIN_06", "table:FIN_07")`
  （该义务本就是跨这两张表；FIN_06/FIN_07 与代码侧表 code 一致）；
- `basis` 写入业务关系 + Y02 真值出处；`gap_note` 清空；
- 清单版本 `obligations-v2` → `obligations-v3`（checker 构成变化必须递增，
  否则历史结论无法回答"当时按哪版要求判定检查完整"）；
- 台账状态随回执真实形成：`pass`/`fail` 都算 `completed`，
  `insufficient_data` 仍是未完成并阻塞门禁（进人工补核待办）。

## 7. 覆盖基线（任务书 §十八/§十九）

```
python scripts/check_coverage_baseline.py --assert-gaps 7   # 退出码 0
```

| 项 | 改前 | 改后 |
| --- | --- | --- |
| 实现缺口 | 8 | **7** |
| final 应检查/未完成 | 44 / 44（阻塞 43） | 44 / 44（阻塞 43） |
| final `by_reason` | 未执行 36 / 尚未实现 7 / 语义模型未执行 1 | 未执行 37 / **尚未实现 6** / 语义模型未执行 1 |
| 清单指纹 | `5461a0267d3d6ac4`（obligations-v2） | `15932b13f7ac0727`（obligations-v3） |

剩余 7 项 = 原 8 项 − `OBL-CROSS-SAN-GONG-ECON`，逐条核对无增减：
`OBL-TXT-FUND-DETAIL`、`OBL-NARRATIVE-INDICATOR-REPEAT`、`OBL-TREND-ZERO-BASE`、
`OBL-TREND-COMPLETION-RATE`、`OBL-DISCLOSURE-PERCENT-UNIT`、`OBL-SG-COMPLETION`、
`OBL-PERF-PHASE-AMOUNT`。

**未改 `docs/baselines/wp0_coverage_baseline.json`**（WP0 比较基准，不是答案文件）。
本轮能力结果另存为 `docs/baselines/wp4a_coverage_current_20260924.json`，
两份文件并存，互不覆盖。

## 8. truth 回归与变异验证（任务书 §十三/§十四/§二十四）

冻结真值夹具：`tests/fixtures/cross_san_gong_truth_page_data.json`
（宜川样张 41 页的 page_texts/page_tables，源 SHA 与夹具内容 SHA 双锁；
本地有真实 PDF 时另做 SHA 交叉校验）。

| 用例族 | 内容 | 结果 |
| --- | --- | --- |
| 正例（真实缺陷） | Y02 三项冲突逐项命中，金额/差额/页码/业务项/严重度全断言 | 通过 |
| 负例（真实一致样张） | 生态环境局样张（302-12/17/31=0.00/0.00/16.95 对 0.00/0.00/16.95）零 finding | 通过 |
| 负例（部分 < 整体） | 基本支出 8.31 ≤ 三公 9.00 属正常口径，不报 | 通过 |
| 舍入级 | 超包络内降为 info，不报 error | 通过 |
| 误配反例 | 出国/接待金额对调后按业务项身份报（13.24 vs 0.30 报在公务接待费） | 通过 |
| 车道隔离 | 金额移入另一栏后不再认领，不产生假冲突 | 通过 |
| 不可比较 | 表缺失、列组重名、单位缺失、年度不可确认、非数值单元格、勾稽不成立的空白 | 均取数不足 |
| 部分取数不足 | 已确认冲突以 `partial_issues` 保留，整体记 `insufficient_data` | 通过 |
| 注册与台账 | final 注册表含该 checker、budget 不含；回执六态映射；门禁待补核集合随状态变化 | 通过 |
| 端到端 | 走真实 final 规则集执行，三条冲突仍在 findings 与 `outcomes`（status=fail） | 通过 |

变异验证（脚本临时改源码、跑完自动还原）：

| 变异 | 期望 | 实测 |
| --- | --- | --- |
| 把 checker 从 `ALL_RULES` 撤下 | 端到端用例红 | **1 failed**（`test_truth_defect_survives_the_real_final_pipeline`） |
| 把 FIN_06 的经济分类身份改错（出国↔接待名称对调） | 真值用例 + 误配反例红 | **2 failed** |
| 还原 | 全绿 | 4 passed |

## 9. 验证汇总

| 检查 | 结果 |
| --- | --- |
| 全量 `pytest` | **1895 passed / 135 skipped**（改前 1868/135；本机同一环境对照） |
| `ruff check .` | All checks passed |
| `mypy api src tests` | Success: no issues found in **247** source files |
| `scripts/check_log_message_safety.py` | 通过 |
| `scripts/check_env_consistency.py` | 通过 |
| `scripts/check_replay_thresholds.py --uploads tests/fixtures/replay/pass` | 通过 |
| 真库 Review lifecycle（CI 口径 `-m real_database`） | **70 passed / 0 skipped** |
| 真库全量（6 个 `*_pg.py`） | 134 passed / 1 failed（见残留，改前基线同样失败） |
| 前端 `npm run test:unit` | 全部子套件通过 |
| 前端 `npm run build` | 通过 |
| E2E | **198 passed / 18 skipped**（首轮 1 条 `report-actions` 勾选用例失败，单跑与复跑均通过，属既有偶发） |
| `check_coverage_baseline.py --assert-gaps 7` | 退出码 0 |

## 10. 明确不做（任务书 §二十一/§二十二）

其余 7 条缺口、WP5（structured consumption convergence，V33-117/120 的
结构化消费收敛）本轮一律不碰。`V33-117`/`V33-120` 的结构化迁移问题未在本轮
动手，也未记录新发现。

## 11. 残留与风险

1. **真库 `tests/test_material_slot_migration_pg.py` 有 1 条既有失败**
   （`test_rollback_restores_previous_shape_without_losing_versions`：
   `DROP TABLE review_sessions` 被 WP3-B 新增的
   `review_obligation_decisions.review_session_id` 外键挡住）。已在**未修改基线**
   上复现同样失败，不属本 PR 引入，本 PR 也不修（不在范围内，且改它属于 WP3-B 的
   迁移回滚口径问题）。
2. **本机 3000 端口被另一个无关 Next 应用占用**，E2E 用
   `E2E_BASE_URL=http://127.0.0.1:3100` 运行；CI 使用默认端口，不受影响。
3. **空白单元格确认 0 的边界**：只在本表两条勾稽等式于显示舍入包络内成立时才认。
   若某地三公表不列「小计」列组，该表的分项空白将一直按取数不足处理（宁可待人工，
   不猜 0）。
4. **规则只做「部分 ≤ 整体」**：不判"项目支出里应列多少"，也不判"哪一侧有误"——
   人工判定原文明确"不能仅凭 PDF 确定哪侧正确"，finding 因此不替审校人下结论。
