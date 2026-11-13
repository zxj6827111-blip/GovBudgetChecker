"use client";

import Link from "next/link";
import type { Route } from "next";
import { useCallback, useMemo, useRef, useState } from "react";
import { SectionTitle } from "@/components/ui";
import { resolvePollingDecision } from "@/lib/jobPolling";
import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { flattenOrganizationTree, type OrganizationFilterOption } from "../workspace/OrganizationFilterSelect";
import { WorkbenchQueueTable } from "../workspace/WorkbenchQueueTable";
import { useJobPolling } from "../workspace/useJobPolling";
import { filterDepartmentReports, groupReportsByFiscalYear, type ReportKindFilter } from "./departmentReportAdapters";

const KINDS: Array<[ReportKindFilter, string]> = [["all", "全部材料"], ["budget", "预算"], ["final", "决算"], ["unknown", "类型待确认"]];

/** 历史报告走上传任务的授权接口，未入材料槽位的文件也能按组织与财政年度查到。 */
export function DepartmentReportsPage() {
  const [jobs, setJobs] = useState<JobSummaryRecord[]>([]);
  const [organizations, setOrganizations] = useState<OrganizationFilterOption[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [year, setYear] = useState("");
  const [kind, setKind] = useState<ReportKindFilter>("all");
  const [keyword, setKeyword] = useState("");
  const [orgKeyword, setOrgKeyword] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const jobsRef = useRef<JobSummaryRecord[] | null>(null);
  const load = useCallback(async () => {
    try {
      const [jobResponse, orgResponse] = await Promise.all([
        fetch("/api/jobs", { cache: "no-store" }),
        fetch("/api/organizations?stats=none", { cache: "no-store" }),
      ]);
      if (!jobResponse.ok || !orgResponse.ok) throw new Error(`加载失败（HTTP ${!jobResponse.ok ? jobResponse.status : orgResponse.status}）`);
      const jobPayload = await jobResponse.json();
      const orgPayload = await orgResponse.json();
      const next = Array.isArray(jobPayload) ? jobPayload : jobPayload.items ?? [];
      jobsRef.current = next;
      setJobs(next);
      setOrganizations(flattenOrganizationTree(orgPayload.tree));
      setLoaded(true);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "报告加载失败");
      throw reason;
    }
  }, []);
  const polling = useJobPolling({ fetcher: load, decide: useCallback(() => resolvePollingDecision(jobsRef.current), []) });
  const organizationJobs = useMemo(() => filterDepartmentReports(jobs, organizations, { organizationId, year: "", kind: "all", keyword: "" }), [jobs, organizations, organizationId]);
  const years = useMemo(() => groupReportsByFiscalYear(organizationJobs).map(([value]) => value), [organizationJobs]);
  const visible = useMemo(() => filterDepartmentReports(jobs, organizations, { organizationId, year, kind, keyword }), [jobs, organizations, organizationId, year, kind, keyword]);
  const groups = useMemo(() => groupReportsByFiscalYear(visible), [visible]);
  const selectedName = organizationId === "unassigned" ? "待确认归属" : organizations.find((item) => item.id === organizationId)?.name ?? "全部部门与单位";
  const selectOrganization = (id: string) => { setOrganizationId(id); setYear(""); };
  const filterClass = "rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400";

  return (
    <div className="mx-auto max-w-[1800px] p-6" data-testid="gbc-department-reports-page">
      <SectionTitle title="部门报告" desc="点击部门或单位，按财政年度查看预算和决算；包括尚未关联材料台账的历史上传文件。" />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <p className="flex-1 text-xs text-slate-500">财政年度来自报告内容，与上传时间、公开日期分别记录。未识别到的年度和类型单独展示。</p>
        <Link href={"/upload" as Route} className={filterClass}>上传新报告</Link>
        <button type="button" onClick={polling.refreshNow} disabled={polling.isManualRefreshing} className={filterClass} data-testid="gbc-reports-refresh">{polling.isManualRefreshing ? "刷新中…" : "刷新"}</button>
      </div>
      {error ? <p role="alert" className="mt-4 rounded-md bg-danger-50 p-3 text-sm text-danger-700">{error}；请刷新重试。{loaded ? "当前保留上次成功加载的结果。" : ""}</p> : null}
      {!loaded && !error ? <p className="mt-4 text-sm text-slate-500">正在加载部门报告…</p> : null}
      <div className="mt-5 grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)]">
        <aside className="rounded-card border border-border bg-white p-3" aria-label="报告所属部门与单位">
          <input type="search" aria-label="搜索部门或单位" placeholder="搜索部门或单位" value={orgKeyword} onChange={(event) => setOrgKeyword(event.target.value)} className={`${filterClass} w-full`} />
          <div className="mt-3 max-h-[65vh] space-y-1 overflow-y-auto">
            {[{ id: "", name: "全部部门与单位", depth: 0 }, { id: "unassigned", name: "待确认归属", depth: 0 }, ...organizations.filter((item) => item.name.includes(orgKeyword.trim()))].map((item) => (
              <button key={item.id} type="button" onClick={() => selectOrganization(item.id)} aria-pressed={organizationId === item.id} data-testid={`gbc-reports-org-${item.id || "all"}`}
                className={`block w-full rounded-md px-3 py-2 text-left text-sm ${organizationId === item.id ? "bg-primary-100 font-medium text-primary-700" : "text-slate-600 hover:bg-surface-100"}`}>
                <span style={{ paddingLeft: Math.min(item.depth, 3) * 10 }}>{item.name}</span>
              </button>
            ))}
          </div>
        </aside>
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-slate-900">{selectedName} <span className="text-sm font-normal text-slate-500">· {visible.length} 份报告</span></h2>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <select aria-label="按财政年度查看报告" value={year} onChange={(event) => setYear(event.target.value)} className={filterClass} data-testid="gbc-reports-year">
              <option value="">全部财政年度</option>
              {years.map((value) => <option key={value} value={value}>{value === "unknown" ? "年度待确认" : `${value} 年`}</option>)}
            </select>
            <input type="search" aria-label="搜索报告" placeholder="搜索报告文件名或单位" value={keyword} onChange={(event) => setKeyword(event.target.value)} className={`${filterClass} min-w-[200px] flex-1`} />
          </div>
          <div className="mt-3 flex flex-wrap gap-2" aria-label="按预算或决算查看报告">
            {KINDS.map(([value, label]) => <button key={value} type="button" aria-pressed={kind === value} onClick={() => setKind(value)} data-testid={`gbc-reports-kind-${value}`} className={`${filterClass} ${kind === value ? "border-primary-400 bg-primary-50 font-medium text-primary-700" : ""}`}>{label}</button>)}
          </div>
          {loaded && groups.length === 0 ? <p className="mt-5 rounded-md border border-dashed border-border bg-white p-8 text-center text-sm text-slate-500">当前筛选下没有报告。可以切换年度或材料类型，未关联组织的文件在“待确认归属”中查看。</p> : null}
          {groups.map(([fiscalYear, reports]) => (
            <section key={fiscalYear} className="mt-5" data-testid={`gbc-reports-year-${fiscalYear}`}>
              <h3 className="mb-2 text-sm font-semibold text-slate-700">{fiscalYear === "unknown" ? "财政年度待确认" : `${fiscalYear} 年度`} · {reports.length} 份</h3>
              <WorkbenchQueueTable jobs={reports} renderRowActions={(job) => (
                <a href={`/api/files/${encodeURIComponent(job.job_id)}/source`} className="whitespace-nowrap rounded-md border border-border px-2.5 py-1.5 text-xs text-slate-600">下载原件</a>
              )} />
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
