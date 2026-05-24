import { expect, test } from "../../app/node_modules/playwright/test";

test.describe("Batch Upload Fallback", () => {
  test("falls back to /api/upload on 503 and still triggers /run", async ({ page }) => {
    test.setTimeout(60_000);

    let docUploadCalls = 0;
    let v2UploadCalls = 0;
    let runCalls = 0;
    let runVersionId = "";

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/config") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            ai_assist_enabled: false,
            ai_extractor_alive: false,
          }),
        });
        return;
      }

      if (path === "/api/jobs") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([]),
        });
        return;
      }

      if (path === "/api/departments") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            departments: [
              {
                id: "dept-mzj",
                name: "姘戞斂灞€",
                level: "department",
                level_name: "閮ㄩ棬",
                parent_id: null,
                children: [
                  {
                    id: "unit-mzj-local",
                    name: "姘戞斂灞€鏈骇",
                    level: "unit",
                    level_name: "鍗曚綅",
                    parent_id: "dept-mzj",
                  },
                ],
                job_count: 0,
                issue_count: 0,
              },
            ],
            total: 1,
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
                id: "dept-mzj",
                name: "姘戞斂灞€",
                level: "department",
                level_name: "閮ㄩ棬",
                parent_id: null,
              },
              {
                id: "unit-mzj-local",
                name: "姘戞斂灞€鏈骇",
                level: "unit",
                level_name: "鍗曚綅",
                parent_id: "dept-mzj",
              },
            ],
            total: 2,
          }),
        });
        return;
      }

      if (path === "/api/documents/preflight" && method === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            filename: "fallback-budget-2025.pdf",
            report_year: 2025,
            doc_type: "dept_budget",
            current: {
              organization_id: "unit-mzj-local",
              organization_name: "unit-mzj-local",
              level: "unit",
              department_id: "dept-mzj",
              department_name: "dept-mzj",
              confidence: 0.98,
              match_basis: "e2e",
            },
          }),
        });
        return;
      }

      if (path === "/api/documents/upload" && method === "POST") {
        docUploadCalls += 1;
        await route.fulfill({
          status: 503,
          contentType: "application/json",
          body: JSON.stringify({ detail: "service unavailable" }),
        });
        return;
      }

      if (path === "/api/upload" && method === "POST") {
        v2UploadCalls += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: "job-fallback-001",
            id: "job-fallback-001",
            filename: "姘戞斂灞€閮ㄩ棬2025棰勭畻.pdf",
          }),
        });
        return;
      }

      const runMatch = path.match(/^\/api\/documents\/([^/]+)\/run$/);
      if (runMatch && method === "POST") {
        runCalls += 1;
        runVersionId = decodeURIComponent(runMatch[1]);

        const payload = req.postDataJSON() as Record<string, unknown>;
        expect(payload.mode).toBe("dual");
        expect(typeof payload.doc_type).toBe("string");

        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ job_id: runVersionId, status: "started" }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.addInitScript(() => window.localStorage.clear());
    await page.goto("/e2e/batch-upload");
    await expect(page).toHaveURL(/\/e2e\/batch-upload/);
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 20_000 });

    const preflightDone = page.waitForResponse((response) => {
      const req = response.request();
      return (
        req.method().toUpperCase() === "POST" &&
        new URL(response.url()).pathname === "/api/documents/preflight"
      );
    });

    await page
      .locator('input[type="file"]')
      .setInputFiles({
        name: "姘戞斂灞€閮ㄩ棬2025棰勭畻.pdf",
        mimeType: "application/pdf",
        buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
      });

    const startButton = page.getByTestId("batch-start");
    await preflightDone;
    await expect(startButton).toBeEnabled({ timeout: 10_000 });
    await startButton.click();

    await expect.poll(() => docUploadCalls).toBe(1);
    await expect.poll(() => v2UploadCalls).toBe(1);
    await expect.poll(() => runCalls).toBe(1);
    expect(runVersionId).toBe("job-fallback-001");
  });
});
