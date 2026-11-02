import { expect, test, type Page } from "../../app/node_modules/playwright/test";

const tree = [{
  id: "city", name: "上海市", level: "city", parent_id: null, selectable: false,
  children: [
    { id: "letters-dept", name: "上海市信访办公室", level: "department", parent_id: "city", selectable: true, keywords: ["信访窗口"], children: [
      { id: "letters-unit", name: "上海市信访办公室单位", level: "unit", parent_id: "letters-dept", selectable: true, children: [] },
    ] },
    { id: "district", name: "普陀区", level: "district", parent_id: "city", selectable: false, children: [
      { id: "district-dept", name: "上海市信访办公室", level: "department", parent_id: "district", selectable: true, children: [
        { id: "district-unit", name: "上海市信访办公室单位", level: "unit", parent_id: "district-dept", selectable: true, children: [] },
      ] },
    ] },
    { id: "ancestor-only", name: "仅可见的上级部门", level: "department", parent_id: "city", selectable: false, children: [] },
  ],
}];

async function installMocks(page: Page, options: { noCandidates?: boolean; failDirectoryOnce?: boolean } = {}) {
  const uploads: string[] = [];
  let catalogRequests = 0;
  await page.context().addCookies([{ name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax" }]);
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    let body: unknown = {};
    if (url.pathname === "/api/auth/me") body = { user: { username: "e2e-user", is_admin: true } };
    if (url.pathname === "/api/config") body = { max_upload_mb: 30, max_upload_pages: 800 };
    if (url.pathname === "/api/ai/capabilities") body = { enabled: false, profiles: [], default_use_ai: false };
    if (url.pathname === "/api/organizations") {
      if (url.searchParams.get("purpose") === "upload") {
        catalogRequests += 1;
        if (options.failDirectoryOnce && catalogRequests === 1) {
          await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "temporarily unavailable" }) });
          return;
        }
      }
      body = { tree };
    }
    if (url.pathname === "/api/documents/preflight") body = {
      report_year: 2024, doc_type: "dept_final", page_count: 27, current: null,
      suggestions: options.noCandidates ? [] : [
        { organization_id: "letters-dept", organization_name: "上海市信访办公室", level: "department", confidence: 0.7 },
        { organization_id: "ancestor-only", organization_name: "仅可见的上级部门", level: "department", confidence: 0.6 },
      ],
      binding_decision: { reasons: [{ code: "scope_undetermined" }] },
    };
    if (url.pathname === "/api/documents/upload") {
      uploads.push(request.postDataBuffer()?.toString("utf8") ?? "");
      body = { job_id: "upload-confirmed-unit" };
    }
    if (url.pathname.endsWith("/analyze")) body = { job_id: "upload-confirmed-unit", status: "accepted" };
    if (url.pathname === "/api/jobs") body = [];
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  return uploads;
}

async function addFiles(page: Page, count = 1) {
  await page.goto("/upload");
  await page.getByTestId("gbc-upload-file-input").setInputFiles(Array.from({ length: count }, (_, i) => ({
    name: `上海市信访办公室2024年决算${i ? `-${i}` : ""}.pdf`,
    mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 e2e placeholder"),
  })));
  await expect(page.locator('[data-testid^="gbc-upload-file-confirm-toggle-"]')).toHaveCount(count);
}

test.describe("Upload organization candidate confirmation", () => {
  test("候选不自动选择，单位 ID 进入真实上传请求，完整路径保留", async ({ page }) => {
    const uploads = await installMocks(page);
    await addFiles(page);
    await page.getByRole("button", { name: "确认归属", exact: true }).click();
    const panel = page.getByTestId("gbc-upload-confirmation-panel");
    await expect(panel.getByTestId("gbc-upload-org-candidates").getByRole("radio")).toHaveCount(2);
    await expect(panel.getByTestId("gbc-upload-org-option-ancestor-only")).toHaveCount(0);
    await expect(panel.getByTestId("gbc-upload-confirm-save")).toBeDisabled();
    await expect(panel.getByRole("radio", { checked: true })).toHaveCount(0);
    const bounds = await panel.boundingBox();
    expect(bounds!.width).toBeGreaterThanOrEqual(640);
    expect(bounds!.width).toBeLessThanOrEqual(720);
    await panel.getByTestId("gbc-upload-org-option-letters-unit").getByRole("radio").check();
    await expect(panel.getByTestId("gbc-upload-org-selection")).toContainText("上海市 › 上海市信访办公室 › 上海市信访办公室单位 · 单位材料");
    await expect(panel.getByTestId("gbc-upload-confirm-save")).toBeEnabled();
    await page.screenshot({ path: test.info().outputPath("candidate-drawer.png"), animations: "disabled" });
    await panel.getByTestId("gbc-upload-confirm-save").click();
    await expect(panel).toHaveCount(0);
    await expect(page.getByTestId("gbc-upload-confirmed-organization")).toContainText("单位材料");
    await expect(page.getByTestId("gbc-upload-submit")).toBeEnabled();
    await page.getByTestId("gbc-upload-submit").click();
    await expect.poll(() => uploads.length).toBe(1);
    expect(uploads[0]).toMatch(/name="org_unit_id"\r?\n\r?\nletters-unit\r?\n/);
  });

  test("搜索支持维护的简称、层级和市区范围，并区分同名单位", async ({ page }) => {
    await installMocks(page);
    await addFiles(page);
    await page.getByRole("button", { name: "确认归属", exact: true }).click();
    const panel = page.getByTestId("gbc-upload-confirmation-panel");
    await panel.getByTestId("gbc-upload-org-search-toggle").click();
    await panel.getByTestId("gbc-upload-org-search").fill("信访窗口");
    await expect(panel.getByRole("radio")).toHaveCount(1);
    await panel.getByTestId("gbc-upload-org-search").fill("信访");
    await panel.getByRole("button", { name: "单位材料", exact: true }).click();
    await expect(panel.getByRole("radio")).toHaveCount(2);
    await panel.getByLabel("管理范围").selectOption("district");
    await expect(panel.getByRole("radio")).toHaveCount(1);
    await expect(panel.getByTestId("gbc-upload-org-option-district-unit")).toContainText("上海市 › 普陀区 › 上海市信访办公室");
    await panel.getByTestId("gbc-upload-org-option-district-unit").getByRole("radio").check();
    await panel.getByTestId("gbc-upload-confirm-save").click();
    await expect(page.getByTestId("gbc-upload-confirmed-organization")).toContainText("普陀区");
  });

  test("取消或 Escape 不保存，逐文件确认且返回触发按钮焦点", async ({ page }) => {
    await installMocks(page);
    await addFiles(page, 2);
    const triggers = page.locator('[data-testid^="gbc-upload-file-confirm-toggle-"]');
    await triggers.first().click();
    const panel = page.getByTestId("gbc-upload-confirmation-panel");
    await panel.getByTestId("gbc-upload-org-option-letters-unit").getByRole("radio").check();
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(triggers.first()).toBeFocused();
    await expect(page.getByTestId("gbc-upload-confirmed-organization")).toHaveCount(0);
    await triggers.first().click();
    await expect(panel.getByTestId("gbc-upload-confirm-save")).toBeDisabled();
    await panel.getByTestId("gbc-upload-org-option-letters-unit").getByRole("radio").check();
    await panel.getByTestId("gbc-upload-confirm-save").click();
    await expect(page.getByTestId("gbc-upload-confirmed-organization")).toHaveCount(1);
    await expect(triggers).toHaveCount(1);
    await expect(page.getByTestId("gbc-upload-submit")).toBeDisabled();
    await triggers.first().click();
    await expect(panel.getByTestId("gbc-upload-confirm-save")).toBeDisabled();
    await panel.getByTestId("gbc-upload-confirm-cancel").click();
    await expect(page.getByTestId("gbc-upload-submit")).toBeDisabled();
  });

  test("目录失败可重试，无候选直接搜索，窄屏不横向溢出", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await installMocks(page, { failDirectoryOnce: true, noCandidates: true });
    await addFiles(page);
    await page.getByRole("button", { name: "确认归属", exact: true }).click();
    const panel = page.getByTestId("gbc-upload-confirmation-panel");
    await expect(panel.getByRole("alert")).toContainText("组织目录加载失败");
    await expect(panel.getByTestId("gbc-upload-confirm-save")).toBeDisabled();
    await panel.getByRole("button", { name: "重新加载组织" }).click();
    const search = panel.getByTestId("gbc-upload-org-search");
    await search.fill("仅可见的上级部门");
    await expect(panel.getByRole("radio")).toHaveCount(0);
    await expect(panel).toContainText("未找到可选组织");
    await search.fill("信访");
    await panel.getByTestId("gbc-upload-org-option-letters-unit").getByRole("radio").check();
    const geometry = await panel.evaluate((element) => ({ width: element.getBoundingClientRect().width, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth }));
    expect(geometry.width).toBeLessThanOrEqual(390);
    expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth);
    await expect(panel.getByTestId("gbc-upload-confirm-save")).toBeEnabled();
    await page.screenshot({ path: test.info().outputPath("candidate-mobile.png"), animations: "disabled" });
    await expect(panel.getByTestId("gbc-upload-confirm-save")).toBeInViewport();
  });
});
