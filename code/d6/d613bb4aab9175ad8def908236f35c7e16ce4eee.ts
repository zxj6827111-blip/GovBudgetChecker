import { expect, test, type Page } from "../../app/node_modules/playwright/test";

async function mockSession(page: Page) {
  await page.context().addCookies([{ name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax" }]);
}

const evidence = { page: 2, quote: "原文金额 123.45", text: "原文金额 123.45", bbox: [72, 60, 300, 90], location_level: "exact_span", verification_status: "verified" };
const ai = { id: "ai-common", source: "ai", rule_id: "AI-001", severity: "high", title: "大模型共同发现", page_number: 2, claimed_page_number: 1, bbox: evidence.bbox, location_level: "exact_span", verification_status: "verified", evidence_status: "complete", evidence_verification: { status: "verified", source_identity_complete: true }, evidence: [evidence], location: { page: 2 } };
const rule = { ...ai, id: "rule-common", source: "rule", title: "规则共同发现", rule_id: "C-003", claimed_page_number: undefined };

test("来源按钮显示完整 AI/规则结果，共同发现仍可单独查看，定位和分类同步", async ({ page }) => {
  await mockSession(page);
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path === "/api/auth/me") body = { user: { username: "reviewer", is_admin: false } };
    if (path === "/api/health") body = { status: "ok" };
    if (path === "/api/jobs/source-fixture") body = {
      job_id: "source-fixture", filename: "来源测试.pdf", status: "review_required",
      result: { ai_findings: [ai], rule_findings: [rule], merged: { merged_ids: ["rule-common"], relations: [{ status: "agreement", ai_issue_ids: ["ai-common"], rule_issue_ids: ["rule-common"] }] }, meta: { pages: 3, ai_execution: { state: "succeeded" } } },
    };
    if (path.startsWith("/api/files/")) return route.fulfill({ status: 200, contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="840"><rect width="600" height="840" fill="white"/></svg>' });
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/review?job=source-fixture");
  await page.getByTestId("gbc-review-toggle-filters").click();
  await page.getByTestId("gbc-review-source-ai").click();
  await expect(page.getByTestId("gbc-review-issue-card-ai-common")).toBeVisible();
  await expect(page.getByTestId("gbc-review-issue-card-rule-common")).toHaveCount(0);
  await expect(page.getByTestId("gbc-review-category-issue")).toContainText("1");
  await page.getByTestId("gbc-review-issue-card-ai-common").click();
  await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("2");
  await expect(page.getByTestId("gbc-review-evidence-highlight")).toBeVisible();
  await expect(page.getByTestId("gbc-review-ai-location-unavailable")).toHaveCount(0);
  await page.getByTestId("gbc-review-source-rule").click();
  await expect(page.getByTestId("gbc-review-issue-card-rule-common")).toBeVisible();
  await page.getByTestId("gbc-review-source-common").click();
  await expect(page.getByTestId("gbc-review-issue-card-rule-common")).toContainText("本地规则");
  await expect(page.getByTestId("gbc-review-issue-card-rule-common")).toContainText("大模型");
});

test("部门报告同时保留 2026 预算、2025 和 2024 决算及未归属历史材料", async ({ page }) => {
  await mockSession(page);
  const jobs = [
    { job_id: "budget26", organization_id: "dept", organization_name: "部门甲", filename: "2026预算.pdf", report_year: 2026, report_kind: "budget", status: "completed" },
    { job_id: "final25", organization_id: "dept", organization_name: "部门甲", filename: "2025决算.pdf", report_year: 2025, report_kind: "final", status: "completed" },
    { job_id: "final24", organization_id: "unit", organization_name: "单位甲", filename: "2024决算.pdf", report_year: 2024, report_kind: "final", status: "completed" },
    { job_id: "legacy", filename: "历史文件.pdf", status: "completed" },
  ];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = path === "/api/auth/me" ? { user: { username: "reviewer", is_admin: false } }
      : path === "/api/health" ? { status: "ok" }
        : path === "/api/jobs" ? jobs
          : path === "/api/organizations" ? { tree: [{ id: "dept", name: "部门甲", level: "department", children: [{ id: "unit", name: "单位甲", level: "unit" }] }] } : {};
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/reports");
  await expect(page.getByTestId("gbc-workspace-nav-reports")).toBeVisible();
  await page.getByTestId("gbc-reports-org-dept").click();
  await expect(page.getByTestId("gbc-reports-year-2026")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-year-2025")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-year-2024")).toBeVisible();
  await page.getByTestId("gbc-reports-kind-budget").click();
  await expect(page.getByTestId("gbc-workbench-queue-row-budget26")).toBeVisible();
  await expect(page.getByTestId("gbc-workbench-queue-row-final25")).toHaveCount(0);
  await page.getByTestId("gbc-reports-kind-final").click();
  await page.getByTestId("gbc-reports-year").selectOption("2024");
  await expect(page.getByTestId("gbc-workbench-queue-row-final24")).toBeVisible();
  await expect(page.getByTestId("gbc-workbench-queue-row-final25")).toHaveCount(0);
  await expect(page.getByTestId("gbc-workbench-queue-review-final24")).toHaveAttribute("href", "/review?job=final24&returnTo=%2Freports");
  await page.getByTestId("gbc-reports-kind-all").click();
  await page.getByTestId("gbc-reports-org-unassigned").click();
  await expect(page.getByTestId("gbc-workbench-queue-row-legacy")).toBeVisible();
  await expect(page.getByTestId("gbc-reports-year-unknown")).toBeVisible();
});
