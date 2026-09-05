"use client";

import { useEffect, useMemo, useState } from "react";

/**
 * OrganizationFilterSelect：组织树在工作台/处理队列/任务历史等屏的降级形态（决策 2=a）。
 *
 * 原型图左侧不再画完整组织树，组织维度改为工作台内的筛选下拉。本组件复用
 * `OrganizationTree.tsx` 的数据获取模式（请求 `/api/organizations?stats=none`
 * 拿只读导航树，见该文件 `loadOrganizations()`），但只做"拉平 + 下拉呈现"，
 * 不带编辑/删除/导入这些管理员操作——那些属于 `OrganizationTree.tsx` 自己的
 * 职责，本组件的定位是"跨屏复用的筛选器"，参见任务书"抽成共享组件供后续
 * queue/history 复用"。
 *
 * 拉平后按"部门 · 单位"的两级缩进展示（用 depth 生成前导空格），
 * value 传给上层的是 organization_id，用于筛选处理队列表的任务列表。
 */
export interface OrganizationFilterOption {
  id: string;
  name: string;
  level: string;
  depth: number;
  /**
   * 结构父节点 id（顶层为 null）。Task 9 共享化新增：上传中心的单文件补齐
   * 表单（UploadConfirmationPanel）需要按 parent_id 过滤部门/单位，
   * 此前它在本地复制了一份拉平实现（flattenWithParent），现统一收敛到
   * 这一份——拉平语义必须全仓只有一个来源。
   */
  parent_id: string | null;
}

interface OrganizationTreeNode {
  id: string;
  name: string;
  level?: string;
  parent_id?: string | null;
  children?: OrganizationTreeNode[];
}

interface OrganizationTreeResponse {
  tree?: OrganizationTreeNode[];
}

/** 把组织树拉平为一维列表，深度优先，保留层级信息供展示缩进。 */
export function flattenOrganizationTree(
  nodes: OrganizationTreeNode[] | undefined | null,
  depth = 0,
  structuralParentId: string | null = null,
): OrganizationFilterOption[] {
  if (!Array.isArray(nodes)) {
    return [];
  }
  const result: OrganizationFilterOption[] = [];
  for (const node of nodes) {
    if (!node?.id) {
      continue;
    }
    result.push({
      id: String(node.id),
      name: String(node.name ?? ""),
      level: String(node.level ?? "organization"),
      depth,
      // 节点自带 parent_id 优先（后端树响应包含该字段）；缺失时用递归路径上
      // 的结构父 id 兜底，保证"单位属于哪个部门"永远可判定。
      parent_id: node.parent_id ? String(node.parent_id) : structuralParentId,
    });
    if (Array.isArray(node.children) && node.children.length > 0) {
      result.push(...flattenOrganizationTree(node.children, depth + 1, String(node.id)));
    }
  }
  return result;
}

export interface OrganizationFilterSelectProps {
  value: string;
  onChange: (organizationId: string) => void;
  className?: string;
  /** 测试锚点，默认 gbc-organization-filter。 */
  "data-testid"?: string;
}

const ALL_ORGANIZATIONS_VALUE = "";

export function OrganizationFilterSelect({
  value,
  onChange,
  className,
  "data-testid": testId = "gbc-organization-filter",
}: OrganizationFilterSelectProps) {
  const [options, setOptions] = useState<OrganizationFilterOption[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadOrganizations() {
      try {
        const response = await fetch("/api/organizations?stats=none", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) {
            setLoadFailed(true);
          }
          return;
        }
        const payload = (await response.json()) as OrganizationTreeResponse;
        if (!cancelled) {
          setOptions(flattenOrganizationTree(payload.tree));
          setLoadFailed(false);
        }
      } catch {
        if (!cancelled) {
          setLoadFailed(true);
        }
      }
    }

    void loadOrganizations();
    return () => {
      cancelled = true;
    };
  }, []);

  const optionLabel = useMemo(
    () => (option: OrganizationFilterOption) => `${"　".repeat(option.depth)}${option.name}`,
    [],
  );

  return (
    <select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      data-testid={testId}
      aria-label="按组织筛选"
      className={className ?? "rounded-md border border-border bg-white px-3 py-2 text-sm text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"}
    >
      <option value={ALL_ORGANIZATIONS_VALUE}>全部组织</option>
      {loadFailed ? (
        <option value={ALL_ORGANIZATIONS_VALUE} disabled>
          组织列表加载失败
        </option>
      ) : (
        options.map((option) => (
          <option key={option.id} value={option.id}>
            {optionLabel(option)}
          </option>
        ))
      )}
    </select>
  );
}

export default OrganizationFilterSelect;
