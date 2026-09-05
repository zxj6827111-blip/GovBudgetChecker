# 剩余工作处置方案：A3 统一存储 + B2 夹具清理（批次二前置材料）

- 日期：2026-08-28
- 用途：批次一交付（额外交付）。A3（两套工作流忽略存储并存）与 B2（删除类
  操作涉及的测试夹具）只出方案、不执行；批次二按本方案 + 用户确认后实施。
- 关联：`docs/UI_REDESIGN_BATCH3_DELIVERY_2026-08-28.md` §3.4（两套机制语义
  判断的原始调研）、`docs/UI_SCREEN_COVERAGE_MAPPING_2026-08-28.md`（删除清单）。

---

## A3. 两套工作流忽略存储的统一方案（只出方案，不动数据）

### A3.1 现状（代码实测，2026-08-28）

| 维度 | 机制 A（新，状态标记） | 机制 B（旧，过滤） |
|---|---|---|
| 入口 | `POST /api/workflow`（`api/routes/workflow.py`） | `POST /api/jobs/{job_id}/issues/ignore`（`api/routes/jobs.py:412`） |
| 实现 | `src/services/issue_workflow_store.py` | `api/runtime.py:523` `ignore_job_issue` |
| 存储 | 全局单文件 `UPLOAD_ROOT/.issue_workflow.json`，键 `{job_id}::{issue_id}`；另有持久化状态文件 `.issue_workflow_persistence.json` 与 DB 镜像（有界等待） | 每任务 `job_dir/ignored_issues.json`（`api/runtime.py:100`），内容为 issue_id 集合 |
| 语义 | **状态标记**：五态 `pending/confirmed/no_issue/needs_review/in_package` + `note`；问题仍在结果里 | **过滤剔除**：`apply_job_issue_filters`（`api/runtime.py:500`）把被忽略问题从返回 payload 整个剔除 |
| 消费方 | 新审核工作台（确认/忽略/补充意见三操作） | 旧详情页 `EvidencePanel`「忽略此问题」（`task-ignore-issue-button`） |

### A3.2 交叉不一致（真实存在，两个方向都有）

- **读方向（旧→新）**：`apply_job_issue_filters` 挂在 `collect_job_summary`
  （`api/runtime.py:1466`，新工作台队列表的问题计数来源）与任务结果读取
  （`api/runtime.py:2357`，审核工作台问题列表来源）上。旧页"忽略"过的问题在
  新 UI **直接消失**，而 workflow store 里没有任何记录——新台既看不到也恢复不了它。
- **读方向（新→旧）**：新台标 `no_issue` 的问题在旧页问题列表**仍然显示**。
- **写方向**：两端点互不感知（`get_visible_state()` 不查 ignored，`ignore_job_issue`
  不查 workflow），同一条问题可能同时是"workflow pending"与"已忽略"。

### A3.3 方案对比

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **S1（推荐）：读兼容收敛到 workflow store** | ① `apply_job_issue_filters` 改为"叠加读"：除 `ignored_issues.json` 外，把 workflow store 里 `no_issue` 的条目一并视为过滤目标（读路径统一）；② 旧页忽略端点改为转调 workflow store（`status=no_issue`），`.ignored_issues.json` 只读不再写；③ 新增一次性迁移脚本：扫描所有 `ignored_issues.json`，把其中的 issue_id 以 `no_issue` 写入 workflow store（幂等，键相同即跳过），成功后把原文件改名为 `ignored_issues.json.migrated` 留档（不删除） | 单一真相源；读路径向后兼容（历史忽略行为不回退）；脚本可回滚（改名回去即可） | 需要动 `apply_job_issue_filters`（热路径，有缓存，须同步失效缓存键）与旧端点行为 |
| S2：仅统一写入口，读维持两份 | 新台/旧台写入都落到 workflow store，读时合并两个来源 | 改动最小 | 两份存储永久并存，"问题为什么消失"要查两处，长期心智负担大 |
| S3：下线旧页后放任并存 | 批次二删旧页后不再有人写机制 B，历史 `ignored_issues.json` 保留原样 | 零开发量 | **不成立**：`apply_job_issue_filters` 是新 UI 的读路径，历史忽略文件会继续让问题在新 UI 静默消失；S3 只能作为 S1 的退化兜底 |

推荐 **S1**，理由：它把"过滤语义"翻译成 workflow store 的 `no_issue` 状态，
读路径收敛后，`apply_job_issue_filters` 可以在未来某个版本彻底退役；
迁移脚本幂等且可回滚，不满足"动历史数据"的风险要求时可以先只落 ①②（读兼容），
迁移脚本单独跑。

### A3.4 实施步骤（批次二，待确认后执行）

1. `src/services/issue_workflow_store.py` 暴露只读查询 `get_no_issue_keys() -> Set[str]`
   （内存态 + 文件读，带既有锁）。
2. `api/runtime.py` `apply_job_issue_filters`：过滤集 = `read_ignored_issue_ids`
   ∪ `get_no_issue_keys()` 中属于当前 job 的部分；注意 `_JOB_SUMMARY_CACHE`
   缓存键需纳入 workflow store 的更新时间戳，避免脏缓存。
3. `api/routes/jobs.py:412` 旧忽略端点：保留路由（兼容外部调用），内部改调
   workflow store `update_issue(status="no_issue")`，响应体维持旧字段形状。
4. 迁移脚本 `scripts/migrate_ignored_issues_to_workflow.py`：干跑（`--dry-run`
   默认开）→ 实跑 → 逐文件改名留档；输出迁移统计。
5. 测试：`tests/` 补三组——叠加过滤正例、迁移脚本幂等、缓存键失效。
6. 回滚：还原 `apply_job_issue_filters` 与旧端点两处 diff 即可；已迁移数据靠
   `.migrated` 留档文件可整体还原。

## B2. 夹具/测试清理方案（随 A2 删除执行）

实测引用面（2026-08-28）：

| 类别 | 现状 | 批次二处理 |
|---|---|---|
| e2e 整文件 | `e2e/tests/gbc-ui-demo-actions.spec.ts`、`gbc-ui-demo-upload.spec.ts` 直接测旧单体 | **整体删除**（能力已被 `upload-center.spec.ts`、`review-workbench.spec.ts` 等覆盖） |
| e2e 局部 | `admin-system-management.spec.ts:486`、`admin-organization.spec.ts:341,422,480,559`、`archive-page.spec.ts:7,134,139`、`full-flow-review-export.spec.ts:178` 访问/断言旧单体 URL | 改指向新页（对照表 §2 的映射）；`archive-page.spec` 的断言对象已是新 `/archive`，只需清理旧 URL 引用 |
| e2e 预热 | `scripts/run-e2e.cjs:21-25` WARMUP_PATHS 含 `/viewer/gbc-ui-demo` | 移除该项 |
| 前端单测 | `app/tests/loginNextPath.test.ts:21` 把 `/viewer/gbc-ui-demo` 当合法深链正例 | 改为其他站内深链（如 `/archive`） |
| 前端单测 | `app/tests/hardcodedColorGuard.test.ts` 注释把 `viewer/*`、`task-review/*` 列为豁免目录 | 删除完成后把豁免注释与新令牌清单同步；`GUARDED_DIRS` 可顺势纳入空出的路径 |
| 后端 | `tests/fixtures/replay/*` 与旧 UI 无耦合；`tests/` 无对 `ignore_job_issue` 旧行为的破坏性依赖（A3 若实施，按 A3.4-5 补测试） | 无需处理 |

e2e 基线对照：当前全量 73 passed；批次二删除 2 个整文件 spec 后，净减少的
用例数应等于这两个文件内的用例数，其余 71+ 项必须保持通过。

## 附：其他遗留决策点（不在本批，仅备忘）

1. **450 个 uploaded 残留任务**处置（删除/归档/保留）待用户决策，见
   `ui-status-polling` 相关交付说明。
2. **YAML 与引擎规则编号分裂**（B1 执行中发现）：`rules/budget_v3_3_draft.yaml`
   里 BUD-001/BUD-002 分列"九张表/必备章节"，而引擎把两分支共用
   `BUD-001`（`budget_rules.py:1052`），`/api/rules/entries` 暴露的是 YAML 口径。
   B1 的统计锚选了引擎口径；若后续要做规则元数据注册表，需先统一编号体系。
3. `data/organizations.json.bak.20260308-125300` 为 git 跟踪的数据备份文件，
   非代码 .bak，未纳入任何删除清单，建议单独确认。
