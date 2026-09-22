import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 2：应用骨架与导航的 e2e 验证（WP2-C 收口后为 **9 项一级导航**）。
 *
 * 覆盖范围：
 * 1. 路由可达性（均 200，管理项按角色）；``/history`` 作为**兼容入口**仍然可达；
 * 2. 导航高亮态、键盘 Tab 可达、aria-current 正确性；
 * 3. 顶栏服务状态反例：/api/health 返回不健康时 UI 必须显示异常态；
 * 4. 逐项点击 9 个一级导航入口均不报错；
 * 5. 权限反例：非管理员访问「管理」组路由被拒；
 * 6. **WP2-C 导航收口**（§四十八~§五十五）：侧栏不再有「任务历史」、
 *    工作区顺序为 …审核工作台 → 材料台账 → 导出归档、``/history`` 直连仍可用
 *    并显示兼容/运维提示。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

/** 可达路由（9 项一级导航 + 兼容入口 /history）。 */
const REACHABLE_ROUTES = [
  "/workbench",
  "/upload",
  "/queue",
  "/review",
  "/materials",
  "/archive",
  "/quality",
  "/rules",
  "/settings",
  "/history",
] as const;

/** 侧栏里应当存在的一级导航项（顺序即信息架构顺序）。 */
const PRIMARY_NAV_IDS = [
  "workbench",
  "upload",
  "queue",
  "review",
  "materials",
  "archive",
  "quality",
  "rules",
  "settings",
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

test.describe("Workspace shell: 9-item primary navigation", () => {
  test("all primary routes plus the legacy /history entry are reachable (200) for an admin session", async ({
    page,
  }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });

    for (const route of REACHABLE_ROUTES) {
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
    for (const item of PRIMARY_NAV_IDS) {
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

  // --- WP2-C：一级导航收口（§四十九~§五十五） --------------------------------

  test("REGRESSION: 任务历史 is no longer a primary navigation entry", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    await expect(page.getByTestId("gbc-workspace-nav-history")).toHaveCount(0);
  });

  test("sidebar keeps exactly the 9 primary entries in the intended order", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });
    await page.goto("/workbench");

    // 先等"管理"组出现：侧栏是等 /api/auth/me 回来后才知道当前账号是不是管理员，
    // evaluateAll 不会自动等待，直接读会在管理员组渲染之前拿到半个列表。
    await expect(page.getByTestId("gbc-workspace-nav-settings")).toBeVisible();

    const ids = await page
      .locator('[data-testid^="gbc-workspace-nav-"]')
      .evaluateAll((nodes) =>
        nodes
          .map((node) => node.getAttribute("data-testid") ?? "")
          .filter((value) => !value.includes("-badge-"))
          .map((value) => value.replace("gbc-workspace-nav-", "")),
      );
    expect(ids).toEqual([...PRIMARY_NAV_IDS]);
    // 材料台账必须在审核工作台之后、导出归档之前
    expect(ids.indexOf("materials")).toBeGreaterThan(ids.indexOf("review"));
    expect(ids.indexOf("materials")).toBeLessThan(ids.indexOf("archive"));
  });

  test("/history remains reachable as a compatibility entry and says so", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });

    const response = await page.goto("/history");
    expect(response?.status()).toBe(200);
    await expect(page.getByTestId("gbc-history-page")).toBeVisible();
    const notice = page.getByTestId("gbc-history-compat-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("任务历史已退出一级业务导航");
    await expect(notice).toContainText("材料台账");
    // 页面能力没有被删掉：筛选器与列表仍在
    await expect(page.getByTestId("gbc-history-search")).toBeVisible();
  });

  test("the topbar shows a readable title for /history instead of a bare path", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installBaselineMocks(page, { isAdmin: true });
    await page.goto("/history");

    const breadcrumb = page.getByTestId("gbc-workspace-breadcrumb");
    await expect(breadcrumb).toContainText("任务运行历史（兼容）");
    await expect(breadcrumb).not.toContainText("/history");
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
