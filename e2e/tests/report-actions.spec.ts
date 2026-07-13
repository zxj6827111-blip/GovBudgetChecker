import { expect, test } from "../../app/node_modules/playwright/test";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

test.describe("Report actions", () => {
  test("task detail can associate report to another organization", async ({ page }) => {
    test.setTimeout(60_000);

    let associateCalls = 0;
    const dialogMessages: string[] = [];
    const departmentOrg = {
      id: "dept-001",
      name: "Finance Bureau",
      level: "department",
      level_name: "department",
    };
    const unitOrg = {
      id: "unit-001",
      name: "Finance Bureau Unit",
      level: "unit",
      level_name: "unit",
    };
    let currentOrganization = { ...departmentOrg };

    await page.context().addCookies([sessionCookie]);
    page.on("dialog", async (dialog) => {
      dialogMessages.push(`${dialog.type()}:${dialog.message()}`);
      await dialog.accept();
    });

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            user: {
              username: "e2e-admin",
              display_name: "E2E Admin",
              is_admin: true,
            },
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-401" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: "job-401",
            filename: "association-report.pdf",
            status: "completed",
            report_year: 2025,
            report_kind: "budget",
            organization_id: currentOrganization.id,
            organization_name: currentOrganization.name,
            organization_level: currentOrganization.level,
            organization_match_type: "manual",
            organization_match_confidence: 1,
            updated_ts: 1_710_500_000,
            result: { issues: [] },
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-401/structured-ingest" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status: "done" }),
        });
        return;
      }

      if (path === "/api/organizations/list" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              { ...departmentOrg, parent_id: null },
              { ...unitOrg, parent_id: "dept-001" },
            ],
            total: 2,
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-401/org-suggestions" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            current: {
              organization: currentOrganization,
              match_type: "manual",
              confidence: 1,
            },
            suggestions: [
              {
                organization: unitOrg,
                confidence: 0.96,
              },
            ],
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-401/associate" && method === "POST") {
        associateCalls += 1;
        const payload = req.postDataJSON() as { org_id?: string };
        expect(payload.org_id).toBe("unit-001");
        currentOrganization = { ...unitOrg };

        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            success: true,
            organization_id: unitOrg.id,
            organization_name: unitOrg.name,
            organization_match_type: "manual",
            organization_match_confidence: 1,
          }),
        });
        return;
      }

      if (path.startsWith("/api/files/")) {
        await route.fulfill({ status: 204, body: "" });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/task/job-401");

    await expect(page.getByTestId("task-associate-button")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("task-associate-button").click();
    await expect(page.getByTestId("associate-dialog")).toBeVisible();
    await page.getByTestId("associate-option-unit-001").click();
    await page.getByTestId("associate-dialog-submit").click();

    await expect.poll(() => associateCalls).toBe(1);
    await expect(page.getByTestId("associate-dialog")).toHaveCount(0);
    await expect(page.getByText("Finance Bureau Unit", { exact: true })).toBeVisible();
    expect(dialogMessages.some((message) => message.includes("alert:"))).toBe(true);
  });

  test("task detail can trigger reanalysis", async ({ page }) => {
    test.setTimeout(60_000);

    let detailFetches = 0;
    let reanalyzeCalls = 0;
    let reanalyzeBody: Record<string, unknown> | null = null;
    let reanalysisStarted = false;
    const dialogMessages: string[] = [];

    await page.context().addCookies([sessionCookie]);
    page.on("dialog", async (dialog) => {
      dialogMessages.push(`${dialog.type()}:${dialog.message()}`);
      await dialog.accept();
    });

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            user: {
              username: "e2e-admin",
              display_name: "E2E Admin",
              is_admin: true,
            },
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-001" && method === "GET") {
        detailFetches += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: "job-001",
            filename: "demo-report.pdf",
            status: reanalysisStarted ? "started" : "completed",
            report_year: 2025,
            report_kind: "budget",
            organization_id: "dept-001",
            organization_name: "Finance Bureau",
            updated_ts: 1_710_000_000,
            result: {
              issues: [
                {
                  id: "issue-001",
                  rule_id: "R-001",
                  title: "Mismatch",
                  severity: "warning",
                  message: "Found a mismatch",
                  suggestion: "Review the source values",
                  page: 3,
                  evidence: [
                    {
                      page: 3,
                      text: "Found a mismatch",
                      bbox: [10, 10, 100, 40],
                    },
                  ],
                },
              ],
            },
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-001/structured-ingest" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status: reanalysisStarted ? "pending" : "done" }),
        });
        return;
      }

      if (path === "/api/jobs/job-001/reanalyze" && method === "POST") {
        reanalyzeCalls += 1;
        reanalyzeBody = req.postDataJSON() as Record<string, unknown>;
        reanalysisStarted = true;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: "job-001",
            source_job_id: "job-001",
            status: "started",
          }),
        });
        return;
      }

      if (path.startsWith("/api/files/")) {
        await route.fulfill({ status: 204, body: "" });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/task/job-001");

    const reanalyzeButton = page.getByTestId("task-reanalyze-button");
    await expect(reanalyzeButton).toBeVisible({ timeout: 20_000 });
    await expect(reanalyzeButton).toBeEnabled();

    await page.getByTestId("task-reanalyze-ai-toggle").uncheck();
    await reanalyzeButton.click();

    await expect.poll(() => reanalyzeCalls).toBe(1);
    await expect.poll(() => reanalyzeBody).toEqual({ use_local_rules: true, use_ai_assist: false });
    await expect.poll(() => detailFetches).toBeGreaterThan(1);
    await expect(reanalyzeButton).toBeDisabled();
    expect(dialogMessages.some((message) => message.includes("confirm:"))).toBe(true);
    expect(dialogMessages.some((message) => message.includes("alert:"))).toBe(true);
  });

  test("task detail can ignore issue, preview evidence and download report", async ({ page }) => {
    test.setTimeout(60_000);

    let ignoreCalls = 0;
    let downloadCalls = 0;
    const dialogMessages: string[] = [];

    await page.context().addCookies([sessionCookie]);
    page.on("dialog", async (dialog) => {
      dialogMessages.push(`${dialog.type()}:${dialog.message()}`);
      await dialog.accept();
    });

    await page.addInitScript(() => {
      window.print = () => undefined;
    });

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            user: {
              username: "e2e-admin",
              display_name: "E2E Admin",
              is_admin: true,
            },
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-ignore" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: "job-ignore",
            filename: "ignore-report.pdf",
            status: "completed",
            report_year: 2026,
            report_kind: "budget",
            organization_id: "dept-001",
            organization_name: "Finance Bureau",
            updated_ts: 1_710_000_000,
            result: {
              issues: [
                {
                  id: "issue-ignore-1",
                  rule_id: "R-IGNORE",
                  title: "Ignored mismatch",
                  severity: "high",
                  message: "Ignored mismatch details",
                  suggestion: "Review ignored mismatch",
                  page: 2,
                  evidence: [
                    {
                      page: 2,
                      text: "Ignored mismatch details",
                      bbox: [10, 20, 120, 80],
                    },
                  ],
                },
              ],
            },
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-ignore/structured-ingest" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status: "done" }),
        });
        return;
      }

      if (path === "/api/jobs/job-ignore/issues/ignore" && method === "POST") {
        ignoreCalls += 1;
        expect(req.postDataJSON()).toEqual({ issue_id: "issue-ignore-1" });
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ success: true, ignored_issue_ids: ["issue-ignore-1"] }),
        });
        return;
      }

      if (path === "/api/reports/download" && method === "GET") {
        downloadCalls += 1;
        expect(url.searchParams.get("job_id")).toBe("job-ignore");
        expect(url.searchParams.get("format")).toBe("pdf");
        await route.fulfill({
          status: 200,
          contentType: "application/pdf",
          headers: { "content-disposition": "attachment; filename=\"ignore-report.pdf\"" },
          body: "%PDF-1.4\n% e2e\n",
        });
        return;
      }

      if (path.startsWith("/api/files/")) {
        await route.fulfill({
          status: 200,
          contentType: "image/png",
          body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=", "base64"),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/task/job-ignore");

    await expect(page.getByTestId("task-ignore-issue-button")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("task-open-viewer-button").click();
    await expect(page.getByTestId("task-pdf-highlighter")).toBeVisible();
    await expect(page.getByTestId("task-pdf-highlighter-source")).toHaveAttribute("href", /job-ignore/);
    await page.getByTestId("task-pdf-highlighter-close").click();
    await expect(page.getByTestId("task-pdf-highlighter")).toHaveCount(0);

    await page.getByTestId("task-open-report-modal").click();
    await expect(page.getByTestId("task-report-modal")).toBeVisible();
    await page.getByTestId("task-report-print").click();
    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("task-report-download").click();
    await downloadPromise;
    await expect.poll(() => downloadCalls).toBe(1);
    await page.getByTestId("task-report-close").click();
    await expect(page.getByTestId("task-report-modal")).toHaveCount(0);

    await page.getByTestId("task-ignore-issue-button").click();
    await expect.poll(() => ignoreCalls).toBe(1);
    await expect(page.getByText("当前任务暂无问题。")).toBeVisible();
    expect(dialogMessages.some((message) => message.includes("confirm:"))).toBe(true);
    expect(dialogMessages.some((message) => message.includes("alert:"))).toBe(true);
  });

  test("department page can batch delete selected reports", async ({ page }) => {
    test.setTimeout(60_000);

    let batchDeleteCalls = 0;
    let lastDeletedIds: string[] = [];
    const dialogMessages: string[] = [];
    let jobs = [
      {
        job_id: "job-101",
        filename: "report-101.pdf",
        status: "completed",
        report_year: 2025,
        report_kind: "budget",
        merged_issue_total: 3,
        issue_error: 1,
        review_item_count: 0,
        organization_id: "dept-001",
        organization_name: "Finance Bureau",
        updated_ts: 1_710_000_001,
        ts: 1_710_000_001,
      },
      {
        job_id: "job-102",
        filename: "report-102.pdf",
        status: "completed",
        report_year: 2025,
        report_kind: "budget",
        merged_issue_total: 1,
        issue_error: 0,
        review_item_count: 0,
        organization_id: "dept-001",
        organization_name: "Finance Bureau",
        updated_ts: 1_710_000_002,
        ts: 1_710_000_002,
      },
    ];

    await page.context().addCookies([sessionCookie]);
    page.on("dialog", async (dialog) => {
      dialogMessages.push(`${dialog.type()}:${dialog.message()}`);
      await dialog.accept();
    });

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            user: {
              username: "e2e-admin",
              display_name: "E2E Admin",
              is_admin: true,
            },
          }),
        });
        return;
      }

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-001",
                name: "Finance Bureau",
                level: "department",
                level_name: "department",
                parent_id: null,
              },
              {
                id: "unit-001",
                name: "Finance Bureau Unit",
                level: "unit",
                level_name: "unit",
                parent_id: "dept-001",
              },
            ],
            total: 2,
          }),
        });
        return;
      }

      if (path === "/api/organizations/dept-001/jobs" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ jobs }),
        });
        return;
      }

      if (path === "/api/jobs/batch-delete" && method === "POST") {
        batchDeleteCalls += 1;
        const payload = req.postDataJSON() as { job_ids?: string[] };
        lastDeletedIds = Array.isArray(payload.job_ids) ? payload.job_ids : [];
        jobs = jobs.filter((job) => !lastDeletedIds.includes(job.job_id));

        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            deleted_job_ids: lastDeletedIds,
            failed: [],
          }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/department/dept-001");

    await expect(page.getByTestId("select-all-jobs")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("select-all-jobs").check();

    await expect(page.getByTestId("selected-actions-bar")).toBeVisible();
    await page.getByTestId("batch-delete-button").click();

    await expect.poll(() => batchDeleteCalls).toBe(1);
    expect(lastDeletedIds).toEqual(["job-101", "job-102"]);
    await expect(page.getByTestId("selected-actions-bar")).toHaveCount(0);
    await expect(page.getByTestId("job-select-job-101")).toHaveCount(0);
    await expect(page.getByTestId("job-select-job-102")).toHaveCount(0);
    expect(dialogMessages.some((message) => message.includes("confirm:"))).toBe(true);
    expect(dialogMessages.some((message) => message.includes("alert:"))).toBe(true);
  });

  test("department page row menu and batch export actions call expected APIs", async ({ page }) => {
    test.setTimeout(90_000);

    let associateCalls = 0;
    let reanalyzeCalls = 0;
    let deleteCalls = 0;
    let batchZipCalls = 0;
    const reportDownloads: Array<{ jobId: string | null; format: string | null }> = [];
    const dialogMessages: string[] = [];
    let jobs = [
      {
        job_id: "job-row-1",
        filename: "row-report-1.pdf",
        status: "completed",
        report_year: 2026,
        report_kind: "budget",
        merged_issue_total: 2,
        issue_error: 1,
        review_item_count: 0,
        organization_id: "dept-001",
        organization_name: "Finance Bureau",
        updated_ts: 1_710_000_010,
        ts: 1_710_000_010,
      },
      {
        job_id: "job-row-2",
        filename: "row-report-2.pdf",
        status: "completed",
        report_year: 2026,
        report_kind: "budget",
        merged_issue_total: 0,
        issue_error: 0,
        review_item_count: 0,
        organization_id: "dept-001",
        organization_name: "Finance Bureau",
        updated_ts: 1_710_000_011,
        ts: 1_710_000_011,
      },
    ];

    await page.context().addCookies([sessionCookie]);
    page.on("dialog", async (dialog) => {
      dialogMessages.push(`${dialog.type()}:${dialog.message()}`);
      await dialog.accept();
    });

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            user: {
              username: "e2e-admin",
              display_name: "E2E Admin",
              is_admin: true,
            },
          }),
        });
        return;
      }

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              { id: "dept-001", name: "Finance Bureau", level: "department", level_name: "department", parent_id: null },
              { id: "unit-001", name: "Finance Unit", level: "unit", level_name: "unit", parent_id: "dept-001" },
            ],
            total: 2,
          }),
        });
        return;
      }

      if (path === "/api/organizations/dept-001/jobs" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ jobs, total: jobs.length }),
        });
        return;
      }

      if (path === "/api/jobs/job-row-1/org-suggestions" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            current: { organization: { id: "dept-001", name: "Finance Bureau", level: "department" }, match_type: "manual", confidence: 1 },
            suggestions: [{ organization: { id: "unit-001", name: "Finance Unit", level: "unit" }, confidence: 0.98 }],
          }),
        });
        return;
      }

      if (path === "/api/jobs/job-row-1/associate" && method === "POST") {
        associateCalls += 1;
        expect(req.postDataJSON()).toEqual({ org_id: "unit-001" });
        jobs = jobs.map((job) => job.job_id === "job-row-1" ? { ...job, organization_id: "unit-001", organization_name: "Finance Unit" } : job);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ success: true, organization_id: "unit-001", organization_name: "Finance Unit" }),
        });
        return;
      }

      if ((path === "/api/jobs/job-row-1/reanalyze" || path === "/api/jobs/job-row-2/reanalyze") && method === "POST") {
        reanalyzeCalls += 1;
        expect(req.postDataJSON()).toEqual({ use_local_rules: true, use_ai_assist: false });
        const jobId = path.includes("job-row-1") ? "job-row-1" : "job-row-2";
        jobs = jobs.map((job) => job.job_id === jobId ? { ...job, status: "started" } : job);
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ success: true }) });
        return;
      }

      if (path === "/api/reports/download" && method === "GET") {
        reportDownloads.push({
          jobId: url.searchParams.get("job_id"),
          format: url.searchParams.get("format"),
        });
        const format = url.searchParams.get("format");
        if (format === "json") {
          await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ issues: [{ id: "issue-csv", rule_id: "R001", title: "CSV issue", severity: "high", page: 1 }] }),
          });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/pdf",
          headers: { "content-disposition": "attachment; filename=\"row-report.pdf\"" },
          body: "%PDF-1.4\n% e2e\n",
        });
        return;
      }

      if (path === "/api/reports/download-batch" && method === "POST") {
        batchZipCalls += 1;
        expect(req.postDataJSON()).toEqual({ job_ids: ["job-row-1", "job-row-2"] });
        await route.fulfill({
          status: 200,
          contentType: "application/zip",
          headers: { "content-disposition": "attachment; filename=\"reports.zip\"" },
          body: "zip",
        });
        return;
      }

      if (path === "/api/jobs/batch-delete" && method === "POST") {
        deleteCalls += 1;
        expect(req.postDataJSON()).toEqual({ job_ids: ["job-row-1"] });
        jobs = jobs.filter((job) => job.job_id !== "job-row-1");
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ deleted_job_ids: ["job-row-1"], failed: [] }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/department/dept-001");

    await expect(page.getByTestId("job-menu-job-row-1")).toBeVisible({ timeout: 20_000 });
    await page.getByTestId("department-reanalyze-ai-toggle").uncheck();
    await page.getByTestId("toggle-include-sub-control").click();
    await expect(page.getByTestId("toggle-include-sub")).not.toBeChecked();

    await page.getByTestId("job-select-job-row-1").check();
    await page.getByTestId("job-select-job-row-2").check();
    await expect(page.getByTestId("selected-actions-bar")).toBeVisible();

    await page.getByTestId("batch-reanalyze-button").click();
    await expect.poll(() => reanalyzeCalls).toBe(2);

    await page.getByTestId("job-select-job-row-1").check();
    await page.getByTestId("job-select-job-row-2").check();
    const csvDownload = page.waitForEvent("download");
    await page.getByTestId("batch-export-button").click();
    await csvDownload;
    await expect.poll(() => reportDownloads.filter((item) => item.format === "json").length).toBe(2);

    const zipDownload = page.waitForEvent("download");
    await page.getByTestId("batch-export-zip-button").click();
    await zipDownload;
    await expect.poll(() => batchZipCalls).toBe(1);

    await page.getByTestId("job-menu-job-row-1").click();
    await expect(page.getByTestId("job-view-job-row-1")).toBeVisible();
    await page.getByTestId("job-associate-job-row-1").click();
    await expect(page.getByTestId("associate-dialog")).toBeVisible();
    await page.getByTestId("associate-option-unit-001").click();
    await page.getByTestId("associate-dialog-submit").click();
    await expect.poll(() => associateCalls).toBe(1);

    await page.getByTestId("job-menu-job-row-1").click();
    const rowDownload = page.waitForEvent("download");
    await page.getByTestId("job-export-job-row-1").click();
    await rowDownload;
    await expect.poll(() => reportDownloads.some((item) => item.jobId === "job-row-1" && item.format === "pdf")).toBe(true);

    await page.getByTestId("job-menu-job-row-1").click();
    await page.getByTestId("job-delete-job-row-1").click();
    await expect.poll(() => deleteCalls).toBe(1);
    await expect(page.getByTestId("job-menu-job-row-1")).toHaveCount(0);
    expect(dialogMessages.some((message) => message.includes("confirm:"))).toBe(true);
    expect(dialogMessages.some((message) => message.includes("alert:"))).toBe(true);
  });

  test("department page loads reports in pages and keeps filters usable", async ({ page }) => {
    test.setTimeout(60_000);

    let firstPageCalls = 0;
    let secondPageCalls = 0;
    const jobs = Array.from({ length: 95 }, (_, index) => ({
      job_id: `job-page-${String(index + 1).padStart(3, "0")}`,
      filename: `report-page-${index + 1}.pdf`,
      status: index % 7 === 0 ? "started" : "completed",
      report_year: index < 80 ? 2025 : 2024,
      report_kind: index % 2 === 0 ? "budget" : "final",
      merged_issue_total: index % 5,
      issue_error: index % 3 === 0 ? 1 : 0,
      review_item_count: index % 11 === 0 ? 1 : 0,
      organization_id: "dept-001",
      organization_name: "Finance Bureau",
      updated_ts: 1_710_000_000 + index,
      ts: 1_710_000_000 + index,
    }));

    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            user: {
              username: "e2e-admin",
              display_name: "E2E Admin",
              is_admin: true,
            },
          }),
        });
        return;
      }

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-001",
                name: "Finance Bureau",
                level: "department",
                level_name: "department",
                parent_id: null,
              },
            ],
            total: 1,
          }),
        });
        return;
      }

      if (path === "/api/organizations/dept-001/jobs" && method === "GET") {
        const limit = Number(url.searchParams.get("limit") || 80);
        const offset = Number(url.searchParams.get("offset") || 0);
        if (offset === 0) firstPageCalls += 1;
        if (offset === 80) secondPageCalls += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            jobs: jobs.slice(offset, offset + limit),
            total: jobs.length,
            limit,
            offset,
          }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/department/dept-001");

    await expect(page.getByTestId("select-all-jobs")).toBeVisible({ timeout: 20_000 });
    await expect.poll(() => firstPageCalls).toBe(1);
    await expect(page.getByTestId("job-select-job-page-001")).toBeVisible();
    await expect(page.getByTestId("job-select-job-page-095")).toHaveCount(0);

    await page.getByTestId("load-more-jobs").click();
    await expect.poll(() => secondPageCalls).toBe(1);
    await expect(page.getByTestId("job-select-job-page-095")).toBeVisible();

    await page.getByRole("combobox").first().selectOption("2024");
    await expect(page.getByTestId("job-select-job-page-081")).toBeVisible();
    await expect(page.getByTestId("job-select-job-page-001")).toHaveCount(0);
  });

  test("department page can rename the current organization", async ({ page }) => {
    test.setTimeout(60_000);

    let updateCalls = 0;
    let currentOrgName = "Finance Bureau";
    const unitName = "Finance Bureau Unit";
    const dialogMessages: string[] = [];
    const jobs = [
      {
        job_id: "job-201",
        filename: "report-201.pdf",
        status: "completed",
        report_year: 2025,
        report_kind: "budget",
        merged_issue_total: 2,
        issue_error: 0,
        review_item_count: 0,
        organization_id: "dept-001",
        organization_name: currentOrgName,
        updated_ts: 1_710_100_001,
        ts: 1_710_100_001,
      },
    ];

    await page.context().addCookies([sessionCookie]);
    page.on("dialog", async (dialog) => {
      dialogMessages.push(`${dialog.type()}:${dialog.message()}`);
      await dialog.accept();
    });

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            user: {
              username: "e2e-admin",
              display_name: "E2E Admin",
              is_admin: true,
            },
          }),
        });
        return;
      }

      if (path === "/api/organizations") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            tree: [
              {
                id: "dept-001",
                name: currentOrgName,
                level: "department",
                level_name: "department",
                parent_id: null,
                job_count: 1,
                issue_count: 2,
                children: [
                  {
                    id: "unit-001",
                    name: unitName,
                    level: "unit",
                    level_name: "unit",
                    parent_id: "dept-001",
                    job_count: 0,
                    issue_count: 0,
                    children: [],
                  },
                ],
              },
            ],
            total: 2,
          }),
        });
        return;
      }

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-001",
                name: currentOrgName,
                level: "department",
                level_name: "department",
                parent_id: null,
              },
              {
                id: "unit-001",
                name: unitName,
                level: "unit",
                level_name: "unit",
                parent_id: "dept-001",
              },
            ],
            total: 2,
          }),
        });
        return;
      }

      if (path === "/api/organizations/dept-001/jobs" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            jobs: jobs.map((job) => ({
              ...job,
              organization_name: currentOrgName,
            })),
          }),
        });
        return;
      }

      if (path === "/api/organizations/dept-001" && method === "PUT") {
        updateCalls += 1;
        const payload = req.postDataJSON() as { name?: string };
        currentOrgName = String(payload.name ?? "").trim();

        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "dept-001",
            name: currentOrgName,
            level: "department",
            level_name: "department",
            parent_id: null,
          }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/department/dept-001");

    await expect(page.getByRole("heading", { name: currentOrgName })).toBeVisible({
      timeout: 20_000,
    });
    await page.getByTestId("org-rename-button").click();
    await expect(page.getByTestId("org-rename-input")).toBeVisible();
    await page.getByTestId("org-rename-input").fill("Finance Bureau Renamed");
    await page.getByTestId("org-rename-submit").click();

    await expect.poll(() => updateCalls).toBe(1);
    await expect(
      page.getByRole("heading", { name: "Finance Bureau Renamed" }),
    ).toBeVisible();
    await expect(
      page.locator("aside").getByRole("link", { name: "Finance Bureau Renamed" }),
    ).toBeVisible();
    expect(dialogMessages.some((message) => message.includes("alert:"))).toBe(true);
  });
});
