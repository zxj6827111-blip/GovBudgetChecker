import assert from "node:assert/strict";

import {
  formatDocScopeText,
  formatRuleCountText,
  formatRulesValueText,
  resolveSeverityTone,
} from "../app/components/rules/rulesPageAdapters";

// 本文件测试 Task 8.3 规则与版本页的纯逻辑层。核心红线：
// 版本值读不到必须显示"未识别到"，本模块不含任何硬编码版本常量。

// --- formatRulesValueText ----------------------------------------------------------

assert.equal(formatRulesValueText("v3_3_all_in_one"), "v3_3_all_in_one");
assert.equal(formatRulesValueText(null), "未识别到", "REGRESSION: 版本读不到必须显示'未识别到'");
assert.equal(formatRulesValueText(undefined), "未识别到");
assert.equal(formatRulesValueText(""), "未识别到", "空字符串视为未识别，不得显示空白");
assert.equal(formatRulesValueText("   "), "未识别到", "纯空白字符串视为未识别");

// --- formatRuleCountText ------------------------------------------------------------

assert.equal(formatRuleCountText(15), "15");
assert.equal(formatRuleCountText(0), "0", "真实的 0 条规则必须显示 0（不是'未识别到'）");
assert.equal(formatRuleCountText(null), "未识别到", "读不到条目数（文件不可读）必须显示'未识别到'");

// --- resolveSeverityTone -------------------------------------------------------------

assert.equal(resolveSeverityTone("high"), "danger");
assert.equal(resolveSeverityTone("critical"), "danger");
assert.equal(resolveSeverityTone("medium"), "warning");
assert.equal(resolveSeverityTone("low"), "neutral");
assert.equal(resolveSeverityTone(""), "neutral", "未知级别归中性，不得冒充高危或中危");

// --- formatDocScopeText --------------------------------------------------------------

assert.equal(formatDocScopeText(["预算", "决算"]), "预算、决算");
assert.equal(formatDocScopeText([]), "—", "无适用范围声明用中性占位——与'识别失败'是两回事");
assert.equal(formatDocScopeText(["预算公开"]), "预算公开");

console.log("rulesPageAdapters.test.ts: all assertions passed");
