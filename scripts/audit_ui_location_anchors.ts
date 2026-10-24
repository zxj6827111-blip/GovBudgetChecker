/** 复用真正的 UI 转换器检查所有报告，避免只验证后端引用而漏掉前端跨页拼接。 */
import fs from "node:fs";
import path from "node:path";
import { toUiProblems, toUiSourceProblems, type JobDetailRecord } from "../app/lib/uiAdapters";

const root = path.resolve(process.argv[2] ?? "artifacts/location-round2");
const receipt = JSON.parse(fs.readFileSync(path.join(root, "location-audit.json"), "utf8"));
const results: unknown[] = [];
let checked = 0;
let refsChecked = 0;
let hidden = 0;
const errors: unknown[] = [];
for (const report of receipt.reports) {
  const payload = JSON.parse(fs.readFileSync(path.join(root, `${report.job_id}.json`), "utf8")) as JobDetailRecord;
  const rawResult = payload.result as Record<string, any>;
  const legacy = Array.isArray(rawResult.issues) ? rawResult.issues : rawResult.issues?.all ?? [];
  const originals = new Map([...(rawResult.ai_findings ?? []), ...(rawResult.rule_findings ?? []), ...legacy].map(item => [item.id, item]));
  const problems = new Map([...toUiProblems(payload), ...toUiSourceProblems(payload, "ai"), ...toUiSourceProblems(payload, "rule")].map(item => [item.id, item]));
  const records: unknown[] = [];
  for (const problem of problems.values()) {
    const targets = [{ page: problem.page, bbox: problem.bbox }, ...(problem.evidenceRefs ?? [])];
    for (const [index, target] of targets.entries()) {
      if (!target.bbox) { if (index === 0) hidden++; continue; }
      if (index === 0) checked++; else refsChecked++;
      const size = target.page ? report.page_sizes[target.page - 1] : undefined;
      const box = target.bbox;
      if (index === 0) {
        const evidence = originals.get(problem.id)?.evidence ?? [];
        if (!evidence.some((ref: any) => (ref.physical_page ?? ref.page) === target.page && ref.verification_status === "verified"
          && Array.isArray(ref.bbox) && ref.bbox.every((value: number, i: number) => Math.abs(value - box[i]) < 0.01))) {
          errors.push({ job_id: report.job_id, issue_id: problem.id, reason: "primary_box_does_not_match_verified_evidence", page: target.page });
        }
      }
      if (!size || !box.every(Number.isFinite) || box[0] < 0 || box[1] < 0 || box[2] > size[0] + 1 || box[3] > size[1] + 1 || box[2] <= box[0] || box[3] <= box[1]) {
        errors.push({ job_id: report.job_id, issue_id: problem.id, index, page: target.page, bbox: box, size });
      }
    }
    records.push({ id: problem.id, page: problem.page ?? null, boxed: Boolean(problem.bbox), references: problem.evidenceRefs?.map(ref => ({ page: ref.page, boxed: Boolean(ref.bbox) })) });
  }
  results.push({ job_id: report.job_id, findings: records });
}
const summary = { reports: results.length, primary_boxes: checked, reference_boxes: refsChecked, unboxed_items: hidden, geometry_errors: errors.length };
fs.writeFileSync(path.join(root, "ui-location-audit.json"), JSON.stringify({ summary, errors, reports: results }, null, 2));
console.log(JSON.stringify(summary));
if (errors.length) process.exitCode = 1;
