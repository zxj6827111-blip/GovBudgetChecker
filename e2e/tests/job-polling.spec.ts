import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * 修复 1（任务状态轮询）e2e 验证。
 *
 * 覆盖范围（对照任务书修复 1 测试要求）：
 * 1. 模拟任务从 processing 变 done，断言列表无需手动刷新即更新（本次回归的直接根因）；
 * 2. 全部终态后轮询停止（不再打 /api/jobs）；
 * 3. 请求失败（500）不得使页面崩溃或永久停止轮询，且失败状态如实呈现；
 * 4. 手动刷新入口真实生效。
 *
 * 注意：/queue 页面上有两个 /api/jobs 消费者——页面本身（轮询）与侧边栏角标
 * （挂载时拉一次）。因此 mock 不能用"按调用次序弹出响应"的队列（两个消费者会
 * 互相错位消费），改用"相位"模型：mock 按当前相位返回数据，测试在观察到初始
 * 状态后显式翻转相位，与具体哪个消费者发了请求无关。
 *
 * 轮询间隔为 5 秒（jobPolling.ACTIVE_POLL_INTERVAL_MS），等待预算按 ≥6 秒给足。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const PROCESSING_JOB = {
  job_id: "job-polling-e2e",
  filename: "轮询验证_区财政局2025年决算报告.pdf",
  status: "processing",
  organization_id: "org-fin",
  organization_name: "区财政局",
  report_year: 2025,
  report_kind: "final",
  merged_issue_total: 0,
  stage_progress: { phase: "rule_ai_analysis", phase_label: "规则与 AI 分析", percent: 55, raw_stage: "分析中" },
};

const DONE_JOB = {
  ...PROCESSING_JOB,
  status: "done",
  merged_issue_total: 3,
  stage_progress: { phase: "quality_gate", phase_label: "质量门禁", percent: 100, raw_stage: "完成" },
};

type JobsPhase = "processing" | "done";

interface JobsMockState {
  phase: JobsPhase;
  /** 相位为 processing 时的失败注入：true 表示所有 /api/jobs 请求返回 500。 */
  failing: boolean;
  jobsRequestCount: number;
}

async function installQueuePollingMocks(page: Page, state: JobsMockState) {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: false } }),
      });
      return;
    }
    if (path === "/api/health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
      return;
    }
    if (path === "/api/jobs") {
      state.jobsRequestCount += 1;
      if (state.failing) {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "mocked server error" }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([state.phase === "done" ? DONE_JOB : PROCESSING_JOB]),
      });
      return;
    }
    if (path === "/api/organizations") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          tree: [{ id: "org-fin", name: "区财政局", level: "department", children: [] }],
        }),
      });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Job status polling (fix 1)", () => {
  test("REGRESSION: a job transitioning processing -> done updates the list without any manual refresh", async ({
    page,
  }) => {
    test.setTimeout(40_000);
    const state: JobsMockState = { phase: "processing", failing: false, jobsRequestCount: 0 };
    await page.context().addCookies([sessionCookie]);
    await installQueuePollingMocks(page, state);
    await page.goto("/queue");

    // 页面加载时的真实状态：正在分析
    const row = page.getByTestId("gbc-workbench-queue-row-job-polling-e2e");
    await expect(row).toBeVisible();
    await expect(row).toContainText("正在分析");

    // 标记"本次页面会话"：若发生整页刷新/导航该标记会消失，
    // 以此证明状态更新来自轮询而非重新加载。
    await page.evaluate(() => {
      (window as unknown as { __pollingSessionMarker: boolean }).__pollingSessionMarker = true;
    });

    // 后端任务完成（mock 翻转相位）：下一次 /api/jobs 轮询（≤5 秒）必须让
    // 列表自动更新到终态，无需任何手动刷新。
    state.phase = "done";
    await expect(row).toContainText("分析完成", { timeout: 15_000 });
    await expect(row).toContainText("3");

    const marker = await page.evaluate(
      () => (window as unknown as { __pollingSessionMarker?: boolean }).__pollingSessionMarker,
    );
    expect(marker).toBe(true);
  });

  test("REGRESSION: polling stops after all jobs reach a terminal state; manual refresh still works", async ({
    page,
  }) => {
    test.setTimeout(40_000);
    const state: JobsMockState = { phase: "done", failing: false, jobsRequestCount: 0 };
    await page.context().addCookies([sessionCookie]);
    await installQueuePollingMocks(page, state);
    await page.goto("/queue");

    await expect(page.getByTestId("gbc-workbench-queue-row-job-polling-e2e")).toBeVisible();
    // 等到"最后更新"出现说明页面自身的刷新链路已成功走完；再留 1 秒让侧边栏
    // 角标的一次性拉取（含 StrictMode 双挂载）彻底落定，避免计入基线。
    await expect(page.getByTestId("gbc-queue-refresh-synced-at")).toContainText("最后更新", {
      timeout: 10_000,
    });
    await page.waitForTimeout(1_000);

    const countAfterSettle = state.jobsRequestCount;
    expect(countAfterSettle).toBeGreaterThan(0);

    // 等待超过一个活跃轮询周期：终态后不得再有任何 /api/jobs 请求。
    await page.waitForTimeout(6_500);
    expect(state.jobsRequestCount).toBe(countAfterSettle);

    // 手动刷新是终态停轮询后的显式恢复入口：点击后必须真实再拉一次。
    await page.getByTestId("gbc-queue-refresh-button").click();
    await expect
      .poll(() => state.jobsRequestCount, { timeout: 10_000 })
      .toBeGreaterThan(countAfterSettle);
  });

  test("REGRESSION: a failed refresh neither crashes the page nor stops polling, and the failure is surfaced", async ({
    page,
  }) => {
    test.setTimeout(60_000);
    const state: JobsMockState = { phase: "processing", failing: true, jobsRequestCount: 0 };
    await page.context().addCookies([sessionCookie]);
    await installQueuePollingMocks(page, state);
    await page.goto("/queue");

    // 首次加载失败：页面不崩溃，且失败状态如实呈现（不静默吞掉）。
    await expect(page.getByTestId("gbc-queue-page")).toBeVisible();
    await expect(page.getByTestId("gbc-queue-refresh-error")).toContainText(
      "上次刷新失败",
      { timeout: 10_000 },
    );

    // 轮询未被失败中断：停止注入失败后，下一次请求成功，列表正常渲染。
    state.failing = false;
    const row = page.getByTestId("gbc-workbench-queue-row-job-polling-e2e");
    await expect(row).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("gbc-queue-refresh-error")).toHaveCount(0);

    // 后续轮询继续工作：processing -> done 自动更新。
    state.phase = "done";
    await expect(row).toContainText("分析完成", { timeout: 15_000 });
  });
});


test.describe("Pending-analysis status & start-analysis entry (fix 3)", () => {
  const UPLOADED_JOB = {
    job_id: "job-uploaded-e2e",
    filename: "待分析验证_区教育局2026年度部门预算.pdf",
    status: "uploaded",
    stage: "uploaded",
    organization_id: "org-fin",
    organization_name: "区教育局",
    report_year: 2026,
    report_kind: "budget",
  };

  test("REGRESSION: an uploaded job shows 待分析 (never 分析中/处理中) with a working 开始分析 entry", async ({
    page,
  }) => {
    test.setTimeout(40_000);
    const analyzeCalls: string[] = [];
    await page.context().addCookies([sessionCookie]);
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ user: { username: "e2e-user", is_admin: false } }),
        });
        return;
      }
      if (path === "/api/health") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
        return;
      }
      if (path === "/api/jobs") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([UPLOADED_JOB]),
        });
        return;
      }
      if (path === "/api/organizations") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ tree: [{ id: "org-fin", name: "区财政局", level: "department", children: [] }] }),
        });
        return;
      }
      if (path.startsWith("/api/analyze/")) {
        analyzeCalls.push(path.split("/").pop() ?? "");
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ started: true }) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });
    await page.goto("/queue");

    const row = page.getByTestId("gbc-workbench-queue-row-job-uploaded-e2e");
    await expect(row).toBeVisible();

    // 核心反例：uploaded 不得显示成任何暗示正在处理的文案。
    const rowText = await row.innerText();
    expect(rowText).not.toMatch(/分析中|正在分析|处理中|执行中|分析阶段/);
    await expect(row).toContainText("待分析");

    // 复核入口禁用，但必须给出"尚未开始分析"的明确原因（而非笼统的"未完成"）。
    const reviewEntry = page.getByTestId("gbc-workbench-queue-review-job-uploaded-e2e");
    await expect(reviewEntry).toBeDisabled();
    await expect(reviewEntry).toHaveAttribute("title", /尚未开始分析/);

    // 「开始分析」入口仅对 uploaded 态出现，且真实触发 POST /api/analyze/{job_id}。
    const startButton = page.getByTestId("gbc-workbench-queue-start-job-uploaded-e2e");
    await expect(startButton).toBeVisible();
    await startButton.click();
    await expect.poll(() => analyzeCalls.length, { timeout: 10_000 }).toBe(1);
    expect(analyzeCalls[0]).toBe("job-uploaded-e2e");
  });
});
