# B1 修复前后基准对比（7 份既有资产，2026-09-28）

> 性质：WP4-I **S4「修复节点评测对比」的可执行形态**，在 B1 语料收灌完成前
> 先对 7 份已有资产跑一次「修复前 / 修复后」全链路对照，用于验证车道1 的
> 净效果并提前暴露工具面缺陷。**它不是 S4 的完成**：Precision/Recall 需要
> S3 盲标（金标先于系统输出），当前 7 份中只有 1 份带 golden.json。
> 本文档全部数字来自 `outputs/benchmark*/` 重放产物与 `docs/baselines/` 快照。

## 一、方法（可复现）

两条车道在同一份语料、同一套工具下各跑一次，唯一变量是引擎版本：

| 项 | 修复前 | 修复后 |
|---|---|---|
| 引擎 | `main`（经 `feat/bench-wp4i`，无 MR-1~MR-4） | `fix/engine-parse-20260927` HEAD `4d6662c` |
| 工具 | `bench_register/run_benchmark/eval_benchmark`（PR #60 版） | 同左（临时拷入 worktree，未提交） |
| 节点 | `main` → `9949dea`(MR-1/2/3) → `4d6662c`(+MR-4)，共三跑 | 同左 |
| 语料 | `corpus/manifest.csv` 登记 7 份（SHA 与源 PDF 绑定） | 同左 |

```bash
# 修复前：车道2 分支自身即 main 引擎
python scripts/run_benchmark.py
python scripts/eval_benchmark.py --replay-dir outputs/benchmark/<ts> \
    --output docs/baselines/bench1_metrics_20260928_before_repair_7docs.json

# 修复后：worktree 取车道1 引擎，工具脚本临时拷入（不提交、不改车道1 分支）
git worktree add .tmp/wt-lane1 fix/engine-parse-20260927
cp scripts/{bench_register,run_benchmark,eval_benchmark}.py .tmp/wt-lane1/scripts/
python .tmp/wt-lane1/scripts/run_benchmark.py \
    --corpus <repo>/corpus --out-root <repo>/outputs/benchmark-after
```

两跑均 `7 docs, 0 failures`，`parse_error = execution_error = 0`（确定性纯规则，
禁 AI，无网络）。

> **节点粒度**：方案 S4 期望「修复前 / 每 MR 后 / 全部后」。车道1 的历史是
> `9949dea`（MR-1/2/3 合为一个提交）→ `f14616d`（MR-5 CI job）→ `4d6662c`（MR-4），
> 因此**能切的节点是三个**：`main` → `9949dea`（MR-1/2/3）→ `4d6662c`（+MR-4）。
> MR-1/2/3 之间的中间态无法切出（未重写已 push 的历史）。

### 三个节点逐份对照

| 材料 | main findings/unresolved | MR-1/2/3 | +MR-4（全量） |
|---|---|---|---|
| DOC-20260905-001 生态环境局 | 7 / 13 | 7 / 4 | 7 / 4 |
| DOC-B1-001 宜川路街道 | 34 / 12 | 13 / 1 | 13 / 1 |
| DOC-B1-002 石泉路街道 | 16 / 14 | 9 / 2 | 9 / 2 |
| DOC-B1-003 文化和旅游局 | 27 / 14 | 2 / 7 | 2 / 7 |
| DOC-B1-004 建管委 2026 预算 | 4 / 5 | **4** / 5 | **2** / 5 |
| DOC-B1-005 城管执法局预算 | 1 / 4 | 1 / 4 | 1 / 4 |
| DOC-B1-006 文化和旅游局预算 | 1 / 6 | 1 / 6 | 1 / 6 |
| **合计** | **90 / 68** | **37 / 29** | **35 / 29** |

**归因结论**：MR-1/2/3 承担了几乎全部效果（90→37 findings、68→29 unresolved）；
**MR-4 的独立贡献只有一条**——建管委的 `BUD-105 ×2` 误报消失（4→2），
且 unresolved 不变（5）：它**止住了误报，但没有让规则跑通**——BUD-105 在 3/3 份
预算上仍是 `insufficient_data`（T1/T4 取数缺口），这正是
`docs/WP4-I_RULE_IMPROVEMENT_LIST_20260928.md` §二 T1 的第一条。

对应快照：`docs/baselines/bench1_metrics_20260928_{before_repair,mr123,after_repair,merge_preview}_7docs.json`。
每份快照都绑定**引擎指纹**（§6.7：源码哈希 + 规则清单哈希 + git head/dirty）。

> **指纹口径说明（2026-09-28 实测）**：指纹哈希的是引擎源码**字节**，所以**纯文档改动
> 也会翻转**——车道1 加了一处 docstring 订正后 head 由 `4d6662c` 变为 `2b05aeb`，
> 指纹 `7cd4c0d6…` → `8b56499d…`，但**重跑 7 份材料的 findings/unresolved 逐份完全
> 一致**（已实测比对）。这是刻意的保守选择：宁可对文档改动过敏，也不放过任何一次
> 真实行为变化。快照 `after_repair` 已按 `2b05aeb` 重生成。

## 二、逐份结果

| 材料 | findings 前→后 | unresolved 前→后 | 说明 |
|---|---|---|---|
| DOC-20260905-001 生态环境局 决算（golden） | 7 → 7 | 13 → 4 | 金标 TP/FP/FN 不变 |
| DOC-B1-001 宜川路街道 决算 | **34 → 13** | 12 → 1 | 与验收行「宜川 ≤12」的差异见 §五 |
| DOC-B1-002 石泉路街道 决算 | 16 → 9 | 14 → 2 | |
| DOC-B1-003 文化和旅游局 决算 | 27 → 2 | 14 → 7 | 级联误报消失 |
| DOC-B1-004 建管委 2026 预算 | 4 → 2 | 5 → 5 | BUD-105 口径门槛生效 |
| DOC-B1-005 城管执法局 2026 预算 | 1 → 1 | 4 → 4 | |
| DOC-B1-006 文化和旅游局 2026 预算 | 1 → 1 | 6 → 6 | |
| **合计** | **90 → 35（−61%）** | **68 → 29（−57%）** | |

没有任何一份材料在修复后变差：findings 与 unresolved 逐份单调不增，
`parse_error` 前后均为 0。

文旅局那份最能说明噪声来源：修复前 27 条里 21 条是 V33-120 逐条输出，
其中 13 条是取数错位造出的假「层级校验失败」（`父级 191.93 != 子级之和
24535.67` 这类跨列错配），另有 3 条 1700.57 级联（V33-005×2、V33-202）。
修复后只剩 CMM-002（多余右引号）与 CMM-007（零基数同比），两条都是原文
可核证的真发现。

### 二·一、确定性实测（§6.7：同输入同输出，支持回归对比）

跨节点对照与后续回归比较都建立在确定性之上，本轮实测：同一引擎连跑两次完整 benchmark
（两个独立进程，各自随机哈希种子），7 份材料的 findings（**含顺序**）、六态摘要、义务账本、
文种、sha256、引擎指纹**逐项一致**；两次运行唯一不同的是时间戳目录名与 `generated_at`
（设计如此）。守护测试 `tests/test_bench_determinism.py` 用 `PYTHONHASHSEED=0/1`
两个子进程再锁一遍——**变异验证**：把规则顺序改成依赖字符串集合（哈希种子敏感）后，
该测试立即转红。

## 三、六态与运行面（7 份合计）

| 指标 | 修复前 | 修复后 | 变化 |
|---|---|---|---|
| pass | 233 | 274 | +41 |
| fail | 27 | 25 | −2 |
| not_applicable | 4 | 4 | 0 |
| insufficient_data | **68** | **29** | **−39** |
| parse_error / execution_error | 0 / 0 | 0 / 0 | 0 |
| 义务完成率（运行时） | 0.7344 | **0.8672** | +0.1328 |
| 文种识别准确率 | 1.0（7/7） | 1.0（7/7） | 0 |
| 零触发规则（全量重放口径） | final 42 / budget 14 / common 4 | final 44 / budget 15 / common 4 | 见 §四 |

`insufficient_data` 减少 39 项是本次改造的核心兑现：方案第二条需求「决算
核心勾稽真正执行（修漏报）」在数量上表现为取数不足转为真实判定
（宜川单份：executed 53→64，insufficient 12→1）。

## 四、规则面变化（需要解释的三处）

1. **V33-120 舍入噪声：55 → 4**。同（表，差值，页域）合并为一条：宜川由
   23 条逐条提示合并为 1 条 `info`（message 带「共23处」，`evidence_text`
   保留全部 23 组「表名/科目/两值」明细，实测 23 组齐全）。方案要求的
   `count` 与明细以「message 计数 + evidence_text 明细」落地，未新增
   finding schema 字段——`_serialize_finding` 的键集被评测器与冻结评测
   语义共用，新增键需另行评估。
2. **V33-005 退出触发面（2 → 0）**。MR-1 给它加了「按列 col_max + 结构
   矛盾哨兵」，命中矛盾时转 `RuleDeferred`（partial_issues），不再发 finding。
   因此它出现在**零触发清单**里——但语义是「拒绝作答」而非「漏报」。
   ⚠️ 解读零触发清单时必须区分「规则太窄/不适用」与「被哨兵转 Deferred」
   两类，否则会把 fail-closed 的改进读成覆盖退化。
3. **修复后新增的触发是真发现**：`V33-227`（科目名不一致，宜川 302 家族
   「文化体育」vs「文化旅游体育与传媒支出」）由 0 → 2——修复前该规则因
   取数错位根本不触发，修复后才可判定；`V33-202` 反向由 1 → 0（文旅局
   `T4=24535.67 vs T5=1700.57` 的口径错配消失，见下）。

## 五、与方案验收表的关系

| 验收行 | 目标 | 本次独立复现 | 判定 |
|---|---|---|---|
| 4 份决算 unresolved | 53 → ≤15 | 53 → **14** | 达标（独立工具链复现） |
| 宜川输出总数 | 34 → ≤12 | 34 → **13** | 差 1 条，待用户裁决（PR #59 评论 issuecomment-5858350680） |
| 文旅局级联误报 | 4 → 0 | 27 → 2：4 条 error 中 3 条属 1700.57 级联（V33-005×2 + V33-202）全部消失，余 1 条 CMM-007 是另一规则的真阳性 | 级联项达标（余项为真发现，非级联） |
| 金标不劣化 | TP=3/3, hint=3/3, FP=0 | tp=3 fp=0 fn=0，P=R=1.0 | 达标 |

本次复现的价值：宜川 **13** 这一关键数由**与验收时不同的工具链**（新写的
`bench_register/run_benchmark` 走 PDF→页面表→引擎）独立得到，排除了原
`replay_engine_repair.py` 单点脚本算错的可能。

### 五·一、MR-1 验收里「V33-101~105 清零」的逐规则取证

MR-1 验收原文：「4 份决算重放后：V33-101/102/103/104/105 维 unresolved 全部清零
（16/16 → 0）；V33-107/108、CMM-001 澄清明确状态」。逐规则状态（修复后引擎）：

| 规则 | 生态环境局 | 宜川 | 石泉 | 文旅 | 未决 |
|---|---|---|---|---|---|
| V33-101 | pass | pass | pass | pass | **0/4** |
| V33-102 | pass | pass | pass | pass | **0/4** |
| V33-103 | pass | pass | pass | pass | **0/4** |
| V33-104 | pass | pass | pass | pass | **0/4** |
| V33-105 | pass | pass | pass | pass | **0/4** |
| V33-106 | pass | pass | pass | insufficient_data | 1/4 |
| V33-107 | pass | pass | pass | pass | 0/4 |
| V33-108 | pass | pass | pass | pass | 0/4 |
| CMM-001 | insufficient_data | insufficient_data | insufficient_data | insufficient_data | 4/4 |

结论：**V33-101~105 合计未决 0/16，与验收目标「16/16 → 0」精确对应**；
V33-107/108 全 pass；CMM-001 四份全为 `insufficient_data`，原因明确
（「未提取到三公经费情况说明数值」）——属「澄清明确状态」而非静默漏报，
并已列入 `docs/WP4-I_RULE_IMPROVEMENT_LIST_20260928.md` §二 T1 改进面。
V33-106 的 1/4 未决在文旅局，原因是材料 T5 合计与分项不同源（结构矛盾哨兵按
fail-closed 拒答，见 §四 与改进清单 T2）。

## 六、本次对照暴露的工具面缺陷（已修）

1. **`.gitignore` 漏通配符**：`corpus/DOC-B1-/*` 缺目录段 `*`，对
   `corpus/DOC-B1-001/` 永不匹配，导致登记的 `sample.pdf` 全部可被
   `git add` 误提交（与「PDF 永不入 git」纪律冲突）。已改为
   `corpus/DOC-B1-*/*`，回归锁 `tests/test_corpus_ingest_guards.py`
   （变异验证：改回旧写法 6 条转红）。
2. **零触发清单口径错误**：S1 首版用 `rule_level`（只统计已标注材料）
   计算零触发，7 份只标注 1 份时把另外 6 份的触发全记成零触发
   （final 虚报 52 条）。已改为**全量重放口径**并新增
   `rule_trigger_counts` / `zero_trigger_scope` 字段，回归锁
   `test_zero_trigger_scope_covers_unannotated_replay`。

## 七、当前不可得出的结论（诚实边界）

- **Precision / Recall / FPR 尚未可测**：7 份中 6 份无 golden.json，
  快照里的 `P=R=1.0` 只覆盖 golden 一份，不代表全语料精度。S4 精度行
  （Precision ≥0.75、Recall ≥0.65）必须等 S3 盲标完成。
- **盲标口径（已按方案 §3.2.2 订正，两版都记下来免得再踩）**：初稿写「uploads
  材料有旧输出即失去盲标资格」，实测一轮后又推翻——方案 §3.2.2 明确允许把
  **旧版**输出当预标注候选清单（禁的只是**当前代码输出**）。真正的分界线是
  「当前引擎有没有在它上面跑过并落盘」：**已登记 7 份**跑过且逐条输出已公开
  （本文与证据文档里），它们 **L2 不可盲**；**其余 23 份本机候选**当前引擎从未
  跑过，**保留 L2 资格**（用旧输出做候选清单）。运行纪律：打算用于 L2 的材料，
  登记后不许跑 `run_benchmark`，等 `truth_frozen_at` 写入后再跑。
  详见 `docs/B1_CANDIDATE_INVENTORY_20260928.md` §二·五。
- **扫描维度的证据已在 2026-09-28 补强**：语料里仍**没有外部采集的真实扫描件**，
  但用**真实版式派生**的扫描件（把样张前 8 页保留文本、其余页栅格化）做了端到端
  验证，并冻结为夹具 `tests/fixtures/scan_sim_mixed_page_data.json`：

  | 形态 | 文种识别 | error 级 | 闸门后 |
  |---|---|---|---|
  | **部分扫描**（有文本、表页被拍） | 成功（final） | **9 条** | **0 条** + 1 条转人工 |
  | **整份扫描**（无文本层） | 失败（kind=unknown） | 0 条（只跑 8 条通用规则） | 不适用 |

  两个结论：① 方案 MR-3 的「模拟扫描件约 10 条 error」在**部分扫描**形态下复现
  （实测 9 条），闸门把它压成 0 条 + 1 条转人工，验收行成立；② **MR-1 修不掉这个
  假阳性**（闸门前仍是 9 条），所以 MR-3 的闸门是必需的那一道、不是冗余。
  另：整份扫描件走的是「文种识别失败 + 质量门」路径，与闸门无关——这条口径原先
  在闸门 docstring 里写反了，已订正。

## 八、度量口径：benchmark 与生产路径的等价面（已实测并加锁）

S4 的精度指标全部取自 `run_benchmark`，而用户在界面里看到的是
`build_issues_payload` 的 `issues` 分桶。两者只有 finding 集合一致，
基准测出来的精度才代表生产行为。实测结论（golden 样张页数据）：

| 面 | 结果 |
|---|---|
| finding 条数 | 7 vs 7，一致 |
| 规则多重集 | 逐条一致（`build_issues_payload` 只做 dict 化 + severity 归一，**不过滤、不去重**） |
| severity | 归一为 error/warn/info 三桶（`high`→error；`medium`/`manual_review`→warn；`info`→info），分桶不是二次筛选 |
| rule_execution_summary | 原样透传（`executed = pass+fail+n/a`；`unresolved_total` = 三种未执行之和 = `unresolved_rules` 条数） |

因此本报告的 finding 计数与用户在界面上所见**同源同量**，精度可比。

**故意保留的一处差异**：MR-3 的文档级可读性闸门在 `api/main.py` 里于
`build_issues_payload` **之后**施加，benchmark 路径不含闸门——scan-watch
材料在基准里会显示闸门前的条目。scan-watch 不进主指标，故对 P/R 无影响；
但要让基准反映"扫描件只出一条转人工评语"的真实行为，需在 run_benchmark
侧对齐闸门（列为 S4 待办，PR #59 合入后随之生效）。

等价性由 `tests/test_bench_production_parity.py` 锁定（**变异验证**：给
`build_issues_payload` 加一行 `info` 过滤，2 条转红）。
