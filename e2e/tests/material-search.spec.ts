import { expect, test, type Page, type Route } from "../../app/node_modules/playwright/test";

/**
 * WP2-C：全局材料搜索（Ctrl/Cmd+K 命令面板）e2e 验证。
 *
 * 覆盖范围：
 * 1. Ctrl+K / Cmd+K 在任意 workspace 页面打开面板，Escape 关闭并归还焦点；
 * 2. 目标业务查询 `规划和自然资源局 本部 2024 决算` -> 打开材料 -> 槽位详情；
 * 3. 输入为空时只显示引导文案，**不发请求**；
 * 4. 四态可分：loading / error / empty / normal，空态文案不泄露存在性；
 * 5. 历史文件名命中要显著标注，且详情页的"当前版本"不受搜索命中影响；
 * 6. job id 精确命中给出「进入复核」；历史版本 job 不成为复核候选；
 * 7. 越权材料不出现在结果里（连文件名/年份都不出现）；
 * 8. 过期响应不覆盖新结果（A 慢 800ms、B 快 50ms -> 界面显示 B）；
 * 9. 下拉/回车键导航：ArrowDown 移动选中项、Enter 打开当前选中材料。
 *
 * 数据全部由本文件 mock（后端不参与）：用例校验的是"给定后端响应，界面行为是否正确"，
 * 后端契约与真库语义由 tests/test_material_search_api.py 与
 * tests/test_material_search_pg.py 覆盖。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const HEAD_SLOT = "slot-head-final-2024";
const SIBLING_SLOT = "slot-sibling-final-2024";

function slotItem(overrides: Record<string, unknown> = {}) {
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

const META = {
  data_basis: "existing_slots_only",
  expected_materials_ready: false,
  generated_at: "2026-09-22T12:00:00Z",
  pagination: { page: 1, page_size: 20, total: 1, total_pages: 1 },
  query: "",
  relationship_resolution: "resolved",
  linkage_basis: "structured_document_version_id",
  legacy_unlinked_job_matches_excluded: true,
};

function searchBody(items: unknown[], meta: Record<string, unknown> = {}) {
  return {
    ok: true,
    data: { items },
    meta: { ...META, ...meta, pagination: { ...META.pagination, total: items.length, ...(meta.pagination as object ?? {}) } },
  };
}

interface SearchMockPlan {
  /** 默认响应（未命中的查询）。 */
  fallback?: { status: number; body: unknown; delayMs?: number };
  /** 按查询串精确匹配的响应。 */
  byQuery?: Record<string, { status: number; body: unknown; delayMs?: number }>;
  /** 记录实际发出的查询串（按顺序）。 */
  requests?: string[];
}

async function installSearchMocks(page: Page, plan: SearchMockPlan = {}) {
  const fallback = plan.fallback ?? { status: 200, body: searchBody([]) };
  const byQuery = plan.byQuery ?? {};
  const requests = plan.requests ?? [];

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
      requests.push(q);
      const plan2 = byQuery[q] ?? fallback;
      if (plan2.delayMs) {
        await new Promise((resolve) => setTimeout(resolve, plan2.delayMs));
      }
      await route.fulfill({
        status: plan2.status,
        contentType: "application/json",
        body: JSON.stringify(plan2.body),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });

  return requests;
}

async function openPanel(page: Page) {
  await page.getByTestId("gbc-global-search-trigger").click();
  await expect(page.getByTestId("gbc-global-search-dialog")).toBeVisible();
}

async function typeQuery(page: Page, value: string) {
  const input = page.getByTestId("gbc-global-search-input");
  await input.fill(value);
}

// ==== 1. 快捷键打开 / 关闭 ===================================================


test("Ctrl+K opens the search panel on any workspace page and Escape closes it", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page);
  await page.goto("/materials");
  await expect(page.getByTestId("gbc-global-search-dialog")).toHaveCount(0);

  await page.keyboard.press("Control+K");
  await expect(page.getByTestId("gbc-global-search-dialog")).toBeVisible();
  await expect(page.getByTestId("gbc-global-search-input")).toBeFocused();

  await page.keyboard.press("Escape");
  await expect(page.getByTestId("gbc-global-search-dialog")).toHaveCount(0);
  // 关闭后焦点回到顶栏搜索按钮（§四十）
  await expect(page.getByTestId("gbc-global-search-trigger")).toBeFocused();

  // 另一个 workspace 页面同样有效
  await page.goto("/workbench");
  await page.keyboard.press("Control+K");
  await expect(page.getByTestId("gbc-global-search-dialog")).toBeVisible();
});

test("Meta+K opens the panel too (macOS behaviour)", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page);
  await page.goto("/workbench");

  await page.keyboard.press("Meta+K");
  await expect(page.getByTestId("gbc-global-search-dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByTestId("gbc-global-search-dialog")).toHaveCount(0);
});

test("the topbar search button opens the same panel", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page);
  await page.goto("/workbench");

  await openPanel(page);
  await expect(page.getByTestId("gbc-global-search-hint")).toContainText("可搜索文件名");
});

// ==== 2. 空输入不发请求 + 四态 ==============================================


test("an empty query shows the hint and never calls the API", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  const requests = await installSearchMocks(page);
  await page.goto("/workbench");
  await openPanel(page);

  await expect(page.getByTestId("gbc-global-search-hint")).toBeVisible();
  await typeQuery(page, "   ");
  await page.waitForTimeout(600);
  expect(requests).toEqual([]);
  await expect(page.getByTestId("gbc-global-search-hint")).toBeVisible();
});

test("a too-short query is rejected locally without a request", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  const requests = await installSearchMocks(page);
  await page.goto("/workbench");
  await openPanel(page);

  await typeQuery(page, "普");
  await expect(page.getByTestId("gbc-global-search-invalid")).toContainText("至少输入 2 个字符");
  expect(requests).toEqual([]);
});

test("empty results show the non-leaking empty state", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, { fallback: { status: 200, body: searchBody([]) } });
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "不存在的主体");

  const empty = page.getByTestId("gbc-global-search-empty");
  await expect(empty).toBeVisible();
  await expect(empty).toContainText("没有找到符合条件且你有权限查看的材料");
  await expect(empty).not.toContainText("无权");
  const state = page.getByTestId("gbc-global-search-state");
  await expect(state).toHaveAttribute("data-state", "empty");
});

test("a failing request shows the error state, not an empty list", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, {
    fallback: { status: 503, body: { detail: "material ledger database unavailable" } },
  });
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "规划和自然资源局");

  const state = page.getByTestId("gbc-global-search-state");
  await expect(state).toHaveAttribute("data-state", "error");
  await expect(page.getByTestId("gbc-global-search-error")).toContainText("database unavailable");
  await expect(page.getByTestId("gbc-global-search-empty")).toHaveCount(0);
});

test("a 403 from the backend is surfaced as an error, not as 'no materials'", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, {
    fallback: { status: 403, body: { detail: "no organization authorized for this account" } },
  });
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "规划和自然资源局");

  await expect(page.getByTestId("gbc-global-search-error")).toContainText("no organization authorized");
  await expect(page.getByTestId("gbc-global-search-empty")).toHaveCount(0);
});

// ==== 3. 目标业务查询 → 打开材料 =============================================


test("the target query finds the head-unit material and opens its slot detail", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  const requests = await installSearchMocks(page, {
    byQuery: {
      "规划和自然资源局 本部 2024 决算": {
        status: 200,
        body: searchBody([slotItem()], { query: "规划和自然资源局 本部 2024 决算" }),
      },
    },
  });
  await page.goto("/workbench");
  await openPanel(page);

  await typeQuery(page, "规划和自然资源局 本部 2024 决算");
  const item = page.getByTestId("gbc-global-search-item").first();
  await expect(item).toBeVisible();
  await expect(page.getByTestId("gbc-global-search-subject")).toContainText("规划和自然资源局（本部）");
  await expect(page.getByTestId("gbc-global-search-meta")).toContainText("2024 年度");
  await expect(page.getByTestId("gbc-global-search-meta")).toContainText("决算");
  await expect(page.getByTestId("gbc-global-search-reason")).toContainText("命中：");
  expect(requests).toContain("规划和自然资源局 本部 2024 决算");

  await page.getByTestId("gbc-global-search-open-material").first().click();
  await expect(page).toHaveURL(new RegExp(`/materials/slots/${HEAD_SLOT}$`));
  // 面板在导航时关闭
  await expect(page.getByTestId("gbc-global-search-dialog")).toHaveCount(0);
});

test("ArrowDown moves the selection and Enter opens the selected material", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, {
    fallback: {
      status: 200,
      body: searchBody([
        slotItem(),
        slotItem({
          slot_id: SIBLING_SLOT,
          slot_key: "key-sibling",
          subject_org_id: "unit-ghzy-affairs",
          subject_org_name: "上海市普陀区规划和自然资源局事务中心",
          relationship: "subordinate_unit",
        }),
      ]),
    },
  });
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "规划和自然资源局");

  const items = page.getByTestId("gbc-global-search-item");
  await expect(items).toHaveCount(2);
  await expect(items.nth(0)).toHaveAttribute("data-selected", "true");

  await page.keyboard.press("ArrowDown");
  await expect(items.nth(1)).toHaveAttribute("data-selected", "true");
  // 到底部不环绕
  await page.keyboard.press("ArrowDown");
  await expect(items.nth(1)).toHaveAttribute("data-selected", "true");
  await page.keyboard.press("ArrowUp");
  await expect(items.nth(0)).toHaveAttribute("data-selected", "true");
  await page.keyboard.press("ArrowUp");
  await expect(items.nth(0)).toHaveAttribute("data-selected", "true");

  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/materials/slots/${SIBLING_SLOT}$`));
});

// ==== 4. 历史文件名命中 =====================================================


test("a historical filename hit is flagged and does not change the current version", async ({
  page,
}) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, {
    byQuery: {
      "old-final.pdf": {
        status: 200,
        body: searchBody([
          slotItem({
            matched_filename: "old-final.pdf",
            matched_document_version_id: 11,
            matched_version_is_current: false,
            matched_fields: ["historical_filename"],
          }),
        ]),
      },
    },
    fallback: { status: 200, body: searchBody([]) },
  });
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "old-final.pdf");

  const item = page.getByTestId("gbc-global-search-item").first();
  await expect(item).toBeVisible();
  const badge = page.getByTestId("gbc-global-search-historical-filename");
  await expect(badge).toContainText("命中历史文件");
  await expect(badge).toContainText("old-final.pdf");
  // 当前文件名仍然显示当前版本（搜索命中历史版本不改写当前真值）
  await expect(page.getByTestId("gbc-global-search-current-filename")).toContainText(
    "current-final.pdf",
  );

  await page.getByTestId("gbc-global-search-open-material").first().click();
  await expect(page).toHaveURL(new RegExp(`/materials/slots/${HEAD_SLOT}$`));
});

// ==== 5. Job 搜索与复核入口 =================================================


test("a current job id offers the review entry, a historical job id does not", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, {
    byQuery: {
      "job-current-001": {
        status: 200,
        body: searchBody([
          slotItem({
            matched_job_uuid: "job-current-001",
            matched_job_version_is_current: true,
            matched_fields: ["job_id"],
            review_candidate: { job_uuid: "job-current-001", status: "done" },
          }),
        ]),
      },
      "job-history-001": {
        status: 200,
        body: searchBody([
          slotItem({
            matched_job_uuid: "job-history-001",
            matched_job_version_is_current: false,
            matched_fields: ["job_id"],
            review_candidate: { job_uuid: "job-current-001", status: "done" },
          }),
        ]),
      },
      "job-legacy-001": { status: 200, body: searchBody([]) },
    },
    fallback: { status: 200, body: searchBody([]) },
  });
  await page.goto("/workbench");
  await openPanel(page);

  await typeQuery(page, "job-current-001");
  await expect(page.getByTestId("gbc-global-search-enter-review")).toBeVisible();
  await page.getByTestId("gbc-global-search-enter-review").click();
  await expect(page).toHaveURL(/\/review\?job=job-current-001$/);

  // 历史版本的 job：能找到材料，但标注为历史，且复核候选仍是当前运行
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "job-history-001");
  await expect(page.getByTestId("gbc-global-search-historical-job")).toContainText("历史版本");
  await expect(page.getByTestId("gbc-global-search-enter-review")).toHaveAttribute(
    "href",
    "/review?job=job-current-001",
  );

  // legacy 任务（没有精确关联字段）：不猜槽位，结果是空的
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "job-legacy-001");
  await expect(page.getByTestId("gbc-global-search-empty")).toBeVisible();
});

test("a review candidate whose status forbids entry shows the reason instead of a link", async ({
  page,
}) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, {
    fallback: {
      status: 200,
      body: searchBody([
        slotItem({
          matched_job_uuid: "job-running-001",
          matched_fields: ["job_id"],
          review_candidate: { job_uuid: "job-running-001", status: "processing" },
        }),
      ]),
    },
  });
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "job-running-001");

  await expect(page.getByTestId("gbc-global-search-enter-review")).toHaveCount(0);
  const blocked = page.getByTestId("gbc-global-search-review-blocked");
  await expect(blocked).toBeVisible();
  await expect(blocked).toContainText("尚未分析完成");
  // 「打开材料」不受第二个动作不可用的影响
  await expect(page.getByTestId("gbc-global-search-open-material")).toBeVisible();
});

// ==== 6. 权限：越权材料不出现在结果里 =======================================


test("REGRESSION: an unauthorized unit never appears in results (no name, filename or year leak)", async ({
  page,
}) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, {
    // 后端在 SQL 层就过滤掉了：越权查询返回空列表（与"没命中"同形）
    fallback: { status: 200, body: searchBody([]) },
  });
  await page.goto("/workbench");
  await openPanel(page);
  await typeQuery(page, "事务中心");

  await expect(page.getByTestId("gbc-global-search-empty")).toBeVisible();
  const dialogText = (await page.getByTestId("gbc-global-search-dialog").innerText()) ?? "";
  expect(dialogText).not.toContain("事务中心");
  expect(dialogText).not.toContain("sibling-final.pdf");
  expect(dialogText).not.toContain(SIBLING_SLOT);
});

// ==== 7. 过期响应不覆盖新结果 ===============================================


test("REGRESSION: a slow earlier response never overwrites the newer query result", async ({
  page,
}) => {
  await page.context().addCookies([sessionCookie]);
  await installSearchMocks(page, {
    byQuery: {
      AA: {
        delayMs: 800,
        status: 200,
        body: searchBody([
          slotItem({ slot_id: "slot-from-A", subject_org_name: "第一个查询的结果" }),
        ]),
      },
      BB: {
        delayMs: 50,
        status: 200,
        body: searchBody([
          slotItem({ slot_id: "slot-from-B", subject_org_name: "第二个查询的结果" }),
        ]),
      },
    },
  });
  await page.goto("/workbench");
  await openPanel(page);

  // 查询串必须 >= 2 个字符，否则会被本地校验拦下（那是另一个用例覆盖的路径）
  await typeQuery(page, "AA");
  // 等 debounce 把 A 发出去，再立刻改成 B
  await page.waitForTimeout(350);
  await typeQuery(page, "BB");

  await expect(page.getByTestId("gbc-global-search-subject").first()).toContainText(
    "第二个查询的结果",
  );
  // 等 A 的 800ms 也过去，确认它没有覆盖回来
  await page.waitForTimeout(900);
  await expect(page.getByTestId("gbc-global-search-subject").first()).toContainText(
    "第二个查询的结果",
  );
  await expect(page.getByTestId("gbc-global-search-results")).not.toContainText("第一个查询的结果");
});

test("debounce collapses rapid typing into a single request", async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
  const requests = await installSearchMocks(page, {
    fallback: { status: 200, body: searchBody([]) },
  });
  await page.goto("/workbench");
  await openPanel(page);

  const input = page.getByTestId("gbc-global-search-input");
  await input.type("规划和", { delay: 40 });
  await input.type("自然资源局", { delay: 40 });
  await page.waitForTimeout(600);
  expect(requests.length).toBe(1);
  expect(requests[0]).toBe("规划和自然资源局");
});
