# AI 检查链路最终发布前验收（2026-10-26）

> 本文件是本轮“冻结版本 + S2 补检 + 真实响应回放 + 补检按钮 + 正式启动验证”的最终交付。
> 上一轮的报告 `AI_CHECK_CHAIN_REMEDIATION_R3_20261025.md` 保留为过程记录；以本文件为准。

## 0. 切换结论

**技术启动条件：可以切换（使用生产构建 `next build` + `next start`，不使用 `next dev`）。**

已验证：

- 冻结版本 `next build` 退出码 0；生产 `next start` 在 3012 端口启动，登录页/API 代理/复核页/实际 CSV 均 200；
- 最终版本的真实响应回放、页面解释块、双方证据、结论作用域、CSV 均通过；
- 全量回归 4068 passed / 11 failed / 189 skipped，11 项与基线同名同因，不是本轮回归；
- 构建产物与生产启动不依赖 `next dev`。

**但有一条业务连续性事项必须在切换前确认：**

- 重新分析会生成新的 analysis revision；旧的人工确认记录**保留在数据库历史中**（`workflow_issue_records` 仍有旧 issue_id + note），
  但当前 revision 的 finding ID 会变化，`/api/workflow` 当前视图为空，**不会自动沿用到新 revision**。
- 如果产品要求“重分析后同一问题自动继承人工确认”，这一项仍未完成，不能按“全自动连续”口径切换；
  如果只要求“历史记录不删除、新 revision 重新确认”，则当前实现满足。
- 这是业务口径决定，不是启动阻塞；本报告不把它写成“已通过自动继承”。

## 1. 冻结版本与交付标识

| 项 | 值 |
| --- | --- |
| 主仓库 HEAD（未改） | `57abcf86`（`codex/zcode-remediation`） |
| 验收分支 | `codex/zcode-remediation-r3` |
| 上一轮 checkpoint | `6bed8137` |
| **最终代码冻结提交** | **`5892b7ba`**（`fix(ui): expose recheck entry for rule-only scope banner`） |
| 最终验收 HEAD（含本报告） | 以 `final_manifest.json` 的 `acceptance_head` 为准 |
| 最终补丁（相对 checkpoint，可应用到当前主工作树） | `scratch/ai_remediation_20261025/acceptance3/final_changes.patch` |
| 最终补丁 SHA-256 | 以 `final_manifest.json` 的 `patch_sha256` 为准（补丁包含本报告，报告内不能自洽记录自身哈希） |
| 全量补丁（相对 `57abcf86`，用于干净 checkout） | `scratch/ai_remediation_20261025/acceptance3/final_from_main_head.patch` |
| 全量补丁 SHA-256 | 以 `final_manifest.json` 的 `full_patch_sha256` 为准 |
| 补丁应用检查 | `final_changes.patch` 对当前主工作树 `git apply --check` 通过 |
| 主工作树原有改动 | 3 个文件 + 原有未跟踪文档，未被本轮 reset/覆盖 |

说明：

- `git apply --check` 只证明补丁可应用，不作为功能验收证据；功能验收见 §3–§5。
- 冻结后所有后端/前端/页面/CSV 验收使用 `5892b7ba` 的 worktree。
- `acceptance_runtime/` 与 `app/node_modules` 是运行目录/软链接，不进入提交与补丁。

## 2. S2 第21页目标句受检证据

- 目标句：`管委会行政运行费用按实际情况支出`。
- S2 源任务：`3508b5e02aebe9be6e15f1248a3ac289`。
- 目标 finding：`ai_semantic_3508b5e02aebe9be6e15f1248a3ac289_4_1792982969`（`narrative_consistency`，第21页）。
- **成功 text_semantics 调用**：trace 索引 0，窗口1（页21-28），`text_semantics`，attempt 1，`error=null`，outcome `ok`。
  - prompt 与窗口原文均包含目标句：`prompt_contains_target=true`、`window_text_contains_target=true`。
  - 原始响应 2,956 字，SHA-256 `9767786bb11a94f224a5d38533c8ee7b8d0e5852cad5a1235a6bc1f1e828ea28`。
  - 响应 6 条候选，逐条解析后没有 `fragment` 类型，也没有目标句；目标句没有被误报为残句。
- **失败 text_semantics 调用保留**：窗口0（页1-23）attempt 1/2 均为上游 524，`response_excerpt` 各 600 字；
  因此覆盖仍为 3/4，不能声明“完整审核通过”。
- 结论：目标句实际进入过成功的 text_semantics 调用，**不是“只落在失败窗口”**；“未产生残句误报”成立。
  无需新增模型调用。

证据文件：`S2_target_checks.json`、`S2_target_1.png`。

## 3. 最终版本真实响应回放

> 明确标注：**真实响应回放（replay）**，不是新的线上模型调用。
> 回放使用 S1/S3 原任务的 `ai_audit_trace.jsonl` 中已保存的原始响应，不调用模型；`new_model_calls=0`。

| 样本 | 源任务 | 回放任务 | 原始 AI / 回放 AI | 匹配 | 覆盖 | 持久化 |
| --- | --- | --- | --- | --- | --- | --- |
| S1 | `07818e0d…` | `replay_S1_1792986196` | 7 / 7 | 7/7 | 4/4 complete | true |
| S3 | `5cf642d3…` | `replay_S3_1792986196` | 16 / 16 | 16/16 | 4/4 complete | true |

- 回放只在最终 `AIFindingsService` 上重放成功响应；S1 原始 5 条 trace 中 1 条 524 失败 attempt 被排除，保留其原始失败记录在源任务中。
- 回放结果写入独立任务目录与 `analysis_results`（`persisted=true`），带 `meta.replay`：
  `source_job_id`、`source_trace_sha256`、`new_model_calls=0`、`matched_findings`。
- API 验证：`GET /api/jobs/replay_S1_1792986196`、`/api/jobs/replay_S3_1792986196` 返回回放结果；
  实际 CSV 下载 200，S1 V33-222 行仍为 `165.80/141.12/-24.68`、`program_calculated`；S3 `AI-TEXT-004/005` 页码29、引文完整。
- 页面验证：S1 AI-XSEC-005 显示当前值/应为、`数值来源：模型声称，尚未经程序核实`、第13/20页双方证据；
  S3 两条 p29 文字候选正常显示。
- 回放 payload 不新建 fiscal fact snapshot（`structured_ingest.fact_snapshot=null`），只验证分析结果链，不冒充新的可信事实分析。

证据：`replay_summary.json`、`S1_replay_review.png`、`S1_replay_target_1.png`、`S3_replay_review.png`、`S3_replay_target_1/2.png`、`replay_S1_report.csv`、`replay_S3_report.csv`、`replay_page_evidence.json`。

## 4. 补检按钮真实操作

- 测试任务：`c40b9343f6a89ea553eb7ca5033ecf1d`（1 页小样本，先纯规则分析，21 条规则发现、0 条 AI）。
- 先人工确认一条问题：`edb88f1f-…`（BUD-001），note `recheck-preserve-test`。
- 在最终版本任务页点击 `task-ai-scope-recheck`：
  - 真实提交请求体：`{"use_local_rules":true,"use_ai_assist":true,"ai_profile_id":"model-mv23o9rd-a24n0j9u"}`；
  - 产生新 revision `rev-20261026T034604-afc38fed`（旧 `rev-20261026T034510-e8f963cb` 仍可从 `/analysis-versions` 查到，`total=2`）；
  - 新结果 AI `requested=true`、`state=succeeded`、`attempts=2`、覆盖 2/2；
  - `analysis_versions` 同时保留旧 revision，分析历史未被覆盖。
- 人工确认记录：
  - 旧 decision 仍在 `workflow_issue_records`（旧 issue_id `edb88f1f-…`，note `recheck-preserve-test`）；
  - 但新 revision 的 issue ID 已变化，`/api/workflow?job_id=...` 当前视图为空，没有自动继承。
  - 因此：**历史记录未删除/未覆盖**；**当前 revision 不自动沿用**。切换前需确认业务口径。

证据：`RECHECK_click_result.json`（含请求体与对话框）、`RECHECK_after_status.json`、`RECHECK_before_click.png`、`RECHECK_after_click.png`、`RECHECK_fixture_status_before.json`。

## 5. 正式启动方式

### 5.1 结论：生产构建，不用 next dev

最终版本已实际验证：

```bash
cd app
NEXT_TELEMETRY_DISABLED=1 npm run build      # BUILD_RC=0
PORT=3012 BACKEND_URL=http://127.0.0.1:18745 npm run start
```

- `next build` 退出码 0；`/supervision/remediation` 路由已出现在构建产物中，上一轮的 PageNotFound 未复现。
- `next start` 3012 启动成功；登录页、`POST /api/auth/login`、`/review?job=replay_S1_...`、CSV 下载均 200。
- 生产服务器页面实际验证：banner、AI-XSEC-005 解释块、模型声称、第13/20页双方证据均可见。
- **正式切换采用 `next build + next start`**；`next dev`（当前 3011）只作为开发/复核审查入口，不能作为正式发布方式。
- 后端正式启动也应去掉 `--reload`（当前主实例 18743 仍是旧开发模式，切换时按原启动约定改为无 reload；具体命令见 §7）。

### 5.2 11 项既有失败的影响

11 项集中在：

- 8 项 `test_api_contract.py`（组织/部门/任务关联）；
- 2 项 `test_user_auth_api.py`（管理员敏感操作/删除预览）；
- 1 项 `test_write_endpoint_auth.py`（普通用户关联）。

共同原因是共享开发库累积测试组织不满足“unit must be under a department”，不是启动/构建/核心 AI/CSV 链路错误。
本轮生产构建 + 生产启动 + 复核页 + CSV 均通过，因此它们**不阻塞本次启动**；
但组织管理/用户认证写路径在正式库上仍应在切换窗口做一次冒烟，不能把“本地测试库污染”当成“生产已验证”。

## 6. 测试与证据

- 最终版本后端定向：**373 passed**（与上一轮相同测试集；后端在冻结提交后未再改）。
- 全量：**4068 passed / 11 failed / 189 skipped**（11 项与基线同名同因）。
- 前端：`tsc --noEmit` 通过；`npm run test:unit` 42 个脚本通过；e2e 4 passed。
- e2e 在 **生产构建 3012** 上复跑通过（`e2e_production_frozen.log`）。
- 构建日志：`frontend_build.log`；生产启动日志：`frontend_prod.log`；生产页面证据：`production_page_evidence.json`、`prod_replay_S1.png`。

## 7. 主工作树、数据备份与回滚

### 7.1 已执行的切换前备份

备份目录：`backups/20261026-ai-chain-pre-switch/`（权限 700；`.env` 600）

- 数据库：`fiscal_db.dump`（210 MB，`pg_dump -Fc`），SHA-256 `dd0c8afe6edd9ac57ae24497a9bf68fb76d4db04dbb5d1f6ba13acbaa57936d9`；`pg_restore -l` 可读。
- 上传：`uploads/` 副本 3.0 GB；`data/` 副本 2.6 MB；`.env` 副本。
- 样本哈希核对：S1/S3 PDF、`data/users.json`、`data/ai-model-settings.json` 4/4 与原件一致。
- 主工作树原有改动补丁：`main_worktree_uncommitted.patch`
  - SHA-256 `e3f2416c190199027a4f656b78e6b53f21ac35d59a9266a2bf6ef5c8245aff83`
- 原有未跟踪文档副本：`main_backup/AI_CHECK_CHAIN_REMEDIATION_20261025.md`
  - SHA-256 `2521a31044912b7414fa19dc4c366cc1f566a4bb59c79e19f52835b7ce87b108`
- 备份清单：`backups/20261026-ai-chain-pre-switch/BACKUP_MANIFEST.txt`
- 主工作树现状：3 个原有修改 + 1 个原有未跟踪文档；本轮未 reset、未覆盖。

### 7.2 切换时复核备份（已具备，不再重复复制）

备份已完成；切换窗口只需复核 `BACKUP_MANIFEST.txt` 与样本哈希，必要时重新执行：

```bash
# 1) 数据库
docker exec fiscal_postgres pg_dump -U govbudget_local -Fc fiscal_db \
  > /path/to/backup/fiscal_db_$(date +%Y%m%d_%H%M%S).dump
# 2) 上传与数据目录
rsync -a --delete=false uploads/ /path/to/backup/uploads/
rsync -a --delete=false data/ /path/to/backup/data/
# 3) 记录当前 HEAD 与工作树
git rev-parse HEAD > /path/to/backup/HEAD.txt
git status --porcelain=v1 > /path/to/backup/status.txt
```

### 7.3 切换步骤

1. 选择窗口，停止 18743 及其 reload 子进程（只停这两个 PID，不碰其他实例）。
2. 在**主工作树**应用 `final_changes.patch`（已 `git apply --check` 通过；不要 reset 原有 3 个改动）。
3. 后端去掉 `--reload` 启动；前端执行 `npm run build`，再 `PORT=3000 BACKEND_URL=... npm run start`。
4. 冒烟：登录、打开 replay 页面/S1、检查 banner、AI 解释块、双方证据、CSV。
5. 若失败，恢复 §7.2 的 `uploads/`/`data/`/`.env`，回滚代码到切换前状态；无数据库 schema 迁移。

### 7.4 回滚

- 代码：`git checkout` 回切换前状态；或反向应用补丁。
- 数据：停止服务后恢复 `uploads/`、`data/`、数据库 dump；恢复前不要覆盖备份。
- 不涉及 schema 迁移；新字段为可选 JSON 字段，旧代码读取时忽略。
- 回滚后重跑一次登录 + 复核页 + CSV 冒烟。

## 8. 已知限制与未验收分支

- S2 的 text_semantics 窗口0 仍连续 524；覆盖 3/4，不能声明完整审核通过。目标句已在成功窗口1受检，见 §2。
- 辅助定位真实场景仍为 `eligible=0, attempted=0`；真实“有原文无页码”样本未验收。
- 纯文本窗口不覆盖视觉版式；D-03 一般多字漏字仍是 AI 辅助、结果待人工复核。
- 重分析不自动继承人工确认（历史保留、当前 revision 为空），见 §0/§4，需业务确认。
- 11 项既有失败不阻塞启动/核心链路，但组织/认证写路径未在正式库冒烟。
- 主实例 18743 在 19:55 曾因仓库内 `scratch/*.py` 落盘触发一次 reload；主源码哈希未变，未加载本轮代码。之后所有验收脚本均写在 worktree/非 .py 证据目录，未再触发。
- 未改动主工作树、未重启 18743、未切换生产。

## 9. 证据索引

- 最终版本/补丁/哈希：`final_manifest.json`、`final_changes.patch`、`final_from_main_head.patch`
- S2 目标句：`S2_target_checks.json`
- 真实响应回放：`replay_summary.json`、`replay_page_evidence.json`、`replay_S1_report.csv`、`replay_S3_report.csv`
- 补检按钮：`RECHECK_click_result.json`、`RECHECK_after_status.json`、`RECHECK_after_workflow.json`
- 生产构建/启动：`frontend_build.log`、`frontend_prod.log`、`production_page_evidence.json`、`prod_replay_S1.png`
- 测试：`targeted_pytest_final.log`、`full_pytest_final2.log`、`frontend_unit_frozen.log`、`e2e_production_frozen.log`
