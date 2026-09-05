/**
 * SettingsPage：Task 8.4 系统设置页。
 *
 * 复用既有 SystemManagementPanel（54KB，内部已包含用户管理
 * UserManagementPanel、组织架构、运维操作、分析结果、规则包、配置留档等
 * 全部管理能力），**只套新布局（SectionTitle + 页面容器），不重写业务逻辑**。
 *
 * 组织数据：请求 /api/organizations?stats=none 拿只读导航树后拉平为
 * OrganizationRecord[]（与 OrganizationFilterSelect / 旧 gbc-ui-demo 的
 * settings 页同一数据获取模式）；onRefresh 触发重新拉取。
 *
 * 权限：路由层 AdminOnlyGuard 门控（客户端），面板内部的管理操作
 * 继续由后端各端点的 require_admin 把守——两道防线都是既有能力。
 */
"use client";

import { useCallback, useEffect, useState } from "react";

import { SectionTitle } from "@/components/ui";
import SystemManagementPanel from "@/components/admin/SystemManagementPanel";
import type { OrganizationRecord } from "@/lib/uiAdapters";

interface OrganizationTreeNode {
  id: string;
  name: string;
  level?: string;
  children?: OrganizationTreeNode[];
}

interface OrganizationTreeResponse {
  tree?: OrganizationTreeNode[];
}

/** 深度优先拉平组织树为 OrganizationRecord 列表（每个节点恰好出现一次）。
 *  SystemManagementPanel 把 organizations 当扁平列表用（概览计数与
 *  OrganizationSection 的兜底数据；该 section 内部会自己重拉完整树）。 */
function flattenOrgTree(nodes: OrganizationTreeNode[] | undefined | null): OrganizationRecord[] {
  if (!Array.isArray(nodes)) {
    return [];
  }
  const result: OrganizationRecord[] = [];
  for (const node of nodes) {
    if (!node?.id) {
      continue;
    }
    result.push({
      id: String(node.id),
      name: String(node.name ?? ""),
      level: String(node.level ?? "organization"),
      parent_id: null,
    });
    if (Array.isArray(node.children) && node.children.length > 0) {
      result.push(...flattenOrgTree(node.children));
    }
  }
  return result;
}

export function SettingsPage() {
  const [organizations, setOrganizations] = useState<OrganizationRecord[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);

  const loadOrganizations = useCallback(async () => {
    try {
      const response = await fetch("/api/organizations?stats=none", { cache: "no-store" });
      if (!response.ok) {
        setLoadFailed(true);
        return;
      }
      const payload = (await response.json()) as OrganizationTreeResponse;
      setOrganizations(flattenOrgTree(payload.tree));
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, []);

  useEffect(() => {
    void loadOrganizations();
  }, [loadOrganizations]);

  return (
    <div className="p-8" data-testid="gbc-settings-page">
      <SectionTitle
        title="系统设置"
        desc="组织架构、用户与权限、运维操作、规则包与配置留档（沿用既有系统管理能力）。"
      />

      {loadFailed ? (
        <div
          className="mt-4 rounded-card border border-warning-200 bg-warning-50 px-4 py-3 text-sm text-warning-800"
          data-testid="gbc-settings-org-load-failed"
        >
          组织列表加载失败，组织相关功能可能不可用；可稍后在面板内刷新重试。
        </div>
      ) : null}

      <div className="mt-6">
        <SystemManagementPanel
          organizations={organizations}
          onRefresh={() => void loadOrganizations()}
        />
      </div>
    </div>
  );
}

export default SettingsPage;
