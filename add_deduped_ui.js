const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, 'app', 'app', 'components', 'OrganizationDetailView.tsx');
let content = fs.readFileSync(filePath, 'utf8');

// Add "已忽略" status logic in table
const targetContentLine = '{mergedTotal > 0 ? `${mergedTotal} 个问题` : "无问题"}';
if (!content.includes(targetContentLine)) {
    console.error("targetContentLine not found");
    process.exit(1);
}

const mapStart = content.indexOf('{virtualWindow.items.map((job) => {');
const mapEnd = content.indexOf('</button>\r\n                            </div>\r\n                          </td>', mapStart);
const mapEndAlt = content.indexOf('</button>\n                            </div>\n                          </td>', mapStart);
const actualEnd = mapEnd !== -1 ? mapEnd + 79 : mapEndAlt + 77;

let mapCode = content.substring(mapStart, actualEnd);

// Add calculated fields for redundant issues and tooltips
mapCode = mapCode.replace(
    'const mergedTotal = getDisplayIssueTotal(job);',
    `const mergedTotal = getDisplayIssueTotal(job);
                      const localTotal = typeof job.local_issue_total === "number" ? job.local_issue_total : job.issue_total || 0;
                      const aiTotal = job.ai_issue_total || 0;
                      const sourceTotal = localTotal + aiTotal;
                      const dedupedCount = sourceTotal > mergedTotal ? sourceTotal - mergedTotal : 0;
                      const isNormal = mergedTotal === 0;`
);

mapCode = mapCode.replace(
    '{mergedTotal > 0 ? `${mergedTotal} 个问题` : "无问题"}',
    `{isNormal ? "正常" : \`合并问题: \${mergedTotal}\`}
                                  </span>
                                )}
                                {dedupedCount > 0 && normalizeJobStatus(job.status) === "done" && (
                                  <span className="text-[10px] font-medium text-gray-400 bg-gray-100 px-1.5 py-0.5 rounded cursor-help" title={\`来源命中 \${sourceTotal} 项，已智能去重 \${dedupedCount} 项\`}>
                                    已去重 \${dedupedCount} 项`
);

content = content.substring(0, mapStart) + mapCode + content.substring(actualEnd);

// Replace generic "合并问题" text nearby
content = content.replace(
    '合并问题{filteredIssueTotal}',
    '合并问题(去重){filteredIssueTotal}'
);
content = content.replace(
    '<span className="text-xs text-gray-400 mb-0.5">合并问题</span>',
    '<span className="text-xs text-gray-400 mb-0.5" title="去重后的最终问题数">合并问题</span>'
);

fs.writeFileSync(filePath, content, 'utf8');
console.log('OrganizationDetailView updated logic for dedupe count');
