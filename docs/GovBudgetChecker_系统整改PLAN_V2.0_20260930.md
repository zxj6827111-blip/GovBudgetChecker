# GovBudgetChecker 系统整改 PLAN V2.0（2026-09-30）

> 性质：产品化整改总纲。以 `docs/GovBudgetChecker_系统整改PLAN_V1.0_20260921.md`
> （下称 **PLAN V1.0**）为底，叠加 WP4-I 基准阶段（2026-09-28/29 实测）暴露的
> 精度闭环缺口，合并为一份全量整改方案。
> **V2.0 不推翻 V1.0**：V1.0 已定的设计（Slot 模型字段、状态机、UI 效果图 06~13、
> API 清单、Gate A~F）直接引用、不重复；本文只做三件事——重排工作包、
> 把精度门禁落到基准工具可机检的形式、记录本次语料口径决策。
>
> 撰写纪律：本文所有数字均给出证据文件路径；证据不足的结论一律写
> 「待人工复核」或「未验证」，不编造。凡任务书前提与代码实况不符之处，
> 在 §1.2 显式订正（仓库一贯做法，见 B1 勾选单 §2.2 的先例）。

---

## 0. 核心判断

当前系统工程门禁扎实：CI 绿（`.github/workflows/ci.yml`），本机 `main`
（`c5fdaeae37…`）pytest 收集 **2310** 条（2026-09-30 实测
`pytest --collect-only -q`；早前快照口径为 2110，以实测为准），基准四件套
（`scripts/bench_register.py` / `run_benchmark.py` / `eval_benchmark.py` /
`bench_fpfn_sheet.py`）可用。

但存在三处「一直没好」的根因：

1. **只有 1 份金标，P/R 未测**——`corpus/` 登记了 7 份，仅
   `corpus/DOC-20260905-001/golden.json` 一份带真值（目录清点：其余 6 份目录
   只有 `sample.pdf`）。改规则没有度量尺子。
2. **精度闭环缺最后一环**——决算侧 findings 90→35、unresolved 68→29
   （`docs/B1_BENCH_DELTA_20260928.md` §二）证明了「能修」，但
   Precision/Recall/FPR 在全语料上仍不可测（同上 §七：「7 份中 6 份无
   golden.json，快照里的 P=R=1.0 只覆盖 golden 一份」）。
3. **产品对象收敛未收尾**——Material Slot 底座与台账 UI 已交付（见 §1.2
   对账表），但存量 450 个 uploaded 残留任务仍在 Slot 体系外
   （`docs/REMAINING_WORK_DISPOSITION_2026-08-28.md:79`），审核闭环还差
   WP3-C 收尾与双忽略存储统一（`docs/WP3A_REVIEW_LIFECYCLE_20260922.md` §230）。

因此整改顺序必须是：**先有尺子、再修靶子；先闭环、再扩面。**

## 〇之一、本次语料口径决策（不变项，先钉死）

- **本轮语料不含外采。** 以本机材料建立第一期基准：
  入集 **23 份 = 7 份已登记（L1 锚定观察面）+ 16 份配额内新登记（保留 L2 资格）**。
  执行实况：2026-09-30 本机 `uploads/` 为空（源 PDF 在原采集机），16 份中仅
  2 份 samples 补录可核验登记（DOC-B1-007/008），14 份挂起待 uploads 同步后
  按留档命令块补登（`docs/B1_CORPUS_CHECKLIST_20260928.md` §〇）；
  另有未入集本机候选 17 份作替补位（逐份清单见
  `docs/B1_L2_CANDIDATES_20260930.csv`，含 1 份 UI 校验样张与 1 份规资局版次 B）。
- 外采 **9~13 份**（真实扫描件 / 2023 决算 / 预算 2025 / 外省探针）作为独立
  工作包 **WP11** 挂起等待材料，材料到位前 C 档（扫描/泛化评测）不启动。
- 23 份同模板单位预算单列 `budget-unit` 分层、不与部门级混算
  （`docs/B1_CORPUS_CHECKLIST_20260928.md` §2.2 第 2 条）。

---

## 1. 与 V1.0 / WP4-I 的关系

### 1.1 来源文档

| 文档 | 在 V2.0 中的角色 |
|---|---|
| `docs/GovBudgetChecker_系统整改PLAN_V1.0_20260921.md` | 产品化总纲：信息架构（§2）、数据模型（§3）、状态机（§4）、UI（§5）、审核流（§6）、误报/漏报专项（§7/§8）、Gate A~F（§13）、红线（§18）、可用定义（§19）全部沿用 |
| `docs/WP4-I_BENCHMARK_PLAN.md` | 基准方法论：语料设计 §2、golden schema §3、指标 §4、S1~S5 执行阶段 §七 |
| `docs/B1_CORPUS_CHECKLIST_20260928.md` | S2 签字单：修订配额 §2.1、盲标口径 §2.2、登记纪律 §三 |
| `docs/B1_BENCH_DELTA_20260928.md` | 修复节点对照（90→35 / 68→29）与诚实边界 §七 |
| `docs/WP4-I_RULE_IMPROVEMENT_LIST_20260928.md` | 规则改进面（T1 取数缺口、T2 哨兵拒答等），WP4/WP5 的输入 |
| `docs/B1_L1_DRAFT_20260928.csv` | L1 底稿（38 份结构类事实，字段带出处与置信） |

### 1.2 WP 对账表（V2.0 ↔ V1.0 ↔ WP4-I，含交付现状）

> 「交付现状」一列以 docs/ 交付说明与迁移文件为准——任务书里有几处前提
> 已被 9 月下旬的实际交付超越，本表如实订正，避免 V2.0 重做已完成的事。

| V2.0 WP | 内容 | ↔ PLAN V1.0 | ↔ WP4-I | 交付现状（证据） |
|---|---|---|---|---|
| WP0 | 第二轮基线冻结（本文件 §4） | V1.0 WP0（2026-09-21 已做第一轮：`docs/MATERIAL_LEDGER_WP0_BASELINE_20260921.md`） | — | **本次执行** |
| WP-C1 | 语料登记与盲标工作包 | — | S2/S3 | 前半**本次执行**（16 份登记）；盲标待人工 |
| WP-C2 | 精度基线首测 | — | S4 | 待 L1 真值冻结后 |
| WP1 | Material Slot 数据模型 | V1.0 WP1 | — | **已交付**（PR #42，`docs/MATERIAL_LEDGER_WP1_DELIVERY_20260921.md`、`docs/MIGRATION_0019_MATERIAL_SLOTS.md`、`scripts/backfill_material_slots.py`）；残余=存量 450 任务映射 + Gate A 机检收口 |
| WP2 | 材料台账 UI | V1.0 WP2 | — | **已交付**（WP2-A/B/C，PR #43~#45，`docs/MATERIAL_LEDGER_WP2A/2B/2C_DELIVERY_*.md`）；残余=Gate 验收 |
| WP3 | 审核持久化闭环收尾 | V1.0 WP3 | — | WP3-A/B **已交付**（PR #46 与 migration `2026-09-22_0020` / `2026-09-23_0021`，`docs/WP3A/WP3B_*.md`；start/complete/reopen 端点在 `api/routes/reviews.py:226~257`）；**残余**=WP3-C 导出归档强约束、版本替换失效端到端验证、A3-S1 双忽略存储统一（本 WP3 范围内一并执行） |
| WP4 | 误报治理机制化 | V1.0 WP6（提前） | MR-1~MR-4 的后续 | 部分：MR-1~MR-4 兑现取数/舍入聚类/口径门槛（`docs/B1_BENCH_DELTA_20260928.md` §四）；formal finding gate v2、统一 Decimal 金额模块、列身份 fail-closed、root_cause_id 机制化**未做**（V1.0 §7 FP-01~06） |
| WP5 | 预算侧反例与基准补验 | V1.0 WP4 | — | **前提订正**：8 项 pending_checkers 已于 WP4-A~H 全部实现（`src/engine/check_obligations.py` 头注 v2~v9，`OBLIGATION_CATALOG_VERSION="obligations-v9"`；`check_coverage_baseline.py --json` 的 `unimplemented_checks=[]`）。WP5 剩余=为这 8 条新 checker 补真实反例入语料 + 基准对比 |
| WP6 | 结构化消费收敛 | V1.0 WP5 | — | **未动**：真正消费 `parsed_tables` 仅 1/52，9 条适配器登记 ≠ 9 条真实迁移（`docs/SYSTEMATIC_AUDIT_20260914.md:29`） |
| WP7 | Golden 扩容与 top30 裁决 | V1.0 WP7 | S3 延伸 | 未动（19 组人工确认问题中 14 组漏报未真值化：PLAN V1.0 §1.3、`docs/COVERAGE_LEDGER_REMEDIATION_20260916.md:235`） |
| WP8 | 质量管理/规则页 | V1.0 WP8 | — | 未动 |
| WP9 | 应收清单（第一阶段 CSV 导入） | V1.0 WP9 | — | 未动；官网采集移至 WP11 |
| WP10 | 性能/安全/部署/UAT | V1.0 WP10 | — | 部分基础件已有（`docs/BACKUP_RESTORE_DRILL_2026-08-27.md`、`docs/SECURITY_CLOSEOUT.md`、`docs/PERF_BASELINE.md`）；Gate A~F 正式验收未做 |
| WP11 | 外采与 OCR 评测面 | — | §2.1 外采分层 | **挂起**，等材料；产品文案维持「人工审核辅助工具」（PLAN V1.0 §19） |
| WP12 | 工程债 | — | — | 新增（本文件 §4.13） |

### 1.3 任务书前提的三处订正（写进方案，防止按过期前提排期）

1. **「8 项 pending_checkers 待实现」→「已全部实现、待基准补验」。**
   v2 清单时代的 8 项缺口（OBL-CROSS-SAN-GONG-ECON / TXT-FUND-DETAIL /
   NARRATIVE-INDICATOR-REPEAT / TREND-ZERO-BASE / TREND-COMPLETION-RATE /
   DISCLOSURE-PERCENT-UNIT / SG-COMPLETION / PERF-PHASE-AMOUNT）已由
   WP4-A~H（2026-09-24~26）逐条补齐 checker，目录升到 obligations-v9，
   静态缺口清零。剩余工作是把每条的**真实反例**入基准语料并跑对比——
   静态注册表绿 ≠ 在真实材料上验证过（预算文档实际调度 25 条规则
   = budget 17 + common 8，在 7 份基准材料上多条仍 insufficient_data，
   见 `docs/B1_BENCH_DELTA_20260928.md` §一 MR-4 行）。
2. **「WP1/WP2/WP3 从头做」→「收尾」。** 底座与 UI 已交付（对账表），
   V2.0 只排残余项。
3. **「本机 30 份（7+23）」→「逐份枚举为准」。** 候选盘点 CSV
   （`docs/B1_CANDIDATE_INVENTORY_20260928.csv`，39 份材料行）按 SHA 逐份
   核对后：6 份既有登记行 + 16 份本次登记 + 17 份未入集候选（含 UI 校验
   样张 1 份、规资局版次 B 1 份）。此前「30/32/23」等多个计数口径均以
   该 CSV 的逐份枚举为准（见 `docs/B1_L2_CANDIDATES_20260930.csv`）。

---

## 2. 现状基线速览（全部带证据路径）

| 维度 | 现状 | 证据 |
|---|---|---|
| 决算侧修复效果 | findings **90→35（−61%）**、unresolved **68→29（−57%）**（7 份口径） | `docs/B1_BENCH_DELTA_20260928.md` §一/§二 |
| 义务完成率（运行时） | **0.7344 → 0.8672**（7 份口径） | 同上 §三 |
| `insufficient_data` | 68 → 29 | 同上 §三 |
| 规则注册表规模 | final **57** / budget **17** / common **8**（合计 82；预算文档实际调度 25=17+8）。9-14 审计时为 52/16/6=74 | 实测 `ALL_RULES/ALL_BUDGET_RULES/ALL_COMMON_RULES`（2026-09-30）；`docs/SYSTEMATIC_AUDIT_20260914.md:11` |
| 金标 | 语料登记 7 份，**仅 1 份** golden.json（DOC-20260905-001）；P=R=1.0 只覆盖这一份 | `corpus/manifest.csv`；`corpus/DOC-20260905-001/` 目录清点；`docs/B1_BENCH_DELTA_20260928.md` §七 |
| 真实扫描件 | **0 份**（仅真实版式派生的扫描模拟夹具） | 同上 §七；`tests/fixtures/scan_sim_mixed_page_data.json` |
| 审核闭环 | WP3-A/B 已交付；WP3-C 导出归档强约束、版本替换失效端到端未验收；「完成复核」曾为纯前端行为（已修） | PLAN V1.0 §1.4；`docs/WP3A_REVIEW_LIFECYCLE_20260922.md`、`docs/WP3B_MANUAL_OBLIGATION_REVIEW_20260923.md` |
| 忽略存储 | **两套并存**：job 目录 `ignored_issues.json`（`api/runtime.py:110`）与 `issue_workflow_store`/workflow 记录 | `docs/WP3A_REVIEW_LIFECYCLE_20260922.md:230`（2026-09-22 记账「→ 独立 PR」，至今未执行） |
| 规则编号 | **三套并存**：引擎 `V33-*/BUD-*/CMM-*`、YAML 展示层 `T-20/C-001…`（`rules/v3_3.yaml` 30 条 + `rules/budget_v3_3_draft.yaml` 36 条）、样张期望旧码 `GJ-001/TB-003/ZB-002`（`samples/manifest.yaml:23`） | 上述文件；`docs/B1_CORPUS_CHECKLIST_20260928.md` §3.2 |
| 规则单体 | `src/engine/rules_v33.py` **10605 行** | `wc -l`（2026-09-30） |
| 结构化消费 | 真消费 `parsed_tables` **1/52**；9 条适配器登记 ≠ 9 条真实迁移 | `docs/SYSTEMATIC_AUDIT_20260914.md:29` |
| legacy AI | legacy 路径 AI **不执行**（`use_ai_assist=true` 名不副实；`run_rules()` 基类默认实现不调用模型） | `docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md:4,27,130-131` |
| 义务静态缺口 | 0（obligations-v9）；但预算侧 23 项义务静态台账 `not_executed=22 + ai_not_run=1` | `scripts/check_coverage_baseline.py --json`（快照 `docs/baselines/wp0v2_coverage_snapshot_20260930.json`） |
| 已知漏报存量 | 19 组人工确认问题中 **14 组漏报**未真值化 | PLAN V1.0:75,745；`docs/COVERAGE_LEDGER_REMEDIATION_20260916.md:235,285` |
| 残留数据 | 450 个 uploaded 残留任务待处置决策；`data/organizations.json.bak.20260308-125300` 被 git 跟踪 | `docs/REMAINING_WORK_DISPOSITION_2026-08-28.md:79,85` |
| 语料同质性 | 本机 24 份预算**全是 2026 年度**、23 份单位预算同一模板；预算 2025 年度 0 份 | `docs/B1_CORPUS_CHECKLIST_20260928.md` §2.1 |

---

## 3. 「可用」定义与发布门禁

### 3.1 沿用项

- **Gate A~F**：按 PLAN V1.0 §13 原文执行，不重述。
- **12 条用户可感知标准**：按 PLAN V1.0 §19 原文执行。未满足前，
  对外定位保持「**人工审核辅助工具**」。

### 3.2 精度门禁落到基准工具可机检的形式（V2.0 新增）

V1.0 的 Gate C/D 是文字门槛；V2.0 把它们绑定到 `run_benchmark` +
`eval_benchmark` 的输出字段上，达到「一条命令可验」：

| 门禁 | V1.0 出处 | 机检形式（V2.0） | 数据来源 |
|---|---|---|---|
| Gate C-1 正式 finding precision ≥ 98% | §13 Gate C | `eval_benchmark` 输出 `precision`（formal 桶，按 ruleset 判定语义），全语料 | `outputs/benchmark*/` + golden 真值 |
| Gate C-2 舍入误报 = 0 | §13 Gate C | formal finding 中根因为舍入差的条数 = 0（`rounding_hint` 只允许落在非 formal 桶） | 同上 |
| Gate C-3 证据可定位率 = 100% | §13 Gate C | 每条 formal finding 的 `evidence_text` 能在源 PDF 页文本中定位（页码 + 子串命中） | `run_benchmark` 产物 |
| Gate D-1 金标 recall ≥ 95% | §13 Gate D | `eval_benchmark` 输出 `recall`（golden labels 全集） | 同上 |
| Gate D-2 关键勾稽 recall = 100% | §13 Gate D | `obligation_group` ∈ 表间/表内/三公勾稽的 golden label 全部命中 | 同上 |
| Gate D-3 8 条义务 checker 反例全覆盖 | §13 Gate D（「8 个已知缺口全部有正反例」） | 每条 checker 至少 1 份真实反例材料入集并命中 | `corpus/manifest.csv` + eval 报告 |

第一期仍按 `docs/WP4-I_BENCHMARK_PLAN.md` §4.5「只观测、不拦截」执行；
连续两轮全语料达标后，才允许把上述门禁接进 CI/发布流程。

---

## 4. WBS 工作包

> 每个工作包：验收标准 / 依赖 / 分支名。分支命名沿用仓库惯例
>（`feat/<主题>-<日期>`）；每 WP 独立分支小 PR。

### WP0 第二轮基线冻结（P0，本次执行）

- 内容：main SHA（`c5fdaeae37…`，干净树）、golden SHA 双记录、
  `docs/baselines/` 快照清册、覆盖基线快照（obligations-v9）、
  7 份基准指标引用、语料口径决策落档。
- 产物：`docs/baselines/WP0V2_BASELINE_FREEZE_20260930.md` +
  `docs/baselines/wp0v2_coverage_snapshot_20260930.json`。
- 验收：此后任何规则修改都能与快照对比「改前/改后」；快照内
  `source.commit/dirty` 与 git 实况一致。
- 依赖：无。分支：本分支。

### WP-C1 语料登记与盲标（P0）

- 内容：①本机配额内 16 份登记入 `corpus/manifest.csv`（逐份核对 SHA、
  `--depth L2`、PDF 不入 git）；②L1 全量标注（底稿
  `docs/B1_L1_DRAFT_20260928.csv`，规范见
  `docs/B1_ANNOTATION_SPEC_L1L2_20260930.md`）；③L2 候选清单
  （`docs/B1_L2_CANDIDATES_20260930.csv`）；④S2 签字单更新
  （外采行 deferred→WP11）。
- **执行实况（2026-09-30）**：本机 `uploads/` 目录为空（源 PDF 在原采集机，
  未同步），16 份中仅 samples 补录 2 份在本机——按 fail-closed 纪律只登记
  SHA 核验通过的 2 份（`DOC-B1-007/008`），其余 14 份**挂起**并留好
  逐份 SHA + 补登命令块（`docs/B1_CORPUS_CHECKLIST_20260928.md` §〇），
  uploads/ 同步后原样执行即完成登记。
- **运行纪律（红线）**：L2 资格材料在 `truth_frozen_at` 写入前，
  **禁止**对它们跑 `run_benchmark`；L2 候选 findings 只允许来自
  **旧版**输出，禁当前代码输出（`docs/B1_CORPUS_CHECKLIST_20260928.md` §2.2.1）。
- 验收：manifest 份数与 `docs/B1_L2_CANDIDATES_20260930.csv` 状态列一致；
  `tests/test_corpus_ingest_guards.py` 全绿；运行纪律留痕（登记记录中
  不含任何 benchmark 产物）。
- 依赖：无（盲标人力在用户侧）。

### WP-C2 精度基线首测（P0）

- 内容：L1/L2 真值冻结后跑 `run_benchmark` + `eval_benchmark`，产出
  第一份全语料 P/R/FPR/FP 密度报告——此后所有规则修改的对照原点。
- 验收：报告含 §3.2 全部机检指标的当前值（无论多难看）；分 `final-main`
  / `budget-main` / `budget-unit` 三层单列。
- 依赖：WP-C1 盲标完成并冻结。

### WP1 Material Slot 收口（P0，=V1.0 WP1 残余）

- 内容：存量 450 个 uploaded 残留任务处置决策（删除/归档/映射入 Slot，
  `docs/REMAINING_WORK_DISPOSITION_2026-08-28.md:79`）+ Gate A 机检
  （同名部门/本部不串线、年度/文种不混、多版本不覆盖）。
- 验收：Gate A 五条逐条有机检用例或人工核验记录；残留任务处置有用户签字。
- 依赖：用户对 450 任务处置的决策。分支：`feat/wp1-slot-closeout-<日期>`。

### WP2 材料台账 UI 验收（P0，=V1.0 WP2 残余）

- 内容：五级页面与效果图 06~13 逐屏对齐复核；Gate A 数据口径在 UI 层
  抽验（不同部门/年度/文种互不串线）。
- 验收：对齐复核记录 + 截图归档（沿用 `docs/wp2*-screenshots/` 惯例）。
- 依赖：WP1。分支：`feat/wp2-ui-acceptance-<日期>`。

### WP3 审核持久化闭环收尾（P0，=V1.0 WP3 残余）

- 内容：①WP3-C 导出归档强约束（`ArchivePage`/`create_package`，
  WP3A 交付说明「本次没有做」清单）；②版本替换→旧复核失效的端到端
  验证（`review_sessions.invalidated` 机制已在 migration 0020，缺端到端
  用例）；③**A3-S1 双忽略存储统一**——`ignored_issues.json` 与
  `issue_workflow_store` 并存（`docs/WP3A_REVIEW_LIFECYCLE_20260922.md:230`），
  2026-09-22 记账至今未执行，本次纳入 WP3 范围一并做掉。
- 验收：Gate E 的 E2E（上传→处理→复核→完成→导出→重开保持完成）
  在刷新/重启后仍通过；忽略存储只剩一套权威实现，旧路径有迁移与回归。
- 依赖：无（可与 WP-C 并行）。分支：`feat/wp3-review-closeout-<日期>`。

### WP4 误报治理机制化（P0/P1，=V1.0 WP6 提前）

- 内容：在 MR-1~MR-4（取数直连、舍入聚类、口径门槛）之上机制化：
  ①formal finding gate v2——数值类正式问题按 V1.0 §7 FP-01 的证据维度
  清单逐项校验，缺一即降级；②统一 Decimal 金额模块（消灭 float 口径
  分叉）；③列身份 fail-closed（FP-03）；④根因归并 `root_cause_id`
  （FP-06）机制化，支撑 Gate C-2/C-3 机检。
- 验收：每项有正反例 + 真实样张对比；不降低其他规则 recall
  （对比基线=WP-C2 报告）。
- 依赖：WP-C2（否则没有对照原点）。分支：`feat/wp4-fp-gate-<日期>`。

### WP5 预算侧反例与基准补验（P1）

- 内容（前提订正后）：8 条 WP4-A~H 新 checker 逐条「真实反例入语料 →
  在基准上跑通 → 与 WP-C2 基线对比」。顺序按有真实反例者优先：
  OBL-CROSS-SAN-GONG-ECON / TXT-FUND-DETAIL / NARRATIVE-INDICATOR-REPEAT /
  TREND-ZERO-BASE 先（反例存量见 PLAN V1.0 §8 FN-01）。
- **红线**：禁止删 pending/义务凑绿；禁止只用合成用例就宣布达标；
  禁止把 budget-unit 与 budget-main 混算。
- 验收：8 条各自有「反例材料 SHA + 基准命中证据 + 对比前后指标」。
- 依赖：WP-C2；部分反例需从 WP11 外采（预算 2025 年度本机为 0 份）。
  分支：`feat/wp5-budget-replay-<日期>`。

### WP6 结构化消费收敛（P1，=V1.0 WP5）

- 内容：V33-117/120 真迁移；9 条登记适配器逐条「真迁移 or 摘出统计」
  （当前真消费 1/52，`docs/SYSTEMATIC_AUDIT_20260914.md:29`）；
  legacy fallback 定终止计划（shadow 差异 → 主路径切换 → 下线）。
- 验收：V1.0 WP5 关键验收「结构化与 legacy 对比通过」；登记数=真消费数。
- 依赖：WP-C2 基线。分支：`feat/wp6-structured-consume-<日期>`。

### WP7 Golden 扩容与 top30 裁决（P1，=V1.0 WP7）

- 内容：19 组人工确认问题（14 组漏报）真值化；top30 人工裁决分桶
  （真问题/误报/口径差/无法判断，按 `docs/WP4-I_BENCHMARK_PLAN.md` §3.4）。
- 验收：真值集进 corpus 并冻结；裁决记录可追溯。
- 依赖：WP-C1 标注规范与工具就绪。分支：`feat/wp7-golden-expand-<日期>`。

### WP8 质量管理/规则页（P1，=V1.0 WP8）

- 内容：obligation 覆盖、Golden 指标、未完成面可视化。
- 验收：业务用户能看到真实覆盖（V1.0 WP8 验收原文）。
- 依赖：WP-C2 指标产出。分支：`feat/wp8-quality-ui-<日期>`。

### WP9 应收清单（P1/P2，=V1.0 WP9 第一阶段）

- 内容：CSV 导入建立应收台账（官网辅助采集移至 WP11）。
- 验收：可真实统计逾期未上传。
- 依赖：WP1/WP2。分支：`feat/wp9-receivable-csv-<日期>`。

### WP10 性能/安全/部署/UAT（P0 收尾，=V1.0 WP10）

- 内容：在既有基础件（备份演练、安全 closeout、perf 基线）之上，
  按 Gate A~F 逐项正式验收 + UAT。
- 验收：Gate A~F 全部通过并有记录。
- 依赖：WP1~WP9 的残余项关闭。分支：`feat/wp10-gate-acceptance-<日期>`。

### WP11 外采与 OCR 评测面（挂起，等材料）

- 内容：真实扫描件 ≤3、2023 决算 3~4、预算 2025 ≈6、外省探针 ≈9
  （配额依据 `docs/B1_CORPUS_CHECKLIST_20260928.md` §2.1）。
  材料到位前 C 档不启动；产品文案维持「疑似扫描页转人工」的边界
  （行为实测见 `docs/B1_BENCH_DELTA_20260928.md` §七）；
  AI 五态状态机与真接入随本 WP 评估（现状：legacy 路径 AI 不执行，
  `docs/SYSTEM_ISSUES_HANDOFF_2026-09-05.md` §3.5）。
- 验收：外采材料逐份登记（source_url 必填）+ 分层报告单列。
- 依赖：用户提供材料。分支：`feat/wp11-probe-corpus-<日期>`。

### WP12 工程债（P2）

- 内容：①规则编号双轨统一——以引擎 `V33/BUD/CMM` 为准，YAML
  （`rules/v3_3.yaml`、`rules/budget_v3_3_draft.yaml`）降为展示映射，
  `samples/manifest.yaml` 旧码做一次「旧→现」映射
  （`docs/B1_CORPUS_CHECKLIST_20260928.md` §3.2 已指出）；②
  `src/engine/rules_v33.py`（10605 行）按表族拆分；③450 个 uploaded
  残留任务处置（与 WP1 联动）；④`data/organizations.json.bak.20260308-125300`
  处置（`docs/REMAINING_WORK_DISPOSITION_2026-08-28.md:85`）。
- 验收：编号映射表唯一权威；拆分后基线指标不劣化。
- 依赖：无强依赖，逐项独立 PR。分支：`chore/wp12-<主题>-<日期>`。

### 建议推进顺序

```
本轮（本分支）：WP0 + WP-C1 前半 + 盲标工作包
下一批（可并行）：WP-C1 后半（人工盲标）→ WP-C2
                  WP3 收尾（独立线，可立即开工）
然后：WP4 / WP5 / WP6（依赖 WP-C2 基线）
再后：WP7 / WP8 / WP9 → WP10 收口
全程挂起：WP11（等材料）；择机：WP12
```

---

## 5. 红线（禁止的「修绿」方式）

PLAN V1.0 §18 十条全部沿用。V2.0 增补：

11. **禁止在 L2 材料 `truth_frozen_at` 写入前对它跑 `run_benchmark`**
    （跑了即丧失盲标资格——已登记 7 份即因此只能作 L1 锚定观察面）。
12. **禁止把「未测」写成「达标」**——任何精度表述必须绑定
    `eval_benchmark` 产物与引擎指纹；没有对照原点的「提升」不成立。
13. **禁止删义务/删 pending、或把 `budget-unit` 并入 `budget-main` 混算**
    来让分层数据变好看。
14. **禁止把旧版输出当真值**——旧版输出只能作 L2 预标注候选，
    真值以人工标注 + `ANNOTATIONS.md` 仲裁为准。

---

## 6. 分支/PR 策略

- 每 WP 独立分支小 PR；普通 merge（保留评审历史，沿用 WP2 系列惯例，
  `docs/MATERIAL_LEDGER_WP2B_DELIVERY_20260922.md`）。
- 每 PR 必须包含：正例、反例、真实样张对比、以及「不降低其他规则
  recall」的证明（对照 = WP-C2 基线或最近一轮全语料报告）。
- 文档/基线类 PR（如本分支）至少带：守护测试全绿 + 快照/登记一致性自查。

---

## 7. 风险与依赖

| 风险 | 说明 | 缓解 |
|---|---|---|
| 盲标人力瓶颈 | L2 深标 2~3 h/份，20 份问题富集材料在用户侧 | L1 底稿已出（省 0.5~1 h/份）；先冻结 L1 跑 WP-C2 的 L1 层指标，L2 分批补 |
| 同模板外推受限 | 23 份单位预算同一模板，precision 外推性弱 | `budget-unit` 单列分层报告，不混算（§〇之一） |
| 扫描件评测盲区 | 真实扫描件 0 份，OCR 面未测 | WP11 前对外定位维持「人工审核辅助工具」；派生夹具已证明闸门行为（B1_BENCH_DELTA §七） |
| 预算 2025 年度空白 | 本机 24 份预算全是 2026 | WP5 的跨年类反例依赖 WP11 外采，先做有反例的 4 条 |
| 规资局版次未签字 | 同名材料两个版次（`f2546b57…` / `631ac442…`） | 本次默认登记 `631ac442`（putuo_final_samples 版，文本层更全：16203 vs 13655 字符）；另一版留候选清单，签字可换（登记按 SHA 幂等，删行重登即可） |
| 双忽略存储迁移回归面 | A3-S1 触及工作台可见集合 | WP3 独立 PR + 全量 workflow 回归 |

---

## 8. 本次（本分支）已执行与明确不做

**已执行**：V2.0 文档（本文件）；WP0 冻结产物；语料登记执行
（本机可核验的 2 份已登记 DOC-B1-007/008，14 份因 uploads/ 未同步本机而
**挂起**、逐份 SHA 与补登命令块已留档，见 §4 WP-C1 执行实况）；
S2 签字单更新（外采行 deferred→WP11）；标注规范 + L2 候选清单；
守护测试回归。

**明确不做**：不外采、不接 OCR、不动 AI 真接入、不做 WP1 以后的
数据库 migration 与前端改造——均在 §4 排期，逐 PR 推进。
