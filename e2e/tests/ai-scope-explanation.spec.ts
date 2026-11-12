import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * 检查范围与解释字段的用户可见性（2026-10-25 收口）。
 *
 * 关键约束：
 * - 覆盖状态不能藏在“来源与分类说明”二级折叠里；
 * - 4 个检查单元只成功 3 个时，顶栏 AI 标识直接写“3/4 未完成”和未检页码，
 *   点开的状态面板写“有效完成 3/4，失败 1”，不能把失败算成“已处理 4/4”；
 * - 问题队列不再另放检查范围条（与顶栏 AI 标识重复）；
 * - 纯规则任务与历史无覆盖记录必须显式写“未执行/未记录”；
 * - 规则问题的当前值/应为/差额和“程序计算”来源必须直接可见。
 */

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";
const JOB_ID = "job-ai-scope-explanation-2026";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: baseURL,
  sameSite: "Lax" as const,
};

const issue = {
  id: "FIN-EXPLAIN-1",
  rule_id: "V33-222",
  source: "rule",
  severity: "high",
  title: "机关事业单位基本养老保险缴费支出表文金额不一致",
  message: "机关事业单位基本养老保险缴费支出表文金额不一致：表内 165.80 万元，说明 141.12 万元，相差 24.68 万元。",
  expected: "165.80",
  actual: "141.12",
  difference: "-24.68",
  check: "final_disclosure_amount",
  table_or_section: "五、一般公共预算财政拨款支出决算情况说明",
  amounts_provenance: "program_calculated",
  calculation_basis: { unit: "万元", precision: 2, rule_version: "v3_3", verified: true },
  evidence_status: "complete",
  evidence_verification: { status: "verified", source_identity_complete: true },
  evidence: [{ page: 20, text: "年初预算为165.80万元，支出决算为141.12万元", verification_status: "verified" }],
  location: { page: 20 },
};

function buildJobDetail(
  meta: Record<string, unknown>,
  reviewReport?: Record<string, unknown>,
  aiFindings: Record<string, unknown>[] = [],
) {
  return {
    job_id: JOB_ID,
    filename: "上海市某单位2022年度部门决算.pdf",
    status: "review_required",
    quality_status: "review_required",
    report_year: 2022,
    report_kind: "final",
    organization_name: "上海市某单位",
    stage_progress: { phase: "quality_gate", percent: 100 },
    stage_failed_at: null,
    result: {
      rule_findings: [issue],
      ai_findings: aiFindings,
      meta,
    },
    ...(reviewReport ? { review_report: reviewReport } : {}),
  };
}

async function installMocks(
  page: Page,
  meta: Record<string, unknown>,
  reviewReport?: Record<string, unknown>,
  aiFindings: Record<string, unknown>[] = [],
) {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me") return json({ user: { username: "e2e-reviewer", is_admin: true } });
    if (path === "/api/reviews") {
      return json({ data: { review_context: { available: true, slot_id: "slot-ai-scope" }, review: {
        slot_id: "slot-ai-scope",
        current_document_version_id: 201,
        current_analysis: { job_uuid: JOB_ID, document_version_id: 201, analysis_basis_token: `${JOB_ID}:1`, completed: true },
        current_session: { review_session_id: "session-ai-scope", slot_id: "slot-ai-scope", document_version_id: 201,
          analysis_job_uuid: JOB_ID, analysis_basis_token: `${JOB_ID}:1`, status: "in_progress", started_by: "e2e-reviewer" },
        history: [],
        completion_gate: { can_complete: false, blockers: [] },
      } } });
    }
    if (path === `/api/jobs/${JOB_ID}`) return json(buildJobDetail(meta, reviewReport, aiFindings));
    if (path === `/api/jobs/${JOB_ID}/structured-ingest`) return json({ status: "done", review_item_count: 0, review_items: [] });
    if (path === `/api/jobs/${JOB_ID}/reanalyze-options`) return json({
      job_id: JOB_ID,
      inherit: { available: true, unknown_fields: [], message: null, mode: "dual", use_local_rules: true, use_ai_assist: true, ai_profile_id: "m1", ai_configuration: { id: "m1", name: "模型1", model: "m1", revision: 1 } },
      last_execution: { status: "done", analysis_conclusion: null, ai_requested: true, local_state: "completed", ai_state: "degraded" },
      model: { state: "ok", message: null, profile: { id: "m1", name: "模型1", model: "m1" } },
      capabilities: { enabled: true, active_profile_id: "m1", profiles: [{ id: "m1", name: "模型1", model: "m1" }] },
    });
    if (path === "/api/workflow") return json({ issues: {}, packages: [] });
    if (path.startsWith("/api/files/")) {
      return route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="1280"><rect width="100%" height="100%" fill="white"/></svg>`,
      });
    }
    return json({});
  });
}

const completeMeta = {
  ai_execution: { requested: true, state: "succeeded" },
  ai_coverage: {
    status: "complete",
    planned_calls: 4,
    completed_calls: 4,
    succeeded_calls: 4,
    failed_calls: 0,
  },
  conclusion_scope: "no_findings_within_covered_checks",
};

const partialMeta = {
  ai_execution: { requested: true, state: "degraded", accepted_candidates: 3 },
  ai_coverage: {
    status: "partial",
    planned_calls: 4,
    completed_calls: 4,
    succeeded_calls: 3,
    failed_calls: 1,
    retried_calls: 1,
    retry_attempts: 1,
    failed_units: [{
      specialty: "narrative_consistency",
      window_id: 1,
      pages: [13, 14, 20],
      status: "error",
      error_type: "server_error",
      http_status: 502,
      retryable: true,
      attempts: 2,
      retry_count: 1,
    }],
    uncovered_ranges: [],
  },
  conclusion_scope: "incomplete_scope",
};

/** 问题列表按需展开：断言列表行之前先展开（已展开时不重复点击）。 */
async function openIssueList(page: Page) {
  const toggle = page.getByTestId("gbc-review-toggle-list");
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  await expect(page.getByTestId("gbc-review-list-panel")).toBeVisible();
}

test.describe("AI scope and explanation visibility", () => {
  test("部分失败直接显示 3/4、失败页与程序计算的解释字段", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, partialMeta);
    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });

    // 不点击任何东西，顶栏 AI 标识已写出完成计数与未检页码。
    const chip = page.getByTestId("gbc-review-state-ai");
    await expect(chip).toContainText("AI 检查完成 3/4 单元");
    await expect(chip).toContainText("未完成：说明一致性第13-14、20页");
    // 问题队列不再重复放一条检查范围条。
    await expect(page.getByTestId("gbc-review-issues-tab")).toBeVisible();
    await expect(page.getByTestId("gbc-review-ai-scope")).toHaveCount(0);

    // 完整计数、结论作用域、错误类型、尝试次数在 AI 标识打开的状态面板里。
    await chip.click();
    const panelScope = page.getByTestId("gbc-review-ai-scope-panel");
    await expect(panelScope).toBeInViewport();
    await expect(panelScope.getByTestId("gbc-review-ai-scope-panel-headline")).toContainText("有效完成 3/4");
    await expect(panelScope.getByTestId("gbc-review-ai-scope-panel-headline")).toContainText("失败 1");
    await expect(panelScope.getByTestId("gbc-review-ai-scope-panel-headline")).toContainText("另有 1 个单元重试");
    await expect(panelScope.getByTestId("gbc-review-ai-scope-panel-conclusion-scope")).toContainText("不能得出完整结论");
    await expect(panelScope.getByTestId("gbc-review-ai-scope-panel-failed-units")).toContainText("说明一致性");
    await expect(panelScope.getByTestId("gbc-review-ai-scope-panel-failed-units")).toContainText("第13-14、20页");
    await expect(panelScope.getByTestId("gbc-review-ai-scope-panel-failed-units")).toContainText("server_error");
    await expect(panelScope.getByTestId("gbc-review-ai-scope-panel-failed-units")).toContainText("已尝试 2 次");
    await page.getByTestId("gbc-review-info-panel-close").click();

    await openIssueList(page);
    await page.getByTestId("gbc-review-issue-card-FIN-EXPLAIN-1").click();
    const explanation = page.getByTestId("gbc-review-issue-explanation-FIN-EXPLAIN-1");
    await expect(explanation).toBeVisible();
    // 数字短时三栏并排：标签与数值分行显示。
    await expect(explanation.getByLabel("金额对照")).toContainText("当前值141.12");
    await expect(explanation.getByLabel("金额对照")).toContainText("应为165.80");
    await expect(explanation.getByLabel("金额对照")).toContainText("差额-24.68");
    await expect(explanation).toContainText("检查项：final_disclosure_amount");
    await expect(explanation).toContainText("表/章节：五、一般公共预算财政拨款支出决算情况说明");
    await expect(explanation).toContainText("程序按已定位事实计算");
    await expect(explanation).toContainText("单位：万元");
    await page.screenshot({ path: test.info().outputPath("ai-scope-partial.png") });
  });

  test("任务页把检查范围压成一行，详情就地展开，重新分析经对话框选择检查方式", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, partialMeta);
    await page.goto(`/task/${JOB_ID}`);
    const scope = page.getByTestId("task-ai-scope");
    await expect(scope).toBeVisible({ timeout: 15_000 });
    await expect(scope.getByTestId("task-ai-scope-headline")).toContainText("有效完成 3/4");
    await expect(scope.getByTestId("task-ai-scope-failed-groups")).toContainText("说明一致性第13-14、20页");
    await expect(scope.getByTestId("task-ai-scope-details")).toHaveCount(0);
    expect((await scope.boundingBox())?.height ?? 999).toBeLessThan(40);

    const toggle = scope.getByTestId("task-ai-scope-details-toggle");
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(scope.getByTestId("task-ai-scope-failed-units")).toContainText("server_error");
    await expect(scope.getByTestId("task-ai-scope-failed-units")).toContainText("已尝试 2 次");

    // 检查方式选择收进对话框：默认按上次方式，展示上轮部分完成状态。
    const header = page.getByTestId("task-summary-header");
    await expect(header.getByTestId("task-reanalyze-button")).toBeVisible();
    await header.getByTestId("task-reanalyze-button").click();
    const dialog = page.getByTestId("reanalyze-method-dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog.getByTestId("reanalyze-method-picker-inherit")).toBeChecked();
    await expect(dialog.getByTestId("reanalyze-method-picker-execution-hint")).toContainText("部分完成");
    await dialog.getByTestId("reanalyze-method-cancel").click();
    await expect(dialog).toHaveCount(0);
    await page.screenshot({ path: test.info().outputPath("ai-scope-task-inline.png") });
  });

  test("纯规则任务明确写“文字语义未执行”，不显示成覆盖完整", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, {
      ai_execution: { requested: false, state: "not_requested" },
      conclusion_scope: "findings_within_covered_checks",
    });
    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });
    const chip = page.getByTestId("gbc-review-state-ai");
    await expect(chip).toHaveText("AI 检查未执行");
    await chip.click();
    const scope = page.getByTestId("gbc-review-ai-scope-panel");
    await expect(scope.getByTestId("gbc-review-ai-scope-panel-headline")).toContainText("本次未执行");
    await expect(scope).toContainText("文字语义检查未执行");
    await expect(scope).not.toContainText("覆盖完整");
  });

  test("复核报告中的 AI 候选保留解释字段、模型声称提示与双方证据", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    const aiFinding = {
      id: "AI-CANON-1",
      rule_id: "AI-XSEC-005",
      source: "ai",
      title: "跨段同口径表述矛盾",
      message: "跨段同口径表述矛盾",
      expected: "表格列示该科目支出决算合计为165.80万元，待核实。",
      actual: "说明列示该科目支出决算为141.12万元，待核实。",
      check: "同年度同科目一般公共预算财政拨款支出表与说明一致性",
      table_or_section: "五（三）第3项：机关事业单位基本养老保险缴费支出",
      amounts_provenance: "model_claimed_not_verified",
      calculation_basis: { source: "model_claim", verified: false },
      location: {
        page: 20,
        evidence_pairs: [
          { page: 13, quote: "165.80", table_or_section: "一般公共预算财政拨款支出决算表" },
          { page: 20, quote: "141.12", table_or_section: "五（三）第3项" },
        ],
      },
      evidence: [{ page: 20, text: "支出决算为141.12万元" }],
      evidence_status: "complete",
      evidence_verification: { status: "verified", source_identity_complete: true },
    };
    const reviewReport = {
      issues: [],
      tips: [],
      manual_review: [{ id: "AI-CANON-1", record_type: "ai_candidate", finding: aiFinding }],
    };
    await installMocks(page, completeMeta, reviewReport);
    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });
    // 待核实的 AI 候选在参考页签，不计入完成条件。
    await page.getByTestId("gbc-review-tab-reference").click();
    await openIssueList(page);
    const card = page.locator('[data-review-problem-id="AI-CANON-1"]');
    await expect(card).toHaveCount(1);
    await card.click();
    const explanation = page.getByTestId("gbc-review-issue-explanation-AI-CANON-1");
    await expect(explanation).toBeVisible();
    await expect(explanation).toContainText("当前值：说明列示该科目支出决算为141.12万元");
    await expect(explanation).toContainText("应为：表格列示该科目支出决算合计为165.80万元");
    await expect(explanation).toContainText("数值来源：模型声称，尚未经程序核实");
    await expect(page.getByText("跨段证据 1")).toBeVisible();
    await expect(page.getByText("跨段证据 2")).toBeVisible();
  });

  test("历史结果缺覆盖记录时写“未记录”，不冒充已完成", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installMocks(page, {
      ai_execution: { requested: true, state: "succeeded" },
      conclusion_scope: "no_findings_within_covered_checks",
    });
    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });
    const chip = page.getByTestId("gbc-review-state-ai");
    await expect(chip).toHaveText("AI 检查未记录");
    await chip.click();
    const scope = page.getByTestId("gbc-review-ai-scope-panel");
    await expect(scope.getByTestId("gbc-review-ai-scope-panel-headline")).toContainText("覆盖信息未记录");
    await expect(scope).not.toContainText("已完整完成");
  });

  test("从 AI 文字检查进入参考时直接筛选大模型来源，不用在全部参考里翻找", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    const aiFinding = {
      id: "AI-NAV-1",
      rule_id: "AI-XSEC-001",
      source: "ai",
      severity: "medium",
      title: "AI 候选：口径表述待核实",
      message: "AI 候选：口径表述待核实",
      evidence: [{ page: 20, text: "支出决算为141.12万元" }],
      evidence_status: "complete",
      location: { page: 20 },
    };
    // AI 发现按复核报告口径归入参考（ai_candidate），与真实后端一致；只给
    // ai_findings 而不给复核报告时，前端会把它当成正式问题放进「必须处理」。
    const reviewReport = {
      issues: [],
      tips: [],
      manual_review: [{ id: "AI-NAV-1", record_type: "ai_candidate", finding: aiFinding }],
    };
    await installMocks(page, completeMeta, reviewReport, [aiFinding]);
    await page.goto(`/review?job=${JOB_ID}`);
    await expect(page.getByTestId("gbc-review-workbench-page")).toBeVisible({ timeout: 15_000 });
    // AI 标识 → 检查与复核状态面板 → AI 文字检查一节里的“查看大模型参考条目”。
    await page.getByTestId("gbc-review-state-ai").click();
    await page.getByRole("button", { name: "查看大模型参考条目" }).click();
    // 来源筛选在问题列表面板内；先展开列表，再确认直接落在「大模型」来源，
    // 而不是 20 条参考里的第一条。
    await openIssueList(page);
    await expect(page.getByTestId("gbc-review-source-ai")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator('[data-review-problem-id="AI-NAV-1"]')).toHaveCount(1);
    await expect(page.locator('[data-review-problem-id="FIN-EXPLAIN-1"]')).toHaveCount(0);
  });
});
