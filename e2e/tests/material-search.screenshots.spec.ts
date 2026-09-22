import path from "node:path";

import { expect, test, type Page, type Route } from "../../app/node_modules/playwright/test";

/**
 * WP2-C（全局材料搜索 + 导航收口）的**可视化证据采集**（默认不跑，需显式开启）。
 *
 * 默认套件不执行的原因与 WP2-A/WP2-B 相同：它只产出截图，不做门禁意义上的断言。
 * 开启方式::
 *
 *     GBC_CAPTURE_SCREENSHOTS=1 npm --prefix app run test:e2e -- material-search.screenshots
 *
 * 截图写入 `docs/wp2c-screenshots/`（1920 视口），供交付说明与人工复核引用。
 * 数据由本文件的 mock 提供：**截图证明的是界面在给定后端响应下渲染成什么样，
 * 不代表线上数据库的真实数据**。
 */

const CAPTURE_ENABLED = process.env.GBC_CAPTURE_SCREENSHOTS === "1";
const OUTPUT_DIR = path.resolve(__dirname, "..", "..", "docs", "wp2c-screenshots");

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const HEAD_SLOT = "slot-head-final-2024";

function item(overrides: Record<string, unknown> = {}) {
  return {
    slot_id: HEAD_SLOT,
    slot_key: "key-head-final-2024",
    jurisdiction_id: "district-pt",
    jurisdiction_name: "普陀区",
    department_org_id: "dept-ghzy",
    department_name: "上海市普陀区规划和自然资源局",
    subject_org_id: "unit-ghzy-head",
    subject_org_name: "上海市普陀区规划和自然资源局（本部）",
    subject_kind: "unit",
    material_scope: "unit_self",
    relationship: "head_unit",
    fiscal_year: 2024,
    report_kind: "final",
    caliber: "self",
    status: "review_required",
    status_reason: "findings_pending",
    applicability_status: "applicable",
    current_document_version_id: 22,
    current_filename: "current-final.pdf",
    matched_fields: ["unit", "fiscal_year", "report_kind", "relationship"],
    matched_filename: null,
    matched_document_version_id: null,
    matched_version_is_current: null,
    matched_job_uuid: null,
    matched_job_version_is_current: null,
    review_candidate: null,
    updated_at: "2026-09-22T12:00:00Z",
    ...overrides,
  };
}

const SUBORDINATE = item({
  slot_id: "slot-enforcement-final-2024",
  slot_key: "key-enforcement-final-2024",
  subject_org_id: "unit-ghzy-enforcement",
  subject_org_name: "上海市普陀区规划和自然资源局执法大队",
  relationship: "subordinate_unit",
  fiscal_year: 2024,
  report_kind: "budget",
  status: "uploaded",
  current_filename: "enforcement-budget.pdf",
});

function body(items: unknown[]) {
  return {
    ok: true,
    data: { items },
    meta: {
      data_basis: "existing_slots_only",
      expected_materials_ready: false,
      generated_at: "2026-09-22T12:00:00Z",
      pagination: { page: 1, page_size: 20, total: items.length, total_pages: 1 },
      query: "",
      relationship_resolution: "resolved",
      linkage_basis: "structured_document_version_id",
      legacy_unlinked_job_matches_excluded: true,
    },
  };
}

async function installMocks(page: Page, byQuery: Record<string, unknown>) {
  await page.route("**/api/**", async (route: Route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: true } }),
      });
      return;
    }
    if (path === "/api/health") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ status: "ok" }),
      });
      return;
    }
    if (path === "/api/jobs") {
      await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
      return;
    }
    if (path === "/api/materials/search") {
      const q = url.searchParams.get("q") ?? "";
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(byQuery[q] ?? body([])),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("WP2-C 截图采集（默认跳过）", () => {
  test.skip(!CAPTURE_ENABLED, "设置 GBC_CAPTURE_SCREENSHOTS=1 才采集截图");

  test.beforeEach(async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await page.setViewportSize({ width: 1920, height: 1080 });
  });

  test("WP2C_01 全局搜索（目标业务查询）", async ({ page }) => {
    await installMocks(page, { "规划和自然资源局 本部 2024 决算": body([item()]) });
    await page.goto("/workbench");
    await page.getByTestId("gbc-global-search-trigger").click();
    await page.getByTestId("gbc-global-search-input").fill("规划和自然资源局 本部 2024 决算");
    await expect(page.getByTestId("gbc-global-search-item")).toBeVisible();
    await page.screenshot({
      path: `${path.join(OUTPUT_DIR, "WP2C_01_global_search.png")}`,
    });
  });

  test("WP2C_02 全局搜索（多 token）", async ({ page }) => {
    await installMocks(page, {
      "规划和自然资源局 2024 决算 普陀": body([item(), SUBORDINATE]),
    });
    await page.goto("/workbench");
    await page.getByTestId("gbc-global-search-trigger").click();
    await page.getByTestId("gbc-global-search-input").fill("规划和自然资源局 2024 决算 普陀");
    await expect(page.getByTestId("gbc-global-search-item")).toHaveCount(2);
    await page.screenshot({
      path: `${path.join(OUTPUT_DIR, "WP2C_02_global_search_multitoken.png")}`,
    });
  });

  test("WP2C_03 命中历史文件名", async ({ page }) => {
    await installMocks(page, {
      "old-final.pdf": body([
        item({
          matched_filename: "old-final.pdf",
          matched_document_version_id: 11,
          matched_version_is_current: false,
          matched_fields: ["historical_filename"],
        }),
      ]),
    });
    await page.goto("/workbench");
    await page.getByTestId("gbc-global-search-trigger").click();
    await page.getByTestId("gbc-global-search-input").fill("old-final.pdf");
    await expect(page.getByTestId("gbc-global-search-historical-filename")).toBeVisible();
    await page.screenshot({
      path: `${path.join(OUTPUT_DIR, "WP2C_03_historical_filename_match.png")}`,
    });
  });

  test("WP2C_04 导航收口（侧栏 + /history 兼容提示）", async ({ page }) => {
    await installMocks(page, {});
    await page.goto("/workbench");
    await expect(page.getByTestId("gbc-workspace-nav-materials")).toBeVisible();
    await page.screenshot({
      path: `${path.join(OUTPUT_DIR, "WP2C_04_history_navigation_cleanup.png")}`,
    });

    await page.goto("/history");
    await expect(page.getByTestId("gbc-history-compat-notice")).toBeVisible();
    await page.screenshot({
      path: `${path.join(OUTPUT_DIR, "WP2C_04b_history_compat_entry.png")}`,
      fullPage: true,
    });
  });

  test("WP2C_05 搜索空态", async ({ page }) => {
    await installMocks(page, {});
    await page.goto("/workbench");
    await page.getByTestId("gbc-global-search-trigger").click();
    await page.getByTestId("gbc-global-search-input").fill("不存在的主体名称");
    await expect(page.getByTestId("gbc-global-search-empty")).toBeVisible();
    await page.screenshot({
      path: `${path.join(OUTPUT_DIR, "WP2C_05_search_empty_state.png")}`,
    });
  });
});
