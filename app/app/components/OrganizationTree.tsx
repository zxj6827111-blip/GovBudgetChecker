"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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

type OrganizationRecordInput = {
  id: string;
  name: string;
  level: string;
  level_name?: string;
  parent_id: string | null;
  children?: OrganizationRecordInput[];
  job_count?: number;
  issue_count?: number;
};

type OrganizationTreeProps = {
  onSelect: (org: OrganizationNode | null) => void;
  onGlobalBatchUpload?: () => void;
  hideUtilityActions?: boolean;
  openImporterSignal?: number;
  isAdmin?: boolean;
  selectedOrgId?: string | null;
  refreshKey?: number;
  fallbackOrganizations?: OrganizationRecordInput[];
  onChanged?: () => Promise<void> | void;
};

type TreeResponse = {
  tree?: OrganizationNode[];
};

function parseErrorMessage(payload: any, fallback: string): string {
  if (payload && typeof payload === "object") {
    const message = String(
      payload.detail ||
      payload.error ||
      payload.message ||
      (Array.isArray(payload.errors) ? payload.errors.join(", ") : "") ||
      fallback
    );
    if (/too many requests/i.test(message)) {
      return "当前操作请求过于频繁，后端暂时限流。请稍等一分钟后重试。";
    }
    return message;
  }
  return fallback;
}

function normalizeSearchValue(value: string): string {
  return value.trim().toLowerCase();
}

function sortTreeByName(nodes: OrganizationNode[]): OrganizationNode[] {
  return [...nodes]
    .map((node) => ({
      ...node,
      children: sortTreeByName(Array.isArray(node.children) ? node.children : []),
    }))
    .sort((left, right) => left.name.localeCompare(right.name, "zh-CN"));
}

function toOrganizationNode(item: OrganizationRecordInput): OrganizationNode {
  return {
    id: String(item.id),
    name: String(item.name ?? ""),
    level: String(item.level ?? "organization"),
    level_name: item.level_name,
    parent_id: item.parent_id ?? null,
    children: [],
    job_count: Number(item.job_count ?? 0),
    issue_count: Number(item.issue_count ?? 0),
  };
}

function buildTreeFromRecords(records?: OrganizationRecordInput[]): OrganizationNode[] {
  if (!Array.isArray(records) || records.length === 0) {
    return [];
  }

  const nodeById = new Map<string, OrganizationNode>();
  for (const record of records) {
    if (!record?.id) {
      continue;
    }
    nodeById.set(String(record.id), toOrganizationNode(record));
  }

  const roots: OrganizationNode[] = [];
  for (const node of Array.from(nodeById.values())) {
    const parentId = node.parent_id ? String(node.parent_id) : null;
    const parent = parentId ? nodeById.get(parentId) : null;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  return sortTreeByName(roots);
}

function buildInitialExpandedState(
  nodes: OrganizationNode[],
  depth = 0,
  result: Record<string, boolean> = {},
): Record<string, boolean> {
  for (const node of nodes) {
    if (depth < 2 && Array.isArray(node.children) && node.children.length > 0) {
      result[node.id] = true;
    }
    if (Array.isArray(node.children) && node.children.length > 0) {
      buildInitialExpandedState(node.children, depth + 1, result);
    }
  }
  return result;
}

function collectExpandableNodeIds(nodes: OrganizationNode[], result: string[] = []): string[] {
  for (const node of nodes) {
    const children = Array.isArray(node.children) ? node.children : [];
    if (children.length > 0) {
      result.push(node.id);
      collectExpandableNodeIds(children, result);
    }
  }
  return result;
}

function findPathToNode(nodes: OrganizationNode[], targetId: string): string[] {
  for (const node of nodes) {
    if (node.id === targetId) {
      return [node.id];
    }
    const children = Array.isArray(node.children) ? node.children : [];
    const childPath = findPathToNode(children, targetId);
    if (childPath.length > 0) {
      return [node.id, ...childPath];
    }
  }
  return [];
}

function findNodeById(nodes: OrganizationNode[], targetId: string): OrganizationNode | null {
  for (const node of nodes) {
    if (node.id === targetId) {
      return node;
    }
    const childNode = findNodeById(Array.isArray(node.children) ? node.children : [], targetId);
    if (childNode) {
      return childNode;
    }
  }
  return null;
}

function nodeContainsTarget(node: OrganizationNode, targetId: string): boolean {
  if (node.id === targetId) {
    return true;
  }
  return (node.children || []).some((child) => nodeContainsTarget(child, targetId));
}

function filterTree(nodes: OrganizationNode[], query: string): OrganizationNode[] {
  return nodes.flatMap((node) => {
    const children = Array.isArray(node.children) ? filterTree(node.children, query) : [];
    const isMatch = normalizeSearchValue(node.name).includes(query);

    if (!isMatch && children.length === 0) {
      return [];
    }

    return [
      {
        ...node,
        children: isMatch ? sortTreeByName(Array.isArray(node.children) ? node.children : []) : children,
      },
    ];
  });
}

function getNodeBadge(node: OrganizationNode) {
  if (Number(node.issue_count ?? 0) > 0) {
    return {
      className: "border-red-100 bg-red-50 text-red-600",
      text: `问题 ${node.issue_count}`,
    };
  }
  if (Number(node.job_count ?? 0) > 0) {
    return {
      className: "border-slate-200 bg-slate-50 text-slate-600",
      text: `报告 ${node.job_count}`,
    };
  }
  return null;
}

function getNodeLevelLabel(level: string) {
  return level === "department" ? "部门" : level === "unit" ? "单位" : "组织";
}

export default function OrganizationTree({
  onSelect,
  onGlobalBatchUpload,
  hideUtilityActions = false,
  openImporterSignal = 0,
  isAdmin = false,
  selectedOrgId,
  refreshKey,
  fallbackOrganizations,
  onChanged,
}: OrganizationTreeProps) {
  const [tree, setTree] = useState<OrganizationNode[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showImporter, setShowImporter] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [modalType, setModalType] = useState<"create" | "edit" | null>(null);
  const [modalOrgId, setModalOrgId] = useState<string | null>(null);
  const [modalInputValue, setModalInputValue] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const requestIdRef = useRef(0);
  const fallbackTree = useMemo(
    () => buildTreeFromRecords(fallbackOrganizations),
    [fallbackOrganizations],
  );

  const applyFallbackTree = useCallback(() => {
    if (fallbackTree.length === 0) {
      return false;
    }

    setTree(fallbackTree);
    setExpanded((current) => {
      const nextExpanded =
        Object.keys(current).length > 0 ? { ...current } : buildInitialExpandedState(fallbackTree);
      if (selectedOrgId) {
        for (const id of findPathToNode(fallbackTree, selectedOrgId)) {
          nextExpanded[id] = true;
        }
      }
      return nextExpanded;
    });
    setError(null);
    return true;
  }, [fallbackTree, selectedOrgId]);

  const loadOrganizations = useCallback(async () => {
    const requestId = ++requestIdRef.current;
    const hasFallbackTree = applyFallbackTree();
    setLoading(!hasFallbackTree);
    setError(null);

    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 10000);

    try {
      const response = await fetch("/api/organizations?stats=none", {
        signal: controller.signal,
        cache: "no-store",
      });
      const payload = (await response.json().catch(() => ({}))) as TreeResponse;
      if (!response.ok) {
        throw new Error(parseErrorMessage(payload, `organizations api not ok (${response.status})`));
      }

      if (requestId !== requestIdRef.current) {
        return;
      }

      const nextTree = sortTreeByName(Array.isArray(payload.tree) ? payload.tree : []);
      setTree(nextTree);
      setExpanded((current) => {
        const nextExpanded =
          Object.keys(current).length > 0 ? { ...current } : buildInitialExpandedState(nextTree);
        if (selectedOrgId) {
          for (const id of findPathToNode(nextTree, selectedOrgId)) {
            nextExpanded[id] = true;
          }
        }
        return nextExpanded;
      });
    } catch (fetchError) {
      if (requestId !== requestIdRef.current) {
        return;
      }
      if (applyFallbackTree()) {
        if (process.env.NODE_ENV !== "production") {
          console.warn("Using fallback organization tree after organization API failed.", fetchError);
        }
        return;
      }
      console.error(fetchError);
      setTree([]);
      setError("加载组织结构失败");
    } finally {
      window.clearTimeout(timeoutId);
      if (requestId === requestIdRef.current) {
        setLoading(false);
      }
    }
  }, [applyFallbackTree, selectedOrgId]);

  useEffect(() => {
    void loadOrganizations();
  }, [loadOrganizations, refreshKey]);

  useEffect(() => {
    if (!selectedOrgId) {
      return;
    }
    setExpanded((current) => {
      const path = findPathToNode(tree, selectedOrgId);
      if (path.length === 0) {
        return current;
      }
      const nextExpanded = { ...current };
      for (const id of path) {
        nextExpanded[id] = true;
      }
      return nextExpanded;
    });
  }, [selectedOrgId, tree]);

  useEffect(() => {
    if (isAdmin && openImporterSignal > 0) {
      setShowImporter(true);
    }
  }, [isAdmin, openImporterSignal]);

  const normalizedSearchQuery = useMemo(
    () => normalizeSearchValue(searchQuery),
    [searchQuery],
  );

  const filteredTree = useMemo(() => {
    if (!normalizedSearchQuery) {
      return tree;
    }
    return filterTree(tree, normalizedSearchQuery);
  }, [normalizedSearchQuery, tree]);

  const expandableNodeIds = useMemo(() => collectExpandableNodeIds(tree), [tree]);
  const selectedNode = useMemo(
    () => (selectedOrgId ? findNodeById(tree, selectedOrgId) : null),
    [selectedOrgId, tree],
  );
  const createDepartmentParent = selectedNode && selectedNode.level !== "unit" ? selectedNode : null;

  const allExpandableNodesExpanded = useMemo(
    () => expandableNodeIds.length > 0 && expandableNodeIds.every((id) => expanded[id]),
    [expandableNodeIds, expanded],
  );

  const toggleExpanded = useCallback((id: string) => {
    setExpanded((current) => ({ ...current, [id]: !current[id] }));
  }, []);

  const toggleAllExpanded = useCallback(() => {
    setExpanded((current) => {
      if (allExpandableNodesExpanded) {
        const collapsed = { ...current };
        for (const id of expandableNodeIds) {
          collapsed[id] = false;
        }
        if (selectedOrgId) {
          for (const id of findPathToNode(tree, selectedOrgId)) {
            collapsed[id] = true;
          }
        }
        return collapsed;
      }

      const expandedAll = { ...current };
      for (const id of expandableNodeIds) {
        expandedAll[id] = true;
      }
      return expandedAll;
    });
  }, [allExpandableNodesExpanded, expandableNodeIds, selectedOrgId, tree]);

  const handleImport = async (file: File) => {
    if (!isAdmin) {
      window.alert("仅管理员可以导入组织结构");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    formData.append("clear_existing", "false");

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
      await loadOrganizations();
      await onChanged?.();
    } catch (importError) {
      window.alert(importError instanceof Error ? importError.message : "导入失败");
    }
  };

  const handleCreateOrg = async () => {
    if (!isAdmin) {
      window.alert("仅管理员可以创建部门");
      return;
    }

    const name = modalInputValue.trim();
    if (!name) {
      return;
    }

    setIsSubmitting(true);
    try {
      const requestBody: Record<string, string> = { name, level: "department" };
      if (createDepartmentParent) {
        requestBody.parent_id = createDepartmentParent.id;
      }

      const response = await fetch("/api/organizations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(parseErrorMessage(payload, "创建部门失败"));
      }

      setModalType(null);
      setModalInputValue("");
      await loadOrganizations();
      if (createDepartmentParent) {
        setExpanded((current) => ({ ...current, [createDepartmentParent.id]: true }));
      }

      if (payload && typeof payload === "object" && payload.id) {
        const createdNode = payload as Partial<OrganizationNode>;
        onSelect({
          id: String(createdNode.id),
          name: String(createdNode.name ?? name),
          level: String(createdNode.level ?? "department"),
          parent_id: createdNode.parent_id ?? createDepartmentParent?.id ?? null,
          children: Array.isArray(createdNode.children) ? createdNode.children : [],
          job_count: Number(createdNode.job_count ?? 0),
          issue_count: Number(createdNode.issue_count ?? 0),
        });
      }
      await onChanged?.();
    } catch (createError) {
      window.alert(createError instanceof Error ? createError.message : "创建部门失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleUpdateOrg = async () => {
    if (!isAdmin) {
      window.alert("仅管理员可以修改组织名称");
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
        throw new Error(parseErrorMessage(payload, "更新组织失败"));
      }

      setModalType(null);
      setModalOrgId(null);
      setModalInputValue("");
      await loadOrganizations();

      if (selectedOrgId === modalOrgId) {
        onSelect({
          ...(payload as OrganizationNode),
          children: Array.isArray((payload as any)?.children) ? (payload as any).children : [],
          job_count: Number((payload as any)?.job_count ?? 0),
          issue_count: Number((payload as any)?.issue_count ?? 0),
        });
      }
      await onChanged?.();
    } catch (updateError) {
      window.alert(updateError instanceof Error ? updateError.message : "更新组织失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteOrg = async (node: OrganizationNode) => {
    if (!isAdmin) {
      window.alert(`仅管理员可以删除${getNodeLevelLabel(node.level)}`);
      return;
    }

    try {
      const previewResponse = await fetch(
        `/api/organizations/${encodeURIComponent(node.id)}/delete-preview`,
        { cache: "no-store" },
      );
      const previewPayload = await previewResponse.json().catch(() => ({}));
      if (!previewResponse.ok) {
        throw new Error(parseErrorMessage(previewPayload, "获取删除影响范围失败"));
      }

      const summary = previewPayload.summary || {};
      const label = getNodeLevelLabel(node.level);
      const confirmed = window.confirm(
        [
          `确定要删除${label}“${node.name}”吗？`,
          `将删除组织 ${summary.organization_count ?? 0} 个，其中单位 ${summary.unit_count ?? 0} 个。`,
          `将影响任务关联 ${summary.job_count ?? 0} 条。`,
        ].join("\n"),
      );
      if (!confirmed) {
        return;
      }

      const response = await fetch(`/api/organizations/${encodeURIComponent(node.id)}/delete`, {
        method: "POST",
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(parseErrorMessage(payload, `删除${label}失败`));
      }

      if (selectedOrgId && nodeContainsTarget(node, selectedOrgId)) {
        onSelect(null);
      }
      await loadOrganizations();
      await onChanged?.();
    } catch (deleteError) {
      window.alert(deleteError instanceof Error ? deleteError.message : "删除组织失败");
    }
  };

  const renderNode = (node: OrganizationNode, depth = 0) => {
    const children = Array.isArray(node.children) ? node.children : [];
    const hasChildren = children.length > 0;
    const isSelected = selectedOrgId === node.id;
    const isExpanded = normalizedSearchQuery ? true : Boolean(expanded[node.id]);
    const badge = getNodeBadge(node);
    const levelLabel = getNodeLevelLabel(node.level);

    return (
      <div key={node.id}>
        <div
          data-testid={`organization-tree-node-${node.id}`}
          className={`group m-1.5 flex cursor-pointer items-center justify-between rounded-xl border px-4 py-3 transition-all duration-200 ${
            isSelected
              ? "border-indigo-100 bg-indigo-50/80 shadow-sm"
              : "border-transparent hover:bg-white hover:shadow-sm"
          }`}
          style={{ marginLeft: depth === 0 ? undefined : depth * 12 }}
          onClick={() => onSelect(node)}
          title={node.name}
        >
          <div className="flex min-w-0 items-center gap-3 overflow-hidden">
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                if (hasChildren) {
                  toggleExpanded(node.id);
                }
              }}
              className={`flex h-5 w-5 flex-shrink-0 items-center justify-center rounded text-gray-400 transition-colors ${
                hasChildren ? "hover:bg-gray-100 hover:text-gray-600" : "cursor-default"
              }`}
              aria-label={hasChildren ? `${isExpanded ? "收起" : "展开"}${node.name}` : `${node.name} 无下级组织`}
            >
              {hasChildren ? (
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  className={`h-4 w-4 transition-transform ${isExpanded ? "rotate-90" : ""}`}
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              ) : (
                <span className="inline-block h-2 w-2 rounded-full bg-gray-200" />
              )}
            </button>
            <div
              className={`h-6 w-1.5 rounded-full transition-all duration-200 ${
                isSelected ? "bg-indigo-600" : "bg-transparent group-hover:bg-gray-200"
              }`}
            />
            <div className="min-w-0">
              <div
                className={`truncate text-sm tracking-tight ${
                  isSelected ? "font-semibold text-indigo-900" : "font-medium text-gray-700"
                }`}
              >
                {node.name}
              </div>
              <div className="mt-0.5 text-[11px] text-gray-400">{levelLabel}</div>
            </div>
          </div>

          <div className="ml-3 flex flex-shrink-0 items-center gap-2">
            {badge ? (
              <span
                className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${badge.className}`}
              >
                {badge.text}
              </span>
            ) : null}

            {isAdmin ? (
              <div
                className={`flex items-center gap-1 transition-opacity ${
                  isSelected ? "opacity-100" : "opacity-60 group-hover:opacity-100"
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
                  title={`编辑${levelLabel}名称`}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                </button>
                <button
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    void handleDeleteOrg(node);
                  }}
                  data-testid={`organization-tree-delete-${node.id}`}
                  className="rounded-md p-1 text-gray-400 transition-colors hover:bg-red-50 hover:text-red-500"
                  title={`删除${levelLabel}`}
                >
                  <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                  </svg>
                </button>
              </div>
            ) : null}
          </div>
        </div>

        {hasChildren && isExpanded ? (
          <div>{children.map((child) => renderNode(child, depth + 1))}</div>
        ) : null}
      </div>
    );
  };

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden">
      <div className="space-y-3 border-b border-gray-200 p-3">
        <div className="flex items-center justify-between">
          <h3 className="flex items-center font-semibold text-gray-700">
            组织视图
            {isAdmin ? (
              <button
                type="button"
                onClick={() => {
                  setModalType("create");
                  setModalInputValue("");
                }}
                data-testid="organization-tree-create-department"
                className="ml-2 rounded p-1 text-gray-400 transition-colors hover:bg-gray-100 hover:text-indigo-600"
                title={createDepartmentParent ? `在“${createDepartmentParent.name}”下新建部门` : "新建顶层部门"}
              >
                <svg xmlns="http://www.w3.org/2000/svg" className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                </svg>
              </button>
            ) : null}
          </h3>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={toggleAllExpanded}
              disabled={expandableNodeIds.length === 0}
              data-testid="organization-tree-toggle-all"
              className="rounded-md border border-gray-200 px-2 py-1 text-xs text-gray-500 transition-colors hover:border-gray-300 hover:bg-gray-50 hover:text-gray-700 disabled:cursor-not-allowed disabled:opacity-50"
              title={allExpandableNodesExpanded ? "收起所有下级组织" : "展开所有下级组织"}
            >
              {allExpandableNodesExpanded ? "收起" : "展开"}
            </button>
            <button
              type="button"
              onClick={() => onSelect(null)}
              className="rounded-md border border-gray-200 px-2 py-1 text-xs text-gray-500 transition-colors hover:border-gray-300 hover:bg-gray-50 hover:text-gray-700"
              title="查看全部组织"
            >
              全部
            </button>
          </div>
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
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M12 11v8m0 0l-3-3m3 3l3-3" />
                  </svg>
                  批量上传
                </div>
                <div className="mt-1 text-[11px] text-indigo-600">上传多个 PDF 并自动匹配组织</div>
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
          placeholder="搜索部门或单位"
          className="w-full rounded-md border border-gray-200 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
        />
      </div>

      <div
        className="custom-scrollbar min-h-0 flex-1 overflow-y-auto overscroll-contain p-2 pb-24"
        data-testid="organization-tree-scroll"
      >
        {loading ? (
          <div className="py-8 text-center text-gray-400">
            <div className="mx-auto mb-2 h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-indigo-500" />
            正在加载...
          </div>
        ) : error ? (
          <div className="py-8 text-center text-red-500">{error}</div>
        ) : filteredTree.length === 0 ? (
          <div className="py-8 text-center text-gray-400">
            <p className="mb-2">{normalizedSearchQuery ? "没有匹配的组织" : "暂无组织数据"}</p>
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
          filteredTree.map((node) => renderNode(node))
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
              {modalType === "create"
                ? createDepartmentParent
                  ? `新建“${createDepartmentParent.name}”下级部门`
                  : "新建部门"
                : "修改组织名称"}
            </h3>
            <input
              type="text"
              autoFocus
              value={modalInputValue}
              onChange={(event) => setModalInputValue(event.target.value)}
              data-testid="organization-tree-modal-input"
              placeholder={modalType === "create" ? "请输入部门名称..." : "请输入组织名称..."}
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
