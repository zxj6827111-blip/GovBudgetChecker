import { expect, test, type Page } from "../../app/node_modules/playwright/test";

async function fixtures(page: Page, options: { holdDetail?: boolean; failDetail?: boolean } = {}) {
  await page.context().addCookies([{ name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax" }]);
  const reads: Record<string, number> = {};
  const writes: string[] = [];
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let failDetail = options.failDetail ?? false;
  await page.route("**/api/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (request.method() === "GET") reads[path + url.search] = (reads[path + url.search] ?? 0) + 1;
    else writes.push(path);
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    if (path === "/api/auth/me") return json({ user: { username: "reviewer", is_admin: true } });
    if (path === "/api/health") return json({ status: "ok" });
    if (path === "/api/jobs") return json([]);
    if (path === "/api/reviews/worklist") return json({ data: { items: [], counts: {}, pending_total: 0 } });
    if (path === "/api/jobs/a") {
      if (options.holdDetail) await gate;
      if (failDetail) return json({}, 503);
      return json({ job_id: "a", filename: "当前材料.pdf", status: "done", page_count: 1, result: { issues: { all: [] } } });
    }
    if (path === "/api/jobs/a/structured-ingest") return json({});
    if (path === "/api/workflow") return json({ issues: {}, packages: [] });
    if (path === "/api/reviews") return json({ data: { review_context: { available: false, reason: "review_context_unavailable" }, review: null } });
    if (path.startsWith("/api/files/")) return route.fulfill({ contentType: "image/svg+xml", body: '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800"><rect width="600" height="800" fill="white"/></svg>' });
    return json({});
  });
  return { reads, writes, release, recover: () => { failDetail = false; } };
}

test("详情、补充数据和上下文并行读取，首次每个资源只请求一次", async ({ page }) => {
  const state = await fixtures(page, { holdDetail: true });
  await page.goto("/review?job=a");
  await expect.poll(() => state.reads["/api/reviews?job_uuid=a"]).toBe(1);
  await expect.poll(() => state.reads["/api/workflow?job_id=a"]).toBe(1);
  await expect(page.getByTestId("gbc-review-loading")).toBeVisible();
  expect(state.reads["/api/jobs/a"]).toBe(1);
  expect(state.reads["/api/jobs/a/structured-ingest"]).toBe(1);
  expect(state.reads["/api/workflow"]).toBeUndefined();
  state.release();
  await expect(page.getByTestId("gbc-review-filename")).toHaveText("当前材料.pdf");
  await expect(page.getByTestId("gbc-review-pdf-page-image")).toBeVisible();
  expect(state.writes).toEqual([]);
});

test("详情读取失败结束加载，用户可以立即重试", async ({ page }) => {
  const state = await fixtures(page, { failDetail: true });
  await page.goto("/review?job=a");
  await expect(page.getByTestId("gbc-review-not-found")).toContainText("暂时无法读取");
  state.recover();
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(page.getByTestId("gbc-review-filename")).toHaveText("当前材料.pdf");
  expect(state.writes).toEqual([]);
});
