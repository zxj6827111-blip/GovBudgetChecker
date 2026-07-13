import { expect, test, type Page } from "../../app/node_modules/playwright/test";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

type JobRecord = {
  job_id: string;
  filename: string;
  status: string;
  progress: number;
  report_year: number;
  report_kind: "budget" | "final";
  doc_type: string;
  issue_total: number;
  issue_error: number;
  issue_warn: number;
  organization_id: string;
  organization_name: string;
  organization_level: string;
  created_ts: number;
  updated_ts: number;
  stage: string;
};

type WorkflowRecord = {
  key: string;
  job_id: string;
  issue_id: string;
  status: "pending" | "confirmed" | "no_issue" | "needs_review" | "in_package";
  title?: string;
  severity?: string;
  page?: number;
  organization_id?: string | null;
  organization_name?: string | null;
  note?: string;
  updated_at: string;
};

type MockState = {
  jobs: JobRecord[];
  workflow: {
    issues: Record<string, WorkflowRecord>;
    packages: Array<{
      id: string;
      name: string;
      organization_id?: string | null;
      organization_name?: string | null;
      job_ids: string[];
      issue_keys: string[];
      status: "draft" | "ready" | "submitted";
      created_at: string;
      updated_at: string;
    }>;
  };
  uploadRequests: Array<{ fiscalYear: string; docType: string }>;
  analyzeRequests: Array<{ fiscal_year?: string; doc_type?: string }>;
  workflowPosts: Array<Record<string, unknown>>;
  packageDownloads: number;
  reportDownloads: string[];
  reanalyzeRequests: string[];
  deleteRequests: string[];
  logoutRequests: number;
  orgJobRequests: string[];
};

const pdfFile = (name: string) => ({
  name,
  mimeType: "application/pdf",
  buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
});

function makeJob(overrides: Partial<JobRecord> & { job_id: string; filename: string; report_year: number; report_kind: "budget" | "final"; doc_type: string }): JobRecord {
  const baseTs = 1_779_000_000;
  return {
    status: "completed",
    progress: 100,
    issue_total: 2,
    issue_error: 1,
    issue_warn: 1,
    organization_id: "dept-001",
    organization_name: "Finance Bureau",
    organization_level: "department",
    created_ts: baseTs,
    updated_ts: baseTs,
    stage: "done",
    ...overrides,
  };
}

function makeIssue(issueId: string, page: number, severity: "high" | "medium" = "high") {
  return {
    id: issueId,
    rule_id: `R-${issueId}`,
    title: `Issue ${issueId}`,
    message: `Issue ${issueId} message`,
    severity,
    page,
    suggestion: `Fix ${issueId}`,
    evidence: [{ page, text: `Evidence for ${issueId}` }],
  };
}

function buildDetail(job: JobRecord) {
  const issues = job.job_id === "job-2026" ? [makeIssue("issue-1", 3, "high"), makeIssue("issue-2", 5, "medium")] : [];
  return {
    ...job,
    result: {
      issues,
    },
  };
}

function buildOrganizations() {
  return {
    tree: [
      {
        id: "dept-001",
        name: "Finance Bureau",
        level: "department",
        level_name: "department",
        parent_id: null,
        issue_count: 2,
        job_count: 2,
        children: [
          {
            id: "unit-001",
            name: "Finance Unit",
            level: "unit",
            level_name: "unit",
            parent_id: "dept-001",
            issue_count: 0,
            job_count: 0,
            children: [],
          },
        ],
      },
    ],
  };
}

function buildOrgList() {
  return {
    organizations: [
      { id: "dept-001", name: "Finance Bureau", level: "department", level_name: "department", parent_id: null },
      { id: "unit-001", name: "Finance Unit", level: "unit", level_name: "unit", parent_id: "dept-001" },
    ],
    total: 2,
  };
}

function initialState(): MockState {
  return {
    jobs: [
      makeJob({
        job_id: "job-2026",
        filename: "budget-2026.pdf",
        report_year: 2026,
        report_kind: "budget",
        doc_type: "dept_budget",
        updated_ts: 1_779_000_010,
      }),
      makeJob({
        job_id: "job-2025",
        filename: "final-2025.pdf",
        report_year: 2025,
        report_kind: "final",
        doc_type: "dept_final",
        updated_ts: 1_779_000_000,
      }),
    ],
    workflow: { issues: {}, packages: [] },
    uploadRequests: [],
    analyzeRequests: [],
    workflowPosts: [],
    packageDownloads: 0,
    reportDownloads: [],
    reanalyzeRequests: [],
    deleteRequests: [],
    logoutRequests: 0,
    orgJobRequests: [],
  };
}

async function installGbcMocks(page: Page, state: MockState) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const method = req.method().toUpperCase();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-admin", display_name: "E2E Admin", is_admin: true } }),
      });
      return;
    }

    if (path === "/api/auth/logout" && method === "POST") {
      state.logoutRequests += 1;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
      return;
    }

    if (path === "/api/organizations") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(buildOrganizations()) });
      return;
    }

    if (path === "/api/organizations/list") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(buildOrgList()) });
      return;
    }

    if (path === "/api/jobs" && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: state.jobs, total: state.jobs.length, limit: 500, offset: 0 }),
      });
      return;
    }

    const orgJobsMatch = path.match(/^\/api\/organizations\/([^/]+)\/jobs$/);
    if (orgJobsMatch && method === "GET") {
      state.orgJobRequests.push(decodeURIComponent(orgJobsMatch[1]));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ jobs: state.jobs, total: state.jobs.length }),
      });
      return;
    }

    const detailMatch = path.match(/^\/api\/jobs\/([^/]+)$/);
    if (detailMatch && method === "GET") {
      const jobId = decodeURIComponent(detailMatch[1]);
      const job = state.jobs.find((item) => item.job_id === jobId);
      await route.fulfill({
        status: job ? 200 : 404,
        contentType: "application/json",
        body: JSON.stringify(job ? buildDetail(job) : { detail: "job not found" }),
      });
      return;
    }

    const structuredMatch = path.match(/^\/api\/jobs\/([^/]+)\/structured-ingest$/);
    if (structuredMatch && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "completed", tables_count: 2, facts_count: 8 }),
      });
      return;
    }

    const reanalyzeMatch = path.match(/^\/api\/jobs\/([^/]+)\/reanalyze$/);
    if (reanalyzeMatch && method === "POST") {
      state.reanalyzeRequests.push(decodeURIComponent(reanalyzeMatch[1]));
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "started" }) });
      return;
    }

    const deleteMatch = path.match(/^\/api\/jobs\/([^/]+)$/);
    if (deleteMatch && method === "DELETE") {
      const jobId = decodeURIComponent(deleteMatch[1]);
      state.deleteRequests.push(jobId);
      state.jobs = state.jobs.filter((item) => item.job_id !== jobId);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true }) });
      return;
    }

    if (path === "/api/gbc-ui-demo/workflow" && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.workflow) });
      return;
    }

    if (path === "/api/gbc-ui-demo/workflow" && method === "POST") {
      const payload = req.postDataJSON() as Record<string, unknown>;
      state.workflowPosts.push(payload);

      if (payload.action === "update_issue") {
        const jobId = String(payload.job_id ?? "");
        const issueId = String(payload.issue_id ?? "");
        const key = `${jobId}::${issueId}`;
        state.workflow.issues[key] = {
          key,
          job_id: jobId,
          issue_id: issueId,
          status: String(payload.status ?? "pending") as WorkflowRecord["status"],
          title: String(payload.title ?? ""),
          severity: String(payload.severity ?? ""),
          page: Number(payload.page ?? 1),
          organization_id: payload.organization_id ? String(payload.organization_id) : null,
          organization_name: payload.organization_name ? String(payload.organization_name) : null,
          note: payload.note ? String(payload.note) : "",
          updated_at: new Date().toISOString(),
        };
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(state.workflow) });
        return;
      }

      if (payload.action === "create_package") {
        const issueKeys = Array.isArray(payload.issue_keys) ? payload.issue_keys.map((item) => String(item)) : [];
        for (const issueKey of issueKeys) {
          const record = state.workflow.issues[issueKey];
          if (record) {
            record.status = "in_package";
            record.updated_at = new Date().toISOString();
          }
        }
        const createdAt = new Date().toISOString();
        const id = `pkg-${state.workflow.packages.length + 1}`;
        state.workflow.packages.push({
          id,
          name: String(payload.name ?? id),
          organization_id: payload.organization_id ? String(payload.organization_id) : null,
          organization_name: payload.organization_name ? String(payload.organization_name) : null,
          job_ids: Array.isArray(payload.job_ids) ? payload.job_ids.map((item) => String(item)) : [],
          issue_keys: issueKeys,
          status: "ready",
          created_at: createdAt,
          updated_at: createdAt,
        });
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ state: state.workflow }) });
        return;
      }
    }

    if (path === "/api/documents/upload" && method === "POST") {
      const body = req.postData() ?? "";
      const fiscalYear = body.match(/name="fiscal_year"\r?\n\r?\n([^\r\n]+)/)?.[1] ?? "";
      const docType = body.match(/name="doc_type"\r?\n\r?\n([^\r\n]+)/)?.[1] ?? "";
      const uploadIndex = state.uploadRequests.length + 1;
      const jobId = `job-upload-${uploadIndex}`;
      state.uploadRequests.push({ fiscalYear, docType });
      state.jobs.unshift(
        makeJob({
          job_id: jobId,
          filename: `${docType}-${fiscalYear}.pdf`,
          report_year: Number(fiscalYear),
          report_kind: docType.includes("final") ? "final" : "budget",
          doc_type: docType,
          issue_total: 0,
          issue_error: 0,
          issue_warn: 0,
          updated_ts: 1_779_100_000 + uploadIndex,
        }),
      );
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ job_id: jobId }) });
      return;
    }

    const analyzeMatch = path.match(/^\/api\/analyze\/([^/]+)$/);
    if (analyzeMatch && method === "POST") {
      state.analyzeRequests.push(req.postDataJSON() as { fiscal_year?: string; doc_type?: string });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "started", job_id: decodeURIComponent(analyzeMatch[1]) }),
      });
      return;
    }

    if (path === "/api/reports/download-batch" && method === "POST") {
      state.packageDownloads += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/zip",
        body: Buffer.from("PK\x03\x04mock-zip", "binary"),
      });
      return;
    }

    if (path === "/api/reports/download" && method === "GET") {
      state.reportDownloads.push(url.searchParams.get("job_id") ?? "");
      await route.fulfill({ status: 200, contentType: "application/pdf", body: Buffer.from("%PDF-1.4\n%%EOF\n", "utf-8") });
      return;
    }

    const previewMatch = path.match(/^\/api\/files\/([^/]+)\/preview$/);
    if (previewMatch && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: "<svg xmlns='http://www.w3.org/2000/svg' width='600' height='400'><rect width='600' height='400' fill='white'/></svg>",
      });
      return;
    }

    const sourceMatch = path.match(/^\/api\/files\/([^/]+)\/source$/);
    if (sourceMatch && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/pdf", body: Buffer.from("%PDF-1.4\n%%EOF\n", "utf-8") });
      return;
    }

    if (path.startsWith("/api/admin/config/") && method === "GET") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ items: [], total: 0 }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

async function openDemo(page: Page, state: MockState) {
  await page.context().addCookies([sessionCookie]);
  await installGbcMocks(page, state);
  await page.goto("/viewer/gbc-ui-demo");
  await expect(page.getByTestId("gbc-workbench-reset")).toBeVisible({ timeout: 20_000 });
}

test.describe("GBC UI demo primary actions", () => {
  test("navigates the main surfaces and keeps uploaded 2025 final task visible", async ({ page }) => {
    test.setTimeout(90_000);
    const state = initialState();
    await openDemo(page, state);

    await page.getByTestId("gbc-nav-workbench").click();
    await expect(page.getByTestId("gbc-workbench-reset")).toBeVisible();
    await page.getByTestId("gbc-sidebar-toggle-dept-001").click();
    await page.getByTestId("gbc-sidebar-node-unit-001").click();
    await expect.poll(() => state.orgJobRequests).toContain("unit-001");
    await expect(page.getByTestId("gbc-workbench-scope")).toHaveValue("unit-001");
    await page.getByTestId("gbc-workbench-scope").selectOption("dept-001");
    await expect.poll(() => state.orgJobRequests).toContain("dept-001");

    await page.getByTestId("gbc-nav-issues").click();
    await expect(page.getByTestId("gbc-issues-reset")).toBeVisible();
    await page.getByTestId("gbc-issues-year").selectOption("2026");
    await page.getByTestId("gbc-issues-scope").selectOption("dept-001");

    await page.getByTestId("gbc-nav-upload").click();
    await expect(page.getByTestId("gbc-upload-input-2026-budget")).toBeAttached();

    const chooser = page.waitForEvent("filechooser");
    await page.getByTestId("gbc-upload-label-2025-final").click();
    await (await chooser).setFiles(pdfFile("final-2025.pdf"));

    await expect.poll(() => state.uploadRequests).toEqual([{ fiscalYear: "2025", docType: "dept_final" }]);
    await expect.poll(() => state.analyzeRequests.length).toBe(1);
    expect(state.analyzeRequests[0]).toMatchObject({ fiscal_year: "2025", doc_type: "dept_final" });
    await expect(page.getByTestId("gbc-upload-next-tasks")).toBeVisible();
    await page.getByTestId("gbc-nav-tasks").click();
    await expect(page.getByTestId("gbc-task-detail-job-upload-1")).toBeVisible({ timeout: 20_000 });

    await page.getByTestId("gbc-nav-archive").click();
    await expect(page.getByTestId("gbc-archive-create-package")).toBeVisible();

    await page.getByTestId("gbc-nav-tasks").click();
    await expect(page.getByTestId("gbc-task-detail-job-2026")).toBeVisible();

    await page.getByTestId("gbc-nav-settings").click();
    await expect(page.getByTestId("admin-system-management")).toBeVisible();
    for (const section of ["overview", "organization", "users", "operations", "analysis", "rules", "mappings", "settings"]) {
      await page.getByTestId(`admin-section-${section}`).click();
      await expect(page.getByTestId("admin-system-management")).toBeVisible();
    }

    await page.getByTestId("gbc-notice-toggle").click();
    await page.getByTestId("gbc-notice-target-tasks").click();
    await expect(page.getByTestId("gbc-task-detail-job-2026")).toBeVisible();

    await page.getByTestId("gbc-user-menu-toggle").click();
    await page.getByTestId("gbc-user-menu-settings").click();
    await expect(page.getByTestId("admin-system-management")).toBeVisible();

    await page.getByTestId("gbc-logout").click();
    await expect.poll(() => state.logoutRequests).toBe(1);
    await expect(page).toHaveURL(/\/login/);
  });

  test("clicks issue workflow, archive download, task retry and delete actions", async ({ page }) => {
    test.setTimeout(90_000);
    const state = initialState();
    page.on("dialog", (dialog) => void dialog.accept());
    await openDemo(page, state);

    await page.getByTestId("gbc-workbench-detail-job-2026").click();
    await expect(page.getByTestId("gbc-detail-reanalyze")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("gbc-detail-source-download")).toHaveAttribute("href", /job-2026/);

    await page.getByTestId("gbc-detail-reanalyze").click();
    await expect.poll(() => state.reanalyzeRequests).toContain("job-2026");

    const detailDownload = page.waitForEvent("download");
    await page.getByTestId("gbc-detail-export").click();
    await detailDownload;
    await expect.poll(() => state.reportDownloads).toContain("job-2026");
    await expect(page).not.toHaveURL(/\/api\/reports\/download/);

    await page.getByTestId("gbc-detail-confirm").click();
    await expect.poll(() => state.workflow.issues["job-2026::issue-1"]?.status).toBe("confirmed");

    await page.getByTestId("gbc-detail-no-issue").click();
    await expect.poll(() => state.workflow.issues["job-2026::issue-1"]?.status).toBe("no_issue");

    await page.getByTestId("gbc-detail-needs-review").click();
    await expect.poll(() => state.workflow.issues["job-2026::issue-1"]?.status).toBe("needs_review");

    await page.getByTestId("gbc-detail-add-package").click();
    await expect.poll(() => state.workflow.issues["job-2026::issue-1"]?.status).toBe("confirmed");
    await expect(page.getByTestId("gbc-archive-create-package")).toBeEnabled({ timeout: 20_000 });

    await page.getByTestId("gbc-archive-create-package").click();
    await expect.poll(() => state.workflow.packages.length).toBe(1);
    await expect(page.getByTestId("gbc-archive-download-pkg-1")).toBeVisible();
    const download = page.waitForEvent("download");
    await page.getByTestId("gbc-archive-download-pkg-1").click();
    await download;
    await expect.poll(() => state.packageDownloads).toBe(1);

    await page.getByTestId("gbc-nav-issues").click();
    await page.getByTestId("gbc-issues-reset").click();
    await expect(page.getByTestId("gbc-issue-confirm-job-2026::issue-2")).toBeVisible();
    await page.getByTestId("gbc-issue-confirm-job-2026::issue-2").click();
    await expect.poll(() => state.workflow.issues["job-2026::issue-2"]?.status).toBe("confirmed");
    await page.getByTestId("gbc-issue-no-issue-job-2026::issue-2").click();
    await expect.poll(() => state.workflow.issues["job-2026::issue-2"]?.status).toBe("no_issue");
    await page.getByTestId("gbc-issue-check-job-2026::issue-2").check();
    await expect(page.getByTestId("gbc-batch-confirm")).toBeEnabled();
    await page.getByTestId("gbc-batch-confirm").click();
    await expect.poll(() => state.workflowPosts.filter((item) => item.action === "update_issue").length).toBeGreaterThanOrEqual(5);
    await page.getByTestId("gbc-batch-package").click();
    await expect(page.getByTestId("gbc-archive-create-package")).toBeEnabled();

    await page.getByTestId("gbc-nav-tasks").click();
    await page.getByTestId("gbc-task-rerun-job-2025").click();
    await expect.poll(() => state.reanalyzeRequests).toContain("job-2025");
    const taskDownload = page.waitForEvent("download");
    await page.getByTestId("gbc-task-export-job-2025").click();
    await taskDownload;
    await expect.poll(() => state.reportDownloads).toContain("job-2025");
    await expect(page).not.toHaveURL(/\/api\/reports\/download/);
    await page.getByTestId("gbc-task-delete-job-2025").click();
    await expect.poll(() => state.deleteRequests).toContain("job-2025");
    await expect(page.getByTestId("gbc-task-detail-job-2025")).toHaveCount(0);
  });

  test("keeps sidebar shortcuts and issue source navigation clickable", async ({ page }) => {
    test.setTimeout(90_000);
    const state = initialState();
    await openDemo(page, state);

    await page.getByTestId("gbc-sidebar-collapse").click();
    await expect(page.getByTestId("gbc-sidebar-expand")).toBeVisible();
    await page.getByTestId("gbc-sidebar-expand").click();
    await expect(page.getByTestId("gbc-sidebar-search")).toBeVisible();

    await page.getByTestId("gbc-sidebar-search").fill("Finance Unit");
    await expect(page.getByTestId("gbc-sidebar-node-unit-001")).toBeVisible();
    await page.getByTestId("gbc-sidebar-filter-高风险").click();
    await expect(page.getByTestId("gbc-sidebar-filter-高风险")).toHaveClass(/border-blue-500/);
    await page.getByTestId("gbc-sidebar-filter-全部").click();

    await page.getByTestId("gbc-workbench-tasks").click();
    await expect(page.getByTestId("gbc-task-detail-job-2026")).toBeVisible();
    await page.getByTestId("gbc-logo-home").click();
    await expect(page.getByTestId("gbc-workbench-reset")).toBeVisible();

    await page.getByTestId("gbc-workbench-upload-job-2026").click();
    await expect(page.getByTestId("gbc-upload-input-2026-budget")).toBeAttached();
    await page.getByTestId("gbc-logo-home").click();
    await expect(page.getByTestId("gbc-workbench-reset")).toBeVisible();

    await page.getByTestId("gbc-workbench-view-issues-job-2026").click();
    await expect(page.getByTestId("gbc-issue-view-job-2026::issue-1")).toBeVisible();
    await page.getByTestId("gbc-issue-view-job-2026::issue-1").click();
    await expect(page.getByTestId("gbc-detail-source-link")).toHaveAttribute("href", /job-2026/);
    await page.getByTestId("gbc-detail-queue-job-2026::issue-2").click();
    await expect(page.getByTestId("gbc-detail-source-download")).toHaveAttribute("href", /job-2026/);
  });

  test("user password menu opens the password page", async ({ page }) => {
    const state = initialState();
    await openDemo(page, state);

    await page.getByTestId("gbc-user-menu-toggle").click();
    await page.getByTestId("gbc-user-menu-password").click();
    await expect(page).toHaveURL(/\/account\/password$/);
  });
});
