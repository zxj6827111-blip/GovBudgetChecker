import { expect, test } from "../../app/node_modules/playwright/test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

test.describe("Batch Upload Bulk Tools", () => {
  test("select folder button reads files from native directory picker", async ({ page }) => {
    test.setTimeout(60_000);

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/organizations/list") {
        await new Promise((resolve) => setTimeout(resolve, 500));
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              { id: "dept-mzj", name: "民政局", level: "department", level_name: "部门", parent_id: null },
              { id: "unit-mzj-local", name: "民政局本级", level: "unit", level_name: "单位", parent_id: "dept-mzj" },
              { id: "dept-czj", name: "财政局", level: "department", level_name: "部门", parent_id: null },
            ],
            total: 3,
          }),
        });
        return;
      }

      if (path === "/api/documents/preflight" && method === "POST") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ report_year: 2026, doc_type: "dept_budget" }),
        });
        return;
      }

      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({}) });
    });

    await page.addInitScript(() => window.localStorage.clear());
    await page.goto("/e2e/batch-upload");
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 20_000 });

    await page.evaluate(() => {
      Object.defineProperty(window, "showDirectoryPicker", {
        configurable: true,
        value: async () => ({
          kind: "directory",
          name: "预算目录",
          values: async function* () {
            yield {
              kind: "file",
              name: "民政局本级2026预算.pdf",
              getFile: async () => new File(["%PDF-1.4\n%%EOF\n"], "民政局本级2026预算.pdf", { type: "application/pdf" }),
            };
            yield {
              kind: "file",
              name: "财政局部门2026预算.pdf",
              getFile: async () => new File(["%PDF-1.4\n%%EOF\n"], "财政局部门2026预算.pdf", { type: "application/pdf" }),
            };
          },
        }),
      });
    });

    await page.getByTestId("batch-upload-select-folder").click();

    await expect(page.getByTestId("batch-upload-selection-notice")).toContainText("已加入 2 个 PDF");
    await expect(page.getByText("民政局本级2026预算.pdf", { exact: false })).toBeVisible();
    await expect(page.getByText("财政局部门2026预算.pdf", { exact: false })).toBeVisible();
  });

  test("supports folder selection and filename based organization matching", async ({ page }) => {
    test.setTimeout(60_000);

    const preflightFilenames: string[] = [];

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/organizations/list") {
        await new Promise((resolve) => setTimeout(resolve, 500));
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-mzj",
                name: "民政局",
                level: "department",
                level_name: "部门",
                parent_id: null,
              },
              {
                id: "unit-mzj-local",
                name: "民政局本级",
                level: "unit",
                level_name: "单位",
                parent_id: "dept-mzj",
              },
              {
                id: "dept-czj",
                name: "财政局",
                level: "department",
                level_name: "部门",
                parent_id: null,
              },
              {
                id: "unit-czj-local",
                name: "财政局本级",
                level: "unit",
                level_name: "单位",
                parent_id: "dept-czj",
              },
            ],
            total: 4,
          }),
        });
        return;
      }

      if (path === "/api/documents/preflight" && method === "POST") {
        const body = req.postData() ?? "";
        const filename = body.match(/filename="([^"]+)"/)?.[1] ?? "";
        preflightFilenames.push(filename);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            filename,
            report_year: filename.includes("2026") ? 2026 : 2025,
            doc_type: filename.includes("预算") ? "dept_budget" : "dept_final",
          }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.addInitScript(() => window.localStorage.clear());
    await page.goto("/e2e/batch-upload");
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 20_000 });

    const uploadDir = await mkdtemp(join(tmpdir(), "gbc-batch-folder-"));
    try {
      await writeFile(
        join(uploadDir, "民政局本级2026预算.pdf"),
        Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
      );
      await writeFile(
        join(uploadDir, "财政局部门2025决算.pdf"),
        Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
      );
      await writeFile(join(uploadDir, "readme.txt"), Buffer.from("ignored", "utf-8"));

      await page.getByTestId("batch-upload-folder-input").setInputFiles(uploadDir);

      await expect(page.getByTestId("batch-upload-selection-notice")).toContainText("已加入 2 个 PDF");
      await expect(page.getByText("民政局本级2026预算.pdf", { exact: false })).toBeVisible();
      await expect(page.getByText("财政局部门2025决算.pdf", { exact: false })).toBeVisible();
      await expect(page.getByText("民政局 / 民政局本级")).toBeVisible();
      await expect(page.getByText("财政局 / 部门级上传")).toBeVisible();
      await expect.poll(() => preflightFilenames.length).toBe(2);
    } finally {
      await rm(uploadDir, { recursive: true, force: true });
    }
  });

  test("skips homepage preflight for large batches already matched by filename", async ({ page }) => {
    test.setTimeout(60_000);

    let preflightCalls = 0;

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-jyj",
                name: "教育局",
                level: "department",
                level_name: "部门",
                parent_id: null,
              },
            ],
            total: 1,
          }),
        });
        return;
      }

      if (path === "/api/documents/preflight" && method === "POST") {
        preflightCalls += 1;
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "preflight should not run for this batch" }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.addInitScript(() => window.localStorage.clear());
    const orgResponse = page.waitForResponse((response) => {
      try {
        return new URL(response.url()).pathname === "/api/organizations/list";
      } catch {
        return false;
      }
    });
    await page.goto("/e2e/batch-upload");
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 20_000 });
    await orgResponse;

    const selectedFiles = Array.from({ length: 24 }, (_, index) => ({
      name: `教育局2026预算-${String(index + 1).padStart(2, "0")}.pdf`,
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
    }));

    await page.getByTestId("batch-upload-file-input").setInputFiles(selectedFiles);

    await expect(page.getByTestId("batch-upload-selection-notice")).toContainText("大批量已优先使用文件名匹配");
    await expect(page.getByText("已按文件名匹配，跳过首页识别").first()).toBeVisible();
    await expect(page.getByTestId("batch-start")).toBeEnabled();
    await page.waitForTimeout(1000);
    expect(preflightCalls).toBe(0);
  });

  test("retries homepage preflight when backend rate limits", async ({ page }) => {
    test.setTimeout(60_000);

    let preflightAttempts = 0;

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-jyj",
                name: "教育局",
                level: "department",
                level_name: "部门",
                parent_id: null,
              },
            ],
            total: 1,
          }),
        });
        return;
      }

      if (path === "/api/documents/preflight" && method === "POST") {
        preflightAttempts += 1;
        if (preflightAttempts === 1) {
          await route.fulfill({
            status: 429,
            headers: { "Retry-After": "0" },
            contentType: "application/json",
            body: JSON.stringify({ detail: "Too many requests. Please try again later." }),
          });
          return;
        }

        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            report_year: 2026,
            doc_type: "dept_budget",
            current: {
              organization_id: "dept-jyj",
              organization_name: "教育局",
              level: "department",
              confidence: 0.95,
              match_basis: "cover",
            },
          }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.addInitScript(() => window.localStorage.clear());
    await page.goto("/e2e/batch-upload");
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 20_000 });

    await page.getByTestId("batch-upload-file-input").setInputFiles({
      name: "首页识别教育局预算.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
    });

    await expect.poll(() => preflightAttempts).toBe(2);
    await expect(page.getByText("Too many requests")).toHaveCount(0);
    await expect(page.getByText("已按PDF首页识别到部门：教育局")).toBeVisible();
  });

  test("supports apply-all selection and retrying failed files", async ({ page }) => {
    test.setTimeout(60_000);

    let uploadAttempts = 0;
    let runCalls = 0;

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const url = new URL(req.url());
      const path = url.pathname;

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              {
                id: "dept-czj",
                name: "财政局",
                level: "department",
                level_name: "部门",
                parent_id: null,
              },
              {
                id: "unit-czj-local",
                name: "财政局本级",
                level: "unit",
                level_name: "单位",
                parent_id: "dept-czj",
              },
            ],
            total: 2,
          }),
        });
        return;
      }

      if (path === "/api/documents/upload" && method === "POST") {
        uploadAttempts += 1;
        if (uploadAttempts === 1) {
          await route.fulfill({
            status: 500,
            contentType: "application/json",
            body: JSON.stringify({ detail: "temporary upload error" }),
          });
          return;
        }
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            id: "version-retry-001",
            job_id: "version-retry-001",
            filename: "测试2025预算.pdf",
          }),
        });
        return;
      }

      const runMatch = path.match(/^\/api\/documents\/([^/]+)\/run$/);
      if (runMatch && method === "POST") {
        runCalls += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ job_id: runMatch[1], status: "started" }),
        });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({}),
      });
    });

    await page.goto("/e2e/batch-upload");
    await expect(page.locator("h2").first()).toBeVisible({ timeout: 20_000 });

    await page.getByTestId("batch-upload-file-input").setInputFiles({
      name: "测试2025预算.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n", "utf-8"),
    });

    const startButton = page.getByTestId("batch-start");
    await expect(startButton).toBeDisabled();

    await page.getByTestId("batch-bulk-department").selectOption("dept-czj");
    await page.getByTestId("batch-bulk-unit").selectOption("unit-czj-local");
    await page.getByTestId("batch-apply-all").click();
    await page.locator('[data-testid^="batch-confirm-metadata-"]').click();

    await expect(startButton).toBeEnabled();
    await startButton.click();

    await expect(page.getByTestId("batch-retry-failed")).toBeVisible();
    await expect.poll(() => uploadAttempts).toBe(1);

    await page.getByTestId("batch-retry-failed").click();
    await expect(startButton).toBeEnabled();
    await startButton.click();

    await expect.poll(() => uploadAttempts).toBe(2);
    await expect.poll(() => runCalls).toBe(1);
  });

  test("retries analysis start without uploading the same document again", async ({ page }) => {
    test.setTimeout(60_000);
    let uploadCalls = 0;
    let runCalls = 0;

    await page.route("**/api/**", async (route) => {
      const req = route.request();
      const method = req.method().toUpperCase();
      const path = new URL(req.url()).pathname;

      if (path === "/api/organizations/list") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            organizations: [
              { id: "dept-czj", name: "财政局", level: "department", parent_id: null },
            ],
          }),
        });
        return;
      }
      if (path === "/api/documents/preflight") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ report_year: 2026, doc_type: "dept_budget" }),
        });
        return;
      }
      if (path === "/api/documents/upload" && method === "POST") {
        uploadCalls += 1;
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ id: "version-run-retry", job_id: "version-run-retry" }),
        });
        return;
      }
      if (path === "/api/documents/version-run-retry/run" && method === "POST") {
        runCalls += 1;
        await route.fulfill({
          status: runCalls === 1 ? 500 : 200,
          contentType: "application/json",
          body: JSON.stringify(
            runCalls === 1
              ? { detail: "temporary run error" }
              : { job_id: "version-run-retry", status: "started" },
          ),
        });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });

    await page.addInitScript(() => window.localStorage.clear());
    await page.goto("/e2e/batch-upload");
    await page.getByTestId("batch-upload-file-input").setInputFiles({
      name: "财政局部门2026预算.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from("%PDF-1.4\n%%EOF\n", "utf-8"),
    });
    await expect(page.getByTestId("batch-start")).toBeEnabled();
    await page.getByTestId("batch-start").click();
    await expect(page.getByTestId("batch-retry-failed")).toBeVisible();
    await page.getByTestId("batch-retry-failed").click();
    await page.getByTestId("batch-start").click();

    await expect.poll(() => runCalls).toBe(2);
    expect(uploadCalls).toBe(1);
  });
});
