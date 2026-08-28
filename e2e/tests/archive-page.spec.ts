import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 9：导出归档页 e2e 验证（整改包能力从旧单体迁移到 /archive）。
 *
 * 覆盖范围：
 * 1. 旧 URL 重定向：/viewer/gbc-ui-demo?page=archive → /archive（老链接不 404）；
 * 2. 全流程能力等价：已确认问题 → 一键生成整改包（create_package 请求体
 *    与旧单体同口径）→ 整改包列表 → 下载 ZIP（/api/reports/download-batch）；
 * 3. 子集打包：勾选部分已确认问题时只打包勾选项（旧"问题处理台勾选打包"
 *    的能力在新 UI 不丢失）；
 * 4. 反例：无已确认问题时不得生成空整改包（按钮禁用 + 明确提示，不发请求）。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

interface MockWorkflowState {
  issues: Record<string, Record<string, unknown>>;
  packages: Array<Record<string, unknown>>;
}

interface ArchiveMockState {
  workflow: MockWorkflowState;
  workflowPosts: Array<Record<string, unknown>>;
  batchDownloads: Array<{ job_ids: string[] }>;
}

function confirmedIssue(jobId: string, issueId: string, title: string, orgName: string) {
  return {
    key: `${jobId}::${issueId}`,
    job_id: jobId,
    issue_id: issueId,
    status: "confirmed",
    title,
    severity: "high",
    page: 12,
    organization_id: "dept-caizheng",
    organization_name: orgName,
    note: null,
    updated_at: "2026-08-28T10:00:00Z",
  };
}

function initialState(): ArchiveMockState {
  return {
    workflow: {
      issues: {
        "job-final-1::FIN-1": confirmedIssue("job-final-1", "FIN-1", "预算分项合计与公开总额不一致", "上海市普陀区财政局"),
        "job-final-1::FIN-2": confirmedIssue("job-final-1", "FIN-2", "三公经费合计不等于分项之和", "上海市普陀区财政局"),
      },
      packages: [],
    },
    workflowPosts: [],
    batchDownloads: [],
  };
}

async function installArchiveMocks(page: Page, state: ArchiveMockState) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const method = req.method().toUpperCase();
    const url = new URL(req.url());
    const path = url.pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

    if (path === "/api/auth/me") {
      return json({ user: { username: "e2e-user", is_admin: true } });
    }
    if (path === "/api/health") {
      return json({ status: "ok" });
    }
    if (path === "/api/jobs") {
      return json([]);
    }
    if (path === "/api/organizations") {
      return json({ tree: [{ id: "dept-caizheng", name: "上海市普陀区财政局", level: "department", children: [] }] });
    }

    if (path === "/api/workflow") {
      if (method === "POST") {
        const payload = req.postDataJSON() as Record<string, unknown>;
        state.workflowPosts.push(payload);
        if (payload.action === "create_package") {
          const issueKeys = Array.isArray(payload.issue_keys) ? payload.issue_keys.map(String) : [];
          const jobIds = Array.isArray(payload.job_ids) ? payload.job_ids.map(String) : [];
          for (const key of issueKeys) {
            const record = state.workflow.issues[key];
            if (record) {
              record.status = "in_package";
            }
          }
          const now = new Date().toISOString();
          const packageId = `pkg-e2e-${state.workflow.packages.length + 1}`;
          const createdPackage = {
            id: packageId,
            name: String(payload.name ?? packageId),
            organization_id: payload.organization_id ?? null,
            organization_name: payload.organization_name ?? null,
            job_ids: jobIds,
            issue_keys: issueKeys,
            status: "ready",
            created_at: now,
            updated_at: now,
          };
          state.workflow.packages.push(createdPackage);
          return json({ state: state.workflow, package: createdPackage });
        }
        return json(state.workflow);
      }
      return json(state.workflow);
    }

    if (path === "/api/reports/download-batch" && method === "POST") {
      const body = req.postDataJSON() as { job_ids?: string[] };
      state.batchDownloads.push({ job_ids: Array.isArray(body.job_ids) ? body.job_ids : [] });
      return route.fulfill({
        status: 200,
        contentType: "application/zip",
        body: Buffer.from("PK\x03\x04mock-zip", "binary"),
      });
    }

    return json({});
  });
}

test.describe("Archive page (Task 9)", () => {
  test("old archive deep link /viewer/gbc-ui-demo?page=archive redirects to /archive", async ({ page }) => {
    const state = initialState();
    await page.context().addCookies([sessionCookie]);
    await installArchiveMocks(page, state);

    await page.goto("/viewer/gbc-ui-demo?page=archive");
    await expect(page).toHaveURL(/\/archive$/, { timeout: 15_000 });
    await expect(page.getByTestId("gbc-archive-page")).toBeVisible({ timeout: 15_000 });
  });

  test("full flow: confirmed issues → create package (old-equivalent payload) → package row → download ZIP", async ({
    page,
  }) => {
    const state = initialState();
    await page.context().addCookies([sessionCookie]);
    await installArchiveMocks(page, state);
    await page.goto("/archive");

    await expect(page.getByTestId("gbc-archive-page")).toBeVisible({ timeout: 15_000 });

    // KPI：待生成整改包 = 2（两个已确认问题），全部来自工作流状态
    await expect(page.getByTestId("gbc-archive-confirmed-count")).toContainText("2");
    // 状态已加载但尚无整改包：真实的 0（不是"—"，那属于未加载态）
    await expect(page.getByTestId("gbc-archive-package-count")).toContainText("0");

    // 已确认问题清单可见且默认全选
    const check1 = page.getByTestId("gbc-archive-issue-check-job-final-1::FIN-1");
    const check2 = page.getByTestId("gbc-archive-issue-check-job-final-1::FIN-2");
    await expect(check1).toBeChecked();
    await expect(check2).toBeChecked();

    // 子集打包：取消勾选 FIN-2，只打包 FIN-1（旧"勾选子集生成"能力不丢失）
    await check2.uncheck();
    await page.getByTestId("gbc-archive-create-package").click();

    await expect.poll(() => state.workflowPosts.length, { timeout: 10_000 }).toBe(1);
    const createPayload = state.workflowPosts[0];
    expect(createPayload.action).toBe("create_package");
    expect(createPayload.name).toBe("上海市普陀区财政局整改包");
    expect(createPayload.issue_keys).toEqual(["job-final-1::FIN-1"]);
    expect(createPayload.job_ids).toEqual(["job-final-1"]);

    // 整改包列表：名称/单位/问题数/任务数/状态
    const packageRow = page.getByTestId("gbc-archive-package-row-pkg-e2e-1");
    await expect(packageRow).toBeVisible({ timeout: 10_000 });
    await expect(packageRow).toContainText("上海市普陀区财政局整改包");
    await expect(packageRow).toContainText("上海市普陀区财政局");
    await expect(packageRow).toContainText("问题清单 / 证据页 / 处理状态 / 报告链接");
    await expect(packageRow).toContainText("可下载");
    await expect(page.getByTestId("gbc-archive-status")).toContainText("已生成");

    // 下载：POST /api/reports/download-batch {job_ids}，浏览器收到 zip 下载事件
    const download = page.waitForEvent("download");
    await page.getByTestId("gbc-archive-download-pkg-e2e-1").click();
    const downloadEvent = await download;
    expect(downloadEvent.suggestedFilename()).toBe("上海市普陀区财政局整改包.zip");
    await expect.poll(() => state.batchDownloads.length, { timeout: 10_000 }).toBe(1);
    expect(state.batchDownloads[0].job_ids).toEqual(["job-final-1"]);

    // KPI 联动：打包后已归档问题 = 1，可下载结果 = 1
    await expect(page.getByTestId("gbc-archive-inpackage-count")).toContainText("1");
    await expect(page.getByTestId("gbc-archive-package-count")).toContainText("1");
  });

  test("REGRESSION: with no confirmed issues the create button stays disabled and no request is fired", async ({
    page,
  }) => {
    const state = initialState();
    state.workflow.issues = {};
    await page.context().addCookies([sessionCookie]);
    await installArchiveMocks(page, state);
    await page.goto("/archive");

    await expect(page.getByTestId("gbc-archive-empty-hint")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("gbc-archive-empty-hint")).toContainText("不会生成空整改包");
    const button = page.getByTestId("gbc-archive-create-package");
    await expect(button).toBeDisabled();
    // 禁用即不可触发；即便绕过 UI 强行点击也不得发出请求
    expect(state.workflowPosts.length).toBe(0);

    // 整改包列表空态文案
    await expect(page.getByTestId("gbc-archive-package-empty")).toBeVisible();
  });

  test("unchecking every confirmed issue disables creation (empty subset is not a package)", async ({ page }) => {
    const state = initialState();
    await page.context().addCookies([sessionCookie]);
    await installArchiveMocks(page, state);
    await page.goto("/archive");

    await expect(page.getByTestId("gbc-archive-issue-check-job-final-1::FIN-1")).toBeChecked({ timeout: 15_000 });
    await page.getByTestId("gbc-archive-toggle-all").click(); // 全不选
    await expect(page.getByTestId("gbc-archive-issue-check-job-final-1::FIN-1")).not.toBeChecked();
    await expect(page.getByTestId("gbc-archive-create-package")).toBeDisabled();
    expect(state.workflowPosts.length).toBe(0);
  });
});
