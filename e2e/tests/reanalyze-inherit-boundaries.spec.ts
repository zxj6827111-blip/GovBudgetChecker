import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * 独立复验 RI-01 / RI-02 的真实浏览器回归：
 * - 继承不可用（历史未知 / 原模型删除）时保持未选择、禁用提交，等用户主动选择第二项；
 * - 部门页批量重查把后端 inheritance_warnings 逐条展示给用户。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const capabilities = {
  enabled: true,
  default_use_ai: false,
  active_profile_id: "model-b",
  profiles: [{ id: "model-b", name: "当前模型B", model: "model-b" }],
};

function optionsPayload(kind: "normal" | "history-unknown" | "model-deleted") {
  const base = {
    job_id: "job-001",
    inherit: {
      available: true,
      unknown_fields: [] as string[],
      message: null as string | null,
      mode: "dual",
      use_local_rules: true,
      use_ai_assist: false,
      ai_profile_id: null as string | null,
      ai_configuration: null as Record<string, unknown> | null,
    },
    last_execution: {
      status: "done",
      analysis_conclusion: null,
      ai_requested: false,
      local_state: "completed",
      ai_state: "skipped",
    },
    model: {
      state: "not_applicable",
      message: null as string | null,
      profile: null as Record<string, unknown> | null,
    },
    capabilities,
  };
  if (kind === "history-unknown") {
    Object.assign(base.inherit, {
      available: false,
      unknown_fields: ["mode", "use_local_rules", "use_ai_assist"],
      message: "该材料没有已记录的上次检查方式，请显式选择本次检查方式",
      mode: null,
      use_local_rules: null,
      use_ai_assist: null,
    });
  }
  if (kind === "model-deleted") {
    Object.assign(base.inherit, {
      use_ai_assist: true,
      ai_profile_id: "model-a",
      ai_configuration: { id: "model-a", name: "原模型A", model: "model-a" },
    });
    base.model = {
      state: "deleted",
      message: "上次使用的模型「原模型A」已删除，请指定本次模型",
      profile: null,
    };
  }
  return base;
}

async function installTaskMocks(page: Page, kind: "normal" | "history-unknown" | "model-deleted") {
  const bodies: Array<Record<string, unknown>> = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/auth/me") {
      return json({ user: { username: "e2e-user", display_name: "E2E User", is_admin: false } });
    }
    if (path === "/api/jobs/job-001" && method === "GET") {
      return json({
        job_id: "job-001",
        filename: "demo-report.pdf",
        status: "completed",
        report_year: 2025,
        report_kind: "budget",
        organization_id: "dept-001",
        organization_name: "测试部门",
        updated_ts: 1_710_000_000,
        result: { issues: [] },
      });
    }
    if (path === "/api/jobs/job-001/structured-ingest" && method === "GET") {
      return json({ status: "done" });
    }
    if (path === "/api/jobs/job-001/reanalyze-options" && method === "GET") {
      return json(optionsPayload(kind));
    }
    if (path === "/api/jobs/job-001/reanalyze" && method === "POST") {
      bodies.push(request.postDataJSON() as Record<string, unknown>);
      return json({ job_id: "job-001", status: "queued" });
    }
    if (path.startsWith("/api/files/")) {
      return route.fulfill({ status: 204, body: "" });
    }
    return json({});
  });
  return bodies;
}

for (const kind of ["history-unknown", "model-deleted"] as const) {
  test(`继承不可用时（${kind}）不自动改选，等用户主动选择第二项`, async ({ page }) => {
    test.setTimeout(60_000);
    await page.context().addCookies([sessionCookie]);
    const bodies = await installTaskMocks(page, kind);

    await page.goto("/task/job-001");
    const button = page.getByTestId("task-reanalyze-button");
    await expect(button).toBeEnabled({ timeout: 20_000 });
    await button.click();

    const inherit = page.getByTestId("reanalyze-method-picker-inherit");
    const ai = page.getByTestId("reanalyze-method-picker-ai");
    const confirm = page.getByTestId("reanalyze-method-confirm");
    await expect(page.getByTestId("reanalyze-method-dialog")).toBeVisible();
    await expect(page.getByTestId("reanalyze-method-picker-inherit-blocked")).toBeVisible();
    // 保留默认选项但置为不可选：既没有替用户改选 AI，也阻断了继承提交。
    await expect(inherit).toBeChecked();
    await expect(inherit).toBeDisabled();
    await expect(ai).not.toBeChecked();
    await expect(confirm).toBeDisabled();
    expect(bodies, "用户未操作时不得提交").toHaveLength(0);

    await ai.check();
    await expect(confirm).toBeEnabled();
    await confirm.click();
    await expect.poll(() => bodies).toEqual([{ check_method: "ai", ai_profile_id: "model-b" }]);
  });
}

test("继承可用时默认选中按上次方式并可直接提交（对照）", async ({ page }) => {
  test.setTimeout(60_000);
  await page.context().addCookies([sessionCookie]);
  const bodies = await installTaskMocks(page, "normal");

  await page.goto("/task/job-001");
  const button = page.getByTestId("task-reanalyze-button");
  await expect(button).toBeEnabled({ timeout: 20_000 });
  await button.click();

  await expect(page.getByTestId("reanalyze-method-picker-inherit")).toBeChecked();
  await expect(page.getByTestId("reanalyze-method-picker-ai")).not.toBeChecked();
  await expect(page.getByTestId("reanalyze-method-confirm")).toBeEnabled();
  await page.getByTestId("reanalyze-method-confirm").click();
  await expect.poll(() => bodies).toEqual([{ check_method: "inherit" }]);
});

type DepartmentJobFixture = {
  job_id: string;
  filename: string;
  status: string;
  report_kind: string;
  report_year: number;
  organization_id: string;
  organization_name: string;
  merged_issue_total: number;
};

async function installDepartmentMocks(
  page: Page,
  options: {
    orgId: string;
    orgName: string;
    jobs: DepartmentJobFixture[];
    warningFor?: (index: number) => string | null;
  },
): Promise<string[]> {
  const dialogMessages: string[] = [];
  page.on("dialog", async (dialog) => {
    dialogMessages.push(dialog.message());
    await dialog.accept();
  });
  const warnings = options.jobs.map((_, index) => options.warningFor?.(index) ?? null);

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const method = request.method().toUpperCase();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/auth/me") {
      return json({ user: { username: "e2e-user", display_name: "E2E User", is_admin: false } });
    }
    if (path === "/api/organizations/list") {
      return json({ organizations: [{ id: options.orgId, name: options.orgName, level: "department" }] });
    }
    if (path === `/api/organizations/${options.orgId}/jobs`) {
      return json({ jobs: options.jobs, total: options.jobs.length, limit: 80, offset: 0 });
    }
    const match = path.match(/^\/api\/jobs\/([^/]+)\/reanalyze$/);
    if (match && method === "POST") {
      const index = options.jobs.findIndex((job) => job.job_id === decodeURIComponent(match[1]));
      const warning = index >= 0 ? warnings[index] : null;
      return json({
        job_id: decodeURIComponent(match[1]),
        status: "queued",
        ...(warning ? { inheritance_warnings: [warning] } : {}),
      });
    }
    return json({});
  });
  return dialogMessages;
}

test("部门页批量重查展示每条模型配置变化告警", async ({ page }) => {
  test.setTimeout(60_000);
  await page.context().addCookies([sessionCookie]);
  const warning = "原模型配置已变化（model），按上次方式执行将使用当前配置";
  const jobs: DepartmentJobFixture[] = [
    {
      job_id: "dept-job-1",
      filename: "甲单位预算.pdf",
      status: "done",
      report_kind: "budget",
      report_year: 2025,
      organization_id: "dept-001",
      organization_name: "测试部门",
      merged_issue_total: 0,
    },
    {
      job_id: "dept-job-2",
      filename: "乙单位预算.pdf",
      status: "done",
      report_kind: "budget",
      report_year: 2025,
      organization_id: "dept-001",
      organization_name: "测试部门",
      merged_issue_total: 0,
    },
  ];
  const dialogMessages = await installDepartmentMocks(page, {
    orgId: "dept-001",
    orgName: "测试部门",
    jobs,
    warningFor: (index) => (index === 0 ? warning : null),
  });

  await page.goto("/department/dept-001");
  await page.getByTestId("job-select-dept-job-1").check();
  await page.getByTestId("job-select-dept-job-2").check();
  await page.getByTestId("batch-reanalyze-button").click();
  await expect(page.getByTestId("reanalyze-method-dialog")).toBeVisible();
  await page.getByTestId("reanalyze-method-confirm").click();

  await expect.poll(() => dialogMessages.join("\n")).toContain("模型配置变化 1 份");
  const warningList = page.getByTestId("department-batch-reanalyze-warnings");
  await expect(warningList).toContainText("甲单位预算.pdf");
  await expect(warningList).toContainText("原模型配置已变化");
  await expect(warningList.locator("li")).toHaveCount(1);
  await expect(page.getByTestId("department-batch-reanalyze-failures")).toHaveCount(0);
});

test("部门页批量 6 条告警全部可查看，不做截断", async ({ page }) => {
  test.setTimeout(60_000);
  await page.context().addCookies([sessionCookie]);
  const warningText = (index: number) => `REVIEW-WARNING-${index + 1}：原模型配置已变化`;
  const jobs: DepartmentJobFixture[] = Array.from({ length: 6 }, (_, index) => ({
    job_id: `review-limit-${index + 1}`,
    filename: `材料${index + 1}预算.pdf`,
    status: "done",
    report_kind: "budget",
    report_year: 2025,
    organization_id: "dept-review",
    organization_name: "独立复验部门",
    merged_issue_total: 0,
  }));
  const dialogMessages = await installDepartmentMocks(page, {
    orgId: "dept-review",
    orgName: "独立复验部门",
    jobs,
    warningFor: warningText,
  });

  await page.goto("/department/dept-review");
  for (const job of jobs) {
    await page.getByTestId(`job-select-${job.job_id}`).check();
  }
  await page.getByTestId("batch-reanalyze-button").click();
  await expect(page.getByTestId("reanalyze-method-dialog")).toBeVisible();
  await page.getByTestId("reanalyze-method-confirm").click();

  await expect.poll(() => dialogMessages.join("\n")).toContain("完整结果见页面");
  const result = page.getByTestId("department-batch-reanalyze-result");
  const warningList = page.getByTestId("department-batch-reanalyze-warnings");
  await expect(result).toBeVisible();
  await expect(warningList.locator("li")).toHaveCount(6);
  await expect(warningList).toContainText("材料6预算.pdf");
  await expect(warningList).toContainText("REVIEW-WARNING-6");
  await expect(result).not.toContainText("见队列状态");
  expect(await warningList.evaluate((element) => getComputedStyle(element).overflowY)).toBe("auto");
});
