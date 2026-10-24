import { expect, test, type Page } from "../../app/node_modules/playwright/test";
import { readFileSync } from "node:fs";

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
  ledger?: boolean;
}

const DEFAULT_JOBS: Array<Record<string, unknown>> = [
  {
    job_id: "job-review",
    filename: "2026年度部门预算公开说明.pdf",
    status: "review_required",
    quality_status: "review_required",
    organization_name: "市教育局",
    organization_id: "org-edu",
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

    if (path === "/api/materials/coverage") {
      if (!options.ledger) {
        await route.fulfill({ status: options.isAdmin ? 503 : 403, contentType: "application/json", body: JSON.stringify({ detail: "unavailable" }) });
      } else {
        const counts = { not_due: 0, missing: 0, uploaded: 0, processing: 1, review_required: 1, reviewing: 1, completed: 4, not_applicable: 0, mapping_required: 0, failed: 1 };
        const selected = url.searchParams.get("status");
        const filtered = selected && selected in counts
          ? Object.fromEntries(Object.keys(counts).map((key) => [key, key === selected ? counts[key as keyof typeof counts] : 0])) : counts;
        const total = Object.values(filtered).reduce((sum, count) => sum + count, 0);
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, data: { summary: {
          slot_total: total, status_counts: filtered, budget_total: total, final_total: 0, unknown_kind_total: 0, due_at_unknown: 0, not_due_confirmed: 0, jurisdiction_unknown_total: 0,
          expected_total: null, expected_budget_total: null, expected_final_total: null, coverage_rate: null, budget_coverage_rate: null, final_coverage_rate: null, missing_expected_total: null,
        }, districts: [] }, meta: { data_basis: "existing_slots_only", expected_materials_ready: false, generated_at: new Date().toISOString() } }) });
      }
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
    await page.getByTestId("gbc-cockpit-all").click();

    const link = page.getByTestId("gbc-workbench-queue-row-link-job-review");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", /\/queue\?job=job-review/);
  });

  test("status filter narrows the queue to review_required rows only", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");
    await page.getByTestId("gbc-cockpit-all").click();

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
    await page.getByTestId("gbc-cockpit-all").click();

    await page.getByTestId("gbc-workbench-year-filter").selectOption("unresolved");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-unresolved-year")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toHaveCount(0);
    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing")).toHaveCount(0);
  });

  test("REGRESSION: review_required row must show 需要人工复核, never 分析完成", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");
    await page.getByTestId("gbc-cockpit-all").click();

    const row = page.getByTestId("gbc-workbench-queue-row-job-review");
    await expect(row).toContainText("需要人工复核");
    await expect(row).not.toContainText("分析完成");
  });

  test("REGRESSION: unresolved report year must render 未识别到, never 2000", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");
    await page.getByTestId("gbc-cockpit-all").click();

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
    await page.getByTestId("gbc-cockpit-activity").click();

    await expect(page.getByTestId("gbc-workbench-activity-needs-admin")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-activity-needs-admin")).toContainText("仅管理员可查看");
    expect(pageErrors).toEqual([]);
  });

  test("admin sees real recent activity entries", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");
    await page.getByTestId("gbc-cockpit-activity").click();

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
    await page.getByTestId("gbc-cockpit-all").click();

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

  test("cockpit landing aggregates data without showing department documents", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");
    await expect(page.getByTestId("gbc-cockpit-kpi-total")).toContainText("3");
    await expect(page.getByTestId("gbc-workbench-page")).not.toContainText("市教育局");
    await expect(page.getByTestId("gbc-workbench-queue-table")).toHaveCount(0);
    await expect(page.getByTestId("gbc-cockpit-state-completed")).toContainText("—");
    await expect(page.getByTestId("gbc-cockpit-ledger-unavailable")).toBeVisible();
  });

  test("formal review progress comes from the ledger, not analysis statuses", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true, ledger: true });
    await page.goto("/workbench");
    await expect(page.getByTestId("gbc-cockpit-kpi-total")).toContainText("8");
    await expect(page.getByTestId("gbc-cockpit-state-completed")).toContainText("4");
    await expect(page.getByTestId("gbc-cockpit-progress")).toContainText("50%");
    await expect(page.getByTestId("gbc-workbench-kpi-review-required")).toContainText("2");
    await page.getByTestId("gbc-cockpit-state-completed").click();
    await expect(page.getByRole("dialog")).toBeVisible();
    const link = page.getByRole("dialog").getByRole("link", { name: /已完成复核/ });
    await expect(link).toHaveAttribute("href", "/materials?status=completed");
    const filteredRequest = page.waitForRequest((request) => request.url().includes("/api/materials/coverage") && request.url().includes("status=completed"));
    await link.click();
    await filteredRequest;
    await expect(page.getByTestId("gbc-material-ledger-page")).toBeVisible();
  });

  test("low coverage risk opens only affected tasks and restores keyboard focus", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");
    const button = page.getByTestId("gbc-cockpit-risk-low-coverage");
    await button.click();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-unresolved-year")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(button).toBeFocused();
  });

  test("department coverage opens linked departments with a report shortcut", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true });
    await page.goto("/workbench");
    await page.getByTestId("gbc-cockpit-kpi-departments").click();
    const department = page.getByTestId("gbc-cockpit-department-org-edu");
    await expect(department).toContainText("已关联材料");
    await expect(department.getByRole("link")).toHaveAttribute("href", "/reports/org-edu");
  });

  test("trend changes period using event timestamps, and exports a real overview", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    const now = Date.now();
    await installWorkbenchMocks(page, { isAdmin: true, jobs: [
      { job_id: "recent", status: "review_required", created_ts: now / 1000 - 100, analysis_finished_ts: now / 1000 - 20 },
      { job_id: "old", status: "done", created_ts: now / 1000 - 10 * 86400, updated_ts: now / 1000 - 30 },
    ] });
    await page.goto("/workbench");
    await expect(page.getByTestId("gbc-cockpit-trend-summary")).toContainText("近7日新增 1 个任务 · 1 个结束分析");
    await page.getByTestId("gbc-cockpit-period-30").click();
    await expect(page.getByTestId("gbc-cockpit-trend-summary")).toContainText("近30日新增 2 个任务 · 1 个结束分析");
    const download = page.waitForEvent("download");
    await page.getByTestId("gbc-cockpit-export").click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/工作台概览_.*\.csv$/);
    const path = await file.path();
    expect(path).not.toBeNull();
    const csv = readFileSync(path!, "utf8");
    expect(csv).toContain('"总量","2"');
    expect(csv).toContain('"已完成人工复核","—"');
    expect(csv).toContain("不代表人工复核完成");
  });

  test("dashboard fits the desktop viewport without a document list below the fold", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 768 });
    await page.context().addCookies([sessionCookie]);
    await installWorkbenchMocks(page, { isAdmin: true, ledger: true });
    await page.goto("/workbench");
    await expect(page.getByTestId("gbc-cockpit-kpi-total")).toContainText("8");
    const geometry = await page.getByTestId("gbc-workbench-page").evaluate((element) => {
      const bounds = element.getBoundingClientRect();
      return { bottom: bounds.bottom, viewport: window.innerHeight, width: element.clientWidth, scroll: element.scrollWidth };
    });
    expect(geometry.scroll).toBeLessThanOrEqual(geometry.width);
    expect(geometry.bottom).toBeLessThanOrEqual(geometry.viewport);
  });
});
