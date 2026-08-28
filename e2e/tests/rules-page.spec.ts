import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 8.3：规则与版本页 e2e 验证。
 *
 * 覆盖范围（对照任务书）：
 * 1. 管理员看到后端返回的真实规则集版本/条目数/引擎版本；
 * 2. 反例：页面不出现 "3.8.1"（原型图设计稿占位版本号）；
 * 3. 反例：端点不可用时显示"未识别到"，不出现编造的版本号；
 * 4. 规则条目清单渲染（rule_id/标题/严重级别/适用范围）；
 * 5. 非管理员被拦（重定向 /workbench）；
 * 6. 版本留痕说明（历史任务版本去哪里看）。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

interface RulesMockOptions {
  isAdmin: boolean;
  versionStatus?: 200 | 403;
  versionPayload?: Record<string, unknown>;
  entriesPayload?: Record<string, unknown>;
}

const DEFAULT_VERSION_PAYLOAD: Record<string, unknown> = {
  available: true,
  unavailable_reason: null,
  rules_file: "rules/v3_3.yaml",
  ruleset_version: "v3_3_all_in_one",
  metadata_version: "v3_3_r2",
  rule_entry_count: 15,
  engine_version: "0.1.0",
};

const DEFAULT_ENTRIES_PAYLOAD: Record<string, unknown> = {
  available: true,
  total: 15,
  items: [
    { rule_id: "R001", title: "金额单位口径错误（万元/元）", severity: "high", doc_scope: ["预算", "决算"] },
    { rule_id: "R002", title: "功能分类科目编码错配（常见码表）", severity: "high", doc_scope: ["预算", "决算"] },
    { rule_id: "R003", title: "重复词与口误（标点/词语重复）", severity: "low", doc_scope: ["预算"] },
  ],
};

async function installRulesMocks(page: Page, options: RulesMockOptions) {
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

    if (path === "/api/rules/version") {
      const status = options.versionStatus ?? 200;
      if (status !== 200) {
        await route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify({ detail: "forbidden" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(options.versionPayload ?? DEFAULT_VERSION_PAYLOAD),
      });
      return;
    }

    if (path === "/api/rules/entries") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(options.entriesPayload ?? DEFAULT_ENTRIES_PAYLOAD),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Rules & versions page (Task 8.3)", () => {
  test("admin sees real ruleset version, entry count and engine version", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installRulesMocks(page, { isAdmin: true });
    await page.goto("/rules");

    // 展示的是后端真实返回值（mock 数据与真实 v3_3.yaml 解析结果一致）
    await expect(page.getByTestId("gbc-rules-ruleset-version")).toContainText("v3_3_all_in_one");
    await expect(page.getByTestId("gbc-rules-metadata-version")).toContainText("v3_3_r2");
    await expect(page.getByTestId("gbc-rules-entry-count")).toContainText("15");
    await expect(page.getByTestId("gbc-rules-engine-version")).toContainText("0.1.0");
    await expect(page.getByTestId("gbc-rules-ruleset-version")).toContainText("rules/v3_3.yaml");
  });

  test("REGRESSION: page must never show the prototype placeholder version 3.8.1", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installRulesMocks(page, { isAdmin: true });
    await page.goto("/rules");

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("3.8.1");
  });

  test("REGRESSION: unavailable rules file shows 未识别到, never a fabricated version", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installRulesMocks(page, {
      isAdmin: true,
      versionPayload: {
        available: false,
        unavailable_reason: "规则文件不可读或不是有效的规则集 YAML：rules/missing.yaml",
        rules_file: "rules/missing.yaml",
        ruleset_version: null,
        metadata_version: null,
        rule_entry_count: null,
        engine_version: "0.1.0",
      },
      entriesPayload: { available: false, items: [], total: null },
    });
    await page.goto("/rules");

    await expect(page.getByTestId("gbc-rules-ruleset-version")).toContainText("未识别到");
    await expect(page.getByTestId("gbc-rules-entry-count")).toContainText("未识别到");
    // 引擎版本来自 provenance（与规则文件无关），仍然显示真实值
    await expect(page.getByTestId("gbc-rules-engine-version")).toContainText("0.1.0");
    // 不可读原因如实展示
    await expect(page.getByTestId("gbc-rules-unavailable")).toContainText("不可读");
    // 空条目清单如实显示降级文案
    await expect(page.getByTestId("gbc-rules-entries-empty")).toContainText("暂无法获取规则条目");
  });

  test("rules entries table renders summaries with severity badges", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installRulesMocks(page, { isAdmin: true });
    await page.goto("/rules");

    const table = page.getByTestId("gbc-rules-entries-table");
    await expect(table).toBeVisible();
    await expect(page.getByTestId("gbc-rules-entry-R001")).toContainText("金额单位口径错误");
    await expect(page.getByTestId("gbc-rules-entry-R001")).toContainText("预算、决算");
    await expect(page.getByTestId("gbc-rules-entry-R003")).toContainText("low");
  });

  test("version provenance note explains where to find per-task versions", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installRulesMocks(page, { isAdmin: true });
    await page.goto("/rules");

    const note = page.getByTestId("gbc-rules-provenance-note");
    await expect(note).toContainText("元数据");
    await expect(note).toContainText("历史任务普遍缺少版本留痕字段");
  });

  test("REGRESSION: non-admin is redirected away from /rules", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installRulesMocks(page, { isAdmin: false });
    await page.goto("/rules");

    await expect(page).toHaveURL(/\/workbench/);
    await expect(page.getByTestId("gbc-rules-page")).toHaveCount(0);
  });

  test("REGRESSION: version endpoint failure degrades to 未识别到 without page error", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.context().addCookies([sessionCookie]);
    await installRulesMocks(page, { isAdmin: true, versionStatus: 403 });
    await page.goto("/rules");

    await expect(page.getByTestId("gbc-rules-unavailable")).toBeVisible();
    await expect(page.getByTestId("gbc-rules-ruleset-version")).toContainText("未识别到");
    expect(pageErrors).toEqual([]);
  });
});
