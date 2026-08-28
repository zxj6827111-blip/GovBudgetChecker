import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 4：工作台总览 e2e 验证。
 *
 * 覆盖范围（对照任务书 4 测试要求）：
 * 1. 队列行可跳详情（点击文档名跳转到 /queue?job=...）；
 * 2. 筛选生效（状态筛选/年份筛选可缩小队列结果）；
 * 3. 反例：Golden Corpus / 召回率 / 精确率不得出现在页面文本中；
 * 4. 非管理员时活动面板优雅降级，不报错、不崩页。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

interface MockJobsOptions {
  isAdmin: boolean;
  jobs?: Array<Record<string, unknown>>;
  activityStatus?: 200 | 403;
  metricsStatus?: 200 | 403;
}

const DEFAULT_JOBS: Array<Record<string, unknown>> = [
  {
    job_id: "job-review",
    filename: "2026年度部门预算公开说明.pdf",
    status: "review_required",
    quality_status: "review_required",
    organization_name: "市教育局",
    report_year: 2026,
    report_kind: "budget",
    merged_issue_total: 6,
    page_coverage: 1.0,
    stage_progress: { phase: "quality_gate", phase_label: "质量门禁", percent: 92, raw_stage: "完成（需人工复核）" },
  },
  {
    job_id: "job-analyzing",
    filename: "区财政局2025年决算报告.pdf",
    status: "processing",
    organization_name: "浦东新区财政局",
    report_year: 2025,
    report_kind: "final",
    merged_issue_total: 0,
    page_coverage: null,
    stage_progress: { phase: "rule_ai_analysis", phase_label: "规则与 AI 分析", percent: 68, raw_stage: "双模式分析" },
  },
  {
    job_id: "job-unresolved-year",
    filename: "扫描件_预算执行情况说明.pdf",
    status: "error",
    organization_name: "组织待确认",
    report_year: null,
    report_kind: "unknown",
    merged_issue_total: 0,
    page_coverage: 0.3,
    stage_progress: null,
  },
];

async function installWorkbenchMocks(page: Page, options: MockJobsOptions) {
  const jobs = options.jobs ?? DEFAULT_JOBS;
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: options.isAdmin } }),
      });
      return;
    }

    if (path === "/api/health") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      });
      return;
    }

    if (path === "/api/jobs") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(jobs) });
      return;
    }

    if (path === "/api/metrics") {
      const status = options.metricsStatus ?? (options.isAdmin ? 200 : 403);
      if (status !== 200) {
        await route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ detail: "forbidden" }) });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ jobs: { total: 25 }, quality: { unknown_report_kind: { count: 1, ratio: 0.5 } } }),
      });
      return;
    }

    if (path === "/api/activity") {
      const status = options.activityStatus ?? (options.isAdmin ? 200 : 403);
      if (status !== 200) {
        await route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ detail: "forbidden" }) });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            { ts: Date.now() / 1000 - 120, action: "李审核员确认了 4 条问题并提交复核", resource_name: "2026 预算" },
          ],
          total: 1,
        }),
      });
      return;
    }

    if (path === "/api/organizations") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ tree: [{ id: "org-edu", name: "市教育局", level: "department", children: [] }] }),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Workbench overview (Task 4)", () => {
  test("queue row filename links to the queue detail route", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    const link = page.getByTestId("gbc-workbench-queue-row-link-job-review");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", /\/queue\?job=job-review/);
  });

  test("status filter narrows the queue to review_required rows only", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing")).toBeVisible();

    await page.getByTestId("gbc-workbench-status-filter").selectOption("review_required");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing")).toHaveCount(0);
  });

  test("year filter 'unresolved' isolates jobs whose report_year is null", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    await page.getByTestId("gbc-workbench-year-filter").selectOption("unresolved");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-unresolved-year")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toHaveCount(0);
    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing")).toHaveCount(0);
  });

  test("REGRESSION: review_required row must show 需要人工复核, never 分析完成", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    const row = page.getByTestId("gbc-workbench-queue-row-job-review");
    await expect(row).toContainText("需要人工复核");
    await expect(row).not.toContainText("分析完成");
  });

  test("REGRESSION: unresolved report year must render 未识别到, never 2000", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    const row = page.getByTestId("gbc-workbench-queue-row-job-unresolved-year");
    await expect(row).toContainText("未识别到");
    await expect(row).not.toContainText("2000");
  });

  test("REGRESSION: page must never render Golden Corpus / 召回率 / 精确率 (decision 1=b)", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("Golden Corpus");
    expect(bodyText).not.toContain("召回率");
    expect(bodyText).not.toContain("精确率");
  });

  test("REGRESSION: non-admin activity panel degrades gracefully without a page error", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: false });
    await page.goto("/workbench");

    await expect(page.getByTestId("gbc-workbench-activity-needs-admin")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-activity-needs-admin")).toContainText("仅管理员可查看");
    expect(pageErrors).toEqual([]);
  });

  test("admin sees real recent activity entries", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    await expect(page.getByTestId("gbc-workbench-activity-list")).toContainText("李审核员确认了 4 条问题并提交复核");
  });

  // -------------------------------------------------------------------------
  // 修复 B：审核工作台入口（工作台总览队列表一侧）。此前 /review?job= 在 UI 上
  // 完全不可达，本用例验证工作台队列行提供入口且只对已分析出结果的任务开放。
  // -------------------------------------------------------------------------
  test("REGRESSION (fix B): workbench queue rows expose a review entry, disabled for unfinished jobs", async ({
    page,
  }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    // review_required 任务：可点链接进入 /review?job=
    const reviewEntry = page.getByTestId("gbc-workbench-queue-review-job-review");
    await expect(reviewEntry).toBeVisible();
    await expect(reviewEntry).toHaveAttribute("href", /\/review\?job=job-review/);

    // processing 任务：禁用并说明原因
    const analyzingEntry = page.getByTestId("gbc-workbench-queue-review-job-analyzing");
    await expect(analyzingEntry).toBeDisabled();
    await expect(analyzingEntry).toHaveAttribute("title", /尚未分析完成/);

    // failed（error 归一）任务：禁用并说明原因
    const failedEntry = page.getByTestId("gbc-workbench-queue-review-job-unresolved-year");
    await expect(failedEntry).toBeDisabled();
    await expect(failedEntry).toHaveAttribute("title", /失败/);
  });

  test("REGRESSION: KPI cards show em dash while jobs have not loaded, never 0", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ user: { username: "e2e-user", is_admin: true } }),
        });
        return;
      }
      if (url.pathname === "/api/jobs") {
        // 永远不 fulfill /api/jobs，模拟"仍在加载中"的状态
        await new Promise(() => {});
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto("/workbench");
    const reviewCard = page.getByTestId("gbc-workbench-kpi-review-required");
    await expect(reviewCard).toContainText("—");
    await expect(reviewCard).not.toContainText(/(^|[^0-9])0([^0-9]|$)/);
  });
});
