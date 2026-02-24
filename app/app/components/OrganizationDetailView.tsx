"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { format } from "date-fns";
import { zhCN } from "date-fns/locale";

interface UnitItem {
  id: string;
  name: string;
  level: string;
  parent_id: string | null;
}

interface JobSummary {
  job_id: string;
  filename: string;
  status: "queued" | "processing" | "done" | "error" | "unknown";
  progress: number;
  ts: number;
  stage?: string;
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

  const selectedUnit = useMemo(
    () => units.find((item) => item.id === selectedUnitId) || null,
    [units, selectedUnitId]
  );

  const fetchUnits = useCallback(async () => {
    setUnitsLoading(true);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    try {
      const res = await fetch(`/api/departments/${departmentId}/units?t=${Date.now()}`, {
        signal: controller.signal,
      });
      if (!res.ok) {
        throw new Error("units api not ok");
      }
      const data = await res.json();
      const unitData = Array.isArray(data.units) ? data.units : [];
      setUnits(unitData);
    } catch (e) {
      console.error("Failed to fetch department units", e);
      setUnits([]);
      onSelectUnit(null);
    } finally {
      clearTimeout(timeoutId);
      setUnitsLoading(false);
    }
  }, [departmentId, onSelectUnit]);

  const fetchJobsForUnit = useCallback(async (unitId: string) => {
    setJobsLoading(true);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);
    try {
      const res = await fetch(`/api/organizations/${unitId}/jobs?t=${Date.now()}`, {
        signal: controller.signal,
      });
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
  }, []);

  useEffect(() => {
    onSelectUnit(null);
    setJobs([]);
    fetchUnits();
  }, [departmentId, fetchUnits, onSelectUnit, refreshKey]);

  useEffect(() => {
    if (!selectedUnitId) {
      setJobs([]);
      return;
    }
    fetchJobsForUnit(selectedUnitId);
  }, [fetchJobsForUnit, selectedUnitId, refreshKey]);

  useEffect(() => {
    if (!selectedUnitId) {
      return;
    }
    const hasProcessing = jobs.some((j) => j.status === "processing" || j.status === "queued");
    if (!hasProcessing) {
      return;
    }

    const interval = setInterval(() => {
      fetchJobsForUnit(selectedUnitId);
    }, 2000);
    return () => clearInterval(interval);
  }, [fetchJobsForUnit, jobs, selectedUnitId]);

  const getStatusBadge = (job: JobSummary) => {
    switch (job.status) {
      case "done":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-green-100 text-green-700">
            已完成
          </span>
        );
      case "processing":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-blue-100 text-blue-700 animate-pulse">
            分析中
          </span>
        );
      case "queued":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-700">
            排队中
          </span>
        );
      case "error":
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-red-100 text-red-700">
            异常
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs bg-gray-100 text-gray-700">
            未知
          </span>
        );
    }
  };

  const handleDelete = async (jobId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("确定要删除这个任务吗？此操作不可恢复。")) {
      return;
    }

    try {
      const res = await fetch(`/api/jobs/${jobId}`, { method: "DELETE" });
      if (res.ok) {
        setJobs((prev) => prev.filter((item) => item.job_id !== jobId));
        onJobDeleted?.();
      } else {
        alert("删除失败");
      }
    } catch (e) {
      console.error("Delete failed", e);
      alert("删除失败");
    }
  };

  return (
    <div className="flex flex-col h-full bg-transparent overflow-hidden">
      <div className="flex-none p-8 pb-4">
        <div className="rounded-2xl bg-white/70 backdrop-blur-xl border border-white/20 shadow-xl p-8">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-3xl font-bold text-gray-900 tracking-tight">{departmentName}</h1>
              <div className="mt-2 text-sm text-gray-500">
                {selectedUnit ? `当前单位: ${selectedUnit.name}` : "请先选择单位"}
              </div>
            </div>
            <button
              onClick={onUpload}
              disabled={!selectedUnit}
              className="inline-flex items-center justify-center px-5 py-3 text-sm font-medium text-white bg-indigo-600 rounded-xl shadow-lg hover:bg-indigo-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
              title={selectedUnit ? "上传到当前单位" : "请先选择单位"}
            >
              上传新文档
            </button>
          </div>
        </div>
      </div>

      <div className="px-8 pb-4">
        <div className="bg-white/50 backdrop-blur-md rounded-2xl border border-white/20 p-4">
          <div className="text-xs text-gray-500 mb-3">第二步：选择单位</div>
          {unitsLoading ? (
            <div className="text-sm text-gray-400">加载单位中...</div>
          ) : units.length === 0 ? (
            <div className="text-sm text-gray-400">该部门下暂无单位</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
              {units.map((unit) => {
                const active = selectedUnitId === unit.id;
                return (
                  <button
                    key={unit.id}
                    onClick={() => onSelectUnit(unit)}
                    className={`text-left px-4 py-3 rounded-xl border transition-all ${
                      active
                        ? "border-indigo-500 bg-indigo-50 text-indigo-700 shadow-sm"
                        : "border-gray-200 bg-white hover:border-indigo-300 hover:bg-indigo-50/40"
                    }`}
                  >
                    <div className="font-medium text-sm">{unit.name}</div>
                    <div className="text-xs text-gray-500 mt-1 font-mono">ID: {unit.id.slice(0, 8)}</div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-auto px-8 pb-8">
        <div className="bg-white/40 backdrop-blur-md rounded-2xl border border-white/20 shadow-sm overflow-x-auto">
          <div className="px-4 py-3 border-b border-gray-200 text-xs text-gray-500">
            第三步：查看单位任务列表
          </div>
          {!selectedUnit ? (
            <div className="text-center py-16 text-gray-400">请选择单位后查看任务</div>
          ) : jobsLoading ? (
            <div className="text-center py-16 text-gray-400">加载任务中...</div>
          ) : jobs.length === 0 ? (
            <div className="text-center py-16 text-gray-400">该单位暂无文档任务</div>
          ) : (
            <table className="min-w-full divide-y divide-gray-200/50">
              <thead className="bg-gray-50/50">
                <tr>
                  <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    文件名称
                  </th>
                  <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    上传时间
                  </th>
                  <th className="px-6 py-4 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    状态
                  </th>
                  <th className="px-6 py-4 text-right text-xs font-semibold text-gray-500 uppercase tracking-wider w-32">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200/50 bg-transparent">
                {jobs.map((job) => (
                  <tr
                    key={job.job_id}
                    className="group hover:bg-white/70 transition-colors duration-150 cursor-pointer"
                    onClick={() => onSelectJob(job.job_id)}
                  >
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="text-sm font-medium text-gray-900 group-hover:text-indigo-600 transition-colors">
                        {job.filename}
                      </div>
                      <div className="text-xs text-gray-500 font-mono">ID: {job.job_id.slice(0, 8)}</div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500 tabular-nums">
                      {format(new Date(job.ts * 1000), "yyyy-MM-dd HH:mm", { locale: zhCN })}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">{getStatusBadge(job)}</td>
                    <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                      <div className="flex items-center justify-end space-x-3">
                        <button
                          onClick={(e) => handleDelete(job.job_id, e)}
                          className="text-red-500 hover:text-red-700 transition-colors px-2 py-1 rounded hover:bg-red-50"
                          title="删除任务"
                        >
                          删除
                        </button>
                        <button
                          onClick={() => onSelectJob(job.job_id)}
                          className="text-indigo-600 hover:text-indigo-900 text-xs"
                        >
                          查看
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
