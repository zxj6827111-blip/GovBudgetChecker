import type { Route } from "next";
import Link from "next/link";
import { Download, Eye, Link2, MoreHorizontal, RefreshCw, Trash2 } from "lucide-react";
import type { MouseEvent as ReactMouseEvent, RefObject } from "react";

import { cn } from "@/lib/utils";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { formatDateTime, getDisplayIssueTotal, getHighRiskCount, normalizeUiTaskStatus, toUiTask } from "@/lib/uiAdapters";
import { needsIngestReview } from "./helpers";

type Props = {
  jobs: JobSummaryRecord[];
  loading: boolean;
  normalizedSearchQuery: string;
  selectedTasks: string[];
  openMenuId: string | null;
  menuRef: RefObject<HTMLDivElement>;
  routerPush: (href: string) => void;
  toggleAll: () => void;
  toggleSelect: (jobId: string) => void;
  setOpenMenuId: (jobId: string | null) => void;
  openAssociateDialog: (jobId: string, event?: ReactMouseEvent<HTMLButtonElement>) => void;
  handleReanalyze: (jobId: string, event?: ReactMouseEvent<HTMLButtonElement>) => Promise<void>;
  handleDownloadReport: (jobId: string, fallbackName: string, event?: ReactMouseEvent<HTMLButtonElement>) => Promise<void>;
  handleDelete: (jobId: string, event: ReactMouseEvent<HTMLButtonElement>) => Promise<void>;
  deletingJobId: string | null;
  reanalyzingJobId: string | null;
  exportingJobId: string | null;
  isAssociatingJob: boolean;
  associatingJobId: string | null;
};

export default function DepartmentJobTable(props: Props) {
  const allSelected = props.selectedTasks.length === props.jobs.length && props.jobs.length > 0;

  return (
    <div className="overflow-visible rounded-xl border border-border bg-white shadow-sm">
      <table className="w-full border-collapse text-left">
        <thead>
          <tr className="border-b border-border bg-slate-50 text-sm font-semibold text-slate-600">
            <th className="w-12 p-4 text-center"><input type="checkbox" data-testid="select-all-jobs" className="rounded border-slate-300 text-primary-600 focus:ring-primary-500" checked={allSelected} onChange={props.toggleAll} /></th>
            <th className="p-4">文件名</th><th className="w-24 p-4">年度</th><th className="w-40 p-4">状态</th><th className="w-32 p-4">问题数</th><th className="w-44 p-4">更新时间</th><th className="w-24 p-4 text-center">操作</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {props.loading ? <tr><td colSpan={7} className="p-8 text-center text-slate-500">正在加载任务...</td></tr> : props.jobs.length === 0 ? <tr><td colSpan={7} className="p-8 text-center text-slate-500">{props.normalizedSearchQuery ? "当前筛选条件和搜索关键字下暂无相关报告" : "当前筛选条件下暂无相关报告"}</td></tr> : props.jobs.map((job) => {
            const task = toUiTask(job);
            const isJobAnalyzing = normalizeUiTaskStatus(job.status) === "analyzing";
            const isReanalyzingThisJob = props.reanalyzingJobId === job.job_id;
            const isExportingThisJob = props.exportingJobId === job.job_id;
            return <tr key={job.job_id} className={cn("transition-colors hover:bg-slate-50", props.selectedTasks.includes(job.job_id) && "bg-primary-50/50 hover:bg-primary-50/80")}>
              <td className="p-4 text-center"><input type="checkbox" data-testid={`job-select-${job.job_id}`} className="rounded border-slate-300 text-primary-600 focus:ring-primary-500" checked={props.selectedTasks.includes(job.job_id)} onChange={() => props.toggleSelect(job.job_id)} /></td>
              <td className="p-4"><Link href={`/task/${job.job_id}` as Route} className="font-medium text-slate-900 hover:text-primary-600">{task.filename}</Link></td>
              <td className="p-4 text-sm text-slate-600">{task.year}</td>
              <td className="p-4"><div className="flex flex-wrap gap-2"><span className={cn("rounded-full border px-2.5 py-1 text-xs font-medium", task.status === "completed" ? "border-success-200 bg-success-50 text-success-700" : task.status === "analyzing" ? "border-warning-200 bg-warning-50 text-warning-700" : "border-danger-200 bg-danger-50 text-danger-700")}>{task.status === "completed" ? "已完成" : task.status === "analyzing" ? "分析中" : "失败"}</span>{String(job.report_kind ?? "").trim().toLowerCase() === "unknown" ? <span className="rounded-full border border-orange-200 bg-orange-50 px-2.5 py-1 text-xs font-medium text-orange-700">类型待识别</span> : null}{needsIngestReview(job) && Number(job.review_item_count ?? 0) > 0 ? <span className="rounded-full border border-amber-200 bg-amber-50 px-2.5 py-1 text-xs font-medium text-amber-700">待复核 {job.review_item_count}</span> : null}</div></td>
              <td className="p-4"><div className="flex items-center gap-2"><span className="font-semibold text-slate-900">{getDisplayIssueTotal(job)}</span>{getHighRiskCount(job) > 0 ? <span className="rounded bg-danger-100 px-1.5 py-0.5 text-xs font-bold text-danger-700">{getHighRiskCount(job)} 高风险</span> : null}</div></td>
              <td className="p-4 text-sm text-slate-500">{formatDateTime(job.updated_ts ?? job.ts)}</td>
              <td className="relative p-4 text-center">
                <button type="button" data-testid={`job-menu-${job.job_id}`} onClick={(event) => { event.stopPropagation(); props.setOpenMenuId(props.openMenuId === job.job_id ? null : job.job_id); }} className="rounded p-1.5 text-slate-400 hover:bg-slate-200 hover:text-slate-700"><MoreHorizontal className="h-5 w-5" /></button>
                {props.openMenuId === job.job_id ? <div ref={props.menuRef} className="absolute right-8 top-10 z-30 w-48 rounded-lg border border-border bg-white py-1 shadow-lg">
                  <button type="button" onClick={() => props.routerPush(`/task/${job.job_id}`)} className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50"><Eye className="h-4 w-4 text-slate-400" />查看复核详情</button>
                  <button type="button" data-testid={`job-associate-${job.job_id}`} onClick={(event) => props.openAssociateDialog(job.job_id, event)} disabled={props.isAssociatingJob && props.associatingJobId === job.job_id} className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"><Link2 className="h-4 w-4" />{job.organization_id ? "更改关联" : "关联报告"}</button>
                  <button type="button" data-testid={`job-reanalyze-${job.job_id}`} onClick={(event) => void props.handleReanalyze(job.job_id, event)} disabled={isJobAnalyzing || isReanalyzingThisJob} className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"><RefreshCw className="h-4 w-4" />{isReanalyzingThisJob ? "重新分析中..." : isJobAnalyzing ? "分析中" : "重新分析"}</button>
                  <button type="button" onClick={(event) => void props.handleDownloadReport(job.job_id, task.filename, event)} disabled={isExportingThisJob} className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"><Download className="h-4 w-4" />{isExportingThisJob ? "导出中..." : "导出审查报告"}</button>
                  <div className="my-1 h-px bg-border" />
                  <button type="button" onClick={(event) => void props.handleDelete(job.job_id, event)} disabled={props.deletingJobId === job.job_id} className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-red-600 hover:bg-red-50 disabled:opacity-50"><Trash2 className="h-4 w-4" />{props.deletingJobId === job.job_id ? "删除中..." : "删除此报告"}</button>
                </div> : null}
              </td>
            </tr>;
          })}
        </tbody>
      </table>
    </div>
  );
}
