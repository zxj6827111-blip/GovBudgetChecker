# 材料台账 WP2-A 交付说明（首页 + 区级矩阵 + 部门矩阵）

- 日期：2026-09-21
- 分支：`feat/material-ledger-ui-wp2a`
- 基线：`main` @ `61250e398169a379740a8cf8af2ac5e5f1a48615`
  （PR #42「材料台账底座 WP0+WP1」已合并，合并方式为普通 merge，未 squash）
- 本批 PR：#43（`feat/material-ledger-ui-wp2a`），交付 HEAD 见 PR 页面；CI 绿（7m6s）
- 对应范围：WP2-A（**只做**首页 / 区级主管部门矩阵 / 部门材料矩阵 + 三个只读 API）

本轮不包含：WP2-B（单位多年度时间轴、材料详情、检查覆盖、版本与来源、缺失态、处理记录）、
WP2-C（全局搜索、Ctrl/Cmd+K、History 导航收口）、WP3（复核生命周期）、WP9（应收材料基线、
CSV/Excel 初始化、官网采集）、`backfill --apply`。

---

## 1. 结论

| 项 | 结果 |
| --- | --- |
| 三个 Materials 只读 API | ✅ 可用（`/api/materials/coverage`、`/api/materials/districts/{id}/departments`、`/api/materials/departments/{id}/matrix`） |
| 统一响应契约 | ✅ `{ok, data, meta}`，meta 含 `data_basis` / `expected_materials_ready` / `generated_at`（列表另带 `pagination`） |
| 状态来源 | ✅ 只有 WP1 一套（`material_slots.status` 的 10 个取值），WP2 未新增第二套业务状态 |
| status_reason | ✅ 原样透出 WP1 原因码，中文映射只在 `app/lib/materialStatusPresentation.ts` 一处 |
| `null` / `0` / `[]` | ✅ 语义分离：无法计算一律 `null`，真实零一律 `0`，空集合一律 `[]` |
| 同上名部门/本部单位隔离 | ✅ 全程按 `subject_org_id` 计数，无任何按名称归并 |
| budget / final / fiscal_year / district / department 隔离 | ✅ 各有专门用例（假连接 + 真库两层） |
| 权限 | ✅ 服务端判定：401（未登录）/ 403（越权、无授权范围）/ 404（未知区划或部门）/ 422（参数非法）/ 503（数据库不可用） |
| department / unit 授权 | ✅ 已接通：可见范围按 `user_can_access_org` 逐节点判定，SQL 三列 OR 过滤；上级区县/部门仅作导航容器 |
| 同名部门 / 同名本级单位 | ✅ 展示归类与前端 `unitMatch.ts` 统一：归一化同名或带本级/本部标志 → 本部单位 |
| 未到期 vs 截止时间未知 | ✅ 新增 `not_due_confirmed`（`due_not_reached`）；`status_counts.not_due` 语义不变，两者并列展示 |
| 不扫文件系统、不用 `/api/jobs` 前端聚合 | ✅ 数据全部来自 `material_slots` 聚合查询 |
| SQL 条数 | ✅ 首页 2 条、区级矩阵 1 条、部门矩阵 1 条（无 N+1） |
| 页面 06/07/08 | ✅ `/materials`、`/materials/district/[districtId]`、`/materials/department/[departmentId]?year=` |
| 06→07→08 E2E | ✅ 19 条 e2e 全绿（含 5 条截图采集） |
| 全量 pytest / ruff / mypy | ✅ 见 §9.1 |
| CI（GitHub Actions `test-and-build`） | ✅ 绿 |
| coverage baseline | ✅ 仍 8 gaps（`--assert-gaps 8` 通过） |
| WP1 schema / Golden / rules / obligations | ✅ 未修改 |
| WP3 / WP9 / backfill apply | ✅ 未触碰 |

`WP1_SCHEMA_BLOCKER = NO`

---

## 2. 新增 / 修改文件

**后端**

| 文件 | 说明 |
| --- | --- |
| `src/schemas/material_ledger.py`（新增） | 三个接口的统一 Pydantic 契约与常量（`DATA_BASIS_EXISTING_SLOTS_ONLY`、`EXPECTED_MATERIALS_READY`、`MaterialStatusCounts`、`MaterialSlotSummary` 等） |
| `src/services/material_ledger_query_service.py`（新增） | 只读查询服务：筛选 → 聚合 → 分页。**不含任何写入**（不建槽、不绑版本、不改状态、不做人工映射） |
| `api/routes/materials.py`（新增） | 三个 GET 接口 + 服务端鉴权 + 授权范围过滤 |
| `api/routes/__init__.py`（修改） | 注册 materials router |

**前端**

| 文件 | 说明 |
| --- | --- |
| `app/lib/materialStatusPresentation.ts`（新增） | 状态/原因/主体层级/材料范围/文种/口径/关系的**唯一**中文与色调契约，以及口径提示文案 |
| `app/app/components/materials/materialLedgerAdapters.ts`（新增） | 响应类型、KPI 折算、行映射、分组、筛选与年度校验、响应契约校验（纯逻辑，可 jiti 直测） |
| `app/app/components/materials/useMaterialResource.ts`（新增） | 取数钩子（加载/错误/空三态、过期响应丢弃、无本地兜底数据） |
| `MaterialStatusBadge.tsx` / `MaterialStatusReason.tsx` / `MaterialDataBasisNotice.tsx` / `MaterialSlotCard.tsx` / `MaterialFilterBar.tsx` / `MaterialBreadcrumb.tsx`（新增） | 共用展示组件 |
| `MaterialLedgerPage.tsx` / `DistrictDepartmentMatrixPage.tsx` / `DepartmentMatrixPage.tsx`（新增） | 页面 06 / 07 / 08 |
| `app/app/(workspace)/materials/**/page.tsx`（新增） | 三个路由入口（`(workspace)` 路由组，URL 不含括号段） |
| `app/app/api/materials/**/route.ts`（新增） | 三个 Next 代理路由（带会话令牌转发、原样透传状态码；**无本地兜底数据**） |
| `app/app/components/workspace/nav.ts`（修改） | 工作区组追加第 7 项「材料台账」（→ `/materials`）；「任务历史」保留，WP2-C 再移除 |
| `app/app/components/ui/Badge.tsx`（修改） | 追加可选 `data-testid`（与 Card/Metric 的既有约定一致，不影响样式） |

**测试与证据**

| 文件 | 说明 |
| --- | --- |
| `tests/test_material_ledger_query_service.py`（新增） | 纯逻辑：筛选、状态分桶、排序、代表槽位选择、关系推导 |
| `tests/test_material_ledger_api.py`（新增） | 接口契约、隔离、鉴权、分页、校验、SQL 条数 |
| `tests/support_material_ledger_db.py`（新增） | 会真过滤、真 GROUP BY、按 SELECT 投影的假连接 |
| `tests/test_material_ledger_pg.py`（新增，opt-in） | **真实 PostgreSQL** 上的三个聚合查询与 DTO 验证（独立 schema，测后自删） |
| `tests/test_material_slot_api_regression.py`（修改） | WP1 的路由面冻结清单：族匹配由子串改为前缀（否则 `/api/materials/districts/{id}/departments` 会被当成"部门接口族多了一条"），并把材料接口面从"空集"改为"恰好三条只读 GET" |
| `app/tests/materialLedgerAdapters.test.ts`（新增） | 状态/原因映射、null vs 0、KPI 折算、exists=false 文案、响应契约校验 |
| `app/tests/workspaceNavigation.test.ts`（修改） | 导航由 9 项 → 10 项（工作区组 6 → 7），并锁定材料台账入口不是管理员专属 |
| `app/tests/hardcodedColorGuard.test.ts`（修改） | 把 `app/components/materials` 纳入硬编码颜色扫描（80 个文件 0 违规） |
| `e2e/tests/material-ledger.spec.ts`（新增） | 06→07→08 全链路 + 四态 + 红线反例 |
| `e2e/tests/material-ledger.screenshots.spec.ts`（新增，opt-in） | 截图采集（`GBC_CAPTURE_SCREENSHOTS=1`） |
| `e2e/tests/workspace-navigation.spec.ts`（修改） | 路由清单 9 → 10 |
| `scripts/run-e2e.cjs`（修改） | 预热路径补三个材料台账路由（`next dev` 首访编译成本移出用例预算） |

---

## 3. API 契约

### 3.1 统一响应包

成功：

```json
{
  "ok": true,
  "data": { },
  "meta": {
    "data_basis": "existing_slots_only",
    "expected_materials_ready": false,
    "generated_at": "2026-09-21T12:00:00Z"
  }
}
```

列表接口的 `meta` 追加：

```json
{ "pagination": { "page": 1, "page_size": 50, "total": 0, "total_pages": 0 } }
```

失败沿用仓库既有 FastAPI 错误契约（`{"detail": "..."}`），**未**为 Materials 另发明格式。

### 3.2 `GET /api/materials/coverage`

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `fiscal_year` | int? | 2000–2099；不传=不限年度（**不猜当前年**） |
| `report_kind` | `all`\|`budget`\|`final` | `all` 只是过滤参数，不是数据库状态 |
| `status` | MaterialStatus? | 非法值 → 422 |
| `jurisdiction_id` | str? | 越权 → 403 |

`data.summary`（统一统计对象）：

```json
{
  "slot_total": 13,
  "status_counts": { "not_due": 1, "missing": 1, "uploaded": 3, "processing": 1,
                     "review_required": 1, "reviewing": 1, "completed": 1,
                     "not_applicable": 1, "mapping_required": 2, "failed": 1 },
  "budget_total": 9, "final_total": 2, "unknown_kind_total": 2,
  "due_at_unknown": 5, "not_due_confirmed": 1, "jurisdiction_unknown_total": 1,
  "expected_total": null, "expected_budget_total": null, "expected_final_total": null,
  "coverage_rate": null, "budget_coverage_rate": null, "final_coverage_rate": null,
  "missing_expected_total": null
}
```

`data.districts[]` 与 summary 同形（少 `jurisdiction_unknown_total`，因为它只对全局有意义），
按 `district_name`（Unicode 码点）+ id 兜底稳定排序。

**四个刻意保留的口径出口**：`unknown_kind_total`（文种未识别/冲突的槽位，既不进预算也不进决算）、
`due_at_unknown`（截止时间未知，不得被读成"尚未到期"）、`jurisdiction_unknown_total`
（没有行政区划的槽位，不变成区县卡片，但也**不会凭空消失**）、
`not_due_confirmed`（已确认尚未到截止时间；与 `status_counts.not_due` 并存，见 §3.6）。

### 3.3 `GET /api/materials/districts/{district_id}/departments`

参数：`fiscal_year?`、`report_kind`、`status?`、`q?`（主管部门名称子串）、`page>=1`、`1<=page_size<=100`（默认 50）。

```json
{
  "data": {
    "district": { "district_id": "...", "district_name": "上海市普陀区" },
    "items": [{
      "department_id": "...", "department_name": "...",
      "subject_count": 10, "slot_total": 10,
      "status_counts": { "...": 0 },
      "budget": { "slot_total": 8, "status_counts": { "...": 0 } },
      "final": { "slot_total": 1, "status_counts": { "...": 0 } },
      "unknown_kind_total": 1, "missing": 1, "not_due": 1, "due_at_unknown": 4,
      "updated_at": "2026-09-21T12:00:00Z",
      "expected_total": null, "coverage_rate": null, "...": null
    }]
  }
}
```

- `department_id` **允许为 null**：区级政府本级材料（`department_org_id IS NULL`）真实存在于该区，
  若把它排除，区级矩阵的槽位总数就会与首页该区的总数对不上——那才是真正会误导人的不一致。
  这类行在界面上显示为「未归入主管部门（区级政府本级材料）」，不可下钻。
- `q` 作用在**聚合之后**的显示名上（显示名以组织目录为准，槽位里只是名称快照）。

### 3.4 `GET /api/materials/departments/{department_id}/matrix`

参数：`fiscal_year`**必填**（不传 → 422；**不**回退到当前自然年）。

```json
{
  "data": {
    "department": { "department_id": "...", "department_name": "...",
                    "jurisdiction_id": "...", "jurisdiction_name": "上海市普陀区" },
    "fiscal_year": 2025,
    "groups": {
      "department_summary": [], "head_unit": [], "subordinate_units": [],
      "relationship_unknown": []
    }
  }
}
```

每个主体行：

```json
{
  "subject_org_id": "...", "subject_org_name": "...",
  "subject_kind": "unit", "relationship": "head_unit",
  "budget": { "exists": true, "slot": { "slot_id": "...", "status": "uploaded", "status_reason": "awaiting_analysis", "...": "..." } },
  "final": { "exists": false, "slot": null },
  "unclassified": { "exists": false, "slot": null }
}
```

- `exists=false` 的语义**只有一条**：当前 `material_slots` 里没有这条槽位。
  界面上对应文案只有「尚无已建立材料」，**不**显示"缺失/逾期/未上传"。
- `unclassified` 是为 `report_kind='unknown'`（文种冲突/未识别）准备的第三个位置。
  协议里只给 budget/final 会把这些材料从部门页上抹掉，而它们恰恰最需要人工确认。
- 同一（主体 × 文种）下若有多条槽位，取哪一条由 `_slot_preference` 单一规则决定：
  身份已确认（`mapping_key=''`）优先 → 更新时间较新优先 → `slot_id` 兜底。
  **SQL 刻意不做排序**，避免两处规则漂移。

### 3.5 「未到期」的两个口径（`not_due` vs `not_due_confirmed`）

| 字段 | 含义 | 用途 |
| --- | --- | --- |
| `status_counts.not_due` | 状态机事实：停在 `not_due` 的槽位总数（**含**"截止时间未知"） | 保留不变，供排查/对账 |
| `not_due_confirmed` | `status='not_due' AND status_reason='due_not_reached'` | 界面"未到期"只取这个字段 |
| `due_at_unknown` | `due_at IS NULL` 的数据质量指标（可能与任意状态并存） | 单独展示为"截止时间未知" |

三者由 SQL 直接计算（分组查询里 `COUNT(*) FILTER (WHERE status = 'not_due' AND
status_reason = 'due_not_reached')`），**不允许**在前端用 `not_due - due_at_unknown` 相减：
`due_at_unknown` 与 `uploaded`/`review_required`/`completed` 等状态也可能并存，相减得不到正确答案。

### 3.6 时间 / ID / 布尔 / 空值

- 时间：一律 ISO 8601、UTC、以 `Z` 结尾（`2026-09-21T11:42:00Z`）；前端负责本地化显示。
- ID：`slot_id` / `jurisdiction_id` / `department_id` / `subject_org_id` / `slot_key` 全部字符串；
  统一叫 `slot_id`，不存在 `id` / `material_id` / `material_slot_id` 三种叫法。
- 布尔：真布尔（`true`/`false`），不使用 `0/1`、`"true"`。
- 空值：不存在/无法计算 → `null`；空集合 → `[]`；真实零 → `0`。
  `expected_*` / `*_coverage_rate` 这七个字段在 `expected_materials_ready=false` 时恒为 `null`
  （显式出现，正是为了让"无法计算"在协议层只有一种表达）。

---

## 4. 权限

| 角色 | 行为 |
| --- | --- |
| 未登录 | 401（`require_login`） |
| 管理员 | 不加权限条件（`visible_org_ids=None`） |
| 授权区县 / 部门 / 单位用户 | 首页只统计自己可见的槽位；可打开所属区县/部门页面作**导航容器**，但容器内的行仍按可见范围过滤 |
| 无任何授权组织的账号 | 首页 → **403**（明确拒绝，而不是返回一片空白让人以为"确实没有材料"） |
| 越权访问别的区县/部门 | **403**（容器只覆盖"自己所属的上级"，不覆盖其它分支） |
| 未知区划/部门 | 组织目录与库中都查不到 → **404**；"存在但当前筛选为空"仍是 200 + 空态 |
| 无法证明归属的槽位（无区划、占位主体） | 非管理员不可见（fail-closed）；管理员可见 |

权限判定复用仓库既有 `user_can_access_org`（已授权节点可访问其后代），**未另写一套规则**。
两个集合职责严格分开：

- `visible_org_ids`（能看到哪些**数据**）= 组织目录中所有 `user_can_access_org(user, id)` 为真的节点；
  SQL 条件为 `(jurisdiction_org_id = ANY(...) OR department_org_id = ANY(...) OR subject_org_id = ANY(...))`，
  三列是 **OR**（写成 AND 会把 department / unit 授权全部误杀）；
- `container_org_ids`（能打开哪些**页面**）= 可见节点的祖先闭包。

**容器可打开 ≠ 容器下数据可见**：只放开容器而不加范围过滤，会从"过度拒绝"直接变成"越权泄露"。
本轮实测就先踩到一次——区县查询重建筛选条件时漏传 `visible_org_ids`，
授权部门的账号打开父区县页后能看到同区其它部门（见 §9.4 第 7 条）。

服务端强制，与前端是否隐藏入口无关。

---

## 5. 性能

| 接口 | SQL 条数 | 说明 |
| --- | --- | --- |
| `/api/materials/coverage` | 2 | 全局汇总 1 条 + 区县分组 1 条（同一组 WHERE 条件） |
| `/api/materials/districts/{id}/departments` | 1 | 按（部门 × 主体 × 文种 × 状态）分组，一条 SQL 同时算出部门槽位总数与覆盖主体数 |
| `/api/materials/departments/{id}/matrix` | 1 | 按（部门 + 年度）取明细，Python 侧归并 |

- 无 N+1（不逐区县/部门/主体/Slot 查询）；
- 不扫 `uploads/`、不读 job 文件、不做 `/api/jobs` 全量后前端聚合；
- 首页的全局汇总**不由区县行相加**得到（那样会漏掉没有行政区划的槽位），而是独立一条聚合。

---

## 6. 页面

### 页面 06 `/materials`（材料台账首页）

顶部固定口径提示（文案随 `meta.expected_materials_ready` 切换）→ 筛选（年度/文种/状态）→
8 张 KPI（已建材料 / 已上传 / 待复核 = review_required + reviewing / 已完成 / 待确认归属 /
处理失败 / 未到期 / 逾期未上传）→ 区县卡片（预算槽位、决算槽位、完整率、待复核、待确认归属、逾期未上传、更新时间）。

`not_applicable` 不并入任何"缺失"类口径。

### 页面 07 `/materials/district/[districtId]`

面包屑（材料台账 / 区县）→ 筛选 + 名称搜索 → 表格：
主管部门 / 预算槽位 / 决算槽位 / 待复核 / 待确认归属 / 处理失败 / 逾期未上传 / 未到期 / 完整率 / 更新时间 / 操作。
完整率在无应收基线时显示 `—`（tooltip：应收材料基线尚未建立）。

未选年度时行内提示「请先选择财政年度」而**不给**一个注定 422 的下钻链接（部门矩阵年度必填）。

### 页面 08 `/materials/department/[departmentId]?year=2025`

面包屑（材料台账 / 区县 / 部门）→ 年度输入（必填）→ 三个固定分组
（部门汇总 / 本部单位 / 直属单位）+「关系待确认」（仅在真有这类材料时出现）→
每个主体一行：主体 / 预算 / 决算 / 文种待确认。

每个槽位卡显示：状态徽章 + 状态原因 + 财政年度 + 文种 + 口径 + 更新时间；
`caliber_conflict_candidate != null` 时额外显示「口径冲突待确认：当前为…，另一次识别为…」。

缺年度时页面提示选择年度，**不发请求**（浏览器层已由 e2e 断言）。

---

## 7. 状态与原因映射

后端只给机器可读的 `status` / `status_reason`；中文只在
`app/lib/materialStatusPresentation.ts` 一处定义，三个页面共用（不存在页面各自硬编码颜色）。

| status | 中文 | tone | 图标 |
| --- | --- | --- | --- |
| `not_due` | 未到期 | neutral | clock |
| `missing` | 逾期未上传 | failed | alert |
| `uploaded` | 已上传 | processing | check |
| `processing` | 处理中 | processing | loader |
| `review_required` | 待人工复核 | review | user-check |
| `reviewing` | 复核中 | review | user-check |
| `completed` | 已完成 | done | check |
| `not_applicable` | 不适用 | neutral | slash |
| `mapping_required` | 待确认归属 | lowconf | help |
| `failed` | 处理失败 | failed | x |

`tone` 表达的是**关注等级**而非语义本身：`missing` 与 `failed` 同为最高关注等级（红），
靠文案区分；两个状态在协议层、DTO 字段、页面文案上完全分离，不会混为一类。

原因码（§九 要求的十个全部登记，另外把 WP1 状态机实际会写入的码也一并登记）：

| reason | 中文 |
| --- | --- |
| `due_at_unknown` | 截止时间未知 |
| `caliber_conflict` | 材料口径存在冲突 |
| `slot_binding_conflict` | 文件版本已绑定其他材料 |
| `organization_missing` | 缺少主体信息 |
| `organization_unresolved` | 主体无法确认 |
| `organization_level_unknown` | 主体层级无法确认 |
| `kind_conflict` | 预算/决算文种存在冲突 |
| `year_conflict` | 财政年度存在冲突 |
| `kind_unknown` | 文种无法识别 |
| `year_unknown` | 财政年度无法识别 |
| `due_not_reached` / `due_exceeded` | 尚未到公开截止时间 / 已过截止时间且未上传 |
| `applicability_unresolved` / `applicability_marked_not_applicable` | 是否应当有这份材料尚未确认 / 已人工确认本年度无此材料 |
| `identity_unresolved` | 年度/文种/主体映射待确认 |
| `awaiting_analysis` / `analysis_running` / `analysis_failed` | 已上传，等待分析 / 分析进行中 / 分析失败，需重试或人工处理 |
| `findings_pending` / `review_in_progress` / `review_completed` | 存在待人工处理的问题 / 人工复核进行中 / 人工复核已完成 |
| `slot_version_missing` | 文件版本不存在 |

未知原因码**原样显示**为 `待确认：<code>`，不退化成空字符串。

> **重要限制（如实标注）**：归属判定（resolver）的细分原因码
> （`year_unknown` / `kind_conflict` / `organization_*` 等）目前**没有落库**——
> `material_slots.status_reason` 只由 WP1 状态机写入。因此本轮界面上
> `mapping_required` 的槽位实际显示的是 `identity_unresolved`（"年度/文种/主体映射待确认"）。
> 上述映射已在共享契约中建立并由单测覆盖，等 WP2-B/WP9 提供来源（例如把 resolver decision
> 详情落到槽位或详情接口）后即可直接生效，界面无需再改文案。

---

## 8. `relationship` 的推导（最终行为）

`relationship` 是**纯展示字段**，不落库、不制造新的业务真值：

1. `subject_org_id == department_id` → `department_summary`（部门汇总材料）；
2. `subject_kind == 'unit'` 且命中"部门本级"证据 → `head_unit`：
   - **归一化后单位名 == 部门名**（`上海市普陀区财政局` 的部门与本级单位可以完全同名），或
   - 单位名带「本级 / 本部」标志（含 `（本级）`/`（本部）` 写法）；
3. `subject_kind == 'unit'` 但无本级证据 → `subordinate_unit`；
4. 主体层级认不出来但材料范围写着部门汇总 → `department_summary`；
5. 其余（含"主体是单位、材料范围却写着部门汇总"这类字段互相矛盾）→ `relationship_unknown`。

归一化口径与前端 `app/lib/unitMatch.ts` 的 `normalizeOrgName` 一致：去空白、去全半角括号、
去「本级/本部」标志。Python 侧不 import TypeScript，但语义必须相同——两处判定不一致会让
"上传中心把某个单位标成本级、材料台账把它标成直属"这种矛盾出现在同一份数据上。

**局限（仍然存在，但已收口）**：组织目录没有"是否本级"的显式字段，名称仍是唯一信号；
判定只用于展示归类，**身份始终按 `subject_org_id`**，`slot_key` / `SlotIdentity` /
`material_scope` / migration 0019 均未改动。WP9 引入组织主数据后应改为显式字段。

---

## 9. 测试与验证

### 9.1 后端

```bash
python -m pytest -q                                  # 1555 passed, 35 skipped, 0 failed
python -m ruff check .                               # All checks passed
python -m mypy api src tests                         # Success: no issues found in 219 source files
python scripts/check_coverage_baseline.py --json
python scripts/check_coverage_baseline.py --assert-gaps 8   # 通过（仍 8 gaps）
```

（33 skipped 为默认跳过的真库/AI/前端依赖用例，与 WP1 基线一致。）

本轮新增用例：

| 文件 | 条数 | 覆盖 |
| --- | --- | --- |
| `tests/test_material_ledger_query_service.py` | 38 | 筛选参数与 `$n` 顺序、10 状态分桶、未知状态报错、文种拆分、无区划计数、区县/部门稳定排序、同名隔离、关系推导（含矛盾→待确认）、代表槽位优先级、`formal_issue_count=null`、空矩阵 |
| `tests/test_material_ledger_api.py` | 57 | 空库零值、全枚举状态、budget/final 与年度/区划/部门隔离、分页契约、非法参数 422、404 与空态区分、403/401、SQL 条数、`MAX(updated_at)` 回归、期望字段恒 null、ISO 时间 |
| `tests/test_material_ledger_pg.py`（opt-in） | 8 | **真库**：三条聚合 SQL 可执行、NULL 分组行为、`ANY($n::text[])` 空数组、UUID→text、TIMESTAMPTZ→tz-aware、分页/关键词、代表槽位选择 |

**真库验证发现并修掉的缺陷**：区县分组的 SQL 最初漏了 `MAX(updated_at)`，
区县卡片的"更新时间"会恒为 `null`（前端显示 `—`）。已补 SQL，并在假连接层补了同口径的快速回归用例。

**变异验证**（确认守护型断言真的能拦住缺陷，方法：改坏实现 → 观察用例变红 → 还原）：

| 变异 | 结果 |
| --- | --- |
| 首页去掉授权范围过滤（`visible_org_ids=None`） | 被 `test_authorized_district_user_sees_only_own_district` 抓住 |
| 容器访问改为恒放行 | 被 `test_out_of_scope_container_still_returns_403` 抓住 |
| 区县查询重建筛选时丢 `visible_org_ids`（真实踩到的缺陷） | 被 `test_department_scope_can_open_parent_district_with_filtered_rows` / `test_unit_scope_parent_district_does_not_leak_siblings` 抓住 |
| `formal_issue_count` 从 `None` 改成 `0` | 被 `test_department_matrix_formal_issue_count_is_null` 抓住 |
| `head_unit` 判定退化为固定 `subordinate_unit` | 被同名与本级用例抓住 |
| "未到期"改回读 `status_counts.not_due` | 被前端 `materialLedgerAdapters.test.ts` 的反例与 e2e 抓住 |

### 9.2 前端

```bash
npm --prefix app run test:unit    # 19 个 jiti 单测脚本全绿（含新增 materialLedgerAdapters）
npm --prefix app run build        # 生产构建通过（新增 3 个路由）
```

### 9.3 E2E

```bash
E2E_BASE_URL=http://127.0.0.1:3100 npm --prefix app run test:e2e -- material-ledger
```

结果：**24 passed**（19 条功能用例 + 5 条截图采集），另有既有导航用例同步更新（9 项 → 10 项）。

其中本轮新增：`due_at_unknown` 与"未到期"分列可见（首页 KPI / 汇总行 / 区县卡片 / 区级矩阵）、
完全同名的本级单位落「本部单位」而不落「直属单位」、受限授权范围（部门 / 单位）下的行数与数字渲染。

> 端口说明：本机 3000 端口被另一个 Next 应用占用，`run-e2e.cjs` 的"服务已就绪"探测会把那个应用
> 当成我们的 dev server（它的 404 也是 `< 500`），导致全部用例打到错误的站点。
> 因此本地跑 e2e 需要显式指定一个空闲端口（例如 `E2E_BASE_URL=http://127.0.0.1:3100`）。
> 这是环境问题，未改动仓库默认端口。

功能用例覆盖：首页四态（加载/正常/空/错误+重试）、KPI 口径（待复核 = 待复核 + 复核中）、
区县卡片完整率显示 `—` 且不出现 `0%`、年度与文种筛选真的进入查询串、非法年度不参与筛选、
06→07→08 全链路、三个分组齐备、预算/决算不串位、
**`exists=false` 显示「尚无已建立材料」且不含"缺失/逾期/未上传"字样**、
待确认归属同时显示原因、缺年度时提示且不发请求、部门矩阵空态、区级矩阵空态与错误态可分、面包屑逐级返回。

### 9.4 验证过程中发现并修掉的问题

| # | 问题 | 发现方式 | 处理 |
| --- | --- | --- | --- |
| 1 | 区县分组的 SQL 漏了 `MAX(updated_at)`，区县卡片的"更新时间"会恒为 `null`（界面显示 `—`） | **真库用例**（假连接不会替 SQL 补列） | 补 SQL + 假连接支持 `MAX(updated_at)`（按时间比较而非字符串）+ 两条回归用例 |
| 2 | 异常消息里拼了运行时值（`f"...{value!r}"`），命中仓库的日志安全门禁（规则 4） | 全量 pytest | 消息不带运行时值，原始状态码改由异常属性 `status_code` 透出（属性不参与 `__str__`，排障信息不丢），单测同时断言属性值与"消息里不含该值" |
| 3 | WP1 的"路由面冻结"用**子串**匹配族名，`/api/materials/districts/{id}/departments` 因含 `/departments` 被误判为"部门接口族多了一条" | 全量 pytest | 族匹配改为**前缀**（并在清单里写明原因）；材料接口面从"空集"改为"恰好三条只读 GET"，继续拦住"顺手多加接口" |
| 4 | 本机 3000 端口被另一个 Next 应用占用，e2e 的"服务已就绪"探测把那个应用当成 dev server（404 也 `< 500`），14 条用例全打到错误站点 | 首次 e2e 全红 + 页面快照是别的产品 | 本地显式指定 `E2E_BASE_URL=http://127.0.0.1:3100`；**未改仓库默认端口**，只在本文档记录该环境坑 |
| 5 | 截图相对路径被 Playwright 按进程工作目录解析，落到了仓库外 | 截图目录为空 | 改为 `path.resolve(__dirname, ...)` 的仓库内绝对路径 |
| 6 | 区级矩阵 11 列在 1600 宽视口下表头折行、右列被横向滚动挤出视口 | 人工看截图 | 表头不折行 + 表头用 §三十五 的短名（完整含义放 `title`）+ 表格 `min-w` + 截图视口取 1920 |
| 7 | **权限泄露（独立 Review 收口时抓到）**：区县查询重建筛选条件时漏传 `visible_org_ids`，授权部门/单位的账号一旦打开父区县页就能看到同区其它部门 | 新增的容器 + 范围用例（`test_department_scope_can_open_parent_district_with_filtered_rows`）直接变红 | 重建筛选时把权限范围一并带过来，并在代码里写明"容器放开 ≠ 范围放开" |
| 8 | 受限账号首页 403（department / unit 授权被错误折算成区县） | 独立 Review 指出 + 新增 10 条 RBAC 用例 | 改为按组织目录逐节点判定可见集合，SQL 三列 OR 过滤 |

### 9.5 截图（可视化证据）

`docs/wp2a-screenshots/`（由 `GBC_CAPTURE_SCREENSHOTS=1` 的 e2e 用例生成）：

1. `01-ledger-home.png` —— 首页：口径提示 + 8 张 KPI + 区县卡片
2. `02-district-matrix.png` —— 区级主管部门矩阵（完整率 `—`）
3. `03-department-matrix.png` —— 部门材料矩阵三分组 + 状态/原因/口径冲突
4. `04-ledger-empty.png` —— 首页空态（0 槽位 + 口径提示"不代表不存在应收材料"）
5. `05-department-need-year.png` —— 部门矩阵缺年度提示

> 截图数据由 e2e mock 提供，证明的是"给定后端响应，界面渲染成什么样"，
> **不代表线上数据库里就有这些材料**。视口 1920×1080（区级矩阵是 11 列宽表，
> 更窄的视口会把右侧"更新时间/操作"推进横向滚动区）。

---

## 10. 已知限制与未覆盖项（如实清单）

1. **应收材料基线未建立（WP9）**：只剩 `existing_slots_only` 一种口径。
   完整率、真实缺失数、应收总数全部为 `null`；`missing` 只可能来自"已建槽位 + 已到期 + 无当前文件"。
2. **resolver 细分原因未落库**（见 §7 的说明）：界面上 `mapping_required` 统一显示
   `identity_unresolved`，看不到"是年份没认出来还是文种冲突"。
3. **`relationship` 依赖组织名称**（见 §8）：同名或带「本级/本部」标志即判为本部单位，
   该判定是展示层推导，不入库、不能被别的系统引用；名称被人工改错时会跟着错（身份仍按 id）。
4. **`formal_issue_count` 恒为 `null`**：正式 finding 与当前文件版本的关联要等 WP3，
   本轮按 §二十六 返回 `null` 而**不是** `0`（`0` 会被读成"查过且没有问题"）。
5. **状态不会自动推进**：本轮只消费 WP1 已经写入的状态；"已上传 → 处理中 → 待复核"的
   自动接线属于 WP3。因此当前很多材料会长期停在 `uploaded`（这是真实状态，前端不得自行改写）。
6. **没有材料详情 / 版本 / 来源页**（WP2-B），因此列表里的槽位不可点击进入明细。
7. **没有全局搜索 / Ctrl/Cmd+K**（WP2-C）。
8. **真库验证仍需显式 opt-in**：`tests/test_material_ledger_pg.py` 默认跳过；
   未配置 `GOVBUDGET_TEST_DATABASE_URL` 时 CI 不覆盖真库行为（与 WP1 的约定一致）。
9. **未做大规模数据量压测**：SQL 条数与索引已按 WP1 的 `material_slots` 索引设计对齐，
   但没有在万级槽位数据上跑过实测耗时。
10. **区县/部门名称以组织目录为准**，组织被重命名（id 变化）时历史槽位会退回名称快照，
    极端情况下页面显示的是旧名或 id（不会 404，也不会猜一个新名字）。

---

## 11. 与后续批次的边界

| 批次 | 关系 |
| --- | --- |
| WP2-B | 在页面 07/08 之后追加单位多年度时间轴与材料详情；本轮的三条 API 与 DTO 是其基础，`MaterialSlotSummary` 可直接复用 |
| WP2-C | 全局搜索与导航收口；「任务历史」入口届时再移除 |
| WP3 | 复核生命周期：写入 `review_*` 状态、产出 `formal_issue_count`、让状态自动推进 |
| WP9 | 应收材料基线：新增 `data_basis=expected_materials`、填充 `expected_*` 与 `*_coverage_rate`、落地"真实缺失数" |

---

## 12. 复算方式

```bash
# 全量后端测试与静态检查
python -m pytest -q
python -m ruff check .
python -m mypy api src tests

# 覆盖基线（应仍是 8 个缺口）
python scripts/check_coverage_baseline.py --assert-gaps 8

# 真库验证（显式指定测试库；用随机 schema，测后自删）
GOVBUDGET_TEST_DATABASE_URL=postgresql://user:pass@host:5432/db \
    python -m pytest tests/test_material_ledger_pg.py -v

# 前端
npm --prefix app run test:unit
npm --prefix app run build
E2E_BASE_URL=http://127.0.0.1:3100 npm --prefix app run test:e2e -- material-ledger

# 截图（可选）
GBC_CAPTURE_SCREENSHOTS=1 E2E_BASE_URL=http://127.0.0.1:3100 \
    npm --prefix app run test:e2e -- material-ledger.screenshots
```

---

## 13. 最终独立 Review 收口（2026-09-21 第二轮）

独立 Review 在 PR #43 上提出三项收口要求，本轮逐项处理并补齐证据。
**未进入 WP2-B，未 merge PR #43**。

### 13.1 department / unit RBAC 已接通

- **问题**：首页与容器页只按"可访问的区县"折算权限。而 `user_can_access_org` 的语义是
  "授权节点可访问其后代"，不是"后代可反向访问祖先"——因此只授权 dept-A / unit-A1 的账号
  既打不开区县页（403），首页也是 403。这个语义在 `tests/test_org_scope_rbac.py` 里已固化
  （department scope 可访问子 unit；unit scope 不得访问 sibling），Materials 必须完全继承。
- **处理**：新增 `MaterialAccessScope`：
  - `visible_org_ids` = 组织目录里所有 `user_can_access_org(user, id)` 为真的节点（层级不限）；
    查询层加 `(jurisdiction_org_id = ANY(...) OR department_org_id = ANY(...) OR subject_org_id = ANY(...))`，
    三列 OR，管理员不加该条件；
  - `container_org_ids` = 可见节点的祖先闭包，**只**决定"页面能不能打开"；
    容器内的每一行仍由 `visible_org_ids` 过滤。
- **结果**：department scope → 首页 200 且只含本部门及后代；unit scope → 首页 200 且只含本单位；
  两者都能进上级容器页导航，但容器里不会出现 sibling 部门/单位；
  越权容器（别的区/部门）仍 403；无法证明归属的槽位对非管理员不可见（fail-closed）。

### 13.2 同名部门 / 同名本级单位的展示归类已统一

- **问题**：原先只判断名称是否带「本级/本部」后缀。真实形态里部门与其本级单位可以**完全同名**
  （部门「上海市普陀区财政局」/ 单位「上海市普陀区财政局」，id 与 level 不同），
  只靠后缀会把本级单位错判成直属单位。
- **处理**：`is_head_unit_name(unit_name, department_name)` 与 `relationship_of(..., department_name=...)`
  支持两条证据：归一化后同名，或带「本级/本部」标志；归一化口径与前端 `app/lib/unitMatch.ts`
  的 `normalizeOrgName` 一致（去空白、去全半角括号、去本级/本部标志）。
- **边界**：只改 **WP2 presentation relationship**。`slot_key` / `SlotIdentity` / `subject_org_id` /
  `material_scope` / migration 0019 **均未改动**，身份仍按 id，不会按名称合并数据。

### 13.3 `due_at_unknown` 不再被表述为确认"未到期"

- **问题**：`not_due` 状态同时覆盖"确实没到截止时间"与"截止时间未知"，界面上一律显示"未到期"
  等于把未知当已知。
- **处理**：SQL 层新增 `not_due_confirmed = COUNT(*) FILTER (WHERE status='not_due' AND
  status_reason='due_not_reached')`；三个 DTO 增加该字段；`status_counts.not_due` **语义不变**。
  界面"未到期"只取 `not_due_confirmed`，并把 `due_at_unknown` 作为独立列/独立条目展示
  （首页 KPI 行、区县卡片、区级矩阵新增「截止未知」列，表头 tooltip 写明两个口径的差别）。
  未做任何前端相减推算。
- **未改动**：`src/services/material_slot_status.py`（WP1 状态机）一行未动。

### 13.4 本轮验证结果

| 项 | 结果 |
| --- | --- |
| `python -m pytest -q` | 1555 passed, 35 skipped, 0 failed |
| `python -m ruff check .` | All checks passed |
| `python -m mypy api src tests` | Success，219 files |
| `check_coverage_baseline.py --assert-gaps 8` | 通过（仍 8 gaps） |
| 前端 `test:unit` / `build` | 全绿 / 通过 |
| E2E（`material-ledger`） | 24 passed（含本轮新增 5 条） |
| 真库 `tests/test_material_ledger_pg.py` | 8 passed（含新增 2 条权限谓词用例） |

新增 RBAC 用例（`tests/test_material_ledger_api.py`，与 `test_org_scope_rbac.py` 同一套语义）：
`test_department_scope_can_read_material_coverage`、
`test_department_scope_coverage_excludes_sibling_departments`、
`test_department_scope_can_open_parent_district_with_filtered_rows`、
`test_department_scope_department_matrix_includes_children`、
`test_unit_scope_can_read_material_coverage`、`test_unit_scope_coverage_contains_only_own_unit`、
`test_unit_scope_parent_district_does_not_leak_siblings`、
`test_unit_scope_parent_department_matrix_contains_only_own_unit`、
`test_unit_scope_cannot_read_sibling_unit_materials`、`test_out_of_scope_container_still_returns_403`；
另有 fail-closed、同名分组、`not_due_confirmed` 三条契约用例。
真库侧新增 `test_department_visible_scope_excludes_sibling_departments_in_real_sql`、
`test_unit_visible_scope_excludes_sibling_units_in_real_sql`，证明三列 OR 谓词在 PostgreSQL 上真实成立。
