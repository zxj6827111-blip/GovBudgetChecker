import { expect, test } from "../../app/node_modules/playwright/test";

/**
 * Task 15.3：关键流程 E2E —— 上传 → 等待 → 复核 → 导出。
 *
 * 为什么这条链要单独测：
 * 既有 e2e 分别覆盖了上传（gbc-ui-demo-upload）、报告动作（report-actions）、
 * 安全头（security-headers），但**没有一条用例把"上传后等分析跑完、再复核、再导出"
 * 串起来**。这条链恰好是 PLAN 第 15 节 Gate 5「上传、处理、复核和导出流程可用」的
 * 验收对象，也是最容易在改动状态模型（M1 引入 review_required）时被打断的路径。
 *
 * 两条用例：
 * 1. 完整闭环：上传 → 轮询等待（processing → done）→ 确认问题 → 导出 PDF；
 * 2. 反例：终态是 review_required 时，界面必须显示"需人工复核"，
 *    **不得**显示成"分析完成"——这正是整改前"虚假成功"的表现形式。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const JOB_ID = "job-full-flow-2026";
const FILENAME = "上海市普陀区某单位 2026 年度部门预算.pdf";
const RULE_ID = "C-001";

const organization = {
  id: "dept-full-flow",
  name: "上海市普陀区某单位",
  level: "department",
  parent_id: null,
};

function jobSummary(overrides: Record<string, unknown> = {}) {
  return {
    job_id: JOB_ID,
    filename: FILENAME,
    status: "processing",
    stage: "解析PDF内容",
    progress: 40,
    report_year: 2026,
    doc_type: "dept_budget",
    report_kind: "budget",
    organization_id: organization.id,
    organization_name: organization.name,
    organization_level: "department",
    created_ts: "2026-08-27T01:00:00Z",
    updated_ts: "2026-08-27T01:00:00Z",
    ...overrides,
  };
}

const finding = {
  id: "FIN-FULL-1",
  rule_id: RULE_ID,
  severity: "high",
  title: "三公经费合计不等于分项之和",
  message: "合计 35.20 万元，分项之和 33.00 万元",
  evidence: [{ page: 12, text: "合计 35.20，其中因公出国 10.00" }],
  location: { page: 12 },
};

test.describe("关键流程：上传 → 等待 → 复核 → 导出", () => {
  test("完成一次上传、等待分析结束、确认问题并导出 PDF", async ({ page }) => {
    test.setTimeout(120_000);

    // 状态机：轮询前两次返回 processing，之后返回 done —— 用来验证界面真的在等
    let analysisStarted = false;
    let jobsPolls = 0;
    let runCalls = 0;
    let workflowUpdates: Array<Record<string, unknown>> = [];
    let downloadCalls = 0;
    const POLLS_BEFORE_DONE = 2;

    const isFinished = () => analysisStarted && jobsPolls > POLLS_BEFORE_DONE;

    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const method = request.method().toUpperCase();
      const url = new URL(request.url());
      const path = url.pathname;
      const json = (body: unknown, status = 200) =>
        route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

      if (path === "/api/auth/me") {
        return json({ user: { username: "e2e-admin", is_admin: true } });
      }
      if (path === "/api/organizations") {
        return json({ tree: [{ ...organization, children: [] }] });
      }
      if (path === "/api/organizations/list") {
        return json({ organizations: [organization], total: 1 });
      }
      if (path === "/api/gbc-ui-demo/workflow") {
        if (method === "POST") {
          workflowUpdates.push(request.postDataJSON() as Record<string, unknown>);
          return json({ issues: {}, packages: [] });
        }
        return json({ issues: {}, packages: [] });
      }
      if (path === "/api/jobs") {
        if (!analysisStarted) {
          return json({ items: [], total: 0, limit: 500, offset: 0 });
        }
        jobsPolls += 1;
        const item = isFinished()
          ? jobSummary({ status: "done", stage: "完成", progress: 100 })
          : jobSummary();
        return json({ items: [item], total: 1, limit: 500, offset: 0 });
      }
      if (path === "/api/documents/preflight" && method === "POST") {
        return json({
          filename: FILENAME,
          report_year: 2026,
          doc_type: "dept_budget",
          report_kind: "budget",
          current: {
            organization_id: organization.id,
            organization_name: organization.name,
            level: "department",
            confidence: 0.97,
            match_basis: "cover",
          },
        });
      }
      if (path === "/api/documents/upload" && method === "POST") {
        return json({ job_id: JOB_ID });
      }
      if (path === `/api/documents/${JOB_ID}/run` && method === "POST") {
        runCalls += 1;
        analysisStarted = true;
        return json({ job_id: JOB_ID, status: "started" });
      }
      if (path === `/api/jobs/${JOB_ID}`) {
        if (!isFinished()) {
          // 分析中：还没有结果，界面不该显示任何问题
          return json(jobSummary());
        }
        return json({
          ...jobSummary({ status: "done", stage: "完成", progress: 100 }),
          analysis_conclusion: "findings_detected",
          page_coverage: 1.0,
          scanned_page_count: 0,
          result: { rule_findings: [finding] },
        });
      }
      if (path === `/api/jobs/${JOB_ID}/structured-ingest`) {
        return json({ status: "done", review_item_count: 0, review_items: [] });
      }
      if (path === "/api/reports/download" && method === "GET") {
        downloadCalls += 1;
        expect(url.searchParams.get("job_id")).toBe(JOB_ID);
        expect(url.searchParams.get("format")).toBe("pdf");
        return route.fulfill({
          status: 200,
          contentType: "application/pdf",
          headers: { "content-disposition": 'attachment; filename="full-flow.pdf"' },
          body: "%PDF-1.4\n% e2e full flow\n%%EOF\n",
        });
      }
      if (path.startsWith("/api/files/")) {
        return route.fulfill({
          status: 200,
          contentType: "image/png",
          body: Buffer.from(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=",
            "base64",
          ),
        });
      }
      return json({});
    });

    // ---- 第 1 步：上传 ----
    await page.goto("/viewer/gbc-ui-demo");
    await page.getByTestId("gbc-nav-upload").click();
    await page.getByTestId("gbc-upload-open-batch").click();
    await page.getByTestId("batch-upload-file-input").setInputFiles({
      name: FILENAME,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
    });
    await expect(page.getByTestId("batch-start")).toBeEnabled({ timeout: 20_000 });
    await page.getByTestId("batch-start").click();
    await expect(page.getByTestId("batch-upload-modal")).toBeHidden({ timeout: 30_000 });
    expect(runCalls).toBe(1);

    // ---- 第 2 步：等待分析结束 ----
    // 详情页已打开，但分析未完成时不能出现任何问题条目（反例断言：
    // 若此时就显示问题，说明界面在拿旧数据或伪造结果）
    await expect(page.getByTestId("gbc-detail-export")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(RULE_ID, { exact: false })).toHaveCount(0);

    // 界面每 5s 轮询一次；轮询到 done 之后问题才会出现。
    // 这一步同时证明"等待"不是靠固定 sleep，而是真的跟着状态变化走。
    await expect(page.getByText(RULE_ID, { exact: false }).first()).toBeVisible({
      timeout: 60_000,
    });
    expect(jobsPolls).toBeGreaterThan(POLLS_BEFORE_DONE);

    // ---- 第 3 步：复核（确认问题）----
    await page.getByTestId("gbc-detail-confirm").click();
    await expect
      .poll(() => workflowUpdates.length, { timeout: 20_000 })
      .toBeGreaterThan(0);
    const update = workflowUpdates[0];
    expect(update.action).toBe("update_issue");
    expect(update.job_id).toBe(JOB_ID);
    expect(update.status).toBe("confirmed");

    // ---- 第 4 步：导出 PDF ----
    const downloadPromise = page.waitForEvent("download");
    await page.getByTestId("gbc-detail-export").click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toContain("report.pdf");
    await expect.poll(() => downloadCalls, { timeout: 20_000 }).toBe(1);
  });

  test("终态 review_required 必须显示为需人工复核，而不是分析完成", async ({ page }) => {
    test.setTimeout(90_000);
    const reviewJobId = "job-review-required-2026";

    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const json = (body: unknown, status = 200) =>
        route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

      const reviewJob = {
        ...jobSummary({
          status: "review_required",
          stage: "完成",
          progress: 100,
        }),
        job_id: reviewJobId,
        analysis_conclusion: "incomplete",
        quality_status: "review_required",
        page_coverage: 0.42,
        scanned_page_count: 7,
        review_reason_codes: ["low_page_coverage", "scanned_pages_detected"],
      };

      if (path === "/api/auth/me") {
        return json({ user: { username: "e2e-admin", is_admin: true } });
      }
      if (path === `/api/jobs/${reviewJobId}`) {
        return json({ ...reviewJob, result: { rule_findings: [] } });
      }
      if (path === `/api/jobs/${reviewJobId}/structured-ingest`) {
        return json({ status: "done", review_item_count: 0, review_items: [] });
      }
      if (path.startsWith("/api/files/")) {
        return route.fulfill({ status: 404, body: "" });
      }
      return json({});
    });

    await page.goto(`/task/${reviewJobId}`);

    // 正例：新状态被如实展示
    await expect(page.getByText("需人工复核").first()).toBeVisible({ timeout: 30_000 });
    // 反例：绝不能出现"分析完成"字样——那正是整改前的虚假成功
    await expect(page.getByText("分析完成", { exact: false })).toHaveCount(0);
  });
});
