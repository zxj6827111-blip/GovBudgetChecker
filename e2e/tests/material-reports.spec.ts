import { expect, test } from "../../app/node_modules/playwright/test";
import { DETAIL_BODY, VERSIONS_BODY } from "./materialLedgerDetailFixtures";

const completed = { status: "completed", confirmed_count: 6, reviewer: "admin", reviewed_at: "2026-10-23T11:08:18Z" };
const pending = { status: "pending", confirmed_count: null };
const run = { job_id: "current23", status: "review_required", analysis_revision: 1 };
const version = { document_version_id: 132, version_number: 1, is_current: true, filename: "上海市医疗保障局.pdf", file_hash: "a".repeat(64), source_job_id: "current23", current_run: run, review: completed };
const material = { item_id: "slot:slot23", slot_id: "slot23", organization_id: "insurance", organization_name: "上海市医疗保障局", fiscal_year: 2023, report_year: 2023, report_kind: "final", material_scope: "department_summary", current_version: version, version_count: 1, analysis_task_count: 5, review: completed };

test("材料主行、复核结果、历史展开、新版不继承结论及失败保留", async ({ page }, testInfo) => {
  const failures: string[] = [];
  page.on("pageerror", error => failures.push(error.message));
  const state = { fail: false, newer: false };
  await page.context().addCookies([{ name: "gbc_session", value: "e2e-session", url: testInfo.project.use.baseURL as string }]);
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    let body: unknown = {};
    if (url.pathname === "/api/auth/me") body = { user: { username: "reviewer", is_admin: true } };
    if (url.pathname === "/api/organizations") body = { tree: [{ id: "city", name: "上海市", level: "city", children: [{ id: "insurance", name: "上海市医疗保障局", level: "department" }] }] };
    const current = state.newer ? { ...material, review: { status: "waiting_analysis", confirmed_count: null }, previous_review: { completed_by: "admin" }, version_count: 2, current_version: { ...version, version_number: 2, document_version_id: 133, current_run: null, review: { status: "waiting_analysis", confirmed_count: null } } } : material;
    if (url.pathname === "/api/materials/reports") {
      if (state.fail) return route.fulfill({ status: 503, json: { detail: "unavailable" } });
      const filtered = url.searchParams.get("review") === "pending" ? [] : [current];
      body = { ok: true, data: { items: filtered, total: filtered.length, page: 1, page_size: 10, page_count: 1, years: [2023], has_unknown_year: false, organization_report_counts: { insurance: 1 }, material_total: 1, legacy_total: 0, unassigned_total: 0, activity: { running: 0, waiting: 0 } } };
    }
    if (url.pathname === "/api/materials/reports/slot23/history") body = { ok: true, data: { versions: [{ ...version, runs: [run, { ...run, job_id: "early23" }], reviews: [{ status: "completed", completed_at: completed.reviewed_at, completed_by: "admin", analysis_job_uuid: "current23", analysis_basis_token: "current23:1", confirmed_count: 6 }] }] } };
    if (url.pathname.includes("/analysis-versions")) body = { items: [{ revision_id: "rev-current", is_current: true }] };
    if (url.pathname === "/api/materials/slots/slot23") body = DETAIL_BODY;
    if (url.pathname === "/api/materials/slots/slot23/versions") body = VERSIONS_BODY;
    return route.fulfill({ json: body });
  });
  await page.goto("/reports");
  await expect(page.getByTestId("gbc-reports-org-insurance")).toContainText("1 份材料");
  await expect(page.getByTestId("material-report-slot23")).toHaveCount(0);
  await page.getByTestId("gbc-reports-org-insurance").click();
  await expect(page).toHaveURL(/\/reports\/insurance$/);
  const row = page.getByTestId("material-report-slot23");
  await expect(row).toContainText("已复核 · 确认问题 6 项");
  await expect(row).toContainText("文件版本 1");
  await expect(row).not.toContainText("文件版本 132");
  await expect(row.getByRole("link", { name: "下载原件", exact: true })).toHaveAttribute("href", `/api/files/current23/source?checksum=${"a".repeat(64)}`);
  await page.screenshot({ path: testInfo.outputPath("material-reports-year-cards.png"), fullPage: true });
  // 版本、分析代际与历史复核结论在材料详情“版本与来源”中查看，返回回到同一年度。
  await row.getByRole("link", { name: "版本与复核记录" }).click();
  await expect(page).toHaveURL(/\/materials\/slots\/slot23\?tab=versions&returnTo=%2Freports%2Finsurance%3Fyear%3D2023$/);
  const history = page.getByTestId("material-history-slot23");
  await expect(history).toContainText("文件版本 1");
  await expect(history).toContainText("确认问题 6 项");
  await expect(history.getByRole("link", { name: "下载此版本原件" })).toHaveAttribute("href", `/api/files/current23/source?checksum=${"a".repeat(64)}`);
  await expect(history.getByRole("link", { name: "查看材料与复核记录" }).first()).toHaveAttribute("href", /^\/review\?job=current23&returnTo=%2Fmaterials%2Fslots%2Fslot23%3Ftab%3Dversions%26returnTo%3D%252Freports%252Finsurance/);
  await history.getByRole("button", { name: "查看分析代际" }).first().click();
  await expect(history).toContainText("当前分析");
  await page.screenshot({ path: testInfo.outputPath("material-reports-history.png"), fullPage: true });
  await page.getByRole("link", { name: "← 返回部门报告" }).click();
  await expect(page).toHaveURL(/\/reports\/insurance\?year=2023$/);
  state.newer = true;
  await page.getByTestId("gbc-reports-refresh").click();
  await expect(row).toContainText("文件版本 2");
  await expect(row).toContainText("等待分析后复核");
  await expect(row).not.toContainText("已复核 · 确认问题 6 项");
  await page.screenshot({ path: testInfo.outputPath("material-reports-new-version.png"), fullPage: true });
  state.fail = true;
  await page.getByTestId("gbc-reports-refresh").click();
  await expect(page.getByTestId("gbc-department-reports-page").getByRole("alert")).toContainText("HTTP 503");
  await expect(row).toContainText("文件版本 2");
  expect(failures).toEqual([]);
});

test("窄屏保留筛选和材料操作且页面无整体横向溢出", async ({ page }, testInfo) => {
  await page.context().addCookies([{ name: "gbc_session", value: "e2e-session", url: testInfo.project.use.baseURL as string }]);
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    return route.fulfill({ json: path === "/api/auth/me" ? { user: { username: "mobile" } } : path === "/api/organizations" ? { tree: [{ id: "city", name: "上海市", level: "city", children: [{ id: "insurance", name: "上海市医疗保障局", level: "department" }] }] } : path === "/api/materials/reports" ? { ok: true, data: { items: [material], total: 1, page: 1, page_count: 1, years: [2023], has_unknown_year: false, organization_report_counts: {}, material_total: 1, legacy_total: 0, unassigned_total: 0, activity: { running: 0, waiting: 0 } } } : {} });
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/reports/insurance");
  await expect(page.getByTestId("gbc-reports-year-tab-2023")).toBeVisible();
  await expect(page.getByTestId("material-report-slot23")).toContainText("确认问题 6 项");
  await expect(page.getByTestId("material-report-slot23").getByRole("link", { name: "查看复核结果" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
