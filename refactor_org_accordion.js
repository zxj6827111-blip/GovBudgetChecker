const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, 'app', 'app', 'components', 'OrganizationDetailView.tsx');
let content = fs.readFileSync(filePath, 'utf8');

if (!content.includes("import ActionAccordion from")) {
    content = content.replace(
        "import React,",
        "import React,\nimport ActionAccordion from \"./ActionAccordion\";"
    );
     content = content.replace(
        "import {\n  useState,\n  useMemo,",
        "import ActionAccordion from \"./ActionAccordion\";\nimport {\n  useState,\n  useMemo,"
    );
}

const targetStart = content.indexOf('<div className="mt-4 overflow-hidden rounded-2xl');
if (targetStart === -1) {
    console.error("targetStart not found in OrganizationDetailView.tsx");
    process.exit(1);
}

const targetEnd = content.indexOf('</div>\n            </div>\n          </div>', targetStart);
if (targetEnd === -1) {
    console.error("targetEnd not found in OrganizationDetailView.tsx");
    process.exit(1);
}

const newAccordion = `<div className="mt-4">
              <ActionAccordion
                title="快捷操作"
                description="默认收起，按需查阅当前部门上传或旧版清理"
                isOpen={showHeaderActions}
                onToggle={() => setShowHeaderActions((prev) => !prev)}
                icon={<svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>}
              >
                {onCleanupStructuredHistory && (
                  <div>
                    <button
                      type="button"
                      onClick={() => onCleanupStructuredHistory({ departmentId, departmentName })}
                      disabled={isCleaningStructuredHistory}
                      className="inline-flex w-full items-center justify-center rounded-xl border border-sky-200 bg-gradient-to-r from-sky-50 to-cyan-50 px-4 py-2.5 text-sm font-semibold text-sky-700 shadow-sm transition-all hover:border-sky-300 hover:shadow disabled:cursor-not-allowed disabled:opacity-60"
                      title="清理当前部门下旧版结构化记录，不删审查合并问题"
                    >
                      {isCleaningStructuredHistory ? "清理旧版入库中..." : "清理本部门旧版入库"}
                    </button>
                    <p className="mt-2 text-[11px] leading-5 text-sky-700/80">
                      只清理本部门旧版结构化入库记录，不删除原始报告和前台合并问题。
                    </p>
                  </div>
                )}
                <div>
                  <button
                    onClick={() => onUpload(selectedUnit)}
                    disabled={!selectedUnit}
                    className="inline-flex w-full items-center justify-center rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-all hover:bg-indigo-700 hover:shadow disabled:cursor-not-allowed disabled:opacity-60"
                    title={selectedUnit ? "上传到当前组织" : "请先选择部门或单位"}
                  >
                    上传报告
                  </button>
                  <p className="mt-2 text-[11px] leading-5 text-indigo-700/80">
                    将当前部门或单位的新报告上传到系统，并刷新合并统计。
                  </p>
                </div>
              </ActionAccordion>
            </div>`;

content = content.substring(0, targetStart) + newAccordion + content.substring(targetEnd);
fs.writeFileSync(filePath, content, 'utf8');
console.log('OrganizationDetailView.tsx refactored successfully.');
