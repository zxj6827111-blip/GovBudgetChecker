import { expect, test, type Page } from "../../app/node_modules/playwright/test";

const cookie = { name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax" as const };
function fixtureItem(job: string, year: number, status = "pending") {
  return { item_id: `slot:${job}`, slot_id: `slot-${job}`, job_id: job, document_version_id: 101,
    filename: `${year}年度公开材料-${job}.pdf`, organization_id: job, organization_name: "示例教育局",
    department_name: "部门甲", fiscal_year: year, report_kind: "budget", review_status: status,
    pending_findings: 0, pending_checks: 1, reviewer: null, reviewed_at: null, updated_at: "2026-10-12T12:00:00Z" };
}
async function mocks(page: Page) {
  const rows = [fixtureItem("a", 2026), fixtureItem("a2", 2026), fixtureItem("b", 2025),
    fixtureItem("c", 2024, "invalidated"), fixtureItem("done", 2026, "completed"),
    { ...fixtureItem("legacy", 2025, "history_only"), slot_id: null, document_version_id: null },
    fixtureItem("running", 2026, "waiting_analysis")];
  const writes: string[] = [];
  const reads: Record<string, number> = {};
  await page.context().addCookies([cookie]);
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (request.method() !== "GET") writes.push(path);
    else reads[path] = (reads[path] ?? 0) + 1;
    if (path === "/api/auth/me") return json({ user: { username: "reviewer", is_admin: true } });
    if (path === "/api/health") return json({ status: "ok" });
    if (path === "/api/jobs") return json([{ job_id: "a", filename: "示例教育局2026预算.pdf", organization_name: "示例教育局", report_year: 2026, report_kind: "budget", status: "review_required" }]);
    if (path === "/api/reviews/worklist") {
      const counts = Object.fromEntries(["pending","in_progress","invalidated","completed","history_only","waiting_analysis"].map(status => [status, rows.filter(row => row.review_status === status).length]));
      return json({ data: { items: rows, counts, pending_total: counts.pending + counts.in_progress + counts.invalidated, viewer: "reviewer" } });
    }
    if (/\/api\/reviews\/slot-[^/]+\/start$/.test(path)) {
      const row = rows.find(item => item.slot_id === path.split("/")[3])!;
      row.review_status = "in_progress";
      return json({ ok: true });
    }
    if (path === "/api/reviews") {
      const job = new URL(request.url()).searchParams.get("job_uuid")!;
      const row = rows.find(item => item.job_id === job)!;
      if (row.review_status === "history_only") return json({ data: { review_context: { available: false, reason: "review_context_unavailable" }, review: null } });
      const session = { review_session_id: `session-${job}`, slot_id: row.slot_id, document_version_id: 101,
        analysis_job_uuid: job, analysis_basis_token: `${job}:1`, status: row.review_status === "completed" ? "completed" : "in_progress",
        started_by: "reviewer", started_at: "2026-10-12T12:00:00Z", completed_by: "reviewer", completed_at: "2026-10-12T12:00:00Z" };
      return json({ data: { review_context: { available: true, slot_id: row.slot_id, job_uuid: job }, review: {
        slot_id: row.slot_id, current_document_version_id: 101,
        current_analysis: { job_uuid: job, document_version_id: 101, analysis_revision: 1, analysis_basis_token: `${job}:1`, completed: true, formal_issue_count: 0 },
        current_session: row.review_status === "in_progress" ? session : null,
        history: row.review_status === "completed" ? [session] : [],
        completion_gate: { can_complete: false, blockers: [{ code: "blocking_obligations", count: 1 }] },
        obligation_review: { available: true, pending_total: 1, items: [] },
      } } });
    }
    const match = path.match(/^\/api\/jobs\/([^/]+)(\/structured-ingest)?$/);
    if (match) return json(match[2] ? { status: "done", review_items: [] } : {
      job_id: match[1], filename: `材料-${match[1]}.pdf`, status: "review_required", report_year: 2026,
      page_count: 1, result: { issues: [], ai_findings: [], rule_findings: [] },
    });
    if (path === "/api/workflow") return json({ issues: {}, packages: [] });
    if (path.startsWith("/api/files/")) return route.fulfill({ status: 200, contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/></svg>' });
    return json({});
  });
  return { rows, writes, reads };
}

test("侧栏和清单共享读取，切换栏目不重复拉取全量数据", async ({ page }) => {
  const state = await mocks(page);
  await page.goto("/review");
  await expect(page.getByTestId("gbc-review-list-table")).toBeVisible();
  expect(state.reads["/api/reviews/worklist"]).toBe(1);
  expect(state.reads["/api/jobs"]).toBe(1);
  await page.getByTestId("gbc-workspace-nav-queue").click();
  await expect(page.getByTestId("gbc-queue-result-count")).toContainText("1 个任务");
  expect(state.reads["/api/jobs"]).toBe(2);
  expect(state.reads["/api/reviews/worklist"]).toBe(1);
  await page.getByTestId("gbc-workspace-nav-review").click();
  await expect(page.getByTestId("gbc-review-list-table")).toBeVisible();
  expect(state.reads["/api/reviews/worklist"]).toBe(2);
  expect(state.reads["/api/jobs"]).toBe(2);
  expect(state.writes).toEqual([]);
});

test("刷新失败保留上次清单并提示重试，恢复后重新确认待办数", async ({ page }) => {
  await mocks(page);
  await page.goto("/review");
  await expect(page.getByTestId("gbc-review-list-table")).toBeVisible();
  await page.route("**/api/reviews/worklist", route => route.fulfill({ status: 503, contentType: "application/json", body: '{}' }));
  await page.getByRole("button", { name: "刷新清单", exact: true }).click();
  await expect(page.getByTestId("gbc-review-list-error")).toContainText("上次成功读取");
  await expect(page.getByTestId("gbc-review-list-table")).toBeVisible();
  await expect(page.getByTestId("gbc-workspace-nav-badge-review")).toHaveCount(0);
  await page.unroute("**/api/reviews/worklist");
  await page.getByRole("button", { name: "刷新清单", exact: true }).click();
  await expect(page.getByTestId("gbc-review-list-error")).toHaveCount(0);
  await expect(page.getByTestId("gbc-workspace-nav-badge-review")).toHaveText("4");
});

test("一级入口先显示清单，角标统计真实复核待办", async ({ page }) => {
  const state = await mocks(page);
  await page.goto("/queue");
  await page.getByTestId("gbc-workspace-nav-review").click();
  await expect(page).toHaveURL(/\/review$/);
  await expect(page.getByTestId("gbc-review-list-page")).toBeVisible();
  await expect(page.getByTestId("gbc-review-list-table").locator("tbody tr")).toHaveCount(4);
  await expect(page.getByTestId("gbc-workspace-nav-badge-review")).toHaveText("4");
  await expect(page.getByTestId("gbc-workspace-sidebar")).toHaveAttribute("data-collapsed", "false");
  expect(state.writes).toEqual([]);
});

test("预览不创建会话，显式开始后返回仍保留筛选", async ({ page }) => {
  const state = await mocks(page);
  await page.goto("/review?year=2026&q=部门甲");
  await expect(page.getByTestId("gbc-review-list-table").locator("tbody tr")).toHaveCount(2);
  await page.getByTestId("gbc-review-list-open-a").click();
  await expect(page.getByTestId("gbc-review-preview-notice")).toBeVisible();
  expect(state.writes).toEqual([]);
  await expect(page.getByTestId("gbc-review-start")).toBeEnabled();
  await page.getByTestId("gbc-review-start").click();
  await expect(page.getByTestId("gbc-review-lifecycle-badge")).toContainText("复核中");
  expect(state.writes).toEqual(["/api/reviews/slot-a/start"]);
  await expect(page.getByTestId("gbc-review-complete")).toBeDisabled();
  await page.getByTestId("gbc-review-back-button").click();
  await expect(page.getByTestId("gbc-review-list-year")).toHaveValue("2026");
  await expect(page.getByTestId("gbc-review-list-search")).toHaveValue("部门甲");
  await expect(page.getByTestId("gbc-review-list-row-a")).toContainText("复核中");
});

test("下一份遵循当前年度筛选，不跳进其他年度", async ({ page }) => {
  await mocks(page);
  await page.goto("/review?year=2026&q=部门甲");
  await page.getByTestId("gbc-review-list-open-a").click();
  await page.getByTestId("gbc-review-next").click();
  await expect(page).toHaveURL(/job=a2/);
  await expect(page.getByTestId("gbc-review-filename")).toHaveText("材料-a2.pdf");
  await expect(page.getByTestId("gbc-review-next")).toHaveCount(0);
});

test("历史结果保留只读查看，不伪装成可完成的复核", async ({ page }) => {
  const state = await mocks(page);
  await page.goto("/review");
  await page.getByTestId("gbc-review-list-status-history_only").click();
  await page.getByTestId("gbc-review-list-open-legacy").click();
  await expect(page.getByTestId("gbc-review-context-unavailable")).toBeVisible();
  await expect(page.getByTestId("gbc-review-start")).toHaveCount(0);
  await expect(page.getByTestId("gbc-review-complete")).toBeDisabled();
  expect(state.writes).toEqual([]);
});

test("清单读取失败必须显示异常，不能显示零待办", async ({ page }) => {
  await mocks(page);
  await page.route("**/api/reviews/worklist", route => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"database unavailable"}' }));
  await page.goto("/review");
  await expect(page.getByTestId("gbc-review-list-error")).toContainText("暂时不可用");
  await expect(page.getByTestId("gbc-review-list-table")).toHaveCount(0);
  await expect(page.getByTestId("gbc-workspace-nav-badge-review")).toHaveCount(0);
});

test("从处理队列查看材料后返回队列并保留筛选", async ({ page }) => {
  await mocks(page);
  await page.goto("/queue?q=示例教育局&year=2026&kind=budget&status=review_required");
  await page.getByTestId("gbc-workbench-queue-review-a").click();
  await expect(page.getByTestId("gbc-review-preview-notice")).toBeVisible();
  await page.getByTestId("gbc-review-back-button").click();
  await expect(page.getByTestId("gbc-queue-search")).toHaveValue("示例教育局");
  await expect(page.getByTestId("gbc-queue-year-filter")).toHaveValue("2026");
  await expect(page.getByTestId("gbc-queue-status-filter")).toHaveValue("review_required");
});

test("连续操作保留年度条件，小窗口导航可展开与收起", async ({ page }) => {
  await mocks(page);
  await page.setViewportSize({ width: 900, height: 800 });
  await page.goto("/review");
  await page.getByTestId("gbc-review-list-year").selectOption("2026");
  await page.getByTestId("gbc-review-list-search").fill("部门甲");
  await expect(page.getByTestId("gbc-review-list-year")).toHaveValue("2026");
  await expect(page.getByTestId("gbc-review-list-table").locator("tbody tr")).toHaveCount(2);
  await expect(page).toHaveURL(/year=2026/);
  await expect(page.getByTestId("gbc-workspace-sidebar")).toHaveAttribute("data-collapsed", "true");
  await page.getByTestId("gbc-workspace-sidebar-toggle").click();
  await expect(page.getByTestId("gbc-workspace-sidebar")).toHaveAttribute("data-collapsed", "false");
  await page.getByTestId("gbc-workspace-sidebar-toggle").click();
  await expect(page.getByTestId("gbc-workspace-sidebar")).toHaveAttribute("data-collapsed", "true");
});
