const fs = require('fs');
const path = require('path');

const files = [
  'app/app/components/PipelineStatus.tsx',
  'app/app/components/JobSidebar.tsx',
  'app/app/components/QCResultView.tsx'
];

for (const relPath of files) {
  const p = path.join(__dirname, relPath);
  if (!fs.existsSync(p)) continue;
  let code = fs.readFileSync(p, 'utf8');
  // replace literal backslash followed by backtick
  code = code.replace(/\\`/g, '`');
  // replace literal backslash followed by dollar sign
  code = code.replace(/\\\$/g, '$');
  fs.writeFileSync(p, code, 'utf8');
}
console.log('Fixed syntax errors');
