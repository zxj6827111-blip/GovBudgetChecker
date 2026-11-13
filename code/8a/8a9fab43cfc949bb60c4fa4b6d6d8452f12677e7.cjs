const path = require('node:path');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = process.cwd();
const React = require(path.join(root, 'app/node_modules/react'));
const ts = require(path.join(root, 'app/node_modules/typescript'));
const { renderToStaticMarkup } = require(path.join(root, 'app/node_modules/react-dom/server'));
const jiti = require(path.join(root, 'app/node_modules/jiti'))(path.join(root, 'app/queue-display-audit.cjs'), {
  alias: {'@/components': path.join(root, 'app/app/components'), '@/lib': path.join(root, 'app/lib')},
  extensions: ['.ts', '.tsx', '.js', '.jsx', '.json', '.mjs', '.cjs'], cache: false,
  transform: ({source, filename}) => ({code: ts.transpileModule(source, {fileName: filename,
    compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2020, esModuleInterop: true}}).outputText}),
});
const { WorkbenchQueueTable } = jiti('./app/components/workspace/WorkbenchQueueTable.tsx');
const directory = path.join(root, 'outputs/queue_display_audit_20261010');
const jobs = JSON.parse(fs.readFileSync(path.join(directory, 'queue_cases.json'), 'utf8'));
const html = renderToStaticMarkup(React.createElement(WorkbenchQueueTable, {jobs, onReanalyze: () => {}}));
for (const job of jobs) {
  const row = html.split(`data-testid="gbc-workbench-queue-row-${job.job_id}"`)[1]?.split('</tr>')[0];
  assert.ok(row, job.filename);
  assert.ok(row.includes(`${job.page_count} 页`), `${job.filename}: 总页数`);
  assert.ok(row.includes(`${job.review_issue_total + job.review_candidate_total} 问题`), `${job.filename}: 材料问题数`);
  assert.ok(row.includes(`${job.tip_total} 条提示`), `${job.filename}: 提示数`);
  if (job.review_notice_total > 0) {
    assert.ok(row.includes(`${job.review_notice_total} 项复核事项`), `${job.filename}: 复核事项数`);
  }
}
fs.writeFileSync(path.join(directory, 'rendered-table.html'), html);
fs.writeFileSync(path.join(directory, 'render_receipt.json'), JSON.stringify({departments: jobs.length,
  renderedRows: jobs.length, allCountsMatch: true,
  scope: '真实任务摘要通过生产React表格组件服务端渲染；不含登录后浏览器点击验收'}, null, 2));
console.log('PASS actual queue table rendered for', jobs.length, 'departments');
