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
      // 模拟真实登录代理的行为：成功时种下会话 cookie，随后跳转的受保护
      // 路由才能通过中间件（否则 /workbench 会被弹回 /login 形成循环）。
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        headers: { "set-cookie": "gbc_session=e2e-session; Path=/; SameSite=Lax" },
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

  test("successful login lands on the new workbench by default (fix D)", async ({ page }) => {
    const state: LoginMockState = { loginSucceeded: false, loginFails: false, loginRequests: 0 };
    // 预置会话 cookie：登录成功跳转到受保护路由时中间件放行
    await page.context().addCookies([sessionCookie]);
    await installLoginMocks(page, state);
    await page.goto("/login");

    await page.locator('input[autocomplete="username"]').fill("admin");
    await page.locator('input[type="password"]').fill("correct-password");
    await page.locator('button[type="submit"]').click();

    expect.poll(() => state.loginRequests).toBe(1);
    // 修复 D：默认落地新版工作台总览 /workbench（旧实现落 "/" 即旧单体）
    await expect(page).toHaveURL(/\/workbench/, { timeout: 15_000 });
    await expect(page.getByTestId("gbc-workbench-page")).toBeVisible({ timeout: 15_000 });
  });

  test("REGRESSION (fix D): ?next=/queue deep link is honored after login", async ({ page }) => {
    const state: LoginMockState = { loginSucceeded: false, loginFails: false, loginRequests: 0 };
    await page.context().addCookies([sessionCookie]);
    await installLoginMocks(page, state);
    await page.goto("/login?next=/queue");

    await page.locator('input[autocomplete="username"]').fill("admin");
    await page.locator('input[type="password"]').fill("correct-password");
    await page.locator('button[type="submit"]').click();

    // 深链必须回到 /queue，不得被无脑改写成 /workbench
    await expect(page).toHaveURL(/\/queue/, { timeout: 15_000 });
    await expect(page).not.toHaveURL(/\/workbench/);
  });

  test("REGRESSION (fix D): open-redirect payloads in ?next are rejected and fall back to /workbench", async ({
    page,
  }) => {
    const payloads = [
      "//evil.com", // 协议相对 URL：旧实现真实存在的放行漏洞
      "/\\evil.com", // 反斜杠变体：浏览器把 \ 规范化为 /，等价于 //evil.com
      "/\\/evil.com", // 反斜杠变体二：归一化后同样是协议相对 URL
      "https://evil.com",
      "/login", // 自指：拒绝，防登录页死循环
      "relative/path", // 非 / 开头
    ];
    for (const payload of payloads) {
      const state: LoginMockState = { loginSucceeded: false, loginFails: false, loginRequests: 0 };
      const context = page.context();
      await context.clearCookies();
      await context.addCookies([sessionCookie]);
      await installLoginMocks(page, state);
      await page.goto(`/login?next=${encodeURIComponent(payload)}`);

      await page.locator('input[autocomplete="username"]').fill("admin");
      await page.locator('input[type="password"]').fill("correct-password");
      await page.locator('button[type="submit"]').click();

      // 被拒的 next 一律回落 /workbench；浏览器永远不得离开本地站点
      await expect(page).toHaveURL(/\/workbench/, { timeout: 15_000 });
      expect(page.url()).not.toContain("evil.com");
    }
  });
});

test.describe("Root entry redirects to the new UI (fix D)", () => {
  test("authenticated visit to / redirects to /workbench", async ({ page }) => {
    const state: LoginMockState = { loginSucceeded: false, loginFails: false, loginRequests: 0 };
    await page.context().addCookies([sessionCookie]);
    await installLoginMocks(page, state);

    await page.goto("/");
    await expect(page).toHaveURL(/\/workbench/, { timeout: 15_000 });
    await expect(page.getByTestId("gbc-workbench-page")).toBeVisible({ timeout: 15_000 });
  });

  test("unauthenticated visit to / lands on login, then the new workbench after logging in", async ({ page }) => {
    const state: LoginMockState = { loginSucceeded: false, loginFails: false, loginRequests: 0 };
    // 无会话 cookie：中间件把 / 重定向到 /login（根路径不带 next 参数）
    await installLoginMocks(page, state);

    await page.goto("/");
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });

    await page.locator('input[autocomplete="username"]').fill("admin");
    await page.locator('input[type="password"]').fill("correct-password");
    await page.locator('button[type="submit"]').click();

    // 登录成功 → 默认落新版工作台（完整链路：/ → /login → /workbench）
    await expect(page).toHaveURL(/\/workbench/, { timeout: 15_000 });
    await expect(page.getByTestId("gbc-workbench-page")).toBeVisible({ timeout: 15_000 });
  });
});
