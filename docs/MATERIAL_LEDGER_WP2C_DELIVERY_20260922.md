# 材料台账 WP2-C 交付说明（全局搜索 + 导航收口 + WP2 收尾）

- 分支：`feat/material-search-wp2c`
- 基线：`aa1de8c3b3a63b4272e3498f612028a4da3a53f4`（PR #44 的**普通 merge commit**，
  两个父提交都在：`731d6eb` WP2-A merge、`d4da8a2` WP2-B final reviewed HEAD）
- 范围：`WP2-C`（全局材料搜索 + Ctrl/Cmd+K + History 导航收口 + WP2 总体验收）

本轮是 WP2 的最后一批。WP2-C 独立 Review 通过后，**WP2 材料台账 UI 正式结束**，
下一阶段才进入 WP3 审核持久化闭环。

---

## 1. 交付物清单

### 1.1 后端：1 个只读 GET（接口面从 7 条变为**恰好 8 条**）

| 接口 | 作用 |
| --- | --- |
| `GET /api/materials/search` | 全局材料搜索（Material-centric） |

- 接口面冻结测试同步更新（`tests/test_material_slot_api_regression.py`）：8 条只读 GET，
  多一条少一条都会失败；**0 条写接口**；
- `SCHEMA_CHANGE_REQUIRED = NO`：没有新增 migration，也没有修改 0019。
  匹配走参数化 `ILIKE` + 既有索引（`fiscal_document_versions(slot_id)`、
  `analysis_jobs(job_uuid)`）。

新增文件：

```
src/schemas/material_search.py                  响应契约
src/services/material_search_query_service.py   查询解析 / SQL / 命中归因 / 分页
api/routes/materials.py                         新增接口八（+1 校验依赖）
```

### 1.2 前端：命令面板 + 导航收口

```
app/lib/materialSearchPresentation.ts                     命中原因等中文文案
app/app/components/materials/materialSearchAdapters.ts    纯逻辑层（可 jiti 直测）
app/app/components/materials/GlobalMaterialSearch.tsx     命令面板本体（含按钮与快捷键）
app/app/components/materials/MaterialSearchResultItem.tsx 单条结果卡
app/app/api/materials/search/route.ts                     Next 代理（透传状态码与响应体）
```

改动：

- `WorkspaceTopbar`：占位「搜索」按钮换成 `GlobalMaterialSearch`（通知/帮助保持占位）；
  面包屑标题改由 `nav.ts` 的 `resolveNavLabel()` 统一给出；
- `nav.ts`：一级导航 10 项 → **9 项**（删「任务历史」，`材料台账` 移到
  `审核工作台` 之后、`导出归档` 之前），新增 `LEGACY_ROUTE_LABELS` 给兼容入口正式标题；
- `HistoryPage`：顶部增加**兼容 / 运维入口**说明（不删任何既有能力）。

---

## 2. 搜索语义（本轮的核心口径）

### 2.1 业务对象是 Material Slot，不是 Job

用户拿 `job-abc123` 来搜时，服务端只按**唯一**关联依据
（`analysis_jobs.metadata.structured_ingest.document_version_id` → 文件版本 → 槽位）
精确关联，最终返回的仍然是**槽位**。前端不存在"拉全量任务再 `filter()`"的实现
（§十的红线）：面板只调 `/api/materials/search`，请求串里只有 `q/page/page_size`。

### 2.2 token 解析（§十三~§十五）

| 输入形态 | 解析结果 |
| --- | --- |
| 4 位年份（2000–2099） | `fiscal_year` |
| 预算 / 预算报告 / budget | `report_kind=budget` |
| 决算 / 决算报告 / final | `report_kind=final` |
| 本部 / 本级 / head_unit | `relationship=head_unit` |
| 其余 | 文本 token（大小写不敏感去重） |

- **财政年度只来自槽位字段**：不从 `published_at` / `created_at` / `uploaded_at` /
  job 年份推导（真库用例：`fiscal_year IS NULL` 的槽位即使运行发生在 2024 年，
  也不会被 `2024` 搜出来）；
- **自相矛盾即拒绝**：两个不同年度、两种文种 → 422，而不是"取其一"；
- **token 上限 8 个**：超过直接 422，不静默丢弃（丢弃会改变 AND 语义）。

### 2.3 匹配与排序

- 字段之间 **OR**：区县名 / 主管部门名 / 主体名 / 任一文件版本的文件名 /
  任一精确关联任务的 `job_uuid`；
- token 之间 **AND**：每个 token 都必须在某个字段命中（§十九）；
- 排序优先级（全部由 SQL 决定，服务端分页）：job id 精确 > 文件名精确 >
  单位名精确 > 主管部门名精确 > `updated_at DESC` > `slot_id`；
- `%` `_` `\` 一律按普通字符处理（`ESCAPE '\'` + 服务端转义）。

### 2.4 命中归因（§二十五/§三十一）

每条结果带 `matched_fields`（机器码，中文在展示层）：
`unit` / `department` / `jurisdiction` / `current_filename` / `historical_filename` /
`job_id` / `fiscal_year` / `report_kind` / `relationship`。

- 命中历史版本的文件名时给 `historical_filename`，并同时给出
  `matched_filename` 与 `matched_version_is_current=false`；
- **当前真值不受影响**：`current_document_version_id` / `current_filename` 永远是
  槽位指针（真库用例断言 V1 命中后指针仍是 V2）；
- 「打开材料」恒指向 `/materials/slots/<slotId>`，历史命中不会把详情页切回旧版本。

### 2.5 关系（本部/本级）fail-closed

- 复用 WP2-A 的 `is_head_unit_name()`：只按"单位名 == 部门名（忽略全半角/本级本部）"
  或"名称带本级/本部标志"判定，**不靠名称包含"本部"三个字**；
- 组织目录不可用时不退化成"主体层级是单位"（直属单位也是单位），
  该约束命中不了任何槽位，并在 `meta.relationship_resolution='unavailable'` 里说明；
  前端据此显示一条非阻塞提示（"本次无法解析本部/本级"）。

### 2.6 review candidate（§二十六~§二十九）

- 候选只能来自 `current_document_version_id` 对应的**当前运行**
  （复用 `select_current_run()`，与处理记录同一判定）；
- 后端用 `user_can_access_job()`（复用，不另写 job 权限）判断"这个人能不能看这个任务"，
  判断不了时 `review_candidate = null`；
- 前端再用 `resolveReviewEntryDecision()` 判断任务状态是否允许进入复核；
- 两个条件同时成立才显示「进入复核」，否则**保留「打开材料」**并给出禁用原因。

---

## 3. SQL 预算（§三十二/§六十六）

| 语句 | 内容 | 说明 |
| --- | --- | --- |
| 1 | 主查询 | 匹配 + 排序 + 分页，`COUNT(*) OVER ()` 顺带给出 total |
| 2 | 当前页明细 | 本页 ≤50 个槽位的版本与运行（命中版本、命中任务、当前运行、复核候选都从这条算） |
| +1 | 越界页兜底计数 | 仅当请求页码超出范围（主查询 0 行、窗口函数无处承载 total）时发生 |

- **无 N+1**：结果条数不影响语句数（真库用例用计数连接断言恰好 2 条语句）；
- `page_size` 上限 50；服务端分页；
- **不引入** `pg_trgm` / Elasticsearch：1 万级槽位下，额外开销是"候选行 × token"的
  少量索引探查（文件名 EXISTS 走 `fiscal_document_versions(slot_id)`，
  job EXISTS 走 `idx_jobs_uuid`）。若将来 EXPLAIN 证明必须上扩展，那是**报告**，
  不是擅自加基础设施。

---

## 4. 权限（§三十四~§三十六）

- 完全复用 WP2-A 的 `MaterialAccessScope.visible_org_ids`，SQL 里仍是三列 OR：
  `jurisdiction_org_id / department_org_id / subject_org_id = ANY(...)`；
- 一个区划都没授权 → **403**；有授权但没命中 → **200 + `items=[]`**。两者不混；
- 越权材料**连存在性都不暴露**：不返回、不以禁用态返回、文件/年份/状态一个字段都不给；
  空态文案固定为"没有找到符合条件且你有权限查看的材料"；
- 任务维度的"进入复核"不因槽位可见而假设任务可见（见 2.6）。

---

## 5. 导航收口（§四十八~§五十五）

最终一级导航（9 项）：

```
工作区：工作台总览 / 上传中心 / 处理队列 / 审核工作台 / 材料台账 / 导出归档
管理：  质量管理 / 规则与版本 / 系统设置
```

- 「任务历史」退出 `NAV_ITEMS`，但 `/history` **路由与页面能力保留**：
  库里仍有 legacy 任务（没有精确关联字段、搜索明确不猜槽位），
  删掉或重定向会丢失这部分运维查询能力；
- `/history` 页顶新增兼容说明；顶栏标题显示「任务运行历史（兼容）」，不显示裸路径
  （标题来自 `LEGACY_ROUTE_LABELS`，**没有**把 history 塞回 `NAV_ITEMS`）。

---

## 6. 测试

### 6.1 新增用例

| 文件 | 数量 | 说明 |
| --- | --- | --- |
| `tests/test_material_search_query_service.py` | 51 | 解析、转义、WHERE/ORDER 参数化形态、关系集合、命中归因、命中版本/任务选择、语句数量 |
| `tests/test_material_search_api.py` | 45 | 接口契约、鉴权、越权、job 搜索、四态、参数校验、meta、无 N+1 |
| `tests/test_material_search_pg.py` | 23 | **真库**：token AND/OR、通配符字面量、排序名次与稳定性、分页与越界 total、权限谓词、历史命中不改当前真值、JSONB 守卫、单语句明细 |
| `e2e/tests/material-search.spec.ts` | 16 | Ctrl/Cmd+K、目标查询、历史文件名、job id、四态、越权不泄露、stale response、debounce |
| `e2e/tests/material-search.screenshots.spec.ts` | 5（默认跳过） | 截图采集 |
| `app/tests/materialSearchAdapters.test.ts` | — | 前端纯逻辑（查询三态、序列、选择移动、展示投影、动作判定） |
| `e2e/tests/workspace-navigation.spec.ts` | +5 | 导航收口：无 history、顺序、/history 兼容入口与标题 |

### 6.2 真库用例发现的两个真实缺陷（已修）

1. **别名硬编码**：关联字段的 JSON 路径常量里写死了别名 `tj`，而当前页明细用 `j`，
   真库直接报 `表 tj 丢失 FROM 子句项`。改为 `_structured_version_path(alias)` 参数化，
   并在注释里写明"只有真库报得出来"（假连接不解析 SQL）。
2. **两份真值**：`MaterialSearchFilters` 曾同时持有 `fiscal_year/report_kind/relationship`，
   与解析结果 `MaterialSearchQuery` 形成两份真值；真库用例当场抓到
   "查 2024 把 `fiscal_year=NULL` 的槽位也返回了"。改为这三个条件只存在于解析结果里，
   filters 只承载权限与"本部 id 集合"。

### 6.3 变异验证（守护型断言逐条去掉防护）

见 §8.3 的清单与结果（10 条红线，**10/10 被抓住**）。

其中第 7 条第一次跑是**未被抓住**的，值得记下来：原始用例里"历史版本上的运行"
恰好比当前版本的运行更旧，于是"按时间取最近一次运行"这种错误实现也能通过。
把历史运行改成"最近完成"之后，该防护才真正可被证伪 —— 这属于用例本身的缺口，
不是实现缺口。

---

## 7. 截图（`docs/wp2c-screenshots/`，1920 视口）

| 文件 | 内容 |
| --- | --- |
| `WP2C_01_global_search.png` | 目标业务查询 `规划和自然资源局 本部 2024 决算` |
| `WP2C_02_global_search_multitoken.png` | 多 token 查询的两条结果 |
| `WP2C_03_historical_filename_match.png` | 命中历史文件名的显著标注 + 当前文件仍是当前版本 |
| `WP2C_04_history_navigation_cleanup.png` | 侧栏 9 项与顺序（材料台账位于审核工作台与导出归档之间） |
| `WP2C_04b_history_compat_entry.png` | `/history` 兼容入口与页面顶部说明 |
| `WP2C_05_search_empty_state.png` | 空态文案（不泄露存在性） |

> 截图数据来自 e2e mock，**不代表线上数据库的真实数据**。

---

## 8. 验证

### 8.1 本机与 CI 实测

| 项 | 结果 |
| --- | --- |
| `python -m pytest -q`（Windows，未开真库） | **1802 passed, 66 skipped** |
| `python -m ruff check .` | All checks passed |
| `python -m mypy api src tests` | Success: no issues found in 232 source files |
| `python scripts/check_coverage_baseline.py --assert-gaps 8` | 通过（**仍 8 gaps**） |
| `npm --prefix app run test:unit` | 全绿（新增 `test:material-search`） |
| `npm --prefix app run build` | 通过（新增 `/api/materials/search` 代理路由） |
| 全仓 E2E | **191 passed, 18 skipped**（18 = 三套截图采集，默认跳过） |
| material-ledger E2E（WP2-A + WP2-B） | **34 passed, 13 skipped** |
| global-search E2E（本轮新增） | **16 passed** |
| PostgreSQL（ledger + detail + search） | **39 passed**（16 + 23） |

### 8.2 WP2 两条找材料路径（§七十/§七十一）

| 路径 | 覆盖 |
| --- | --- |
| 管理式：材料台账 → 区县 → 主管部门 → 单位 → 多年度 → 材料详情（检查结果 / 检查覆盖 / 版本与来源 / 处理记录） | WP2-A/B e2e（`material-ledger*`） |
| 搜索式：Ctrl/Cmd+K → 单位/部门/文件名/年度/文种/job id → 材料详情 | 本轮 e2e（`material-search`） |

两条路径并存；本轮**没有**改动 WP1 / WP2-A / WP2-B 的任何真值与既有接口行为。

### 8.3 变异验证清单与结果

| # | 去掉/反转的防护 | 被抓住的用例 |
| --- | --- | --- |
| 1 | 服务端转义 `escape_like` 不再转义 `%` `_` | 前端单测 + 真库通配符用例 |
| 2 | 权限谓词不再传 `visible_org_ids` | 接口层 unit/sibling/district 越权用例 + 真库权限用例 |
| 3 | 关系条件不再约束 `subject_org_id` | 同名部门/本部用例（接口层 + 真库） |
| 4 | job 匹配从精确改成子串（SQL 操作符） | 真库 `job_id` 精确匹配用例（接口层的假连接按 Python 语义执行，看不见 SQL 操作符） |
| 5 | `current_filename` 直接取命中版本（忽略指针） | 历史文件名用例（接口层 + 真库） |
| 6 | 命中版本不校验 token（恒给 `matched_filename`） | 命中归因用例（前端单测 + 接口层） |
| 7 | 复核候选不再限定当前版本（任意运行都可当选） | 历史 job 用例（真库）：历史版本的运行刻意设为"最近完成" |
| 8 | `review_candidate` 不检查 `job_access` | 任务可见性用例（接口层 + 真库） |
| 9 | 过期响应守卫恒真（`isLatestSearchResponse` 返回 true） | 前端单测（序列语义）；e2e 另有 A 慢 B 快 的可见行为断言 |
| 10 | 本地查询长度校验去掉（短查询也发请求） | 前端单测 + e2e（不发请求用例） |

---

## 9. 已知限制

1. **只支持一种关系修饰词**：`本部/本级`。`部门汇总` / `直属单位` 没有可靠的查询词，
   与其猜一个词，不如不支持——搜索结果的命中原因必须可解释。
2. **job id 只精确匹配**：不支持前缀/模糊。反过来也意味着用户必须知道完整 id。
3. **结果不返回正式问题数**：列表口径仍是 `formal_issue_count=null`（与 WP2-A/B 一致，
   WP3 接通复核生命周期后再演进）。
4. **不返回预览/下载 URL**：与 WP2-B 同一决定（版本级安全入口应与 WP3 一起设计）。
5. **未做万级数据压测**：本轮只保证 ≤2 条主要 SQL、无 N+1、服务端分页与 debounce。
6. **真库用例默认跳过**：需显式 `GOVBUDGET_TEST_DATABASE_URL`。
7. **`/history` 仍是兼容入口**：等"运维任务历史"有正式归属页面后再迁移。

---

## 10. 复算命令

```bash
python -m pytest -q && python -m ruff check . && python -m mypy api src tests
python scripts/check_coverage_baseline.py --assert-gaps 8
npm --prefix app run test:unit && npm --prefix app run build
E2E_BASE_URL=http://127.0.0.1:3101 npm --prefix app run test:e2e
E2E_BASE_URL=http://127.0.0.1:3101 npm --prefix app run test:e2e -- material-ledger
E2E_BASE_URL=http://127.0.0.1:3101 npm --prefix app run test:e2e -- material-search
GBC_CAPTURE_SCREENSHOTS=1 E2E_BASE_URL=http://127.0.0.1:3101 \
  npm --prefix app run test:e2e -- material-search.screenshots
GOVBUDGET_TEST_DATABASE_URL=postgresql://... \
  python -m pytest tests/test_material_ledger_pg.py tests/test_material_ledger_detail_pg.py \
                   tests/test_material_search_pg.py -v
```

> 本机 3000 端口被另一个 Next 应用占用，e2e 需显式指定空闲端口（本轮用 3101）。
> 另外：`next build` 与 `next dev` 共用 `app/.next`；dev 存活的期间跑 build 会让
> dev 的路由清单失效（新路由 404）。`scripts/next-dev.cjs` 会在启动前清缓存，
> 因此"先 build 再 e2e"是安全的，"dev 存活时 build"不是。
