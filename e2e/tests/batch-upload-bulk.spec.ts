import { expect, test } from "../../app/node_modules/playwright/test";

test.describe("Batch Upload Bulk Tools", () => {
  test("supports apply-all selection and retrying failed files", async ({ page }) => {
    test.setTimeout(60_000);

    let uploadAttempts = 0;
    let runCalls = 0;

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-czj",
                name: "财政局",
                level: "department",
                level_name: "部门",
                parent_id: null,
              },
              {
                id: "unit-czj-local",
                name: "财政局本级",
                level: "unit",
                level_name: "单位",
                parent_id: "dept-czj",
              },
            ],
            total: 2,
          }),
        });
        return;
      }

      if (path === "/api/documents/upload" && method === "POST") {
        uploadAttempts += 1;
        if (uploadAttempts === 1) {
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            body: JSON.stringify({ detail: "temporary upload error" }),
          });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "version-retry-001",
            job_id: "version-retry-001",
            filename: "测试2025预算.pdf",
          }),
        });
        return;
      }

      const runMatch = path.match(/^\/api\/documents\/([^/]+)\/run$/);
      if (runMatch && method === "POST") {
        runCalls += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ job_id: runMatch[1], status: "started" }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/e2e/batch-upload");
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 20_000 });

    await page.locator('input[type="file"]').setInputFiles({
      name: "测试2025预算.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
    });

    const startButton = page.getByTestId("batch-start");
    await expect(startButton).toBeDisabled();

    await page.getByTestId("batch-bulk-department").selectOption("dept-czj");
    await page.getByTestId("batch-bulk-unit").selectOption("unit-czj-local");
    await page.getByTestId("batch-apply-all").click();

    await expect(startButton).toBeEnabled();
    await startButton.click();

    await expect(page.getByTestId("batch-retry-failed")).toBeVisible();
    await expect.poll(() => uploadAttempts).toBe(1);

    await page.getByTestId("batch-retry-failed").click();
    await expect(startButton).toBeEnabled();
    await startButton.click();

    await expect.poll(() => uploadAttempts).toBe(2);
    await expect.poll(() => runCalls).toBe(1);
  });
});
