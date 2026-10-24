import assert from "node:assert/strict";
import { directoryMaterialStatus, materialHistoryHref, openReviewCount, reviewLabel, versionSource, type MaterialVersion } from "../app/components/reports/materialReportAdapters";

assert.equal(reviewLabel({ status: "completed", confirmed_count: 6 }), "已复核 · 确认问题 6 项");
assert.equal(reviewLabel({ status: "completed", confirmed_count: 0 }), "已复核 · 未确认问题");
assert.equal(reviewLabel({ status: "completed", confirmed_count: null }), "已复核 · 问题数未记录");
assert.equal(reviewLabel({ status: "invalidated", confirmed_count: null }), "需重新复核");
const version: MaterialVersion = { document_version_id: 132, version_number: 1, is_current: true, review: { status: "pending", confirmed_count: null }, source_job_id: "job", file_hash: "a".repeat(64) };
assert.equal(versionSource(version), `/api/files/job/source?checksum=${"a".repeat(64)}`);
assert.equal(versionSource({ ...version, source_job_id: null }), null);
assert.equal(versionSource({ ...version, file_hash: "unknown" }), null);
console.log("Material review labels and exact-file download regressions passed");

// 部门目录的最新材料状态：没有材料写“暂无”，不推断应交未交；分析未完成按运行状态区分。
const latest = { fiscal_year: 2025, confirmed_count: null, run_status: "review_required" };
assert.deepEqual(directoryMaterialStatus(undefined), { label: "暂无", tone: "neutral" });
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "completed", confirmed_count: 9 }), { label: "已复核 · 9 项问题", tone: "done" });
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "completed", confirmed_count: 0 }), { label: "已复核 · 0 项问题", tone: "done" });
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "completed" }), { label: "已复核", tone: "done" }, "问题数未记录时不写成 0");
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "pending" }), { label: "待复核", tone: "review" });
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "invalidated" }), { label: "需重新复核", tone: "review" });
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "in_progress" }), { label: "复核中", tone: "processing" });
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "waiting_analysis", run_status: "analyzing" }), { label: "分析中", tone: "processing" });
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "waiting_analysis", run_status: "error" }), { label: "分析失败", tone: "failed" });
assert.deepEqual(directoryMaterialStatus({ ...latest, review_status: "waiting_analysis", run_status: null }), { label: "待分析", tone: "neutral" });
assert.equal(openReviewCount({ pending: 2, in_progress: 1, invalidated: 3, completed: 9, waiting_analysis: 4 }), 6);
assert.equal(openReviewCount(undefined), 0);
console.log("Department directory material status labels passed");

assert.equal(materialHistoryHref("slot 1", "/reports/finance?year=2025"), "/materials/slots/slot%201?tab=versions&returnTo=%2Freports%2Ffinance%3Fyear%3D2025");
console.log("Material history link passed");
