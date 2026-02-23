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
  selectedOrgId?: string | null;
  refreshKey?: number;
}

export default function OrganizationTree({
  onSelect,
  selectedOrgId,
  refreshKey,
}: OrganizationTreeProps) {
  const [departments, setDepartments] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showImporter, setShowImporter] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

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
      const res = await fetch(`/api/departments?t=${Date.now()}`, {
        signal: controller.signal,
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
        const fallbackRes = await fetch(`/api/organizations?t=${Date.now()}`, {
          signal: controller.signal,
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

  const handleImport = async (file: File) => {
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

  const filteredDepartments = departments.filter((item) =>
    item.name.toLowerCase().includes(searchQuery.trim().toLowerCase())
  );

  const renderDepartment = (node: Organization) => {
    const isSelected = selectedOrgId === node.id;
    return (
      <div key={node.id}>
        <div
          className={`flex items-center py-2 px-3 m-1 rounded-md cursor-pointer transition-all duration-200 group ${
            isSelected
              ? "bg-indigo-600 shadow-md shadow-indigo-500/20 text-white font-medium"
              : "hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300"
          }`}
          onClick={() => onSelect(node)}
          title={node.name}
        >
          <span className="truncate flex-1 text-sm tracking-tight">{node.name}</span>

          {!isSelected && (
            <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded border bg-blue-50 text-blue-600 border-blue-100">
              部门
            </span>
          )}

          {node.job_count > 0 && (
            <span className="text-xs text-gray-400 ml-2">{node.job_count}任务</span>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 border-b border-gray-200 flex items-center justify-between">
        <h3 className="font-semibold text-gray-700">部门视图</h3>
        <div className="flex space-x-2">
          <button
            onClick={() => onSelect(null)}
            className="text-xs text-gray-500 hover:text-gray-700"
          >
            全部
          </button>
          <button
            onClick={() => setShowImporter(true)}
            className="text-xs text-indigo-600 hover:text-indigo-800"
          >
            导入
          </button>
        </div>
      </div>

      <div className="px-3 py-2 border-b border-gray-100">
        <input
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="搜索部门"
          className="w-full text-sm border border-gray-200 rounded-md px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </div>

      <div className="flex-1 overflow-auto p-2">
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

      {showImporter && (
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
    </div>
  );
}
