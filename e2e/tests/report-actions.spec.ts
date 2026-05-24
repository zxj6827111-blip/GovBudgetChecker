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

    await reanalyzeButton.click();

    await expect.poll(() => reanalyzeCalls).toBe(1);
    await expect.poll(() => detailFetches).toBeGreaterThan(1);
    await expect(reanalyzeButton).toBeDisabled();
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
