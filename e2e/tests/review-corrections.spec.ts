import { expect, test, type Page } from "../../app/node_modules/playwright/test";

// 「读取校正」页签：合成夹具取自真实材料的版式（预算单位财政拨款收支预算总表首行缺科目名称），
// 所有 API 请求均被拦截，不会写入任何任务。
const JOB_ID = "corrections-fixture";
const BASIS = `${JOB_ID}:rev-1`;

const grantTable = {
  fragment_uid: "a4dda1671af5816b", instance_uid: "a4dda1671af5816b", table_code: "FIN_04_fiscal_grant_total",
  title: "2024年预算单位财政拨款收支预算总表", page: 2, bbox: [56, 120, 560, 400], header_row_count: 2,
  extraction_method: "lines", scope: "core", mapping_status: "mapping_incomplete",
  column_mappings: [
    {source_col_idx: 1, canonical_measure: "income_budget"},
    {source_col_idx: 3, canonical_measure: "expenditure_budget"},
  ],
  rows: [
    ["财政拨款收入", null, "财政拨款支出", null, null],
    ["项目", "预算数", "项目", "合计", "一般公共预算"],
    ["", "3,949,153,400", "", "3,949,153,400", "3,949,153,400"],
    ["一、一般预算资金", "3,949,153,400", "一、科学技术支出", "56,460,000", "56,460,000"],
  ],
  row_boxes: [null, null, {page: 2, bbox: [56, 200, 560, 222]}, {page: 2, bbox: [56, 222, 560, 244]}],
  unresolved_money_cells: [
    {row: 2, column: 1, raw_text: "3,949,153,400", page: 2, bbox: [164, 200, 255, 222], reason: "source_label_missing"},
    {row: 2, column: 4, raw_text: "3,949,153,400", page: 2, bbox: [470, 200, 560, 222], reason: "amount_column_unmapped"},
  ],
  unqualified_ocr_cells: [], arithmetic_conflicts: [],
  measure_options: [
    {measure: "income_budget", label: "预算数"},
    {measure: "expenditure_budget", label: "合计"},
    {measure: "general_public_budget", label: "一般公共预算"},
  ],
};

async function correctionMocks(page: Page, posted: unknown[]) {
  await page.context().addCookies([{name: "gbc_session", value: "e2e-session", url: "http://127.0.0.1:3000", sameSite: "Lax"}]);
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const json = (body: unknown, status = 200) =>
      route.fulfill({status, contentType: "application/json", body: JSON.stringify(body)});
    if (path === `/api/jobs/${JOB_ID}`) return json({
      job_id: JOB_ID, filename: "读取校正测试.pdf", status: "review_required", report_year: 2024,
      result: {rule_findings: [], ai_findings: [], meta: {pages: 3, ai_execution: {state: "succeeded"}}},
    });
    if (path === `/api/jobs/${JOB_ID}/correction-targets`) return json({
      job_id: JOB_ID, analysis_basis_token: BASIS, reading_status: "partial", tables: [grantTable],
      gaps: [{id: "g1", page: 3, reason: "image_region_not_structured", bbox: [40, 60, 500, 700], affected_fragment_uids: []}],
      orphaned_amounts: [], overlay_status: null, history: [],
    });
    if (path === `/api/jobs/${JOB_ID}/corrections`) {
      posted.push(request.postDataJSON());
      return json({message: "校正已追加留痕，并已触发基于校正后事实的重新分析"}, 202);
    }
    if (path === "/api/auth/me") return json({user: {username: "corrections-reviewer", is_admin: true}});
    if (path === "/api/workflow") return json({issues: {}});
    if (path.startsWith("/api/files/")) return route.fulfill({
      status: 200, contentType: "image/svg+xml",
      body: `<svg xmlns="http://www.w3.org/2000/svg" width="960" height="1344"><rect width="100%" height="100%" fill="white"/></svg>`,
    });
    return json({});
  });
}

test("读取校正：补录缺失的科目名称、指定列含义，带原页位置一次提交", async ({page}) => {
  const posted: unknown[] = [];
  await page.setViewportSize({width: 1440, height: 900});
  await correctionMocks(page, posted);
  await page.goto(`/review?job=${JOB_ID}`);
  await page.getByTestId("gbc-review-tab-corrections").click();
  const card = page.getByTestId(`gbc-correction-table-${grantTable.fragment_uid}`);
  await expect(card).toContainText("待处理 2 格");
  await expect(card).toContainText("1 列未对应列含义");
  await expect(page.getByTestId("gbc-review-corrections-tab")).toContainText("图片区域没有读成表格");

  // 缺科目名称的行：点标签格，照原页填写。
  await card.locator("tbody tr").nth(3).locator("td").nth(0).click();
  await expect(page.getByTestId("gbc-review-evidence-label")).toContainText("对照原页");
  await page.getByLabel("原页上的内容").fill("本年收入合计");
  await page.getByRole("button", {name: "加入待提交"}).click();
  // 金额列不接受文字。
  await card.locator("tbody tr").nth(3).locator("td").nth(4).click();
  await page.getByLabel("原页上的内容").fill("三十九亿");
  await page.getByRole("button", {name: "加入待提交"}).click();
  await expect(page.getByTestId("gbc-correction-cell-editor")).toContainText("金额列只能填写数字");
  await page.getByLabel("第 5 列含义").selectOption("general_public_budget");

  const pending = page.getByTestId("gbc-correction-pending");
  await expect(pending).toContainText("待提交（2）");
  await page.getByRole("button", {name: /提交校正并重新分析/}).click();
  await expect(pending).toContainText("请填写校正说明");
  await page.getByLabel("校正说明").fill("对照原页第 2 页补录总计行科目名称");
  await page.getByRole("button", {name: /提交校正并重新分析/}).click();
  await expect(page.getByTestId("gbc-correction-pending")).toContainText("校正已追加留痕");

  expect(posted).toEqual([{
    analysis_basis_token: BASIS,
    reason: "对照原页第 2 页补录总计行科目名称",
    changes: [
      {target_type: "table_instance", target_id: grantTable.fragment_uid, field: "cell_value",
       value: {row_idx: 2, col_idx: 0, raw_value: "本年收入合计"},
       location: {physical_page: 2, bbox: [56, 200, 560, 222]}},
      {target_type: "table_instance", target_id: grantTable.fragment_uid, field: "column_mapping",
       value: {mappings: [{canonical_measure: "general_public_budget", source_col_idx: 4}]},
       location: {physical_page: 2, bbox: [470, 200, 560, 222]}},
    ],
  }]);
});
