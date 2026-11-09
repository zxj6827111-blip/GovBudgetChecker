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
assert.equal(issueCategoryId(item("missing", "V33-002", "缺失表：支出决算表")), "structure");
assert.equal(issueCategoryId(item("empty", "V33-114", "空表缺少说明")), "structure");
assert.equal(issueCategoryId(item("identity", "CMM-018", "主体口径混用")), "scope");
assert.equal(issueCategoryId(item("personnel", "V33-223", "说明6↔T6人员经费不一致")), "amount");
assert.equal(issueCategoryId(item("structured", "OTHER", "金额识别待核", { source: "structured_ingest" })), "coverage");
assert.equal(issueCategoryId(item("coverage", "OTHER", "应检查事项中10项未完成")), "coverage");
assert.equal(issueCategoryId(item("unknown", "OTHER", "待核事项")), "other");

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
