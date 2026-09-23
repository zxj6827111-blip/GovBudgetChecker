# WP3-B：人工 obligation review（人工补核）

日期：2026-09-23
分支：`feat/manual-obligation-review-wp3b`
前置：WP3-A（PR #46，已以 merge commit `1484295` 合入 main）
迁移：`2026-09-23_0021_review_obligation_decisions`（additive，见 docs/MIGRATION_0021_REVIEW_OBLIGATION_DECISIONS.md）

## 目标（任务书原文的落点）

把当前「`coverage blocking_count > 0` ⇒ 阻塞 complete」升级为
「人工确认 obligation ⇒ 完成补核 ⇒ Review Complete 可通过」。

## 一句话设计

**给每条引擎判阻塞的检查义务一个人工结论，结论绑定"哪一次复核"
（`review_session_id`），写进 PostgreSQL，完成门禁只认当前有效会话的结论。**

```
引擎判阻塞未完成（blocks_gate ∧ unresolved）
        │
        ▼  人工显式四选一（没有任何自动路径）
pending（默认，无记录）── 继续阻塞
verified_ok        ──┐
verified_issue      ├─ 已处理 ⇒ 该义务放行
not_applicable     ──┘
```

## 数据结构复用（不重造第二套 obligation 模型，任务书 §六）

审校确认的义务现状，本批全部沿用：

- **引擎层** `src/engine/check_obligations.py`：版本化义务清单 + 账本，
  `blocking_total` 口径 = `blocks_gate ∧ status ∈ UNRESOLVED_OBLIGATION_STATUSES`；
- **落库点** `analysis_jobs.metadata.result_meta.obligation_coverage`（属于一次分析运行）；
- **解析层** `material_detail_query_service.build_coverage` → `CoverageBlock`
  （`available / summary / items`），门禁与补核用同一次解析的结果，
  不允许各处各算（任务书 §十六）。

新增的只有**人工结论**这一层：`review_obligation_decisions` 表 +
`obligation_review` 响应块。义务本体（标题/分组/为什么阻塞）永远来自引擎账本，
决定表只存"谁、什么时候、判了什么、依据是什么"。

## 完成门禁口径变化（任务书 §十一）

| | WP3-A | WP3-B |
| --- | --- | --- |
| 覆盖不可用 | `coverage_unavailable` 阻塞 | 不变（**禁止**自动 not_applicable） |
| 阻塞义务存在 | `blocking_obligations`（count=引擎阻塞数） | `blocking_obligations`（count=**未处理数**） |
| 放行条件 | 引擎阻塞数 = 0 | 当前有效会话里每条阻塞义务都有已处理结论 |

`count` 语义变化是故意的：页面上「检查补核」页签的待办清单与 409 的
`count` 必须说的是同一批条目。

## 会话绑定与失效（任务书 §十/§十六）

- 决定挂 `review_session_id`；重开复核 / 版本替换 / 重新分析都会让旧会话失效，
  旧决定**保留在表里**（审计追溯），但新会话的门禁看不到它们——
  "换人复核"不会继承上一轮的结论；
- 服务层在会话身份变化处（start 新建/复用、reopen、懒失效）重新绑定
  决定集合，保证 `ReviewState.obligation_decisions` 永远是"当前这一次复核"的；
- 完成复核的快照同时记两本账：`blocking_engine_total`（引擎判出多少）
  与 `manual_decisions`（人工怎么处理的），`blocking_obligations_closed_by`
  在 `automatic_only` / `automatic_and_manual` 间如实取值。

## 接口

```
PUT /api/reviews/{slot_id}/obligations/{obligation_id}
  body: { decision, note?, evidence_reference?, expected_revision?, job_uuid? }
  200 → ObligationDecisionData（决定 + 重算后的完成门禁）
  401 未登录 / 403 槽位越权（MaterialAccessScope，与 start/complete 同源）
  404 义务不在当前复核范围 / 409 review_not_active、review_completed_locked、
      obligation_not_blocking、obligation_decision_conflict（含 current_revision）
  422 参数（decision 不在四态内等）
```

读取侧无新接口：`GET /api/reviews*` 的响应体多了 `obligation_review` 块
（`available / pending_total / items[]`，每项含当前决定与 `decision_revision`）。

## 并发（任务书 §十五：唯一约束 + 乐观锁，两道都要）

1. 库约束：`uq_review_obligation_decisions_scope (review_session_id, obligation_id)`——
   两人同时首次表态，后到者拿唯一冲突 → 409，先到者的结论优先；
2. 乐观锁：改写必须带 `expected_revision`，不匹配/缺省 → 409 + `current_revision`；
3. 槽位行锁把同槽位的补核写入与完成复核串行化（沿用 WP3-A 全局锁序：
   `material_slots → … → review_sessions → review_obligation_decisions → analysis_jobs`）。

## 审计（任务书 §十三）

复用同一套 JSONL 审计（`append_audit_event`），不建第二套：

- 成功：`obligation.reviewed`（首次）/ `obligation.updated`（改写），
  由**服务层提交成功后**写入（与 `review.complete` 同一纪律：单写入口、
  提交前不写"已成功"）；details 含 session/义务/结论/revision/复核人备注与依据；
- 拒绝：`obligation.review` + `result="rejected"`，路由层留痕（谁尝试过什么）。

## 独立评审整改（2026-09-23 第二轮）

1. **写路径任务层权限不再可绕过（P1）**。此前 start/complete/reopen/PUT 补核
   只在请求体带 `job_uuid` 时才做 job 层权限检查——省略即豁免。现统一为：
   路由先按槽位当前版本由服务端解析 candidate（`resolve_current_analysis`），
   对 candidate 强制 `user_can_access_job`（无权 403），再把 candidate 传给
   service；service 在事务内 `_require_job_belongs_to_state` 二次校验——
   "授权看 A、事务里变成 B"以 409 `review_context_mismatch` 收尾（TOCTOU 闭合）。
2. **依据纪律（P1/P2）**。resolved 决定不允许零依据放开阻塞：
   `verified_ok` 需 note 或 evidence_reference；`verified_issue` 需 note
   （确认问题必须写明是什么问题）；`not_applicable` 需 note（改变适用性必须
   给原因）；`pending` 不需要依据（置回待处理不产生放行效果）。违例一律
   结构化 422 `obligation_decision_evidence_required`（请求结构合法、缺业务
   必填字段属输入校验；全接口统一 422）。依据写入数据库与审计，未另建日志。
3. **前端**。补核行分设「补核说明」与「证据位置/引用」两个输入；面板常驻
   依据纪律提示；按钮不禁用（判定在服务端），422 消息落通知条。
4. **E2E 真链**：`e2e/tests/obligation-review.spec.ts`——补核两项义务到
   门禁放行并完成复核的完整链路 + 空依据 422 展示用例。

## 验证

- 真库 PG 用例：`tests/test_obligation_review_pg.py`（9 条，0 skipped）——
  迁移 0021 表结构与幂等、决定持久化、门禁集成、会话绑定、并发首次表态、
  乐观锁、审计恰好一条；与 WP3-A 用例一起进 CI 硬门禁
  （`Review lifecycle (real PostgreSQL)`，67 passed / 0 skipped）；
- 纯逻辑：`tests/test_review_lifecycle_service.py` 新增 4 条门禁用例
  （pending/未知值阻塞、三类已处理放行、非阻塞义务不误伤）；
- API 契约：`tests/test_review_lifecycle_api.py` 新增 7 条
  （401/403/404/409/422 + 审计留痕、成功不在路由层重复记审计）；
- 前端：`app/tests/reviewObligationAdapters.test.ts`（done）。

## 明确不做（任务书 §五）

WP9 / checker 算法优化 / Golden 扩展 / Archive hard gate / PDF preview & download /
全量采集 / CSV 导入 / 组织体系重构 / Material Slot schema 修改，本轮一律不碰。
