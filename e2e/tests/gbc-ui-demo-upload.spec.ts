import { expect, test } from "../../app/node_modules/playwright/test";

const sessionCookie = {
  name: "gbc_session",
  value: "e2e-session",
  url: "http://127.0.0.1:3000",
  sameSite: "Lax" as const,
};

const pdfFile = (name: string) => ({
  name,
  mimeType: "application/pdf",
  buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
});

test.describe("GBC UI demo upload", () => {
  test("upload cards support drag-drop, file chooser and card-specific metadata", async ({ page }) => {
    test.setTimeout(60_000);

    const uploads: Array<{ fiscalYear: string; docType: string }> = [];
    const analyzePayloads: Array<{ fiscal_year?: string; doc_type?: string }> = [];

    await page.context().addCookies([sessionCookie]);

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/auth/me") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ user: { username: "e2e-admin", is_admin: true } }),
        });
        return;
      }

      if (path === "/api/organizations") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            tree: [
              {
                id: "dept-001",
                name: "普陀区",
                level: "department",
                level_name: "区",
                parent_id: null,
                children: [],
              },
            ],
          }),
        });
        return;
      }

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-001",
                name: "普陀区",
                level: "department",
                level_name: "区",
                parent_id: null,
              },
            ],
            total: 1,
          }),
        });
        return;
      }

      if (path === "/api/jobs") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ items: [], total: 0, limit: 500, offset: 0 }),
        });
        return;
      }

      if (path === "/api/gbc-ui-demo/workflow") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ issues: {}, packages: [] }),
        });
        return;
      }

      if (path === "/api/documents/upload" && method === "POST") {
        const body = req.postData() ?? "";
        const fiscalYear = body.match(/name="fiscal_year"\r?\n\r?\n([^\r\n]+)/)?.[1] ?? "";
        const docType = body.match(/name="doc_type"\r?\n\r?\n([^\r\n]+)/)?.[1] ?? "";
        uploads.push({ fiscalYear, docType });
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ job_id: `job-upload-${uploads.length}` }),
        });
        return;
      }

      const analyzeMatch = path.match(/^\/api\/analyze\/([^/]+)$/);
      if (analyzeMatch && method === "POST") {
        analyzePayloads.push(req.postDataJSON() as { fiscal_year?: string; doc_type?: string });
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ status: "started", job_id: analyzeMatch[1] }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    const openUploadPage = async () => {
      await page.goto("/viewer/gbc-ui-demo");
      await expect(page.getByText("年度审核", { exact: true })).toBeVisible({ timeout: 20_000 });
      await page.getByRole("button", { name: "材料上传" }).click();
      await expect(page.getByText("材料上传与批次提交", { exact: true })).toBeVisible();
    };

    await openUploadPage();
    await page.getByTestId("gbc-upload-open-batch").click();
    await expect(page.getByTestId("batch-upload-modal")).toBeVisible();
    await expect(page.getByTestId("batch-upload-dropzone")).toBeVisible();
    await expect(page.getByTestId("batch-upload-select-folder")).toBeVisible();
    await page.getByTestId("batch-upload-close").click();
    await expect(page.getByTestId("batch-upload-modal")).toBeHidden();

    await expect(page.locator('input[type="file"]')).toHaveCount(2);

    const budgetDrop = await page.evaluateHandle(() => {
      const transfer = new DataTransfer();
      transfer.items.add(new File(["%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"], "budget-2026.pdf", { type: "application/pdf" }));
      return transfer;
    });
    await page.getByTestId("gbc-upload-label-2026-budget").dispatchEvent("dragover", { dataTransfer: budgetDrop });
    await expect(page.getByTestId("gbc-upload-label-2026-budget")).toContainText("松开后开始上传");
    await page.getByTestId("gbc-upload-label-2026-budget").dispatchEvent("drop", { dataTransfer: budgetDrop });
    await budgetDrop.dispose();

    await expect.poll(() => uploads.length).toBe(1);
    expect(uploads[0]).toEqual({ fiscalYear: "2026", docType: "dept_budget" });
    await expect.poll(() => analyzePayloads.length).toBe(1);
    expect(analyzePayloads[0]).toMatchObject({ fiscal_year: "2026", doc_type: "dept_budget" });
    await expect(page.getByText("材料上传与批次提交", { exact: true })).toBeVisible();

    await openUploadPage();
    const finalChooser = page.waitForEvent("filechooser");
    await page.locator('label[for="gbc-upload-2025-final"]').click();
    await (await finalChooser).setFiles(pdfFile("final-2025.pdf"));

    await expect.poll(() => uploads.length).toBe(2);
    expect(uploads[1]).toEqual({ fiscalYear: "2025", docType: "dept_final" });
    await expect.poll(() => analyzePayloads.length).toBe(2);
    expect(analyzePayloads[1]).toMatchObject({ fiscal_year: "2025", doc_type: "dept_final" });
  });
});
