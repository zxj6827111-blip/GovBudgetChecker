import { expect, test } from "../../app/node_modules/playwright/test";

test("表文跨页定位保持页码和框配对；输入跳页、错误页码和返回证据同步", async ({ page }) => {
  await page.context().addCookies([{ name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax" }]);
  const table = { page: 28, physical_page: 28, quote: "234,955.97", bbox: [334.2, 202.49, 415.2, 226.99], location_level: "exact_cell", verification_status: "verified" };
  const narrative = { page: 40, physical_page: 40, quote: "政府性基金收入 78,656.53 万元", bbox: [119.88, 704.15, 452.4, 721.52], location_level: "exact_span", verification_status: "verified" };
  const rule = { id: "rule-cross-page", source: "rule", rule_id: "V33-248", severity: "high", title: "政府性基金表文金额不一致", message: "复核表文金额", page_number: 40, bbox: narrative.bbox, location_level: "exact_span", verification_status: "verified", evidence_status: "complete", evidence_verification: { status: "verified", source_identity_complete: true }, evidence: [table, narrative], location: { page: 40, table_refs: [{ ...table, role: "表格金额" }, { ...narrative, role: "原文金额" }] } };
  const ai = { ...rule, id: "ai-repeat", source: "ai", rule_id: "AI-TEXT-000", severity: "manual_review", title: "重复表述", page_number: 39, claimed_page_number: 22, message: "PDF 第 39 页存在重复表述。", bbox: [89, 548, 505, 594], location: { page: 39 }, evidence: [{ ...narrative, page: 39, physical_page: 39, bbox: [89, 548, 505, 594] }] };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith("/api/files/")) {
      const landscape = url.searchParams.get("page") === "28";
      const scale = Number(url.searchParams.get("scale") ?? 1.6);
      const width = Math.ceil((landscape ? 841.9 : 595.3) * scale);
      const height = Math.ceil((landscape ? 595.3 : 841.9) * scale);
      return route.fulfill({ status: 200, contentType: "image/svg+xml", body: `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="white"/></svg>` });
    }
    const body = url.pathname === "/api/auth/me" ? { user: { username: "reviewer", is_admin: false } }
      : url.pathname === "/api/health" ? { status: "ok" }
        : url.pathname === "/api/jobs/location-fixture" ? { job_id: "location-fixture", filename: "跨页报告.pdf", status: "review_required", result: { ai_findings: [ai], rule_findings: [rule], merged: { merged_ids: [rule.id, ai.id] }, meta: { pages: 46 } } } : {};
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });
  await page.goto("/review?job=location-fixture");
  await page.getByTestId("gbc-review-toggle-filters").click();
  await page.getByTestId("gbc-review-source-rule").click();
  await page.getByTestId("gbc-review-issue-card-rule-cross-page").click();
  const input = page.getByTestId("gbc-review-pdf-page-input");
  const frame = page.getByTestId("gbc-review-evidence-highlight");
  await expect(input).toHaveValue("40");
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute("style", /top: 83\./);
  await page.getByTestId("gbc-review-evidence-ref-rule-cross-page-表格金额|28|0").click();
  await expect(input).toHaveValue("28");
  await expect.poll(async () => Number((await frame.getAttribute("style"))?.match(/top: ([\d.]+)/)?.[1])).toBeCloseTo(202.49 * 1.6 / 953 * 100, 3);
  await page.getByTestId("gbc-review-pdf-zoom-in").click();
  await expect(page.getByTestId("gbc-review-pdf-zoom-level")).toHaveText("125%");
  await expect.poll(async () => Number((await frame.getAttribute("style"))?.match(/top: ([\d.]+)/)?.[1])).toBeCloseTo(202.49 * 2 / 1191 * 100, 3);
  await page.getByTestId("gbc-review-pdf-zoom-out").click();
  await expect(page.getByTestId("gbc-review-pdf-zoom-level")).toHaveText("100%");
  await input.fill("26");
  await input.press("Enter");
  await expect(page.getByTestId("gbc-review-pdf-page-image")).toHaveAttribute("alt", "第 26 页");
  await expect(frame).toHaveCount(0);
  await expect(page.getByTestId("gbc-review-selected-page-mismatch")).toContainText("第 28 页");
  await page.getByRole("button", { name: "返回证据页" }).click();
  await expect(input).toHaveValue("28");
  await expect(frame).toBeVisible();
  await input.fill("47");
  await page.getByTestId("gbc-review-pdf-jump").click();
  await expect(page.getByTestId("gbc-review-pdf-viewer").getByRole("alert")).toContainText("1–46");
  await expect(page.getByTestId("gbc-review-pdf-page-image")).toHaveAttribute("alt", "第 28 页");
  await page.getByTestId("gbc-review-source-ai").click();
  await page.getByTestId("gbc-review-issue-card-ai-repeat").click();
  await expect(input).toHaveValue("39");
  await expect(page.getByTestId("gbc-review-issue-detail")).toContainText("PDF 第 39 页存在重复表述");
  await expect(page.getByTestId("gbc-review-ai-location-unavailable")).toHaveCount(0);
});
