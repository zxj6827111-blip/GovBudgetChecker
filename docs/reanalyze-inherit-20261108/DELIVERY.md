# 重新分析检查方式继承 · 修改说明与验证证据

日期：2026-11-09
分支：`codex/reanalyze-inherit-20261108`（worktree：`gbc-worktrees/reanalyze-inherit-20261108`）
基线：运行版 `main@609599e2`（`artifacts/daily-main`，后端 :18743 / 前端 :3000）

> 主工作区落后 main 131 个提交且有 15 个未提交改动，全程未触碰。本修复在独立
> worktree 基于运行版实施；**未合并、未部署、未批量重分析任何历史材料**。

## 一、根因（运行版代码核实）

1. `api/runtime.py` 旧 `reanalyze_job`：dual 模式未显式传 AI 开关时取
   `capabilities()["default_use_ai"] OR 上轮`——系统当前默认开关污染继承；
   `ai_profile_id` 从不继承，静默落到系统当前默认模型。
2. 队列 / 审核台 / 工作台发送 `{mode:"dual", use_local_rules:true}`（不带 AI 开关），
   正好触发上述系统默认逻辑。
3. 口径 / 写法确认后自动重查（`document_scope.dispatch_*`）继承了 AI 开关但漏模型 id。
4. 校正后自动重查空 body 走同一条缺陷逻辑。
5. 前端只有"启用大模型"勾选框，无"按上次方式"选项、无本次模型选择、无上轮配置 /
   执行状态展示。

## 二、继承契约（后端强制，非仅前端勾选框）

请求体两个选择：

- `{"check_method":"inherit"}`（**默认**，缺省即此）：严格按本材料最近一轮已记录的
  启动配置（`status.json` 的 `mode/use_local_rules/use_ai_assist/ai_profile_id/
  ai_configuration`）继承，**不读系统当前 AI 默认开关**。
- `{"check_method":"ai", "ai_profile_id"?:...}`：本次显式本地规则 + 大模型，可在界面
  选择本次模型；缺省模型为系统当前模型（界面已展示该选择）。

判定与保护：

- 历史配置缺失 / 矛盾 → `409`，预览标 `available:false` + `unknown_fields`，不猜测、
  不假装继承。
- 原模型**删除 / 停用 / 缺密钥 / 密钥不可解密** → `422` 明确中文提示，**校验在清理
  产物之前**，已有结果 / 事实快照字节不变。
- 原模型**配置变化**（同 id，model/base_url/密钥等变了）→ 允许执行但响应带
  `inheritance_warnings`，预览标 `model.state:"changed"` 并展示变化字段。
- 上轮 AI **失败 / 部分完成**：仍继承其启用选择，预览 `last_execution.ai_state`
  展示 `failed/degraded`。
- 兼容旧调用方：带显式 `mode/use_*` 键但无 `check_method` 时，显式键优先、缺失键
  从上轮继承（仍不读系统默认）。
- 批量 `reanalyze-all` 默认**逐条继承**；`created[]` 每项带 `check_method` 与
  `inheritance_warnings`，`failed[]` 每项带中文 `detail`，单条失败不影响其他条目。

新增只读预览接口：`GET /api/jobs/{id}/reanalyze-options`（已登记路由冻结清单），
返回上轮配置、执行状态、原模型当前可用性与本次可选模型。Next 代理层新增对应
`app/app/api/jobs/[job_id]/reanalyze-options/route.ts`。

自动重查统一：`document_scope.dispatch_pending` / `dispatch_writing_pending` 改传
`check_method:"inherit"`（含原模型）；校正后 `reanalyze_job(job_id)` 空 body 自动走继承。

## 三、前端统一

新增 `ReanalyzeMethodPicker`（内联）与 `ReanalyzeMethodDialog`（按钮入口）+ 纯逻辑
`reanalyzeOptions.ts`。两选项单选：按上次方式（默认选中，展示上轮摘要 / 执行状态 /
模型警告 / 未知阻断）与使用大模型参与（本次模型下拉）。删除旧 `ReanalyzeAiToggle`。

接入入口：任务详情、审核台、部门页（单条 + 批量）、队列（单条 + 批量）、工作台、
管理端批量重查。批量进度对话框展示逐条继承警告与失败原因；队列页与部门页的单条 /
批量回执也逐条展示成功响应里的 `inheritance_warnings`（带材料名），不再只留在管理端。

## 四、验证证据

| 层级 | 命令 / 范围 | 结果 |
|---|---|---|
| 后端全量（含隔离真库） | `pytest tests/`（`GOVBUDGET_TEST_DATABASE_URL` 指向独立库） | **5625 passed, 33 skipped, 0 failed** |
| 继承场景测试 | `tests/test_reanalyze_inheritance.py` | **20 passed**（系统默认开关双向、模型删除/停用/变化、AI 失败/部分完成、混合批量、历史缺失、显式选择、校验失败不清结果） |
| 口径确认 PG | `tests/test_material_dimensions_pg.py`（真库 + 继承断言） | **3 passed** |
| 前端全量单测链 | `npm run test:unit` | **exit=0**（52 个测试文件通过，含新增 `reanalyzeOptions.test.ts`、颜色门禁 0 违规） |
| 类型 / 规范 | `tsc --noEmit` / `eslint <改动文件>` | **0 错** |
| E2E（真实浏览器） | report-actions / ai-scope-explanation / review-layout-batch / admin-system-management | **20 passed** |
| 真实 HTTP 冒烟 | 隔离后端 :18744 + 独立库，`real-http-smoke.py` | **17/17 passed**（见 `real-http-smoke-results.json`） |
| Next 代理 | 经 :3100 代理请求 `reanalyze-options` | **200**，真实预览 JSON |
| 浏览器走查 | 真实数据打开任务页重分析对话框 | 默认继承选中、上轮摘要、模型变化橙色提示、选项二模型下拉均正确（`reanalyze-dialog-realdata.png`） |

真实 HTTP 冒烟覆盖：系统默认开但上轮仅本地→AI 仍关；继承原 model-a 而非当前
model-b；模型变化告警仍可执行；模型删除 422 且结果字节不变；历史缺失 409 且不变；
显式 AI 选择生效；系统停用 422 且不变。

## 五、保留与边界

- 历史分析版本（`analysis_versions/`）、原件、校正记录（`corrections.jsonl`）、复核
  记录均保留；模型校验失败不清除现有结果（测试逐字节断言）。
- 运行实例 :18743 / :3000 全程未触碰，验证用独立端口 / 独立库 / 独立 uploads，验证
  进程已停止。
- 校正后重查（含队列关闭且允许 inline 回退时）统一经 `runtime.reanalyze_job`：
  先做历史配置判定与模型可用性预检，再由 `start_analysis` 按队列配置派发。原模型
  删除/停用、历史未知时内联与队列路径一致拒绝启动。`structured`（仅结构化入库）
  模式按原样继承。

## 六、复跑指引

```bash
cd gbc-worktrees/reanalyze-inherit-20261108
# 后端（复用 daily-main venv 解释器，pythonpath 指向本 worktree）
<venv>/python -m pytest tests/test_reanalyze_inheritance.py -q
# 前端
cd app && ./node_modules/.bin/jiti tests/reanalyzeOptions.test.ts
./node_modules/.bin/tsc --noEmit
```
## 七、独立复验返工（2026-11-09）

针对独立复验 RI-01 / RI-02 / RI-03 的三项返工：

1. **RI-01 不再自动改选大模型**：删除 `ReanalyzeMethodPicker` 中"继承不可用即
   `onChange({checkMethod:"ai"})`"的 effect。历史未知 / 原模型删除时保留默认选项
   （继承置灰不可选）并禁用确认；只有用户主动选择第二项后才启用提交。显式
   "使用大模型重新分析"快捷入口的 `initialValue` 不受影响。
2. **RI-02 成功响应中的继承告警不再丢弃**：`requestQueueRun` 返回
   `{warnings}`，`runQueueBatch` 逐条保留 `{jobId, filename, messages}`；队列页
   批量结果、单条回执与部门页批量回执均展示材料名 + 告警。
3. **RI-03 内联回退统一预检**：`api/routes/corrections.py` 删除直接
   `runner(job_dir)` 的分支，统一 `await runtime.reanalyze_job(job_id)`。校正记录
   已落盘而派发被拒时返回 `reanalysis_status:"not_executed_dispatch_rejected"`、
   `reanalysis_detail` 与 `review_decisions_status:"unchanged_until_new_analysis_is_published"`，
   不误报已排队。启动失败的日志只记任务 ID（日志门禁 fail-closed，避免把可能含
   材料内容的启动错误整体拼进 message）。
4. **RI-02-B 批量告警不截断**：部门页新增完整批量回执面板
   `department-batch-reanalyze-result`，告警与未提交原因都用可滚动列表逐条展示
   材料名 + 消息；删除 `warnings.slice(0,5)` 与没有实际入口支撑的"其余 N 份见
   队列状态"提示（告警只存在于提交响应，任务状态和队列都不会保存）。批量完成
   的 alert 只给汇总数并指向页面结果面板。

针对性证据（2026-11-09，候选用独立端口，未触碰 :18743 / :3000）：

| 项 | 命令 / 范围 | 结果 |
|---|---|---|
| 后端全量（真库） | `pytest tests/`，临时库 `gbc_full_20261109`（跑完已删） | **5626 passed, 33 skipped, 0 failed**（292.78s） |
| 后端针对性 | 上述 6 个文件 + 复验方 `test_independent_reanalyze_review.py`（`tests/` 下借 conftest 隔离） | **99 passed** |
| 前端全量单测链 | `npm run test:unit`（已把 `test:reanalyze-options` 接入默认链） | **exit=0** |
| 类型 | `tsc --noEmit` | **exit=0** |
| 浏览器回归 | `e2e/tests/reanalyze-inherit-boundaries.spec.ts`（历史未知 / 模型删除 / 正常对照 / 部门页小批量告警 / **部门页 6 条告警不截断**） | **5 passed** |
| 复验方原样断言 | 6 条部门批量告警用例（输出重定向到 /tmp，不覆盖原证据） | **1 passed**（第 6 条 `REVIEW-WARNING-6` 可见） |
| 队列告警 | `review-layout-batch.spec.ts -g 处理队列` | **1 passed**（断言 `gbc-queue-batch-warnings` 含材料名） |
| 既有入口 | `report-actions.spec.ts -g "task detail can trigger reanalysis"`、`ai-scope-explanation.spec.ts` | **1 + 6 passed** |
