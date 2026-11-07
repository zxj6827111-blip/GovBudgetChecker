import { expect, test, type Page, type Route } from "../../app/node_modules/playwright/test";

/**
 * 人工补核（WP3-B）端到端验证：补核已并入「必须处理」（“未能自动核对”条目）。
 *
 * 这一层验证什么
 * --------------
 * 前端**接线**：补核条目是否出现在必须处理里、三个结论按钮是否真实调用
 * `PUT /api/reviews/{slot}/obligations/{id}`（携带 job_uuid 声明、乐观锁
 * revision、note 与 evidence_reference）、待办数徽标如何消退、门禁何时放行、
 * 空依据被服务端 422 拒绝时**业务消息必须落到通知条**（评审纪19）。
 *
 * 这一层不验证什么
 * ----------------
 * 服务端的门禁判定、依据纪律、并发与权限由 pytest 覆盖
 * （tests/test_obligation_review_pg.py / test_review_lifecycle_api.py）。
 * 这里的假服务端按同一套对外契约返回，用于断言"页面按服务端答案行动"。
 */

const JOB_ID = "job-obligation-review-e2e";
const SLOT_ID = "slot-obligation-review-e2e";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const PNG_1X1_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

interface ObligationItem {
  obligation_id: string;
  group_id: string;
  group_title: string;
  title: string;
  status: string;
  reason: string | null;
  reason_label: string | null;
  detail: string | null;
  locations?: Array<{ page: number; label: string | null; bbox: number[] | null }>;
  decision: string | null;
  decision_revision: number | null;
  decided_by: string | null;
  decided_at: string | null;
  note: string | null;
  evidence_reference: string | null;
}

interface SessionRecord {
  review_session_id: string;
  status: string;
  slot_id: string;
  document_version_id: number;
  analysis_job_uuid: string;
  analysis_basis_token: string;
  started_by: string;
  started_at: string;
  completed_by: string | null;
  completed_at: string | null;
  invalidated_at: string | null;
  invalidated_reason: string | null;
  review_result: Record<string, unknown>;
}

/** 假复核服务端状态（补核版）。 */
interface ReviewServerState {
  activeSession: SessionRecord | null;
  history: SessionRecord[];
  obligations: ObligationItem[];
  startCalls: number;
  completeCalls: number;
  decideCalls: { obligationId: string; body: Record<string, unknown> }[];
  systemReviewCodes?: string[];
}

const RESOLVED = ["verified_ok", "verified_issue", "not_applicable"];

function pendingCount(state: ReviewServerState): number {
  return state.obligations.filter((item) => !RESOLVED.includes(String(item.decision ?? ""))).length;
}

/** 依据纪律（与服务端一致的最小镜像；权威判定在服务端与 PG 用例）。 */
function evidenceError(decision: string, note: string, evidence: string): string | null {
  if (decision === "pending") return null;
  if (decision === "verified_ok") {
    return note || evidence ? null : "补核通过必须填写补核说明或证据位置（页码/表名/章节）";
  }
  if (decision === "verified_issue") {
    return note ? null : "确认存在问题必须写明发现了什么问题（补核说明）";
  }
  if (decision === "not_applicable") {
    return note ? null : "判定不适用必须写明原因（为什么本材料不涉及该项检查）";
  }
  return "unknown decision";
}

function buildSession(overrides: Partial<SessionRecord> = {}): SessionRecord {
  return {
    review_session_id: "sess-e2e-obl-1",
    status: "in_progress",
    slot_id: SLOT_ID,
    document_version_id: 101,
    analysis_job_uuid: JOB_ID,
    analysis_basis_token: `${JOB_ID}:1`,
    started_by: "e2e-reviewer",
    started_at: "2026-09-23T08:00:00+00:00",
    completed_by: null,
    completed_at: null,
    invalidated_at: null,
    invalidated_reason: null,
    review_result: {},
    ...overrides,
  };
}

function obligationItem(id: string, title: string): ObligationItem {
  return {
    obligation_id: id,
    group_id: "TABLE_CROSS",
    group_title: "表间关系",
    title,
    status: "not_executed",
    reason: "not_executed",
    reason_label: "未执行",
    detail: "该要求尚无规则实现（V33-TEST）",
    decision: null,
    decision_revision: null,
    decided_by: null,
    decided_at: null,
    note: null,
    evidence_reference: null,
  };
}

function buildReviewPayload(state: ReviewServerState) {
  const pending = pendingCount(state);
  const canComplete = state.activeSession !== null && pending === 0;
  return {
    slot_id: SLOT_ID,
    current_document_version_id: 101,
    current_analysis: {
      job_uuid: JOB_ID,
      analysis_revision: 1,
      analysis_basis_token: `${JOB_ID}:1`,
      document_version_id: 101,
      status: "done",
      completed: true,
      formal_issue_count: 0,
    },
    current_session: state.activeSession,
    history: state.history,
    completion_gate: {
      can_complete: canComplete,
      blockers: pending > 0 ? [{ code: "blocking_obligations", count: pending }] : [],
    },
    obligation_review: {
      available: true,
      reason: null,
      pending_total: pending,
      items: state.obligations,
    },
  };
}

async function installMocks(page: Page, state: ReviewServerState) {
  const workflowIssues: Record<string, Record<string, unknown>> = {};
  const handle = async (route: Route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    const { pathname } = new URL(request.url());
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (pathname === "/api/auth/me") {
      return json({ user: { username: "e2e-reviewer", is_admin: true } });
    }
    if (pathname === "/api/health") {
      return json({ status: "ok" });
    }
    if (pathname === `/api/jobs/${JOB_ID}`) {
      return json({
        job_id: JOB_ID,
        filename: "2026年度某单位决算公开说明.pdf",
        status: "review_required",
        quality_status: "review_required",
        report_year: 2026,
        report_kind: "final",
        organization_name: "上海市某单位",
        structured_report_id: "GBC-2026-UNK-0001",
        stage_progress: { phase: "quality_gate", percent: 100 },
        stage_failed_at: null,
        // 本链路聚焦补核：无 issue 待办，问题侧不得再拖住完成按钮。
        result: { rule_findings: [], meta: { pages: 15 } },
        ...(state.systemReviewCodes ? { review_report: {
          issues: [], tips: [],
          manual_review: state.systemReviewCodes.map((code) => ({
            record_type: "review_reason", code, message: `系统提示：${code}`,
          })),
        } } : {}),
      });
    }
    if (pathname === `/api/jobs/${JOB_ID}/structured-ingest`) {
      return json({ status: "done", review_item_count: 0, review_items: [] });
    }
    if (pathname === "/api/workflow") {
      if (method === "POST") {
        const body = request.postDataJSON() as Record<string, unknown>;
        const id = String(body.issue_id ?? "");
        if (!state.systemReviewCodes?.some((code) => id === `review_reason:${code}`)) {
          return json({ detail: "issue_id does not exist" }, 404);
        }
        workflowIssues[id] = { ...body, job_id: JOB_ID };
      }
      return json({ issues: workflowIssues, packages: [] });
    }
    if (pathname === "/api/reviews") {
      return json({
        ok: true,
        data: {
          review_context: { available: true, reason: null, slot_id: SLOT_ID, job_uuid: JOB_ID },
          review: buildReviewPayload(state),
        },
      });
    }
    if (pathname === `/api/reviews/${SLOT_ID}/start`) {
      state.startCalls += 1;
      if (!state.activeSession) {
        state.activeSession = buildSession();
      }
      return json({ ok: true, data: { slot_id: SLOT_ID } });
    }
    const decideMatch = pathname.match(/^\/api\/reviews\/[^/]+\/obligations\/(.+)$/);
    if (decideMatch && method === "PUT") {
      const obligationId = decodeURIComponent(decideMatch[1]);
      const body = (request.postDataJSON() ?? {}) as Record<string, unknown>;
      state.decideCalls.push({ obligationId, body });
      // 前端必须继续携带"待核对声明"（服务端已不再依赖它授权，但仍要核对一致性）
      if (body.job_uuid !== JOB_ID) {
        return json(
          { detail: { error: "review_context_mismatch", message: "该任务不是这条材料当前的复核对象" } },
          409,
        );
      }
      const target = state.obligations.find((item) => item.obligation_id === obligationId);
      if (!target) {
        return json({ detail: "obligation not found in current review" }, 404);
      }
      if (!state.activeSession) {
        return json(
          { detail: { error: "review_not_active", message: "请先开始复核，再处理检查义务" } },
          409,
        );
      }
      const note = String(body.note ?? "").trim();
      const evidence = String(body.evidence_reference ?? "").trim();
      const message = evidenceError(String(body.decision ?? ""), note, evidence);
      if (message) {
        return json(
          { detail: { error: "obligation_decision_evidence_required", message } },
          422,
        );
      }
      target.decision = String(body.decision ?? "");
      target.decision_revision = (target.decision_revision ?? 0) + 1;
      target.decided_by = "e2e-reviewer";
      target.decided_at = "2026-09-23T08:10:00+00:00";
      target.note = note || null;
      target.evidence_reference = evidence || null;
      return json({
        ok: true,
        data: {
          slot_id: SLOT_ID,
          current_document_version_id: 101,
          session: state.activeSession,
          decision: {
            review_session_id: state.activeSession.review_session_id,
            slot_id: SLOT_ID,
            obligation_id: obligationId,
            decision: target.decision,
            note: target.note,
            evidence_reference: target.evidence_reference,
            reviewer: "e2e-reviewer",
            reviewed_at: target.decided_at,
            revision: target.decision_revision,
          },
          completion_gate: buildReviewPayload(state).completion_gate,
          blockers: buildReviewPayload(state).completion_gate.blockers,
        },
      });
    }
    if (pathname === `/api/reviews/${SLOT_ID}/complete`) {
      state.completeCalls += 1;
      const pending = pendingCount(state);
      if (pending > 0) {
        return json(
          {
            detail: {
              error: "review_completion_blocked",
              message: "当前无法完成复核",
              blockers: [{ code: "blocking_obligations", count: pending }],
            },
          },
          409,
        );
      }
      const completed = buildSession({
        status: "completed",
        completed_by: "e2e-reviewer",
        completed_at: "2026-09-23T08:30:00+00:00",
      });
      state.activeSession = null;
      state.history = [completed, ...state.history];
      return json({ ok: true, data: buildReviewPayload(state) });
    }
    if (pathname.startsWith("/api/files/")) {
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: Buffer.from(PNG_1X1_BASE64, "base64"),
      });
    }
    return json({});
  };

  await page.route("**/api/**", handle);
}

function freshState(): ReviewServerState {
  return {
    activeSession: null,
    history: [],
    obligations: [
      obligationItem("OBL-CROSS-SAN-GONG-ECON", "三公经费表与财政拨款经济分类表资金来源一致性"),
      obligationItem("OBL-TXT-FUND-DETAIL", "政府性基金表与对应说明逐项一致"),
    ],
    startCalls: 0,
    completeCalls: 0,
    decideCalls: [],
  };
}

/** 处理计数与完成阻塞收在「完成条件」说明里：打开说明断言后关闭。 */
async function expectStatusCounts(page: Page, text: string) {
  await page.getByTestId("gbc-review-state-progress").click();
  await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText(text);
  await page.getByTestId("gbc-review-info-panel-close").click();
}

/** text 为 null 表示服务端门禁已无阻塞。 */
async function expectGateBlockers(page: Page, text: string | null) {
  await page.getByTestId("gbc-review-state-progress").click();
  if (text === null) await expect(page.getByTestId("gbc-review-gate-blockers")).toHaveCount(0);
  else await expect(page.getByTestId("gbc-review-gate-blockers")).toContainText(text);
  await page.getByTestId("gbc-review-info-panel-close").click();
}

/** 问题列表按需展开：断言列表行之前先展开（已展开时不重复点击）。 */
async function openIssueList(page: Page) {
  const toggle = page.getByTestId("gbc-review-toggle-list");
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  await expect(page.getByTestId("gbc-review-list-panel")).toBeVisible();
}

/** 必须处理里补核条目的 id（与 obligationProblems.obligationProblemId 一致）。 */
const entry = (obligationId: string) => `obligation:${obligationId}`;

test.describe("人工补核（WP3-B）：并入必须处理", () => {
  test("系统提示可处理并保存，处理完未能自动核对的检查后才能结束复核", async ({ page }) => {
    const state = freshState();
    state.systemReviewCodes = [
      "evidence_incomplete_findings", "rule_evidence_incomplete",
      "rules_insufficient_data", "check_obligations_incomplete",
    ];
    state.obligations = [obligationItem("OBL-TXT-T5", "一般公共预算支出决算表与功能分类说明一致")];
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);
    await page.goto(`/review?job=${JOB_ID}`);
    if (!state.activeSession && !state.history.some(item => item.status === "completed")) await page.getByTestId("gbc-review-start").click();
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");
    // 不再有单独的「检查补核」页签。
    await expect(page.getByTestId("gbc-review-tab-obligations")).toHaveCount(0);

    // 系统提示不计入完成条件，放在参考页签。
    await page.getByTestId("gbc-review-tab-reference").click();
    for (const code of state.systemReviewCodes) {
      const id = `review_reason:${code}`;
      await openIssueList(page);
      await page.getByTestId(`gbc-review-issue-card-${id}`).click();
      await page.getByTestId(`gbc-review-issue-confirm-${id}`).click();
      await openIssueList(page);
      await expect(page.getByTestId(`gbc-review-issue-card-${id}`)).toHaveCount(0);
      await expect(page.getByTestId("gbc-review-undo")).toBeEnabled();
      await expect(page.getByTestId("gbc-review-action-notice")).toHaveCount(0);
      await page.getByTestId("gbc-review-close-list").click();
    }
    await page.reload();
    await page.getByTestId("gbc-review-tab-reference").click();
    await expect(page.getByTestId("gbc-review-issues-empty")).toContainText("当前队列已无待处理项");
    await page.getByTestId("gbc-review-view-all").click();
    await openIssueList(page);
    for (const code of state.systemReviewCodes) {
      await expect(page.getByTestId(`gbc-review-issue-card-review_reason:${code}`)).toContainText("已确认");
    }
    await expectGateBlockers(page, "还有 1 项“未能自动核对”的检查待处理");
    await expect(page.getByTestId("gbc-review-complete")).toBeDisabled();

    // 确认有问题：面板预填系统发现的情况，可直接提交。
    await page.getByTestId("gbc-review-tab-issues").click();
    await expect(page.getByTestId("gbc-review-tab-issues")).toContainText("必须处理 1");
    await page.getByTestId(`gbc-review-obligation-verified_issue-${entry("OBL-TXT-T5")}`).click();
    await expect(page.getByTestId("gbc-review-obligation-note")).toHaveValue("该要求尚无规则实现（V33-TEST）");
    await page.getByTestId("gbc-review-obligation-note").fill("说明第 18 页科目名称与表内不一致");
    await page.getByTestId("gbc-review-obligation-save").click();
    expect(state.decideCalls[0].body).toMatchObject({ decision: "verified_issue", note: "说明第 18 页科目名称与表内不一致" });
    await expect(page.getByTestId("gbc-review-complete")).toBeEnabled();
    await page.getByTestId("gbc-review-complete").click();
    await expect(page.getByTestId("gbc-review-completed-summary")).toContainText("e2e-reviewer");
    await page.reload();
    await expect(page.getByTestId("gbc-review-complete")).toHaveText("重新复核");
    expect(state.completeCalls).toBe(1);
  });

  test("主链路：必须处理里逐条核对 → 门禁放行 → 完成复核", async ({ page }) => {
    const state = freshState();
    state.obligations[0].locations = [{ page: 11, label: "测试表", bbox: null }];
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);
    if (!state.activeSession && !state.history.some(item => item.status === "completed")) await page.getByTestId("gbc-review-start").click();
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");
    expect(state.startCalls).toBe(1);

    // 门禁与页签用同一个说法、同一个数。
    await expectGateBlockers(page, "还有 2 项“未能自动核对”的检查待处理");
    await expect(page.getByTestId("gbc-review-complete")).toBeDisabled();
    const tab = page.getByTestId("gbc-review-tab-issues");
    await expect(tab).toContainText("必须处理 2");
    await openIssueList(page);
    await expect(page.getByTestId("gbc-review-group-obligation")).toContainText("未能自动核对（2）");
    await page.getByTestId(`gbc-review-issue-card-${entry("OBL-CROSS-SAN-GONG-ECON")}`).click();

    // 卡片说清：为什么没核对成、请你核对什么、怎么判断、原文在哪。
    const detail = page.getByTestId("gbc-review-issue-detail");
    await expect(detail).toContainText("三公经费表与财政拨款经济分类表资金来源一致性");
    await expect(page.getByTestId("gbc-review-obligation-reason-OBL-CROSS-SAN-GONG-ECON")).toContainText("为什么没能自动核对");
    await expect(page.getByTestId("gbc-obligation-guidance-OBL-CROSS-SAN-GONG-ECON")).toContainText("请你核对");
    await expect(page.getByTestId("gbc-obligation-guidance-OBL-CROSS-SAN-GONG-ECON")).toContainText("怎么判断");
    await expect(detail).toContainText("疑似材料问题");
    const ref = `${entry("OBL-CROSS-SAN-GONG-ECON")}|location|0`;
    await page.getByTestId(`gbc-review-evidence-ref-${entry("OBL-CROSS-SAN-GONG-ECON")}-${ref}`).click();
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("11");
    await expect(page.getByTestId("gbc-review-pdf-locator")).toContainText("未能自动核对");

    // 核对无误（R2.2 A3/IR04）：系统定位只作证据位置附件；仍须经面板写明
    // 核对关系/数字并确认覆盖范围后提交——一键绕过已取消。
    await page.getByTestId(`gbc-review-obligation-verified_ok-${entry("OBL-CROSS-SAN-GONG-ECON")}`).click();
    await expect(page.getByTestId("gbc-review-obligation-panel")).toHaveAttribute("data-obligation-decision", "verified_ok");
    await expect(page.getByTestId("gbc-review-obligation-save")).toBeDisabled();
    await page.getByTestId("gbc-review-obligation-note").fill("第 11 页三公表与基本支出经济分类表逐项比较一致");
    await page.getByTestId("gbc-review-obligation-scope-confirm").check();
    await page.getByTestId("gbc-review-obligation-save").click();
    const firstCall = state.decideCalls[0];
    expect(firstCall.obligationId).toBe("OBL-CROSS-SAN-GONG-ECON");
    expect(firstCall.body.decision).toBe("verified_ok");
    expect(firstCall.body.evidence_reference).toBe("第 11 页 · 测试表（按系统定位核对）");
    expect(firstCall.body.note).toBe("第 11 页三公表与基本支出经济分类表逐项比较一致");
    expect(firstCall.body.scope_confirmed).toBe(true);
    await expect(tab).toContainText("必须处理 1");
    await expectGateBlockers(page, "还有 1 项“未能自动核对”的检查待处理");

    // 处理后接续到下一条；不适用：选原因即可，原因写进补核说明。
    await expect(detail).toHaveAttribute("data-detail-problem-id", entry("OBL-TXT-FUND-DETAIL"));
    await page.getByTestId(`gbc-review-obligation-not_applicable-${entry("OBL-TXT-FUND-DETAIL")}`).click();
    await expect(page.getByTestId("gbc-review-obligation-save")).toBeDisabled();
    await page.getByTestId("gbc-review-obligation-reason-not_involved").check();
    await page.getByTestId("gbc-review-obligation-note").fill("本单位本年度无政府性基金预算");
    // R2.2 A3：整项决定须先确认覆盖范围（"本决定覆盖该义务全部阻断点"）。
    await page.getByTestId("gbc-review-obligation-scope-confirm").check();
    await page.getByTestId("gbc-review-obligation-save").click();
    expect(state.decideCalls[1].body).toMatchObject({ decision: "not_applicable", note: "本材料不涉及此项：本单位本年度无政府性基金预算" });

    // 门禁放行：完成按钮可点，点击后显示已完成复核及复核人
    await expect(tab).toContainText("必须处理 0");
    await expectGateBlockers(page, null);
    const completeButton = page.getByTestId("gbc-review-complete");
    await expect(completeButton).toBeEnabled();
    await completeButton.click();
    await expect(page.getByTestId("gbc-review-completed-summary")).toContainText("已完成复核");
    await expect(page.getByTestId("gbc-review-completed-summary")).toContainText("e2e-reviewer");
    expect(state.completeCalls).toBe(1);
  });

  test("没有系统定位的核对无误必须写一句核对了哪里；已处理的可恢复待处理", async ({ page }) => {
    const state = freshState();
    state.obligations = [obligationItem("OBL-TXT-FUND-DETAIL", "政府性基金表与对应说明逐项一致")];
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);
    if (!state.activeSession && !state.history.some(item => item.status === "completed")) await page.getByTestId("gbc-review-start").click();
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");

    const id = entry("OBL-TXT-FUND-DETAIL");
    await page.getByTestId(`gbc-review-obligation-verified_ok-${id}`).click();
    await expect(page.getByTestId("gbc-review-obligation-panel")).toHaveAttribute("data-obligation-decision", "verified_ok");
    await expect(page.getByTestId("gbc-review-obligation-save")).toBeDisabled();
    expect(state.decideCalls).toHaveLength(0);
    await page.getByTestId("gbc-review-obligation-note").fill("第 20 页基金表与第 26 页说明逐项一致");
    // R2.2 A3：核对无误的面板路径同样须确认覆盖范围后才能提交。
    await page.getByTestId("gbc-review-obligation-scope-confirm").check();
    await page.getByTestId("gbc-review-obligation-save").click();
    expect(state.decideCalls[0].body).toMatchObject({ decision: "verified_ok", note: "第 20 页基金表与第 26 页说明逐项一致" });
    await expect(page.getByTestId("gbc-review-complete")).toBeEnabled();

    // 查看全部条目，恢复待处理后门禁重新阻塞。
    await page.getByTestId("gbc-review-view-all").click();
    await expect(page.getByTestId("gbc-review-issue-detail")).toContainText("已核对无误");
    await page.getByTestId(`gbc-review-obligation-pending-${id}`).click();
    expect(state.decideCalls[1].body.decision).toBe("pending");
    await expect(page.getByTestId("gbc-review-tab-issues")).toContainText("必须处理 1");
    await expect(page.getByTestId("gbc-review-complete")).toBeDisabled();
  });
});
