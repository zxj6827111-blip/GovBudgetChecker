// Render the candidate's actual adapter/card locally; no browser/server/network.
const fs = require('fs');
const path = require('path');
const Module = require('module');
const code = '/Volumes/workSSD/software development/gbc-worktrees/auto-check-remediation-r22';
const appRequire = Module.createRequire(path.join(code, 'app/package.json'));
const ts = appRequire('typescript');
for (const ext of ['.ts', '.tsx']) {
  require.extensions[ext] = (mod, file) => {
    const originalRequire = mod.require.bind(mod);
    // The workflow badge is unrelated to the blocker/location paths under review.
    if (file.endsWith('/ObligationCard.tsx')) {
      mod.require = (name) => name === './IssueCard'
        ? { WorkflowStatusBadge: () => null } : originalRequire(name);
    }
    const source = fs.readFileSync(file, 'utf8');
    mod._compile(ts.transpileModule(source, { compilerOptions: {
      module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
      jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true,
    }, fileName: file }).outputText, file);
  };
}
const React = appRequire('react');
const { renderToStaticMarkup } = appRequire('react-dom/server');
const base = path.join(code, 'app/app/components/review-workbench');
const { toObligationProblem, obligationAutoEvidence } = require(path.join(base, 'obligationProblems.ts'));
const { ObligationCard } = require(path.join(base, 'ObligationCard.tsx'));
const operands = Array.from({ length: 10 }, (_, i) => ({
  page: 8, row_key: `独立关系${i+1}`, column: '决算数', role: `缺数${i+1}`,
  check_relation_id: `V33-X:relation:${i+1}`,
}));
const item = {
  obligation_id: 'OBL-REVIEW-PROBE', title: '收入表勾稽', status: 'insufficient_data',
  detail: '多条关系缺数', reason: 'operand_missing', decision: null,
  missing_operands: operands, blocked_by: [],
  locations: [{ page: 8, label: '收入表', bbox: [1,2,3,4], related: [
    { page: 8, label: '第二格', bbox: [10,20,30,40], related: [] },
  ] }],
};
const problem = toObligationProblem(item, [item]);
const html = renderToStaticMarkup(React.createElement(ObligationCard, {
  item, problem, allItems: [item], workflowStatus: 'pending',
}));
fs.writeFileSync(path.join(__dirname, 'card_render.html'), html);
const result = {
  scope: 'Actual candidate adapter and React server render; workflow badge stubbed only; no browser/API write',
  input_frames: 2, output_evidence_refs: problem.evidenceRefs.length,
  output_bboxes: problem.evidenceRefs.map(r => r.bbox),
  input_blockers: 10, rendered_blocker_li_count: (html.match(/<li\b/g) || []).length,
  renders_seventh_blocker: html.includes('独立关系7'),
  labels_full_blocker_count: html.includes('阻断点清单（10）'),
  automatic_evidence_from_location_only: obligationAutoEvidence(item),
};
fs.writeFileSync(path.join(__dirname, 'frontend_results.json'), JSON.stringify(result, null, 2)+'\n');
console.log(JSON.stringify(result, null, 2));
