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
            // 非管理员的徽章文字更长、操作按钮更多，是表格宽度的最坏情况。
            {
              username: "reviewer01",
              is_admin: false,
              is_active: true,
              organization_ids: ["org-edu"],
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
  test("adding a user opens only on request and can be collapsed", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installSettingsMocks(page, { isAdmin: true });
    await page.goto("/settings?section=users");
    const toggle = page.getByTestId("admin-users-toggle-create");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByTestId("admin-users-create-panel")).toHaveCount(0);
    await toggle.click();
    await expect(page.getByTestId("admin-users-new-username")).toBeVisible();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await toggle.click();
    await expect(page.getByTestId("admin-users-create-panel")).toHaveCount(0);
    await expect(page.getByTestId("admin-users-row-admin")).toBeVisible();
  });

  for (const width of [1280, 1440]) {
    test(`template forms remain within the panel at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 1000 });
      await page.context().addCookies([sessionCookie]);
      await installSettingsMocks(page, { isAdmin: true });
      await page.route("**/api/admin/templates", (route) => route.fulfill({
        json: { sources: [], drafts: [], bindings: [], current: [], audit: [], packages: [{
          id: "package-1", version: "2025.1", report_kind: "final", source_report_year: 2025,
          content_sha256: "a".repeat(64), capability_scope: { writing_auto: true, position_checks: true },
        }] },
      }));
      await page.goto("/settings?section=templates");
      await page.getByRole("button", { name: "4. 年度适用", exact: true }).click();
      await page.getByLabel("年度发布包").selectOption("package-1");
      const panel = page.getByTestId("template-step-3");
      await expect(panel.getByText("目标年度的能力兼容验证", { exact: true })).toBeVisible();
      const overflow = await panel.evaluate((element) => {
        const box = element.getBoundingClientRect();
        return [...element.querySelectorAll("input,select,textarea")]
          .filter((field) => field.getBoundingClientRect().width > 0)
          .filter((field) => {
            const b = field.getBoundingClientRect();
            return b.left < box.left - 1 || b.right > box.right + 1;
          }).length;
      });
      expect(overflow).toBe(0);
      await page.screenshot({ path: testInfo.outputPath(`templates-${width}.png`), fullPage: true });
    });
  }

  test("settings upload completion opens the current queue", async ({ page }) => {
    test.setTimeout(60_000);
    await page.context().addCookies([sessionCookie]);
    await installSettingsMocks(page, { isAdmin: true });
    await page.route("**/api/organizations/list", (route) => route.fulfill({ json: { organizations: [
      { id: "org-edu", name: "市教育局", level: "department", parent_id: null },
      { id: "org-school", name: "市第一中学", level: "unit", parent_id: "org-edu" },
    ] } }));
    await page.route("**/api/ai/capabilities", (route) => route.fulfill({ json: { enabled: false, profiles: [] } }));
    await page.route("**/api/documents/preflight", (route) => route.fulfill({ json: { report_year: 2026, doc_type: "dept_budget" } }));
    await page.route("**/api/documents/upload", (route) => route.fulfill({ json: { id: "settings-upload-1" } }));
    await page.route("**/api/documents/settings-upload-1/run", (route) => route.fulfill({ json: { job_id: "settings-upload-1", status: "started" } }));
    await page.goto("/settings?section=operations");
    await page.getByTestId("admin-operations-open-upload").click();
    await expect(page.getByRole("dialog", { name: "上传报告" })).toBeVisible();
    await page.getByTestId("batch-upload-file-input").setInputFiles({
      name: "市第一中学2026预算.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n%%EOF\n"),
    });
    await expect(page.getByTestId("batch-bulk-department").locator("option[value='org-edu']")).toHaveCount(1);
    await page.getByTestId("batch-bulk-department").selectOption("org-edu");
    await page.getByTestId("batch-bulk-unit").selectOption("org-school");
    await page.getByTestId("batch-apply-all").click();
    const confirmation = page.locator('[data-testid^="batch-confirm-metadata-"]');
    if (await confirmation.count()) await confirmation.first().click();
    await expect(page.getByTestId("batch-start")).toBeEnabled();
    await page.getByTestId("batch-start").click();
    await expect(page).toHaveURL(/\/queue\?job=settings-upload-1$/);
  });

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

  test("REGRESSION: one page title, template and calendar panels behind their own sections", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installSettingsMocks(page, { isAdmin: true });
    await page.goto("/settings");

    await expect(page.getByTestId("admin-system-management")).toBeVisible();
    // 曾有 3 个 H1：页面标题、年度公开时限、分区大标题。
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator("h1")).toHaveText("系统设置");

    // 这两块曾堆在管理区上方，把管理区推到第一屏以外。
    await expect(page.getByTestId("annual-template-panel")).toHaveCount(0);
    await expect(page.getByTestId("gbc-disclosure-calendar")).toHaveCount(0);

    await page.getByTestId("admin-section-templates").click();
    await expect(page.getByTestId("annual-template-panel")).toBeVisible();
    await expect(page.getByTestId("gbc-disclosure-calendar")).toHaveCount(0);

    await page.getByTestId("admin-section-calendar").click();
    await expect(page.getByTestId("gbc-disclosure-calendar")).toBeVisible();
    await expect(page.getByTestId("annual-template-panel")).toHaveCount(0);
  });

  for (const width of [1280, 1440]) {
    test(`REGRESSION: user table is not squeezed at ${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.context().addCookies([sessionCookie]);
      await installSettingsMocks(page, { isAdmin: true });
      await page.goto("/settings?section=users");

      const row = page.getByTestId("admin-users-row-admin");
      await expect(row).toBeVisible();

      // 曾因和表单并排，表格 590px 只分到 450px（1280 宽时 290px）。
      const fit = await row.evaluate((element) => {
        const table = element.closest("table") as HTMLTableElement;
        const wrapper = table.parentElement as HTMLElement;
        return { table: table.scrollWidth, wrapper: wrapper.clientWidth };
      });
      expect(fit.table).toBeLessThanOrEqual(fit.wrapper);

      // 曾把“管理员”挤成一字一行。
      const badgeBox = await row.getByText("管理员", { exact: true }).boundingBox();
      expect(badgeBox?.height ?? Number.POSITIVE_INFINITY).toBeLessThan(40);
    });
  }

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
