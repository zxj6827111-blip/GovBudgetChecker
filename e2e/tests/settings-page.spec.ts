import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 8.4：系统设置页 e2e 验证。
 *
 * 覆盖范围（对照任务书）：
 * 1. 管理员进入 /settings 看到既有 SystemManagementPanel（含概览卡片，
 *    组织数据来自真实 /api/organizations 响应的拉平）；
 * 2. 面板内部导航可用（切到"用户与权限"分区，UserManagementPanel 渲染）；
 * 3. 非管理员被拦（重定向 /workbench，不渲染管理面板）；
 * 4. 组织列表加载失败时优雅降级（提示条），不崩页。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

interface SettingsMockOptions {
  isAdmin: boolean;
  orgsStatus?: 200 | 500;
}

async function installSettingsMocks(page: Page, options: SettingsMockOptions) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
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
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
      return;
    }

    if (path === "/api/organizations") {
      const status = options.orgsStatus ?? 200;
      if (status !== 200) {
        await route.fulfill({ status, contentType: "application/json", body: JSON.stringify({ detail: "error" }) });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          tree: [
            {
              id: "org-edu",
              name: "市教育局",
              level: "department",
              children: [{ id: "org-school", name: "市第一中学", level: "unit", children: [] }],
            },
          ],
        }),
      });
      return;
    }

    if (path === "/api/users" && route.request().method() === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          users: [
            {
              username: "admin",
              is_admin: true,
              is_active: true,
              organization_ids: [],
              created_at: 1750000000,
              updated_at: 1750000000,
            },
          ],
        }),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Settings page (Task 8.4)", () => {
  test("admin sees the embedded system management panel with real org count", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installSettingsMocks(page, { isAdmin: true });
    await page.goto("/settings");

    await expect(page.getByTestId("gbc-settings-page")).toBeVisible();
    // 复用既有 SystemManagementPanel（内部自带分区导航）
    await expect(page.getByTestId("admin-system-management")).toBeVisible();
    // 组织树拉平后两个组织（部门 + 单位）
    await expect(page.getByTestId("admin-overview-card-organization")).toContainText("2 个组织");
  });

  test("panel section navigation switches to user management", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installSettingsMocks(page, { isAdmin: true });
    await page.goto("/settings");

    await page.getByTestId("admin-section-users").click();
    // UserManagementPanel 是 SystemManagementPanel 内嵌的既有能力
    await expect(page.getByTestId("admin-users-panel")).toBeVisible();
  });

  test("REGRESSION: non-admin is redirected away from /settings", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installSettingsMocks(page, { isAdmin: false });
    await page.goto("/settings");

    // 超时放宽原因见 quality-management.spec.ts 同名用例注释（dev-server 并行抢占）
    await expect(page).toHaveURL(/\/workbench/, { timeout: 15_000 });
    await expect(page.getByTestId("gbc-settings-page")).toHaveCount(0);
  });

  test("REGRESSION: org list failure degrades gracefully without page error", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.context().addCookies([sessionCookie]);
    await installSettingsMocks(page, { isAdmin: true, orgsStatus: 500 });
    await page.goto("/settings");

    await expect(page.getByTestId("gbc-settings-org-load-failed")).toBeVisible();
    // 面板仍然渲染（组织相关功能有兜底），页面不崩
    await expect(page.getByTestId("admin-system-management")).toBeVisible();
    expect(pageErrors).toEqual([]);
  });
});
