import path from "node:path";

import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * WP2-A 材料台账三页的**可视化证据采集**（默认不跑，需显式开启）。
 *
 * 默认套件不执行的原因：它只产出截图，不做断言意义上的门禁；混在常规 e2e 里
 * 只会拉长每次跑测试的时间。开启方式::
 *
 *     GBC_CAPTURE_SCREENSHOTS=1 npm --prefix app run test:e2e -- material-ledger.screenshots
 *
 * 截图写入 `docs/wp2a-screenshots/`，供交付说明与人工复核引用。
 * 数据仍由本文件 mock：截图证明的是"给定后端响应，界面渲染成什么样"，
 * 不代表线上数据长这样——交付说明里会写明这一点。
 */

const CAPTURE_ENABLED = process.env.GBC_CAPTURE_SCREENSHOTS === "1";
// 绝对路径：相对路径会被 Playwright 按进程工作目录解析，实测落到了仓库外
// （跑在 e2e 配置目录时 `../../` 指到了别的盘符目录）。写死仓库内的绝对路径，
// 保证截图永远落在 docs/wp2a-screenshots/ 下。
const OUTPUT_DIR = path.resolve(__dirname, "..", "..", "docs", "wp2a-screenshots");

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
  not_due: 1,
  missing: 2,
  uploaded: 3,
  processing: 1,
  review_required: 4,
  reviewing: 1,
  completed: 2,
  not_applicable: 1,
  mapping_required: 5,
  failed: 1,
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
      slot_total: 41,
      status_counts: STATUS_COUNTS,
      budget_total: 20,
      final_total: 19,
      unknown_kind_total: 2,
      due_at_unknown: 35,
      jurisdiction_unknown_total: 2,
      ...EXPECTED_NULLS,
    },
    districts: [
      {
        district_id: DISTRICT_ID,
        district_name: "上海市普陀区",
        slot_total: 24,
        status_counts: STATUS_COUNTS,
        budget_total: 12,
        final_total: 12,
        unknown_kind_total: 0,
        due_at_unknown: 20,
        updated_at: "2026-09-21T11:42:00Z",
        ...EXPECTED_NULLS,
      },
      {
        district_id: "district-ja",
        district_name: "上海市静安区",
        slot_total: 15,
        status_counts: STATUS_COUNTS,
        budget_total: 8,
        final_total: 7,
        unknown_kind_total: 0,
        due_at_unknown: 13,
        updated_at: "2026-09-21T10:05:00Z",
        ...EXPECTED_NULLS,
      },
    ],
  },
  meta: meta(),
};

const DISTRICT_BODY = {
  ok: true,
  data: {
    district: { district_id: DISTRICT_ID, district_name: "上海市普陀区" },
    items: [
      {
        department_id: DEPT_ID,
        department_name: "上海市普陀区规划和自然资源局",
        subject_count: 12,
        slot_total: 20,
        status_counts: STATUS_COUNTS,
        budget: { slot_total: 10, status_counts: STATUS_COUNTS },
        final: { slot_total: 10, status_counts: STATUS_COUNTS },
        unknown_kind_total: 0,
        missing: 2,
        not_due: 1,
        due_at_unknown: 15,
        updated_at: "2026-09-21T11:42:00Z",
        ...EXPECTED_NULLS,
      },
      {
        department_id: "dept-civil",
        department_name: "上海市普陀区民政局",
        subject_count: 5,
        slot_total: 4,
        status_counts: STATUS_COUNTS,
        budget: { slot_total: 2, status_counts: STATUS_COUNTS },
        final: { slot_total: 2, status_counts: STATUS_COUNTS },
        unknown_kind_total: 0,
        missing: 0,
        not_due: 0,
        due_at_unknown: 3,
        updated_at: "2026-09-20T16:20:00Z",
        ...EXPECTED_NULLS,
      },
    ],
  },
  meta: meta({ pagination: { page: 1, page_size: 20, total: 2, total_pages: 1 } }),
};

function slot(overrides: Record<string, unknown>) {
  return {
    slot_id: "slot-x",
    slot_key: "key-x",
    jurisdiction_id: DISTRICT_ID,
    jurisdiction_name: "上海市普陀区",
    department_org_id: DEPT_ID,
    department_name: "上海市普陀区规划和自然资源局",
    subject_org_id: SUB_UNIT_ID,
    subject_org_name: "上海市普陀区规划和自然资源局执法大队",
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
    current_document_version_id: 123,
    formal_issue_count: null,
    updated_at: "2026-09-21T11:42:00Z",
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
      jurisdiction_name: "上海市普陀区",
    },
    fiscal_year: 2025,
    groups: {
      department_summary: [
        {
          subject_org_id: DEPT_ID,
          subject_org_name: "上海市普陀区规划和自然资源局",
          subject_kind: "department",
          relationship: "department_summary",
          budget: {
            exists: true,
            slot: slot({ slot_id: "slot-summary-b", status: "completed", status_reason: "review_completed", caliber: "summary" }),
          },
          final: {
            exists: true,
            slot: slot({
              slot_id: "slot-summary-f",
              report_kind: "final",
              status: "mapping_required",
              status_reason: "caliber_conflict",
              caliber: "summary",
              caliber_conflict_candidate: "self",
            }),
          },
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
              slot_id: "slot-head-f",
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
          budget: { exists: true, slot: slot({ slot_id: "slot-sub-b" }) },
          final: { exists: false, slot: null },
          unclassified: {
            exists: true,
            slot: slot({
              slot_id: "slot-sub-u",
              report_kind: "unknown",
              status: "mapping_required",
              status_reason: "identity_unresolved",
            }),
          },
        },
        {
          subject_org_id: "unit-ghzy-affairs",
          subject_org_name: "上海市普陀区规划和自然资源管理事务中心",
          subject_kind: "unit",
          relationship: "subordinate_unit",
          budget: { exists: true, slot: slot({ slot_id: "slot-affairs-b", status: "processing", status_reason: "analysis_running" }) },
          final: { exists: true, slot: slot({ slot_id: "slot-affairs-f", report_kind: "final", status: "not_due", status_reason: "due_at_unknown" }) },
          unclassified: { exists: false, slot: null },
        },
      ],
      relationship_unknown: [],
    },
  },
  meta: meta(),
};

async function installMocks(page: Page): Promise<void> {
  await page.route("**/api/**", async (route) => {
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
    if (path === "/api/materials/coverage") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(COVERAGE_BODY),
      });
      return;
    }
    if (path === `/api/materials/districts/${DISTRICT_ID}/departments`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(DISTRICT_BODY),
      });
      return;
    }
    if (path === `/api/materials/departments/${DEPT_ID}/matrix`) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MATRIX_BODY),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("WP2-A 截图采集（默认跳过）", () => {
  test.skip(!CAPTURE_ENABLED, "设置 GBC_CAPTURE_SCREENSHOTS=1 才采集截图");

  test.beforeEach(async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    // 1920 宽（常见办公显示器）：区级矩阵是 11 列的宽表，1600 宽时右侧
    // 「更新时间/操作」会落进横向滚动区，截图就看不全了。
    await page.setViewportSize({ width: 1920, height: 1080 });
    await installMocks(page);
  });

  test("01 材料台账首页", async ({ page }) => {
    await page.goto("/materials");
    await expect(page.getByTestId("gbc-material-district-cards")).toBeVisible();
    await page.screenshot({ path: `${OUTPUT_DIR}/01-ledger-home.png`, fullPage: true });
  });

  test("02 区级主管部门矩阵", async ({ page }) => {
    await page.goto(`/materials/district/${DISTRICT_ID}?year=2025`);
    await expect(page.getByTestId("gbc-material-district-table")).toBeVisible();
    await page.screenshot({ path: `${OUTPUT_DIR}/02-district-matrix.png`, fullPage: true });
  });

  test("03 部门材料矩阵（三分组）", async ({ page }) => {
    await page.goto(`/materials/department/${DEPT_ID}?year=2025`);
    await expect(page.getByTestId("gbc-material-group-subordinate_units")).toBeVisible();
    await page.screenshot({ path: `${OUTPUT_DIR}/03-department-matrix.png`, fullPage: true });
  });

  test("04 首页空态（无槽位 + 完整率 —）", async ({ page }) => {
    await page.route("**/api/materials/coverage", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          ok: true,
          data: {
            summary: {
              slot_total: 0,
              status_counts: Object.fromEntries(
                Object.keys(STATUS_COUNTS).map((key) => [key, 0]),
              ),
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
        }),
      });
    });
    await page.goto("/materials");
    await expect(page.getByTestId("gbc-material-ledger-empty")).toBeVisible();
    await page.screenshot({ path: `${OUTPUT_DIR}/04-ledger-empty.png`, fullPage: true });
  });

  test("05 部门矩阵缺年度提示", async ({ page }) => {
    await page.goto(`/materials/department/${DEPT_ID}`);
    await expect(page.getByTestId("gbc-material-department-need-year")).toBeVisible();
    await page.screenshot({ path: `${OUTPUT_DIR}/05-department-need-year.png`, fullPage: true });
  });
});
