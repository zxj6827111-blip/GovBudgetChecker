import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 7：质量管理页 e2e 验证。
 *
 * 覆盖范围（对照任务书 7 测试要求）：
 * 1. 管理员进入 /quality 看到真实结构性指标与门禁判定；
 * 2. 反例（关键）：页面不出现「召回率」「精确率」「Golden Corpus」
 *    「OCR 成功率」「准确率」字样（防未来误加假数）；
 * 3. 反例：证据完整率分母为 0 时显示 —，不出现 100%；
 * 4. 反例：无失败任务时失败分布显示空态，不凭空生成分段；
 * 5. 局限声明文案存在性（结构性门禁 ≠ 业务质量达标）；
 * 6. 未启用指标说明渲染；
 * 7. 非管理员被拦（重定向 /workbench）；
 * 8. 指标端点不可用时优雅降级，不报错崩页。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

interface QualityMockOptions {
  isAdmin: boolean;
  metricsStatus?: 200 | 403;
  metricsPayload?: Record<string, unknown>;
  jobs?: Array<Record<string, unknown>>;
}

const DEFAULT_JOBS: Array<Record<string, unknown>> = [
  {
    job_id: "job-done",
    filename: "2026年度部门预算公开说明.pdf",
    status: "done",
    organization_name: "市教育局",
    organization_id: "org-edu",
    report_year: 2026,
    report_kind: "budget",
    page_coverage: 1.0,
    merged_issue_total: 6,
  },
  {
    job_id: "job-failed",
    filename: "损坏文件.pdf",
    status: "error",
    organization_id: null,
    report_year: null,
    report_kind: "unknown",
    page_coverage: null,
    stage_failed_at: { phase: "pdf_parse", phase_label: "PDF 解析", percent: null, raw_stage: "构建文档对象" },
  },
  {
    job_id: "job-failed-2",
    filename: "超时文件.pdf",
    status: "error",
    organization_id: null,
    report_year: null,
    report_kind: "unknown",
    page_coverage: null,
    stage_failed_at: { phase: "rule_ai_analysis", phase_label: "规则与 AI 分析", percent: null, raw_stage: "执行规则检查" },
  },
];

const DEFAULT_METRICS: Record<string, unknown> = {
  jobs: { total: 30 },
  quality: {
    unknown_report_kind: { count: 2, ratio: 0.0667 },
    review_required: { count: 4, ratio: 0.1333 },
    unresolved_report_year: { count: 3, ratio: 0.1 },
    error_jobs: { count: 2, ratio: 0.0667 },
    evidence_degraded_findings: 1,
    formal_issue_total: 12,
    evidence_completeness: {
      findings_total: 12,
      findings_complete: 12,
      completeness_rate: 1.0,
      jobs_without_field: 3,
    },
  },
  report_id: { collision_count: 0 },
};

async function installQualityMocks(page: Page, options: QualityMockOptions) {
  const jobs = options.jobs ?? DEFAULT_JOBS;
  const metrics = options.metricsPayload ?? DEFAULT_METRICS;
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

    if (path === "/api/jobs") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(jobs) });
      return;
    }

    if (path === "/api/metrics") {
      const status = options.metricsStatus ?? 200;
      if (status !== 200) {
        await route.fulfill({
          status,
          contentType: "application/json",
          body: JSON.stringify({ detail: "forbidden" }),
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(metrics) });
      return;
    }

    if (path === "/api/organizations") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ tree: [{ id: "org-edu", name: "市教育局", level: "department", children: [] }] }),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Quality management (Task 7)", () => {
  test("admin sees real structural metrics and gate verdicts", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, { isAdmin: true });
    await page.goto("/quality");

    // 处理成功率：(30-2)/30 = 93.3%
    await expect(page.getByTestId("gbc-quality-metric-success-rate")).toContainText("93.3%");
    // 口径必须写清楚（分子分母）
    await expect(page.getByTestId("gbc-quality-metric-success-rate")).toContainText("error 终态任务占比");
    await expect(page.getByTestId("gbc-quality-metric-success-rate")).toContainText("累计口径");

    await expect(page.getByTestId("gbc-quality-metric-review-required")).toContainText("4");
    await expect(page.getByTestId("gbc-quality-metric-evidence-rate")).toContainText("100.0%");

    // 失败阶段分布：真实归因（2 个失败任务分别归 PDF 解析与规则与 AI 分析）
    const stagesList = page.getByTestId("gbc-quality-failure-stages-list");
    await expect(stagesList).toContainText("PDF 解析");
    await expect(stagesList).toContainText("规则与 AI 分析");

    // 门禁清单四条 + 结论
    await expect(page.getByTestId("gbc-quality-gates-table")).toBeVisible();
    await expect(page.getByTestId("gbc-quality-gate-report_id_uniqueness")).toBeVisible();
    await expect(page.getByTestId("gbc-quality-gate-page_coverage")).toBeVisible();
    await expect(page.getByTestId("gbc-quality-gate-evidence_completeness")).toBeVisible();
    await expect(page.getByTestId("gbc-quality-gate-unknown_report_kind")).toBeVisible();
    await expect(page.getByTestId("gbc-quality-gate-verdict")).toContainText("仅覆盖结构性指标");
  });

  test("REGRESSION: page must never render 召回率/精确率/Golden Corpus/OCR/准确率", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, { isAdmin: true });
    await page.goto("/quality");

    const bodyText = await page.locator("body").innerText();
    expect(bodyText).not.toContain("召回率");
    expect(bodyText).not.toContain("精确率");
    expect(bodyText).not.toContain("Golden Corpus");
    expect(bodyText).not.toContain("OCR 成功率");
    expect(bodyText).not.toContain("OCR");
    expect(bodyText).not.toContain("准确率");
  });

  test("REGRESSION: evidence rate with zero denominator shows em dash, never 100%", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, {
      isAdmin: true,
      metricsPayload: {
        jobs: { total: 5 },
        quality: {
          unknown_report_kind: { count: 0, ratio: 0 },
          review_required: { count: 0, ratio: 0 },
          unresolved_report_year: { count: 0, ratio: 0 },
          error_jobs: { count: 0, ratio: 0 },
          evidence_degraded_findings: 0,
          formal_issue_total: 0,
          // 分母为 0：后端红线是 completeness_rate = null
          evidence_completeness: {
            findings_total: 0,
            findings_complete: 0,
            completeness_rate: null,
            jobs_without_field: 5,
          },
        },
        report_id: { collision_count: 0 },
      },
      jobs: [],
    });
    await page.goto("/quality");

    const evidenceCard = page.getByTestId("gbc-quality-metric-evidence-rate");
    await expect(evidenceCard).toContainText("—");
    await expect(evidenceCard).not.toContainText("100%");
    // 对应门禁必须是无样本，不是通过
    await expect(page.getByTestId("gbc-quality-gate-status-evidence_completeness")).toContainText("无样本");
  });

  test("REGRESSION: no failed jobs shows empty state, no fabricated segments", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, {
      isAdmin: true,
      jobs: [{ job_id: "job-done", status: "done", page_coverage: 1.0, organization_id: "org-edu" }],
    });
    await page.goto("/quality");

    await expect(page.getByTestId("gbc-quality-failure-stages-empty")).toBeVisible();
    await expect(page.getByTestId("gbc-quality-failure-stages-empty")).toContainText("当前没有失败任务");
    await expect(page.getByTestId("gbc-quality-failure-stages-list")).toHaveCount(0);
  });

  test("structural gate limitation notice is prominent", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, { isAdmin: true });
    await page.goto("/quality");

    // 局限声明：对齐 docs/CI_BUSINESS_GATE.md 第 0 节口径
    const gatesCard = page.getByTestId("gbc-quality-gates-card");
    await expect(gatesCard).toContainText("仅覆盖结构性指标");
    await expect(gatesCard).toContainText("全绿不等于业务质量达标");
  });

  test("disabled metrics notes list all three items with references", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, { isAdmin: true });
    await page.goto("/quality");

    const disabledCard = page.getByTestId("gbc-quality-disabled-metrics-card");
    await expect(disabledCard).toBeVisible();
    await expect(page.getByTestId("gbc-quality-disabled-scan_text_recognition")).toContainText("扫描件文字识别");
    await expect(page.getByTestId("gbc-quality-disabled-annotated_corpus_regression")).toContainText("人工标注语料回归");
    await expect(page.getByTestId("gbc-quality-disabled-recognition_correctness")).toContainText("识别正确性");
    await expect(disabledCard).toContainText("docs/RELEASE_ACCEPTANCE_2026-08-27.md");
  });

  test("REGRESSION: non-admin is redirected away from /quality", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, { isAdmin: false });
    await page.goto("/quality");

    await expect(page).toHaveURL(/\/workbench/);
    // 被拦后不得渲染质量管理内容
    await expect(page.getByTestId("gbc-quality-page")).toHaveCount(0);
  });

  test("REGRESSION: metrics endpoint failure degrades gracefully without page error", async ({ page }) => {
    const pageErrors: string[] = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, { isAdmin: true, metricsStatus: 403 });
    await page.goto("/quality");

    await expect(page.getByTestId("gbc-quality-metrics-unavailable")).toBeVisible();
    await expect(page.getByTestId("gbc-quality-metrics-unavailable")).toContainText("指标端点暂不可用");
    // 依赖 metrics 的卡片显示 —（未知），不是 0 或编造值
    await expect(page.getByTestId("gbc-quality-metric-success-rate")).toContainText("—");
    expect(pageErrors).toEqual([]);
  });

  test("REGRESSION: empty job sample shows unknown coverage, never 0% or 100%", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installQualityMocks(page, { isAdmin: true, jobs: [] });
    await page.goto("/quality");

    const coverageCard = page.getByTestId("gbc-quality-metric-page-coverage");
    await expect(coverageCard).toContainText("—");
    await expect(coverageCard).not.toContainText("0%");
    await expect(coverageCard).not.toContainText("100%");
  });
});
