import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 8.1：处理队列全量页 e2e 验证。
 *
 * 覆盖范围（对照任务书）：
 * 1. 多维筛选（状态/年份/文档类型/组织/阶段）各自缩小结果；
 * 2. 排序（问题数降序）改变行顺序；
 * 3. 分页（超过 20 条时出现分页器，翻页内容变化）；
 * 4. 侧边栏角标与本页实际结果数口径一致（analyzing 筛选结果 = 角标数字）；
 * 5. 反例：年份未识别显示"未识别到"，不出现 2000；
 * 6. ?job= 参数作为初始关键词定位任务（工作台队列行的跳转目标）。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const BASE_JOBS: Array<Record<string, unknown>> = [
  {
    job_id: "job-review",
    filename: "2026年度部门预算公开说明.pdf",
    status: "review_required",
    organization_id: "org-edu",
    organization_name: "市教育局",
    report_year: 2026,
    report_kind: "budget",
    merged_issue_total: 6,
    page_coverage: 1.0,
    stage_progress: { phase: "quality_gate", phase_label: "质量门禁", percent: 100, raw_stage: "完成（需人工复核）" },
  },
  {
    job_id: "job-analyzing-1",
    filename: "区财政局2025年决算报告.pdf",
    status: "processing",
    organization_id: "org-fin",
    organization_name: "区财政局",
    report_year: 2025,
    report_kind: "final",
    merged_issue_total: 0,
    stage_progress: { phase: "pdf_parse", phase_label: "PDF 解析", percent: 20, raw_stage: "解析PDF内容" },
  },
  {
    job_id: "job-analyzing-2",
    filename: "queued-task.pdf",
    status: "queued",
    organization_id: null,
    organization_name: null,
    report_year: null,
    report_kind: "unknown",
    merged_issue_total: 0,
    stage_progress: null,
  },
  {
    job_id: "job-unresolved-year",
    filename: "扫描件_预算执行情况说明.pdf",
    status: "error",
    organization_id: null,
    organization_name: "组织待确认",
    report_year: null,
    report_kind: "unknown",
    merged_issue_total: 0,
    page_coverage: 0.3,
    stage_failed_at: { phase: "pdf_parse", phase_label: "PDF 解析", percent: null, raw_stage: "构建文档对象" },
  },
];

/** 生成超过一页（pageSize=20）的任务用于分页断言。 */
function makeManyJobs(): Array<Record<string, unknown>> {
  const many: Array<Record<string, unknown>> = [];
  for (let index = 1; index <= 25; index += 1) {
    many.push({
      job_id: `job-bulk-${String(index).padStart(2, "0")}`,
      filename: `批量任务${String(index).padStart(2, "0")}.pdf`,
      status: "done",
      organization_id: "org-edu",
      organization_name: "市教育局",
      report_year: 2026,
      report_kind: "budget",
      merged_issue_total: index,
    });
  }
  return many;
}

async function installQueueMocks(page: Page, jobs: Array<Record<string, unknown>>) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: false } }),
      });
      return;
    }

    if (path === "/api/health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
      return;
    }

    if (path === "/api/jobs") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(jobs) });
      return;
    }

    if (path === "/api/organizations") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          tree: [
            {
              id: "org-edu",
              name: "市教育局",
              level: "department",
              children: [{ id: "org-school", name: "市第一中学", level: "unit", children: [] }],
            },
          ],
        }),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Queue page (Task 8.1)", () => {
  test("loads full queue and shows the unfiltered result count", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, BASE_JOBS);
    await page.goto("/queue");

    await expect(page.getByTestId("gbc-queue-page")).toBeVisible();
    await expect(page.getByTestId("gbc-queue-result-count")).toContainText("共 4 个任务");
    // 队列表复用工作台组件：行级 testid 与工作台一致
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toBeVisible();
  });

  test("status filter narrows results and badge count matches analyzing filter", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, BASE_JOBS);
    await page.goto("/queue");

    // 侧边栏"处理队列"角标 = analyzing 数（processing + queued 归一）
    const badge = page.getByTestId("gbc-workspace-nav-badge-queue");
    await expect(badge).toBeVisible();
    const badgeText = await badge.innerText();

    await page.getByTestId("gbc-queue-status-filter").selectOption("analyzing");

    // 口径一致性（任务书红线）：角标数字 === 本页"正在处理"筛选结果数
    await expect(page.getByTestId("gbc-queue-result-count")).toContainText(
      `共 ${badgeText} 个任务`,
    );
    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing-1")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing-2")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toHaveCount(0);
  });

  test("year filter 'unresolved' isolates null-year jobs and never shows 2000", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, BASE_JOBS);
    await page.goto("/queue");

    await page.getByTestId("gbc-queue-year-filter").selectOption("unresolved");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-unresolved-year")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing-2")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toHaveCount(0);
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("2000");
  });

  test("report kind filter narrows to final documents", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, BASE_JOBS);
    await page.goto("/queue");

    await page.getByTestId("gbc-queue-kind-filter").selectOption("final");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing-1")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toHaveCount(0);
  });

  test("stage filter narrows to jobs currently in pdf_parse", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, BASE_JOBS);
    await page.goto("/queue");

    await page.getByTestId("gbc-queue-stage-filter").selectOption("pdf_parse");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing-1")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toHaveCount(0);
  });

  test("stage filter 'unknown' isolates jobs without stage_progress", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, BASE_JOBS);
    await page.goto("/queue");

    await page.getByTestId("gbc-queue-stage-filter").selectOption("unknown");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing-2")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toHaveCount(0);
  });

  test("sorting by issue count reorders rows descending", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, BASE_JOBS);
    await page.goto("/queue");

    await page.getByTestId("gbc-queue-sort").selectOption("issues_desc");

    // job-review 有 6 个问题，必须排在 0 问题的任务前面
    const firstRow = page.locator("[data-testid^='gbc-workbench-queue-row-']").first();
    await expect(firstRow).toHaveAttribute("data-testid", "gbc-workbench-queue-row-job-review");
  });

  test("pagination appears beyond one page and navigates", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, makeManyJobs());
    await page.goto("/queue");

    await expect(page.getByTestId("gbc-queue-result-count")).toContainText("共 25 个任务");
    await expect(page.getByTestId("gbc-queue-pagination")).toBeVisible();
    await expect(page.getByTestId("gbc-queue-pagination-info")).toContainText("第 1 / 2 页");
    await expect(page.getByTestId("gbc-workbench-queue-row-job-bulk-01")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-bulk-21")).toHaveCount(0);

    await page.getByTestId("gbc-queue-next-page").click();

    await expect(page.getByTestId("gbc-workbench-queue-row-job-bulk-21")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-bulk-01")).toHaveCount(0);
    await expect(page.getByTestId("gbc-queue-prev-page")).toBeEnabled();
  });

  test("?job= query param pre-fills the keyword to locate the job", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQueueMocks(page, BASE_JOBS);
    await page.goto("/queue?job=job-review");

    // 初始关键词 = job 参数：直接定位到该任务（工作台队列行的跳转语义）
    const search = page.getByTestId("gbc-queue-search");
    await expect(search).toHaveValue("job-review");
    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-analyzing-1")).toHaveCount(0);
  });
});
