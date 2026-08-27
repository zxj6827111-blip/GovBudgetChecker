import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 2：应用骨架与 8+1 项导航的 e2e 验证。
 *
 * 覆盖范围（对照任务书 Task 2 测试要求）：
 * 1. 9 个路由可达性测试（均 200，管理项按角色）；
 * 2. 导航高亮态、键盘 Tab 可达、aria-current 正确性；
 * 3. 顶栏服务状态反例：/api/health 返回不健康时 UI 必须显示异常态；
 * 4. 逐项点击 9 个导航入口均不报错；
 * 5. 权限反例：非管理员访问「管理」组路由被拒。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const WORKSPACE_ROUTES = [
  "/workbench",
  "/upload",
  "/queue",
  "/review",
  "/history",
  "/archive",
  "/quality",
  "/rules",
  "/settings",
] as const;

async function installBaselineMocks(page: Page, options: { isAdmin: boolean; healthStatus?: "ok" | "down" }) {
  const healthStatus = options.healthStatus ?? "ok";
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: options.isAdmin } }),
      });
      return;
    }

    if (path === "/api/health") {
      if (healthStatus === "ok") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status: "ok", service: "GovBudgetChecker", ts: Date.now() / 1000 }),
        });
      } else {
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({ status: "down", error: "connect failed" }),
        });
      }
      return;
    }

    if (path === "/api/jobs") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { job_id: "job-1", status: "processing" },
          { job_id: "job-2", status: "review_required" },
        ]),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Workspace shell: 9-route navigation", () => {
  test("all 9 workspace routes are reachable (200) for an admin session", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });

    for (const route of WORKSPACE_ROUTES) {
      const response = await page.goto(route);
      expect(response, `route ${route} must respond`).not.toBeNull();
      expect(response!.status(), `route ${route} must return 200`).toBe(200);
    }
  });

  test("navigation highlight state and aria-current follow the active route", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });

    await page.goto("/workbench");
    const workbenchLink = page.getByTestId("gbc-workspace-nav-workbench");
    await expect(workbenchLink).toHaveAttribute("aria-current", "page");

    const uploadLink = page.getByTestId("gbc-workspace-nav-upload");
    await expect(uploadLink).not.toHaveAttribute("aria-current", "page");

    await uploadLink.click();
    await expect(page).toHaveURL(/\/upload$/);
    await expect(uploadLink).toHaveAttribute("aria-current", "page");
    await expect(workbenchLink).not.toHaveAttribute("aria-current", "page");
  });

  test("clicking through all 9 navigation entries does not error", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });

    await page.goto("/workbench");
    for (const item of ["workbench", "upload", "queue", "review", "history", "archive", "quality", "rules", "settings"]) {
      await page.getByTestId(`gbc-workspace-nav-${item}`).click();
      await page.waitForLoadState("domcontentloaded");
    }

    expect(pageErrors).toEqual([]);
  });

  test("navigation links are keyboard-reachable via Tab", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    const firstNavLink = page.getByTestId("gbc-workspace-nav-workbench");
    await firstNavLink.focus();
    await expect(firstNavLink).toBeFocused();

    // Tab 到下一个导航项，确认焦点确实移动到了"上传中心"而不是卡住或跳出导航区域
    await page.keyboard.press("Tab");
    const uploadLink = page.getByTestId("gbc-workspace-nav-upload");
    await expect(uploadLink).toBeFocused();
  });

  test("REGRESSION: service status must show unhealthy when /api/health reports down, never fake 服务正常", async ({
    page,
  }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true, healthStatus: "down" });
    await page.goto("/workbench");

    const statusEl = page.getByTestId("gbc-workspace-service-status");
    await expect(statusEl).toHaveAttribute("data-service-state", "unhealthy");
    await expect(statusEl).toContainText("服务异常");
    await expect(statusEl).not.toContainText("服务正常");
  });

  test("service status shows healthy when /api/health reports ok", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true, healthStatus: "ok" });
    await page.goto("/workbench");

    const statusEl = page.getByTestId("gbc-workspace-service-status");
    await expect(statusEl).toHaveAttribute("data-service-state", "healthy");
    await expect(statusEl).toContainText("服务正常");
  });

  test("badge counts reflect real job data (queue=1 analyzing, review=1 review_required)", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    await expect(page.getByTestId("gbc-workspace-nav-badge-queue")).toHaveText("1");
    await expect(page.getByTestId("gbc-workspace-nav-badge-review")).toHaveText("1");
  });

  test("admin group items are hidden from the sidebar for a non-admin user", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: false });
    await page.goto("/workbench");

    await expect(page.getByTestId("gbc-workspace-nav-quality")).toHaveCount(0);
    await expect(page.getByTestId("gbc-workspace-nav-rules")).toHaveCount(0);
    await expect(page.getByTestId("gbc-workspace-nav-settings")).toHaveCount(0);
    // 工作区组仍然完整可见，只有管理组被过滤
    await expect(page.getByTestId("gbc-workspace-nav-workbench")).toBeVisible();
  });

  test("REGRESSION: a non-admin user who navigates directly to an admin URL is redirected away, not shown admin content", async ({
    page,
  }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: false });

    await page.goto("/settings");
    await expect(page).toHaveURL(/\/workbench$/);
    // 被拒绝后落地到工作台，且工作台本身正常渲染（不是一个错误页）
    await expect(page.getByTestId("gbc-workspace-nav-workbench")).toBeVisible();
  });

  test("an admin user can reach the admin-only settings page directly", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });

    await page.goto("/settings");
    await expect(page).toHaveURL(/\/settings$/);
    // "系统设置" 同时出现在顶栏标题与页面主标题里，用 .first() 只校验存在即可，
    // 不需要区分是哪一处（本用例只关心"确实进入了设置页而不是被拒"）。
    await expect(page.getByText("系统设置").first()).toBeVisible();
  });
});
