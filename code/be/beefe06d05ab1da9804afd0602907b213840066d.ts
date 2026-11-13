import { expect, test, type Page } from "../../app/node_modules/playwright/test";

const finance = { id: "finance", name: "上海市财政局", level: "department", children: [
  { id: "unit", name: "预算绩效中心", level: "unit" },
  { id: "service", name: "财政业务服务中心", level: "unit" },
] };
const tree = [{ id: "city", name: "上海市", level: "city", children: [finance,
  { id: "district", name: "浦东新区", level: "district", children: [
    { id: "other-finance", name: "浦东新区财政局", level: "department", children: [{ id: "other-unit", name: "预算绩效中心", level: "unit" }] },
  ] },
  ...Array.from({ length: 8 }, (_, i) => ({ id: `empty-${i + 1}`, name: `测试部门${String(i + 1).padStart(2, "0")}`, level: "department" })),
] }];
const jobs = [
  { job_id: "budget26", organization_id: "finance", organization_name: "上海市财政局", filename: "2026预算.pdf", report_year: 2026, report_kind: "budget", status: "review_required", stage_progress: { phase_label: "质量门禁", percent: 100 }, check_progress: { local: { state: "completed", percent: 100 }, ai: { state: "completed", percent: 100, model: "test-model" } } },
  { job_id: "final25", organization_id: "finance", filename: "2025决算.pdf", report_year: 2025, report_kind: "final", status: "completed" },
  { job_id: "unit24", organization_id: "unit", organization_name: "预算绩效中心", filename: "2024单位决算.pdf", report_year: 2024, report_kind: "final", status: "completed" },
  ...Array.from({ length: 12 }, (_, i) => ({ job_id: `finance-history-${i}`, organization_id: "finance", filename: `历史决算${i}.pdf`, report_year: 2025, report_kind: "final", status: "completed" })),
  { job_id: "other-unit26", organization_id: "other-unit", organization_name: "预算绩效中心", filename: "浦东绩效预算.pdf", report_year: 2026, report_kind: "budget", status: "completed" },
  { job_id: "legacy", filename: "历史文件.pdf", status: "completed" },
];

async function fixture(page: Page) {
  const state = { username: "directory-reviewer", failJobs: false };
  await page.context().addCookies([{ name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax" }]);
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/jobs" && state.failJobs) return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"temporary outage"}' });
    const body = path === "/api/auth/me" ? { user: { username: state.username, is_admin: false } }
      : path === "/api/health" ? { status: "ok" }
        : path === "/api/jobs" ? jobs
          : path === "/api/organizations" ? { tree } : {};
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/reports");
  await expect(page.getByTestId("gbc-reports-region")).toHaveValue("city");
  return state;
}

test("分级目录分页、部门本级/下属单位与面包屑返回", async ({ page }, testInfo) => {
  await fixture(page);
  const grid = page.getByTestId("gbc-reports-directory-grid");
  await expect(grid.locator("button")).toHaveCount(6);
  await page.getByRole("button", { name: "目录下一页", exact: true }).click();
  await expect(grid.locator("button")).toHaveCount(4);
  await expect(page.getByTestId("gbc-reports-directory-pagination")).toContainText("第 2 / 2 页");
  await page.getByTestId("gbc-reports-org-search").fill("上海市财政局");
  await expect(grid.locator("button")).toHaveCount(1);
  await page.getByTestId("gbc-reports-org-finance").click();
  await expect(grid.locator("button")).toHaveCount(3);
  await expect(page.getByTestId("gbc-reports-selected").getByRole("heading", { level: 2 })).toContainText("15 份报告");
  await page.screenshot({ path: testInfo.outputPath("department-directory.png"), fullPage: true });
  await page.getByTestId("gbc-reports-scope-self").click();
  await expect(page.getByTestId("gbc-workbench-queue-row-unit24")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-selected").getByRole("heading", { level: 2 })).toContainText("14 份报告");
  await page.getByTestId("gbc-reports-scope-all").click();
  await expect(page.getByTestId("gbc-workbench-queue-row-unit24")).toBeVisible();
  await page.getByTestId("gbc-reports-org-finance").click();
  await expect(page.getByTestId("gbc-reports-scope-self")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("gbc-reports-org-unit").click();
  await expect(page.getByTestId("gbc-reports-selected-path")).toHaveText("上海市 › 上海市财政局 › 预算绩效中心");
  await expect(page.getByTestId("gbc-workbench-queue-row-unit24")).toBeVisible();
  await page.getByTestId("gbc-reports-breadcrumb-finance").click();
  await expect(page.getByTestId("gbc-reports-scope-all")).toHaveAttribute("aria-pressed", "true");
  await page.getByTestId("gbc-reports-breadcrumb-city").click();
  await expect(grid.locator("button")).toHaveCount(6);
  await page.getByTestId("gbc-reports-org-search").fill("不存在的组织");
  await expect(page.getByTestId("gbc-reports-directory")).toContainText("没有找到匹配");
  await page.getByTestId("gbc-reports-org-search").fill("测试部门08");
  await page.getByTestId("gbc-reports-org-empty-8").click();
  await expect(page.getByTestId("gbc-reports-selected")).toContainText("当前筛选下没有报告");
});

test("搜索同名单位显示真实归属，切换地区，历史原件与复核链接保留", async ({ page }) => {
  await fixture(page);
  await page.getByTestId("gbc-reports-org-search").fill("预算绩效中心");
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(2);
  await expect(page.getByTestId("gbc-reports-org-unit")).toContainText("上海市 › 上海市财政局 › 预算绩效中心");
  await expect(page.getByTestId("gbc-reports-org-other-unit")).toContainText("上海市 › 浦东新区 › 浦东新区财政局 › 预算绩效中心");
  await page.getByTestId("gbc-reports-org-other-unit").click();
  await expect(page.getByTestId("gbc-reports-region")).toHaveValue("district");
  await expect(page.getByTestId("gbc-workbench-queue-row-other-unit26")).toBeVisible();
  await expect(page.getByTestId("gbc-workbench-queue-row-unit24")).toHaveCount(0);
  await page.getByTestId("gbc-reports-org-search").fill("预算绩效中心");
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(1);
  await page.getByTestId("gbc-reports-region").selectOption("city");
  await page.getByTestId("gbc-reports-org-search").fill("预算绩效中心");
  await page.getByTestId("gbc-reports-org-unit").click();
  await expect(page.getByTestId("gbc-workbench-queue-review-unit24")).toHaveAttribute("href", "/review?job=unit24");
  await expect(page.getByTestId("gbc-workbench-queue-row-unit24").getByRole("link", { name: "下载原件" })).toHaveAttribute("href", "/api/files/unit24/source");
  await page.getByTestId("gbc-reports-org-unassigned").click();
  await expect(page.getByTestId("gbc-reports-year-unknown")).toBeVisible();
  await expect(page.getByTestId("gbc-workbench-queue-row-legacy")).toBeVisible();
  await page.getByTestId("gbc-reports-kind-unknown").click();
  await expect(page.getByTestId("gbc-workbench-queue-row-legacy")).toBeVisible();
});

test("收藏按用户保存，检查明细可展开且质量状态不误报", async ({ page }) => {
  const state = await fixture(page);
  await page.getByTestId("gbc-reports-org-search").fill("上海市财政局");
  await page.getByTestId("gbc-reports-org-finance").click();
  const row = page.getByTestId("gbc-workbench-queue-row-budget26");
  await expect(row).toContainText("需要人工复核");
  await expect(row).not.toContainText("分析完成");
  await expect(row.getByTestId("check-progress-ai")).not.toBeVisible();
  await row.getByText("检查详情", { exact: true }).click();
  await expect(row.getByTestId("check-progress-ai")).toBeVisible();
  await expect(row).toContainText("test-model");
  await page.getByTestId("gbc-reports-favorite").click();
  await expect(page.getByTestId("gbc-reports-favorite")).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await page.getByTestId("gbc-reports-directory-favorites").click();
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(1);
  await expect(page.getByTestId("gbc-reports-org-finance")).toBeVisible();
  state.username = "another-reviewer";
  await page.reload();
  await page.getByTestId("gbc-reports-directory-favorites").click();
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(0);
});

test("报告分页和财政年度筛选，刷新失败保留当前数据与目录", async ({ page }) => {
  const state = await fixture(page);
  await page.getByTestId("gbc-reports-org-search").fill("上海市财政局");
  await page.getByTestId("gbc-reports-org-finance").click();
  await expect(page.locator('[data-testid^="gbc-workbench-queue-row-"]').filter({ has: page.locator("td") })).toHaveCount(10);
  await page.getByRole("button", { name: "报告下一页", exact: true }).click();
  await expect(page.getByTestId("gbc-reports-pagination")).toContainText("第 2 / 2 页");
  await expect(page.getByTestId("gbc-workbench-queue-row-budget26")).toHaveCount(0);
  await page.getByTestId("gbc-reports-year").selectOption("2024");
  await expect(page.getByTestId("gbc-workbench-queue-row-unit24")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-pagination")).toHaveCount(0);
  state.failJobs = true;
  await page.getByTestId("gbc-reports-refresh").click();
  await expect(page.getByTestId("gbc-department-reports-page").getByRole("alert")).toContainText("HTTP 503");
  await expect(page.getByTestId("gbc-workbench-queue-row-unit24")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-breadcrumb-finance")).toBeVisible();
  state.failJobs = false;
  await page.getByTestId("gbc-reports-refresh").click();
  await expect(page.getByTestId("gbc-department-reports-page").getByRole("alert")).toHaveCount(0);
  await expect(page.getByTestId("gbc-workbench-queue-row-unit24")).toBeVisible();
});
