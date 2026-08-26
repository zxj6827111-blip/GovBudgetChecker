import { expect, test } from "../../app/node_modules/playwright/test";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const uploadedJob = {
  job_id: "job-putuo-office-2024",
  filename: "上海市普陀区人民政府办公室 2024 年度部门决算.pdf",
  status: "analyzing",
  stage: "规则审校中",
  report_year: 2024,
  doc_type: "dept_final",
  report_kind: "final",
  organization_id: "dept-putuo-office",
  organization_name: "上海市普陀区人民政府办公室",
  organization_level: "department",
  created_ts: "2026-07-13T10:00:00Z",
  updated_ts: "2026-07-13T10:00:00Z",
};

test.describe("GBC UI demo upload", () => {
  test("single upload opens the uploaded document detail after analysis starts", async ({ page }) => {
    test.setTimeout(60_000);
    let analysisStarted = false;

    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const path = new URL(req.url()).pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user: { username: "e2e-admin", is_admin: true } }) });
        return;
      }
      if (path === "/api/organizations") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tree: [{ id: "dept-putuo-office", name: "上海市普陀区人民政府办公室", level: "department", parent_id: null, children: [] }] }) });
        return;
      }
      if (path === "/api/organizations/list") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ organizations: [{ id: "dept-putuo-office", name: "上海市普陀区人民政府办公室", level: "department", parent_id: null }], total: 1 }) });
        return;
      }
      if (path === "/api/jobs") {
        const items = analysisStarted ? [uploadedJob] : [];
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items, total: items.length, limit: 500, offset: 0 }) });
        return;
      }
      if (path === "/api/gbc-ui-demo/workflow") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ issues: {}, packages: [] }) });
        return;
      }
      if (path === "/api/documents/preflight" && method === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            filename: uploadedJob.filename,
            report_year: 2024,
            doc_type: "dept_final",
            report_kind: "final",
            current: {
              organization_id: "dept-putuo-office",
              organization_name: "上海市普陀区人民政府办公室",
              level: "department",
              confidence: 0.98,
              match_basis: "cover",
            },
          }),
        });
        return;
      }
      if (path === "/api/documents/upload" && method === "POST") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ job_id: uploadedJob.job_id }) });
        return;
      }
      if (path === `/api/documents/${uploadedJob.job_id}/run` && method === "POST") {
        analysisStarted = true;
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ job_id: uploadedJob.job_id, status: "started" }) });
        return;
      }
      if (path === `/api/jobs/${uploadedJob.job_id}`) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ...uploadedJob,
            result: {
              rule_findings: [{
                id: "FIN-001",
                rule_id: "FIN-001",
                severity: "medium",
                title: "决算说明需复核",
                message: "决算说明需复核",
                evidence: [{ page: 1, text: "测试证据" }],
                location: { page: 1 },
              }],
            },
          }),
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto("/viewer/gbc-ui-demo");
    await page.getByTestId("gbc-nav-upload").click();
    await page.getByTestId("gbc-upload-open-batch").click();
    await page.getByTestId("batch-upload-file-input").setInputFiles({
      name: uploadedJob.filename,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
    });

    await expect(page.getByTestId("batch-start")).toBeEnabled({ timeout: 10_000 });
    await page.getByTestId("batch-start").click();

    await expect(page.getByTestId("batch-upload-modal")).toBeHidden({ timeout: 20_000 });
    await expect(page.getByTestId("gbc-detail-reanalyze")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("heading", { name: /2024.*部门决算/ })).toBeVisible();
    await expect(page.getByText("FIN-001", { exact: true })).toBeVisible();
  });

  test("multiple production-page uploads open task center and retain every started task", async ({ page }) => {
    test.setTimeout(60_000);
    const jobs: Array<typeof uploadedJob> = [];
    let uploadCount = 0;

    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const path = new URL(req.url()).pathname;
      if (path === "/api/auth/me") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ user: { username: "e2e-admin", is_admin: true } }) });
        return;
      }
      if (path === "/api/organizations") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tree: [{ id: "dept-putuo-office", name: "Putuo Office", level: "department", parent_id: null, children: [] }] }) });
        return;
      }
      if (path === "/api/organizations/list") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ organizations: [{ id: "dept-putuo-office", name: "Putuo Office", level: "department", parent_id: null }], total: 1 }) });
        return;
      }
      if (path === "/api/jobs") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: jobs, total: jobs.length, limit: 500, offset: 0 }) });
        return;
      }
      if (path === "/api/gbc-ui-demo/workflow") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ issues: {}, packages: [] }) });
        return;
      }
      if (path === "/api/documents/preflight" && method === "POST") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ report_year: 2024, doc_type: "dept_final", current: { organization_id: "dept-putuo-office", organization_name: "Putuo Office", level: "department", confidence: 0.99, match_basis: "cover" } }) });
        return;
      }
      if (path === "/api/documents/upload" && method === "POST") {
        uploadCount += 1;
        const job = { ...uploadedJob, job_id: `job-batch-${uploadCount}`, filename: `final-2024-${uploadCount}.pdf`, status: "analyzing", stage: "rules", organization_name: "Putuo Office" };
        jobs.unshift(job);
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ job_id: job.job_id }) });
        return;
      }
      if (/^\/api\/documents\/job-batch-\d+\/run$/.test(path) && method === "POST") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "started" }) });
        return;
      }
      const detailMatch = path.match(/^\/api\/jobs\/(job-batch-\d+)$/);
      if (detailMatch) {
        const job = jobs.find((item) => item.job_id === detailMatch[1]);
        await route.fulfill({ status: job ? 200 : 404, contentType: "application/json", body: JSON.stringify(job ? { ...job, result: { rule_findings: [] } } : {}) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto("/viewer/gbc-ui-demo");
    await page.getByTestId("gbc-nav-upload").click();
    await page.getByTestId("gbc-upload-open-batch").click();
    await page.getByTestId("batch-upload-file-input").setInputFiles([
      { name: "final-2024-1.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n%%EOF\n") },
      { name: "final-2024-2.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n%%EOF\n") },
    ]);
    await expect(page.getByTestId("batch-start")).toBeEnabled({ timeout: 15_000 });
    await page.getByTestId("batch-start").click();

    await expect(page.getByTestId("batch-upload-modal")).toBeHidden({ timeout: 25_000 });
    await expect(page.getByTestId("gbc-task-detail-job-batch-1")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("gbc-task-detail-job-batch-2")).toBeVisible();
  });
});
