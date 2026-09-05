import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 6：审核工作台三栏布局 e2e 验证。
 *
 * 覆盖范围（对照任务书 Task 6 测试要求）：
 * 1. 打开任务 → 选中问题 → 跳页 → 确认 → 计数变化 → 刷新仍在（核心闭环）；
 * 2. 反例：年份未识别不出现 2000；evidence_status=degraded 必须出现降级标识；
 *    进度未知显示"—"；
 * 3. 反例：review_required 状态徽章不得显示成"分析完成"；
 * 4. 三 tab 切换与计数准确性（问题数与后端 count_formal_findings 同口径）。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const PNG_1X1_BASE64 =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=";

const JOB_ID = "job-review-workbench-2026";

interface MockOptions {
  status?: string;
  qualityStatus?: string;
  reportYear?: number | null;
  totalPages?: number;
  workflowUpdates: Array<Record<string, unknown>>;
  workflowIssuesState: Record<string, { issue_id: string; job_id: string; status: string; note?: string | null }>;
  ruleFindings?: Array<Record<string, unknown>>;
}

function buildJobDetail(options: MockOptions) {
  return {
    job_id: JOB_ID,
    filename: "2026年度上海市教育局部门预算公开说明.pdf",
    status: options.status ?? "review_required",
    quality_status: options.qualityStatus ?? "review_required",
    report_year: options.reportYear === undefined ? null : options.reportYear,
    report_kind: "budget",
    organization_name: "上海市教育局",
    structured_report_id: "GBC-2026-0731-0187",
    stage_progress: { phase: "quality_gate", percent: 100 },
    stage_failed_at: null,
    result: {
      rule_findings: options.ruleFindings ?? [
        {
          id: "FIN-1",
          rule_id: "GBC-BUD-014",
          severity: "high",
          title: "预算分项合计与公开总额不一致",
          message: "基本支出与项目支出合计为 129,650.00 万元，比财政拨款支出预算总额高 1,000.00 万元。",
          evidence: [{ page: 12, bbox: [100, 200, 400, 260], text: "基本支出 76,300.00 万元" }],
          location: { page: 12 },
        },
        {
          id: "FIN-2",
          rule_id: "GBC-META-003",
          severity: "medium",
          title: "公开年份与任务元数据存在冲突",
          message: "封面识别为 2026 年，但上传报送年份为 2025 年。",
          evidence: [{ page: 1, text: "2026 年度部门预算" }],
          location: { page: 1 },
          evidence_status: "degraded_missing_evidence",
        },
      ],
      meta: {
        pages: options.totalPages ?? 15,
        versions: {
          rule_versions: ["v3_3"],
          engine_version: "0.1.0",
          model_versions: [],
          prompt_versions: [],
        },
      },
    },
  };
}

async function installReviewWorkbenchMocks(page: Page, options: MockOptions) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/auth/me") {
      return json({ user: { username: "e2e-reviewer", is_admin: true } });
    }
    if (path === "/api/health") {
      return json({ status: "ok" });
    }
    if (path === `/api/jobs/${JOB_ID}`) {
      return json(buildJobDetail(options));
    }
    if (path === `/api/jobs/${JOB_ID}/structured-ingest`) {
      return json({ status: "done", review_item_count: 0, review_items: [] });
    }
    if (path === "/api/workflow") {
      if (method === "POST") {
        const body = request.postDataJSON() as Record<string, unknown>;
        options.workflowUpdates.push(body);
        const issueId = String(body.issue_id ?? "");
        options.workflowIssuesState[issueId] = {
          issue_id: issueId,
          job_id: String(body.job_id ?? ""),
          status: String(body.status ?? ""),
          note: typeof body.note === "string" ? body.note : null,
        };
        return json({ issues: options.workflowIssuesState, packages: [] });
      }
      return json({ issues: options.workflowIssuesState, packages: [] });
    }
    if (path.startsWith("/api/files/")) {
      return route.fulfill({
        status: 200,
        contentType: "image/png",
        body: Buffer.from(PNG_1X1_BASE64, "base64"),
      });
    }
    return json({});
  });
}

test.describe("Review workbench (Task 6)", () => {
  test("核心闭环：打开任务 → 选中问题 → 跳页 → 确认 → 计数变化 → 刷新仍在", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    // 打开任务：文件名与三栏骨架均可见
    await expect(page.getByTestId("gbc-review-filename")).toContainText("2026年度上海市教育局部门预算公开说明.pdf");
    await expect(page.getByTestId("gbc-review-thumbnail-rail")).toBeVisible();
    await expect(page.getByTestId("gbc-review-pdf-viewer")).toBeVisible();
    await expect(page.getByTestId("gbc-review-issues-tab")).toBeVisible();

    // 底部状态条初始计数：2 条 finding 中只有 1 条是正式问题（FIN-2 因证据不足
    // 被降级，不计入正式问题数，也不计入待处理），均未操作，应是
    // 0 已确认 · 0 已忽略 · 1 待处理
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("已确认 0");
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("待处理 1");

    // 选中问题：点击第一张问题卡，应跳转到该问题所在页（第 12 页）
    await page.getByTestId("gbc-review-issue-card-FIN-1").click();
    await expect(page.getByTestId("gbc-review-pdf-current-page")).toContainText("第 12 页");
    // 证据高亮应出现，标签包含问题的 rule_id
    await expect(page.getByTestId("gbc-review-evidence-label")).toContainText("GBC-BUD-014");

    // 确认问题：点击"确认问题"按钮
    await page.getByTestId("gbc-review-issue-confirm-FIN-1").click();
    await expect.poll(() => workflowUpdates.length, { timeout: 10_000 }).toBeGreaterThan(0);
    expect(workflowUpdates[0].action).toBe("update_issue");
    expect(workflowUpdates[0].job_id).toBe(JOB_ID);
    expect(workflowUpdates[0].issue_id).toBe("FIN-1");
    expect(workflowUpdates[0].status).toBe("confirmed");

    // 计数变化：已确认应从 0 变为 1，待处理从 1 变为 0
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("已确认 1");
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("待处理 0");
    // 自动保存时间应从"尚无保存记录"变为真实的 HH:MM
    await expect(page.getByTestId("gbc-review-status-bar-saved-at")).toContainText("自动保存于");

    // 刷新仍在：重新加载页面后，/api/workflow 返回的状态应让 FIN-1 仍显示已确认
    await page.reload();
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("已确认 1");
    await expect(page.locator('[data-testid="gbc-review-issue-card-FIN-1"]')).toContainText("已确认");
  });

  test("三 tab 切换与计数准确性：元数据/阶段记录 tab 显示真实数据", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    // 审核问题 tab：计数必须是 1（正式问题数，FIN-2 是降级问题不计入，
    // 与后端 count_formal_findings 同口径：2 条 finding 里只有 1 条正式）
    await expect(page.getByTestId("gbc-review-issues-count")).toContainText("审核问题（1）");

    // 元数据 tab
    await page.getByTestId("gbc-review-tab-metadata").click();
    await expect(page.getByTestId("gbc-review-metadata-tab")).toBeVisible();
    await expect(page.getByTestId("gbc-review-metadata-year")).toContainText("2026");
    await expect(page.getByTestId("gbc-review-metadata-rule-version")).toContainText("v3_3");
    await expect(page.getByTestId("gbc-review-metadata-engine-version")).toContainText("0.1.0");
    await expect(page.getByTestId("gbc-review-metadata-report-id")).toContainText("GBC-2026-0731-0187");

    // 阶段记录 tab：quality_gate 应是最终阶段，显示已完成
    await page.getByTestId("gbc-review-tab-stages").click();
    await expect(page.getByTestId("gbc-review-stage-history-tab")).toBeVisible();
    await expect(page.getByTestId("gbc-review-stage-quality_gate")).toBeVisible();
  });

  test("REGRESSION: degraded 问题必须带降级标识，不计入审核问题数", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    // FIN-2 是降级问题：卡片仍然展示（用户有权知道），但带独立的降级标识
    await expect(page.getByTestId("gbc-review-issue-degraded-badge-FIN-2")).toBeVisible();
    await expect(page.getByTestId("gbc-review-issue-degraded-badge-FIN-2")).toContainText("证据不足待复核");
    // 但"审核问题（N）"的 N 不应把它算进去：2 条 finding 里只有 1 条正式，
    // 必须显示（1）而不是（2）——这正是与 count_formal_findings 同口径的核心断言。
    await expect(page.getByTestId("gbc-review-issues-count")).toContainText("审核问题（1）");
    await expect(page.getByTestId("gbc-review-issues-count")).not.toContainText("审核问题（2）");
  });

  test("REGRESSION: 年份未识别到时元数据必须显示'未识别到'，不出现 2000 兜底", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: null, workflowUpdates, workflowIssuesState });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    await page.getByTestId("gbc-review-tab-metadata").click();
    await expect(page.getByTestId("gbc-review-metadata-year")).toContainText("未识别到");
    await expect(page.getByTestId("gbc-review-metadata-year")).not.toContainText("2000");
  });

  test("REGRESSION: review_required 状态徽章不得显示成'分析完成'", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, {
      status: "review_required",
      qualityStatus: "review_required",
      reportYear: 2026,
      workflowUpdates,
      workflowIssuesState,
    });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    await expect(page.getByText("需要人工复核")).toBeVisible();
    await expect(page.getByText("分析完成", { exact: false })).toHaveCount(0);
  });

  test("忽略问题后计数变化，且忽略的问题不再计入待处理", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    await page.getByTestId("gbc-review-issue-ignore-FIN-1").click();
    await expect.poll(() => workflowUpdates.length, { timeout: 10_000 }).toBeGreaterThan(0);
    expect(workflowUpdates[0].status).toBe("no_issue");

    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("已忽略 1");
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("待处理 0");
  });

  test("补充意见：保存备注后随问题一并持久化（复用既有 note 能力）", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    await page.getByTestId("gbc-review-issue-note-FIN-1").click();
    await expect(page.getByTestId("gbc-review-issue-note-dialog")).toBeVisible();
    await page.getByTestId("gbc-review-issue-note-textarea").fill("已与预算处电话核实，属实需整改。");
    await page.getByTestId("gbc-review-issue-note-save").click();

    await expect.poll(() => workflowUpdates.length, { timeout: 10_000 }).toBeGreaterThan(0);
    expect(workflowUpdates[0].note).toBe("已与预算处电话核实，属实需整改。");
  });

  test("REGRESSION: 阶段进度未知时必须显示 —，不得显示 0% 或猜测值", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    // 先注册通配的通用 mock，再注册更具体的 job detail 路由覆盖 stage_progress
    // 为 null 的场景——Playwright 的路由匹配是"后注册的先尝试"，因此更具体的
    // 覆盖路由必须在通用路由之后注册，才能真正生效覆盖掉通用响应。
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });
    await page.route(`**/api/jobs/${JOB_ID}`, async (route) => {
      const detail = buildJobDetail({ reportYear: 2026, workflowUpdates, workflowIssuesState });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ...detail, stage_progress: null, stage_failed_at: null }),
      });
    });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    await page.getByTestId("gbc-review-tab-stages").click();
    await expect(page.getByTestId("gbc-review-stage-history-tab")).toBeVisible();
    // 5 个阶段在数据缺失时都应显示 —（unknown 态），不得显示 0% 或误标为已完成
    for (const stage of ["upload", "pdf_parse", "metadata_recognition", "rule_ai_analysis", "quality_gate"]) {
      await expect(page.getByTestId(`gbc-review-stage-status-${stage}`)).toContainText("—");
    }
  });

  test("未带 job 参数时显示引导态，不假装有默认任务", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === "/api/auth/me") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ user: { username: "e2e-reviewer", is_admin: true } }),
        });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto("/review");
    await expect(page.getByTestId("gbc-review-no-job")).toBeVisible({ timeout: 15_000 });
  });

  // -------------------------------------------------------------------------
  // 修复 B：真实入口路径回归。此前 /review?job= 只能手敲 URL（全仓无链接指向
  // 它），e2e 全绿却没覆盖入口。本用例从处理队列列表点击「复核」进入审核台，
  // 不允许用直接构造 URL 的方式绕过入口验证。
  // -------------------------------------------------------------------------
  test("REGRESSION (fix B): entering from the queue list via the 复核 button loads the real issues", async ({
    page,
  }) => {
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates: [], workflowIssuesState });
    // 补充列表接口 mock：安装顺序在后，优先于通配 mock 生效
    await page.route("**/api/jobs", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { job_id: JOB_ID, filename: "2026年度上海市教育局部门预算公开说明.pdf", status: "review_required", report_year: 2026 },
        ]),
      });
    });

    await page.goto("/queue");
    const reviewEntry = page.getByTestId(`gbc-workbench-queue-review-${JOB_ID}`);
    await expect(reviewEntry).toBeVisible({ timeout: 15_000 });

    // 从列表点击进入（真实入口路径）
    await reviewEntry.click();

    await expect(page).toHaveURL(new RegExp(`/review\\?job=${JOB_ID}`), { timeout: 15_000 });
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });
    // 看到该任务的真实问题列表（与直接访问 URL 相同的数据与断言口径）
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toBeVisible();
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("待处理 1");
  });

  test("REGRESSION (fix B): hand-typed /review?job= for a running job shows a clear guidance state, not an empty workbench", async ({
    page,
  }) => {
    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      if (path === "/api/auth/me") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ user: { username: "e2e-reviewer", is_admin: true } }),
        });
      }
      if (path === "/api/jobs/running-job-1") {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            job_id: "running-job-1",
            filename: "正在分析的材料.pdf",
            status: "processing",
            result: { rule_findings: [], meta: { pages: 10 } },
          }),
        });
      }
      if (path === "/api/jobs/running-job-1/structured-ingest") {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "none" }) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto("/review?job=running-job-1");
    await expect(page.getByTestId("gbc-review-not-ready")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("gbc-review-not-ready")).toContainText("尚未分析完成");
    // 引导态提供返回路径，不允许是死胡同
    await expect(page.getByTestId("gbc-review-not-ready")).toContainText("前往处理队列");
  });
});
