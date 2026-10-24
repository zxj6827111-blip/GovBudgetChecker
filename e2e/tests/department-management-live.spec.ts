import { expect, test } from "../../app/node_modules/playwright/test";
import { readFileSync } from "node:fs";
import path from "node:path";

// 显式开启时才使用本机配置登录；只读真实目录和材料，不生成用户或业务数据。
test("真实系统的市区范围、目录密度与切换显示", async ({ page }, testInfo) => {
  test.skip(process.env.GBC_LIVE_REPORT_CHECK !== "1", "只在本机显式开启真实数据只读验证");
  test.setTimeout(90_000);
  const env = Object.fromEntries(readFileSync(path.resolve(__dirname, "../../.env"), "utf8").split(/\r?\n/).flatMap(line => {
    const match = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    return match ? [[match[1], match[2].trim().replace(/^["']|["']$/g, "")]] : [];
  }));
  const failures: string[] = [];
  page.on("pageerror", error => failures.push(error.message));
  const login = await page.request.post("/api/auth/login", { data: {
    username: env.DEFAULT_ADMIN_USERNAME || "admin", password: env.DEFAULT_ADMIN_PASSWORD,
  } });
  // 只记录状态，不把密码、Cookie、令牌写入报告或截图。
  expect(login.status(), "正常登录须成功，失败时不尝试其他凭据").toBe(200);
  const orgResponse = await page.request.get("/api/organizations?stats=none");
  expect(orgResponse.status()).toBe(200);
  const tree = (await orgResponse.json()).tree;
  const rows: Array<{ id: string; name: string; level: string; parent: string | null }> = [];
  const flatten = (nodes: typeof tree, parent: string | null = null) => {
    for (const node of nodes) { rows.push({ id: node.id, name: node.name, level: node.level, parent }); flatten(node.children ?? [], node.id); }
  };
  flatten(tree);
  const city = rows.find(row => row.level === "city");
  const districts = rows.filter(row => row.level === "district");
  const cityDepartments = rows.filter(row => row.parent === city?.id && row.level === "department");
  expect(city).toBeTruthy();
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.goto("/reports");
  const grid = page.getByTestId("gbc-reports-directory-grid");
  await expect(grid.locator("button")).toHaveCount(Math.min(cityDepartments.length, 24));
  await expect(page.getByTestId("gbc-reports-management-city")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("gbc-reports-selected")).toHaveCount(0);
  const cityApi = await page.request.get(`/api/materials/reports?management_scope=city&management_region=${city!.id}&org=${city!.id}&org_scope=descendants&page_size=100`);
  expect(cityApi.status()).toBe(200);
  const cityData = (await cityApi.json()).data;
  const cityCountIds = new Set(Object.keys(cityData.organization_report_counts));
  for (const district of districts) {
    const ids = new Set([district.id]);
    for (let changed = true; changed;) { changed = false; for (const row of rows) if (row.parent && ids.has(row.parent) && !ids.has(row.id)) { ids.add(row.id); changed = true; } }
    for (const id of ids) expect(cityCountIds.has(id)).toBe(false);
  }
  for (const view of ["list", "grid", "list", "grid"]) {
    await page.getByTestId(`gbc-reports-view-${view}`).click();
    await expect(grid).toHaveAttribute("data-view", view);
  }
  await page.screenshot({ path: testInfo.outputPath("live-city-compact.png"), fullPage: true });
  await page.getByTestId("gbc-reports-view-list").click();
  await page.screenshot({ path: testInfo.outputPath("live-city-list.png"), fullPage: true });
  await page.getByTestId("gbc-reports-management-district").click();
  const picker = page.getByTestId("gbc-reports-district-picker");
  await expect(picker.locator("button")).toHaveCount(16);
  await expect(picker.locator("button:enabled")).toHaveCount(districts.length);
  await page.screenshot({ path: testInfo.outputPath("live-district-picker.png"), fullPage: true });
  if (districts.length) {
    await page.getByTestId(`gbc-reports-district-${districts[0].id}`).click();
    await expect(page.getByTestId("gbc-reports-management-summary")).toContainText(districts[0].name);
    await expect(page.getByTestId("gbc-reports-selected")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("live-district-departments.png"), fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect(failures).toEqual([]);
  console.log(JSON.stringify({ city_departments: cityDepartments.length, configured_districts: districts.map(d => d.name), city_materials: cityData.material_total }));
});
