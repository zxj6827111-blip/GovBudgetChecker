import { expect, test, type Page, type Route } from "../../app/node_modules/playwright/test";

/**
 * 审核闭环（WP3-A）端到端验证。
 *
 * 这一层验证什么
 * --------------
 * 前端**接线**：进入工作台是否真的调用了 `start`、点「完成复核」是否真的发起了
 * `POST /complete`（而不是像整改前那样 `router.push("/queue")`）、服务端拒绝时
 * 是否把业务原因逐条显示出来、刷新之后"已完成复核"是否还在。
 *
 * 这一层不验证什么
 * ----------------
 * 服务端的门禁判定（版本是否变、分析代际是否变、问题是否都有人表态、
 * 检查覆盖是否可信）由 pytest 覆盖：`tests/test_review_lifecycle_service.py`
 * （纯逻辑门禁矩阵）与 `tests/test_review_lifecycle_pg.py`（真库并发与失效）。
 * 这里用一个**有状态的假复核服务**模拟服务端结论，因此本文件断言的是
 * "给定服务端这样回答，页面是否如实呈现并对每个按钮发出正确的请求"。
 *
 * 四条用例对应任务书的四段固定链路：
 * 1. 主链路：进入 → 自动开复核 → 确认问题 → 完成 → 刷新仍已完成；
 * 2. 版本替换：完成被拒，页面必须说"材料文件已更新，请重新复核"；
 * 3. 同 job 重分析：旧完成记录失效，页面必须说"分析结果已更新"；
 * 4. 阻塞义务：还有 2 项检查义务未完成时禁止完成，并把条数说出来。
 */

const JOB_ID = "job-review-lifecycle-2026";

/** 会话 Cookie：`/review` 受 routeAuth 保护，没有它会重定向到 /login，
 *  用例就会以"找不到元素"的形式失败（看起来像前端没渲染，实际是没登录）。 */
const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};
const SLOT_ID = "slot-review-lifecycle-2026";

const PNG_1X1_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

interface Blocker {
  code: string;
  count?: number | null;
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

/** 假复核服务的可变状态：模拟一台真实后端在每个动作后的结论。 */
interface ReviewServerState {
  /** 当前进行中的会话；null = 没有。 */
  activeSession: SessionRecord | null;
  /** 历史（已完成/已失效）。 */
  history: SessionRecord[];
  /** 完成时服务端给出的阻塞（用来模拟门禁拒绝）。 */
  completionBlockers: Blocker[];
  /** 进入工作台时是否应该自动开复核。 */
  autoStartAllowed: boolean;
  /** 记录收到的复核请求，供断言"按钮真的打了后端"。 */
  startCalls: number;
  completeCalls: number;
  reopenCalls: number;
  /** 版本替换/重新分析后，页面上的失效提示来源。 */
  invalidatedReason: string | null;
}

function buildSession(overrides: Partial<SessionRecord> = {}): SessionRecord {
  return {
    review_session_id: "sess-e2e-1",
    status: "in_progress",
    slot_id: SLOT_ID,
    document_version_id: 101,
    analysis_job_uuid: JOB_ID,
    analysis_basis_token: `${JOB_ID}:1`,
    started_by: "e2e-reviewer",
    started_at: "2026-09-22T12:00:00+00:00",
    completed_by: null,
    completed_at: null,
    invalidated_at: null,
    invalidated_reason: null,
    review_result: {},
    ...overrides,
  };
}

function buildReviewPayload(state: ReviewServerState, options: { canComplete: boolean }) {
  const blockers: Blocker[] = options.canComplete ? [] : state.completionBlockers;
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
      formal_issue_count: 1,
    },
    current_session: state.activeSession,
    history: state.history,
    completion_gate: { can_complete: options.canComplete, blockers },
  };
}

function buildJobDetail(state: ReviewServerState) {
  return {
    job_id: JOB_ID,
    filename: "2026年度上海市教育局部门预算公开说明.pdf",
    status: "review_required",
    quality_status: "review_required",
    report_year: 2026,
    report_kind: "budget",
    organization_name: "上海市教育局",
    structured_report_id: "GBC-2026-0731-0187",
    stage_progress: { phase: "quality_gate", percent: 100 },
    stage_failed_at: null,
    result: {
      rule_findings: [
        {
          id: "FIN-1",
          rule_id: "GBC-BUD-014",
          severity: "high",
          title: "预算分项合计与公开总额不一致",
          message: "基本支出与项目支出合计为 129,650.00 万元。",
          evidence: [{ page: 12, bbox: [100, 200, 400, 260], text: "基本支出 76,300.00 万元" }],
          location: { page: 12 },
        },
      ],
      meta: { pages: 15 },
    },
    ...(state.invalidatedReason ? {} : {}),
  };
}

/** 安装有状态的假复核服务 + 工作台所需的其余接口。 */
async function installMocks(page: Page, state: ReviewServerState) {
  const workflowIssues: Record<string, { issue_id: string; job_id: string; status: string }> = {};

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
      return json(buildJobDetail(state));
    }
    if (pathname === `/api/jobs/${JOB_ID}/structured-ingest`) {
      return json({ status: "done", review_item_count: 0, review_items: [] });
    }
    if (pathname === "/api/workflow") {
      if (method === "POST") {
        const body = request.postDataJSON() as Record<string, unknown>;
        const issueId = String(body.issue_id ?? "");
        workflowIssues[issueId] = {
          issue_id: issueId,
          job_id: String(body.job_id ?? ""),
          status: String(body.status ?? ""),
        };
      }
      return json({ issues: workflowIssues, packages: [] });
    }
    if (pathname === "/api/reviews") {
      return json({
        ok: true,
        data: {
          review_context: { available: true, reason: null, slot_id: SLOT_ID, job_uuid: JOB_ID },
          review: buildReviewPayload(state, {
            canComplete: state.activeSession !== null && state.completionBlockers.length === 0,
          }),
        },
      });
    }
    if (pathname === `/api/reviews/${SLOT_ID}/start`) {
      state.startCalls += 1;
      if (!state.activeSession && state.autoStartAllowed) {
        state.activeSession = buildSession();
      }
      return json({ ok: true, data: { slot_id: SLOT_ID } });
    }
    if (pathname === `/api/reviews/${SLOT_ID}/complete`) {
      state.completeCalls += 1;
      if (state.completionBlockers.length > 0) {
        return json(
          {
            detail: {
              error: "review_completion_blocked",
              message: "当前无法完成复核",
              blockers: state.completionBlockers,
            },
          },
          409,
        );
      }
      const completed = buildSession({
        status: "completed",
        completed_by: "e2e-reviewer",
        completed_at: "2026-09-22T12:30:00+00:00",
        review_result: { issue_counts: { total: 1, confirmed: 1 } },
      });
      state.activeSession = null;
      state.history = [completed, ...state.history];
      return json({
        ok: true,
        data: buildReviewPayload(state, { canComplete: true }),
      });
    }
    if (pathname === `/api/reviews/${SLOT_ID}/reopen`) {
      state.reopenCalls += 1;
      const previous = state.activeSession ?? state.history[0] ?? null;
      if (previous) {
        state.history = [
          {
            ...previous,
            status: "invalidated",
            invalidated_reason: "review_reopened",
            invalidated_at: "2026-09-22T13:00:00+00:00",
          },
          ...state.history.filter((item) => item.review_session_id !== previous.review_session_id),
        ];
      }
      state.activeSession = buildSession({ review_session_id: "sess-e2e-2" });
      state.invalidatedReason = null;
      return json({ ok: true, data: buildReviewPayload(state, { canComplete: true }) });
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

function freshState(overrides: Partial<ReviewServerState> = {}): ReviewServerState {
  return {
    activeSession: null,
    history: [],
    completionBlockers: [],
    autoStartAllowed: true,
    startCalls: 0,
    completeCalls: 0,
    reopenCalls: 0,
    invalidatedReason: null,
    ...overrides,
  };
}

test.describe("Review lifecycle (WP3-A)", () => {
  test("主链路：进入→自动开复核→确认问题→完成→刷新仍显示已完成", async ({ page }) => {
    const state = freshState();
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);

    // 1. 进入工作台即幂等开复核（§七十四），页面显示"复核中"
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible();
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");
    expect(state.startCalls).toBeGreaterThan(0);

    // 2. 逐个确认问题
    const issuesTab = page.getByTestId("gbc-review-tab-issues");
    await issuesTab.click();
    await page.getByTestId("gbc-review-issue-card-FIN-1").click();
    await page.getByTestId("gbc-review-issue-confirm-FIN-1").click();
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("已确认 1");

    // 3. 完成复核必须真的打到后端（整改前这里只是 router.push("/queue")）
    await page.getByTestId("gbc-review-complete").click();
    await expect(page.getByTestId("gbc-review-completed-summary")).toContainText("已完成复核");
    expect(state.completeCalls).toBe(1);

    // 4. 刷新之后"已完成复核"仍然是持久化事实（本轮的立项缺陷就是这个）
    await page.reload();
    await expect(page.getByTestId("gbc-review-completed-summary")).toContainText("e2e-reviewer");
    await expect(page.getByTestId("gbc-review-complete")).toHaveText("重新复核");
  });

  test("版本替换：完成被拒，页面说明「材料文件已更新，请重新复核」", async ({ page }) => {
    const state = freshState({
      activeSession: buildSession(),
      completionBlockers: [{ code: "document_version_changed" }],
    });
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");

    // 门禁阻塞必须**常驻**显示：按钮在"还有待处理问题"时是灰的，
    // 若只在点击后才显示原因，用户就只能看着一个点不动的按钮。
    const blockers = page.getByTestId("gbc-review-gate-blockers");
    await expect(blockers).toBeVisible();
    await expect(blockers).toContainText("材料文件已更新，请重新复核");
    // 不能只显示 HTTP 状态码 —— 那等于把唯一有用的信息丢掉
    await expect(blockers).not.toContainText("409");
  });

  test("同 job 重分析：旧完成记录失效，页面说明「分析结果已更新」", async ({ page }) => {
    const state = freshState({
      activeSession: null,
      history: [
        buildSession({
          review_session_id: "sess-old",
          status: "invalidated",
          invalidated_reason: "analysis_restarted",
          invalidated_at: "2026-09-22T12:40:00+00:00",
        }),
      ],
      autoStartAllowed: false,
    });
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);

    await expect(page.getByTestId("gbc-review-invalidated-notice")).toContainText(
      "分析结果已更新，需要重新复核",
    );
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核已失效");
  });

  test("阻塞义务：还有 2 项检查义务未完成时禁止完成，并说出条数", async ({ page }) => {
    const state = freshState({
      activeSession: buildSession(),
      completionBlockers: [{ code: "blocking_obligations", count: 2 }],
    });
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);

    const blockers = page.getByTestId("gbc-review-gate-blockers");
    await expect(blockers).toContainText("还有 2 项检查义务未完成");
    // 能力边界要如实说明，不能让用户以为"处理完问题就能完成"
    await expect(blockers).toContainText("人工补核");

    // 按钮此时应当是灰的（即时提示）：点不动也是被禁止完成的正确表现之一
    await expect(page.getByTestId("gbc-review-complete")).toBeDisabled();
  });

  test("重新复核：显式动作才把完成状态改回进行中", async ({ page }) => {
    const state = freshState({
      activeSession: null,
      history: [
        buildSession({
          review_session_id: "sess-done",
          status: "completed",
          completed_by: "e2e-reviewer",
          completed_at: "2026-09-22T12:30:00+00:00",
        }),
      ],
      autoStartAllowed: false,
    });
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, state);

    await page.goto(`/review?job=${JOB_ID}`);
    // 打开页面不会自动把 completed 改回 reviewing（§七十六）
    await expect(page.getByTestId("gbc-review-completed-summary")).toContainText("已完成复核");
    expect(state.startCalls).toBe(0);

    await page.getByTestId("gbc-review-complete").click();
    await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");
    expect(state.reopenCalls).toBe(1);
    // 旧完成记录转为失效并留在历史里（可追溯）
    expect(state.history[0].status).toBe("invalidated");
    expect(state.history[0].invalidated_reason).toBe("review_reopened");
  });
});
