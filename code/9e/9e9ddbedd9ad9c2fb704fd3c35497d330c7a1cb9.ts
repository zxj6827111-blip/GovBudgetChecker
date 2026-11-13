import assert from 'node:assert/strict';
import fs from 'node:fs';
import { toUiProblems } from '../../app/lib/uiAdapters';
const directory = 'outputs/annotation_audit_20261010';
const cases = JSON.parse(fs.readFileSync(`${directory}/ui_cases.json`, 'utf8'));
let problems = 0, cells = 0, roundingRelations = 0;
for (const detail of cases) {
  const items = toUiProblems(detail, { includeEvidencePreview: false });
  assert.equal(items.length, detail.review_report.issues.length + detail.review_report.tips.length, detail.name);
  assert.equal(items.length, detail.result.rule_findings.length, `${detail.name}: 复查结果不得被页级去重隐藏`);
  for (const item of items) {
    assert.equal(item.sourceIdentityVerified, true, `${detail.name}: ${item.ruleId}`);
    assert.equal(item.bbox?.length, 4, `${detail.name}: ${item.ruleId}`);
    assert.ok(item.page && item.page > 0, detail.name);
    for (const ref of item.evidenceRefs ?? []) {
      assert.equal(ref.verificationStatus, 'verified', `${detail.name}: ${ref.role}`);
      assert.equal(ref.bbox?.length, 4, `${detail.name}: ${ref.role}`);
      cells++;
    }
    for (const relation of item.roundingCases ?? []) {
      assert.ok(item.evidenceRefs?.some((ref) => ref.relationId === relation.relationId || (ref.operandId && relation.operandIds.includes(ref.operandId))), `${detail.name}: ${relation.summary}`);
      roundingRelations++;
    }
    problems++;
  }
}
fs.writeFileSync(`${directory}/ui_receipt.json`, JSON.stringify({ departments: cases.length, displayedFindings: problems, exactEvidenceLinks: cells, groupedRoundingRelations: roundingRelations, passed: true, scope: '真实复查结果通过生产页面适配器；不包含浏览器点击验收' }, null, 2));
console.log({ departments: cases.length, displayedFindings: problems, exactEvidenceLinks: cells, groupedRoundingRelations: roundingRelations, passed: true });
