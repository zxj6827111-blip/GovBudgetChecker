# 材料台账 WP2-B 交付说明（单位时间轴 + 材料详情）

- 交付日期：2026-09-22
- 分支：`feat/material-ledger-ui-wp2b`
- 基线：PR #43 普通 merge 后的 main = `731d6eb69143e3fcf76a3188b4fae19d21ed3e9e`
  （PR #43 的 merge commit 保留了两个父提交，WP2-A 的独立 Review 历史未丢失）
- 本轮范围：PLAN 的 `WP-UI-07`（09 单位时间轴）与 `WP-UI-08`（10/11/12/13 材料详情 + 第五个 Tab 处理记录）
- 明确未做：WP2-C 全局搜索 / Ctrl+K / `/history` 收口、WP3 复核生命周期（`review_session`、
  Review Complete、人工 obligation review、复核状态写入、reanalysis invalidation）、
  WP9 应收材料基线（CSV/Excel 初始化、官网采集、expected slots 批量生成）、
  `backfill --apply`、WP4 的 8 个 obligation checker、WP5–WP8

---

## 1. 交付物清单

### 1.1 后端：4 个只读 GET（接口面从 3 条变为 7 条）

| # | 接口 | SQL 条数 | 说明 |
| --- | --- | --- | --- |
| 4 | `GET /api/materials/units/{unit_id}/timeline` | 1 | 单位多年度时间轴 |
| 5 | `GET /api/materials/slots/{slot_id}` | 3 | 槽位详情（含当前版本、来源、当前分析、检查覆盖） |
| 6 | `GET /api/materials/slots/{slot_id}/versions` | 1 | 文件版本历史 |
| 7 | `GET /api/materials/slots/{slot_id}/runs` | 1 | 处理记录（分析运行摘要） |

新增/修改文件：

- 新增 `src/schemas/material_detail.py`（详情契约；复用 WP2-A 的 `MaterialSlotSummary` 与 meta 口径）
- 新增 `src/services/material_detail_query_service.py`（只读查询与投影）
- 修改 `api/routes/materials.py`（新增 4 个路由 + `slot_row_is_visible` / `_require_unit_access` /
  `_require_slot_access`；`_run_query` 抽出 `_with_connection` 供两个查询服务共用 503 口径）
- 修改 `tests/test_material_slot_api_regression.py`（接口面冻结清单 3 条 → **恰好 7 条只读 GET**）

**没有新增 migration**：`SCHEMA_CHANGE_REQUIRED = NO`。

### 1.2 前端：2 个新路由

| 路由 | 页面 | 对应效果图 |
| --- | --- | --- |
| `/materials/unit/[unitId]` | 单位多年度材料时间轴 | `09_unit_multiyear_timeline.png` |
| `/materials/slots/[slotId]?tab=` | 材料详情（5 个 Tab） | `10`~`13` + `WP2B_06` |

Tab key 固定：`overview` / `findings` / `coverage` / `versions` / `runs`
（写入查询串，便于以后全局搜索直接落到某个 Tab）。

新增文件：`app/lib/materialDetailPresentation.ts`、`app/app/components/materials/materialDetailAdapters.ts`、
`UnitTimelinePage.tsx`、`MaterialDetailPage.tsx` 及 5 个 Tab 组件、4 个 Next 代理 route handler、
2 个页面入口 `page.tsx`。

改动：`MaterialSlotCard` 增加可选 `href`（有槽位才可点击）；部门矩阵的单位主体名链接到单位时间轴
（部门汇总主体**不**链接，避免"点进去空页面"）。

---

## 2. 数据链路审计结论（写代码前先查清的事实）

处理记录/当前分析的关联链**唯一**，全部取自数据库既有字段，没有发明第二条：

```
material_slots.current_document_version_id
  └─> fiscal_document_versions.id                        （版本历史；版本挂 slot_id）
        └─> analysis_jobs.metadata
              -> structured_ingest ->> document_version_id  （精确关联）
              └─> analysis_results.job_id                 （分析结果：ai_findings / rule_findings）
                    ↑
              analysis_jobs.metadata -> result_meta -> obligation_coverage
                                                        （检查覆盖：引擎写入的整份义务账本）
```

关键事实（逐条核对过源码，不是推测）：

1. **检查覆盖的落库位置**：`api/main.py` 把整份义务账本写进 `result["meta"]["obligation_coverage"]`；
   `analysis_result_store._build_job_metadata` 又把整份 `result.meta` 落进
   `analysis_jobs.metadata.result_meta`。因此覆盖数据的读取路径是
   `metadata -> result_meta -> obligation_coverage`，且它**天然属于某次运行**，
   而运行已精确绑定文件版本 —— 这是"这份材料的检查覆盖"成立的前提。
2. **JSONB 的真实形态**：仓库没有给 asyncpg 注册 JSON codec，取回来是**字符串**。
   只写 `isinstance(x, dict)` 会在真库上静默读到空 dict，页面显示"没有覆盖记录"而数据其实存在。
   查询层因此统一走 `_json_value` 强制转换（与 `analysis_result_store._coerce_json_value` 同一手法）。
3. **正式 finding 的唯一门禁**是 `src/services/evidence_guard.is_formal_finding`
   （`evidence_status != "degraded_missing_evidence"`），唯一计数函数是
   `evidence_guard.count_formal_findings`。权威语义是"没有被 evidence guard 降级
   就属于正式 finding"，`error` / `warn` / `info` 三种严重度**都算**。
   详情接口的 `formal_issue_count` 直接调用该函数（见 §11.1 的收口），
   没有第二套判定。legacy 落库路径下 `rule_findings` 就是 `issues` 的展平去重结果
   （`_flatten_legacy_issues`），双模式路径本来就没有 `issues` 键 —— 两条路径读的都是同一批对象。
4. **仓库既有的任务排序口径**是
   `COALESCE(completed_at, started_at, updated_at, created_at) DESC, id DESC`
   （见 `analysis_result_store` 的 jobs 列表查询）。处理记录复用这一口径，
   因此「任务历史」与「处理记录」看到的"最新"是同一个。
5. **PDF 入口全部是 job 维度**：`/api/files/{job_id}/source|preview` 与
   `/api/reports/download`，鉴权都走 `require_job_access`（依赖 status.json 里的
   `created_by` 与组织归属）。仓库**没有**版本维度、且带权限校验的预览入口。

---

## 3. 口径：本轮不允许出现的假结论

### 3.1 时间轴（09）

- **只按 `material_slots.fiscal_year` 排列**（降序）。禁止按 `published_at` /
  `uploaded_at` / `created_at` / job 年份排：2024 年度决算在 2025-08 发布是常态，
  按发布时间排会把它挪到 2025 那一行。页面顶部与来源卡片同时显示"财政年度"与
  "网页发布日期"两件事，且互相不覆盖。
- **`fiscal_year IS NULL` 不兜底**成 2000 / 0 / 当前年份，单独进 `unresolved_year_slots`。
- **同一（年度 × 文种）多条槽位不静默吃掉**：DTO 是数组
  （`budget_slots[]` / `final_slots[]` / `unclassified_slots[]`），
  界面显示主卡片 + 「另有 N 个待确认槽位」并**逐条列出**。
- 没有槽位的格子只写「尚无已建立材料」，**不写**缺失/逾期/未上传。
- 年度状态标签**没有「完整」这个取值**：判断完整需要应收材料基线（WP9），
  基线未建立时把年度说成"完整"就是把"我只知道这些"说成"这些就是全部"。

### 3.2 身份

- 单位时间轴**只按 `subject_org_id`** 查询，不按单位名称（组织目录里"部门"与
  "同名本级单位"可以完全同名）。
- 权限判定按槽位的 `jurisdiction_org_id` / `department_org_id` / `subject_org_id`
  三列 OR 命中可见范围，与 SQL 里的过滤谓词同源。

### 3.3 当前版本与当前分析

- 当前版本**唯一真值**是 `material_slots.current_document_version_id`；
  为 NULL 时返回 `current_version: null`，**不**从历史版本里挑最新一份冒充。
- 当前分析必须满足"当前版本 → 精确运行 → 已落库结果 → 正式门禁"四环全通，
  才给出 `formal_issue_count` 整数；缺任何一环即 `null`。
  **版本替换后不会继续显示上一版的 finding 数**（既不显示 3，也不显示 0）。
- 同版本多次运行的选择稳定：先在有 `analysis_jobs.id` 的行上按
  `run_sort_key` 排序，再按同一顺序投影成 DTO（DTO 里没有 id，所以排序必须先在行上做——
  否则"处理记录的顺序"和"当前分析选的是哪次"会各有一套规则）。

### 3.4 检查覆盖

- 不可用时返回 `{available:false, reason, summary:null, items:[]}`，
  页面只说一句"当前文件版本暂无可确认的检查覆盖记录"，**一个数字都不给**：
  不显示 `0 / 0`、`0%`、`100%` 或"全部通过"。
- 义务状态（11 个取值）与原因码原样透出，中文映射只在
  `app/lib/materialDetailPresentation.ts` 一处；业务分组 =
  `completed → 自动检查完成`、`not_implemented → 自动检查暂不可用，需要人工核验` 等按 §三十六 逐条落地。
- `blocking_total` 只展示并明确写出"不得标成检查完成"，**不提供**任何"人工完成/关闭阻塞"入口（属于 WP3）。

### 3.5 缺失态与截止时间

- **只有真实 `status = 'missing'` 才进缺失态**。
- `not_due + due_at_unknown` → "截止时间未知，当前无法判断是否逾期"，绝不显示缺失。
- missing 且该槽位存在历史版本 → 只写"当前无有效文件版本"；即使没有关联版本，
  也只说"该槽位尚未关联任何 PDF 版本"。整段文案里**不出现**"从未上传"这个词
  （连否定句里也不放：一句"不能视为从未上传"在关键字扫描时与断言本身无法区分）。

### 3.6 处理记录

- 关联依据唯一：`metadata.structured_ingest.document_version_id` 等于该槽位某个版本 id。
  meta 上明示 `linkage_basis = structured_document_version_id` 与
  `legacy_unlinked_runs_excluded = true`。
- **不按文件名 / organization_name / 创建时间接近 / 文件哈希 / 目录名 / "最新 job"** 猜归属。
  真库用例里专门构造了"同名文件"的 legacy 任务，验证它不会进入任何槽位。
- 每条运行标明**当前文件版本 / 历史文件版本**，界面上分成两个区块，不混成一列。
- 列表只给摘要，不含 `raw_response` / 完整 finding / 完整 evidence（§四十七）。
- 失败运行的错误摘要经过安全过滤：形态像堆栈、本地路径、连接串的一律换成
  "处理失败（详情见任务日志）"。

### 3.7 版本级预览 URL 的显式决定（§四十一）

本轮**不生成** `preview_url` / `download_url`（恒为 `null`），理由：

1. 仓库现有 PDF 入口全部是 **job 维度**，其鉴权 `require_job_access` 依赖 status.json
   （含 `created_by` 与组织归属），与"这条槽位你能看吗"不是同一个权限主体；
2. 从槽位详情发一个 job 维度的链接，会让"页面可见"与"链接可用"分属两套判定，
   还会把 job_uuid 暴露给无权访问该任务的人；
3. 本轮不引入第四套权限模型。版本级安全入口应当与 WP3 的复核生命周期一起设计，
   那时权限主体统一到槽位；
4. 因此按 §四十一 的 fail-closed 分支取 `null`，且**不制造** `file://` 或 `/uploads/...`。

界面上对应位置显示这句说明，而不是一个点了会 403 的按钮。

---

## 4. SQL 与性能

| 接口 | 实际 SQL | 预算（§七十三） |
| --- | --- | --- |
| unit timeline | 1 | ≤ 2 |
| slot detail | 3 | ≤ 5 |
| slot versions | 1 | ≤ 2 |
| slot runs | 1 | ≤ 2 |

版本 / 运行 / 详情共用**一条** `fiscal_document_versions LEFT JOIN analysis_jobs LEFT JOIN analysis_results`：

- `LEFT JOIN` 而不是 `JOIN`：没有关联运行的版本（刚上传还没分析）必须照样出现在版本历史里；
- 关联谓词写在 `ON` 而不是 `WHERE`（写进 `WHERE` 会把 `j` 为 NULL 的行一起滤掉，
  等价于把 LEFT JOIN 降级成 INNER JOIN）；
- 加了 `~ '^[0-9]+$'` 守卫，避免被写坏的 metadata 让整条查询抛 bigint cast 异常；
- 结果行数由"该槽位的版本数 × 每版本运行数"决定，实际是个位数行；响应里**不含**大字段。
- SQL 里**没有 ORDER BY**：排序只在 Python 的比较器里存在一处，两处各写一份必然漂移。

没有为此新增索引，也没有改 `migration 0019` 或任何历史 migration。

---

## 5. 鉴权与越权（IDOR）

- `MaterialAccessScope` 沿用 WP2-A：`visible_org_ids`（能看哪些**数据**）
  与 `container_org_ids`（能开哪些**页面容器**）严格分开。
- **单位时间轴是数据目标**：`unit_id` 必须自己就在 `visible_org_ids` 里（§六十八），
  不靠容器闭包放行。
- **槽位详情 / 版本 / 处理记录三个接口共用唯一入口 `_require_slot_access`**：
  不存在 404、越权 403；三个接口各写一份必然漂移，漏掉的那一个就是数据泄露口。
- 反例覆盖：
  - 单位 A1 的账号拿单位 A2 的 id 请求时间轴 → 403；
  - 单位 A1 的账号拿槽位 B2 的 UUID 请求 `/slots/{id}`、`/versions`、`/runs` → 三个都 403；
  - 完全无关区划的账号请求四个新接口 → 全部 403；
  - 部门级授权可读其下级单位的槽位（可见集合 = 授权节点的后代闭包）；
  - 没有任何授权范围 → 403（fail-closed），不是"一片空白"；
  - 数据库不可用 → 503，不伪装成空数据。
- 真库用例 `test_detail_queries_do_not_cross_unit_scope` 在**真 SQL** 上验证了
  时间轴的可见范围谓词确实排除了对方单位的槽位。

---

## 6. 测试

### 6.1 新增用例

| 文件 | 条数 | 覆盖 |
| --- | --- | --- |
| `tests/test_material_ledger_detail_query_service.py` | 45 | 年度分组/排序、未知年度、多槽位不丢、当前版本/当前分析、正式门禁分桶、覆盖可用性、SQL 预算 |
| `tests/test_material_ledger_detail_api.py` | 26 | 4 个接口契约、三桶划分、missing / due_at_unknown、storage_key 不外泄、RBAC/IDOR、只读性 |
| `tests/test_material_ledger_detail_pg.py` | 8 | 真库 A–F（版本保留与 `is_current`、历史分析不冒充、多 run 稳定选择、JSONB 覆盖读取、legacy 不关联、跨单位范围、年度排序） |
| `app/tests/materialDetailAdapters.test.ts` | 111 断言 | 时间轴分组/多槽位/未知年度、缺失态与 due_at_unknown 文案、null≠0、覆盖 KPI 与分组、义务状态 11 个映射、版本/来源/运行呈现、Tab 契约、响应解析 |
| `e2e/tests/material-ledger-detail.spec.ts` | 13 | 主链 08→09→10→11→12、`?tab=`、年度口径、多槽位、missing、due_at_unknown、版本替换、覆盖不可用、人工上传来源、403 错误态、部门汇总主体不链单位 |

新增支撑：`tests/support_material_detail_db.py`（支持两表 LEFT JOIN 三态的假连接；
不认识的 SQL 直接报错，投影列必须存在 —— 与 WP1/WP2-A 的假连接同一纪律）。

### 6.2 变异验证（8 条红线逐条去掉防护）

对每条红线把生产代码的防护去掉/反转，再跑相关用例，全部被抓住（8/8）：

| # | 变异 | 结果 |
| --- | --- | --- |
| ① | 时间轴年度排序降序 → 升序 | CAUGHT |
| ② | 当前版本指针为空时挑最新版本冒充 | CAUGHT |
| ③ | 当前版本无分析时回退到该槽位任意运行的结论 | CAUGHT |
| ④ | `formal_issue_count` 算不出来时返回 `0` 而不是 `null` | CAUGHT |
| ⑤ | 检查覆盖不可用时返回 `available=true` + 全 0 | CAUGHT |
| ⑥ | 单位时间轴授权恒放行 | CAUGHT |
| ⑦ | 槽位可见性判定恒真（放行越权） | CAUGHT |
| ⑧ | 版本级预览 URL 直接拼未鉴权地址 | CAUGHT |

> ④ 首次跑时报 MISSED，原因是**跑错了测试文件**（覆盖该分支的用例在纯逻辑测试文件里），
> 补上正确的文件后确认被抓住。这类"测试选错文件"的假绿灯正是变异验证要暴露的东西。

### 6.3 本机验证结果（Windows，2026-09-22）

| 项 | 结果 |
| --- | --- |
| `python -m pytest -q` | **1626 passed, 43 skipped**（WP2-A 基线 1555/35；新增 71 条用例，其中 8 条真库用例默认跳过，故 skip 由 35 变为 43） |
| `python -m ruff check .` | All checks passed |
| `python -m mypy api src tests` | Success: no issues found in 225 source files |
| `npm --prefix app run test:unit`（20 个脚本） | 全绿（新增 `test:material-detail`，111 断言） |
| `npm --prefix app run build` | 通过（新增 2 条动态路由） |
| 全仓 E2E | **156 passed, 13 skipped**（13 skipped = 两套截图采集，默认跳过） |
| material-ledger E2E（WP2-A + WP2-B） | **32 passed, 5 skipped** |
| PostgreSQL（`test_material_ledger_pg.py` + `test_material_ledger_detail_pg.py`） | **16 passed** |
| `python scripts/check_coverage_baseline.py --assert-gaps 8` | 通过（**仍 8 gaps**，本轮不修 checker） |

`Golden / rules / obligations` 均未修改；WP3、WP9、`backfill --apply` 未触碰。

---

## 7. 截图（`docs/wp2b-screenshots/`）

| 文件 | 内容 |
| --- | --- |
| `WP2B_01_unit_multiyear_timeline.png` | 单位多年度时间轴（2026/2025/2024 + 年度待确认块、多槽位展开） |
| `WP2B_02_material_detail_overview.png` | 材料详情 · 材料概览 |
| `WP2B_03_material_detail_coverage.png` | 材料详情 · 检查覆盖 |
| `WP2B_04_material_detail_versions_source.png` | 材料详情 · 版本与来源 |
| `WP2B_05_material_slot_missing_state.png` | 缺失态（逾期未上传 + 当前无有效文件版本 + 历史版本仍在） |
| `WP2B_06_material_processing_runs.png` | 处理记录（当前/历史文件版本分栏） |
| `WP2B_07_no_current_analysis.png` | 附加：版本替换后无当前分析结果（本次最易误读的口径） |

采集方式：`GBC_CAPTURE_SCREENSHOTS=1 npm --prefix app run test:e2e -- material-ledger-detail.screenshots`
（默认跳过；1920 视口）。

> **截图证明的是界面在给定后端响应下渲染成什么样，不代表线上数据库的真实数据。**
> 数据来自 `e2e/tests/materialLedgerDetailFixtures.ts` 的固定 mock。

---

## 8. 提交拆分

| # | commit | 内容 |
| --- | --- | --- |
| 1 | `feat(materials-api): add unit timeline and slot detail queries` | 契约 + 查询服务 + 时间轴/详情两个路由 |
| 2 | `feat(materials-api): add slot versions and run history` | 版本历史与处理记录两个路由 + 越权入口收敛 |
| 3 | `feat(materials-ui): add unit timeline and material detail shell` | 页面 09 与详情骨架、5 个 Tab 的壳、路由与代理 |
| 4 | `feat(materials-ui): add coverage versions sources and runs tabs` | 4 个 Tab 的完整实现 + 展示层文案 |
| 5 | `test(materials): cover current-version truth and detail RBAC` | 后端/前端/真库/E2E 用例与截图 |

---

## 9. 已知限制

1. **版本级安全预览/下载入口未开放**（见 §3.7）：详情页不提供"打开此版本"按钮，
   只给说明文案。可在审核工作台按任务查看原件。
2. **受理基线未建立（WP9）**：时间轴的"年度情况"是保守描述（没有「完整」取值），
   完整率与真实缺失数仍为 `null`。
3. **`formal_issue_count` 只在槽位详情里可算**（列表接口仍为 `null`）：
   给列表每一行都算需要额外的 join，会破坏"列表 1-2 条 SQL"的预算，
   属于 WP3 复核生命周期接通后的口径演进。
4. **状态不会自动推进**：本轮只消费 WP1 已写入的 `status` / `status_reason`。
5. **missing 槽位只能展示已存在的**：矩阵里"没有 budget cell"不会被自动造成 missing 槽位（WP9）。
6. **真库用例默认跳过**（需显式 `GOVBUDGET_TEST_DATABASE_URL`）；本机 16 条已实跑通过。
7. **未在万级槽位数据上做耗时压测**；已用 SQL 条数与索引现状论证，但没有实测 p95。
8. 前端 `npx tsc --noEmit` 在 `app/tests/workspaceNavigation.test.ts:103` 有一条**基线既有**的
   类型报错（该文件本轮未修改）；仓库的门禁是 `test:unit` 与 `build`，两者均通过。

---

## 10. 复算命令

```bash
python -m pytest -q && python -m ruff check . && python -m mypy api src tests
python scripts/check_coverage_baseline.py --assert-gaps 8
npm --prefix app run test:unit && npm --prefix app run build
E2E_BASE_URL=http://127.0.0.1:3100 npm --prefix app run test:e2e -- material-ledger
GBC_CAPTURE_SCREENSHOTS=1 npm --prefix app run test:e2e -- material-ledger-detail.screenshots
GOVBUDGET_TEST_DATABASE_URL=postgresql://... python -m pytest \
  tests/test_material_ledger_pg.py tests/test_material_ledger_detail_pg.py -v
```

> 本机 3000 端口被另一个 Next 应用占用，e2e 请显式指定空闲端口（本说明用 3100）。
> 另外：`next build` 与 `next dev` 共用 `app/.next`，在 dev server 存活期间跑 build
> 会让 dev 的路由清单失效（新路由返回 404）。e2e 前先停掉 dev server，或让
> `scripts/next-dev.cjs` 清缓存重启。

---

## 11. 最终独立 Review 收口（2026-09-22）

独立 Review 基线 `960409f1cf5d382b368a47dddce5509d20f36e73` 通过主链、版本隔离、
RBAC/IDOR、timeline、current pointer、current analysis、coverage、sources、runs 之后，
只提出两项合并阻塞。本轮**只**修这两项，没有重构任何已通过的部分。

### 11.1 `formal_issue_count` 回到仓库唯一权威口径

**问题**：``partition_findings`` 的第一桶是**展示**分组（把 ``info`` 拆到另一栏），
而当时的 ``formal_issue_count = len(formal)``。于是同一份分析出现两个问题数：

| 读取方 | 口径 | 结果 |
| --- | --- | --- |
| 审核工作台 / 质量门禁 / ``evidence_guard`` | ``is_formal_finding``（未降级即正式，``error``/``warn``/``info`` 都算） | error + warn + info |
| 材料详情（修复前） | ``len(正式问题那一栏)`` | error + warn |

**修法**：新增 ``count_canonical_formal_findings(ai_findings, rule_findings)``，
把已落库的两列拼成 ``count_formal_findings`` 认识的结果形态后**直接调用**该权威函数；
``_build_analysis`` 改用它，不再用 ``len(formal)``。
三个展示分组保持原样（正式问题 / 需人工核验 / 信息提示）——
**展示分组不重新定义业务真值**，只是把同一批正式 finding 按严重度拆开展示。

因此现在恒有：

```
formal_issue_count == len(formal_findings) + len(info_findings)
formal_issue_count == count_formal_findings({ai_findings, rule_findings})
```

**文案同步**：页面顶部总计由「正式问题：N」改为「**正式检查记录：N**」，
并补一句说明「包含正式问题与信息提示；证据不足被降级的待人工核验项不计入」——
否则用户会把"总计"与下面「信息提示」那一栏看成两回事。
概览页的 KPI 标签同步改为「正式检查记录」。
**API 字段名 ``formal_issue_count`` 未改**（本轮保持兼容）。

### 11.2 处理记录不再回显任何原始 `error_message`

**问题**：``_safe_error_summary`` 原实现用一份"坏形态黑名单"
（``c:\`` ``d:\`` / ``/opt/`` ``/home/`` ``/var/`` / 连接串 / Traceback）决定是否脱敏，
并**放行其余文本**。这条路的漏洞是原理性的：

- 黑名单补不完：``E:\`` ``F:\`` ``Z:\``、``/tmp/`` ``/mnt/`` ``/usr/`` ``/srv/``、
  UNC ``\server\share`` —— 而本项目开发环境本身就在 ``E:\Software Development\...`` 下，
  不是理论风险；
- 错误文本里可能出现的敏感信息远超"路径"：连接串、URL、token、用户名、数据库名、
  内部 host、环境变量值、文件名、业务数据片段；
- "看起来安全的错误"与"不安全的错误"没有稳定分类标准，部分脱敏只是在赌下一版错误消息。

**修法（fail-closed）**：

```python
def _safe_error_summary(value):
    if not str(value or "").strip():
        return None
    return "处理失败（详情见任务日志）"
```

只要数据库里存在非空 ``error_message``，普通材料详情一律返回**这一条固定文案**；
没有错误（``None``/空串/纯空白）返回 ``None``，不把"没失败"渲染成"处理失败"。
完整错误仍保留在任务日志、运维界面与数据库里，**没有信息丢失**。
本轮**不做**部分脱敏（不新增 sanitize 函数后再回显剩余原文），
也不在页面上新增「查看日志」按钮（日志权限属于后续运维/权限设计）。

### 11.3 新增测试

| 文件 | 新增 | 内容 |
| --- | --- | --- |
| `tests/test_material_ledger_detail_closure.py` | 47 | 口径对照（多样本与权威函数逐一比对、info 计数但单独展示、降级不计入、"只有 info"时计数不为 0）、`_safe_error_summary` 的 16 类原文样本与 13 个泄露片段反向断言、空值返回 None、不做部分脱敏 |
| `tests/test_material_ledger_detail_api.py` | +16 | `test_runs_api_returns_only_fixed_safe_error_summary`（14 个样本参数化，断言响应体任意位置不含原文片段）、`test_runs_api_error_summary_is_null_when_there_is_no_error`、`test_slot_detail_formal_issue_count_equals_canonical_count` |
| `app/tests/materialDetailAdapters.test.ts` | +4 断言（111 → 115） | 前端契约层断言"总计 = 正式问题栏 + 信息提示栏"，且"只有 info 时正式问题栏为空但计数不为 0" |
| `e2e/tests/material-ledger-detail.spec.ts` | +2（13 → 15） | 顶部「正式检查记录」= 两栏之和（并核对三栏条数）、失败运行只显示固定安全文案且无"查看日志"入口 |

### 11.4 变异验证（收口项）

| # | 变异 | 结果 |
| --- | --- | --- |
| A | `formal_issue_count` 改回 `len(formal)` | **CAUGHT**（5 failed） |
| B1 | `_safe_error_summary` 改回 `return first_line` | **CAUGHT**（47 failed） |
| B2 | `_safe_error_summary` 改回**原始黑名单实现** | **CAUGHT**（20 failed） |

B2 是最有价值的一条：它证明新增的安全反例**确实能抓住被 Review 指出的原始缺陷**
（``E:\`` 与 ``/tmp/`` 会漏过旧黑名单）。还原后全绿（134 passed）。

### 11.5 收口后的实测数字

| 项 | 结果 |
| --- | --- |
| 本地 `python -m pytest -q`（Windows） | **1689 passed, 43 skipped**（收口前 1626/43；新增 63 条） |
| `python -m ruff check .` | All checks passed |
| `python -m mypy api src tests` | Success: no issues found in 226 source files |
| `npm --prefix app run test:unit` | 全绿（`test:material-detail` 115 断言，收口前 111） |
| `npm --prefix app run build` | 通过 |
| 全仓 E2E | **171 passed, 13 skipped**（13 = 两套截图采集，默认跳过） |
| material-ledger E2E（WP2-A + WP2-B + 两套截图 spec） | **34 passed, 13 skipped**（WP2-B 用例 13 → 15） |
| PostgreSQL（pg + detail_pg） | **16 passed** |
| `check_coverage_baseline.py --assert-gaps 8` | 通过（**仍 8 gaps**） |
| `SCHEMA_CHANGE_REQUIRED` | **NO**（本轮未新增 migration） |

WP1 底座（migration 0019 / `material_slot_service` / `material_slot_resolver` /
`material_slot_status` / slot identity / cleanup protection / allocation ordering）
与 WP2-A 的 access scope 语义均未修改；WP3、WP9、`backfill --apply` 未触碰。
