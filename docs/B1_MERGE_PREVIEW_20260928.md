# 车道1 合入 main 的合并预演（2026-09-28）

> 为什么要做：车道1（`fix/engine-parse-20260927`）是从 **PR #57 合并之前**的 main
> （`28d7ebb`）切出来的，而 PR #57 的 WP4-H 与车道1 的 MR-4 **都改了
> `src/engine/budget_rules.py`**——「车道1 + 当前 main」这个组合在合并前从未被
> 测试过。并行规程要求「车道1 合入后推倒重跑对应评测」，本预演把这个验证提前到
> 合并之前，避免在 main 上才发现冲突或行为变化。
>
> 方法：`merge-preview/lane1-into-main` = `4d6662c`（车道1 HEAD）+ `origin/main`（`d82f4fa`）。

## 一、合并本身

| 项 | 结果 |
|---|---|
| 冲突 | **无**（`git merge origin/main` 干净完成） |
| 带入的内容 | PR #57 的 WP4-H 实现与测试、PR #58 的系统审计报告与 `docs/WP4-I_BENCHMARK_PLAN.md` |
| 合并后 HEAD | `cb961ec`（预演分支，未推） |

注：预演合并把 main 上的 WP4-H 一并带入，因此车道1 的三次评测**基线原本不含
WP4-H**——本预演正是为量化这个差异。

## 二、测试面：2110 passed / 0 failed

合并态全量测试通过（含 WP4-H 的 `tests/test_perf_phase_amount.py` 578 行与车道1
新增的 `tests/test_engine_repair_20260927.py`、`tests/test_bud105_dual_caliber.py`），
**没有出现规则模块互踩**。

## 三、行为面：4 份决算完全不变，差异全部来自 WP4-H 自己的新规则

同一批 7 份语料、同一套工具，对比「车道1 单独（`4d6662c`）」与「车道1 + main」：

| 材料 | 车道1 单独 | 车道1 + main | 差异来源 |
|---|---|---|---|
| DOC-20260905-001 | 7 / 4 | 7 / 4 | — |
| DOC-B1-001 宜川 | 13 / 1 | 13 / 1 | — |
| DOC-B1-002 石泉 | 9 / 2 | 9 / 2 | — |
| DOC-B1-003 文旅 | 2 / 7 | 2 / 7 | — |
| DOC-B1-004 建管委预算 | 2 / 5 | 2 / **6** | `V33-PERF-PHASE-AMOUNT` 新增 `insufficient_data` |
| DOC-B1-005 城管预算 | 1 / 4 | 1 / 4 | — |
| DOC-B1-006 文旅预算 | 1 / 6 | 1 / **2** findings（6 unresolved 不变） | `V33-PERF-PHASE-AMOUNT` 产出 1 条 `medium` |

（单元格为 findings / unresolved。）

逐规则 diff 显示：**除 `V33-PERF-PHASE-AMOUNT` 外，没有任何规则的 finding 数或
执行状态发生变化**。

## 四、结论

1. **合并安全**：无冲突、无回归，差异只来自 main 自带的新规则自身的输出。
2. **验收行不受影响**：「4 份决算 unresolved 53→14」「宜川 13」「文旅级联消失」
   在合并态上**一字不变**——这些数字可以作为合并后的验收依据。
3. **需要用户知晓的一点**：合并后预算侧会多出 WP4-H 的 1 条 finding 与 1 条未决。
   这不是回归（是 main 已有能力的体现），但会让预算侧计数与车道1 单独时不同。
4. 预演分支未推送、未合并；是否合并 PR #59 仍取决于宜川 ≤12/≤13 的裁决。

## 五、可复现

```bash
git branch -f merge-preview/lane1-into-main fix/engine-parse-20260927
git worktree add .tmp/wt-preview merge-preview/lane1-into-main
cd .tmp/wt-preview && git merge origin/main --no-edit
python -m pytest tests/ -q                      # 2110 passed
cp <repo>/scripts/bench_register.py <repo>/scripts/run_benchmark.py \\
   <repo>/scripts/eval_benchmark.py scripts/
python scripts/run_benchmark.py --corpus <repo>/corpus \\
    --out-root <repo>/outputs/benchmark3-merge
```
