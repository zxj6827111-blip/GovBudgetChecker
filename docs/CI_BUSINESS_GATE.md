# CI 业务门禁说明（Task 15.2 / 缺口 P2-06 + P2-03）

- 日期：2026-08-27
- 相关脚本：`scripts/check_replay_thresholds.py`、`scripts/run_db_migrations.py`
- 相关测试：`tests/test_ci_business_gate.py`
- 语料：`tests/fixtures/replay/`

## 0. 先说清楚它**不**代表什么

> **本门禁只覆盖结构性指标，不度量召回率与精确率。全绿不等于业务质量达标。**

原因是本轮按既定决策**不建 Golden Corpus、不做人工标注**（缺口 B-04 / P0-08）。
没有标注语料就没有"标准答案"，也就无法回答"应该查出 10 个问题，实际查出几个"。
门禁能保证的是另一件事：**结果的形状是可信的**——不存在"一页文本没提取到却报
`done`"、不存在"多个任务共用一个报告身份"、不存在"正式问题拿不出证据"。

把它当作"业务质量已验证"是错误解读。真正的召回率/精确率门禁需要另行排期建语料。

## 1. CI 全链步骤（整改后）

| 步骤 | 作用 | 本轮新增 |
|---|---|---|
| Ruff | 代码风格与静态错误 | |
| Log message safety | 日志 message 不含材料原文/凭据（Task C） | ✅ |
| Env var consistency | 代码 / `.env.example` / compose 三方对账（Task 14.1） | ✅ |
| Compose config check | compose 插值与服务清单（恰好 5 个服务） | ✅ |
| Mypy | 类型检查 | |
| Pytest | 单元与集成测试 | |
| **DB migrations** | 空库跑全量迁移 + 幂等校验 | ✅ |
| **Business gate** | 回放结构性指标阈值 | ✅ |
| Frontend build | 前端构建 | |
| E2E | Playwright 关键流程 | 本轮补齐上传→等待→复核→导出 |

至此覆盖 PLAN 要求的"构建 + 迁移 + E2E 全链"。

## 2. 迁移步骤（P2-03）

CI 起一个一次性 `postgres:16` service，然后：

```bash
DATABASE_URL=postgresql://govbudget_ci:govbudget_ci@localhost:5432/fiscal_ci \
  python scripts/run_db_migrations.py
```

脚本做两件事：

1. 对空库跑一遍全部迁移（当前 18 个）；
2. **再跑一遍**，断言第二遍新增应用数为 0 —— 这是迁移幂等性的机器证明，
   也是重复部署与回滚的前提。

本机实测（真实 PostgreSQL 15.17）：

- 对已迁移的开发库 `fiscal_db`：新增 0 个、第二遍 0 个 → PASS（幂等）；
- 对全新 schema `PG_SCHEMA=migcheck_tmp`：新增 18 个、第二遍 0 个 → PASS（空库路径可用）。

未在本机验证"全新**数据库**"路径：开发库用户没有 `CREATE DATABASE` 权限
（`InsufficientPrivilegeError`），该路径由 CI 的 postgres service 覆盖。

## 3. 业务门禁的五条检查

| 检查项 | 判定 | 默认阈值 | 关联缺口 |
|---|---|---|---|
| `report_id_uniqueness` | 不同任务不得共用同一 `report_id` | 冲突数 = 0 | P0-09 |
| `completed_jobs_have_page_coverage` | 完成态任务必须带 `page_coverage` | 缺失数 = 0 | B-01 / B-03 |
| `done_jobs_min_page_coverage` | `done` 任务覆盖率下限（低覆盖只能进 `review_required`） | ≥ 0.8 | B-01 / B-03 |
| `evidence_completeness_rate` | 正式问题的证据完整率 | ≥ 0.99 | P0-07 |
| `unknown_report_kind_ratio` | 类型识别失败比例上限 | ≤ 0.35 | P0-04 / P1-05 |

阈值都是 CLI 参数（`--min-page-coverage` / `--min-evidence-rate` /
`--max-unknown-kind-ratio`），不是硬编码。

## 4. 为什么这不是"假门禁"

一个只会通过的门禁没有意义。这里用**成对语料**证明每条阈值都真的会拦：

| 语料 | 预期结果 |
|---|---|
| `tests/fixtures/replay/pass`（4 个任务） | 五项全绿 |
| `fail_report_id_collision` | 只有 `report_id_uniqueness` 变红 |
| `fail_missing_coverage` | 只有 `completed_jobs_have_page_coverage` 变红 |
| `fail_low_coverage_done` | 只有 `done_jobs_min_page_coverage` 变红 |
| `fail_low_evidence` | 只有 `evidence_completeness_rate` 变红 |
| `fail_high_unknown_ratio` | 只有 `unknown_report_kind_ratio` 变红 |

`tests/test_ci_business_gate.py` 把上表固化成参数化测试，并额外断言：
把阈值拧紧后 `pass` 语料必须变红（证明阈值是被读取的参数），
数据源缺失时不给 `--allow-missing` 必须失败（避免"没数据就静默通过"）。

`pass` 语料刻意包含一个**低覆盖率的 `review_required` 任务**：
整改后的正确形态就是"覆盖率不够 → 转人工复核"，门禁必须接受这种任务，
只拦"覆盖率不够却报 `done`"。

## 5. 生产环境怎么用

CI 上没有真实 `uploads/`，因此额外跑一次带 `--allow-missing` 的判定：有数据才校验，
无数据显式打印 SKIP 与原因，不伪装成通过。

生产/预发环境按同一套阈值判定真实产物：

```bash
python scripts/replay_analysis.py --uploads /data/uploads --output /tmp/replay.json
python scripts/check_replay_thresholds.py --report /tmp/replay.json
```

注意：**历史产物会直接把这套门禁打红**（`docs/HISTORICAL_REPLAY_2026-08-27.md`：
766 个历史任务里 0 个带 `page_coverage`，另有 2 组 `report_id` 冲突）。
这是数据事实而非门禁误判——历史数据的处置见
`docs/LEGACY_DATA_CLEANUP_PLAN_2026-08-27.md`。对存量数据判定时应先按
"整改后新产生的任务"过滤，或在历史数据完成重跑后再纳入门禁。
