import { expect, test, type Page } from "../../app/node_modules/playwright/test";

/**
 * Task 5：上传中心 e2e 验证。
 *
 * 覆盖范围（对照任务书 5 测试要求）：
 * 1. 反例：拖拽区显示的上传限制来自 /api/config，不是原型图示例值 200MB；
 * 2. 三步归属必填与联动约束：未选完必填项不得允许提交；
 *    部门汇总文件不应要求选单位、单位文件必须选单位；
 * 3. 分析前确认横幅文案必须保留（M1 语义的用户侧表达）；
 * 4. 待上传文件列表显示真实预检状态（校验通过/需要确认）。
 */

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

interface MockOptions {
  maxUploadMb?: number;
  maxUploadPages?: number;
}

const SAMPLE_ORG_TREE = [
  {
    id: "dept-caizheng",
    name: "上海市普陀区财政局",
    level: "department",
    parent_id: null,
    children: [
      { id: "unit-caizheng-head", name: "上海市普陀区财政局", level: "unit", parent_id: "dept-caizheng", children: [] },
      { id: "unit-guoku", name: "上海市普陀区国库收付中心", level: "unit", parent_id: "dept-caizheng", children: [] },
    ],
  },
];

async function installUploadCenterMocks(page: Page, options: MockOptions = {}) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: true } }),
      });
      return;
    }

    if (path === "/api/health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
      return;
    }

    if (path === "/api/config") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          max_upload_mb: options.maxUploadMb ?? 30,
          max_upload_pages: options.maxUploadPages ?? 800,
        }),
      });
      return;
    }

    if (path === "/api/organizations") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ tree: SAMPLE_ORG_TREE, total: 1 }),
      });
      return;
    }

    if (path === "/api/documents/preflight") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          filename: "2026年度部门预算公开说明.pdf",
          report_year: 2026,
          doc_type: "dept_budget",
          report_kind: "budget",
          current: { organization_id: "dept-caizheng", organization_name: "上海市普陀区财政局", level: "department", confidence: 0.9 },
          suggestions: [],
          page_count: 48,
        }),
      });
      return;
    }

    if (path === "/api/jobs") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

/** 前置修复 1 专用：preflight 响应缺年份（低置信度场景），供确认闸门 e2e 测试使用。 */
async function installUploadCenterMocksWithMissingYear(page: Page, uploadRequests: Array<Record<string, unknown>>) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;

    if (path === "/api/auth/me") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ user: { username: "e2e-user", is_admin: true } }),
      });
      return;
    }
    if (path === "/api/health") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "ok" }) });
      return;
    }
    if (path === "/api/config") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ max_upload_mb: 30, max_upload_pages: 800 }),
      });
      return;
    }
    if (path === "/api/organizations") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ tree: SAMPLE_ORG_TREE, total: 1 }),
      });
      return;
    }
    if (path === "/api/documents/preflight") {
      // 关键：report_year 为 null，模拟"无法识别年份"的低置信度场景。
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          filename: "扫描件_预算执行情况说明.pdf",
          report_year: null,
          doc_type: "dept_budget",
          report_kind: "budget",
          current: { organization_id: "dept-caizheng", organization_name: "上海市普陀区财政局", level: "department", confidence: 0.9 },
          suggestions: [],
          page_count: 22,
        }),
      });
      return;
    }
    if (path === "/api/documents/upload") {
      const formData = req.postDataBuffer();
      // Playwright 的 route 无法直接拿到 multipart 字段值的结构化解析，因此这里
      // 用简单的文本查找断言请求体里确实带上了补齐后的年份，而不是解析完整
      // multipart（这是"补齐值真正进入上传请求"这条反例最直接的证据来源）。
      const bodyText = formData ? formData.toString("utf-8") : "";
      uploadRequests.push({ bodyText });
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ job_id: "job-created-by-e2e" }),
      });
      return;
    }
    if (path === "/api/jobs") {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([]) });
      return;
    }

    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
  });
}

test.describe("Upload center (Task 5)", () => {
  test("REGRESSION: dropzone shows the real configured upload limit, never the prototype's 200MB placeholder", async ({
    page,
  }) => {
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocks(page, { maxUploadMb: 30, maxUploadPages: 800 });
    await page.goto("/upload");

    const hint = page.getByTestId("gbc-upload-limit-hint");
    await expect(hint).toContainText("30 MB");
    await expect(hint).toContainText("800 页");
    await expect(hint).not.toContainText("200 MB");
  });

  test("dropzone reflects a different real config value when the backend limit changes", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocks(page, { maxUploadMb: 50, maxUploadPages: 1200 });
    await page.goto("/upload");

    const hint = page.getByTestId("gbc-upload-limit-hint");
    await expect(hint).toContainText("50 MB");
    await expect(hint).toContainText("1200 页");
  });

  test("REGRESSION: the pre-analysis confirmation banner text is preserved verbatim", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocks(page);
    await page.goto("/upload");

    const banner = page.getByTestId("gbc-upload-preanalysis-banner");
    await expect(banner).toContainText("系统不会把无法识别的年份写成默认值");
    await expect(banner).toContainText("低置信度元数据将在任务进入规则分析前要求人工确认");
  });

  test("uploading a PDF runs preflight and shows a real 校验通过 badge", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocks(page);
    await page.goto("/upload");

    const fileInput = page.getByTestId("gbc-upload-file-input");
    await fileInput.setInputFiles({
      name: "2026年度部门预算公开说明.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 minimal placeholder content for e2e"),
    });

    await expect(page.getByTestId("gbc-upload-file-list")).toBeVisible();
    const row = page.locator('[data-testid^="gbc-upload-file-row-"]').first();
    await expect(row).toContainText("校验通过", { timeout: 10_000 });
    await expect(row).toContainText("48 页");
  });

  test("REGRESSION: attribution wizard blocks submission until all required steps are filled", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocks(page);
    await page.goto("/upload");

    await page.getByTestId("gbc-upload-mode-attribution").click();
    await expect(page.getByTestId("gbc-attribution-wizard")).toBeVisible();

    // 上传一个文件让"开始分析"按钮进入"文件已就绪但归属未完成"的分支
    const fileInput = page.getByTestId("gbc-upload-file-input");
    await fileInput.setInputFiles({
      name: "上海市普陀区财政局2026年度单位预算.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 minimal placeholder content for e2e"),
    });
    await page.waitForTimeout(300);

    // 步骤 1 未选：提交按钮必须禁用
    await expect(page.getByTestId("gbc-upload-submit")).toBeDisabled();

    // 选部门后仍未选文件层级：仍应禁用
    await page.getByTestId("gbc-attribution-department-select").selectOption("dept-caizheng");
    await expect(page.getByTestId("gbc-upload-submit")).toBeDisabled();
    await expect(page.getByTestId("gbc-attribution-validation-error")).toContainText("文件层级");
  });

  test("REGRESSION: department-summary file level does not require selecting a unit", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocks(page);
    await page.goto("/upload");

    await page.getByTestId("gbc-upload-mode-attribution").click();
    await page.getByTestId("gbc-attribution-department-select").selectOption("dept-caizheng");
    await page.getByTestId("gbc-attribution-level-department").click();

    // 部门汇总文件：不出现单位选择区块，且归属校验判定为已完成
    await expect(page.getByTestId("gbc-attribution-unit-select")).toHaveCount(0);
    await expect(page.getByTestId("gbc-attribution-validation-error")).toHaveCount(0);
    await expect(page.getByTestId("gbc-attribution-breadcrumb")).toContainText("上海市普陀区财政局（部门）");
  });

  test("REGRESSION: unit file level requires selecting a unit before submission is allowed", async ({ page }) => {
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocks(page);
    await page.goto("/upload");

    await page.getByTestId("gbc-upload-mode-attribution").click();
    await page.getByTestId("gbc-attribution-department-select").selectOption("dept-caizheng");
    await page.getByTestId("gbc-attribution-level-unit").click();

    // 单位文件层级但尚未选单位：必须显示校验错误
    await expect(page.getByTestId("gbc-attribution-validation-error")).toContainText("必须选择所属预算单位");

    await page.getByTestId("gbc-attribution-unit-select").selectOption("unit-caizheng-head");
    await expect(page.getByTestId("gbc-attribution-validation-error")).toHaveCount(0);
  });

  test("REGRESSION: same-name department and head unit are shown as distinct entries in the breadcrumb", async ({
    page,
  }) => {
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocks(page);
    await page.goto("/upload");

    await page.getByTestId("gbc-upload-mode-attribution").click();
    await page.getByTestId("gbc-attribution-department-select").selectOption("dept-caizheng");
    await page.getByTestId("gbc-attribution-level-unit").click();
    await page.getByTestId("gbc-attribution-unit-select").selectOption("unit-caizheng-head");

    const breadcrumb = page.getByTestId("gbc-attribution-breadcrumb");
    await expect(breadcrumb).toContainText("上海市普陀区财政局（部门）");
    await expect(breadcrumb).toContainText("上海市普陀区财政局（本级单位）");
    // 同名处理规则说明必须存在，且文案与实现口径一致
    await expect(page.getByTestId("gbc-attribution-same-name-notice")).toContainText("部门 ID + 单位 ID + 层级类型");
  });

  // -------------------------------------------------------------------------
  // 前置修复 1：分析前确认闸门（决策 B——真的实现这个闸门，让文案成立）
  // -------------------------------------------------------------------------

  test("REGRESSION: needs_confirmation file blocks submission, and the submit button is disabled", async ({
    page,
  }) => {
    const uploadRequests: Array<Record<string, unknown>> = [];
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocksWithMissingYear(page, uploadRequests);
    await page.goto("/upload");

    const fileInput = page.getByTestId("gbc-upload-file-input");
    await fileInput.setInputFiles({
      name: "扫描件_预算执行情况说明.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 minimal placeholder content for e2e"),
    });

    const row = page.locator('[data-testid^="gbc-upload-file-row-"]').first();
    await expect(row).toContainText("需要确认", { timeout: 10_000 });

    // 核心反例：存在未解决的 needs_confirmation 文件时，提交按钮必须被禁用。
    await expect(page.getByTestId("gbc-upload-submit")).toBeDisabled();
    // 拦截原因必须明确写出来，不能只是禁用按钮却不说明为什么。
    await expect(page.getByTestId("gbc-upload-confirmation-blocking-notice")).toContainText("1 个文件需要确认");

    // 确认过程中不应该发生任何真实上传请求（防止"看起来禁用了，其实点了也会提交"）。
    expect(uploadRequests.length).toBe(0);
  });

  test("REGRESSION: filling the missing year via the per-file 补齐 form unblocks submission and the real value reaches the backend", async ({
    page,
  }) => {
    const uploadRequests: Array<Record<string, unknown>> = [];
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocksWithMissingYear(page, uploadRequests);
    await page.goto("/upload");

    const fileInput = page.getByTestId("gbc-upload-file-input");
    await fileInput.setInputFiles({
      name: "扫描件_预算执行情况说明.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 minimal placeholder content for e2e"),
    });

    const row = page.locator('[data-testid^="gbc-upload-file-row-"]').first();
    await expect(row).toContainText("需要确认", { timeout: 10_000 });
    await expect(page.getByTestId("gbc-upload-submit")).toBeDisabled();

    // 单文件补齐：点击"补齐"展开表单，只填年份（该文件只缺年份，其它字段已识别正确）。
    const toggleButton = page.locator('[data-testid^="gbc-upload-file-confirm-toggle-"]').first();
    await toggleButton.click();
    await expect(page.getByTestId("gbc-upload-confirmation-panel")).toBeVisible();
    await page.getByTestId("gbc-upload-confirm-year").selectOption("2026");
    await page.getByTestId("gbc-upload-confirm-save").click();

    // 反例（防止把闸门做成死路）：补齐后必须能提交。
    await expect(page.getByTestId("gbc-upload-submit")).toBeEnabled();
    await expect(row).toContainText("校验通过");

    await page.getByTestId("gbc-upload-submit").click();

    // 等待真实上传请求发生，并断言请求体里确实带上了补齐后的年份 2026——
    // 不是仅前端把徽章改绿，后端仍收到空年份。
    await expect.poll(() => uploadRequests.length, { timeout: 10_000 }).toBeGreaterThan(0);
    const bodyText = String(uploadRequests[0]?.bodyText ?? "");
    expect(bodyText).toContain("2026");
  });

  test("REGRESSION: batch preset year fixes a needs_confirmation file without requiring per-file editing", async ({
    page,
  }) => {
    const uploadRequests: Array<Record<string, unknown>> = [];
    await page.context().addCookies([sessionCookie]);
    await installUploadCenterMocksWithMissingYear(page, uploadRequests);
    await page.goto("/upload");

    const fileInput = page.getByTestId("gbc-upload-file-input");
    await fileInput.setInputFiles({
      name: "扫描件_预算执行情况说明.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4 minimal placeholder content for e2e"),
    });

    const row = page.locator('[data-testid^="gbc-upload-file-row-"]').first();
    await expect(row).toContainText("需要确认", { timeout: 10_000 });

    // 用批量预设的年份下拉补齐（而不是逐文件编辑），验证预设值会喂回 preflight 状态。
    await page.getByTestId("gbc-upload-preset-year").selectOption(String(new Date().getFullYear()));

    await expect(row).toContainText("校验通过");
    await expect(page.getByTestId("gbc-upload-submit")).toBeEnabled();
  });
});
