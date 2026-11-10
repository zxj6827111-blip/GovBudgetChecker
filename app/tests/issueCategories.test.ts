import assert from "node:assert/strict";
import { groupProblemsByCategory, issueCategoryId } from "../lib/issueCategories";
import type { Problem } from "../lib/mock";

function item(id: string, ruleId: string, title: string, extra: Partial<Problem> = {}): Problem {
  return { id, ruleId, title, description: title, severity: "low", category: "AI 智能分析",
    suggestion: "核对原文", snippet: "原文", evidenceImage: "", status: "pending", ...extra };
}

const pairs = [
  [item("format-rule", "CMM-010", "金额未分千分位"), item("format-ai", "AI-TEXT-005", "金额书写格式：79,85.49")],
  [item("template-rule", "CMM-005", "疑似模板残留：增减方向未确定"), item("template-ai", "AI-TPL-009", "占位符未清理")],
  [item("name-rule", "V33-227", "类级名称错误"), item("name-ai", "AI-CODE-000", "科目名称或层级不匹配")],
  [item("amount-rule", "V33-117", "人员经费合计不符"), item("amount-ai", "AI-XSEC-005", "人员经费表文矛盾")],
  [item("direction-rule", "V33-110", "预算与决算比较不一致"), item("direction-ai", "AI-DIR-001", "增减方向与数字矛盾")],
];
for (const [rule, ai] of pairs) {
  assert.equal(issueCategoryId(rule), issueCategoryId(item(ai.id, ai.ruleId, ai.title, { ...ai, source: "ai", severity: "manual_review" })),
    "业务相同的规则、AI、已核实与待核实条目不能按来源拆类");
}
assert.equal(issueCategoryId(item("ratio", "V33-234", "同比比例与同条金额不一致")), "percentage");
assert.equal(issueCategoryId(item("ratio-2", "OTHER", "占比合计不是100%")), "percentage");
assert.equal(issueCategoryId(item("procurement-sum", "V33-234", "政府采购总额与货物、工程、服务三项之和不一致")), "amount",
  "真实页面的采购构成求和不能按共用V33-234编号归入同比类");
assert.equal(issueCategoryId(item("total-sum", "OTHER", "支出合计与明细之和不一致")), "amount");
assert.equal(issueCategoryId(item("procurement-ratio", "V33-234", "政府采购分项占比合计与总额比例不一致")), "percentage");
assert.equal(issueCategoryId(item("sum-format", "CMM-010", "总计与分项小数位不一致")), "number_format");
assert.equal(issueCategoryId(item("missing", "V33-002", "缺失表：支出决算表")), "structure");
assert.equal(issueCategoryId(item("empty", "V33-114", "空表缺少说明")), "structure");
assert.equal(issueCategoryId(item("identity", "CMM-018", "主体口径混用")), "scope");
assert.equal(issueCategoryId(item("personnel", "V33-223", "说明6↔T6人员经费不一致")), "amount");
assert.equal(issueCategoryId(item("structured", "OTHER", "金额识别待核", { source: "structured_ingest" })), "coverage");
assert.equal(issueCategoryId(item("coverage", "OTHER", "应检查事项中10项未完成")), "coverage");
assert.equal(issueCategoryId(item("unknown", "OTHER", "待核事项")), "other");

// 345份真实报告中曾落入“其他”的已知业务类型。规则映射只确定展示入口，
// 跨表差额仍保留待核状态，单位规范也不等同于已证实金额数量级错误。
const knownCases = [
  ["CMM-004", "同一科目在收入决算表与支出决算表金额差异较大", "amount"],
  ["CMM-007", "同比零基数/增减额与增长百分比复算不一致", "percentage"],
  ["V33-TREND-COMPLETION-RATE", "预算完成率分母为0/分母身份/复算不一致", "percentage"],
  ["CMM-002", "中文语境使用半角逗号/分号，应使用全角逗号/分号", "wording"],
  ["CMM-009", "漏字", "wording"],
  ["V33-113", "重复用词", "wording"],
  ["BUD-002", "可能存在未填充内容", "wording"],
  ["CMM-011", "金额单位疑似漏写万字", "number_format"],
  ["CMM-011", "零值金额单位写作元", "number_format"],
  ["V33-222", "金额单位口径错误，表文金额相差10000倍", "amount"],
  ["V33-232", "规范性提示", "number_format"],
  ["V33-DISCLOSURE-PERCENT-UNIT", "百分比单位缺失", "number_format"],
  ["V33-246", "公务接待说明未披露国内公务接待批次、人次", "structure"],
  ["V33-SG-COMPLETION", "三公经费数量要素未披露", "structure"],
  ["CMM-023", "报告缺少第四部分", "structure"],
  ["BUD-001", "缺失预算表", "structure"],
  ["V33-003", "页数过少", "structure"],
  ["CMM-016", "报告文字页整体横向排版", "structure"],
  ["CMM-012", "年度表述与报告年度不一致", "scope"],
  ["CMM-012", "比较年度疑似写成本年度", "scope"],
  ["CMM-012", "年份书写有误：20265年", "wording"],
  ["rules_not_executed", "rules_not_executed", "coverage"],
  ["CMM-020", "经营支出占比与金额不符", "percentage"],
  ["V33-103", "财政拨款收入占比：表算99.69% ≠ 文本99.89%", "percentage"],
  ["V33-104", "项目支出占比：表算21.40% ≠ 文本20.40%", "percentage"],
  ["V33-103", "财政拨款收入", "amount"],
  ["V33-104", "项目支出", "amount"],
  ["V33-004", "出现负数：-31.98", "amount"],
  ["V33-122", "政府性基金决算表存在非零金额，但财政拨款总表对应资金列明确为零", "amount"],
  ["V33-201", "T1↔T3支出合计不一致：T1=100.00, T3=80.00", "amount"],
  ["V33-201", "T3(支出决算表)缺失，无法与T1支出合计进行勾稽", "structure"],
  ["V33-234", "说明条目序号由7跳到9，建议核对编号连续性；不能据此判定科目内容缺失。", "wording"],
  ["V33-224", "公务用车购置及运行维护费占三公经费决算合计的比例不成立", "percentage"],
  ["table_reading_incomplete", "表格取数未完成，部分金额无法核对", "coverage"],
] as const;
for (const [rule, title, expected] of knownCases) {
  assert.equal(issueCategoryId(item(`known-${rule}-${title}`, rule, title)), expected, title);
}

for (const rule of ["OBL-TXT-T1", "OBL-TXT-T4", "OBL-NARRATIVE-AMOUNT"]) {
  assert.equal(issueCategoryId(item(rule, rule, "金额已披露，但原文单位名称与材料主体不一致")), "scope");
}
assert.equal(issueCategoryId(item("t5", "OBL-TXT-T5", "说明中的款“行政事业单位养老支出”在表内找不到同名科目")), "scope");
assert.equal(issueCategoryId(item("completion", "OBL-TREND-COMPLETION-RATE", "预算完成率分母口径与复算")), "percentage");

// 分类不等于去掉核对关系：同类但不同科目、文件、状态的原始事项全部保留。
const a = item("amount-a", "V33-234", "机关运行费同比错误", { jobId: "file-a", page: 33 });
const b = item("amount-b", "V33-234", "财政收支总计同比错误", { jobId: "file-a", page: 24 });
const c = item("amount-c", "V33-234", "机关运行费同比错误", { jobId: "file-b", page: 24, status: "ignored" });
const input = [...pairs.flat(), a, b, c];
const snapshot = JSON.stringify(input);
const groups = groupProblemsByCategory(input);
assert.equal(new Set(groups.map((group) => group.id)).size, groups.length, "一类只产生一个展示组");
assert.deepEqual(new Set(groups.flatMap((group) => group.items.map((problem) => problem.id))), new Set(input.map((problem) => problem.id)),
  "归类后不丢任何问题身份");
assert.equal(groups.find((group) => group.id === "percentage")?.items.length, 3);
assert.ok(groups.flatMap((group) => group.items).every((problem) => input.includes(problem)), "不改写证据和工作流对象");
assert.equal(JSON.stringify(input), snapshot);
console.log("issueCategories.test.ts passed");
