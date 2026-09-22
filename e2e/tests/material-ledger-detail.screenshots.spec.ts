import path from "node:path";

import { expect, test } from "../../app/node_modules/playwright/test";

import {
  COVERAGE_UNAVAILABLE,
  DETAIL_BODY,
  HEAD_UNIT_ID,
  RUNS_BODY,
  SLOT_MAIN,
  SLOT_MISSING,
  VERSIONS_BODY,
  installDetailMocks,
  meta,
  sessionCookie,
} from "./materialLedgerDetailFixtures";

/**
 * WP2-B（单位时间轴 + 材料详情）的**可视化证据采集**（默认不跑，需显式开启）。
 *
 * 默认套件不执行的原因与 WP2-A 相同：它只产出截图，不做门禁意义上的断言，
 * 混在常规 e2e 里只会拉长每次跑测试的时间。开启方式::
 *
 *     GBC_CAPTURE_SCREENSHOTS=1 npm --prefix app run test:e2e -- material-ledger-detail.screenshots
 *
 * 截图写入 `docs/wp2b-screenshots/`，供交付说明与人工复核引用。
 * 数据仍由 fixtures 提供：**截图证明的是界面在给定后端响应下渲染成什么样，
 * 不代表线上数据库的真实数据**——交付文档里会写明这一点。
 */

const CAPTURE_ENABLED = process.env.GBC_CAPTURE_SCREENSHOTS === "1";
// 绝对路径：相对路径会被 Playwright 按进程工作目录解析（WP2-A 实测落到过仓库外），
// 写死仓库内路径保证截图永远落在 docs/wp2b-screenshots/ 下。
const OUTPUT_DIR = path.resolve(__dirname, "..", "..", "docs", "wp2b-screenshots");

test.describe("WP2-B 截图采集（默认跳过）", () => {
  test.skip(!CAPTURE_ENABLED, "设置 GBC_CAPTURE_SCREENSHOTS=1 才采集截图");

  test.beforeEach(async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    // 1920 宽（常见办公显示器）：时间轴与详情页都是宽表，1600 宽时右侧会落进横向
    // 滚动区，截图就看不全了。
    await page.setViewportSize({ width: 1920, height: 1080 });
    await installDetailMocks(page);
  });

  test("WP2B_01 单位多年度时间轴", async ({ page }) => {
    await page.goto(`/materials/unit/${HEAD_UNIT_ID}`);
    await expect(page.getByTestId("gbc-material-timeline-table")).toBeVisible();
    await expect(page.getByTestId("gbc-material-timeline-year-2024")).toBeVisible();
    await page.screenshot({
      path: `${OUTPUT_DIR}/WP2B_01_unit_multiyear_timeline.png`,
      fullPage: true,
    });
  });

  test("WP2B_02 材料详情概览", async ({ page }) => {
    await page.goto(`/materials/slots/${SLOT_MAIN}`);
    await expect(page.getByTestId("gbc-material-overview-formal-count")).toContainText("2");
    await page.screenshot({
      path: `${OUTPUT_DIR}/WP2B_02_material_detail_overview.png`,
      fullPage: true,
    });
  });

  test("WP2B_03 检查覆盖", async ({ page }) => {
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=coverage`);
    await expect(page.getByTestId("gbc-material-coverage-kpi-blocking")).toBeVisible();
    await page.screenshot({
      path: `${OUTPUT_DIR}/WP2B_03_material_detail_coverage.png`,
      fullPage: true,
    });
  });

  test("WP2B_04 版本与来源", async ({ page }) => {
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=versions`);
    await expect(page.getByTestId("gbc-material-version-22")).toBeVisible();
    await expect(page.getByTestId("gbc-material-source-1")).toBeVisible();
    await page.screenshot({
      path: `${OUTPUT_DIR}/WP2B_04_material_detail_versions_source.png`,
      fullPage: true,
    });
  });

  test("WP2B_05 缺失态材料详情", async ({ page }) => {
    await page.goto(`/materials/slots/${SLOT_MISSING}`);
    await expect(page.getByTestId("gbc-material-overview-state-detail")).toBeVisible();
    await page.screenshot({
      path: `${OUTPUT_DIR}/WP2B_05_material_slot_missing_state.png`,
      fullPage: true,
    });
  });

  test("WP2B_06 处理记录", async ({ page }) => {
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=runs`);
    await expect(page.getByTestId("gbc-material-runs-current-table")).toBeVisible();
    await expect(page.getByTestId("gbc-material-runs-history-table")).toBeVisible();
    await page.screenshot({
      path: `${OUTPUT_DIR}/WP2B_06_material_processing_runs.png`,
      fullPage: true,
    });
  });

  test("WP2B_07 无当前分析结果（版本替换后）", async ({ page }) => {
    // 额外一张：版本替换后"不能显示旧结论"的形态，是本次最容易被误读的口径，
    // 交付文档会引用它说明"算不出来"与"没问题"的区别。
    await page.goto("/materials/slots/slot-enforcement-final-2024");
    await expect(page.getByTestId("gbc-material-overview-analysis-unavailable")).toBeVisible();
    await page.screenshot({
      path: `${OUTPUT_DIR}/WP2B_07_no_current_analysis.png`,
      fullPage: true,
    });
  });
});

// 引用一下未在截图用例里直接用到的常量，避免 ESLint 未使用导入告警
// （fixtures 是共享模块，保持完整导入面便于后续新增用例）。
test.describe("fixtures 自检（默认跳过）", () => {
  test.skip(!CAPTURE_ENABLED, "截图模式才校验夹具完整性");
  test("夹具里必须有可用的详情/版本/运行/缺失数据", () => {
    expect(DETAIL_BODY.data.current_analysis.available).toBe(true);
    expect(VERSIONS_BODY.data.items.length).toBeGreaterThan(1);
    expect(RUNS_BODY.data.items.length).toBeGreaterThan(1);
    expect(COVERAGE_UNAVAILABLE.available).toBe(false);
    expect(meta().linkage_basis).toBe("structured_document_version_id");
  });
});
