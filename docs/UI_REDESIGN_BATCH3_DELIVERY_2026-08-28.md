# GovBudgetChecker UI 第三批交付说明

- 日期：2026-08-28
- 分支：`feat/ui-redesign-prototype`（仅推送，未开 PR、未合并 main）
- 提交：
  - `86d76a5` fix(upload-center): 实现分析前确认闸门，阻断 needs_confirmation 文件提交
  - `dc7eb8c` fix(workbench): 队列表补齐真实耗时列，拆分耗时与页数两列
  - `c3f4a46` feat(review-workbench): 实现审核工作台三栏布局（Task 6）
- CI：run [`33146787107`](https://github.com/zxj6827111-blip/GovBudgetChecker/actions/runs/33146787107)（HEAD `c3f4a46`），`conclusion=success`，全部 23 个步骤绿，用时 5m21s

---

## 0. 全量门禁真实输出（本机 Windows）

```
.venv/Scripts/python.exe -m ruff check .
All checks passed!

.venv/Scripts/python.exe -m mypy api src tests
Success: no issues found in 165 source files
（基线 164，+1 因新增 tests/test_job_summary_elapsed_ms.py）

.venv/Scripts/python.exe -m pytest -q
854 passed, 1 skipped, 22 warnings in 80.71s
（基线 849 passed + 1 skipped；+5 为前置修复 2 的耗时字段测试）

.venv/Scripts/python.exe scripts/check_log_message_safety.py
exit 0（无输出即通过，fail-closed 门禁未新增违规）

npm --prefix app run test:unit
全部 10 个子脚本通过：test:ui-adapters / test:ui-status / test:unit-match /
test:admin-config / test:ui-components / test:ui-preview-guard /
test:workspace-navigation / test:workbench-adapters /
test:upload-center-adapters / test:review-workbench-adapters（新增）

npm --prefix app run build
Compiled successfully；/review 路由生成为 10.9 kB（130 kB First Load JS）；
/api/workflow 路由已注册为 Dynamic

npm --prefix app run test:e2e
73 passed (1.1m)
（基线 ≥44 passed；本批净增 12 项：review-workbench.spec.ts 9 项 +
upload-center.spec.ts 3 项确认闸门场景；其余 61 项既有 e2e 全部保持通过）
```

CI（Linux, run `33146787107`, success）：Ruff / Log message safety / Env var
consistency / Compose config check / Mypy / Pytest / DB migrations（fresh
database + idempotency） / Business gate（replay structural metrics） /
Frontend unit tests / Frontend build / E2E 全部通过。

---

## 1. 前置修复 1：分析前确认闸门（决策 B）

### 问题回顾
`UploadCenterPage.tsx` 的 banner 写着"低置信度元数据将在任务进入规则分析前
要求人工确认"，但 `canSubmit` 只阻断 `pending_preflight`/`failed`，不阻断
`needs_confirmation`——闸门是纯装饰，点了也不影响流程。

### 实现
| 文件:行 | 改动 |
|---|---|
| `app/app/components/workspace/uploadCenterAdapters.ts:56-215` | 新增 `listPreflightConfirmationReasons`（精确列出缺失哪几项：年份/类型/组织）、`applyManualConfirmationOverride`（只补缺失字段，不覆盖已识别正确的字段；人工选择组织时 confidence 记为 1，与 associate 接口既有语义一致） |
| `app/app/components/workspace/UploadConfirmationPanel.tsx`（新建） | 单文件补齐表单：按 `listPreflightConfirmationReasons` 结果按需渲染年份/类型/组织三个字段，不强迫用户重填已识别正确的项 |
| `app/app/components/workspace/UploadFileList.tsx:1-140` | `needs_confirmation` 状态文件新增「补齐」按钮，点击展开 `UploadConfirmationPanel` |
| `app/app/components/workspace/UploadCenterPage.tsx:78-260` | 新增 `effectivePreflightFor()`：统一计算"批量预设 + 单文件覆盖"叠加后的有效值（单文件覆盖优先级更高）；`effectiveEntries` 用 `derivePreflightStatus` 重新判定状态（不是前端直接改标记）；`canSubmit` 增加 `!hasUnresolvedConfirmation`；`handleSubmit` 改用 `effectivePreflightFor()` 取值，确保补齐值真正进入 `formData` |

### 「仅靠批量预设是否够用」的场景判断（硬性要求 5）
**结论：不够用，确实存在批量预设无法解决的场景**，因此实现了单文件编辑。
理由：批量预设的组织/年份/文档类型是一个全局统一值，应用到本轮全部待上传
文件；但真实场景里同一批文件的缺失项经常不同——例如文件 A 只是年份识别
失败（封面扫描质量差），文件 B 是组织匹配置信度不足（新单位、匹配库里
没有）。给文件 A 补的年份对文件 B 没有帮助，反之亦然，批量预设的"一个值
套全部文件"模型结构性地无法覆盖这种"不同文件缺不同字段"的情况。

### 用户被拦住后具体怎么补齐（硬性要求 5）
1. 页面上方黄色横幅会显示"还有 N 个文件需要确认后才能开始分析"，并明确
   指出两条路径：填写批量预设，或点击文件右侧「补齐」；
2. 每个 `needs_confirmation` 文件的徽章旁会出现「补齐」按钮，点击后展开的
   表单只显示该文件真正缺失的字段（`PREFLIGHT_CONFIRMATION_REASON_LABELS`：
   "年份未识别到"/"文档类型未识别到"/"组织匹配置信度不足"）；
3. 填完并点击「保存并重新校验」后，`derivePreflightStatus` 用叠加后的有效
   值重新判定，状态从"需要确认"变为"校验通过"，提交按钮随即解除禁用；
4. 提交时 `handleSubmit` 用的是同一份 `effectivePreflightFor()` 计算结果，
   e2e 已验证提交请求体确实携带补齐后的真实值（见下方测试）。

### 测试
- `app/tests/uploadCenterAdapters.test.ts`：新增 11 条断言，包含：
  - `listPreflightConfirmationReasons` 精确列出缺失项组合的正反对照；
  - `applyManualConfirmationOverride` 只补缺失字段、不覆盖已识别正确字段；
  - **核心反例**：补齐全部缺失字段后 `derivePreflightStatus` 必须重新判定为
    `passed`（"补齐后必须能提交"）；只补一半仍是 `needs_confirmation`（防止
    "半补也放行"的误判）；
  - `override`/`response` 为 `null`/`undefined` 时的幂等对照。
- `e2e/tests/upload-center.spec.ts`：新增 3 个场景：
  1. **核心反例**：`needs_confirmation` 文件存在时提交按钮必须 disabled，
     且确认过程中真实上传请求数为 0（防止"看起来禁用了其实点了也会提交"）；
  2. 单文件补齐后按钮解除禁用，且真实上传请求体包含补齐后的年份 `2026`
     （断言 multipart body 文本，是"补齐值真正进入上传请求"的直接证据）；
  3. 批量预设补齐年份同样能解除闸门（验证批量预设路径也真实生效）。

真实输出：`npm run test:upload-center-adapters` → `uploadCenterAdapters.test.ts
passed`；`node scripts/run-e2e.cjs upload-center.spec.ts` → `11 passed (17.2s)`。

### banner 文案核实
实现后 banner 文案（"系统不会把无法识别的年份写成默认值。低置信度元数据
将在任务进入规则分析前要求人工确认。"）与实现完全一致，未发现需要修正的
偏差，文案保持不变。

---

## 2. 前置修复 2：队列表「耗时」列

### 问题回顾
`WorkbenchQueueTable.tsx` 列名是"耗时/页数"，但 `formatElapsedAndPages` 只渲染
页数，耗时完全缺失。

### 实现
| 文件:行 | 改动 |
|---|---|
| `api/runtime.py:1516-1541`（`collect_job_summary` 内） | 新增 `computed_elapsed_ms` 计算：优先用 `result.meta.started_at`/`finished_at` 差值，`elapsed_ms.total` 兜底；两者都拿不到或差值为负（脏数据/时钟回退）时为 `None` |
| `api/runtime.py:1897`（summary dict） | 新增 `"elapsed_ms": computed_elapsed_ms` 字段 |
| `app/lib/uiAdapters.ts:38-42` | `JobSummaryRecord` 新增 `elapsed_ms?: number \| null` |
| `app/app/components/workspace/workbenchAdapters.ts:329-378` | 新增 `formatElapsedText`（null/undefined/负数→"—"，0→"0 秒"，含秒/分/时分级格式化）、`formatPagesText`（从组件迁出） |
| `app/app/components/workspace/WorkbenchQueueTable.tsx:1-175` | 表头「耗时/页数」拆分为「耗时」「页数」两列，改用导入的格式化函数，新增 `data-testid="gbc-workbench-queue-elapsed-{jobId}"` |

### 真实历史数据可用比例（硬性要求：如实报告实际可用比例）
用本地 786 个真实历史任务目录实测（`uploads/`，脚本为一次性检查，未入库）：

| 口径 | 分母 | 分子 | 比例 |
|---|---|---|---|
| 全部任务中 `result.meta.started_at`/`finished_at` 均存在 | 786 | 328 | 41.7% |
| **仅"确实跑过分析"的任务子集**（`status` 为 `done`/`review_required`/`error`） | 336 | 328 | **97.6%** |
| 全部任务中 `elapsed_ms.total` 存在（双模式分析才写入） | 786 | 178 | 22.6% |

结论：`uploaded`（从未真正分析过）的 450 个任务天然没有耗时，这不是缺陷，
是真实事实（列名保持"耗时"而非改名，因为在真正跑过分析的任务里覆盖率
97.6%，已经足够可靠；未分析任务显示"—"是正确行为，不是异常）。因此采用
`finished_at - started_at` 作为主口径，`elapsed_ms.total` 仅兜底（其覆盖率
仅约 53%，来自双模式分析才写入该字段）。

### 测试
- `tests/test_job_summary_elapsed_ms.py`（新建 5 项）：正例（差值计算/
  `elapsed_ms.total` 兜底/真实 0 耗时不误判为 `None`）+ 反例（双数据源都缺失
  必须是 `None`，不得显示 0 或猜测值；`finished_at < started_at` 脏数据不
  信任差值，回退兜底）。真实输出：`5 passed in 0.61s`。
- `app/tests/workbenchAdapters.test.ts`：新增 `formatElapsedText`/
  `formatPagesText` 断言，含 60 秒边界（显示"1 分钟"不带零秒尾巴）、3600
  秒边界（显示"1 小时"）、负数（脏数据）显示"—"等正反对照。

回归验证：`tests/test_display_issue_count.py` + `tests/test_pipeline_stage_
progress_integration.py`（12 项，覆盖 `collect_job_summary` 既有行为）在改动
后仍全部通过，证明新增字段是纯附加、未扰动既有质量门禁判定。

---

## 3. Task 6：审核工作台三栏布局

### 3.1 原型图对照说明

| 原型图元素 | 还原情况 | 文件:行 |
|---|---|---|
| 顶部返回箭头 + 文件名 + 状态徽章 | 1:1 还原；徽章 tone 与既有队列表 `resolveQualityBadge` 同构判定，`review_required` 独立分支显示"需要人工复核" | `ReviewWorkbenchPage.tsx:283-296`，`resolveWorkbenchHeaderBadge`（`reviewWorkbenchAdapters.ts:290-310`） |
| 报告编号 · 页数 · 规则集版本 · 模型标识 | **刻意偏离**：顶部信息条只展示"报告编号 + 页数"两项真实字段（`structured_report_id`、`result.meta.pages`）；规则集版本/模型标识改放到「元数据」tab（见下）而非顶部一行，理由：顶部空间有限且这两项是"审核时需要深入核实"的信息，比"扫一眼确认身份"的报告编号/页数优先级低，放进结构化的元数据 tab 更符合信息层级，且避免顶部信息条过长换行 | `ReviewWorkbenchPage.tsx:298-301` |
| 「重新分析」「导出报告」「完成复核」 | 1:1 还原按钮位置；「完成复核」诚实边界见下 | `ReviewWorkbenchPage.tsx:302-320` |
| 左栏页面缩略图，当前页/总页数，当前页高亮 | 1:1 还原；总页数取真实 `result.meta.pages`，不足时显示"正在加载页面缩略图…"而非猜一个数字 | `ThumbnailRail.tsx:148-176` |
| 中栏 PDF + 页导航 + 缩放 | 1:1 还原「上一页」「第 N 页」「下一页」常驻控件；缩放范围 50%-200%，步长 25% | `PdfViewerPane.tsx:76-132` |
| 橙色"问题证据 · GBC-BUD-014"标记 | 1:1 还原视觉效果（`warning-700` 边框 + `warning-100` 底 + `warning-700` 文字标签），文案里的 rule_id 是真实问题的 `ruleId`，不是原型图占位的 `GBC-BUD-014` | `PdfViewerPane.tsx:147-160` |
| 右栏三 tab（审核问题/元数据/阶段记录） | 1:1 还原 tab 结构与切换交互 | `ReviewWorkbenchPage.tsx:355-390` |
| 问题卡：风险徽章/rule_id/页码/标题/说明/引文块/三按钮 | 1:1 还原；风险徽章颜色改用语义 token（`danger`/`warning`/`info`/`slate`），不用原型图截图里可能出现的默认 Tailwind 调色板 | `IssueCard.tsx` |
| 底部"已确认 N·已忽略 N·待处理 N"+"自动保存于 HH:MM" | 1:1 还原布局；数字为真实计算值（见下），不是原型图示例的 2/1/3 | `ReviewWorkbenchPage.tsx:395-402`，`computeWorkflowStatusCounts`（`reviewWorkbenchAdapters.ts:312-327`） |

### 3.2 缩略图性能实测数据（额外交付 1）

**测试方法**：本地 786 个历史任务目录中，用 PyMuPDF 直接读取每份 PDF 的
真实页数（不依赖是否已分析过），找出本地可用的最大真实材料——**34 页**
（`uploads/c03ee7f30020357eab932f0372a64408/上海市普陀区人民政府石泉路街道
办事处26单位.pdf`；本地历史数据中位数仅 1 页，无 ≥20 页材料，34 页已是
本地能找到的最大真实材料）。

用一次性脚本（FastAPI `TestClient` 内嵌 ASGI 调用 + `runtime.UPLOAD_ROOT`
指向真实 `uploads/` 目录 + 真实 PyMuPDF 渲染路径；测完已删除，未提交进
仓库）模拟前端 `ThumbnailLoadScheduler` 的调度行为（线程池大小 = 并发上限，
即线程池不会同时跑超过该数量的任务）：

```
=== Task 6 缩略图性能实测（真实 PyMuPDF 渲染 + 真实 34 页 PDF） ===
job_id: c03ee7f30020357eab932f0372a64408
总页数: 34
并发上限: 4
总请求数: 34
成功请求数: 34
实测并发峰值: 4
首屏（前 4 张）到达耗时: 0.079s
全部 34 页加载完成总耗时: 0.454s
单页请求耗时 - 最小: 0.016s, 最大: 0.079s, 平均: 0.052s
```

- **总请求数 34**：每页恰好 1 次请求，无重复（客户端缓存/去重生效）；
- **成功请求数 34/34**：100% 成功；
- **实测并发峰值 4**：精确等于设定的并发上限，调度器行为符合设计；
- **首屏（前 4 张）到达耗时 0.079s**：远低于用户可感知的卡顿阈值；
- **全部 34 页加载完成总耗时 0.454s**。

**已知局限（如实报告，不回避）**：FastAPI `TestClient` 走 ASGI 内存传输，
没有真实网络延迟，也没有真实多 worker 并行（单进程 GIL）。因此额外跑的
"不设并发上限、34 页齐发"对照组（`max_workers=34`）反而耗时更低（0.391s
vs 0.454s），**没有复现出"48 页齐发拖垮后端"的退化**——本次测试条件下
（内存传输 + 单进程）不足以体现真实网络延迟叠加 + 真实并行渲染成本下的
瞬时压力差异。这不代表"并发上限没用"的结论：任务书基于 PyMuPDF 渲染是
CPU 密集操作、真实生产环境是多请求真实网络往返这一前提做出的"需要并发
上限"的推断依然合理，本次测试**只实证了并发上限本身被精确遵守（peak=4）**，
未能在这个测试环境下实证其对吞吐的提升幅度。若需要更贴近生产的验证，
应在真实多 worker HTTP 服务器 + 真实网络客户端下重跑，这是本批次未覆盖
的部分。

### 3.3 「补充意见」的实现方式（额外交付 2）

**复用了现有备注能力，未扩展后端**。调研确认 `src/services/issue_workflow_
store.py` 的 `update_issue()` 函数签名早已带 `note: Optional[str]` 参数
（`issue_workflow_store.py:466-502`），`_normalize_state()` 也已经解析并
持久化该字段（`issue_workflow_store.py:117-177`，`"note": str(item.get
("note") or "").strip() or None`）。因此 Task 6 的「补充意见」功能只是把
这个已有能力接到 UI 上：`IssueNoteDialog.tsx` 收集用户输入的备注文本，
`ReviewWorkbenchPage.tsx:handleSaveNote` 通过 `POST /api/workflow` 的
`action=update_issue` 请求体带上 `note` 字段提交，没有新增任何后端字段或
迁移脚本。

### 3.4 `/api/workflow` 与 `/api/jobs/{job_id}/issues/ignore` 的关系判断（额外交付 3）

**选择 `/api/workflow` 为唯一工作流路径，不使用 `/api/jobs/{job_id}/issues/
ignore`。**

判断依据（调研阶段实测代码路径确认）：
- `/api/workflow`（`api/routes/workflow.py`，底层 `src/services/issue_
  workflow_store.py`）写入 `UPLOAD_ROOT/.issue_workflow.json`，支持
  `pending`/`confirmed`/`no_issue`/`needs_review`/`in_package` 五态 +
  `note` 备注字段，是**状态标记**语义（问题仍在结果里，只是带一个工作流
  状态）；
- `/api/jobs/{job_id}/issues/ignore`（`api/runtime.py:523` `ignore_job_
  issue`，`ignore_job_issue` 内部调用 `write_ignored_issue_ids`）写入
  `job_dir/.ignored_issues.json`，是**过滤**语义（`apply_job_issue_filters`
  会把 `ignored_issue_ids` 里的问题从返回 payload 中整个剔除，问题"消失"
  而不是被标记）；
- **两者完全独立、互不感知**：`get_visible_state()`（workflow 的读取路径）
  不检查 `ignored_issue_ids`；`ignore_job_issue()`（issues/ignore 的写入
  路径）也不检查 `.issue_workflow.json`。如果审核工作台同时使用两个端点
  （例如"确认/补充意见"走 workflow，"忽略"走 issues/ignore），会出现
  真实的状态不一致风险：一个问题可能同时是 workflow 里的 `pending`（因为
  从未调用过 `/api/workflow`）和 `.ignored_issues.json` 里的"已忽略"（因为
  调用过 `issues/ignore`），两个数据源各自认为自己是权威，前端读哪个会得
  出不同结论。

因此 Task 6 的「忽略」按钮统一走 `/api/workflow` 的 `status=no_issue`，
不调用 `issues/ignore`——这样"确认/忽略/补充意见"三个操作全部落在同一份
`.issue_workflow.json` 里，不产生第二个真相源。**风险已报告**：如果未来
有其它入口（例如旧 `task/[job_id]/page.tsx` 页面的"忽略此问题"按钮，见
`EvidencePanel.tsx:task-ignore-issue-button`）继续调用 `issues/ignore`，
与审核工作台的 workflow 记录会互不感知——这是既有旧页面的既定行为，本批
未改动旧页面（按硬性要求不删除/不改动旧组件），因此这个"两套机制并存"的
风险在旧页面与新审核工作台之间**依然存在**，只是本批的新组件自己没有让
它变得更糟（新组件只写一份，不叠加第二份）。彻底消除需要在 Task 10 下线
旧页面、或把旧页面的忽略按钮也迁移到 `/api/workflow` 时统一解决。

### 3.5 待 Task 10 清理的旧组件清单（额外交付 4）

`git ls-files app/app/components/task-review/` 确认以下 7 个文件均为已跟踪
文件，`grep` 确认 `app/app/task/[job_id]/page.tsx` 是唯一直接引用方
（`PDFHighlighter`/`ProblemSidebar`/`EvidencePanel`/`PipelineDrawer`/
`ReportPreviewModal` 共 5 处直接 import），`ProblemPreviewFrame` 被
`EvidencePanel.tsx`/`PDFHighlighter.tsx` 内部引用（间接依赖）：

| 文件 | 状态 |
|---|---|
| `PDFHighlighter.tsx` | 保留，被 `task/[job_id]/page.tsx` 直接引用 |
| `ProblemSidebar.tsx` | 保留，被 `task/[job_id]/page.tsx` 直接引用 |
| `EvidencePanel.tsx` | 保留，被 `task/[job_id]/page.tsx` 直接引用 |
| `PipelineDrawer.tsx` | 保留，被 `task/[job_id]/page.tsx` 直接引用 |
| `ReportPreviewModal.tsx` | 保留，被 `task/[job_id]/page.tsx` 直接引用 |
| `ProblemPreviewFrame.tsx` | 保留，被 `EvidencePanel.tsx`/`PDFHighlighter.tsx` 间接引用 |
| `problemPreview.ts` | 保留（且本批新增导出了 `OverlayBox` 类型供审核工作台复用），仍被上述全部组件依赖 |

**结论：本批一个都不能删**，因为 `task/[job_id]/page.tsx`（旧详情页）仍在
线上可用，硬性要求"旧页面仍需可用，Task 10 才下线旧入口"。全部 7 个文件
留待 Task 10 下线旧页面时统一清理。

### 3.6 后端字段/接口新增清单

均为纯增量，不改变既有行为（已通过既有测试回归验证）：
- `app/lib/mock.ts`：`Problem` 接口新增 `evidenceStatus?: string`；
- `app/lib/uiAdapters.ts`：`toUiProblems()` 提取 `issue.evidence_status`
  写入 `evidenceStatus`；`JobSummaryRecord` 新增 `structured_report_id`；
- `app/app/components/task-review/problemPreview.ts`：新增导出 `OverlayBox`
  类型（原为文件内私有类型），供审核工作台的 `computeOverlayBoxAtScale`
  复用同一套 CSS 百分比转换逻辑，未改变 `getProblemOverlayBox` 等既有函数
  的任何行为；
- 新建 `app/app/api/workflow/route.ts`：`/api/workflow` 的 Next.js 代理路由
  （此前只有 `gbc-ui-demo` 专用的代理），写法完全参照既有
  `app/app/api/gbc-ui-demo/workflow/route.ts`。

### 3.7 「完成复核」按钮的诚实边界

调研确认后端**没有**"标记任务复核完成"的端点（`api/routes/jobs.py`/
`api/routes/workflow.py` 均无此类写操作）。因此按钮实现为：仅在全部问题
都已确认/忽略（`pending === 0`）时可点击，点击后仅前端导航回 `/queue`，
**不向后端发送任何声称"审核已完成"的请求**——这是一个诚实的前端把关
（"确认你已经处理完当前列表"），不是向系统承诺一个它做不到的能力。
见 `ReviewWorkbenchPage.tsx:14-20`（顶部注释）与 `:317-320`（按钮实现）。

### 3.8 测试清单

- `app/tests/reviewWorkbenchAdapters.test.ts`（新建，398 行）：
  `ThumbnailLoadScheduler`（mock fetch 统计并发峰值/总请求数/去重缓存/
  懒加载不主动拉取未请求页/失败页缓存不自动重试）、`countFormalProblems`/
  `isProblemDegraded`（与 `count_formal_findings` 同口径正反对照）、
  `sortProblemsForReview`（页码升序稳定排序）、`computeOverlayBoxAtScale`
  （bbox 百分比换算，含"渲染缩放变化但图片自然尺寸同步变化时百分比不变"
  的关键反例）、`extractFindingVersions`/`formatVersionList`（M2 版本留痕）、
  `formatMetadataYear`/`formatMetadataReportKind`（禁止 2000 兜底反例）、
  `resolveWorkbenchHeaderBadge`（`review_required` 反例）、
  `computeWorkflowStatusCounts`（不得写死 2/1/3 反例）、`deriveStageHistory`
  （5 态阶段派生，含失败/未知/未识别 phase 三种反例）、
  `extractTotalPageCount`（0 页视为未知反例）。真实输出：
  `reviewWorkbenchAdapters.test.ts passed`。
- `e2e/tests/review-workbench.spec.ts`（新建，9 项，337 行）：核心闭环
  （打开任务→选中问题→跳页→确认→计数变化→刷新仍在）、三 tab 切换与计数
  准确性、degraded 降级标识反例、年份未识别反例（禁止 2000）、
  `review_required` 徽章反例（禁止"分析完成"）、忽略问题计数变化、
  补充意见备注持久化、阶段进度未知显示"—"反例、未带 job 参数引导态。
  真实输出：`9 passed (11.0s)`（独立跑）；完整套件里为 `73 passed (1.1m)`。

测试驱动修正记录（诚实披露）：写 e2e 时最初断言"审核问题（2）"，跑测试
后发现实际是"（1）"——排查后确认是我最初计算测试期望时算错了（mock 数据
2 条 finding 里有 1 条标记为 `degraded_missing_evidence`，正式问题数应为
1，与 `count_formal_findings` 口径一致），修正测试期望后通过，证明实现是
对的。此外单测阶段发现并修正了 `ThumbnailLoadScheduler.requestPage()` 的
一个设计缺陷：最初实现允许对已经处于 `error` 态的页立即重新排队（本意是
"允许重试"），但这与"避免同一张坏图反复重试拖慢整体加载"的任务要求冲突，
测试暴露后改为 `error` 态是 sticky 的（普通 `requestPage` 不会重置），显式
重试需调用新增的 `retryPage()`（当前 UI 未接入重试按钮，是已知局限，留待
后续按需补充）。

---

## 4. 未验证部分（如实说明）

1. **缩略图并发上限对吞吐的真实提升幅度未在真实网络环境下验证**（见 3.2
   局限说明），仅验证了并发上限本身被精确遵守。
2. **48 页级别的真实材料未测试**：本地历史数据最大只有 34 页，无法验证
   更大页数（如原型图示例的 48 页）下的表现，34 页已是本地可用的最大
   真实材料。
3. **`/api/workflow` 与旧页面 `issues/ignore` 的历史数据交叉影响未做数据
   层面清理**：本批只是新组件不再新增交叉写入，历史上如果有任务同时被
   两个端点操作过，数据层面的不一致依然存在（未做回填/修复，超出本批
   范围）。
4. **`ThumbnailLoadScheduler.retryPage()` 未接入 UI**：缩略图加载失败后
   当前只显示"加载失败"文案，没有重试按钮，用户需要刷新整个页面才能
   重新加载（已实现的 `retryPage()` 方法可以支撑未来补充这个交互，本批
   未做是因为原型图与任务书都未明确要求"失败重试"这个具体交互）。
5. **顶部信息条未展示"规则集版本 · 模型标识"这一行**（改放进元数据 tab，
   见 3.1 表格"刻意偏离"说明），如果后续复核认为顶部必须保留这一行，需要
   额外补充，本批未做是基于信息层级的主动设计取舍，非遗漏。
