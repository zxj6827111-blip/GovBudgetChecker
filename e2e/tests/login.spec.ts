import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * 登录页 e2e（修复 C：登录页纳入设计系统）。
 *
 * 覆盖范围：
 * 1. 主按钮使用 primary 令牌（墨绿 #087f75），不再是被硬编码 rgba(59,130,246)
 *    支配的旧配色（蓝色只存在于渐变任意值里，色号类名扫描抓不到）；
 * 2. 登录功能三态不受换色影响：校验中 / 登录失败（错误提示） / 登录成功（跳转）。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

interface LoginMockState {
  loginSucceeded: boolean;
  loginFails: boolean;
  loginRequests: number;
}

async function installLoginMocks(page: Page, state: LoginMockState) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const method = req.method().toUpperCase();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      // 登录成功前探测必须说"未登录"（否则页面直接跳走，表单测不到）；
      // 登录成功后说"已登录"（落地页的会话态）。
      if (state.loginSucceeded) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ user: { username: "e2e-user", is_admin: true } }),
        });
        return;
      }
      await route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ detail: "not logged in" }),
      });
      return;
    }

    if (path === "/api/auth/login" && method === "POST") {
      state.loginRequests += 1;
      if (state.loginFails) {
        await route.fulfill({
          status: 401,
          contentType: "application/json",
          body: JSON.stringify({ detail: "用户名或密码错误" }),
        });
        return;
      }
      state.loginSucceeded = true;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: true } }),
      });
      return;
    }

    if (path === "/api/health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Login page (fix C: design-system colors, three states intact)", () => {
  test("REGRESSION: submit button renders the primary token color (墨绿), never the old hardcoded blue/slate", async ({
    page,
  }) => {
    await installLoginMocks(page, { loginSucceeded: false, loginFails: false, loginRequests: 0 });
    await page.goto("/login");

    const button = page.locator('button[type="submit"]');
    await expect(button).toBeVisible({ timeout: 10_000 });
    await expect(button).toContainText("登录");

    // primary-600 = #087f75（docs/UI_COLOR_TOKEN_MAPPING.md 实测主按钮色）。
    // 旧实现是 bg-slate-900(rgb(15,23,42))，蓝色则藏在渐变任意值里——
    // 这里用计算样式直接钉住令牌色，防止未来又漂回硬编码。
    const backgroundColor = await button.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(backgroundColor).toBe("rgb(8, 127, 117)");
    expect(backgroundColor).not.toBe("rgb(15, 23, 42)");
    expect(backgroundColor).not.toBe("rgb(59, 130, 246)");
  });

  test("checking state shows while the auth probe is pending", async ({ page }) => {
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/auth/me") {
        // 永不响应：页面必须停留在"正在检查登录状态..."
        await new Promise(() => {});
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto("/login");
    await expect(page.getByText("正在检查登录状态...")).toBeVisible({ timeout: 10_000 });
  });

  test("failed login shows the backend error message inline", async ({ page }) => {
    const state: LoginMockState = { loginSucceeded: false, loginFails: true, loginRequests: 0 };
    await installLoginMocks(page, state);
    await page.goto("/login");

    await page.locator('input[autocomplete="username"]').fill("admin");
    await page.locator('input[type="password"]').fill("wrong-password");
    await page.locator('button[type="submit"]').click();

    await expect(page.locator('button[type="submit"]')).toContainText("登录", { timeout: 10_000 });
    await expect(page.getByText("用户名或密码错误")).toBeVisible();
    await expect.poll(() => state.loginRequests).toBe(1);
    // 失败后停留在登录页，不得跳转
    await expect(page).toHaveURL(/\/login/);
  });

  test("successful login submits credentials and navigates away", async ({ page }) => {
    const state: LoginMockState = { loginSucceeded: false, loginFails: false, loginRequests: 0 };
    // 预置会话 cookie：登录成功跳转到受保护路由时中间件放行
    await page.context().addCookies([sessionCookie]);
    await installLoginMocks(page, state);
    await page.goto("/login");

    await page.locator('input[autocomplete="username"]').fill("admin");
    await page.locator('input[type="password"]').fill("correct-password");
    await page.locator('button[type="submit"]').click();

    expect.poll(() => state.loginRequests).toBe(1);
    // 登录成功后离开 /login（当前默认落地为 "/"；修复 D 将切换为 /workbench
    // 并同步更新本断言）。
    await expect(page).not.toHaveURL(/\/login/, { timeout: 15_000 });
  });
});
