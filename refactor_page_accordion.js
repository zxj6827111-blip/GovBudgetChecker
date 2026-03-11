const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, 'app', 'app', 'page.tsx');
let content = fs.readFileSync(filePath, 'utf8');

if (!content.includes("import ActionAccordion from")) {
    content = content.replace(
        "import IssueCard from \"./components/IssueCard\";",
        "import IssueCard from \"./components/IssueCard\";\nimport ActionAccordion from \"./components/ActionAccordion\";"
    );
}

const targetStart = content.indexOf('<div className="mt-4 overflow-hidden rounded-2xl');
if (targetStart === -1) {
    console.error("targetStart not found in page.tsx");
    process.exit(1);
}

const targetEnd = content.indexOf('</div>\n          </div>\n          <OrganizationTree', targetStart);
if (targetEnd === -1) {
    console.error("targetEnd not found in page.tsx");
    process.exit(1);
}

const newAccordion = `<div className="mt-4">
              <ActionAccordion
                title="快捷操作"
                description="默认收起，按需查阅批量重跑或清理"
                isOpen={showSidebarTools}
                onToggle={() => setShowSidebarTools((prev) => !prev)}
                icon={<svg className="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" /><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" /></svg>}
              >
                <div>
                  <button
                    onClick={handleGlobalReanalyze}
                    disabled={isGlobalReanalyzing}
                    className="inline-flex w-full items-center justify-center rounded-xl border border-amber-200 bg-gradient-to-r from-amber-50 to-orange-50 px-4 py-2.5 text-sm font-semibold text-amber-700 shadow-sm transition-all hover:border-amber-300 hover:shadow disabled:cursor-not-allowed disabled:opacity-60"
                    title="按每个部门的最新报告批量创建新的重分析任务"
                  >
                    {isGlobalReanalyzing ? "批量重分析中..." : "按部门重分析"}
                  </button>
                  <p className="mt-2 text-[11px] leading-5 text-amber-700/80">
                    每个部门只重跑最新一份报告，默认跳过正在分析中的任务。
                  </p>
                </div>
                <div>
                  <button
                    onClick={() => handleStructuredCleanup()}
                    disabled={!!structuredCleanupBusyScope || isStructuredCleanupExecuting}
                    className="inline-flex w-full items-center justify-center rounded-xl border border-sky-200 bg-gradient-to-r from-sky-50 to-cyan-50 px-4 py-2.5 text-sm font-semibold text-sky-700 shadow-sm transition-all hover:border-sky-300 hover:shadow disabled:cursor-not-allowed disabled:opacity-60"
                    title="清理数据库中的旧版记录，前台合并问题不变"
                  >
                    {structuredCleanupBusyScope === "all"
                      ? (isStructuredCleanupExecuting ? "清理中..." : "加载预览...")
                      : "清理旧版入库"}
                  </button>
                  <p className="mt-2 text-[11px] leading-5 text-sky-700/80">
                    清理旧记录，不删除原始报告和前台的合并问题记录。
                  </p>
                </div>
              </ActionAccordion>
            </div>`;

content = content.substring(0, targetStart) + newAccordion + content.substring(targetEnd);
fs.writeFileSync(filePath, content, 'utf8');
console.log('page.tsx refactored successfully.');
