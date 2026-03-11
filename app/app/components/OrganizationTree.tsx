"use client";

import { useCallback, useEffect, useState } from "react";

interface Organization {
  id: string;
  name: string;
  level: string;
  level_name: string;
  parent_id: string | null;
  children: Organization[];
  job_count: number;
  issue_count: number;
}

interface OrganizationTreeProps {
  onSelect: (org: Organization | null) => void;
  onGlobalBatchUpload?: () => void;
  hideUtilityActions?: boolean;
  openImporterSignal?: number;
  isAdmin?: boolean;
  selectedOrgId?: string | null;
  refreshKey?: number;
}

export default function OrganizationTree({
  onSelect,
  onGlobalBatchUpload,
  hideUtilityActions = false,
  openImporterSignal = 0,
  isAdmin = false,
  selectedOrgId,
  refreshKey,
}: OrganizationTreeProps) {
  const [departments, setDepartments] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showImporter, setShowImporter] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  // Create/Edit modal state
  const [modalType, setModalType] = useState<"create" | "edit" | null>(null);
  const [modalOrgId, setModalOrgId] = useState<string | null>(null);
  const [modalInputVal, setModalInputVal] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const collectDepartmentsFromTree = useCallback((nodes: Organization[]): Organization[] => {
    const result: Organization[] = [];
    const walk = (items: Organization[]) => {
      for (const item of items) {
        if (item.level === "department") {
          result.push(item);
        }
        if (item.children?.length) {
          walk(item.children);
        }
      }
    };
    walk(nodes);
    return result;
  }, []);

  const fetchDepartments = useCallback(async () => {
    setLoading(true);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    try {
      const res = await fetch(`/api/departments`, {
        signal: controller.signal,
        cache: "no-store",
      });
      if (!res.ok) {
        throw new Error("departments api not ok");
      }

      const data = await res.json();
      const deptData = Array.isArray(data.departments) ? data.departments : [];
      setDepartments(deptData);
      setError(null);

      // Backward-compatible fallback when departments endpoint is unavailable or empty.
      if (deptData.length === 0) {
        const fallbackRes = await fetch(`/api/organizations`, {
          signal: controller.signal,
          cache: "no-store",
        });
        if (fallbackRes.ok) {
          const fallbackData = await fallbackRes.json();
          const treeData = Array.isArray(fallbackData.tree) ? fallbackData.tree : [];
          setDepartments(collectDepartmentsFromTree(treeData));
        }
      }
    } catch (e) {
      console.error(e);
      setError("加载部门列表失败");
      setDepartments([]);
    } finally {
      clearTimeout(timeoutId);
      setLoading(false);
    }
  }, [collectDepartmentsFromTree]);

  useEffect(() => {
    fetchDepartments();
  }, [fetchDepartments, refreshKey]);

  useEffect(() => {
    if (isAdmin && openImporterSignal > 0) {
      setShowImporter(true);
    }
  }, [isAdmin, openImporterSignal]);

  const handleImport = async (file: File) => {
    if (!isAdmin) {
      alert("仅管理员可以导入组织结构");
      return;
    }
    const formData = new FormData();
    formData.append("file", file);
    formData.append("clear_existing", "true");

    try {
      const res = await fetch("/api/organizations/import", {
        method: "POST",
        body: formData,
      });
      const result = await res.json();
      if (result.success) {
        alert(`导入成功，共导入 ${result.imported} 个组织`);
        fetchDepartments();
        setShowImporter(false);
      } else {
        const msg =
          result.detail ||
          result.error ||
          result.message ||
          result.errors?.join(", ") ||
          "未知错误";
        alert(`导入失败: ${msg}`);
      }
    } catch (e) {
      alert(`导入失败: ${e}`);
    }
  };

  const handleCreateOrg = async () => {
    if (!isAdmin) {
      alert("仅管理员可以创建部门");
      return;
    }
    if (!modalInputVal.trim()) return;
    setIsSubmitting(true);
    try {
      const res = await fetch("/api/organizations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: modalInputVal.trim(),
          level: "department"
        }),
      });
      if (!res.ok) throw new Error("创建失败");
      setModalType(null);
      setModalInputVal("");
      fetchDepartments();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUpdateOrg = async () => {
    if (!isAdmin) {
      alert("仅管理员可以修改部门");
      return;
    }
    if (!modalOrgId || !modalInputVal.trim()) return;
    setIsSubmitting(true);
    try {
      const res = await fetch(`/api/organizations/${modalOrgId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: modalInputVal.trim() }),
      });
      if (!res.ok) throw new Error("更新失败");
      setModalType(null);
      setModalOrgId(null);
      setModalInputVal("");
      fetchDepartments();
    } catch (e: any) {
      alert(e.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteOrg = async (id: string, name: string) => {
    if (!isAdmin) {
      alert("仅管理员可以删除部门");
      return;
    }
    try {
      const previewRes = await fetch(`/api/organizations/${encodeURIComponent(id)}/delete-preview`, {
        cache: "no-store",
      });
      let previewPayload: any = {};
      try {
        previewPayload = await previewRes.json();
      } catch {
        previewPayload = {};
      }
      if (!previewRes.ok) {
        throw new Error(previewPayload?.detail || previewPayload?.error || "获取删除影响范围失败");
      }
      const summary = previewPayload?.summary || {};
      const confirmed = confirm(
        `确定要删除部门 "${name}" 吗？\n\n将删除 ${summary.organization_count ?? 0} 个组织（其中单位 ${summary.unit_count ?? 0} 个），影响 ${summary.job_count ?? 0} 个任务关联。`
      );
      if (!confirmed) return;

      const res = await fetch(`/api/organizations/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      let payload: any = {};
      try {
        payload = await res.json();
      } catch {
        payload = {};
      }
      if (!res.ok) {
        throw new Error(payload?.detail || payload?.error || payload?.message || "删除部门失败");
      }
      if (selectedOrgId === id) {
        onSelect(null);
      }
      fetchDepartments();
    } catch (e: any) {
      alert(e.message);
    }
  };

  const filteredDepartments = departments.filter((item) =>
    item.name.toLowerCase().includes(searchQuery.trim().toLowerCase())
  );

  const renderDepartment = (node: Organization) => {
    const isSelected = selectedOrgId === node.id;
    return (
      <div key={node.id}>
        <div
          className={`flex items-center justify-between py-3 px-4 m-1.5 rounded-xl cursor-pointer transition-all duration-300 group ${isSelected
            ? "bg-indigo-50/80 shadow-sm border border-indigo-100"
            : "hover:bg-white hover:shadow-sm border border-transparent"
            }`}
          onClick={() => onSelect(node)}
          title={node.name}
        >
          <div className="flex items-center space-x-3 overflow-hidden">
            <div className={`w-1.5 h-6 rounded-full transition-all duration-300 ${isSelected ? 'bg-indigo-600' : 'bg-transparent group-hover:bg-gray-200'}`}></div>
            <span className={`truncate text-sm tracking-tight ${isSelected ? 'text-indigo-900 font-semibold' : 'text-gray-700 font-medium'}`}>{node.name}</span>
          </div>

          <div className="flex items-center space-x-2 flex-shrink-0">
            {node.job_count > 0 && (
              <span className={`text-[11px] px-2 py-0.5 rounded-full font-medium ${node.issue_count > 0
                ? 'bg-red-50 text-red-600 border border-red-100' // Has issues
                : 'bg-green-50 text-green-600 border border-green-100' // All good
                }`}>
                {node.issue_count > 0 ? `合并: ${node.issue_count}` : '正常: 0'}
              </span>
            )}

            {/* Action buttons (Edit & Delete) shown on hover */}
            {isAdmin ? (
            <div className={`${isSelected ? 'flex opacity-100' : 'hidden group-hover:flex opacity-0 group-hover:opacity-100'} items-center space-x-1 transition-opacity`}>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setModalType("edit");
                  setModalOrgId(node.id);
                  setModalInputVal(node.name);
                }}
                className="text-gray-400 hover:text-indigo-600 p-1 rounded-md hover:bg-indigo-50 transition-colors"
                title="编辑"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                </svg>
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  handleDeleteOrg(node.id, node.name);
                }}
                className="text-gray-400 hover:text-red-500 p-1 rounded-md hover:bg-red-50 transition-colors"
                title="删除"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                </svg>
              </button>
            </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="flex-1 overflow-hidden flex flex-col">
      <div className="p-3 border-b border-gray-200 space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-700 flex items-center">
            部门视图
            {isAdmin ? (
              <button
                onClick={() => {
                  setModalType("create");
                  setModalInputVal("");
                }}
                className="ml-2 p-1 text-gray-400 hover:text-indigo-600 hover:bg-gray-100 rounded"
                title="新建部门"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </button>
            ) : null}
          </h3>
          <button
            onClick={() => onSelect(null)}
            className="text-xs px-2 py-1 rounded-md border border-gray-200 text-gray-500 hover:text-gray-700 hover:border-gray-300 hover:bg-gray-50 transition-colors"
            title="查看全部部门"
          >
            全部
          </button>
        </div>

        {!hideUtilityActions && (
          <div className="grid grid-cols-2 gap-2">
            {onGlobalBatchUpload && (
              <button
                onClick={onGlobalBatchUpload}
                disabled={!isAdmin}
                className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50 to-blue-50 px-3 py-2 text-left transition-all hover:shadow-sm hover:border-indigo-300 disabled:cursor-not-allowed disabled:opacity-60"
                title={isAdmin ? "上传全区 PDF 文档（批量）" : "仅管理员可操作"}
              >
                <div className="flex items-center gap-1.5 text-[13px] font-semibold text-indigo-700">
                  <svg className="w-4 h-4 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                  </svg>
                  全区上传
                </div>
                <div className="mt-1 text-[11px] text-indigo-500">上传全区 PDF</div>
              </button>
            )}

            <button
              onClick={() => setShowImporter(true)}
              disabled={!isAdmin}
              className="rounded-xl border border-emerald-200 bg-gradient-to-br from-emerald-50 to-teal-50 px-3 py-2 text-left transition-all hover:shadow-sm hover:border-emerald-300 disabled:cursor-not-allowed disabled:opacity-60"
              title={isAdmin ? "导入部门及单位名称模板（CSV / XLSX）" : "仅管理员可操作"}
            >
              <div className="flex items-center gap-1.5 text-[13px] font-semibold text-emerald-700">
                <svg className="w-4 h-4 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M12 11v8m0 0l-3-3m3 3l3-3" />
                </svg>
                导入
              </div>
              <div className="mt-1 text-[11px] text-emerald-600">导入部门名称</div>
            </button>
          </div>
        )}
      </div>

      <div className="px-3 py-2 border-b border-gray-100">
        <input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="搜索部门"
          className="w-full text-sm border border-gray-200 rounded-md px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </div>

      <div className="flex-1 overflow-y-auto p-2 pb-24 custom-scrollbar">
        {loading ? (
          <div className="text-center py-8 text-gray-400">
            <div className="w-6 h-6 border-2 border-gray-300 border-t-indigo-500 rounded-full animate-spin mx-auto mb-2"></div>
            加载中...
          </div>
        ) : error ? (
          <div className="text-center py-8 text-red-500">{error}</div>
        ) : filteredDepartments.length === 0 ? (
          <div className="text-center py-8 text-gray-400">
            <p className="mb-2">暂无部门数据</p>
            <button onClick={() => setShowImporter(true)} className="text-indigo-600 underline">
              点击导入
            </button>
          </div>
        ) : (
          filteredDepartments.map((node) => renderDepartment(node))
        )}
      </div>

      {showImporter && isAdmin && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-96 shadow-xl">
            <h3 className="text-lg font-semibold mb-4">导入组织结构</h3>
            <p className="text-sm text-gray-500 mb-4">
              支持 Excel(.xlsx) 或 CSV 文件。
              <br />
              支持模板:
              <br />
              <code className="text-xs bg-gray-100 px-1">department_name + unit_name</code>
              <br />
              或
              <br />
              <code className="text-xs bg-gray-100 px-1">name + level + parent</code>
            </p>
            <input
              type="file"
              accept=".xlsx,.csv"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) {
                  handleImport(file);
                }
              }}
              className="w-full border rounded p-2 mb-4"
            />
            <div className="flex justify-end space-x-2">
              <button
                onClick={() => setShowImporter(false)}
                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      )}

      {modalType && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div className="bg-white rounded-lg p-6 w-80 shadow-xl">
            <h3 className="text-lg font-semibold mb-4">
              {modalType === "create" ? "新建部门" : "修改部门名称"}
            </h3>
            <input
              type="text"
              autoFocus
              value={modalInputVal}
              onChange={(e) => setModalInputVal(e.target.value)}
              placeholder="请输入部门名称..."
              className="w-full border border-gray-300 rounded p-2 mb-4 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  modalType === "create" ? handleCreateOrg() : handleUpdateOrg();
                }
              }}
            />
            <div className="flex justify-end space-x-2">
              <button
                onClick={() => setModalType(null)}
                className="px-4 py-2 text-gray-600 hover:bg-gray-100 rounded"
                disabled={isSubmitting}
              >
                取消
              </button>
              <button
                onClick={modalType === "create" ? handleCreateOrg : handleUpdateOrg}
                disabled={!modalInputVal.trim() || isSubmitting}
                className="px-4 py-2 bg-indigo-600 text-white rounded hover:bg-indigo-700 disabled:opacity-50"
              >
                {isSubmitting ? "提交中..." : "确定"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
