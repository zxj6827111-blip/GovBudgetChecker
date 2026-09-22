# WP3-A 交付说明：复核会话与持久化完成（2026-09-22）

分支 `feat/review-lifecycle-wp3a`（基线 `main` = `097160d`，即 PR #45 的普通 merge commit）。

## 这一轮修掉的缺陷

整改前，「完成复核」按钮只在 `pending == 0` 时 `router.push("/queue")`，
**没有任何后端写入**。刷新页面或重启服务之后，系统并不知道这份材料被复核过——
"复核完成"是一个只存在于那一刻浏览器里的结论，而且它看起来完全正常。

WP3-A 把复核变成**可持久化、可追溯、可失效**的服务端事实。

## 交付内容

### 1. 独立业务对象：复核会话

```
review_sessions(slot_id, document_version_id, analysis_job_uuid, analysis_basis_token, status, ...)
```

- 四元组绑定，缺一不可（理由见 `docs/MIGRATION_0020_REVIEW_LIFECYCLE.md`）；
- `status ∈ {in_progress, completed, invalidated}`，只有 `in_progress` 有 partial unique index；
- 终态自洽由 CHECK 约束兜住（completed 必须有完成人，invalidated 必须有原因）；
- **失效是保留，不是删除**：没有任何 `DELETE FROM review_sessions`。

### 2. 分析代际：让"重新分析"与"重复落库"可区分

`analysis_jobs.analysis_revision` + `analysis_result_fingerprint`：

- `analysis_basis_token = "<job_uuid>:<analysis_revision>"`；
- 重新分析（包括产出与上一代**逐字相同**的结果）一定换代；
- 同一个结果的重复落库 / 断线重放一定**不**换代。

### 3. 完成门禁：服务端重算，前端 disabled 只是提示

| 阻塞码 | 判据 |
| --- | --- |
| `document_version_changed` | 会话钉的版本 ≠ 槽位当前版本 |
| `analysis_basis_changed` | 会话钉的代际 ≠ 当前分析代际 |
| `identity_unresolved` | 主体/文种/年度未确认（`slot_identity_is_resolved`） |
| `caliber_conflict` | 口径矛盾未裁决 |
| `analysis_unavailable` / `analysis_not_completed` | 当前版本没有可读的当前分析 |
| `pending_findings` / `needs_review_findings` | 逐条问题的人工处理状态 |
| `coverage_unavailable` | 取不到检查覆盖（**未知不得当成零阻塞**） |
| `blocking_obligations` | `blocking_total > 0`（WP3-A 无人工补核能力，因此一律阻塞） |
| `review_not_started` | 还没开复核（0 问题 + 0 阻塞义务也必须有人显式开始并显式完成） |

`confirmed` / `no_issue` / `in_package` 视为已解决；`pending` / `needs_review` 阻塞；
**完全没有记录 = pending**。

### 4. 与工作台同源的问题集合

`src/services/review_problem_set.py` 是后端对"什么算一条待人工处理的问题"的**唯一**口径，
逐条对应前端 `toUiProblems`：`merged.merged_ids` 优先 → legacy `issues` → 全量 findings，
加上 `structured_ingest.review_items`，按 id 去重，再排除 legacy `ignored_issue_ids`。

跨语言契约由 `tests/fixtures/review_problem_fixture.json` 双向锁定：

- 后端 `tests/test_review_lifecycle_service.py`
- 前端 `app/tests/reviewWorkbenchContract.test.ts`

同时修掉一个既有缺陷：`issue_workflow_store._find_issue` 现在也能定位结构化待复核项，
否则工作台上"确认/忽略"这些条目会 404，而门禁又要求它们被处理——
用户会遇到"页面上干不掉，却永远完不成复核"的死局。

### 5. 失效的双保险

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 主动钩子 | `reanalyze_job`（两个重分析入口的共同漏斗） | 立刻失效旧复核 + 清指纹，界面马上显示"需要重新复核" |
| 主动钩子 | `MaterialSlotService.bind_document_version`（版本指针推进路径，语句自守卫） | 版本一换，钉在旧版本上的会话当场失效 |
| 懒失效 | `GET / start / complete` 每次都重新对照当前事实 | 钩子没跑过也拦得住（正确性不依赖钩子） |

复核服务内部的锁顺序（挂在 WP1 既有全序之后）：

```
身份 advisory → material_slots → fiscal_document_versions → review_sessions → analysis_jobs
```

**代际只在拿到槽位行锁与会话行锁之后才读**：顺序反过来会在"读完代际、加会话锁"
之间留下窗口，重新分析在窗口里提交，本次复核就把旧代际记成完成。

### 6. 复核完成后禁止静默改问题

`complete` 在 `issue_workflow_store` 的**同一份文件、同一把文件锁**下落一把编辑锁，
`update_issue` 与 `create_package` 命中即返回 `409 review_completed_locked` +
"请先重新开始复核"。

锁不放到复核所在的 PostgreSQL 里，是刻意的：跨存储无法构成原子事务，
"复核刚完成、锁还没落下"的那一瞬间一次 issue 修改会静默穿过去（§五十七）。

### 7. 槽位状态真正接通

WP1 预置的 `review_required` 此前**没有任何路径会写出来**。现在：

| 事件 | 状态 |
| --- | --- |
| 分析结果落库且当前没有同代际的完成记录 | `review_required` |
| `start` | `reviewing` |
| `complete` | `completed` |
| 失效（版本替换 / 重新分析） | `review_required` |
| 重开 | `reviewing` |

状态值一律由 `material_slot_service.refresh_status` 推导，没有任何直接
`UPDATE material_slots SET status=...`。

### 8. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/reviews/{slot_id}` | 状态 + 当前分析 + 历史 + 门禁 |
| GET | `/api/reviews?job_uuid=` | 按任务解析复核上下文（工作台入口） |
| POST | `/api/reviews/{slot_id}/start` | 幂等开始/复用；就地失效过期会话 |
| POST | `/api/reviews/{slot_id}/complete` | 服务端门禁；幂等 |
| POST | `/api/reviews/{slot_id}/reopen` | 显式重新复核 |

403 无权 / 404 不存在 / 409 业务状态不允许 / 422 参数 / 503 数据库不可用；
业务 409 的 `detail` 是对象（`error` + `message` + `blockers`），不是字符串。

鉴权先判槽位（`api/material_access.py`，从 `api/routes/materials.py` 搬运而来，
判定逻辑逐行未改），再判当前 job。请求体里的 `job_uuid` 只是"待核对的声明"：
服务端自行验证它属于 URL 里的槽位且正是当前分析，不匹配 409。

## 本次没有做（明确的范围边界）

- **人工 obligation 补核**（`verified_ok` / `not_applicable` / `evidence_note`）→ WP3-B。
  因此本轮的 `blocking_obligations` 一律阻塞完成，没有"强制完成"后门。
- **导出归档的强约束**（`ArchivePage` / `create_package` 的导出逻辑）、
  版本级预览/下载入口 → WP3-C。
- **checker / coverage baseline 未改动**（仍为 8 gaps）。
- **legacy ignore 大迁移**（`ignored_issues.json` 与 `issue_workflow_store` 双机制并存）
  → 独立 PR。本轮只保证门禁与工作台**可见集合**一致。
- `GET /api/materials/slots/{slot_id}` 的 `review_summary`（§八十四 标注为非核心阻塞）
  未加：材料详情页改动的回归面大于收益，复核状态已由工作台徽章与状态条呈现。

## 已知风险

1. **`create_package` 的编辑锁是本轮新增的约束**（§一百零三 只要求不动导出业务逻辑，
   §五十六 只点名 `update_issue`）。加它的理由是：只锁 `update_issue` 会让
   "复核完成后问题不会被静默修改"这条规则只挡住两条路径中的一条。
2. **数据库不可用期间发起重新分析**：主动失效钩子写不进去，旧完成记录会短暂显示
   为"已完成"；恢复后懒失效会拦下 complete。极端窄的窗口（故障覆盖整个重分析起点
   **且**新结果与旧结果字节相同）下代际可能不变——此时被复核的内容确实逐字未变，
   结论不受影响，但界面不会提示"需要重新复核"。
3. **新前端没有 JS 单测覆盖页面组件本身**：页面行为由 5 条 E2E 用例覆盖
   （`e2e/tests/review-lifecycle.spec.ts`），纯逻辑（适配层、文案、计数口径）
   由 `app/tests/reviewWorkbenchContract.test.ts` 与既有 adapter 用例覆盖。

## 验证命令

```bash
python -m pytest -q                                    # 1843 passed / 99 skipped
python -m ruff check .
python -m mypy api src tests                           # 243 files, no issues
python scripts/check_coverage_baseline.py --assert-gaps 8

npm --prefix app run test:unit
npm --prefix app run build

node scripts/run-e2e.cjs                               # 196 passed / 18 skipped

# 真库（显式 opt-in）
GOVBUDGET_TEST_DATABASE_URL=... python -m pytest tests/ -q -m real_database   # 98 passed
```
