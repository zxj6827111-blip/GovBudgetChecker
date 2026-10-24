import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * 审核工作台左右对照布局 e2e 验证。
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
          evidence_status: "complete",
          evidence_verification: { status: "verified", source_identity_complete: true },
          evidence: [{ page: 12, bbox: [100, 200, 400, 260], text: "基本支出 76,300.00 万元", location_level: "exact_span", verification_status: "verified" }],
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
    if (path === "/api/reviews") {
      return json({ data: { review_context: { available: true, slot_id: "slot-workbench" }, review: {
        slot_id: "slot-workbench", current_document_version_id: 101,
        current_analysis: { job_uuid: JOB_ID, document_version_id: 101, analysis_basis_token: `${JOB_ID}:1`, completed: true },
        current_session: { review_session_id: "session-workbench", slot_id: "slot-workbench", document_version_id: 101,
          analysis_job_uuid: JOB_ID, analysis_basis_token: `${JOB_ID}:1`, status: "in_progress", started_by: "e2e-reviewer" },
        history: [], completion_gate: { can_complete: false, blockers: [] },
      } } });
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
        contentType: "image/svg+xml",
        body: `<svg xmlns="http://www.w3.org/2000/svg" width="${600 * Number(new URL(request.url()).searchParams.get("scale") ?? 1.6)}" height="${840 * Number(new URL(request.url()).searchParams.get("scale") ?? 1.6)}"><rect width="100%" height="100%" fill="white"/></svg>`,
      });
    }
    return json({});
  });
}

test.describe("Review workbench (Task 6)", () => {
  test("核心闭环：确认移出待处理，自动接续证据，全部问题及刷新保留记录", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });

    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    // 打开任务：默认左右对照，缩略图按需展开。
    await expect(page.getByTestId("gbc-review-filename")).toContainText("2026年度上海市教育局部门预算公开说明.pdf");
    await expect(page.getByTestId("gbc-review-thumbnail-rail")).toHaveCount(0);
    await page.getByTestId("gbc-review-toggle-thumbnails").click();
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
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("12");
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

    // 保存成功才移出队列，接续下一张卡和对应 PDF 原文。
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveCount(0);
    await expect(page.getByTestId("gbc-review-issue-card-FIN-2")).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("1");
    await expect(page.getByTestId("gbc-review-scope-pending")).toContainText("待处理（1）");
    await page.screenshot({ path: test.info().outputPath("pending-after-confirm.png") });
    await page.getByTestId("gbc-review-scope-all").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toContainText("已确认");

    // 刷新默认回到待处理，已处理记录在全部问题中持续可查。
    await page.reload();
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("已确认 1");
    await expect(page.getByTestId("gbc-review-scope-pending")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveCount(0);
    await page.getByTestId("gbc-review-scope-all").click();
    await page.getByTestId("gbc-review-toggle-filters").click();
    await page.getByTestId("gbc-review-status-confirmed").click();
    await expect(page.locator('[data-testid="gbc-review-issue-card-FIN-1"]')).toContainText("已确认");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-2")).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath("all-confirmed.png") });
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
    await expect(page.getByTestId("gbc-review-issues-count")).toContainText("正式问题（1）");

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
    await expect(page.getByTestId("gbc-review-issue-card-FIN-2")).toContainText("证据不足");
    await page.getByTestId("gbc-review-issue-card-FIN-2").click();
    await expect(page.getByTestId("gbc-review-issue-degraded-badge-FIN-2")).toBeVisible();
    await expect(page.getByTestId("gbc-review-issue-degraded-badge-FIN-2")).toContainText("证据不足待复核");
    // 但"审核问题（N）"的 N 不应把它算进去：2 条 finding 里只有 1 条正式，
    // 必须显示（1）而不是（2）——这正是与 count_formal_findings 同口径的核心断言。
    await expect(page.getByTestId("gbc-review-issues-count")).toContainText("正式问题（1）");
    await expect(page.getByTestId("gbc-review-issues-count")).not.toContainText("正式问题（2）");
  });

  test("列表 + 详情：打开即选中首条，↑↓ 与上一条/下一条同步切换详情和原文", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });
    await page.goto(`/review?job=${JOB_ID}`);

    const first = page.getByTestId("gbc-review-issue-card-FIN-1");
    const detail = page.getByTestId("gbc-review-issue-detail");
    await expect(first).toHaveAttribute("aria-current", "true");
    await expect(detail).toHaveAttribute("data-detail-problem-id", "FIN-1");
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("12");
    await expect(page.getByTestId("gbc-review-issue-position")).toContainText("第 1 / 2 条");
    await expect(page.getByTestId("gbc-review-issue-prev")).toBeDisabled();

    await first.focus();
    await page.keyboard.press("ArrowDown");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-2")).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-2")).toBeFocused();
    await expect(detail).toHaveAttribute("data-detail-problem-id", "FIN-2");
    await expect(page.getByTestId("gbc-review-issue-next")).toBeDisabled();

    await page.getByTestId("gbc-review-issue-prev").click();
    await expect(detail).toHaveAttribute("data-detail-problem-id", "FIN-1");
    await expect(first).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("12");
    expect(workflowUpdates).toHaveLength(0);
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
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveCount(0);
    await page.getByTestId("gbc-review-scope-all").click();
    await page.getByTestId("gbc-review-toggle-filters").click();
    await page.getByTestId("gbc-review-status-no_issue").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toContainText("已忽略");
    await page.getByTestId("gbc-review-issue-restore-FIN-1").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "pending");
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("待处理 1");
    await page.reload();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "pending");
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
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "pending");
    await expect(page.getByTestId("gbc-review-undo")).toHaveCount(0);
  });

  test("撤销确认恢复原来的待复核状态、意见及证据定位", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {
      "FIN-1": { issue_id: "FIN-1", job_id: JOB_ID, status: "needs_review", note: "待核对财政拨款口径" },
    };
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });
    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "needs_review");
    await page.getByTestId("gbc-review-issue-confirm-FIN-1").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveCount(0);
    await page.getByTestId("gbc-review-undo").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "needs_review");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("12");
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("待复核 1");
    expect(workflowUpdates.at(-1)).toMatchObject({ status: "needs_review", note: "待核对财政拨款口径" });
    await page.reload();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "needs_review");
  });

  test("全部问题中修改判定后，撤销恢复已确认状态而不是一律改成待处理", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {
      "FIN-1": { issue_id: "FIN-1", job_id: JOB_ID, status: "confirmed", note: "已核实" },
    };
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });
    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("已确认 1");
    await page.getByTestId("gbc-review-scope-all").click();
    await page.getByTestId("gbc-review-issue-card-FIN-1").click();
    await page.getByTestId("gbc-review-issue-ignore-FIN-1").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toContainText("已忽略");
    await page.getByTestId("gbc-review-undo").click();
    await expect(page.getByTestId("gbc-review-scope-all")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "confirmed");
    expect(workflowUpdates.at(-1)).toMatchObject({ status: "confirmed", note: "已核实" });
  });

  test("最后一条处理后展示全部问题入口，保留来源和分类筛选而不跳到其他类型", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });
    await page.goto(`/review?job=${JOB_ID}`);
    await page.getByTestId("gbc-review-toggle-filters").click();
    await page.getByTestId("gbc-review-source-rule").click();
    await page.getByTestId("gbc-review-category-issue").click();
    await page.getByTestId("gbc-review-issue-card-FIN-1").click();
    await page.getByTestId("gbc-review-issue-confirm-FIN-1").click();
    await expect(page.getByTestId("gbc-review-issues-empty")).toContainText("当前筛选下已无待处理项");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-2")).toHaveCount(0);
    await expect(page.getByTestId("gbc-review-evidence-highlight")).toHaveCount(0);
    await expect(page.getByTestId("gbc-review-source-rule")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("gbc-review-category-issue")).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("gbc-review-scope-pending")).toContainText("待处理（0）");
    await page.getByTestId("gbc-review-view-all").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toContainText("已确认");
    await page.getByTestId("gbc-review-clear-filters").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-2")).toBeVisible();
  });

  test("保存被拒时保留当前问题、选中状态和证据，不显示撤销成功入口", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates: [], workflowIssuesState: {} });
    await page.route("**/api/workflow", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      return route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { error: "review_context_mismatch", message: "分析结果已更新，请刷新后重新核对" } }) });
    });
    await page.goto(`/review?job=${JOB_ID}`);
    await page.getByTestId("gbc-review-issue-card-FIN-1").click();
    await page.getByTestId("gbc-review-issue-confirm-FIN-1").click();
    await expect(page.getByTestId("gbc-review-action-notice")).toContainText("分析结果已更新");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "pending");
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("12");
    await expect(page.getByTestId("gbc-review-undo")).toHaveCount(0);
  });

  test("决定保存成功后，工作流重取短暂失败也不能让已处理问题重新出现", async ({ page }) => {
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates: [], workflowIssuesState });
    await page.route("**/api/workflow?**", async (route) => {
      if (!workflowIssuesState["FIN-1"]) return route.fallback();
      return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "temporarily unavailable" }) });
    });
    await page.goto(`/review?job=${JOB_ID}`);
    await page.getByTestId("gbc-review-issue-confirm-FIN-1").click();
    await expect(page.getByTestId("gbc-review-undo")).toBeEnabled();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveCount(0);
    await expect(page.getByTestId("gbc-review-status-bar-counts")).toContainText("已确认 1");
    await page.getByTestId("gbc-review-scope-all").click();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toContainText("已确认");
  });

  test("长列表保存前保留卡片并阻止重复操作，成功后下一条标题接续当前位置", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 850 });
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    const template = buildJobDetail({ workflowUpdates, workflowIssuesState }).result.rule_findings[0];
    const ruleFindings = Array.from({ length: 6 }, (_, i) => ({
      ...template, id: `LONG-${i}`, title: `连续审核问题 ${i}`, message: "长证据说明，核对当前材料原始表格。".repeat(60),
      evidence: [{ page: i + 2, bbox: [100, 200, 400, 260], text: `测试原文 ${i}`, location_level: "exact_span", verification_status: "verified" }],
      location: { page: i + 2 },
    }));
    let releaseWrite!: () => void;
    const writeGate = new Promise<void>((resolve) => { releaseWrite = resolve; });
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState, ruleFindings });
    await page.route("**/api/workflow", async (route) => {
      if (route.request().method() === "POST") await writeGate;
      return route.fallback();
    });
    await page.goto(`/review?job=${JOB_ID}`);
    const firstBounds = await page.getByTestId("gbc-review-issue-card-LONG-0").boundingBox();
    const listBefore = await page.getByTestId("gbc-review-issues-list").boundingBox();
    await page.getByTestId("gbc-review-issue-confirm-LONG-0").click();
    await expect(page.getByTestId("gbc-review-issue-confirm-LONG-0")).toBeDisabled();
    await expect(page.getByTestId("gbc-review-issue-ignore-LONG-0")).toBeDisabled();
    await expect(page.getByTestId("gbc-review-issue-card-LONG-0")).toHaveCount(1);
    expect(workflowUpdates).toHaveLength(0);
    releaseWrite();
    await expect(page.getByTestId("gbc-review-issue-card-LONG-0")).toHaveCount(0);
    const next = page.getByTestId("gbc-review-issue-card-LONG-1");
    await expect(next).toHaveAttribute("aria-current", "true");
    await expect(page.getByTestId("gbc-review-pdf-page-input")).toHaveValue("3");
    await expect(page.getByTestId("gbc-review-issue-detail")).toHaveAttribute("data-detail-problem-id", "LONG-1");
    await expect(page.getByTestId("gbc-review-issue-confirm-LONG-1")).toBeVisible();
    // 撤销提示条会把列表整体下移，所以按列表内的相对位置比较。
    const nextBounds = await next.boundingBox();
    const listAfter = await page.getByTestId("gbc-review-issues-list").boundingBox();
    expect(Math.abs((nextBounds!.y - listAfter!.y) - (firstBounds!.y - listBefore!.y))).toBeLessThan(8);
    expect(workflowUpdates).toHaveLength(1);
  });

  test("待处理只排除已处理终态，已进整改包仍在全部问题中保留", async ({ page }) => {
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    const template = buildJobDetail({ workflowUpdates, workflowIssuesState }).result.rule_findings[0];
    const statuses = ["confirmed", "no_issue", "in_package", "needs_review", "future_status", undefined];
    const ruleFindings = statuses.map((status, i) => {
      const id = `STATUS-${i}`;
      if (status) workflowIssuesState[id] = { issue_id: id, job_id: JOB_ID, status };
      return { ...template, id, title: `处理状态边界 ${i}` };
    });
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState, ruleFindings });
    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-scope-pending")).toContainText("待处理（3）");
    for (let i = 0; i < 3; i++) await expect(page.getByTestId(`gbc-review-issue-card-STATUS-${i}`)).toHaveCount(0);
    for (let i = 3; i < 6; i++) await expect(page.getByTestId(`gbc-review-issue-card-STATUS-${i}`)).toHaveCount(1);
    await page.getByTestId("gbc-review-scope-all").click();
    await expect(page.getByTestId("gbc-review-scope-all")).toContainText("全部问题（6）");
    await page.getByTestId("gbc-review-toggle-filters").click();
    await page.getByTestId("gbc-review-status-in_package").click();
    await expect(page.getByTestId("gbc-review-issue-card-STATUS-2")).toContainText("已进整改包");
    await expect(page.getByTestId("gbc-review-issues-list").locator("[data-review-problem-id]")).toHaveCount(1);
  });

  test("保存期间切换材料，旧决定不串入新材料且操作锁正常释放", async ({ page }) => {
    const nextJob = `${JOB_ID}-next`;
    const workflowUpdates: Array<Record<string, unknown>> = [];
    const workflowIssuesState: MockOptions["workflowIssuesState"] = {};
    await page.context().addCookies([sessionCookie]);
    await installReviewWorkbenchMocks(page, { reportYear: 2026, workflowUpdates, workflowIssuesState });
    await page.route(`**/api/jobs/${nextJob}`, (route) => route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ...buildJobDetail({ reportYear: 2026, workflowUpdates, workflowIssuesState }), job_id: nextJob }),
    }));
    await page.route("**/api/reviews?**", (route) => {
      if (new URL(route.request().url()).searchParams.get("job_uuid") !== nextJob) return route.fallback();
      return route.fulfill({ contentType: "application/json", body: JSON.stringify({ data: {
        review_context: { available: true, slot_id: "slot-next" },
        review: {
          slot_id: "slot-next", current_document_version_id: 102,
          current_analysis: { job_uuid: nextJob, document_version_id: 102, analysis_basis_token: `${nextJob}:1`, completed: true },
          current_session: { review_session_id: "session-next", slot_id: "slot-next", document_version_id: 102,
            analysis_job_uuid: nextJob, analysis_basis_token: `${nextJob}:1`, status: "in_progress", started_by: "e2e-reviewer" },
          history: [], completion_gate: { can_complete: false, blockers: [] },
        },
      } }) });
    });
    let releaseWrite!: () => void;
    const writeGate = new Promise<void>((resolve) => { releaseWrite = resolve; });
    await page.route("**/api/workflow", async (route) => {
      if (route.request().method() === "POST") await writeGate;
      return route.fallback();
    });
    await page.goto(`/review?job=${JOB_ID}`);
    await page.getByTestId("gbc-review-issue-confirm-FIN-1").click();
    await expect(page.getByTestId("gbc-review-issue-confirm-FIN-1")).toBeDisabled();
    // Next 支持的原生历史导航保留页面组件，覆盖同一个工作台切换 job 的竞态。
    await page.evaluate((url) => window.history.pushState(null, "", url), `/review?job=${nextJob}`);
    await expect(page.getByTestId("gbc-review-scope-pending")).toHaveAttribute("aria-pressed", "true");
    releaseWrite();
    await expect(page.getByTestId("gbc-review-issue-confirm-FIN-1")).toBeEnabled();
    await expect(page.getByTestId("gbc-review-issue-card-FIN-1")).toHaveAttribute("data-workflow-status", "pending");
    await expect(page.getByTestId("gbc-review-decision-notice")).toHaveCount(0);
    await page.getByTestId("gbc-review-issue-ignore-FIN-1").click();
    await expect(page.getByTestId("gbc-review-undo")).toBeEnabled();
    expect(workflowUpdates).toEqual([
      expect.objectContaining({ job_id: JOB_ID, status: "confirmed" }),
      expect.objectContaining({ job_id: nextJob, status: "no_issue" }),
    ]);
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

  test("未带 job 参数时显示复核清单，不自动选择任务", async ({ page }) => {
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
      if (url.pathname === "/api/reviews/worklist") {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { items: [], counts: {}, pending_total: 0, viewer: "e2e-reviewer" } }) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.goto("/review");
    await expect(page.getByTestId("gbc-review-list-page")).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveURL(/\/review$/);
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
