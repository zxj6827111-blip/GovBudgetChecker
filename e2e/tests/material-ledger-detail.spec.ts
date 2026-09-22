import { expect, test } from "../../app/node_modules/playwright/test";

import {
  DEPT_ID,
  DETAIL_VERSION_REPLACED_BODY,
  DETAIL_BODY,
  HEAD_UNIT_ID,
  RUNS_BODY,
  SLOT_DUE_UNKNOWN,
  SLOT_MAIN,
  SLOT_MISSING,
  SLOT_MULTI_EXTRA,
  SLOT_MULTI_PRIMARY,
  SLOT_VERSION_REPLACED,
  SLOT_YEAR_UNKNOWN,
  SUB_UNIT_ID,
  UNIT_NAME,
  installDetailMocks,
  sessionCookie,
} from "./materialLedgerDetailFixtures";

/**
 * WP2-B：单位多年度时间轴（09）+ 材料详情（10-13）e2e 验证。
 *
 * 覆盖范围
 * --------
 * 1. 主链 08 → 09 → 10 → 11 → 12：部门矩阵点单位 → 单位时间轴点 Slot →
 *    材料详情 → 检查覆盖 Tab → 版本与来源 Tab；
 * 2. 反例（红线）：真实 missing 槽位显示"逾期未上传 / 当前无有效文件版本"，
 *    **不**出现"从未上传"；
 * 3. 反例（红线）：`not_due + due_at_unknown` 显示"截止时间未知，当前无法判断
 *    是否逾期"，**不**显示"缺失"；
 * 4. 反例（红线）：**版本替换** —— V1 有 3 个 finding、V2 是当前版本且没有分析 →
 *    详情不得显示 V1 的问题数（既不显示 3，也不显示 0），版本 Tab 区分
 *    当前/历史，处理记录把 V1 标成历史文件版本；
 * 5. 时间轴按财政年度排列、年度未识别单列、同一年度多条槽位不隐藏。
 *
 * 所有数据由 fixtures 提供（后端不参与）：用例校验的是"给定后端响应，页面显示
 * 是否正确"。后端契约由 tests/test_material_ledger_detail_api.py 覆盖。
 */

test.beforeEach(async ({ page }) => {
  await page.context().addCookies([sessionCookie]);
});

test.describe("WP2-B 主链：08 → 09 → 10 → 11 → 12", () => {
  test("部门矩阵 → 单位时间轴 → 材料详情 → 检查覆盖 → 版本与来源", async ({ page }) => {
    await installDetailMocks(page);

    // 08：部门矩阵里单位主体名称可点进单位时间轴
    await page.goto(`/materials/department/${DEPT_ID}?year=2024`);
    await expect(page.getByTestId("gbc-material-department-page")).toBeVisible();
    const unitLink = page.getByTestId(`gbc-material-subject-${HEAD_UNIT_ID}-unit-link`);
    await expect(unitLink).toBeVisible();
    await unitLink.click();

    // 09：单位多年度时间轴
    await expect(page).toHaveURL(new RegExp(`/materials/unit/${HEAD_UNIT_ID}$`));
    await expect(page.getByTestId("gbc-material-unit-page")).toBeVisible();
    await expect(page.getByTestId("gbc-material-timeline-year-2024")).toBeVisible();
    await expect(page.getByTestId("gbc-material-timeline-year-2026")).toBeVisible();
    await expect(page.getByTestId("gbc-material-unit-page")).toContainText(UNIT_NAME);

    // 09 → 10：点一个 Slot 卡进详情
    const slotCard = page.getByTestId("gbc-material-timeline-final-2024-primary");
    await expect(slotCard).toHaveAttribute("data-slot-exists", "true");
    await slotCard.click();

    // 10：材料详情概览
    await expect(page).toHaveURL(new RegExp(`/materials/slots/${SLOT_MAIN}`));
    await expect(page.getByTestId("gbc-material-detail-page")).toBeVisible();
    await expect(page.getByTestId("gbc-material-detail-tab-overview")).toHaveAttribute(
      "data-tab-active",
      "true",
    );
    // 3 = 2 条 error/warn 正式 finding + 1 条 info 正式 finding（info 也计入权威口径）
    await expect(page.getByTestId("gbc-material-overview-formal-count")).toContainText("3");

    // 11：检查覆盖 Tab
    await page.getByTestId("gbc-material-detail-tab-coverage").click();
    await expect(page.getByTestId("gbc-material-detail-panel-coverage")).toBeVisible();
    await expect(page.getByTestId("gbc-material-coverage-kpi-blocking")).toContainText("15");
    await expect(page.getByTestId("gbc-material-coverage-group-table_internal")).toBeVisible();
    await expect(page.getByTestId("gbc-material-coverage-group-table_internal-progress")).toContainText(
      "6 / 7",
    );

    // 12：版本与来源 Tab
    await page.getByTestId("gbc-material-detail-tab-versions").click();
    await expect(page.getByTestId("gbc-material-version-22")).toBeVisible();
    await expect(page.getByTestId("gbc-material-version-22-badge")).toContainText("当前版本");
    await expect(page.getByTestId("gbc-material-version-11-badge")).toContainText("历史版本");
    await expect(page.getByTestId("gbc-material-source-0-kind")).toContainText("官网公开来源");

    // 处理记录 Tab
    await page.getByTestId("gbc-material-detail-tab-runs").click();
    await expect(page.getByTestId("gbc-material-runs-current-table")).toBeVisible();
    await expect(page.getByTestId("gbc-material-runs-history-table")).toBeVisible();

    // 检查结果 Tab：三栏分栏
    await page.getByTestId("gbc-material-detail-tab-findings").click();
    await expect(page.getByTestId("gbc-material-findings-section-formal")).toBeVisible();
    await expect(page.getByTestId("gbc-material-findings-section-manual_review")).toBeVisible();
  });

  test("详情 Tab 可以用 ?tab= 直接落到指定分区", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=coverage`);
    await expect(page.getByTestId("gbc-material-detail-tab-coverage")).toHaveAttribute(
      "data-tab-active",
      "true",
    );
    await expect(page.getByTestId("gbc-material-detail-panel-coverage")).toBeVisible();

    // 未知 tab 回落到概览，而不是空白
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=does-not-exist`);
    await expect(page.getByTestId("gbc-material-detail-panel-overview")).toBeVisible();
  });
});

test.describe("检查结果：计数口径与展示分组是两件事", () => {
  test("顶部「正式检查记录」= 正式问题栏 + 信息提示栏（分组只是展示拆分）", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=findings`);
    // 先等三栏都渲染出来再计数：count() 不重试，数据未到位时会读到 0，
    // 失败信息看起来像"分组错了"，其实是时序问题。
    await expect(page.getByTestId("gbc-material-findings-section-formal")).toBeVisible();
    await expect(page.getByTestId("gbc-material-findings-section-info")).toBeVisible();
    await expect(page.getByTestId("gbc-material-findings-section-manual_review")).toBeVisible();

    // 总计取后端权威口径 count_formal_findings：error/warn/info 都算正式 finding
    await expect(page.getByTestId("gbc-material-findings-formal-count")).toContainText("3");
    await expect(page.getByTestId("gbc-material-findings-formal-count-note")).toContainText(
      "包含正式问题与信息提示",
    );

    // 用 data-finding-id 计数：卡片内部还有 severity/rule/evidence 等细粒度
    // testid 也带 -item-N 前缀，按前缀数会一条当六条（12 而不是 2）。
    const formalItems = await page
      .locator('[data-testid="gbc-material-findings-section-formal"] [data-finding-id]')
      .count();
    const infoItems = await page
      .locator('[data-testid="gbc-material-findings-section-info"] [data-finding-id]')
      .count();
    const manualItems = await page
      .locator('[data-testid="gbc-material-findings-section-manual_review"] [data-finding-id]')
      .count();

    expect(formalItems).toBe(2);
    expect(infoItems).toBe(1);
    expect(manualItems).toBe(1);
    // 恒等式：总计 = 正式问题 + 信息提示（降级待核验项不计入）
    expect(formalItems + infoItems).toBe(3);

    // 三个分组同时存在，且降级项不在正式分组里
    await expect(page.getByTestId("gbc-material-findings-section-formal")).toBeVisible();
    await expect(page.getByTestId("gbc-material-findings-section-info")).toBeVisible();
    await expect(page.getByTestId("gbc-material-findings-section-manual_review")).toBeVisible();
    const formalSectionText = await page
      .getByTestId("gbc-material-findings-section-formal")
      .innerText();
    expect(formalSectionText).not.toContain("缺证据的候选问题");
  });
});

test.describe("09 单位时间轴：年度口径", () => {
  test("按财政年度排序列出预算/决算，年度未识别单列", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/unit/${HEAD_UNIT_ID}`);
    // 先等表格真的渲染出来再读文本：直接 allInnerTexts() 会在数据到位前
    // 读到空数组，断言失败的原因看起来像"年度没排序"，其实是时序问题。
    await expect(page.getByTestId("gbc-material-timeline-table")).toBeVisible();

    const yearLabels = await page
      .locator('[data-testid^="gbc-material-timeline-year-label-"]')
      .allInnerTexts();
    expect(yearLabels).toEqual(["2026", "2025", "2024"]);

    // 预算/决算不串位
    await expect(page.getByTestId("gbc-material-timeline-budget-2024-primary")).toHaveAttribute(
      "data-slot-id",
      SLOT_MULTI_PRIMARY,
    );
    await expect(page.getByTestId("gbc-material-timeline-final-2024-primary")).toHaveAttribute(
      "data-slot-id",
      SLOT_MAIN,
    );

    // 年度未识别的槽位单独一块，不塞进任何具体年份
    await expect(page.getByTestId("gbc-material-timeline-unresolved")).toContainText("年度待确认");
    await expect(
      page.getByTestId(`gbc-material-timeline-unresolved-${SLOT_YEAR_UNKNOWN}`),
    ).toBeVisible();
    for (const year of ["2024", "2025", "2026"]) {
      await expect(page.getByTestId(`gbc-material-timeline-year-${year}`)).not.toContainText(
        SLOT_YEAR_UNKNOWN,
      );
    }
  });

  test("同一年度同一文种有多条槽位时逐条可见，不只显示数量", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/unit/${HEAD_UNIT_ID}`);
    const cell = page.getByTestId("gbc-material-timeline-budget-2024");
    await expect(cell).toHaveAttribute("data-slot-total", "2");
    await expect(page.getByTestId("gbc-material-timeline-budget-2024-extra-summary")).toContainText(
      "另有 1 个待确认槽位",
    );
    // 展开后逐条列出（不是只给一句提示）
    await page.getByTestId("gbc-material-timeline-budget-2024-extra-summary").click();
    await expect(
      page.getByTestId(`gbc-material-timeline-budget-2024-extra-${SLOT_MULTI_EXTRA}`),
    ).toBeVisible();
  });

  test("没有槽位的格子只写「尚无已建立材料」，不出现缺失/逾期/未上传", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/unit/${HEAD_UNIT_ID}`);
    const emptyCell = page.getByTestId("gbc-material-timeline-final-2026");
    await expect(emptyCell).toHaveAttribute("data-slot-exists", "false");
    await expect(emptyCell).toContainText("尚无已建立材料");

    // 负向断言锚在**数据区**：页脚的状态图例会列出「逾期未上传」这个状态名，
    // 那是"系统有哪些状态"的说明，不是对某个格子的结论。
    const tableText = await page.getByTestId("gbc-material-timeline-table").innerText();
    expect(tableText).not.toContain("逾期未上传");
    expect(tableText).not.toContain("未上传");
    // 也不显示"年度完整"这种需要应收基线才能得出的结论
    expect(tableText).not.toContain("完整");
  });
});

test.describe("反例：缺失态与截止时间未知", () => {
  test("真实 missing：显示逾期未上传 + 当前无有效文件版本，且不写「从未上传」", async ({
    page,
  }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/slots/${SLOT_MISSING}`);

    await expect(page.getByTestId("gbc-material-detail-header")).toContainText("逾期未上传");
    await expect(page.getByTestId("gbc-material-overview-state-detail")).toContainText(
      "当前无有效文件版本",
    );
    const overviewText = await page.getByTestId("gbc-material-detail-panel-overview").innerText();
    expect(overviewText).not.toContain("从未上传");

    // 没有当前版本 → 分析不可用，问题数不显示 0
    await expect(page.getByTestId("gbc-material-overview-formal-count")).toContainText("—");
    await expect(page.getByTestId("gbc-material-overview-analysis-unavailable")).toContainText(
      "可确认的分析结果",
    );
    await expect(page.getByTestId("gbc-material-overview-analysis-unavailable")).toContainText(
      "没有有效文件版本",
    );
    expect(overviewText).not.toContain("暂无问题");
    expect(overviewText).not.toContain("没有问题");

    // 版本历史仍然显示这一条历史版本（所以不能说"从未上传"）
    await page.getByTestId("gbc-material-detail-tab-versions").click();
    await expect(page.getByTestId("gbc-material-version-33-badge")).toContainText("历史版本");
  });

  test("due_at_unknown：显示截止时间未知，绝不显示缺失", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/slots/${SLOT_DUE_UNKNOWN}`);

    await expect(page.getByTestId("gbc-material-detail-due-unknown")).toContainText(
      "截止时间未知，当前无法判断是否逾期",
    );
    // 头部不能出现缺失结论（"逾期未上传"是唯一允许的缺失态文案）
    const headerText = await page.getByTestId("gbc-material-detail-header").innerText();
    expect(headerText).not.toContain("逾期未上传");
    expect(headerText).not.toContain("缺失");
    // 缺失态说明块根本不渲染
    await expect(page.getByTestId("gbc-material-overview-missing-note")).toHaveCount(0);
    // 状态仍是 not_due，且没有截止时间
    await expect(page.getByTestId("gbc-material-detail-due")).toContainText("未登记");
  });
});

test.describe("反例：版本替换后历史版本不冒充当前结果", () => {
  test("V2 当前且无分析：详情不显示 V1 的 finding 数，版本/运行标明历史", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/slots/${SLOT_VERSION_REPLACED}`);

    const header = page.getByTestId("gbc-material-detail-header");
    await expect(header).toContainText("v42");
    await expect(header).toContainText("当前文件版本：v42");

    // 关键：不能显示 V1 的 3 个 finding，也不能显示 0
    await expect(page.getByTestId("gbc-material-overview-formal-count")).toContainText("—");
    // 这条材料**有**当前版本，只是这一版还没分析 —— 文案必须说清是"这一版还没结果"，
    // 而不是"没有文件版本"（两者对用户的下一步动作完全不同）。
    await expect(page.getByTestId("gbc-material-overview-analysis-unavailable")).toContainText(
      "当前文件版本暂无可确认的分析结果",
    );
    const overviewText = await page.getByTestId("gbc-material-detail-panel-overview").innerText();
    expect(overviewText).not.toContain("正式问题 3");
    expect(overviewText).not.toContain("V1 的问题");

    // 版本 Tab：V42 当前、V11 历史
    await page.getByTestId("gbc-material-detail-tab-versions").click();
    await expect(page.getByTestId("gbc-material-version-42-badge")).toContainText("当前版本");
    await expect(page.getByTestId("gbc-material-version-11-badge")).toContainText("历史版本");

    // 处理记录：V11 的运行出现在"历史文件版本"区块
    await page.getByTestId("gbc-material-detail-tab-runs").click();
    const historyRow = page.getByTestId("gbc-material-runs-history-table-row-0");
    await expect(historyRow).toHaveAttribute("data-current-document-version", "false");
    await expect(historyRow).toContainText("历史文件版本");
    await expect(page.getByTestId("gbc-material-runs-current-table-empty")).toBeVisible();
  });

  test("检查结果 Tab 在无当前分析时给「暂无可确认的分析结果」而不是「暂无问题」", async ({
    page,
  }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/slots/${SLOT_VERSION_REPLACED}?tab=findings`);
    await expect(page.getByTestId("gbc-material-findings-unavailable")).toContainText(
      "暂无可确认的分析结果",
    );
    const text = await page.getByTestId("gbc-material-detail-panel-findings").innerText();
    expect(text).not.toContain("暂无问题");
    expect(text).not.toContain("没有问题");
  });
});

test.describe("检查覆盖：不可用时不冒充 100%", () => {
  test("没有覆盖记录时不给任何数字", async ({ page }) => {
    await installDetailMocks(page, {
      detail: {
        ok: true,
        data: { ...DETAIL_BODY.data, current_analysis: DETAIL_VERSION_REPLACED_BODY.data.current_analysis },
        meta: DETAIL_BODY.meta,
      },
    });
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=coverage`);
    await expect(page.getByTestId("gbc-material-coverage-unavailable")).toContainText(
      "当前文件版本暂无可确认的检查覆盖记录",
    );
    const text = await page.getByTestId("gbc-material-detail-panel-coverage").innerText();
    expect(text).not.toContain("100%");
    expect(text).not.toContain("0 / 0");
    expect(text).not.toContain("全部通过");
  });
});

test.describe("材料来源：人工上传无 URL 是正常形态", () => {
  test("人工上传显示「人工上传」，不显示「来源缺失」", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=versions`);

    const manualSource = page.getByTestId("gbc-material-source-1");
    await expect(manualSource).toHaveAttribute("data-source-kind", "manual_upload");
    await expect(page.getByTestId("gbc-material-source-1-kind")).toContainText("人工上传");
    await expect(page.getByTestId("gbc-material-source-1-url")).toContainText("人工上传来源本就没有 URL");

    // 官网来源单独显示发布日期
    await expect(page.getByTestId("gbc-material-source-0-published")).toContainText("2025-08-20");
    const text = await page.getByTestId("gbc-material-detail-panel-versions").innerText();
    expect(text).not.toContain("来源缺失");
  });
});

test.describe("处理记录：失败运行只给固定安全文案", () => {
  test("原始 error_message 含路径/连接串时，页面只显示固定文案", async ({ page }) => {
    await installDetailMocks(page, {
      runs: {
        ok: true,
        data: {
          slot_id: SLOT_MAIN,
          items: [
            {
              ...RUNS_BODY.data.items[0],
              status: "error",
              error_summary: "处理失败（详情见任务日志）",
              // 这里刻意不把原文放进 mock：后端已经换掉了。
              // 这个用例校验的是"页面不会自己拼原文、也不会多给一个查看日志入口"。
            },
          ],
        },
        meta: RUNS_BODY.meta,
      },
    });
    await page.goto(`/materials/slots/${SLOT_MAIN}?tab=runs`);

    await expect(page.getByTestId("gbc-material-runs-current-table-row-0-status")).toContainText(
      "处理失败",
    );
    await expect(page.getByTestId("gbc-material-runs-current-table-row-0-error")).toContainText(
      "处理失败（详情见任务日志）",
    );

    const panelText = await page.getByTestId("gbc-material-detail-panel-runs").innerText();
    // 本轮不新增"查看日志"入口：日志权限属于后续运维/权限设计
    expect(panelText).not.toContain("查看日志");
    expect(panelText).not.toContain("secret");
    expect(panelText).not.toContain("password");
  });
});

test.describe("受限授权与错误态", () => {
  test("403 时显示失败与重试入口，不伪装成空态", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ user: { username: "e2e-user", is_admin: false } }),
        });
        return;
      }
      if (url.pathname.startsWith("/api/materials/slots/")) {
        await route.fulfill({
          status: 403,
          contentType: "application/json",
          body: JSON.stringify({ detail: "slot access denied" }),
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });

    await page.goto(`/materials/slots/${SLOT_MAIN}`);
    await expect(page.getByTestId("gbc-material-detail-error")).toBeVisible();
    await expect(page.getByTestId("gbc-material-detail-retry")).toBeVisible();
  });

  test("部门矩阵的部门汇总主体不链到单位时间轴", async ({ page }) => {
    await installDetailMocks(page);
    await page.goto(`/materials/department/${DEPT_ID}?year=2024`);
    // 部门汇总那一行的主体名不是链接（按单位去查会得到一个空页面）
    await expect(page.getByTestId(`gbc-material-subject-${DEPT_ID}-unit-link`)).toHaveCount(0);
    // 单位主体才是链接
    await expect(page.getByTestId(`gbc-material-subject-${SUB_UNIT_ID}-unit-link`)).toBeVisible();
  });
});
