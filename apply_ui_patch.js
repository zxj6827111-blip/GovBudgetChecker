const fs = require('fs');
const path = require('path');

const filePath = path.join(__dirname, 'app', 'app', 'components', 'OrganizationDetailView.tsx');
let content = fs.readFileSync(filePath, 'utf8');

// Replace renderOrganizationCard
const cardRenderStart = content.indexOf('const renderOrganizationCard = (org: UnitItem) => {');
const returnDivStart = content.indexOf('<div key={org.id} className={`relative flex flex-col bg-white rounded-2xl', cardRenderStart);

// We find the ending `  };` for the function
let endIdx = content.indexOf('  };\n\n  return (', returnDivStart);
if (endIdx === -1) {
  endIdx = content.indexOf('  };\r\n\r\n  return (', returnDivStart);
}
if (endIdx === -1) {
    endIdx = content.indexOf('  };', returnDivStart);
}
const returnDivEnd = endIdx + 4;

const newCardCode = `  const renderOrganizationCard = (org: UnitItem) => {
    const active = selectedUnitId === org.id;
    const fallbackStats = orgStatsMap[org.id];
    const loadedIssueTotal = jobs.reduce((sum, item) => sum + getDisplayIssueTotal(item), 0);
    const activeHasPartialJobs = jobsTotal > 0 && jobs.length < jobsTotal;
    const liveStats = active
      ? {
          job_count: jobsTotal || jobs.length,
          issue_total: activeHasPartialJobs
            ? (fallbackStats?.issue_total ?? loadedIssueTotal)
            : loadedIssueTotal,
          has_issues: activeHasPartialJobs
            ? (fallbackStats?.issue_total ?? loadedIssueTotal) > 0
            : jobs.some((item) => getDisplayIssueTotal(item) > 0),
        }
      : undefined;
    const stats = liveStats || fallbackStats;
    const hasKnownStats = !!stats;
    const jobCount = stats?.job_count ?? 0;
    const hasJobs = hasKnownStats ? jobCount > 0 : true;
    const isDepartment = org.level === "department";
    const isUnit = org.level === "unit";
    const orgDisplayName = !isDepartment && org.name === departmentName ? \`\${org.name} (Local Unit)\` : org.name;
    const orgIssueTotal = stats?.issue_total ?? 0;
    const isDeletingThisUnit = deletingUnitId === org.id;

    return (
      <div 
        key={org.id} 
        onClick={() => handleOrganizationCardAction(org, hasJobs)}
        className={\`group relative flex flex-col bg-white rounded-xl border transition-all duration-200 cursor-pointer shadow-sm hover:shadow-md \${active ? "border-indigo-400 ring-1 ring-indigo-400" : "border-gray-200 hover:border-gray-300"}\`}
      >
        <div className="p-5 flex-1 pl-6 relative">
          {active && <div className="absolute left-0 top-0 bottom-0 w-1 bg-indigo-500 rounded-l-xl" />}
          <div className="flex items-start justify-between gap-2 mb-2">
            <h3 className="font-semibold text-gray-800 text-base flex-1 line-clamp-2" title={orgDisplayName}>{orgDisplayName}</h3>
            {isUnit && (
              <button
                type="button"
                onClick={(e) => { e.stopPropagation(); handleDeleteUnit(org, e); }}
                disabled={isDeletingThisUnit}
                className="opacity-0 group-hover:opacity-100 transition-opacity inline-flex items-center justify-center p-1 rounded-md text-gray-400 hover:text-red-500 hover:bg-red-50 disabled:opacity-50"
                title={isDeletingThisUnit ? "删除中..." : "删除单位"}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
              </button>
            )}
          </div>
          <div className="flex items-center gap-4 mt-4">
            <div className="flex flex-col">
              <span className="text-xs text-gray-400 mb-0.5">文件数</span>
              <span className="text-sm font-semibold text-gray-700">{hasKnownStats ? jobCount : "-"}</span>
            </div>
            <div className="h-6 w-px bg-gray-100"></div>
            <div className="flex flex-col">
              <span className="text-xs text-gray-400 mb-0.5">合并问题</span>
              <span className={\`text-sm font-semibold \${!hasKnownStats ? "text-gray-400" : orgIssueTotal > 0 ? "text-red-500" : "text-green-600"}\`}>
                {hasKnownStats ? orgIssueTotal : "-"}
              </span>
            </div>
          </div>
        </div>
        <div className="px-5 py-3 border-t border-gray-50 flex items-center justify-between bg-gray-50/50 rounded-b-xl group-hover:bg-indigo-50/30 transition-colors">
          <span className="text-xs font-medium text-gray-500 group-hover:text-indigo-600 transition-colors">
            {!hasKnownStats ? "查看组织" : hasJobs ? "查看任务列表" : "上传首份报告"}
          </span>
          <svg className="w-4 h-4 text-gray-300 group-hover:text-indigo-500 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
        </div>
      </div>
    );
  };`;

content = content.substring(0, cardRenderStart) + newCardCode + content.substring(returnDivEnd);


const mapStart = content.indexOf('{virtualWindow.items.map((job) => {');
const mapEnd = content.indexOf('</tbody>', mapStart);
const newMapCode = `{virtualWindow.items.map((job) => {
                      const mergedTotal = getDisplayIssueTotal(job);

                      return (
                        <tr key={job.job_id} className="group hover:bg-gray-50/50 transition-colors cursor-pointer" onClick={() => onSelectJob(job.job_id)}>
                          <td className="px-6 py-4">
                            <div className="flex items-center gap-3">
                              <div className="flex-1 min-w-0">
                                <div className="text-sm font-medium text-gray-900 group-hover:text-indigo-600 truncate transition-colors" title={job.filename || "未命名文件"}>
                                  {job.filename || "未命名文件"}
                                </div>
                                <div className="mt-1.5 flex items-center gap-2 text-[11px] text-gray-500">
                                  <span>{typeof job.report_year === "number" ? \`\${job.report_year}年\` : "年度未知"}</span>
                                  <span className="w-1 h-1 rounded-full bg-gray-300"></span>
                                  <span>{job.report_kind === "budget" ? "预算" : job.report_kind === "final" ? "决算" : "类型未知"}</span>
                                  {job.organization_name && (
                                    <>
                                      <span className="w-1 h-1 rounded-full bg-gray-300"></span>
                                      <span className="truncate max-w-[120px]" title={job.organization_name}>{job.organization_name}</span>
                                    </>
                                  )}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{format(new Date(job.ts * 1000), "MM-dd HH:mm", { locale: zhCN })}</td>
                          <td className="px-6 py-4">
                            <div className="flex flex-col gap-1.5 justify-center">
                              <div className="flex items-center gap-2">
                                {getStatusBadge(job)}
                                {normalizeJobStatus(job.status) === "done" && (
                                  <span className={\`text-xs font-medium \${mergedTotal > 0 ? "text-red-500" : "text-green-600"}\`}>
                                    {mergedTotal > 0 ? \`\${mergedTotal} 个问题\` : "无问题"}
                                  </span>
                                )}
                              </div>
                              <div className="text-[11px] text-gray-400 flex items-center gap-2">
                                {job.structured_ingest_status === "done" && (
                                  <span className="text-emerald-600" title="已成功结构化入库">✓ 已入库</span>
                                )}
                                {(job.review_item_count || 0) > 0 && (
                                  <span className="text-amber-600">{job.review_item_count}待复核</span>
                                )}
                              </div>
                            </div>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-right text-sm">
                            <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                              <button onClick={(e) => { e.stopPropagation(); handleDelete(job.job_id, e); }} className="p-1.5 text-gray-400 hover:text-red-600 hover:bg-red-50 rounded-md transition-colors" title="删除">
                                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
                              </button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                    {virtualWindow.enabled && virtualWindow.bottomSpacerHeight > 0 && (
                      <tr aria-hidden="true" className="border-0">
                        <td colSpan={4} className="p-0" style={{ height: \`\${virtualWindow.bottomSpacerHeight}px\` }} />
                      </tr>
                    )}
                  `;

content = content.substring(0, mapStart) + newMapCode + content.substring(mapEnd);

fs.writeFileSync(filePath, content, 'utf8');
console.log('UI updated successfully!');
