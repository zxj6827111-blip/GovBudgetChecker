import assert from "node:assert/strict";

import {
  WRITING_SOURCE_LABELS,
  adoptionLabel,
  amountNeed,
  auditDetail,
  templateSummary,
} from "../app/components/review-workbench/materialDimensionLabels";

const forward = {
  available: true, version: "2024", adoption_kind: "not_received", source_report_year: 2024, target_year: 2025,
  capability_scope: { writing_auto: true }, package_id: "pkg-1", content_sha256: "abcdef0123456789", revision: 3,
};
assert.equal(templateSummary(forward), "采用 2024 版模板（未收到新版，沿用参考，2024 年模板用于 2025 年度）");
// 确认无变化与暂未收到新版必须分开显示。
assert.equal(adoptionLabel({ ...forward, adoption_kind: "verified_same" }), "已核对与旧版一致");
assert.notEqual(adoptionLabel({ adoption_kind: "verified_same" }), adoptionLabel({ adoption_kind: "not_received" }));
// 主界面不出现包 ID 或哈希，审计细节单独提供。
assert.doesNotMatch(templateSummary(forward), /pkg-1|abcdef/);
assert.match(auditDetail(forward, "material-writing-v3.6"), /pkg-1 · 内容摘要 abcdef012345 · 年度绑定修订 3 · 算法 material-writing-v3\.6/);
// 回滚显示原沿用方式；缺原始依据时如实标待核。
assert.equal(adoptionLabel({ adoption_kind: "rollback", basis: JSON.stringify({ original_adoption_kind: "issued" }) }), "当年正式下发（恢复历史修订）");
assert.equal(adoptionLabel({ adoption_kind: "rollback", basis: "not-json" }), "恢复历史修订（原沿用方式待核）");
assert.match(templateSummary({ ...forward, adoption_kind: "issued", target_year: 2024, capability_scope: {} }), /^采用 2024 版模板（当年正式下发）；仅作参考/);
assert.equal(templateSummary({ available: false, reason: "年度模板未绑定" }), "模板对照未启用：年度模板未绑定");
assert.match(amountNeed(["CMM-018", "V33-236"]), /仍有 2 项检查.*CMM-018、V33-236/);
assert.match(amountNeed([]), /可暂不填写/);

// 阶段 0：上传所选层级只是检查基准，不写“身份已核实”。
assert.equal(WRITING_SOURCE_LABELS.archive, "按上传所选层级作为检查基准");
assert.doesNotMatch(JSON.stringify(WRITING_SOURCE_LABELS), /身份已核实/);
assert.equal(WRITING_SOURCE_LABELS.human, "人工确认");
assert.equal(WRITING_SOURCE_LABELS.declaration, "原文明示");

console.log("materialDimensionLabels.test.ts passed");
