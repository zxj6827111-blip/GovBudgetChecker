# WP0-V2 基线冻结记录（2026-09-30）

> 性质：PLAN V2.0（`docs/GovBudgetChecker_系统整改PLAN_V2.0_20260930.md` §4 WP0）
> 的冻结产物。第一轮冻结见 `docs/MATERIAL_LEDGER_WP0_BASELINE_20260921.md`
>（基线 `4a1cabf2`，2026-09-21）。本轮冻结的是**基准/精度语境**的基线：
> 此后任何规则修改，都应能与本记录引用的快照对比「改前/改后」。

## 一、代码基线

| 项 | 值 | 取证方式 |
|---|---|---|
| main HEAD（冻结点） | `c5fdaeae373266e6f1f4318a53e0d04771ebb976`（短 `c5fdaea`，PR #61 合并后） | `git rev-parse HEAD` |
| 工作树 | 冻结时仅含本分支新增文档（`docs/` 下 2 个文件），**引擎 `.py` 零改动**（`git status --short` 佐证） | 同左 |
| 引擎指纹 `rules_sha256` | `14271febb54801f8a4adbe0ac794931eab8a81fc927cc036c19272979924f466` | `scripts/bench_register.py::engine_fingerprint()`（哈希 4 个引擎源文件字节：rules_v33.py / budget_rules.py / common_rules.py / pipeline.py） |
| 规则清单指纹 `rule_inventory_sha256` | `1391641aa0af7f3a8f8c51cb4fb3182edcae008896787179547ae443236a8d71`（82 条：final 57 / budget 17 / common 8） | 同上 |
| 测试规模 | pytest 收集 **2310** 条（`pytest --collect-only -q`，2026-09-30） | 本机实测 |

> 口径说明：指纹里 `git_dirty=True`（分支新增文档所致）；但 `rules_sha256`
> 只哈希引擎源码，与文档无关，以它作引擎身份锚点。

## 二、金标基线（唯一一份）

| 项 | 值 |
|---|---|
| 金标文档 | `corpus/DOC-20260905-001/golden.json`（生态环境局 2025 年度部门决算） |
| golden.json 文件 SHA-256 | `05a4bf29944818fd307faf4b0e533903e7f5a77746843bf194ae15e90489d5a8` |
| golden.json **声明**的源 PDF SHA-256 | `113b98bb5df18c264f9c589a1034d3bfc27ed65562b4bbbe72bfd33420f912c7` |
| 一致性 | 与 `corpus/manifest.csv` 第 2 行登记 SHA **逐字符一致**（fail-closed 绑定复核通过） |
| 冻结状态 | `truth_frozen_at` 已写入（golden.json 字段） |

## 三、指标快照清册（docs/baselines/，冻结时点共 15 份）

既有 14 份（本轮未改动任何一个字节，仅登记在册）：

| 快照 | 内容 |
|---|---|
| `bench1_metrics_20260928_before_repair_7docs.json` | 修复前：findings 90 / unresolved 68 |
| `bench1_metrics_20260928_mr123_7docs.json` | MR-1/2/3 后：37 / 29 |
| `bench1_metrics_20260928_after_repair_7docs.json` | +MR-4 后：**35 / 29**（义务完成率 0.8672）——当前对照原点 |
| `bench1_metrics_20260928_merge_preview_7docs.json` | 合并预览 |
| `bench1_metrics_20260929_budget_intake_before_7docs.json` | 预算 intake 改造前对照 |
| `wp0_coverage_baseline.json` | 第一轮覆盖基线（obligations-v2 时代，含 8 项 `unimplemented_checks` 原始记录） |
| `wp4a~wp4h_coverage_current_20260924~26.json`（8 份） | WP4-A~H 逐包覆盖快照 |

本轮新增 1 份：

| 快照 | 内容 |
|---|---|
| `wp0v2_coverage_snapshot_20260930.json` | `check_coverage_baseline.py --json` 当前全量输出（obligations-v9：`unimplemented_checks=[]`；预算侧静态台账 not_executed=22 + ai_not_run=1；source.commit=c5fdaea、dirty=false 时采集） |

> 指标解读一律以 `docs/B1_BENCH_DELTA_20260928.md` 为准；特别注意其
> §七「当前不可得出的结论」——**全语料 P/R 仍不可测**（6/7 份无 golden）。

## 四、pending_checkers 台账（口径订正记录）

- **v2 时代 8 项**（原始记录：`docs/baselines/wp0_coverage_baseline.json` 的
  `unimplemented_checks`；清单：PLAN V1.0 §8 FN-01）：
  OBL-CROSS-SAN-GONG-ECON / OBL-TXT-FUND-DETAIL / OBL-NARRATIVE-INDICATOR-REPEAT /
  OBL-TREND-ZERO-BASE / OBL-TREND-COMPLETION-RATE / OBL-DISCLOSURE-PERCENT-UNIT /
  OBL-SG-COMPLETION / OBL-PERF-PHASE-AMOUNT。
- **当前状态**：已由 WP4-A~H（2026-09-24~26）全部实现 checker，目录升至
  `obligations-v9`（`src/engine/check_obligations.py` 头注 v2~v9 逐条对应；
  `wp0v2_coverage_snapshot_20260930.json` 的 `unimplemented_checks=[]`）。
- **遗留面**：8 条新 checker 的**真实反例基准补验**未做 → PLAN V2.0 §4 WP5。

## 五、语料口径决策（本轮钉死，改动须重开签字）

1. 本轮语料**不含外采**；外采 9~13 份挂 WP11（PLAN V2.0 §4 WP11）。
2. 本机入集 23 份 = 7 份已登记（L1 锚定观察面）+ 16 份配额内新登记。
   **执行实况（2026-09-30）**：本机 `uploads/` 目录为空（源 PDF 在原采集机，
   未同步到本机），14 份配额内材料无法通过 fail-closed 的 SHA 核验——
   按纪律**只登记了本机实际存在且 SHA 核验通过的 2 份** samples 补录
   （`DOC-B1-007` / `DOC-B1-008`）。当前 manifest 共 9 行；
   14 份挂起材料的逐份 SHA 与补登命令块见
   `docs/B1_CORPUS_CHECKLIST_20260928.md` §〇，同步后原样执行即可
   （编号自动续 DOC-B1-009~022）。
3. 23 份同模板单位预算单列 `budget-unit` 分层，不与 `budget-main` 混算。
4. L2 纪律：`truth_frozen_at` 写入前禁止对 L2 资格材料跑 `run_benchmark`；
   L2 候选 findings 只允许来自旧版输出。**本轮登记运行零次 benchmark 执行。**
5. 规资局 2024 决算默认取 `631ac4422779…`（putuo_final_samples 版）；
   版次 B `f2546b577801…` 留候选清单，签字可换。

全量 40 份材料的逐份状态（已登记 9 / 挂起 14 / 未入集候选 17 / golden 1）
见 `docs/B1_L2_CANDIDATES_20260930.csv`。
