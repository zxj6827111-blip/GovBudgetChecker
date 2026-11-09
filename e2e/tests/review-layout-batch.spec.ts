import { expect, test, type Page } from "../../app/node_modules/playwright/test";

// 浏览器回归只使用合成夹具，所有 API 请求均被拦截，不会重跑真实材料。
const JOB_ID = "layout-fixture";
const verifiedIssue = {
  id: "layout-issue", source: "rule", rule_id: "C-003", severity: "high",
  title: "文字与表格金额不一致", message: "测试夹具：正文与预算表金额不一致，请复核。",
  evidence_status: "complete",
  evidence_verification: {status: "verified", source_identity_complete: true},
  evidence: [{page: 2, bbox: [100, 180, 420, 230], text: "测试金额 100.00 万元", location_level: "exact_span", verification_status: "verified"}],
  location: {page: 2},
};

async function session(page: Page) {
  await page.context().addCookies([{name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax"}]);
}

async function commonResponse(path: string) {
  if (path === "/api/auth/me") return {user: {username: "layout-reviewer", is_admin: true}};
  if (path === "/api/health") return {status: "ok"};
  if (path === "/api/organizations") return {tree: []};
  return {};
}

async function reviewMocks(page: Page) {
  const decisions: Record<string, unknown> = {};
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown) => route.fulfill({status: 200, contentType: "application/json", body: JSON.stringify(body)});
    if (path === `/api/jobs/${JOB_ID}`) return json({
      job_id: JOB_ID, filename: "布局回归测试.pdf", status: "review_required", report_year: 2026,
      result: {
        rule_findings: [verifiedIssue], ai_findings: [],
        // AI 覆盖完整：状态只在状态条显示，这里测的是布局本身。
        meta: {
          pages: 4, ai_execution: {state: "succeeded", requested: true},
          ai_coverage: {status: "complete", planned_calls: 2, succeeded_calls: 2, failed_calls: 0},
          conclusion_scope: "findings_within_covered_checks",
        },
      },
    });
    if (path === "/api/jobs") return json([{job_id: JOB_ID, status: "review_required"}]);
    if (path === "/api/reviews") return json({data: {review_context: {available: true}, review: {
      slot_id: "layout-slot", current_document_version_id: 1,
      current_analysis: {job_uuid: JOB_ID, completed: true, document_version_id: 1, analysis_basis_token: `${JOB_ID}:1`},
      current_session: {status: "in_progress", slot_id: "layout-slot", document_version_id: 1, analysis_job_uuid: JOB_ID, analysis_basis_token: `${JOB_ID}:1`},
      history: [], completion_gate: {can_complete: false, blockers: []},
    }}});
    if (path === "/api/workflow") {
      if (request.method() === "POST") {
        const body = request.postDataJSON();
        decisions[body.issue_id] = {...body};
      }
      return json({issues: decisions});
    }
    if (path.startsWith("/api/files/")) return route.fulfill({
      status: 200, contentType: "image/svg+xml",
      body: `<svg xmlns="http://www.w3.org/2000/svg" width="${600 * Number(new URL(request.url()).searchParams.get("scale") ?? 1.6)}" height="${840 * Number(new URL(request.url()).searchParams.get("scale") ?? 1.6)}"><rect width="100%" height="100%" fill="white"/><text x="70" y="80" fill="gray">Layout fixture</text></svg>`,
    });
    return json(await commonResponse(path));
  });
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

test("原文与完整解释并排：解释区占满问题栏，列表按需展开，放大解释保留原文，证据与工作流保持联动", async ({page}) => {
  await page.setViewportSize({width: 1440, height: 900});
  await session(page);
  await reviewMocks(page);
  await page.goto(`/review?job=${JOB_ID}`);
  await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible();
  await expect(page.getByTestId("gbc-workspace-sidebar")).toHaveAttribute("data-collapsed", "true");
  await expect(page.getByTestId("gbc-review-thumbnail-rail")).toHaveCount(0);
  await expect(page.getByTestId("gbc-review-source-summary")).toHaveCount(0);
  const main = await page.getByTestId("gbc-review-split-pane").boundingBox();
  const problems = await page.getByTestId("gbc-review-problems-pane").boundingBox();
  const detail = await page.getByTestId("gbc-review-issue-detail-pane").boundingBox();
  expect(problems!.width / main!.width).toBeGreaterThan(0.45);
  // 列表默认收起，解释区独占问题栏的大部分高度。
  await expect(page.getByTestId("gbc-review-list-panel")).toHaveCount(0);
  expect(detail!.height / main!.height).toBeGreaterThan(0.6);
  // 打开即选中首条：详情与原文都对应它，不用先点一下。
  await expect(page.getByTestId("gbc-review-issue-detail")).toHaveAttribute("data-detail-problem-id", "layout-issue");
  await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("2");
  await openIssueList(page);
  await expect(page.getByTestId("gbc-review-issues-count")).toContainText("正式问题（1）");
  await expect(page.getByTestId("gbc-review-issue-card-layout-issue")).toHaveAttribute("aria-current", "true");
  await page.getByTestId("gbc-review-close-list").click();

  await page.getByTestId("gbc-workspace-sidebar-toggle").click();
  await expect(page.getByTestId("gbc-workspace-sidebar")).toHaveAttribute("data-collapsed", "false");
  await page.getByTestId("gbc-workspace-sidebar-toggle").click();
  await page.getByTestId("gbc-review-toggle-thumbnails").click();
  await expect(page.getByTestId("gbc-review-thumbnail-rail")).toBeVisible();
  await page.getByTestId("gbc-review-toggle-thumbnails").click();
  await openIssueList(page);
  await page.getByTestId("gbc-review-toggle-source-details").click();
  await expect(page.getByTestId("gbc-review-source-summary")).toBeVisible();
  await page.getByTestId("gbc-review-toggle-source-details").click();
  await page.getByTestId("gbc-review-close-list").click();
  const handle = page.getByTestId("gbc-review-resize-handle");
  await handle.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(handle).toHaveAttribute("aria-valuenow", "48");
  const bounds = await handle.boundingBox();
  await page.mouse.move(bounds!.x + bounds!.width / 2, bounds!.y + 30);
  await page.mouse.down();
  await page.mouse.move(bounds!.x - 70, bounds!.y + 30);
  await page.mouse.up();
  expect(Number(await handle.getAttribute("aria-valuenow"))).toBeLessThan(48);
  await handle.focus();
  await page.keyboard.press("Home");
  await expect(handle).toHaveAttribute("aria-valuenow", "50");
  // 拖动后的宽度按浏览器记住，刷新后仍然生效。
  await page.keyboard.press("ArrowRight");
  await page.reload();
  await expect(page.getByTestId("gbc-review-resize-handle")).toHaveAttribute("aria-valuenow", "52");
  await handle.focus();
  await page.keyboard.press("Home");
  // 放大解释：原文栏收窄但仍在，页签收起；还原后回到原宽度。
  await page.getByTestId("gbc-review-focus-toggle").click();
  await expect(page.getByTestId("gbc-review-document-pane")).toBeVisible();
  await expect(handle).toHaveAttribute("aria-valuenow", "36");
  await expect(page.getByTestId("gbc-review-tabs")).toHaveCount(0);
  await page.getByTestId("gbc-review-focus-toggle").click();
  await expect(handle).toHaveAttribute("aria-valuenow", "50");
  await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("2");
  await expect(page.getByTestId("gbc-review-evidence-highlight")).toBeVisible();
  await page.getByTestId("gbc-review-pdf-zoom-in").click();
  await expect(page.getByTestId("gbc-review-pdf-zoom-level")).toHaveText("125%");
  await expect(page.getByTestId("gbc-review-evidence-highlight")).toBeVisible();
  await page.getByTestId("gbc-review-pdf-fit-width").click();
  await expect(page.getByTestId("gbc-review-pdf-zoom-level")).toHaveText("100%");
  await page.screenshot({path: "output/ui-layout-batch/review-workbench.png"});
  await page.getByTestId("gbc-review-issue-confirm-layout-issue").click();
  await expectStatusCounts(page, "已确认 1");
  await expect(page.getByTestId("gbc-review-issues-empty")).toBeVisible();
  await openIssueList(page);
  await page.getByTestId("gbc-review-source-rule").click();
  await expect(page.getByTestId("gbc-review-issue-card-layout-issue")).toHaveCount(0);
  await openIssueList(page);
  await page.getByTestId("gbc-review-scope-all").click();
  await expect(page.getByTestId("gbc-review-issue-card-layout-issue")).toBeVisible();
  await page.reload();
  await expectStatusCounts(page, "已确认 1");
  await page.getByTestId("gbc-review-tab-more").click();
  await page.getByTestId("gbc-review-tab-metadata").click();
  await expect(page.getByTestId("gbc-review-metadata-tab")).toBeVisible();
  await page.getByTestId("gbc-review-tab-more").click();
  await page.getByTestId("gbc-review-tab-stages").click();
  await expect(page.getByTestId("gbc-review-stage-history-tab")).toBeVisible();
});

test("处理队列：跨页勾选、筛选重置、全选筛选结果、并发防连点、部分失败保留并可重试", async ({page}) => {
  await session(page);
  const jobs = Array.from({length: 25}, (_, index) => ({job_id: `batch-${index + 1}`, filename: `批量任务${index + 1}.pdf`, status: "done"}));
  jobs.push({job_id: "first-analysis", filename: "待分析.pdf", status: "uploaded"}, {job_id: "running", filename: "正在运行.pdf", status: "processing"});
  const calls: Array<{id: string; path: string; body: Record<string, unknown>}> = [];
  let failFirst = true;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) => route.fulfill({status, contentType: "application/json", body: JSON.stringify(body)});
    if (path === "/api/jobs") return json(jobs);
    if (request.method() === "POST" && (path.endsWith("/reanalyze") || path.startsWith("/api/analyze/"))) {
      const id = path.split("/")[3];
      calls.push({id, path, body: request.postDataJSON()});
      await new Promise((resolve) => setTimeout(resolve, 40));
      if (id === "batch-1" && failFirst) return json({detail: {message: "测试：模型配置不可用"}}, 422);
      const job = jobs.find((item) => item.job_id === id)!;
      job.status = "queued";
      if (id === "batch-4") {
        return json({job_id: id, status: "queued", inheritance_warnings: ["原模型配置已变化（model），按上次方式执行将使用当前配置"]});
      }
      return json({job_id: id, status: "queued"});
    }
    return json(await commonResponse(path));
  });
  await page.goto("/queue");
  await expect(page.getByTestId("gbc-queue-result-count")).toContainText("共 27 个任务");
  await expect(page.getByTestId("gbc-queue-batch-run")).toBeDisabled();
  await page.getByTestId("gbc-queue-select-page").check();
  await expect(page.getByTestId("gbc-queue-selected-count")).toContainText("已选 20");
  await page.getByTestId("gbc-queue-clear-selection").click();
  await page.getByTestId("gbc-queue-select-batch-1").check();
  await expect(page.getByTestId("gbc-queue-select-page")).toHaveJSProperty("indeterminate", true);
  await page.getByTestId("gbc-queue-next-page").click();
  await page.getByTestId("gbc-queue-select-batch-21").check();
  await expect(page.getByTestId("gbc-queue-selected-count")).toContainText("已选 2");
  await expect(page.getByTestId("gbc-queue-select-running")).toBeDisabled();
  await page.getByTestId("gbc-queue-search").fill("批量任务");
  await expect(page.getByTestId("gbc-queue-selected-count")).toContainText("已选 0");
  await page.getByTestId("gbc-queue-search").fill("");
  await page.getByTestId("gbc-queue-select-filtered").click();
  await expect(page.getByTestId("gbc-queue-selected-count")).toContainText("已选 26");
  await page.screenshot({path: "output/ui-layout-batch/queue-batch-selection.png"});
  await page.getByTestId("gbc-queue-batch-run").evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  // 批量先经检查方式对话框（默认逐条继承），连点只打开一次对话框，不直接提交。
  await expect(page.getByTestId("reanalyze-method-dialog")).toBeVisible();
  expect(calls).toHaveLength(0);
  await page.getByTestId("reanalyze-method-confirm").click();
  await expect(page.getByTestId("gbc-queue-batch-run")).toBeDisabled();
  await expect(page.getByTestId("gbc-queue-batch-result")).toContainText("已提交 25 个，未提交 1 个");
  expect(calls).toHaveLength(26);
  expect(new Set(calls.map((call) => call.id)).size).toBe(26);
  expect(calls.some((call) => call.id === "running")).toBe(false);
  expect(calls.find((call) => call.id === "first-analysis")!.path).toBe("/api/analyze/first-analysis");
  expect(calls.find((call) => call.id === "batch-2")!.body).toEqual({check_method: "inherit"});
  await expect(page.getByTestId("gbc-queue-batch-result")).toContainText("模型配置不可用");
  // 成功响应里的继承告警必须逐条展示材料名与原因，不能只留在后端响应里。
  await expect(page.getByTestId("gbc-queue-batch-warnings")).toContainText("批量任务4.pdf");
  await expect(page.getByTestId("gbc-queue-batch-warnings")).toContainText("配置已变化");
  await expect(page.getByTestId("gbc-queue-selected-count")).toContainText("已选 1");
  failFirst = false;
  await page.getByTestId("gbc-queue-batch-run").click();
  await expect(page.getByTestId("reanalyze-method-dialog")).toBeVisible();
  await page.getByTestId("reanalyze-method-confirm").click();
  await expect(page.getByTestId("gbc-queue-batch-result")).toContainText("已提交 1 个，未提交 0 个");
  expect(calls).toHaveLength(27);
  expect(calls.filter((call) => call.id === "batch-1")).toHaveLength(2);
  await expect(page.getByTestId("gbc-queue-selected-count")).toContainText("已选 0");
  await expect(page.getByTestId("gbc-workspace-nav-badge-queue")).toContainText("27");
});

test("左右对照在较小窗口中仍有独立滚动的问题阅读区", async ({page}) => {
  await session(page);
  await reviewMocks(page);
  await page.setViewportSize({width: 1024, height: 720});
  await page.goto(`/review?job=${JOB_ID}`);
  await expect(page.getByTestId("gbc-review-issue-detail")).toBeVisible();
  expect(await page.locator("body").evaluate((body) => body.scrollWidth <= window.innerWidth)).toBe(true);
  const height = await page.getByTestId("gbc-review-issue-detail-pane").evaluate((element) => element.clientHeight);
  expect(height).toBeGreaterThan(300);
});
