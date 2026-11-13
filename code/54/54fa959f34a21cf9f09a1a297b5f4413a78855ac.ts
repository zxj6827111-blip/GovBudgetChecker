import assert from "node:assert/strict";
import { reviewLabel, versionSource, type MaterialVersion } from "../app/components/reports/materialReportAdapters";

assert.equal(reviewLabel({ status: "completed", confirmed_count: 6 }), "已复核 · 确认问题 6 项");
assert.equal(reviewLabel({ status: "completed", confirmed_count: 0 }), "已复核 · 未确认问题");
assert.equal(reviewLabel({ status: "completed", confirmed_count: null }), "已复核 · 问题数未记录");
assert.equal(reviewLabel({ status: "invalidated", confirmed_count: null }), "需重新复核");
const version: MaterialVersion = { document_version_id: 132, version_number: 1, is_current: true, review: { status: "pending", confirmed_count: null }, source_job_id: "job", file_hash: "a".repeat(64) };
assert.equal(versionSource(version), `/api/files/job/source?checksum=${"a".repeat(64)}`);
assert.equal(versionSource({ ...version, source_job_id: null }), null);
assert.equal(versionSource({ ...version, file_hash: "unknown" }), null);
console.log("Material review labels and exact-file download regressions passed");
