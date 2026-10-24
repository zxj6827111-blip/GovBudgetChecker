import assert from "node:assert/strict";
import { fileAnalysisRevision, reportDownloadUrl, recognitionStateLabel } from "../lib/reportVersion";
import { selectStructuredForJob, toUiTask } from "../lib/uiAdapters";

assert.equal(fileAnalysisRevision(12), undefined, "数据库整数代际不能当作文件版本");
assert.equal(fileAnalysisRevision(undefined), undefined);
assert.throws(() => fileAnalysisRevision("../different"));
assert.throws(() => reportDownloadUrl("job", "pdf", "nested/revision"));
const url = new URL(reportDownloadUrl("job with spaces", "json", "rev-old"), "http://localhost");
assert.equal(url.searchParams.get("job_id"), "job with spaces");
assert.equal(url.searchParams.get("analysis_revision"), "rev-old");
assert.equal(url.searchParams.get("format"), "json");
assert.equal(toUiTask({ job_id: "job", analysis_revision: "rev-loaded" }).analysisRevision, "rev-loaded");
assert.equal(toUiTask({ job_id: "job", analysis_revision: 4 }).analysisRevision, undefined);
const displayed = { job_id: "job", analysis_revision: "rev-running", fact_snapshot: { revision_id: "rev-visible" },
  structured_ingest: { facts_count: 3, fact_snapshot: { revision_id: "rev-visible" } } };
const later = { facts_count: 99, fact_snapshot: { revision_id: "rev-newer" } };
assert.equal(toUiTask(displayed, later).analysisRevision, "rev-visible");
assert.equal(selectStructuredForJob(displayed, later).facts_count, 3);
assert.deepEqual(selectStructuredForJob({ ...displayed, structured_ingest: undefined }, later), {});
console.log("report version identity passed");
assert.equal(recognitionStateLabel(null), undefined);
assert.equal(recognitionStateLabel({ source_bound_cells: null }), undefined);
assert.equal(recognitionStateLabel({ source_bound_cells: 2, pending_adjudication_cells: 1,
  pending_processing_pages: [1], storage_consistent: true }), "已恢复 2 格 · 待裁定 1 格 · 待处理 1 页 · 存储一致");
