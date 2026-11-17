import { expect, test } from "../../app/node_modules/playwright/test";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

test.describe("Report actions", () => {
  test("department page can associate report to another organization", async ({ page }) => {
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

      if (path === "/api/organizations/dept-001/jobs" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            jobs: [
              {
                job_id: "job-401",
                filename: "association-report.pdf",
                status: "completed",
                report_year: 2025,
                report_kind: "budget",
                merged_issue_total: 0,
                organization_id: currentOrganization.id,
                organization_name: currentOrganization.name,
                organization_level: currentOrganization.level,
                updated_ts: 1_710_500_000,
              },
            ],
            total: 1,
            limit: 20,
            offset: 0,
          }),
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

    // 任务页已改为复核工作台（43cb2266）：关联报告入口在部门/单位报告的作业菜单里。
    await page.goto("/department/dept-001");
    await page.getByTestId("job-menu-job-401").click();
    await page.getByTestId("job-associate-job-401").click();
    await expect(page.getByTestId("associate-dialog")).toBeVisible();
    await page.getByTestId("associate-option-unit-001").click();
    await page.getByTestId("associate-dialog-submit").click();

    await expect.poll(() => associateCalls).toBe(1);
    await expect(page.getByTestId("associate-dialog")).toHaveCount(0);
    await expect(page.getByText("Finance Bureau Unit", { exact: false }).first()).toBeVisible();
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

      if (path === "/api/jobs/job-001/reanalyze-options" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: "job-001",
            inherit: { available: true, unknown_fields: [], message: null, mode: "dual", use_local_rules: true, use_ai_assist: false, ai_profile_id: null, ai_configuration: null },
            last_execution: { status: "done", analysis_conclusion: null, ai_requested: false, local_state: "completed", ai_state: "skipped" },
            model: { state: "not_applicable", message: null, profile: null },
            capabilities: { enabled: true, active_profile_id: "m1", profiles: [{ id: "m1", name: "模型1", model: "m1" }] },
          }),
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
            inheritance_warnings: ["上轮部分单元未完成，本次沿用相同检查方式"],
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

    // 任务页已改为复核工作台（43cb2266）：重新分析入口在「更多」菜单里。
    await page.goto("/review?job=job-001");
    await page.getByTestId("gbc-review-tab-more").click();
    const reanalyzeButton = page.getByTestId("gbc-review-reanalyze");
    await expect(reanalyzeButton).toBeVisible({ timeout: 20_000 });
    await expect(reanalyzeButton).toBeEnabled();

    // 检查方式继承：默认"按上次检查方式"，对话框即确认，不再有点头 confirm。
    await reanalyzeButton.click();
    await expect(page.getByTestId("reanalyze-method-dialog")).toBeVisible();
    await expect(page.getByTestId("reanalyze-method-picker-inherit")).toBeChecked();
    await page.getByTestId("reanalyze-method-confirm").click();

    await expect.poll(() => reanalyzeCalls).toBe(1);
    // 默认继承：只发 check_method，由后端按上轮记录解析本地规则/AI/原模型。
    await expect.poll(() => reanalyzeBody).toEqual({ check_method: "inherit" });
    await expect.poll(() => detailFetches).toBeGreaterThan(1);
    expect(dialogMessages.some((message) => message.includes("alert:"))).toBe(true);
  });

  test("workbench can ignore issue, preview evidence and export report", async ({ page }) => {
    test.setTimeout(60_000);

    let ignoreCalls = 0;

    await page.context().addCookies([sessionCookie]);

    await page.addInitScript(() => {
      window.print = () => undefined;
      // 导出走 window.open(报告下载地址)；拦下即可断言前端真的拼出了下载请求。
      (window as unknown as { __openedUrls: string[] }).__openedUrls = [];
      window.open = ((url?: string | URL) => {
        (window as unknown as { __openedUrls: string[] }).__openedUrls.push(String(url ?? ""));
        return null;
      }) as typeof window.open;
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

      if (path === "/api/reviews" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ok: true,
            data: {
              review_context: { available: true, reason: null, slot_id: "slot-ignore", job_uuid: "job-ignore" },
              review: {
                slot_id: "slot-ignore",
                current_document_version_id: 1,
                current_analysis: {
                  job_uuid: "job-ignore",
                  analysis_revision: 1,
                  analysis_basis_token: "job-ignore:1",
                  document_version_id: 1,
                  status: "done",
                  completed: true,
                  formal_issue_count: 1,
                },
                current_session: {
                  review_session_id: "sess-ignore",
                  slot_id: "slot-ignore",
                  document_version_id: 1,
                  analysis_job_uuid: "job-ignore",
                  analysis_basis_token: "job-ignore:1",
                  status: "in_progress",
                  started_by: "e2e-admin",
                  started_at: "2026-10-12T12:00:00Z",
                },
                history: [],
                completion_gate: { can_complete: false, blockers: [{ code: "pending_findings", count: 1 }] },
                obligation_review: { available: true, reason: null, pending_total: 0, items: [] },
              },
            },
          }),
        });
        return;
      }

      if (path === "/api/workflow" && method === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ issues: {}, packages: [] }),
        });
        return;
      }

      if (path === "/api/workflow" && method === "POST") {
        ignoreCalls += 1;
        expect(req.postDataJSON()).toMatchObject({
          action: "update_issue",
          job_id: "job-ignore",
          issue_id: "issue-ignore-1",
          status: "no_issue",
        });
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ issues: {}, packages: [] }),
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

    await page.goto("/review?job=job-ignore");
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 20_000 });
    // 证据原文固定在第 2 页：文档面板可见即可继续核对。
    await expect(page.getByTestId("gbc-review-document-pane")).toBeVisible();
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toBeVisible();

    // 导出报告：工作台从「更多」菜单打开下载地址。
    await page.getByTestId("gbc-review-tab-more").click();
    await page.getByTestId("gbc-review-export").click();
    await expect
      .poll(() => page.evaluate(() => (window as unknown as { __openedUrls: string[] }).__openedUrls ?? []))
      .toEqual([expect.stringMatching(/\/api\/reports\/download\?job_id=job-ignore&format=pdf/)]);

    // 忽略问题：工作台卡片内选择原因 → 保存 → 走 /api/workflow。
    const issuesTab = page.getByTestId("gbc-review-tab-issues");
    await issuesTab.click();
    const toggle = page.getByTestId("gbc-review-toggle-list");
    if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
    await page.getByTestId("gbc-review-issue-card-issue-ignore-1").click();
    await page.getByTestId("gbc-review-issue-ignore-issue-ignore-1").click();
    await page.getByTestId("gbc-review-ignore-reason-rounding").check();
    await page.getByTestId("gbc-review-ignore-save").click();
    await expect.poll(() => ignoreCalls).toBe(1);
    await expect(page.getByTestId("gbc-review-issue-card-issue-ignore-1")).toHaveCount(0);
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
        // 检查方式继承契约：批量默认逐条继承，只发 check_method，不悄悄打开 AI。
        expect(req.postDataJSON()).toEqual({ check_method: "inherit" });
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
    await page.getByTestId("toggle-include-sub-control").click();
    await expect(page.getByTestId("toggle-include-sub")).not.toBeChecked();

    await page.getByTestId("job-select-job-row-1").check();
    await page.getByTestId("job-select-job-row-2").check();
    await expect(page.getByTestId("selected-actions-bar")).toBeVisible();

    await page.getByTestId("batch-reanalyze-button").click();
    await expect(page.getByTestId("reanalyze-method-dialog")).toBeVisible();
    await expect(page.getByTestId("reanalyze-method-picker-inherit")).toBeChecked();
    await page.getByTestId("reanalyze-method-confirm").click();
    await expect.poll(() => reanalyzeCalls).toBe(2);
    // 等待对话框关闭与成功条目取消选择落定，避免与延迟提交的选择清空竞态。
    await expect(page.getByTestId("reanalyze-method-dialog")).toHaveCount(0);
    await expect(page.getByTestId("selected-actions-bar")).toHaveCount(0);

    await page.getByTestId("job-select-job-row-1").check();
    await page.getByTestId("job-select-job-row-2").check();
    await expect(page.getByTestId("selected-actions-bar")).toContainText("已选择 2");
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
