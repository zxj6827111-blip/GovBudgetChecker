# UI 逐屏功能对照表与 A2 删除清单（Task 10 批次二前置材料）

- 日期：2026-08-28
- 用途：批次一交付（额外交付）。逐屏核对三个旧路由的功能在新 UI 中的去向，
  作为批次二（A2 删旧单体等）执行前的确认依据。本文件只做对照与清单，不执行删除。
- 关联：`docs/UI_REDESIGN_BATCH3_DELIVERY_2026-08-28.md` §3.5（task-review 组件
  本批不能删的结论）、`docs/RELEASE_ACCEPTANCE_2026-08-27.md`。
- 核实方式：逐文件 grep + 通读（2026-08-28，分支 `feat/ui-redesign-prototype`，
  含批次一 T1–T5 改动）。对照结论中"待人工复核"的条目请逐条确认后再执行批次二。

---

## 1. 三个旧路由概览

| 旧路由 | 文件 | 行数 | 性质 |
|---|---|---|---|
| `/viewer/gbc-ui-demo` | `app/app/viewer/gbc-ui-demo/page.tsx` | 1947 | 旧 UI 单体（旧版完整工作台，按 `page=` 查询参数分 7 屏） |
| `/task/[job_id]` | `app/app/task/[job_id]/page.tsx` | 540 | 旧任务详情页（消费 task-review 6 组件） |
| `/viewer/[job_id]` | `app/app/viewer/[job_id]/page.tsx` | 181 | 问题 bbox 高亮查看器（**孤页**，全仓无代码跳入，仅页内上/下一页自链） |

另发现一个孤组件：`app/app/components/OrganizationDetailView.tsx`——全仓无任何
import 方（仅自身定义），见 §4 处置建议。

## 2. 逐屏对照：`/viewer/gbc-ui-demo`（7 屏）

| 旧屏（page=） | 功能要点 | 新 UI 去向 | 差距 / 备注 |
|---|---|---|---|
| `workbench`（默认） | KPI 概览、待办（含"生成整改包"入口）、组织树范围筛选、年份筛选 | `/workbench`（新版工作台总览 + 队列表） | 已覆盖；登录默认落地 `/workbench`（fix D） |
| `issues` | 问题列表、工作流操作（确认/忽略等 + 批量操作）、分页 | `/review`（审核工作台三栏，Task 6：确认/忽略/补充意见统一走 `/api/workflow`） | 交互形态从"列表批量"变为"逐问题三栏审核"；批量操作在新工作台无一一对应入口——**待人工复核：是否有高频批量确认场景** |
| `upload` | 上传 PDF | `/upload`（上传中心：预检 + 分析前确认闸门，前置修复 1） | 已覆盖且强于旧版 |
| `tasks` | 任务列表（进度条/状态）、重跑、导出、删除（admin） | `/queue`（队列表，含耗时/页数列，前置修复 2）+ `/history` | 重跑入口在新 UI 由 `/queue`/`/review` 提供；**待人工复核：删除任务入口在新 UI 是否可见可达** |
| `detail` | 内联任务详情（选中任务的问题/工作流/重跑/导出） | `/review?job=<id>`（Task 6 审核工作台） | 已覆盖 |
| `settings`（仅 admin） | `SystemManagementPanel` 系统管理（组织/结构化清理等） | `/settings` + `/admin`（新 UI 保留同一面板，经 `admin/SystemManagementPanel`） | 已覆盖；`SystemManagementPanel.tsx:901` 的旧跳转是删除牵连点（见 §4） |
| `archive` | 整改包列表/生成/下载 ZIP | `/archive`（Task 9 迁入 + `middleware.ts:90-93` 旧 URL 重定向） | 已覆盖 |
| 全局：Header/Sidebar（组织树）、Toast、登录登出 | 组织树筛选贯穿各屏 | 新 UI 侧栏导航 + `/department/[id]` 组织页 | 组织树形态不同；**待人工复核：按组织树逐节点浏览问题的路径在新 UI 是否等价** |

## 3. 逐屏对照：`/task/[job_id]`（旧详情页）与 `/viewer/[job_id]`（孤页）

### 3.1 `/task/[job_id]`（540 行）

| 旧功能 | 依据（data-testid/组件） | 新 UI 去向 | 备注 |
|---|---|---|---|
| 阶段进度抽屉 | `PipelineDrawer` | `/review` 右栏「阶段记录」tab（`deriveStageHistory`） | 已覆盖 |
| 问题侧栏 | `ProblemSidebar` | `/review` 右栏「审核问题」tab + 底部计数条 | 已覆盖 |
| 证据面板（含"忽略此问题"→ `issues/ignore`） | `EvidencePanel`、`task-ignore-issue-button` | `/review` 问题卡三按钮（确认/忽略/补充意见，统一写 `/api/workflow`） | **语义差异**：旧页"忽略"= 过滤剔除（`.ignored_issues.json`），新台"忽略"= 状态标记 `no_issue`（`.issue_workflow.json`）；两套存储并存问题见 A3 方案文档 |
| PDF 高亮（bbox 叠加） | `PDFHighlighter` | `/review` 中栏 `PdfViewerPane`（缩略图 + 高亮 + 页导航 + 缩放） | 已覆盖 |
| 导出报告预览 | `ReportPreviewModal`、`task-open-report-modal` | `/review` 顶部「导出报告」+ `/archive` 整改包 | 已覆盖 |
| 重新分析（含 AI 开关确认） | `task-reanalyze-button`、`ReanalyzeAiToggle` | `/queue`/`/review` 重跑入口 | **待人工复核：AI 开关（取消勾选仅本地解析）在新 UI 重跑路径是否保留** |
| 关联组织 | `task-associate-button` | `/upload` 上传中心确认流程（组织匹配 + 人工补齐） | 入口时点不同：旧页在分析后补关联，新 UI 在上传前确认 |

### 3.2 `/viewer/[job_id]`（181 行，孤页）

| 旧功能 | 新 UI 去向 | 备注 |
|---|---|---|
| 按 `?page=&bbox=&title=` 渲染整页预览并叠加 bbox 红框、缩放 60–180%、翻页、打开原 PDF | `/review` 中栏 `PdfViewerPane`（`computeOverlayBoxAtScale` 同一套百分比换算，`problemPreview.ts` 复用） | 已覆盖。全仓无代码跳入（grep 无 `"/viewer/${...}` 构造点），删页无入口断裂；但**待人工复核：是否有外部书签/交付物直接引用该 URL** |

## 4. A2 删除清单（批次二执行，本批不删）

### 4.1 删除文件（12 个）

| # | 文件 | 行数 | 说明 |
|---|---|---|---|
| 1 | `app/app/viewer/gbc-ui-demo/page.tsx` | 1947 | 旧单体 |
| 2 | `app/app/task/[job_id]/page.tsx` | 540 | 旧详情页 |
| 3 | `app/app/viewer/[job_id]/page.tsx` | 181 | 孤页 |
| 4–9 | `app/app/components/task-review/{EvidencePanel,PDFHighlighter,PipelineDrawer,ProblemPreviewFrame,ProblemSidebar,ReportPreviewModal}.tsx` | — | 仅被旧详情页（及彼此）引用 |
| 10 | `app/app/components/OrganizationDetailView.tsx` | — | **新发现孤组件**：全仓无 import 方 |
| 11–12 | 既有硬编码色残留载体：`viewer/[job_id]` 5 处、`task-review/{EvidencePanel,PDFHighlighter}` 2 处 | — | 随删除自然消失，无需单独修改（已含于上述文件） |

**保留**：`app/app/components/task-review/problemPreview.ts`——被新工作台
`review-workbench/PdfViewerPane.tsx:20-23`、`reviewWorkbenchAdapters.ts:18-19`
复用（`OverlayBox` 类型、overlay 样式与 URL 构造）。建议批次二将其迁往
`app/app/components/review-workbench/`（或原位保留），迁移属纯移动 + 改 import。

### 4.2 牵连点处理清单（删除时同步修改）

| # | 位置 | 现状 | 批次二处理 |
|---|---|---|---|
| 1 | `app/middleware.ts:90-93` | `/viewer/gbc-ui-demo?page=archive` 重定向到 `/archive` | 删除 demo 后该重定向失效源消失；改为对旧 URL 返回 410 或保留永久重定向——**待定** |
| 2 | `app/app/components/admin/SystemManagementPanel.tsx:901` | `window.location.assign("/viewer/gbc-ui-demo?page=...")` 跳旧详情 | 改跳 `/review?job=<id>` 或对应新页 |
| 3 | `app/app/department/[id]/DepartmentPageClient.tsx:1187` | `router.push("/viewer/gbc-ui-demo?page=...")`（**本对照新发现的实际跳转点**） | 同上，改跳新页 |
| 4 | `app/app/api/gbc-ui-demo/workflow/route.ts` | demo 专用的 `/api/workflow` 代理路由 | 随 demo 删除（新 UI 已有通用 `app/app/api/workflow/route.ts`） |
| 5 | `scripts/run-e2e.cjs:21-25` | 预热路径含 `/viewer/gbc-ui-demo` | 移除该预热项 |
| 6 | e2e specs：`admin-system-management.spec.ts:486`、`admin-organization.spec.ts:341,422,480,559`、`archive-page.spec.ts:7,134,139`、`gbc-ui-demo-actions.spec.ts`（整文件）、`gbc-ui-demo-upload.spec.ts`（整文件）、`full-flow-review-export.spec.ts:178` | 直接访问/断言旧单体 | 两个 gbc-ui-demo 专用 spec 整体删除；其余改指向新页——即 B2 夹具清理方案（见处置文档） |
| 7 | `app/tests/loginNextPath.test.ts:21` | 正例断言 `/viewer/gbc-ui-demo` 深链保留 | 批次二删除 demo 时同步移除该正例（或改为 `/archive` 等其他深链） |
| 8 | `app/app/page.tsx:9`、`app/app/components/archive/ArchivePage.tsx:2`、`archivePageAdapters.ts:4`、`app/app/api/workflow/route.ts:11-14` | 注释提及旧单体/旧代理 | 更新注释 |
| 9 | `middleware.ts:117-119` 等登录 next 注入链路 | 不直接引用旧单体 | 无需处理（列出仅为完整性） |

## 5. 待人工复核汇总

1. 旧 `issues` 屏的**批量工作流操作**在新审核工作台无一一对应入口——是否存在
   高频批量场景需要补能力。
2. **删除任务**（admin）入口在新 UI 是否可见可达。
3. 重新分析的 **AI 开关**（"仅本地解析"选项）在新 UI 重跑路径是否保留。
4. 组织树逐节点浏览问题的路径在新 UI 是否等价（现为 `/department/[id]`）。
5. `/viewer/[job_id]` 是否存在外部书签/导出物直接引用（全仓代码无跳入）。
6. `middleware.ts` 对旧 URL 的处理策略（重定向保留 or 410）。
