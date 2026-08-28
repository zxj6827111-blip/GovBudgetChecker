import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 8.2：任务历史页 e2e 验证。
 *
 * 覆盖范围（对照任务书）：
 * 1. 只显示终态任务（processing/queued 不出现在历史里）；
 * 2. 报告下载入口可达（PDF/CSV/JSON 三格式链接指向既有 /api/reports/download）；
 * 3. 筛选（状态/年份"未识别到"）缩小结果；
 * 4. 反例：review_required 行显示"需要人工复核"，不显示"分析完成"（继承红线）；
 * 5. 普通审核员（非管理员）可访问本页（任务历史不属于管理组）。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const HISTORY_JOBS: Array<Record<string, unknown>> = [
  {
    job_id: "job-done",
    filename: "2026年度部门预算公开说明.pdf",
    status: "done",
    organization_id: "org-edu",
    organization_name: "市教育局",
    report_year: 2026,
    report_kind: "budget",
    merged_issue_total: 6,
    page_coverage: 1.0,
  },
  {
    job_id: "job-review",
    filename: "2025年决算公开说明.pdf",
    status: "review_required",
    organization_id: "org-fin",
    organization_name: "区财政局",
    report_year: 2025,
    report_kind: "final",
    merged_issue_total: 2,
  },
  {
    job_id: "job-error",
    filename: "损坏文件.pdf",
    status: "error",
    organization_id: null,
    organization_name: null,
    report_year: null,
    report_kind: "unknown",
    merged_issue_total: 0,
  },
  {
    // 还在跑的任务：不得出现在任务历史
    job_id: "job-processing",
    filename: "正在处理.pdf",
    status: "processing",
    organization_id: "org-edu",
    organization_name: "市教育局",
    report_year: 2026,
    report_kind: "budget",
    merged_issue_total: 0,
  },
];

async function installHistoryMocks(page: Page) {
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
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(HISTORY_JOBS),
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

test.describe("History page (Task 8.2)", () => {
  test("shows only terminal jobs with download entries for each format", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installHistoryMocks(page);
    await page.goto("/history");

    await expect(page.getByTestId("gbc-history-page")).toBeVisible();
    // 4 个任务里 3 个终态
    await expect(page.getByTestId("gbc-history-result-count")).toContainText("共 3 个终态任务");
    await expect(page.getByTestId("gbc-history-result-count")).toContainText("总 4 个任务");

    // 还在跑的任务不得出现
    await expect(page.getByTestId("gbc-workbench-queue-row-job-processing")).toHaveCount(0);

    // 每个终态任务行渲染三种格式的下载链接，指向既有 /api/reports/download
    const pdfLink = page.getByTestId("gbc-history-download-job-done-pdf");
    await expect(pdfLink).toBeVisible();
    await expect(pdfLink).toHaveAttribute(
      "href",
      "/api/reports/download?job_id=job-done&format=pdf",
    );
    await expect(page.getByTestId("gbc-history-download-job-done-csv")).toHaveAttribute(
      "href",
      "/api/reports/download?job_id=job-done&format=csv",
    );
    await expect(page.getByTestId("gbc-history-download-job-done-json")).toHaveAttribute(
      "href",
      "/api/reports/download?job_id=job-done&format=json",
    );
    // 失败任务同样提供下载入口（错误报告也是交付物）
    await expect(page.getByTestId("gbc-history-download-job-error-pdf")).toBeVisible();
  });

  test("status filter narrows to review_required rows only", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installHistoryMocks(page);
    await page.goto("/history");

    await page.getByTestId("gbc-history-status-filter").selectOption("review_required");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-review")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-done")).toHaveCount(0);
    await expect(page.getByTestId("gbc-history-result-count")).toContainText("共 1 个终态任务");
  });

  test("year filter 'unresolved' isolates null-year terminal jobs", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installHistoryMocks(page);
    await page.goto("/history");

    await page.getByTestId("gbc-history-year-filter").selectOption("unresolved");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-error")).toBeVisible();
    await expect(page.getByTestId("gbc-workbench-queue-row-job-done")).toHaveCount(0);
    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("2000");
  });

  test("REGRESSION: review_required row shows 需要人工复核, never 分析完成", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installHistoryMocks(page);
    await page.goto("/history");

    const row = page.getByTestId("gbc-workbench-queue-row-job-review");
    await expect(row).toContainText("需要人工复核");
    await expect(row).not.toContainText("分析完成");
  });

  test("non-admin reviewer can access the history page", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installHistoryMocks(page);
    await page.goto("/history");

    // 普通审核员可访问（mock 的 is_admin=false），页面不重定向
    await expect(page).toHaveURL(/\/history/);
    await expect(page.getByTestId("gbc-history-page")).toBeVisible();
  });
});
