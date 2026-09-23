import { expect, test, type Page, type Route } from "../../app/node_modules/playwright/test";

/**
 * 人工补核（WP3-B）端到端验证。
 *
 * 这一层验证什么
 * --------------
 * 前端**接线**：义务面板是否出现、补核按钮是否真实调用
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
      });
    }
    if (pathname === `/api/jobs/${JOB_ID}/structured-ingest`) {
      return json({ status: "done", review_item_count: 0, review_items: [] });
    }
    if (pathname === "/api/workflow") {
      return json({ issues: {}, packages: [] });
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

test.describe("人工补核（WP3-B）", () => {
  test("主链路：补核两项义务 → 门禁放行 → 完成复核", async ({ page }) => {
    const state = freshState();
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);

    // 进入工作台即自动开复核（WP3-A 口径），顶部显示"复核中"
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");
    expect(state.startCalls).toBe(1);

    // 门禁常驻提示：2 项待补核
    await expect(page.getByTestId("gbc-review-gate-blockers")).toContainText(
      "还有 2 项检查义务待人工补核",
    );
    await expect(page.getByTestId("gbc-review-complete")).toBeDisabled();

    // 打开「检查补核」页签，徽标带待办数
    const tab = page.getByTestId("gbc-review-tab-obligations");
    await expect(tab).toContainText("检查补核 (2)");
    await tab.click();
    await expect(page.getByTestId("gbc-obligation-summary")).toContainText("待补核 2 项");

    // 第一项：填写说明 + 证据位置 → 补核通过
    await page.getByTestId("gbc-obligation-note-OBL-CROSS-SAN-GONG-ECON").fill("已对照两表逐项核对");
    await page
      .getByTestId("gbc-obligation-evidence-OBL-CROSS-SAN-GONG-ECON")
      .fill("第 12 页 表 8");
    await page.getByTestId("gbc-obligation-verify-ok-OBL-CROSS-SAN-GONG-ECON").click();

    // 服务端收到了完整的补核写入（含乐观锁声明与依据）
    const firstCall = state.decideCalls[0];
    expect(firstCall.obligationId).toBe("OBL-CROSS-SAN-GONG-ECON");
    expect(firstCall.body.decision).toBe("verified_ok");
    expect(firstCall.body.note).toBe("已对照两表逐项核对");
    expect(firstCall.body.evidence_reference).toBe("第 12 页 表 8");

    await expect(tab).toContainText("检查补核 (1)");
    await expect(page.getByTestId("gbc-obligation-summary")).toContainText("待补核 1 项");
    await expect(page.getByTestId("gbc-review-gate-blockers")).toContainText(
      "还有 1 项检查义务待人工补核",
    );

    // 第二项：写明原因 → 人工判定不适用
    await page.getByTestId("gbc-obligation-note-OBL-TXT-FUND-DETAIL").fill("本单位本年度无政府性基金预算");
    await page.getByTestId("gbc-obligation-not-applicable-OBL-TXT-FUND-DETAIL").click();

    await expect(tab).toContainText("检查补核");
    await expect(tab).not.toContainText("(1)");
    await expect(page.getByTestId("gbc-obligation-summary")).toContainText("待补核 0 项");

    // 门禁放行：完成按钮可点，点击后显示已完成复核及复核人
    await expect(page.getByTestId("gbc-review-gate-blockers")).toHaveCount(0);
    const completeButton = page.getByTestId("gbc-review-complete");
    await expect(completeButton).toBeEnabled();
    await completeButton.click();
    await expect(page.getByTestId("gbc-review-completed-summary")).toContainText("已完成复核");
    await expect(page.getByTestId("gbc-review-completed-summary")).toContainText("e2e-reviewer");
    expect(state.completeCalls).toBe(1);
  });

  test("空依据拒绝：「不适用」不填理由时业务消息必须展示", async ({ page }) => {
    const state = freshState();
    state.obligations = [obligationItem("OBL-TXT-FUND-DETAIL", "政府性基金表与对应说明逐项一致")];
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");

    await page.getByTestId("gbc-review-tab-obligations").click();

    // 面板上有静态依据纪律提示（点击前的明确告知）
    await expect(page.getByTestId("gbc-obligation-evidence-policy")).toContainText("依据纪律");

    // 不填任何说明直接点「不适用」：服务端 422 的消息必须落到通知条，
    // 且义务保持待处理、门禁不放行（评审纪19：不许"点击无反应"）。
    await page.getByTestId("gbc-obligation-not-applicable-OBL-TXT-FUND-DETAIL").click();
    const notice = page.getByTestId("gbc-review-action-notice");
    await expect(notice).toContainText("判定不适用必须写明原因");
    await expect(page.getByTestId("gbc-obligation-summary")).toContainText("待补核 1 项");
    await expect(page.getByTestId("gbc-review-complete")).toBeDisabled();
  });
});
