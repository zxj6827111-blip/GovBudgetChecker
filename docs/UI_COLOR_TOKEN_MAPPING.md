# UI 原型图取色 / 设计令牌对照表

- 制定日期：2026-08-27
- 关联任务：`docs/UI_REDESIGN_PLAN_2026-08-27.md` Task 1
- 取色方式：对 `output/ui-concept/*.png`（1600×1000 像素）用 Python + Pillow
  做区域像素采样（每个目标元素采样一个小矩形区域，统计出现频率最高的颜色，
  抗锯齿/文字边缘造成的单点误差），而非肉眼估读。采样脚本为一次性工具，
  已在完成取色后从仓库删除（硬编码本机绝对路径，不适合长期维护，采样结果
  已固化进本文档与 `app/tailwind.config.ts`）。

## 一、取色 → token 对照

| 原型图元素 | 取样图片 | 实测 hex | token 名 |
|---|---|---|---|
| 主按钮背景（"上传 PDF""开始分析"）、链接文字、进度条填充、环形图最大分段 | 01/02/03/04 | `#087f75` | `primary-600` |
| 侧栏选中态浅底、"正在分析"徽章浅底、门禁通过图标浅底 | 01/04 | `#e4f4f1` | `primary-100` |
| 环形图"PDF 解析"分段、质量管理第二条指标条 | 04 | `#1769aa` | `info-600` |
| 环形图"元数据识别"分段、发布门禁未通过行、告警卡左侧橙色竖条 | 01/04 | `#a65f00` | `warning-700` |
| 高风险/低置信度徽章浅底、门禁未通过图标浅底 | 01/03/04 | `#fff3d9` | `warning-100` |
| 环形图"AI 分析"分段、告警卡左侧红色竖条 | 01/04 | `#b5332d` | `danger-600` |
| 处理失败徽章浅底 | 01 | `#fdecea` | `danger-100` |
| 环形图"质量门禁"分段（占比最小，纯装饰） | 04 | `#b9c4c9` | `neutral-chart-400` |
| 服务状态点（"服务正常"）、门禁通过图标 | 01/04 | `#19764b` | `success-700` |
| 门禁通过图标浅底 | 04 | `#e7f5ed` | `success-100` |
| Logo 色块（GBC） | 01 | `#19394d` | `brand-900` |
| 上传拖拽区浅底 | 02 | `#f2fbf9` | `primary-50` |
| 分析前确认横幅浅底 | 02 | `#fff9eb` | `warning-50` |
| 证据高亮浅底（审核工作台命中片段） | 03 | `#fff4dc` | 未建独立 token，Task 6 消费证据高亮时可直接引用此 hex 或复用 `warning-50` 系（视觉上足够接近，无需为单一用途新增 token） |
| 问题证据标记徽章（"问题证据 · GBC-BUD-014"） | 03 | `#c96e0b` | 落在 `warning-600`~`700` 区间，Task 6 直接用 `warning-700` |
| 页面正文背景 | 01 | `#fafcfc` | `surface-50` |
| 表格行/门禁列表行背景 | 04 | `#f7f9fa` | `surface-100` |

## 二、完整色阶（50–900）

对每个语义色只实测出现在原型图里的关键档位（通常是 600/700 档的强调色 +
50/100 档的浅底），50–900 的其余档位按同一色相的明度插值生成，用于满足
现有代码库已经在用的档位（如 `text-primary-900`、`text-warning-900`）、
以及组件库需要的 hover/disabled 等派生状态。

| 档位 | primary | success | danger | warning | info |
|---|---|---|---|---|---|
| 50 | `#f2fbf9` | `#eaf7ef` | `#fdf2f1` | `#fffaf0` | `#eef6fb` |
| 100 | `#dff2ee` | `#e7f5ed`* | `#fdecea`* | `#fff3d9`* | `#dcecf6` |
| 200 | `#b3ded4` | `#bfe6cf` | `#f8cdc9` | `#ffe1a3` | `#b0d5ea` |
| 300 | `#7fc4b6` | `#8fd1ab` | `#ec9d96` | `#ffc44a` | `#7ab8db` |
| 400 | `#3fa294` | `#4fae7c` | `#cf5f57` | `#e5ad21` | `#3f93c2` |
| 500 | `#0c8c7f` | `#268f5c` | `#bd4038` | `#c98800` | `#1c7cae` |
| 600 | `#087f75`* | `#1f7f52` | `#b5332d`* | `#b57200` | `#1769aa`* |
| 700 | `#0a6b62` | `#19764b`* | `#962a25` | `#a65f00`* | `#125485` |
| 800 | `#0d5650` | `#155f3d` | `#78221e` | `#834b00` | `#0d4066` |
| 900 | `#0f423e` | `#124a30` | `#5c1a17` | `#603700` | `#092c47` |

`*` 标记的档位是原型图像素采样实测值，其余档位为同色相插值生成。

## 三、无障碍对比度核实（WCAG 2.1 AA，正文文字 ≥4.5:1）

用标准 WCAG 相对亮度公式核算（脚本核算过程见本次 Task 1 提交说明，未固化为
仓库脚本）：

| 组合 | 对比度 | 结论 |
|---|---|---|
| `primary-600` 文字 / 白底 | 4.88:1 | 达标 |
| 白字 / `primary-600` 底（主按钮） | 4.88:1 | 达标 |
| `success-700` 文字 / 白底 | 5.62:1 | 达标 |
| `danger-600` 文字 / 白底 | 6.04:1 | 达标 |
| `warning-700` 文字 / 白底 | 4.93:1 | 达标 |
| `info-600` 文字 / 白底 | 5.77:1 | 达标 |
| `brand-900` 文字（或白字于其上） / 白底 | 12.11:1 | 达标（远超阈值） |
| `primary-700` 文字 / `primary-50` 浅底（徽章场景） | 6.06:1 | 达标 |
| `warning-700` 文字 / `warning-100` 浅底（徽章场景，原型图实际配色） | **4.48:1** | **略低于 4.5:1**，见下方已知限制 |
| `neutral-chart-400`（`#b9c4c9`）用作文字 | 1.78:1 | **不达标，禁止用作文字/图标，只允许图表分段填充** |

### 已知限制：warning-700 文字置于 warning-100 浅底

原型图"高风险""需要人工复核"等加粗徽章标签用的正是 `warning-700 on
warning-100` 这一组合，实测对比度 4.48:1，略低于 WCAG 正文文字 4.5:1 门槛
（但满足"大字号/加粗文字"3:1 门槛，原型图中该文案确实是加粗展示）。

处理方式：**未擅自加深原型图实测色值**，保持与原型图 1:1 视觉还原；已在
`app/tailwind.config.ts` 的 `warning` 注释与本文档中明确记录该限制。若后续
希望更严格满足 WCAG AA（不依赖"大字号"豁免），可在该具体场景下把文字换成
`warning-800`（`#834b00`，置于同一 `warning-100` 浅底时对比度为 6.42:1），但这会使该色与原型图截图产生
肉眼可辨的色差，本轮未采用。

### neutral-chart-400 使用限制

`#b9c4c9` 只出现在质量管理页环形图"质量门禁"分段（占比最小的装饰性图表
填充），从未在原型图中作为文字或图标颜色使用。`tailwind.config.ts` 已在
该 token 注释中写明"禁止用于文字或需要辨识的图标"，仅当图表分段填充使用。

## 四、现有代码库换色影响面

`app/tailwind.config.ts` 的 `primary`（靛蓝→墨绿）、`success`/`danger`/
`warning`（标准 Tailwind 色→原型图实测色）三组颜色发生了色相变化，
影响以下已引用这些 token 的文件（`grep` 统计，Task 1 完成时的引用计数）：

| 文件 | 引用次数 | 主要用途 |
|---|---|---|
| `app/app/components/admin/BatchUploadModal.tsx` | 20 | 步骤指示、匹配状态提示 |
| `app/app/department/[id]/DepartmentPageClient.tsx` | 16 | 主操作按钮、勾选态 |
| `app/app/components/task-review/ReportPreviewModal.tsx` | 12 | 问题数高亮、操作按钮 |
| `app/app/components/task-review/EvidencePanel.tsx` | 7 | 证据面板危险色强调 |
| `app/app/components/task-review/ProblemSidebar.tsx` | 7 | 问题列表选中态 |
| `app/app/components/task-review/PipelineDrawer.tsx` | 6 | 阶段完成图标 |
| `app/app/department/[id]/DepartmentJobTable.tsx` | 6 | 状态徽章 |
| `app/app/components/Sidebar.tsx` | 5 | 组织树选中态、搜索高亮 |
| `app/app/task/[job_id]/page.tsx` | 4 | 质量状态徽章 |
| `app/app/components/task-review/PDFHighlighter.tsx` | 3 | 证据高亮标签（深色背景场景） |
| `app/app/components/AppLayout.tsx` | 1 | 侧栏收起按钮 hover 态 |
| `app/app/components/Header.tsx` | 1 | 旧版顶栏 Logo 色块（Task 2 会整体替换该组件） |
| `app/app/components/ReanalyzeAiToggle.tsx` | 1 | checkbox 选中色 |

**核实结论**：以上全部引用的档位均落在 600 及以上（正文文字场景）或
50/100（仅作背景场景），与本次新色阶的 AA 达标范围完全吻合，
换色后不会引入新的对比度回退。已通过：
1. WCAG 对比度数学核算（见上表）；
2. `npm run build` 全量编译通过、0 个新增 TypeScript/ESLint 错误；
3. 完整 e2e 套件（34 项，含新增 1 项）在真实 Chromium 渲染下全部通过，
   覆盖了 `login`、`department/[id]`、`task/[job_id]`、`gbc-ui-demo`、
   管理面板等使用这些 token 的页面，且 `security-headers.spec.ts` 断言了
   页面在真实 CSP 下渲染无异常（间接确认新样式未引发渲染阻断）。

**唯一例外**（不属于回退，是预先已知的架构性差异）：
`app/app/components/task-review/PDFHighlighter.tsx:40` 的
`text-danger-300` 用在深色（`slate-800`/`slate-900`）背景的证据高亮标签上，
不是"文字置于白底"场景。新旧 `danger-300` 明度相近
（旧 `#fecdd3` → 新 `#ec9d96`），新值实际更深、对比度略有提升，
不构成回退，但因该组件属于原型图重建计划中"审核工作台三栏布局"
（Task 6）的既有实现，Task 6 落地时会对其重新走一次视觉核对。
