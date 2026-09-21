# 批次 B 第七轮独立复验报告

2026-09-15。**结论：GO。R6 残留 P1（纯年份表头跨年借数）完整闭环，R6 已知局限（换序布局本年完整仍 deferred）一并治愈。R5-P1-1、R5-P1-2、R6-P1-1 至此全部关闭，批次 B 整改验收通过，暂停点二可由用户决定是否解除。**

本轮读取新版 `outputs/remediation_v5/batch_b/handoff.md`（Round 6），核对核心文件 SHA 指纹与声明一致，静态通读新取数实现，重跑 R1～R6 累计探针（含 R6 全部反例回归）并新增 7 组纯年份表头正反对照，最后独立跑完全部门禁。业务源码与既有测试未修改。

## 本轮已确认的修复

### R6-P1-1：纯年份表头跨年借数关闭（`src/engine/budget_rules.py:557-662`）

新实现分四层，静态核验与实测一致：

1. **年份解析**：表头前 3 行提取全部 4 位年份（2000~2099），按列记录最大年份；全表头最大年份为预算年度，要求 distinct_years ≥ 2 才启用年份判定（单年份表头安全回退）。
2. **关键词扩展**：`col_is_prior` 词库新增「执行数/决算数/决算」，「2024年决算数」类列直接命中 prior，不依赖年份比较。
3. **current 列精准定位**：候选列中存在 current 列时直接取该列单元格，空白/非数值立即返回 None，绝不向右 fallback。
4. **空白即停**：无 current 列时的 fallback 扫描，遇 prior 列、字段边界文本或非数值单元格一律 break。

实测对照（真实规则，未 mock）：

| 探针 | 结果 | 判定 |
|---|---|---|
| `r6_bud101_pure_year_header_blank`（R6 原反例：2025 空白 + 2024=100） | deferred「T1未找到收入总计数值」 | ✅ 假平衡关闭 |
| `r6_bud105_t4_pure_year_header_blank`（R6 原反例：T4 纯年份借数） | deferred 且 reason 含「T1与T4收入总计数值缺失」 | ✅ 静默判等关闭 |
| `r7_bud101_pure_year_complete`（纯年份两年度完整 100/90、100/95） | returned [] | ✅ 精准取 2025 列，非一律 deferred |
| `r7_bud105_t4_pure_year_mismatch`（T4 本年 95、2024 列 90） | 报「T1与T4收入总计不一致： T1=100.00, T4=95.00 （差额=5.00)」 | ✅ 差额 5 铁证取本年列：借 2024 列则差额为 10，deferred 则记缺失而非差异 |
| `r7_bud101_pure_year_zero`（本年 0、2024=100） | 报「T1收支总计不一致： 收入=0.00, 支出=100.00」 | ✅ 0 按合法值处理 |
| `r7_bud101_single_year_prior_word_blank`（['预算数','2024年决算数'] 空白） | deferred | ✅ distinct_years<2 时决算词仍挡驾 |
| `r7_bud101_keyword_year_mixed_blank`（['本年预算数','2024年决算数'] 空白） | deferred | ✅ |
| `r7_bud101_three_year_blank`（2025/2024/2023 三年份空白） | deferred | ✅ 不借 2024 或 2023 |

### R6 已知局限治愈：换序布局

- `r6_bud101_swapped_complete`（上年在左、本年完整 100/100）：**returned []**，本轮前为 deferred——current 列精准定位使换序布局可用。
- `r6_bud101_swapped_blank_current`（换序且本年空白）：仍 deferred，fail-closed 方向正确。

### 无回归确认

- R1～R6 累计探针（严格解析、单位换算、精度、跨逗口子句、年份绑定、日志扫描、缺输入/缺列边界、双年度关键词布局等）全部保持预期；本轮独立结果与交付方重跑的 R6 探针结果逐键一致（交叉验证）。
- R5-P1-2 缺列对照（缺项目列、缺基本列）行为不变。

## 独立回归

- 完整 pytest（工作区 TEMP/TMP，禁用 cacheprovider）：**1206 passed、1 skipped**，94.97 秒。与交付声明一致（较 R6 的 1201 净增 5 个回归测试）。
- Ruff `src api scripts tests`：通过（All checks passed）。
- mypy `src api --ignore-missing-imports`：Success，104 个源文件无问题。
- 新鲜 legacy Golden：**TP=3、FP=0、FN=0**，precision/recall=1.0，hint 3/3，证据面 4/4，severity/page/locatable 全 1.0，GATE-PASS。
- 核心文件 SHA-256 与 handoff 指纹清单逐项一致；`corpus/DOC-20260905-001/golden.json` 未触碰。

## 取证说明（不影响结论）

`outputs/batch_b_independent_review_r6_20260915/probe_results.json` 已被交付方在 Round 6 整改后用 R6 探针重跑覆盖（其 handoff 已注明该运行方式）。R6 原始反例结果以《BATCH_B_INDEPENDENT_REVIEW_R6_20260915.md》正文记录为准；本轮全部结论以 R7 目录独立产物为准。建议后续轮次交付方不要在复验目录内重跑覆盖原始产物。

## 证据与交付边界

目录：`outputs/batch_b_independent_review_r7_20260915/`。

- `probe.py`、`probe_results.json`、`probe.log`：R1～R6 累计探针 + 本轮 7 组纯年份对照（`r7_*` 键），实际规则未 mock。
- `pytest.log`、`ruff.log`、`mypy.log`：独立检查日志。
- `golden_replay.log`、`golden_eval.log`：新鲜回放及评估输出。
- 回放：`outputs/golden_replay/DOC-20260905-001-legacy-20260915-125231-880-be79fe30724e40db8a1db6fb1eac8f8d.json`。
- 评估：`outputs/golden_eval/DOC-20260905-001-eval-legacy-20260915-205247-b707aab67d8b426e90cdcc37fc65bb7d.json`。

批次 B 取数边界整改验收通过。A 契约、冻结输入和 Golden 真值全程保持；本轮只新增 R7 报告与 R7 证据，保留以前各轮记录；未进入批次 C、未推送部署。是否解除暂停点二、进入批次 C，由用户决定。
