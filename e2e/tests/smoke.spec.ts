import { test, expect } from "../../app/node_modules/playwright/test";

test.describe("Scaffold", () => {
  test("batch upload harness renders with core controls", async ({ page }) => {
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
          body: JSON.stringify({ departments: [], total: 0 }),
        });
        return;
      }

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ organizations: [], total: 0 }),
        });
        return;
      }

      if (path === "/api/upload" && method === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ job_id: "job-smoke-001", id: "job-smoke-001" }),
        });
        return;
      }

      const runMatch = path.match(/^\/api\/documents\/([^/]+)\/run$/);
      if (runMatch && method === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ job_id: "job-smoke-001", status: "started" }),
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
    await expect(page).toHaveURL(/\/e2e\/batch-upload/);
    await expect(page.locator("h2").first()).toBeVisible();
    await expect(page.locator('input[type="file"]')).toHaveCount(1);
    await expect(page.locator("button").first()).toBeVisible();
  });
});
