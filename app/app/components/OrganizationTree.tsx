"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type OrganizationNode = {
  id: string;
  name: string;
  level: string;
  level_name?: string;
  parent_id: string | null;
  children: OrganizationNode[];
  job_count: number;
  issue_count: number;
};

type OrganizationTreeProps = {
  onSelect: (org: OrganizationNode | null) => void;
  onGlobalBatchUpload?: () => void;
  hideUtilityActions?: boolean;
  openImporterSignal?: number;
  isAdmin?: boolean;
  selectedOrgId?: string | null;
  refreshKey?: number;
};

type DepartmentsResponse = {
  departments?: OrganizationNode[];
};

type TreeResponse = {
  tree?: OrganizationNode[];
};

function parseErrorMessage(payload: any, fallback: string): string {
  if (payload && typeof payload === "object") {
    return (
      payload.detail ||
      payload.error ||
      payload.message ||
      (Array.isArray(payload.errors) ? payload.errors.join("，") : "") ||
      fallback
    );
  }
  return fallback;
}

function sortByName<T extends { name: string }>(items: T[]): T[] {
  return [...items].sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
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
  const [departments, setDepartments] = useState<OrganizationNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showImporter, setShowImporter] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [modalType, setModalType] = useState<"create" | "edit" | null>(null);
  const [modalOrgId, setModalOrgId] = useState<string | null>(null);
  const [modalInputValue, setModalInputValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const collectDepartmentsFromTree = useCallback((nodes: OrganizationNode[]): OrganizationNode[] => {
    const result: OrganizationNode[] = [];

    const walk = (items: OrganizationNode[]) => {
      for (const item of items) {
        if (item.level === "department") {
          result.push(item);
        }
        if (Array.isArray(item.children) && item.children.length > 0) {
          walk(item.children);
        }
      }
    };

    walk(nodes);
    return result;
  }, []);

  const loadDepartments = useCallback(async () => {
    setLoading(true);
    setError(null);

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 10000);

    try {
      const response = await fetch("/api/departments", {
        signal: controller.signal,
        cache: "no-store",
      });
      const payload = (await response.json().catch(() => ({}))) as DepartmentsResponse;
      if (!response.ok) {
        throw new Error("departments api not ok");
      }

      const rows = Array.isArray(payload.departments) ? payload.departments : [];
      if (rows.length > 0) {
        setDepartments(sortByName(rows));
        return;
      }

      const fallbackResponse = await fetch("/api/organizations", {
        signal: controller.signal,
        cache: "no-store",
      });
      const fallbackPayload = (await fallbackResponse.json().catch(() => ({}))) as TreeResponse;
      if (!fallbackResponse.ok) {
        setDepartments([]);
        return;
      }

      const tree = Array.isArray(fallbackPayload.tree) ? fallbackPayload.tree : [];
      setDepartments(sortByName(collectDepartmentsFromTree(tree)));
    } catch (fetchError) {
      console.error(fetchError);
      setDepartments([]);
      setError("加载组织架构失败");
    } finally {
      window.clearTimeout(timeoutId);
      setLoading(false);
    }
  }, [collectDepartmentsFromTree]);

  useEffect(() => {
    void loadDepartments();
  }, [loadDepartments, refreshKey]);

  useEffect(() => {
    if (isAdmin && openImporterSignal > 0) {
      setShowImporter(true);
    }
  }, [isAdmin, openImporterSignal]);

  const filteredDepartments = useMemo(() => {
    const keyword = searchQuery.trim().toLowerCase();
    if (!keyword) {
      return departments;
    }
    return departments.filter((item) => item.name.toLowerCase().includes(keyword));
  }, [departments, searchQuery]);

  const handleImport = async (file: File) => {
    if (!isAdmin) {
      window.alert("仅管理员可以导入组织结构。");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("clear_existing", "true");

    try {
      const response = await fetch("/api/organizations/import", {
        method: "POST",
        body: formData,
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(parseErrorMessage(payload, "导入失败"));
      }

      window.alert(`导入成功，共导入 ${payload.imported ?? 0} 个组织。`);
      setShowImporter(false);
      await loadDepartments();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "导入失败");
    }
  };

  const handleCreateOrg = async () => {
    if (!isAdmin) {
      window.alert("仅管理员可以创建部门。");
      return;
    }

    const name = modalInputValue.trim();
    if (!name) {
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch("/api/organizations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, level: "department" }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(parseErrorMessage(payload, "创建部门失败"));
      }

      setModalType(null);
      setModalInputValue("");
      await loadDepartments();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "创建部门失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUpdateOrg = async () => {
    if (!isAdmin) {
      window.alert("仅管理员可以修改部门。");
      return;
    }

    const name = modalInputValue.trim();
    if (!modalOrgId || !name) {
      return;
    }

    setIsSubmitting(true);
    try {
      const response = await fetch(`/api/organizations/${encodeURIComponent(modalOrgId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(parseErrorMessage(payload, "更新部门失败"));
      }

      setModalType(null);
      setModalOrgId(null);
      setModalInputValue("");
      await loadDepartments();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "更新部门失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteOrg = async (id: string, name: string) => {
    if (!isAdmin) {
      window.alert("仅管理员可以删除部门。");
      return;
    }

    try {
      const previewResponse = await fetch(
        `/api/organizations/${encodeURIComponent(id)}/delete-preview`,
        { cache: "no-store" },
      );
      const previewPayload = await previewResponse.json().catch(() => ({}));
      if (!previewResponse.ok) {
        throw new Error(parseErrorMessage(previewPayload, "获取删除影响范围失败"));
      }

      const summary = previewPayload.summary || {};
      const confirmed = window.confirm(
        `确定要删除“${name}”吗？\n\n` +
          `将删除 ${summary.organization_count ?? 0} 个组织（其中单位 ${summary.unit_count ?? 0} 个），` +
          `影响 ${summary.job_count ?? 0} 个任务关联。`,
      );
      if (!confirmed) {
        return;
      }

      const response = await fetch(`/api/organizations/${encodeURIComponent(id)}/delete`, {
        method: "POST",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(parseErrorMessage(payload, "删除部门失败"));
      }

      if (selectedOrgId === id) {
        onSelect(null);
      }
      await loadDepartments();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : "删除部门失败");
    }
  };

  const renderDepartment = (node: OrganizationNode) => {
    const isSelected = selectedOrgId === node.id;
    const hasIssues = Number(node.issue_count ?? 0) > 0;

    return (
      <div key={node.id}>
        <div
          data-testid={`organization-tree-node-${node.id}`}
          className={`group m-1.5 flex cursor-pointer items-center justify-between rounded-xl border px-4 py-3 transition-all duration-200 ${
            isSelected
              ? "border-indigo-100 bg-indigo-50/80 shadow-sm"
              : "border-transparent hover:bg-white hover:shadow-sm"
          }`}
          onClick={() => onSelect(node)}
          title={node.name}
        >
          <div className="flex items-center gap-3 overflow-hidden">
            <div
              className={`h-6 w-1.5 rounded-full transition-all duration-200 ${
                isSelected ? "bg-indigo-600" : "bg-transparent group-hover:bg-gray-200"
              }`}
            />
            <span
              className={`truncate text-sm tracking-tight ${
                isSelected ? "font-semibold text-indigo-900" : "font-medium text-gray-700"
              }`}
            >
              {node.name}
            </span>
          </div>

          <div className="flex flex-shrink-0 items-center gap-2">
            {node.job_count > 0 ? (
              <span
                className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${
                  hasIssues
                    ? "border-red-100 bg-red-50 text-red-600"
                    : "border-green-100 bg-green-50 text-green-600"
                }`}
              >
                {hasIssues ? `问题 ${node.issue_count}` : "正常"}
              </span>
            ) : null}

            {isAdmin ? (
              <div
                className={`items-center gap-1 transition-opacity ${
                  isSelected ? "flex opacity-100" : "hidden opacity-0 group-hover:flex group-hover:opacity-100"
                }`}
              >
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    setModalType("edit");
                    setModalOrgId(node.id);
                    setModalInputValue(node.name);
                  }}
                  data-testid={`organization-tree-edit-${node.id}`}
                  className="rounded-md p-1 text-gray-400 transition-colors hover:bg-indigo-50 hover:text-indigo-600"
                  title="编辑名称"
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleDeleteOrg(node.id, node.name);
                  }}
                  data-testid={`organization-tree-delete-${node.id}`}
                  className="rounded-md p-1 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-500"
                  title="删除部门"
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
    <div className="flex flex-1 flex-col overflow-hidden">
      <div className="space-y-3 border-b border-gray-200 p-3">
        <div className="flex items-center justify-between">
          <h3 className="flex items-center font-semibold text-gray-700">
            部门视图
            {isAdmin ? (
              <button
                type="button"
                onClick={() => {
                  setModalType("create");
                  setModalInputValue("");
                }}
                data-testid="organization-tree-create-department"
                className="ml-2 rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-indigo-600"
                title="新建部门"
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </button>
            ) : null}
          </h3>
          <button
            type="button"
            onClick={() => onSelect(null)}
            className="rounded-md border border-gray-200 px-2 py-1 text-xs text-gray-500 transition-colors hover:border-gray-300 hover:bg-gray-50 hover:text-gray-700"
            title="查看全部部门"
          >
            全部
          </button>
        </div>

        {!hideUtilityActions ? (
          <div className="grid grid-cols-2 gap-2">
            {onGlobalBatchUpload ? (
              <button
                type="button"
                onClick={onGlobalBatchUpload}
                disabled={!isAdmin}
                className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50 to-blue-50 px-3 py-2 text-left transition-all hover:border-indigo-300 hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-60"
                title={isAdmin ? "上传全区 PDF 文档（批量）" : "仅管理员可操作"}
              >
                <div className="flex items-center gap-1.5 text-[13px] font-semibold text-indigo-700">
                  <svg className="h-4 w-4 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
                  </svg>
                  全区上传
                </div>
                <div className="mt-1 text-[11px] text-indigo-500">批量上传全区 PDF</div>
              </button>
            ) : (
              <div />
            )}

            <button
              type="button"
              onClick={() => setShowImporter(true)}
              disabled={!isAdmin}
              data-testid="organization-tree-import-button"
              className="rounded-xl border border-emerald-200 bg-gradient-to-br from-emerald-50 to-teal-50 px-3 py-2 text-left transition-all hover:border-emerald-300 hover:shadow-sm disabled:cursor-not-allowed disabled:opacity-60"
              title={isAdmin ? "导入部门和单位名称模板（CSV / XLSX）" : "仅管理员可操作"}
            >
              <div className="flex items-center gap-1.5 text-[13px] font-semibold text-emerald-700">
                <svg className="h-4 w-4 text-emerald-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M12 11v8m0 0l-3-3m3 3l3-3" />
                </svg>
                导入组织
              </div>
              <div className="mt-1 text-[11px] text-emerald-600">导入部门/单位名称</div>
            </button>
          </div>
        ) : null}
      </div>

      <div className="border-b border-gray-100 px-3 py-2">
        <input
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.target.value)}
          data-testid="organization-tree-search"
          placeholder="搜索部门"
          className="w-full rounded-md border border-gray-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </div>

      <div className="custom-scrollbar flex-1 overflow-y-auto p-2 pb-24">
        {loading ? (
          <div className="py-8 text-center text-gray-400">
            <div className="mx-auto mb-2 h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-indigo-500" />
            正在加载...
          </div>
        ) : error ? (
          <div className="py-8 text-center text-red-500">{error}</div>
        ) : filteredDepartments.length === 0 ? (
          <div className="py-8 text-center text-gray-400">
            <p className="mb-2">{searchQuery.trim() ? "没有匹配的部门" : "暂无部门数据"}</p>
            {isAdmin ? (
              <button
                type="button"
                onClick={() => setShowImporter(true)}
                className="text-indigo-600 underline"
              >
                去导入组织
              </button>
            ) : null}
          </div>
        ) : (
          filteredDepartments.map((node) => renderDepartment(node))
        )}
      </div>

      {showImporter && isAdmin ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
          data-testid="organization-tree-importer"
        >
          <div className="w-96 rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-4 text-lg font-semibold">导入组织结构</h3>
            <p className="mb-4 text-sm text-gray-500">
              支持 Excel（.xlsx）和 CSV 文件。
              <br />
              可使用以下模板：
              <br />
              <code className="bg-gray-100 px-1 text-xs">department_name + unit_name</code>
              <br />
              或
              <br />
              <code className="bg-gray-100 px-1 text-xs">name + level + parent</code>
            </p>
            <input
              type="file"
              accept=".xlsx,.csv"
              data-testid="organization-tree-import-file"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  void handleImport(file);
                }
              }}
              className="mb-4 w-full rounded border p-2"
            />
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => setShowImporter(false)}
                className="rounded px-4 py-2 text-gray-600 transition-colors hover:bg-gray-100"
              >
                取消
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {modalType ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="w-80 rounded-lg bg-white p-6 shadow-xl">
            <h3 className="mb-4 text-lg font-semibold">
              {modalType === "create" ? "新建部门" : "修改部门名称"}
            </h3>
            <input
              type="text"
              autoFocus
              value={modalInputValue}
              onChange={(event) => setModalInputValue(event.target.value)}
              data-testid="organization-tree-modal-input"
              placeholder="请输入部门名称..."
              className="mb-4 w-full rounded border border-gray-300 p-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  if (modalType === "create") {
                    void handleCreateOrg();
                  } else {
                    void handleUpdateOrg();
                  }
                }
              }}
            />
            <div className="flex justify-end space-x-2">
              <button
                type="button"
                onClick={() => {
                  if (!isSubmitting) {
                    setModalType(null);
                    setModalOrgId(null);
                    setModalInputValue("");
                  }
                }}
                className="rounded px-4 py-2 text-gray-600 transition-colors hover:bg-gray-100"
                disabled={isSubmitting}
              >
                取消
              </button>
              <button
                type="button"
                onClick={() => {
                  if (modalType === "create") {
                    void handleCreateOrg();
                  } else {
                    void handleUpdateOrg();
                  }
                }}
                disabled={!modalInputValue.trim() || isSubmitting}
                data-testid="organization-tree-modal-submit"
                className="rounded bg-indigo-600 px-4 py-2 text-white transition-colors hover:bg-indigo-700 disabled:opacity-50"
              >
                {isSubmitting ? "提交中..." : "确定"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
