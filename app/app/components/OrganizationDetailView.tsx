"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { format } from "date-fns";
import { zhCN } from "date-fns/locale";

interface UnitItem {
  id: string;
  name: string;
  level: "department" | "unit" | string;
  parent_id: string | null;
}

interface TopIssueRule {
  rule_id: string;
  count: number;
}

interface JobSummary {
  job_id: string;
  filename: string;
  status: "queued" | "processing" | "done" | "error" | "unknown";
  progress: number;
  ts: number;
  mode?: string;
  dual_mode_enabled?: boolean;
  stage?: string;
  report_year?: number | null;
  report_kind?: "budget" | "final" | "unknown";
  doc_type?: string | null;
  issue_total?: number;
  issue_error?: number;
  issue_warn?: number;
  issue_info?: number;
  has_issues?: boolean;
  top_issue_rules?: TopIssueRule[];
  local_participated?: boolean;
  ai_participated?: boolean;
  local_issue_total?: number;
  local_issue_error?: number;
  local_issue_warn?: number;
  local_issue_info?: number;
  ai_issue_total?: number;
  ai_issue_error?: number;
  ai_issue_warn?: number;
  ai_issue_info?: number;
}

interface OrganizationDetailViewProps {
  departmentId: string;
  departmentName: string;
  selectedUnitId?: string | null;
  onSelectUnit: (unit: UnitItem | null) => void;
  onSelectJob: (jobId: string) => void;
  onUpload: () => void;
  refreshKey?: number;
  onJobDeleted?: () => void;
}

export default function OrganizationDetailView({
  departmentId,
  departmentName,
  selectedUnitId,
  onSelectUnit,
  onSelectJob,
  onUpload,
  refreshKey,
  onJobDeleted,
}: OrganizationDetailViewProps) {
  const [units, setUnits] = useState<UnitItem[]>([]);
  const [unitsLoading, setUnitsLoading] = useState(true);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [unitJobsMap, setUnitJobsMap] = useState<Record<string, JobSummary[]>>({});
  const [orgViewTab, setOrgViewTab] = useState<"department" | "units">("department");
  const [selectedYearFilter, setSelectedYearFilter] = useState<string>("all");
  const [selectedKindFilter, setSelectedKindFilter] = useState<"all" | "budget" | "final">("all");
  const [yearFilterTouched, setYearFilterTouched] = useState(false);

  const buildJobsApiPath = useCallback(
    (orgId: string, includeChildren = false) =>
      `/api/organizations/${orgId}/jobs?include_children=${includeChildren ? "true" : "false"}&t=${Date.now()}`,
    []
  );

  const departmentOrg = useMemo<UnitItem>(
    () => ({
      id: departmentId,
      name: departmentName,
      level: "department",
      parent_id: null,
    }),
    [departmentId, departmentName]
  );

  const selectableOrganizations = useMemo(() => [departmentOrg, ...units], [departmentOrg, units]);

  const selectedUnit = useMemo<UnitItem | null>(
    () => selectableOrganizations.find((item) => item.id === selectedUnitId) || null,
    [selectableOrganizations, selectedUnitId]
  );

  const switchToDepartmentTab = useCallback(() => {
    setOrgViewTab("department");
    onSelectUnit(departmentOrg);
  }, [departmentOrg, onSelectUnit]);

  const switchToUnitsTab = useCallback(() => {
    setOrgViewTab("units");
    if (!selectedUnit || selectedUnit.level !== "unit") {
      onSelectUnit(null);
      setJobs([]);
    }
  }, [onSelectUnit, selectedUnit]);

  const fetchUnits = useCallback(async () => {
    setUnitsLoading(true);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    try {
      const toUnitItems = (items: any[]): UnitItem[] =>
        items
          .filter((item) => item && item.level === "unit")
          .map((item) => ({
            id: String(item.id),
            name: String(item.name || ""),
            level: "unit",
            parent_id: item.parent_id ?? null,
          }));

      const findUnitsInTree = (tree: any[]): UnitItem[] => {
        let target: any = null;
        const walk = (nodes: any[]) => {
          for (const node of nodes) {
            if (!node) continue;
            if (node.level === "department" && (node.id === departmentId || node.name === departmentName)) {
              target = node;
              return;
            }
            if (Array.isArray(node.children) && node.children.length > 0) {
              walk(node.children);
              if (target) return;
            }
          }
        };
        walk(Array.isArray(tree) ? tree : []);
        return target && Array.isArray(target.children) ? toUnitItems(target.children) : [];
      };

      const res = await fetch(`/api/departments/${departmentId}/units?t=${Date.now()}`, {
        signal: controller.signal,
      });
      if (res.ok) {
        const data = await res.json();
        const unitData = toUnitItems(Array.isArray(data.units) ? data.units : []);
        if (unitData.length > 0) {
          setUnits(unitData);
          return;
        }
      }

      const fallbackRes = await fetch(`/api/organizations?t=${Date.now()}`, {
        signal: controller.signal,
      });
      if (!fallbackRes.ok) {
        throw new Error("units api and fallback tree api both failed");
      }
      const fallbackData = await fallbackRes.json();
      const unitsFromTree = findUnitsInTree(Array.isArray(fallbackData.tree) ? fallbackData.tree : []);
      setUnits(unitsFromTree);
    } catch (e) {
      console.error("Failed to fetch department units", e);
      setUnits([]);
      onSelectUnit(null);
    } finally {
      clearTimeout(timeoutId);
      setUnitsLoading(false);
    }
  }, [departmentId, departmentName, onSelectUnit]);

  const fetchJobsForUnit = useCallback(
    async (unitId: string) => {
      setJobsLoading(true);
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 10000);
      try {
        const res = await fetch(buildJobsApiPath(unitId), { signal: controller.signal });
        if (res.ok) {
          const data = await res.json();
          setJobs(data.jobs || []);
        } else {
          setJobs([]);
        }
      } catch (e) {
        console.error("Failed to fetch jobs", e);
        setJobs([]);
      } finally {
        clearTimeout(timeoutId);
        setJobsLoading(false);
      }
    },
    [buildJobsApiPath]
  );

  useEffect(() => {
    onSelectUnit(null);
    setJobs([]);
    setOrgViewTab("department");
    fetchUnits();
  }, [departmentId, fetchUnits, onSelectUnit, refreshKey]);

  useEffect(() => {
    let active = true;
    if (selectableOrganizations.length === 0) return;

    const fetchAllJobs = async () => {
      const newMap: Record<string, JobSummary[]> = {};
      await Promise.all(
        selectableOrganizations.map(async (u) => {
          try {
            const res = await fetch(buildJobsApiPath(u.id));
            if (res.ok) {
              const data = await res.json();
              newMap[u.id] = data.jobs || [];
            } else {
              newMap[u.id] = [];
            }
          } catch {
            newMap[u.id] = [];
          }
        })
      );
      if (active) setUnitJobsMap(newMap);
    };

    fetchAllJobs();
    return () => {
      active = false;
    };
  }, [selectableOrganizations, buildJobsApiPath, refreshKey]);

  useEffect(() => {
    if (!selectedUnitId) {
      setJobs([]);
      return;
    }
    setYearFilterTouched(false);
    setSelectedYearFilter("all");
    setSelectedKindFilter("all");
    fetchJobsForUnit(selectedUnitId);
  }, [fetchJobsForUnit, selectedUnitId, refreshKey]);

  const availableYears = useMemo(() => {
    const years = new Set<number>();
    jobs.forEach((job) => {
      if (typeof job.report_year === "number" && job.report_year >= 2000 && job.report_year <= 2099) {
        years.add(job.report_year);
      }
    });
    return Array.from(years).sort((a, b) => b - a);
  }, [jobs]);

  useEffect(() => {
    if (yearFilterTouched || availableYears.length === 0) return;
    const latest = String(availableYears[0]);
    if (selectedYearFilter !== latest) setSelectedYearFilter(latest);
  }, [availableYears, selectedYearFilter, yearFilterTouched]);

  useEffect(() => {
    if (selectedYearFilter === "all") return;
    const target = Number(selectedYearFilter);
    if (!availableYears.includes(target)) {
      setSelectedYearFilter(availableYears.length > 0 ? String(availableYears[0]) : "all");
    }
  }, [availableYears, selectedYearFilter]);

  const filteredJobs = useMemo(() => {
    let scoped = jobs;
    if (selectedYearFilter !== "all") {
      const target = Number(selectedYearFilter);
      if (!Number.isNaN(target)) scoped = scoped.filter((job) => job.report_year === target);
    }
    if (selectedKindFilter !== "all") {
      scoped = scoped.filter((job) => job.report_kind === selectedKindFilter);
    }
    return scoped;
  }, [jobs, selectedKindFilter, selectedYearFilter]);

  const applyViewFilters = useCallback(
    (jobList: JobSummary[]) => {
      let scoped = jobList;
      if (selectedYearFilter !== "all") {
        const target = Number(selectedYearFilter);
        if (!Number.isNaN(target)) scoped = scoped.filter((job) => job.report_year === target);
      }
      if (selectedKindFilter !== "all") {
        scoped = scoped.filter((job) => job.report_kind === selectedKindFilter);
      }
      return scoped;
    },
    [selectedKindFilter, selectedYearFilter]
  );

  const filteredIssueTotal = useMemo(
    () => filteredJobs.reduce((sum, job) => sum + (job.issue_total || 0), 0),
    [filteredJobs]
  );

  const kindCounts = useMemo(() => {
    const counts = { budget: 0, final: 0 };
    jobs.forEach((job) => {
      if (job.report_kind === "budget") counts.budget += 1;
      if (job.report_kind === "final") counts.final += 1;
    });
    return counts;
  }, [jobs]);

  useEffect(() => {
    if (!selectedUnitId) return;
    const hasProcessing = jobs.some((j) => j.status === "processing" || j.status === "queued");
    if (!hasProcessing) return;
    const interval = setInterval(() => fetchJobsForUnit(selectedUnitId), 2000);
    return () => clearInterval(interval);
  }, [fetchJobsForUnit, jobs, selectedUnitId]);

  const getStatusBadge = (job: JobSummary) => {
    if (job.status === "done") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-green-100 text-green-700">已完成</span>;
    if (job.status === "processing") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-blue-100 text-blue-700 animate-pulse">处理中</span>;
    if (job.status === "queued") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-700">排队中</span>;
    if (job.status === "error") return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-red-100 text-red-700">异常</span>;
    return <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-700">未知</span>;
  };

  const handleDelete = async (jobId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("确定要删除这个任务吗？此操作不可恢复。")) return;
    try {
      const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
      if (res.ok) {
        setJobs((prev) => prev.filter((item) => item.job_id !== jobId));
        onJobDeleted?.();
      } else {
        alert("删除失败");
      }
    } catch (err) {
      console.error("Delete failed", err);
      alert("删除失败");
    }
  };

  const renderOrganizationCard = (org: UnitItem) => {
    const active = selectedUnitId === org.id;
    const orgJobsList = unitJobsMap[org.id] || [];
    const hasJobs = orgJobsList.length > 0;
    const statsJobsList = active ? applyViewFilters(orgJobsList) : orgJobsList;
    const isDepartment = org.level === "department";
    const orgDisplayName = !isDepartment && org.name === departmentName ? `${org.name} (Local Unit)` : org.name;
    const orgIssueTotal = statsJobsList.reduce((sum, item) => sum + (item.issue_total || 0), 0);
    const hasProblems = orgIssueTotal > 0;

    return (
      <div key={org.id} className={`relative flex flex-col bg-white rounded-2xl border transition-all duration-300 shadow-sm hover:shadow-md ${active ? "border-indigo-500 shadow-indigo-100" : "border-gray-200 hover:border-indigo-300"}`}>
        <div className="p-5 flex-1">
          <div className="flex items-start justify-between gap-2 mb-3">
            <h3 className="font-bold text-gray-900 text-lg tracking-tight leading-snug" title={orgDisplayName}>{orgDisplayName}</h3>
            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${isDepartment ? "bg-indigo-100 text-indigo-700" : "bg-slate-100 text-slate-700"}`}>{isDepartment ? "部门" : "单位"}</span>
          </div>
          <div className="flex items-center text-sm">
            <span className="text-gray-500 font-medium w-24">文件数量:</span>
            <span className="text-gray-700 font-mono text-sm font-semibold">{statsJobsList.length}</span>
          </div>
          <div className="flex items-center text-sm mt-2">
            <span className="text-gray-500 font-medium w-24">问题数量:</span>
            <span className={`font-mono text-sm font-semibold ${orgIssueTotal > 0 ? "text-red-600" : "text-green-600"}`}>{orgIssueTotal}</span>
          </div>
        </div>
        <div className="px-5 pb-5 pt-2">
          <button
            onClick={() => {
              setOrgViewTab(org.level === "department" ? "department" : "units");
              onSelectUnit(org);
            }}
            className={`w-full py-2.5 rounded-xl font-medium text-sm transition-all duration-300 flex items-center justify-center shadow-sm ${hasJobs ? "bg-indigo-600 hover:bg-indigo-700 text-white shadow-indigo-200" : "bg-amber-500 hover:bg-amber-600 text-white shadow-amber-200"}`}
          >
            {hasJobs ? (hasProblems ? "查看问题" : "查看报告") : "待上传"}
          </button>
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col h-full bg-transparent overflow-hidden">
      <div className="flex-none p-8 pb-4">
        <div className="rounded-2xl bg-white/70 backdrop-blur-xl border border-white/20 shadow-xl p-8">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-3xl font-bold text-gray-900 tracking-tight">{departmentName}</h1>
              <div className="mt-2 text-sm text-gray-500">{selectedUnit ? selectedUnit.name : "请先选择部门或单位"}</div>
            </div>
            <button
              onClick={onUpload}
              disabled={!selectedUnit}
              className="inline-flex items-center justify-center px-5 py-3 text-sm font-medium text-white bg-indigo-600 rounded-xl shadow-lg hover:bg-indigo-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
              title={selectedUnit ? "上传到当前组织" : "请先选择部门或单位"}
            >
              上传报告
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-auto px-8 pb-8">
        <div className="space-y-6">
          <div>
            <div className="flex items-center justify-between gap-4 mb-4">
              <div className="text-sm font-semibold text-gray-700 bg-white/60 inline-flex px-4 py-2 rounded-lg border border-white/40 shadow-sm backdrop-blur-md">{departmentName} - 组织列表</div>
              <div className="inline-flex bg-white/70 rounded-xl border border-white/30 p-1 shadow-sm">
                <button type="button" onClick={switchToDepartmentTab} className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${orgViewTab === "department" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>部门本级 (1)</button>
                <button type="button" onClick={switchToUnitsTab} className={`px-3 py-1.5 text-sm rounded-lg transition-colors ${orgViewTab === "units" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>下属单位 ({units.length})</button>
              </div>
            </div>

            {orgViewTab === "department" ? (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">{renderOrganizationCard(departmentOrg)}</div>
            ) : unitsLoading ? (
              <div className="text-center py-12 text-gray-400">加载单位中...</div>
            ) : units.length === 0 ? (
              <div className="text-center py-12 text-gray-400">该部门下暂无单位数据</div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">{units.map((unit) => renderOrganizationCard(unit))}</div>
            )}
          </div>

          <div className="bg-white/40 backdrop-blur-md rounded-2xl border border-white/20 shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-200 text-xs text-gray-500">第三步：查看所选组织任务列表</div>

            {selectedUnit && !jobsLoading && jobs.length > 0 && (
              <div className="px-4 py-3 border-b border-gray-100 bg-white/30">
                <div className="flex flex-wrap items-center gap-3">
                  <div className="inline-flex bg-white rounded-lg border border-gray-200 p-1">
                    <button type="button" onClick={() => setSelectedKindFilter("all")} className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedKindFilter === "all" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>全部类型</button>
                    <button type="button" onClick={() => setSelectedKindFilter("budget")} className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedKindFilter === "budget" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>预算 ({kindCounts.budget})</button>
                    <button type="button" onClick={() => setSelectedKindFilter("final")} className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedKindFilter === "final" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>决算 ({kindCounts.final})</button>
                  </div>

                  <div className="inline-flex bg-white rounded-lg border border-gray-200 p-1">
                    <button type="button" onClick={() => { setYearFilterTouched(true); setSelectedYearFilter("all"); }} className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedYearFilter === "all" ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>全部年度</button>
                    {availableYears.map((year) => (
                      <button key={year} type="button" onClick={() => { setYearFilterTouched(true); setSelectedYearFilter(String(year)); }} className={`px-3 py-1 text-xs rounded-md transition-colors ${selectedYearFilter === String(year) ? "bg-indigo-600 text-white" : "text-gray-600 hover:bg-gray-100"}`}>{year}</button>
                    ))}
                  </div>

                  <div className="text-xs text-gray-500">
                    当前{selectedKindFilter === "all" ? "全部类型" : selectedKindFilter === "budget" ? "预算" : "决算"}，当前{selectedYearFilter === "all" ? "全部年度" : `${selectedYearFilter}年度`}：{filteredJobs.length}个文件，问题{filteredIssueTotal}
                  </div>
                </div>
              </div>
            )}

            {!selectedUnit ? (
              <div className="text-center py-10 text-gray-400">请先选择部门或单位后查看任务</div>
            ) : jobsLoading ? (
              <div className="text-center py-10 text-gray-400">加载任务中...</div>
            ) : jobs.length === 0 ? (
              <div className="text-center py-10 text-gray-400">当前组织暂无文档任务</div>
            ) : filteredJobs.length === 0 ? (
              <div className="text-center py-10 text-gray-400">当前筛选条件下暂无任务</div>
            ) : (
              <div className="max-h-[360px] overflow-auto">
                <table className="min-w-full divide-y divide-gray-200/50">
                  <thead className="bg-gray-50/50">
                    <tr>
                      <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">文件名称</th>
                      <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">上传时间</th>
                      <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">状态</th>
                      <th className="px-6 py-4 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider w-32">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200/50 bg-transparent">
                    {filteredJobs.map((job) => {
                      const localParticipated = job.local_participated ?? true;
                      const aiParticipated = job.ai_participated ?? job.mode === "dual";
                      const localTotal = typeof job.local_issue_total === "number" ? job.local_issue_total : job.issue_total || 0;
                      const localError = typeof job.local_issue_error === "number" ? job.local_issue_error : job.issue_error || 0;
                      const localWarn = typeof job.local_issue_warn === "number" ? job.local_issue_warn : job.issue_warn || 0;
                      const localInfo = typeof job.local_issue_info === "number" ? job.local_issue_info : job.issue_info || 0;
                      const aiTotal = job.ai_issue_total || 0;
                      const aiError = job.ai_issue_error || 0;
                      const aiWarn = job.ai_issue_warn || 0;
                      const aiInfo = job.ai_issue_info || 0;
                      const localHasIssues = localParticipated && localTotal > 0;
                      const aiHasIssues = aiParticipated && aiTotal > 0;
                      const hasAnyIssues = localHasIssues || aiHasIssues;
                      const localBadgeClass = !localParticipated ? "bg-gray-100 text-gray-500" : localHasIssues ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700";
                      const aiBadgeClass = !aiParticipated ? "bg-gray-100 text-gray-500" : aiHasIssues ? "bg-red-50 text-red-700" : "bg-green-50 text-green-700";

                      return (
                        <tr key={job.job_id} className="group hover:bg-white/70 transition-colors duration-150 cursor-pointer" onClick={() => onSelectJob(job.job_id)}>
                          <td className="px-6 py-4 whitespace-nowrap">
                            <div className="text-sm font-medium text-gray-900 group-hover:text-indigo-600 transition-colors">{job.filename}</div>
                            <div className="mt-1 flex items-center gap-2 text-xs">
                              <span className="text-gray-500 font-mono">ID: {job.job_id.slice(0, 8)}</span>
                              <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-slate-100 text-slate-700">{typeof job.report_year === "number" ? `${job.report_year}年度` : "年度未识别"}</span>
                              <span className={`inline-flex items-center px-2 py-0.5 rounded-full ${job.report_kind === "budget" ? "bg-emerald-50 text-emerald-700" : job.report_kind === "final" ? "bg-cyan-50 text-cyan-700" : "bg-gray-100 text-gray-600"}`}>{job.report_kind === "budget" ? "预算检查" : job.report_kind === "final" ? "决算检查" : "类型未识别"}</span>
                            </div>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 tabular-nums">{format(new Date(job.ts * 1000), "yyyy-MM-dd HH:mm", { locale: zhCN })}</td>
                          <td className="px-6 py-4 whitespace-nowrap">
                            <div className="space-y-1">
                              {getStatusBadge(job)}
                              {job.status === "done" && (
                                <div className="space-y-1">
                                  <div className="flex flex-wrap gap-1.5">
                                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${localBadgeClass}`}>{!localParticipated ? "本地：未参与" : localHasIssues ? `本地：${localTotal}个问题` : "本地：正常"}</span>
                                    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium ${aiBadgeClass}`}>{!aiParticipated ? "AI：未参与" : aiHasIssues ? `AI：${aiTotal}个问题` : "AI：正常"}</span>
                                  </div>
                                  <div className={`text-xs font-medium ${hasAnyIssues ? "text-red-600" : "text-green-600"}`}>{hasAnyIssues ? "已发现问题" : "未发现问题"}</div>
                                  {localHasIssues && <div className="text-[11px] text-gray-500">本地明细：错{localError} / 警{localWarn} / 提{localInfo}</div>}
                                  {aiHasIssues && <div className="text-[11px] text-gray-500">AI明细：错{aiError} / 警{aiWarn} / 提{aiInfo}</div>}
                                  {localHasIssues && Array.isArray(job.top_issue_rules) && job.top_issue_rules.length > 0 && <div className="text-[11px] text-gray-500">主要类型：{job.top_issue_rules.map((item) => `${item.rule_id}(${item.count})`).join("、")}</div>}
                                </div>
                              )}
                            </div>
                          </td>
                          <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                            <div className="flex items-center justify-end space-x-3">
                              <button onClick={(e) => handleDelete(job.job_id, e)} className="text-red-500 hover:text-red-700 transition-colors px-2 py-1 rounded hover:bg-red-50" title="删除任务">删除</button>
                              <button onClick={() => onSelectJob(job.job_id)} className="text-indigo-600 hover:text-indigo-900 text-xs">查看</button>
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
