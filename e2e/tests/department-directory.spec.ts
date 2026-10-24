import { expect, test, type Page } from "../../app/node_modules/playwright/test";

const finance = { id: "finance", name: "上海市财政局", level: "department", children: [
  { id: "unit", name: "预算绩效中心", level: "unit" },
  { id: "service", name: "财政业务服务中心", level: "unit" },
] };
const tree = [{ id: "city", name: "上海市", level: "city", children: [finance,
  { id: "district", name: "浦东新区", level: "district", children: [
    { id: "other-finance", name: "浦东新区财政局", level: "department", children: [{ id: "other-unit", name: "预算绩效中心", level: "unit" }] },
  ] },
  ...Array.from({ length: 28 }, (_, i) => ({ id: `empty-${i + 1}`, name: `测试部门${String(i + 1).padStart(2, "0")}`, level: "department" })),
] }];
const jobs = [
  { job_id: "budget26", organization_id: "finance", organization_name: "上海市财政局", filename: "2026预算.pdf", report_year: 2026, report_kind: "budget", status: "review_required", stage_progress: { phase_label: "质量门禁", percent: 100 }, check_progress: { local: { state: "completed", percent: 100 }, ai: { state: "completed", percent: 100, model: "test-model" } } },
  { job_id: "final25", organization_id: "finance", filename: "2025决算.pdf", report_year: 2025, report_kind: "final", status: "completed" },
  { job_id: "unit24", organization_id: "unit", organization_name: "预算绩效中心", filename: "2024单位决算.pdf", report_year: 2024, report_kind: "final", status: "completed" },
  { job_id: "own-unit23", organization_id: "finance", organization_name: "上海市财政局", filename: "2023本级单位预算.pdf", report_year: 2023, report_kind: "budget", material_scope: "unit_self", status: "completed" },
  { job_id: "unknown22", organization_id: "finance", filename: "2022口径待确认.pdf", report_year: 2022, material_scope: "unknown", status: "completed" },
  ...Array.from({ length: 12 }, (_, i) => ({ job_id: `finance-history-${i}`, organization_id: "finance", filename: `历史决算${i}.pdf`, report_year: 2010 + i, report_kind: "final", status: "completed" })),
  { job_id: "other-unit26", organization_id: "other-unit", organization_name: "预算绩效中心", filename: "浦东绩效预算.pdf", report_year: 2026, report_kind: "budget", status: "completed" },
  { job_id: "legacy", filename: "历史文件.pdf", status: "completed" },
];

const reviewOf = (jobId: string) => jobId === "final25" ? { status: "completed", confirmed_count: 3 } : { status: "pending", confirmed_count: null as number | null };

function materialPage(params: URLSearchParams) {
  const parents: Record<string, string> = { finance: "city", unit: "finance", service: "finance", district: "city", "other-finance": "district", "other-unit": "other-finance" };
  const descendant = (id: string, org: string) => { const seen = new Set<string>(); while (id && !seen.has(id)) { if (id === org) return true; seen.add(id); id = parents[id]; } return false; };
  const raw = jobs as Array<{ job_id: string; organization_id?: string; organization_name?: string; filename?: string; report_year?: number; report_kind?: string; status: string; material_scope?: string }>;
  const items = raw.map(job => ({ item_id: `slot:${job.job_id}`, slot_id: job.job_id === "legacy" ? null : job.job_id, job_id: job.job_id, organization_id: job.organization_id, organization_name: job.organization_name, filename: job.filename, fiscal_year: job.report_year ?? null, report_year: job.report_year ?? null, report_kind: job.report_kind ?? "unknown", material_scope: job.material_scope ?? (job.organization_id?.includes("unit") ? "unit_self" : "department_summary"), version_count: 1, analysis_task_count: 1, review: reviewOf(job.job_id), current_version: { document_version_id: 1, version_number: 1, is_current: true, filename: job.filename, file_hash: "b".repeat(64), source_job_id: job.job_id, current_run: { job_id: job.job_id, status: job.status, analysis_revision: 1 }, review: reviewOf(job.job_id) } }));
  const org = params.get("org") ?? "";
  const scope = params.get("management_scope") ?? "all";
  const region = params.get("management_region") ?? "";
  const managementItems = scope === "all" ? items : items.filter(item => item.organization_id && (scope === "district") === descendant(item.organization_id, "district") && (!region || descendant(item.organization_id, region)));
  const orgItems = managementItems.filter(item => !org || (org === "unassigned" ? !item.organization_id : params.get("org_scope") === "descendants" ? descendant(item.organization_id ?? "", org) : item.organization_id === org));
  const category = params.get("report_category") ?? "all";
  const scoped = orgItems.filter(item => category === "all" || item.material_scope === (category === "department" ? "department_summary" : "unit_self"));
  const years = [...new Set(scoped.flatMap(item => item.fiscal_year ? [item.fiscal_year] : []))].sort((a,b) => b-a);
  const selected = scoped.filter(item => (!params.get("year") || String(item.fiscal_year ?? "unknown") === params.get("year")) && (!params.get("kind") || params.get("kind") === "all" || item.report_kind === params.get("kind"))).sort((a,b) => (b.fiscal_year ?? 0)-(a.fiscal_year ?? 0));
  const size = Number(params.get("page_size") ?? 10), pageCount = Math.ceil(selected.length/size), page = Math.min(Number(params.get("page") ?? 1), Math.max(pageCount,1));
  const counts: Record<string,number> = {};
  for (const item of managementItems) if (item.organization_id) counts[item.organization_id] = (counts[item.organization_id] ?? 0)+1;
  const unitCounts: Record<string, number> = {};
  for (const item of managementItems) if (item.organization_id && item.material_scope === "unit_self") unitCounts[item.organization_id] = (unitCounts[item.organization_id] ?? 0) + 1;
  // 与接口同口径：每个组织的复核状态计数，部门汇总/单位本级口径下预算、决算各自最新年度的状态。
  const reviewStatus: Record<string, { review_counts: Record<string, number>; unit_review_counts: Record<string, number>; latest: Record<string, unknown> }> = {};
  const latestYear: Record<string, number> = {};
  for (const item of managementItems) {
    if (!item.slot_id || !item.organization_id) continue;
    const entry = reviewStatus[item.organization_id] ??= { review_counts: {}, unit_review_counts: {}, latest: {} };
    const status = item.review.status;
    entry.review_counts[status] = (entry.review_counts[status] ?? 0) + 1;
    if (item.material_scope === "unit_self") entry.unit_review_counts[status] = (entry.unit_review_counts[status] ?? 0) + 1;
    const key = `${item.material_scope}:${item.report_kind}`;
    if (!["department_summary", "unit_self"].includes(item.material_scope) || !["budget", "final"].includes(item.report_kind) || !item.fiscal_year) continue;
    if ((latestYear[`${item.organization_id}|${key}`] ?? 0) >= item.fiscal_year) continue;
    latestYear[`${item.organization_id}|${key}`] = item.fiscal_year;
    entry.latest[key] = { fiscal_year: item.fiscal_year, review_status: status, confirmed_count: item.review.confirmed_count, run_status: item.current_version.current_run.status };
  }
  const reviewStatusCounts: Record<string, number> = {};
  for (const item of scoped) if (item.slot_id) reviewStatusCounts[item.review.status] = (reviewStatusCounts[item.review.status] ?? 0) + 1;
  return { ok: true, data: { items: selected.slice((page-1)*size,page*size), total: selected.length, page, page_size: size, page_count: pageCount, years, has_unknown_year: scoped.some(item => !item.fiscal_year), organization_report_counts: counts, organization_unit_report_counts: unitCounts, organization_review_status: reviewStatus, review_status_counts: reviewStatusCounts, uncategorized_total: orgItems.filter(item => item.material_scope === "unknown").length, material_total: scoped.filter(item => item.slot_id).length, legacy_total: scoped.filter(item => !item.slot_id).length, unassigned_total: items.filter(item => !item.organization_id).length, activity: { running: 0, waiting: 0 } } };
}

async function fixture(page: Page) {
  const state = { username: "directory-reviewer", failJobs: false, isAdmin: false };
  await page.context().addCookies([{ name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax" }]);
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    // 年度报送功能默认关闭：capabilities 返回 404 时页面回落历史材料查询。
    // 兜底的 200 {} 会让入口把 enabled 当成 undefined，误判为已启用并渲染新视图，
    // 于是本用例要断言的台账 testid 一个都不出现。
    if (url.pathname === "/api/reporting-tasks/capabilities") {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "reporting tasks disabled" }),
      });
      return;
    }
    const path = url.pathname;
    if (path === "/api/materials/reports" && state.failJobs) return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"temporary outage"}' });
    if (path.startsWith("/api/materials/reports/") && path.endsWith("/history")) {
      const id = path.split("/")[4];
      const item = materialPage(new URLSearchParams({ page_size: "100" })).data.items.find(item => item.slot_id === id);
      return route.fulfill({ json: { ok: true, data: { versions: item ? [{ ...item.current_version, runs: [item.current_version.current_run], reviews: [] }] : [] } } });
    }
    const body = path === "/api/materials/reports" ? materialPage(url.searchParams) : path === "/api/auth/me" ? { user: { username: state.username, is_admin: state.isAdmin } }
      : path === "/api/health" ? { status: "ok" }
        : path === "/api/jobs" ? jobs
          : path === "/api/organizations" ? { tree } : {};
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/reports");
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button").first()).toBeVisible();
  return state;
}

test("分级目录分页、部门本级/下属单位与面包屑返回", async ({ page }, testInfo) => {
  await fixture(page);
  const grid = page.getByTestId("gbc-reports-directory-grid");
  await expect(grid.locator("button")).toHaveCount(24);
  // 首页只做查找与进入：不出现材料列表、地区单选下拉和范围提示条。
  await expect(page.getByTestId("gbc-reports-selected")).toHaveCount(0);
  await expect(page.locator('[data-testid^="material-report-"]')).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-region")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-management-summary")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-org-all")).toHaveAttribute("href", "/reports/city");
  // 目录状态：默认待复核优先；部门卡片看部门汇总口径的最新预算、决算，没有材料的部门不显示徽章。
  await expect(grid.locator("button").first()).toHaveAttribute("data-testid", "gbc-reports-org-finance");
  await expect(page.getByTestId("gbc-reports-org-finance-budget")).toHaveText("预算 2026 · 待复核");
  await expect(page.getByTestId("gbc-reports-org-finance-final")).toHaveText("决算 2025 · 已复核 · 3 项问题");
  await expect(page.getByTestId("gbc-reports-org-finance-open")).toHaveText("16 份待复核");
  await expect(page.getByTestId("gbc-reports-org-finance")).toContainText("17 份材料");
  await expect(page.getByTestId("gbc-reports-org-empty-1-budget")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-summary-materials")).toContainText("17");
  await expect(page.getByTestId("gbc-reports-summary-open")).toContainText("16");
  await expect(page.getByTestId("gbc-reports-summary-completed")).toContainText("1");
  await expect(page.getByTestId("gbc-reports-summary-waiting")).toContainText("0");
  await page.getByRole("button", { name: "目录下一页", exact: true }).click();
  await expect(grid.locator("button")).toHaveCount(5);
  await expect(page.getByTestId("gbc-reports-directory-pagination")).toContainText("第 2 / 2 页");
  await page.getByTestId("gbc-reports-org-search").fill("上海市财政局");
  await expect(grid.locator("button")).toHaveCount(1);
  await page.getByTestId("gbc-reports-org-finance").click();
  await expect(page).toHaveURL(/\/reports\/finance$/);
  await expect(page.getByTestId("gbc-reports-directory")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-view-grid")).toHaveCount(0);
  await expect(grid).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-selected-path")).toHaveText("上海市 › 上海市财政局");
  await expect(page.getByTestId("gbc-reports-selected-path")).toBeInViewport();
  await expect(page.getByTestId("gbc-reports-selected").getByRole("heading", { level: 1 })).toContainText("14 条材料记录");
  await expect(page.getByTestId("gbc-reports-selected")).toContainText("本范围已登记材料 14 份");
  await expect(page.getByTestId("gbc-reports-selected")).toContainText("另有 1 条材料口径待确认");
  // 部门汇总按年度卡片：默认最新年度，预算、决算各一张；没有的写“暂无材料”。
  await expect(page.getByTestId("gbc-reports-year-tab-2024")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-year-tab-2026")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("material-report-budget26")).toContainText("2026 年预算");
  await expect(page.getByTestId("material-report-empty-final")).toContainText("2026 年决算");
  await expect(page.getByTestId("gbc-reports-year")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("department-detail.png"), fullPage: true });
  await page.getByRole("tab", { name: "下属单位", exact: true }).click();
  await expect(page).toHaveURL(/\/reports\/finance\?tab=units$/);
  await expect(grid.locator("button")).toHaveCount(3);
  // 下属单位目录看单位本级口径：部门本级卡片固定第一。
  await expect(grid.locator("button").first()).toHaveAttribute("data-testid", "gbc-reports-org-finance");
  await expect(page.getByTestId("gbc-reports-org-finance-budget")).toHaveText("预算 2023 · 待复核");
  await expect(page.getByTestId("gbc-reports-org-finance-final")).toHaveText("决算 · 暂无");
  await expect(page.getByTestId("gbc-reports-org-unit-final")).toHaveText("决算 2024 · 待复核");
  await expect(page.getByTestId("gbc-reports-org-unit-open")).toHaveText("1 份待复核");
  await expect(page.getByTestId("gbc-reports-org-service-budget")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-summary")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-org-finance")).toContainText("1 份材料");
  await expect(page.getByTestId("material-report-unit24")).toBeVisible();
  await expect(page.getByTestId("material-report-own-unit23")).toBeVisible();
  await expect(page.getByTestId("material-report-budget26")).toHaveCount(0);
  await expect(page.getByTestId("material-report-unknown22")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-selected").getByRole("heading", { level: 1 })).toContainText("2 条材料记录");
  await page.getByTestId("gbc-reports-org-finance").click();
  await expect(page.getByTestId("gbc-reports-selected").getByRole("heading", { level: 1 })).toContainText("1 条材料记录");
  await expect(page.getByTestId("material-report-own-unit23")).toBeVisible();
  await expect(page.getByTestId("material-report-unit24")).toHaveCount(0);
  await page.getByTestId("gbc-reports-org-unit").click();
  await expect(page).toHaveURL(/\/reports\/unit$/);
  await expect(page.getByTestId("gbc-reports-selected-path")).toHaveText("上海市 › 上海市财政局 › 预算绩效中心");
  await expect(page.getByTestId("material-report-unit24")).toBeVisible();
  // 浏览器后退回到部门本级口径，前进回到单位。
  await page.goBack();
  await expect(page).toHaveURL(/\/reports\/finance\?self=1$/);
  await expect(page.getByTestId("gbc-reports-selected").getByRole("heading", { level: 1 })).toContainText("1 条材料记录");
  await expect(page.getByTestId("gbc-reports-org-finance")).toHaveAttribute("aria-pressed", "true");
  await page.goForward();
  await expect(page.getByTestId("gbc-reports-selected-path")).toHaveText("上海市 › 上海市财政局 › 预算绩效中心");
  await expect(page.getByTestId("material-report-unit24")).toBeVisible();
  await page.getByRole("tab", { name: "部门", exact: true }).click();
  await expect(page.getByTestId("gbc-reports-selected-path")).toHaveText("上海市 › 上海市财政局");
  await expect(page.getByTestId("gbc-reports-selected").getByRole("heading", { level: 1 })).toContainText("14 条材料记录");
  await expect(grid).toHaveCount(0);
  await page.getByTestId("gbc-reports-back-directory").click();
  await expect(page).toHaveURL(/\/reports$/);
  await expect(grid.locator("button")).toHaveCount(24);
  await page.getByTestId("gbc-reports-org-search").fill("不存在的组织");
  await expect(page.getByTestId("gbc-reports-directory")).toContainText("没有找到匹配");
  await page.getByTestId("gbc-reports-org-search").fill("测试部门08");
  await page.getByTestId("gbc-reports-org-empty-8").click();
  await expect(page.getByTestId("gbc-reports-selected")).toContainText("这里还没有材料");
});

test("搜索同名单位显示真实归属，切换地区，历史原件与复核链接保留", async ({ page }) => {
  await fixture(page);
  await page.getByTestId("gbc-reports-org-search").fill("预算绩效中心");
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(1);
  await expect(page.getByTestId("gbc-reports-org-unit")).toContainText("上海市 › 上海市财政局 › 预算绩效中心");
  await expect(page.getByTestId("gbc-reports-org-other-unit")).toHaveCount(0);
  await page.getByTestId("gbc-reports-management-district").click();
  await expect(page.getByTestId("gbc-reports-district-picker")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-selected")).toHaveCount(0);
  await page.getByTestId("gbc-reports-district-district").click();
  await page.getByTestId("gbc-reports-org-search").fill("预算绩效中心");
  await expect(page.getByTestId("gbc-reports-org-other-unit")).toContainText("上海市 › 浦东新区 › 浦东新区财政局 › 预算绩效中心");
  await page.getByTestId("gbc-reports-org-other-unit").click();
  await expect(page.getByTestId("gbc-reports-selected-path")).toContainText("浦东新区");
  await expect(page.getByTestId("material-report-other-unit26")).toBeVisible();
  await expect(page.getByTestId("material-report-unit24")).toHaveCount(0);
  await page.getByTestId("gbc-reports-org-search").fill("预算绩效中心");
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(1);
  await page.getByTestId("gbc-reports-back-directory").click();
  await expect(page).toHaveURL(/\/reports\?scope=district&region=district$/);
  await expect(page.getByTestId("gbc-reports-management-summary")).toContainText("浦东新区");
  await page.getByTestId("gbc-reports-management-city").click();
  await page.getByTestId("gbc-reports-org-search").fill("预算绩效中心");
  await page.getByTestId("gbc-reports-org-unit").click();
  await expect(page.getByTestId("material-report-unit24").getByRole("link", { name: "进入复核" })).toHaveAttribute("href", "/review?job=unit24&returnTo=%2Freports%2Funit%3Fyear%3D2024");
  await expect(page.getByTestId("material-report-unit24").getByRole("link", { name: "下载原件" })).toHaveAttribute("href", `/api/files/unit24/source?checksum=${"b".repeat(64)}`);
  await page.getByTestId("gbc-reports-back-directory").click();
  await expect(page.getByTestId("gbc-reports-unassigned-alert")).toContainText("1 份材料尚未确认所属部门");
  await page.getByTestId("gbc-reports-org-unassigned").click();
  await expect(page).toHaveURL(/\/reports\/unassigned$/);
  await expect(page.getByTestId("gbc-reports-year-unknown")).toBeVisible();
  await expect(page.getByTestId("material-report-legacy")).toBeVisible();
  await page.getByTestId("gbc-reports-kind-unknown").click();
  await expect(page.getByTestId("material-report-legacy")).toBeVisible();
});

test("卡片列表反复切换、每页12/24/48项与窄屏保持可用", async ({ page }, testInfo) => {
  await fixture(page);
  const grid = page.getByTestId("gbc-reports-directory-grid");
  await expect(grid.locator("button")).toHaveCount(24);
  for (const view of ["list", "grid", "list", "grid"]) {
    await page.getByTestId(`gbc-reports-view-${view}`).click();
    await expect(grid).toHaveAttribute("data-view", view);
    await expect(page.getByTestId(`gbc-reports-view-${view}`)).toHaveAttribute("aria-pressed", "true");
    await expect(grid.locator("button")).toHaveCount(24);
  }
  await page.getByTestId("gbc-reports-directory-page-size").selectOption("12");
  await expect(grid.locator("button")).toHaveCount(12);
  await page.getByRole("button", { name: "目录下一页", exact: true }).click();
  await expect(page.getByTestId("gbc-reports-directory-pagination")).toContainText("第 2 / 3 页");
  await page.getByTestId("gbc-reports-directory-page-size").selectOption("48");
  await expect(grid.locator("button")).toHaveCount(29);
  await expect(page.getByTestId("gbc-reports-directory-pagination")).toContainText("第 1 / 1 页");
  await page.getByTestId("gbc-reports-directory-page-size").selectOption("24");
  await page.setViewportSize({ width: 1600, height: 1200 });
  await page.getByTestId("gbc-department-reports-page").getByRole("heading").first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("management-compact-cards.png"), fullPage: true });
  await page.getByTestId("gbc-reports-view-list").click();
  await page.screenshot({ path: testInfo.outputPath("management-two-column-list.png"), fullPage: true });
  await page.reload();
  await expect(grid).toHaveAttribute("data-view", "list", { timeout: 10_000 });
  // 列表视图按列显示最新预算、决算、待复核与材料数；没有材料的部门写“暂无”。
  await expect(page.getByTestId("gbc-reports-org-finance-final")).toHaveText("决算 2025 · 已复核 · 3 项问题");
  await expect(page.getByTestId("gbc-reports-org-empty-1-budget")).toHaveText("预算 · 暂无");
  await page.getByTestId("gbc-reports-directory-sort").selectOption("name");
  await expect(grid.locator("button").first()).toHaveAttribute("data-testid", "gbc-reports-org-empty-1");
  await page.reload();
  await expect(page.getByTestId("gbc-reports-directory-sort")).toHaveValue("name");
  await expect(grid.locator("button").first()).toHaveAttribute("data-testid", "gbc-reports-org-empty-1");
  await page.getByTestId("gbc-reports-directory-sort").selectOption("attention");
  await expect(grid.locator("button").first()).toHaveAttribute("data-testid", "gbc-reports-org-finance");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("gbc-workspace-sidebar")).toHaveAttribute("data-collapsed", "true");
  await expect(page.getByRole("button", { name: "展开导航", exact: true })).toBeVisible();
  await expect(page.getByText("每份材料一行", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "上传新报告", exact: true })).toBeVisible();
  expect((await page.getByTestId("gbc-reports-org-search").boundingBox())?.width).toBeGreaterThan(200);
  await expect(page.getByTestId("gbc-reports-view-grid")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByTestId("gbc-reports-management-district").click();
  await expect(page.getByTestId("gbc-reports-district-picker")).toBeVisible();
  await page.getByTestId("gbc-reports-district-district").click();
  await expect(page.getByTestId("gbc-reports-management-summary")).toContainText("浦东新区");
  await expect(page.getByTestId("gbc-reports-org-other-finance")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-org-finance")).toHaveCount(0);
  await page.getByTestId("gbc-reports-back-to-districts").click();
  await expect(page.getByTestId("gbc-reports-district-picker")).toBeVisible();
});

test("管理员看到16区入口，未配置的区不可进入且不伪造材料", async ({ page }) => {
  const state = await fixture(page);
  state.isAdmin = true;
  await page.reload();
  await page.getByTestId("gbc-reports-management-district").click();
  const picker = page.getByTestId("gbc-reports-district-picker");
  await expect(picker.locator("button")).toHaveCount(16);
  await expect(picker.locator("button:enabled")).toHaveCount(1);
  await expect(picker.locator("button:disabled")).toHaveCount(15);
  await expect(page.getByTestId("gbc-reports-selected")).toHaveCount(0);
  await page.getByTestId("gbc-reports-district-district").click();
  await expect(page.getByTestId("gbc-reports-selected")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-org-other-finance")).toContainText("1 份材料");
  await expect(page.getByTestId("gbc-reports-org-all")).toContainText("查看本范围全部材料（1）");
  await page.getByTestId("gbc-reports-org-all").click();
  await expect(page).toHaveURL(/\/reports\/district$/);
  await expect(page.getByTestId("gbc-reports-selected")).toContainText("浦东新区 · 全部材料");
  await expect(page.getByTestId("gbc-reports-selected")).toContainText("1 条材料记录");
  await expect(page.getByTestId("material-report-budget26")).toHaveCount(0);
  await expect(page.getByTestId("material-report-other-unit26")).toBeVisible();
});

test("收藏按用户保存，检查明细可展开且质量状态不误报", async ({ page }) => {
  const state = await fixture(page);
  await page.getByTestId("gbc-reports-org-search").fill("上海市财政局");
  await page.getByTestId("gbc-reports-org-finance").click();
  const row = page.getByTestId("material-report-budget26");
  await expect(row).toContainText("待复核");
  await expect(row).not.toContainText("已复核");
  // 版本与复核记录移到材料详情，部门报告只给入口。
  await expect(page.getByTestId("material-history-budget26")).toHaveCount(0);
  await expect(row.getByRole("link", { name: "版本与复核记录" })).toHaveAttribute("href", "/materials/slots/budget26?tab=versions&returnTo=%2Freports%2Ffinance%3Fyear%3D2026");
  await expect(row.getByRole("link", { name: "进入复核" })).toHaveAttribute("href", "/review?job=budget26&returnTo=%2Freports%2Ffinance%3Fyear%3D2026");
  await page.getByTestId("gbc-reports-favorite").click();
  await expect(page.getByTestId("gbc-reports-favorite")).toHaveAttribute("aria-pressed", "true");
  await page.reload();
  await expect(page.getByTestId("gbc-reports-favorite")).toHaveAttribute("aria-pressed", "true");
  await page.goto("/reports");
  await page.getByTestId("gbc-reports-directory-favorites").click();
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(1);
  await expect(page.getByTestId("gbc-reports-org-finance")).toBeVisible();
  state.username = "another-reviewer";
  await page.reload();
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button").first()).toBeVisible();
  await page.getByTestId("gbc-reports-directory-favorites").click();
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(0);
});

test("年度切换写入地址，汇总列表分页和年度筛选，刷新失败保留当前数据与目录", async ({ page }) => {
  const state = await fixture(page);
  await page.getByTestId("gbc-reports-org-search").fill("上海市财政局");
  await page.getByTestId("gbc-reports-org-finance").click();
  await expect(page.getByTestId("gbc-reports-pagination")).toHaveCount(0);
  await page.getByTestId("gbc-reports-year-tab-2025").click();
  await expect(page).toHaveURL(/\/reports\/finance\?year=2025$/);
  await expect(page.getByTestId("material-report-final25")).toContainText("已复核 · 确认问题 3 项");
  await expect(page.getByTestId("material-report-budget26")).toHaveCount(0);
  await expect(page.getByTestId("material-report-empty-budget")).toContainText("2025 年预算");
  await page.reload();
  await expect(page.getByTestId("gbc-reports-year-tab-2025")).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("material-report-final25")).toBeVisible();
  await page.goto("/reports/finance?year=1999");
  await expect(page.getByTestId("gbc-reports-year-tab-2026")).toHaveAttribute("aria-selected", "true");
  await page.getByTestId("gbc-reports-tab-units").click();
  await page.getByTestId("gbc-reports-year").selectOption("2024");
  await expect(page.getByTestId("material-report-unit24")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-pagination")).toHaveCount(0);
  state.failJobs = true;
  await page.getByTestId("gbc-reports-refresh").click();
  await expect(page.getByTestId("gbc-department-reports-page").getByRole("alert")).toContainText("HTTP 503");
  await expect(page.getByTestId("material-report-unit24")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-selected-path")).toHaveText("上海市 › 上海市财政局");
  state.failJobs = false;
  await page.getByTestId("gbc-reports-refresh").click();
  await expect(page.getByTestId("gbc-department-reports-page").getByRole("alert")).toHaveCount(0);
  await expect(page.getByTestId("material-report-unit24")).toBeVisible();
  // 多主体的地区汇总仍是分页列表。
  await page.goto("/reports/city");
  await expect(page.locator('tbody > tr[data-testid^="material-report-"]')).toHaveCount(10);
  await page.getByRole("button", { name: "报告下一页", exact: true }).click();
  await expect(page.getByTestId("gbc-reports-pagination")).toContainText("第 2 / 2 页");
});

test("没有本级单位材料时不把部门汇总重复为本级卡片，单位搜索不跨部门", async ({ page }) => {
  await fixture(page);
  await page.getByTestId("gbc-reports-org-search").fill("测试部门08");
  await page.getByTestId("gbc-reports-org-empty-8").click();
  await page.getByRole("tab", { name: "下属单位", exact: true }).click();
  await expect(page.getByTestId("gbc-reports-org-empty-8")).toHaveCount(0);
  await page.getByTestId("gbc-reports-org-search").fill("预算绩效中心");
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(0);
  await expect(page.getByTestId("gbc-reports-org-unit")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  // 窄屏侧栏由媒体查询收起，量宽度前先等收起完成。
  await expect(page.getByTestId("gbc-workspace-sidebar")).toHaveAttribute("data-collapsed", "true");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect((await page.getByRole("searchbox", { name: "搜索报告", exact: true }).boundingBox())?.width).toBeGreaterThan(200);
});

test("单位直达链接保留部门口径切换和返回目录", async ({ page }) => {
  await fixture(page);
  await page.goto("/reports?organization=unit");
  await expect(page).toHaveURL(/\/reports\/unit$/);
  await expect(page.getByRole("tab", { name: "下属单位", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("material-report-unit24")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-management-city")).toHaveCount(0);
  await page.getByRole("tab", { name: "部门", exact: true }).click();
  await expect(page.getByTestId("gbc-reports-selected-path")).toHaveText("上海市 › 上海市财政局");
  await expect(page.getByTestId("gbc-reports-directory-grid")).toHaveCount(0);
  await expect(page.getByTestId("material-report-budget26")).toBeVisible();
  await page.getByTestId("gbc-reports-back-directory").click();
  await expect(page).toHaveURL(/\/reports$/);
  await expect(page.getByTestId("gbc-reports-directory-grid").locator("button")).toHaveCount(24);
  await page.goto("/reports/finance?tab=units");
  await expect(page.getByRole("tab", { name: "下属单位", exact: true })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByTestId("material-report-unit24")).toBeVisible();
  await page.goto("/reports/not-authorized");
  await expect(page.getByTestId("gbc-reports-missing")).toContainText("未找到这个部门或单位");
  await expect(page.locator('[data-testid^="material-report-"]')).toHaveCount(0);
});
