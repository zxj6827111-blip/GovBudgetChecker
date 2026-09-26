# WP4-G：三公经费说明四项细化披露完整性（V33-SG-COMPLETION）

- 日期：2026-09-26（R1 交付）
- 分支：`fix/obligation-sg-completion`（基线 = main 7f0d17c，即 PR #55 WP4-F 合并后）
- obligation：`OBL-SG-COMPLETION`（GROUP_SAN_GONG，**final only**）
- checker：`V33-SG-COMPLETION` / `R33SGCompletion`（src/engine/rules_v33.py，紧邻 V33-245/246）
- 覆盖缺口：2 → **1**（剩余 OBL-PERF-PHASE-AMOUNT，budget 车道）
- catalog：obligations-v8 指纹更新（pending → implemented，不递增版本）

## 一、Truth Discovery（REAL，非自造）

真值不在 2026-09-16 的 19 组人工确认组里（那 19 组是保守登记的"数值性
差错"，完整性类问题走的是官方检查表通道）。真值来自**官方人工检查表**：

- 文档：`上海市普陀区人民政府宜川路街道办事处 2025 年度决算.pdf`（41 页）
- SHA：`f809eef2afa91d258d5fbe4b0d1553dbe3a8ef9c4688d07e3a73aeaecd02ec03`
- 主体：宜川路街道办事处；年度：2025；文种：final（部门决算）
- 人工判定记录：`C:\Users\zxj68\Desktop\普陀区财政局决算检查样张\
  上海市普陀区人民政府宜川路街道办事处（部门决算）检查表.xlsx`
  （2025年度普陀区部门决算公开情况检查表，含公开网址与截图证据）

检查表"是否合格=否"且属本义务范围的一行：

> 是否细化"公务用车购置及运行费"：公开"公务用车购置费"、"公务用车运行费"
> → **否**；问题1：第三部分缺少以下"三公"经费细化披露字段：**公务用车购置费**。

样张对照（冻结夹具逐字核验）：

- P24 表七（FIN_07）列结构六对：合计 / 因公出国（境）费 / 小计 /
  **公务用车购置费**（预算、决算均空白）/ 公务用车运行维护费 30.67/19.56 /
  公务接待费 0.80/0.30——表格结构四项在列；
- P36 说明（二）2：「2、公务用车购置及运行维护费支出 19.56 万元。其中：
  公务用车运行维护支出 19.56 万元。……公务用车保有量为 9 辆。」——
  **公务用车购置费既无金额、也无"未发生"说明**，被合并披露形态掩盖；
- 石泉（购置 25.00/12.35 分别明示）与文旅（购置 0 万元明示 + 同比段
  "未新增公务用车"）均细化披露——只有宜川漏。

historical_system_result：**miss**。2026-09-16 fresh 重跑（
`outputs/sample_validation_20260916/1/system.json`）26 条 finding 全 info，
三公相关的只有两条"表七建议补 0"提示，未报告购置费细化缺失。

同批检查表另有「因公出国团组数」「公务用车购置数」两行"否"——属**数量
要素**，本地披露标准未定（comparison.md 对同类问题判"待裁决"，如石泉
接待 0 时是否须明示 0 批次 0 人次），本轮只收编四项**金额**披露，
不越界到数量要素（批次/人次归 V33-246 领域）。

## 二、Truth 规则冻结（检查对象不混用）

检查对象 = **三公经费支出决算情况说明章节内部四项细化披露**（
`find_section_scope_with_title(merged, ["三公"])` 主章节完整范围，含
（一）（二）子章节，与 V33-245/246 同源；找不到章节 → RuleDeferred，
禁止全文回退）。表格数值**不参与判定**——既有分工：

| 既有义务 | 覆盖面 | 本轮不重复 |
|---|---|---|
| OBL-SG-TOTAL（V33-121/244） | 合计=分项之和 | 金额勾稽 |
| OBL-SG-ITEMS（V33-244） | 表七分项齐备 | 表格披露 |
| OBL-SG-TABLE-TEXT（V33-108/224） | 表文金额一致 | 数值对照 |
| OBL-SG-DISCLOSURE（V33-246） | 国内接待批次/人次 | 数量要素 |
| OBL-SG-YOY（V33-245） | 同比方向与持平矛盾 | 方向逻辑 |
| V33-CROSS-SAN-GONG-ECON（WP4-A） | 三公表×经济分类表 | 跨表一致性 |
| CMM-001 / OBL-SG-BUDGET-TABLE | 预算三公表 | budget 车道 |

本轮补的是官方检查表口径：**说明必须把四项逐项写出来**。合并披露
「公务用车购置及运行维护费」是合法三项口径（检查表"是否细化'三公'
经费支出"行宜川判"合格"），但**不能当作购置费已细化**——这正是宜川
真值被掩盖的形态。判定词干用 `公务用车购置(?!及)` 排除合并形态。

## 三、判定设计（未发生 vs 未披露）

四项：`overseas` 因公出国（境）费 / `vehicle_purchase` 公务用车购置费 /
`vehicle_operation` 公务用车运行维护费 / `reception` 公务接待费。
每项在章节分句级独立判定，**任一满足即披露完整**：

1. 金额在场：数字 +（万元｜万｜元），**0/0.00 同样算披露**（Case D）；
2. 未发生/零值等效语境：「未发生/未安排/未支出/未新购/无支出/持平/无」
   与主体同分句（Case B）；购置另有专属形态「未新增/未购置公务用车」
   （文旅（一）同比段实测，与合并主体同分句）；
3. 整章等效说明：合计主体分句（主体沿分句继承，项级主体优先）写明
   「无三公」或「三公…支出/决算…0 万元」→ 四项全部视为已披露
   （AGENTS.md：没有三公支出时应有"无此项"或等效说明）。

只有既无金额又无等效说明才出 finding（每项一条，`three_public_item`
字段定位到项，不写"三公经费不完整"这种无法定位的问题）。零值判定
锚定：数字以 0 开头、小数段全零（0.30 不算 0）、必须带单位——
"10.00 万元"里嵌在 10 里的 0、非零预算数均不触发。

finding 结构：message 含台账标签 + 期望披露 + 实际披露；
`location` 携带 `three_public_item / item_key / section /
expected_disclosure / actual_disclosure`；`evidence_text` 以
【章节:…】前缀 + 证据段落（缺失项的证据 = 应出现细化披露的最近语境段，
购置项用包含合并形态的段落，宜川落在 P36「2、公务用车购置及运行维护费
支出 19.56 万元。其中：」）。

## 四、负例与边界（任务 §九对照）

| 用例 | 输入形态 | 期望 |
|---|---|---|
| Case A（contract） | 四项完整各带金额 | 0 finding |
| Case B（contract） | 「本年度未发生公务用车购置」 | 0 finding |
| Case C（contract） | 完全缺少公务接待费且无说明 | 1 finding（reception） |
| Case D（contract） | 「公务接待费支出 0 万元」 | 0 finding |
| Case E（contract） | 表七四项有值 + 说明缺购置细化 | 1 finding（购置；表格在场也救不了说明侧缺失） |
| REAL 正例 | 宜川全文档 | 恰好 1 finding（购置费，P36） |
| REAL 负例 | 石泉 / 文旅全文档 | 0 finding |
| 合并-（一）豁免反例 | （一）出现合并主体即豁免 | 仍 1 finding（不豁免） |
| 合并-无子项 | 只有合并金额、两个子项都没有 | 购置 + 运行各 1 条 |
| 全局无三公 | 「本单位无三公经费财政拨款收支」 | 0 finding |
| 全局合计为 0 | 预算 5.00 / 决算 0 万元 | 0 finding（非零预算不误触发） |
| 章节缺失 | 无三公说明章节 | RuleDeferred（证据不足） |
| 空文本 | page_texts 全空 | RuleDeferred |

## 五、Mutation

- **Mutation A（删除完整性判断）**：`_collect_disclosed` 直接返回全部
  已披露 → 7 条测试红（含 REAL 真值正例）→ 还原后 21 条全绿。
- **Mutation B（0 金额当缺失）**：金额披露判定改为要求 >0 → 8 条测试红
  （Case D、文旅 REAL 负例、宜川"出国 0.00 不得误报"断言全部抓住）→
  还原后全绿。
- **Mutation C（撤 registry）**：自动化用例
  `test_withdrawing_the_checker_puts_the_obligation_back_as_a_gap`——
  monkeypatch 从 `ALL_RULES` 撤下后，`registered_rule_ids("final")` 不含
  本规则，final 台账该义务回到 `not_implemented`。

## 六、验证与证据

- 单元：`pytest tests/ -q`（除 PG/E2E）**2064 passed / 123 skipped**；
  其中 `tests/test_sg_completion.py` 21 条全绿。
- 真库 PostgreSQL：`GOVBUDGET_TEST_DATABASE_URL=… pytest
  tests/test_obligation_review_pg.py` **12 passed**（随机 schema 隔离）。
- ruff check . **通过**；mypy api src tests **通过**（253 文件）。
- coverage：`python scripts/check_coverage_baseline.py --assert-gaps 1`
  **PASS**；final 注册表 64 → **65** 条，final 制度性完成率上限 **1.0**
  （0 项尚无实现），剩余缺口仅 budget 车道 OBL-PERF-PHASE-AMOUNT。
- business replay（CI 口径 `--uploads tests/fixtures/replay/pass`）
  **PASS**；`./uploads` 全量语料的 5 项结构性 FAIL 为**既有状态**
  （replay 只做存量产物结构分析，不执行规则引擎，与代码改动无关；
  2026-09-09 历史报告 report_id unique=False 可佐证）。
- E2E：`npm --prefix app run test:e2e` 本机执行结果见 PR 描述。

## 七、已知边界与后续

- 数量要素（因公出国团组数/人次、公务用车购置数、国内接待批次人次中的
  数量缺项）不在本规则范围：宜川检查表另两行"否"待本地披露标准裁决后
  另立义务（comparison.md 待裁决先例）。
- 说明章节整体缺失时不出确定性结论（RuleDeferred），结构性缺失由文档
  结构类义务与人工补核兜底。
- 预算车道三公表义务（OBL-SG-BUDGET-TABLE，BUD-104/CMM-001）不适用本
  规则：V33-SG-COMPLETION 是 final only。
