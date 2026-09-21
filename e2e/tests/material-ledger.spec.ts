import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * WP2-A：材料台账三页（06 首页 → 07 区级主管部门矩阵 → 08 部门材料矩阵）e2e 验证。
 *
 * 覆盖范围：
 * 1. 06→07→08 全链路可走通（首页卡片 → 区级矩阵 → 部门矩阵）；
 * 2. 三个分组（部门汇总 / 本部单位 / 直属单位）都出现，且预算/决算不串位；
 * 3. 反例（红线）：没有槽位时显示「尚无已建立材料」，绝不显示"缺失/逾期/未上传"；
 * 4. 反例（红线）：没有应收基线时完整率显示 —，不显示 0%；
 * 5. 状态徽章与状态原因使用统一中文文案（待确认归属 + 原因说明）；
 * 6. 筛选（年度/文种）真的进入查询串；
 * 7. 空态、加载态、错误态三态可分，错误态带重试入口；
 * 8. 部门矩阵缺财政年度时提示选择年度，且不发请求（不猜当前自然年）。
 *
 * 所有数据由本文件 mock（后端不参与）：用例校验的是"给定后端响应，页面显示是否正确"，
 * 后端契约由 tests/test_material_ledger_api.py 覆盖。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const DISTRICT_ID = "district-pt";
const DEPT_ID = "dept-ghzy";
const HEAD_UNIT_ID = "unit-ghzy-head";
const SUB_UNIT_ID = "unit-ghzy-enforcement";

const STATUS_COUNTS = {
  not_due: 0,
  missing: 0,
  uploaded: 3,
  processing: 0,
  review_required: 1,
  reviewing: 0,
  completed: 1,
  not_applicable: 0,
  mapping_required: 1,
  failed: 0,
};

const EXPECTED_NULLS = {
  expected_total: null,
  expected_budget_total: null,
  expected_final_total: null,
  coverage_rate: null,
  budget_coverage_rate: null,
  final_coverage_rate: null,
  missing_expected_total: null,
};

function meta(extra: Record<string, unknown> = {}) {
  return {
    data_basis: "existing_slots_only",
    expected_materials_ready: false,
    generated_at: "2026-09-21T12:00:00Z",
    ...extra,
  };
}

const COVERAGE_BODY = {
  ok: true,
  data: {
    summary: {
      slot_total: 6,
      status_counts: STATUS_COUNTS,
      budget_total: 3,
      final_total: 2,
      unknown_kind_total: 1,
      due_at_unknown: 4,
      jurisdiction_unknown_total: 1,
      ...EXPECTED_NULLS,
    },
    districts: [
      {
        district_id: DISTRICT_ID,
        district_name: "普陀区",
        slot_total: 5,
        status_counts: STATUS_COUNTS,
        budget_total: 3,
        final_total: 2,
        unknown_kind_total: 0,
        due_at_unknown: 3,
        updated_at: "2026-09-21T11:00:00Z",
        ...EXPECTED_NULLS,
      },
    ],
  },
  meta: meta(),
};

const DISTRICT_BODY = {
  ok: true,
  data: {
    district: { district_id: DISTRICT_ID, district_name: "普陀区" },
    items: [
      {
        department_id: DEPT_ID,
        department_name: "上海市普陀区规划和自然资源局",
        subject_count: 3,
        slot_total: 5,
        status_counts: STATUS_COUNTS,
        budget: { slot_total: 3, status_counts: STATUS_COUNTS },
        final: { slot_total: 2, status_counts: STATUS_COUNTS },
        unknown_kind_total: 0,
        missing: 0,
        not_due: 0,
        due_at_unknown: 3,
        updated_at: "2026-09-21T11:00:00Z",
        ...EXPECTED_NULLS,
      },
    ],
  },
  meta: meta({
    pagination: { page: 1, page_size: 20, total: 1, total_pages: 1 },
  }),
};

function slot(overrides: Record<string, unknown>) {
  return {
    slot_id: "slot-x",
    slot_key: "key-x",
    jurisdiction_id: DISTRICT_ID,
    jurisdiction_name: "普陀区",
    department_org_id: DEPT_ID,
    department_name: "上海市普陀区规划和自然资源局",
    subject_org_id: "unit-x",
    subject_org_name: "某单位",
    subject_kind: "unit",
    material_scope: "unit_self",
    fiscal_year: 2025,
    report_kind: "budget",
    caliber: "self",
    caliber_conflict_candidate: null,
    status: "uploaded",
    status_reason: "awaiting_analysis",
    applicability_status: "applicable",
    applicability_note: null,
    due_at: null,
    current_document_version_id: 12,
    formal_issue_count: null,
    updated_at: "2026-09-21T11:30:00Z",
    ...overrides,
  };
}

const MATRIX_BODY = {
  ok: true,
  data: {
    department: {
      department_id: DEPT_ID,
      department_name: "上海市普陀区规划和自然资源局",
      jurisdiction_id: DISTRICT_ID,
      jurisdiction_name: "普陀区",
    },
    fiscal_year: 2025,
    groups: {
      department_summary: [
        {
          subject_org_id: DEPT_ID,
          subject_org_name: "上海市普陀区规划和自然资源局",
          subject_kind: "department",
          relationship: "department_summary",
          budget: { exists: true, slot: slot({ slot_id: "slot-summary-budget", status: "completed", status_reason: "review_completed" }) },
          final: { exists: false, slot: null },
          unclassified: { exists: false, slot: null },
        },
      ],
      head_unit: [
        {
          subject_org_id: HEAD_UNIT_ID,
          subject_org_name: "上海市普陀区规划和自然资源局本级",
          subject_kind: "unit",
          relationship: "head_unit",
          budget: { exists: false, slot: null },
          final: {
            exists: true,
            slot: slot({
              slot_id: "slot-head-final",
              subject_org_id: HEAD_UNIT_ID,
              report_kind: "final",
              status: "review_required",
              status_reason: "findings_pending",
            }),
          },
          unclassified: { exists: false, slot: null },
        },
      ],
      subordinate_units: [
        {
          subject_org_id: SUB_UNIT_ID,
          subject_org_name: "上海市普陀区规划和自然资源局执法大队",
          subject_kind: "unit",
          relationship: "subordinate_unit",
          budget: { exists: true, slot: slot({ slot_id: "slot-sub-budget", subject_org_id: SUB_UNIT_ID }) },
          final: { exists: false, slot: null },
          unclassified: {
            exists: true,
            slot: slot({
              slot_id: "slot-sub-unclassified",
              subject_org_id: SUB_UNIT_ID,
              report_kind: "unknown",
              status: "mapping_required",
              status_reason: "identity_unresolved",
            }),
          },
        },
      ],
      relationship_unknown: [],
    },
  },
  meta: meta(),
};

interface MockOptions {
  isAdmin?: boolean;
  coverage?: { status: number; body: unknown; delayMs?: number };
  district?: { status: number; body: unknown };
  matrix?: { status: number; body: unknown };
}

async function installMaterialMocks(page: Page, options: MockOptions = {}) {
  const coverage = options.coverage ?? { status: 200, body: COVERAGE_BODY };
  const district = options.district ?? { status: 200, body: DISTRICT_BODY };
  const matrix = options.matrix ?? { status: 200, body: MATRIX_BODY };

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: options.isAdmin ?? true } }),
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

    if (path === "/api/materials/coverage") {
      if (coverage.delayMs) {
        await new Promise((resolve) => setTimeout(resolve, coverage.delayMs));
      }
      await route.fulfill({
        status: coverage.status,
        contentType: "application/json",
        body: JSON.stringify(coverage.body),
      });
      return;
    }

    if (path === `/api/materials/districts/${DISTRICT_ID}/departments`) {
      await route.fulfill({
        status: district.status,
        contentType: "application/json",
        body: JSON.stringify(district.body),
      });
      return;
    }

    if (path === `/api/materials/departments/${DEPT_ID}/matrix`) {
      await route.fulfill({
        status: matrix.status,
        contentType: "application/json",
        body: JSON.stringify(matrix.body),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.beforeEach(async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
});

test.describe("材料台账首页（06）", () => {
  test("展示数据口径提示、状态 KPI 与区县卡片", async ({ page }) => {
    await installMaterialMocks(page);
    await page.goto("/materials");

    const notice = page.getByTestId("gbc-material-data-basis-notice");
    await expect(notice).toBeVisible();
    await expect(notice).toContainText("当前仅统计已建立的材料槽位");
    await expect(notice).toContainText("应收材料基线尚未导入");
    await expect(notice).toHaveAttribute("data-expected-materials-ready", "false");

    await expect(page.getByTestId("gbc-material-kpi-slot-total")).toContainText("6");
    // 待复核 = review_required(1) + reviewing(0)
    await expect(page.getByTestId("gbc-material-kpi-review")).toContainText("1");
    await expect(page.getByTestId("gbc-material-kpi-mapping")).toContainText("1");
    await expect(page.getByTestId("gbc-material-kpi-missing")).toContainText("0");

    const card = page.getByTestId(`gbc-material-district-card-${DISTRICT_ID}`);
    await expect(card).toBeVisible();
    await expect(card).toContainText("普陀区");
    await expect(card).toContainText("完整率");
    // 反例：无应收基线时完整率必须是 —，不能显示 0% 或任何百分比
    await expect(card).toContainText("—");
    await expect(card).not.toContainText("0%");
  });

  test("空态：没有槽位时显示空态与'不代表不存在应收材料'提示", async ({ page }) => {
    await installMaterialMocks(page, {
      coverage: {
        status: 200,
        body: {
          ok: true,
          data: {
            summary: {
              slot_total: 0,
              status_counts: Object.fromEntries(Object.keys(STATUS_COUNTS).map((key) => [key, 0])),
              budget_total: 0,
              final_total: 0,
              unknown_kind_total: 0,
              due_at_unknown: 0,
              jurisdiction_unknown_total: 0,
              ...EXPECTED_NULLS,
            },
            districts: [],
          },
          meta: meta(),
        },
      },
    });
    await page.goto("/materials");

    const empty = page.getByTestId("gbc-material-ledger-empty");
    await expect(empty).toBeVisible();
    await expect(empty).toContainText("当前筛选条件下暂无已建立的材料槽位");
    await expect(empty).toContainText("这不代表系统中不存在应收材料");
    await expect(page.getByTestId("gbc-material-kpi-slot-total")).toContainText("0");
  });

  test("错误态：后端 500 时显示失败与重试入口，不伪装成空台账", async ({ page }) => {
    await installMaterialMocks(page, {
      coverage: { status: 500, body: { detail: "material ledger database unavailable" } },
    });
    await page.goto("/materials");

    const error = page.getByTestId("gbc-material-ledger-error");
    await expect(error).toBeVisible();
    await expect(error).toContainText("material ledger database unavailable");
    await expect(page.getByTestId("gbc-material-ledger-retry")).toBeVisible();
    await expect(page.getByTestId("gbc-material-ledger-empty")).toHaveCount(0);
  });

  test("加载态：请求未返回时显示加载提示，不提前显示 0", async ({ page }) => {
    await installMaterialMocks(page, {
      coverage: { status: 200, body: COVERAGE_BODY, delayMs: 1500 },
    });
    await page.goto("/materials");

    await expect(page.getByTestId("gbc-material-ledger-loading")).toBeVisible();
    await expect(page.getByTestId("gbc-material-kpi-slot-total")).toHaveCount(0);
    await expect(page.getByTestId("gbc-material-district-cards")).toBeVisible({ timeout: 10_000 });
  });

  test("年度与文种筛选真的进入查询串", async ({ page }) => {
    const requested: string[] = [];
    await installMaterialMocks(page);
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/materials/coverage") {
        requested.push(url.search);
      }
    });

    await page.goto("/materials");
    await page.getByTestId("gbc-material-ledger-year-filter").fill("2025");
    await page.getByTestId("gbc-material-ledger-kind-filter").selectOption("final");

    await expect.poll(() => requested.some((search) => search.includes("fiscal_year=2025"))).toBe(true);
    await expect
      .poll(() => requested.some((search) => search.includes("report_kind=final")))
      .toBe(true);
  });

  test("非法年度不参与筛选，并给出提示", async ({ page }) => {
    const requested: string[] = [];
    await installMaterialMocks(page);
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname === "/api/materials/coverage") {
        requested.push(url.search);
      }
    });

    await page.goto("/materials");
    await page.getByTestId("gbc-material-ledger-year-filter").fill("25");

    await expect(page.getByTestId("gbc-material-ledger-year-hint")).toBeVisible();
    await expect
      .poll(() => requested.filter((search) => search.includes("fiscal_year")))
      .toEqual([]);
  });
});

test.describe("06 → 07 → 08 下钻链路", () => {
  test("首页卡片进入区级主管部门矩阵，再进入部门材料矩阵", async ({ page }) => {
    await installMaterialMocks(page);
    await page.goto("/materials");

    await page.getByTestId(`gbc-material-district-card-${DISTRICT_ID}`).click();
    await expect(page).toHaveURL(new RegExp(`/materials/district/${DISTRICT_ID}`));

    // 07：区级矩阵页面
    await expect(page.getByTestId("gbc-material-district-page")).toBeVisible();
    await expect(page.getByTestId("gbc-material-breadcrumb-current")).toHaveText("普陀区");
    const table = page.getByTestId("gbc-material-district-table");
    await expect(table).toContainText("完整率");

    const row = page.getByTestId(`gbc-material-district-row-${DEPT_ID}`);
    await expect(row).toContainText("上海市普陀区规划和自然资源局");
    // 反例：无应收基线时完整率必须是 —，不能显示 0% 或任何百分比
    await expect(row).toContainText("—");
    await expect(row).not.toContainText("0%");

    // 未选年度时不允许下钻（部门矩阵接口年度必填，页面不猜年度）
    await expect(page.getByTestId(`gbc-material-district-row-${DEPT_ID}-need-year`)).toBeVisible();
    await page.getByTestId("gbc-material-district-year-filter").fill("2025");

    await page.getByTestId(`gbc-material-district-row-${DEPT_ID}-open`).click();
    await expect(page).toHaveURL(new RegExp(`/materials/department/${DEPT_ID}\\?year=2025`));

    // 08：部门材料矩阵，三个分组都在
    await expect(page.getByTestId("gbc-material-department-page")).toBeVisible();
    await expect(page.getByTestId("gbc-material-group-department_summary")).toBeVisible();
    await expect(page.getByTestId("gbc-material-group-head_unit")).toBeVisible();
    await expect(page.getByTestId("gbc-material-group-subordinate_units")).toBeVisible();
    await expect(page.getByTestId("gbc-material-department-page")).toContainText("部门汇总");
    await expect(page.getByTestId("gbc-material-department-page")).toContainText("本部单位");
    await expect(page.getByTestId("gbc-material-department-page")).toContainText("直属单位");
    await expect(page.getByTestId("gbc-material-department-year")).toContainText("2025");
  });

  test("部门矩阵：预算与决算各自显示在正确的列，不串位", async ({ page }) => {
    await installMaterialMocks(page);
    await page.goto(`/materials/department/${DEPT_ID}?year=2025`);

    const summaryBudget = page.getByTestId(`gbc-material-slot-${DEPT_ID}-budget`);
    await expect(summaryBudget).toHaveAttribute("data-slot-exists", "true");
    await expect(summaryBudget).toHaveAttribute("data-slot-status", "completed");
    await expect(summaryBudget).toContainText("已完成");
    await expect(page.getByTestId(`gbc-material-slot-${DEPT_ID}-final`)).toHaveAttribute(
      "data-slot-exists",
      "false",
    );

    const headFinal = page.getByTestId(`gbc-material-slot-${HEAD_UNIT_ID}-final`);
    await expect(headFinal).toHaveAttribute("data-slot-exists", "true");
    await expect(headFinal).toContainText("待人工复核");
    await expect(page.getByTestId(`gbc-material-slot-${HEAD_UNIT_ID}-budget`)).toHaveAttribute(
      "data-slot-exists",
      "false",
    );
  });

  test("反例：没有槽位的单元格显示'尚无已建立材料'，绝不显示缺失/逾期/未上传", async ({ page }) => {
    await installMaterialMocks(page);
    await page.goto(`/materials/department/${DEPT_ID}?year=2025`);

    const absent = page.getByTestId(`gbc-material-slot-${DEPT_ID}-final`);
    await expect(absent).toHaveText("尚无已建立材料");
    await expect(absent).not.toContainText("缺失");
    await expect(absent).not.toContainText("逾期");
    await expect(absent).not.toContainText("未上传");
  });

  test("待确认归属的槽位同时显示状态原因，而不是只给一个黄色异常", async ({ page }) => {
    await installMaterialMocks(page);
    await page.goto(`/materials/department/${DEPT_ID}?year=2025`);

    const unclassified = page.getByTestId(`gbc-material-slot-${SUB_UNIT_ID}-unclassified`);
    await expect(unclassified).toContainText("待确认归属");
    await expect(unclassified.getByTestId(`gbc-material-slot-${SUB_UNIT_ID}-unclassified-reason`)).toHaveText(
      "年度/文种/主体映射待确认",
    );
  });

  test("缺财政年度时提示选择年度，且不向后端发矩阵请求", async ({ page }) => {
    const requested: string[] = [];
    await installMaterialMocks(page);
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (url.pathname.includes("/api/materials/departments/")) {
        requested.push(url.pathname + url.search);
      }
    });

    await page.goto(`/materials/department/${DEPT_ID}`);

    await expect(page.getByTestId("gbc-material-department-need-year")).toBeVisible();
    await expect(page.getByTestId("gbc-material-department-need-year")).toContainText("请先选择财政年度");
    expect(requested).toEqual([]);

    await page.getByTestId("gbc-material-department-year-filter").fill("2025");
    await expect(page.getByTestId("gbc-material-group-head_unit")).toBeVisible();
  });

  test("部门矩阵空态：该年度没有槽位时显示空态而不是别的年度的数据", async ({ page }) => {
    await installMaterialMocks(page, {
      matrix: {
        status: 200,
        body: {
          ok: true,
          data: {
            department: {
              department_id: DEPT_ID,
              department_name: "上海市普陀区规划和自然资源局",
              jurisdiction_id: DISTRICT_ID,
              jurisdiction_name: "普陀区",
            },
            fiscal_year: 2025,
            groups: {
              department_summary: [],
              head_unit: [],
              subordinate_units: [],
              relationship_unknown: [],
            },
          },
          meta: meta(),
        },
      },
    });
    await page.goto(`/materials/department/${DEPT_ID}?year=2025`);

    const empty = page.getByTestId("gbc-material-department-empty");
    await expect(empty).toBeVisible();
    await expect(empty).toContainText("当前筛选年度暂无已建立的材料槽位");
    await expect(empty).toContainText("这不代表该部门不存在应收材料");
  });

  test("区级矩阵空态与错误态可分", async ({ page }) => {
    await installMaterialMocks(page, {
      district: {
        status: 200,
        body: {
          ok: true,
          data: {
            district: { district_id: DISTRICT_ID, district_name: "普陀区" },
            items: [],
          },
          meta: meta({ pagination: { page: 1, page_size: 20, total: 0, total_pages: 0 } }),
        },
      },
    });
    await page.goto(`/materials/district/${DISTRICT_ID}`);

    await expect(page.getByTestId("gbc-material-district-empty")).toContainText("暂无已建立的材料槽位");

    await installMaterialMocks(page, {
      district: { status: 403, body: { detail: "jurisdiction access denied" } },
    });
    await page.goto(`/materials/district/${DISTRICT_ID}`);

    const error = page.getByTestId("gbc-material-district-error");
    await expect(error).toBeVisible();
    await expect(error).toContainText("jurisdiction access denied");
    await expect(page.getByTestId("gbc-material-district-empty")).toHaveCount(0);
  });

  test("面包屑可从部门矩阵逐级返回区级矩阵与首页", async ({ page }) => {
    await installMaterialMocks(page);
    await page.goto(`/materials/department/${DEPT_ID}?year=2025`);

    const breadcrumb = page.getByTestId("gbc-material-breadcrumb");
    await expect(breadcrumb).toContainText("材料台账");
    await expect(breadcrumb).toContainText("普陀区");
    await expect(breadcrumb).toContainText("上海市普陀区规划和自然资源局");

    await page.getByTestId("gbc-material-breadcrumb-link-0").click();
    await expect(page).toHaveURL(/\/materials$/);
  });
});
