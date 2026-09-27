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
- **7 份资产的盲标性已受损**：这 7 份的引擎输出在本仓库历史中已多次出现
  （fixtures、验收脚本、PR 评论），L2 深标的「系统输出生成前完成」前提
  对它们不再成立。建议：这 7 份只作 L1 与锚定观察面，L2 深标集中在
  S2 新收的 23 份外部材料上。
- **未覆盖**：扫描观察集（scan-watch）本次为空——可读性闸门的效果只在
  单元测试中验证（MR-3），没有真实扫描件语料。
