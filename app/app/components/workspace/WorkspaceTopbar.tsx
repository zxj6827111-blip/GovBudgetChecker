"use client";

import { usePathname, useSearchParams } from "next/navigation";
import Link from "next/link";
import type { Route } from "next";
import { useEffect, useState, type ReactNode } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { GlobalMaterialSearch } from "../materials/GlobalMaterialSearch";

import { NAV_GROUP_LABELS, findNavItemByPathname, resolveNavLabel } from "./nav";
import { resolveServiceHealthState, type ServiceHealthResult } from "./serviceHealth";

/**
 * WorkspaceTopbar：原型图顶栏——面包屑 + "服务正常 · HH:MM 更新" + 搜索/通知/帮助。
 *
 * 服务状态**必须接 `/api/health` 真实结果**，不得写死"服务正常"：
 * - 组件挂载与每次轮询都真实发起请求，用 resolveServiceHealthState() 判定三态
 *   （见 serviceHealth.ts 顶部注释：healthy / unhealthy / unknown 三态而非二态）；
 * - 异常时如实显示"服务异常"或"服务状态未知"，不吞掉失败伪装成"正常"。
 *
 * 「搜索」在 WP2-C 正式接通：由 GlobalMaterialSearch 自带按钮 + 命令面板 +
 * Ctrl/Cmd+K 快捷键；「通知」「帮助」继续保持占位（本轮范围外）。
 *
 * 面包屑标签由 `nav.ts` 的 `resolveNavLabel` 统一给出：它同时覆盖
 * 一级导航项与**已退出一级导航但路由仍在**的兼容入口（如 `/history`），
 * 因此这里不会再出现裸路径字符串。
 */
const POLL_INTERVAL_MS = 30_000;

function formatUpdatedAt(date: Date): string {
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function resolveGroupLabel(pathname: string): string | null {
  if (pathname.startsWith("/supervision")) return "主管端";
  const navItem = findNavItemByPathname(pathname);
  return navItem ? NAV_GROUP_LABELS[navItem.group] : null;
}

export function WorkspaceTopbar({ compact = false, sidebarCollapsed = false, onToggleSidebar, leading }: {
  compact?: boolean;
  sidebarCollapsed?: boolean;
  onToggleSidebar?: () => void;
  /** 页面自带的标题内容（如复核详情的文件名），排在页面名称之后。 */
  leading?: ReactNode;
}) {
  const pathname = usePathname() ?? "";
  const searchParams = useSearchParams();
  const supervisorMode = pathname.startsWith("/supervision");
  const supervisorSearch = new URLSearchParams();
  for (const key of ["scope_id", "fiscal_year", "report_kind", "task_id", "reporting_year", "jurisdiction_id", "component", "department_id", "subject_id", "status", "keyword", "legacy"]) {
    const value = searchParams.get(key);
    if (value) supervisorSearch.set(key, value);
  }
  supervisorSearch.set("mode", "materials");
  const [health, setHealth] = useState<ServiceHealthResult>({ state: "unknown", label: "服务状态未知" });
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function pollHealth() {
      try {
        const response = await fetch("/api/health", { cache: "no-store" });
        const payload = await response.json().catch(() => null);
        if (!cancelled) {
          setHealth(resolveServiceHealthState({ ok: response.ok, status: response.status }, payload));
          setUpdatedAt(new Date());
        }
      } catch {
        if (!cancelled) {
          setHealth(resolveServiceHealthState(null, null));
          setUpdatedAt(new Date());
        }
      }
    }

    void pollHealth();
    const timer = window.setInterval(() => void pollHealth(), POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  const groupLabel = resolveGroupLabel(pathname);
  const pageLabel = resolveNavLabel(pathname);
  const statusDotClass =
    health.state === "healthy"
      ? "bg-success-600"
      : health.state === "unhealthy"
        ? "bg-danger-600"
        : "bg-slate-400";

  return (
    <header className={`flex shrink-0 items-center justify-between gap-3 border-b border-border bg-white ${compact ? "h-12 px-3" : "h-16 px-6"}`}>
      <div className="flex min-w-0 items-center gap-2">
        {onToggleSidebar ? (
          <button
            type="button"
            onClick={onToggleSidebar}
            aria-label={sidebarCollapsed ? "展开导航" : "收起导航"}
            aria-expanded={!sidebarCollapsed}
            data-testid="gbc-workspace-sidebar-toggle"
            className="rounded-md p-2 text-slate-500 hover:bg-surface-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          >
            {sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          </button>
        ) : null}
        <div className="min-w-0">
        <div className={compact ? "sr-only" : "text-sm text-slate-400"} data-testid="gbc-workspace-breadcrumb">
          {groupLabel ? `${groupLabel} / ` : ""}
          {pageLabel}
        </div>
        {leading ? null : <div className="truncate text-base font-semibold text-slate-900">{pageLabel}</div>}
        </div>
        {leading ? (
          <>
            <span className="shrink-0 whitespace-nowrap text-sm text-slate-400" aria-hidden="true">{pageLabel} /</span>
            <div className="flex min-w-0 items-center gap-2">{leading}</div>
          </>
        ) : null}
      </div>

      <div className={`flex shrink-0 items-center ${compact ? "gap-2" : "gap-4"}`}>
        <div
          className="flex items-center gap-2 whitespace-nowrap text-sm text-slate-600"
          data-testid="gbc-workspace-service-status"
          data-service-state={health.state}
          title={updatedAt ? `${formatUpdatedAt(updatedAt)} 更新` : undefined}
        >
          <span className={`h-2 w-2 rounded-full ${statusDotClass}`} aria-hidden="true" />
          <span>{health.label}</span>
          {/* 顶栏嵌在半宽栏里时把位置留给页面标题，更新时间改为悬停提示。 */}
          {updatedAt && !leading ? <span className={compact ? "hidden text-slate-400 lg:inline" : "text-slate-400"}>· {formatUpdatedAt(updatedAt)} 更新</span> : null}
        </div>

        <div className={`flex items-center gap-1 border-l border-border ${compact ? "pl-2" : "pl-4"}`}>
          {supervisorMode ? <Link className="whitespace-nowrap rounded-md p-2 text-sm text-primary-700" href={`/supervision/materials?${supervisorSearch}` as Route}>查找材料</Link> : <GlobalMaterialSearch />}
          {!supervisorMode ? <>
          <button
            type="button"
            aria-label="通知"
            data-testid="gbc-workspace-action-notification"
            className={`${compact ? "hidden md:block " : ""}whitespace-nowrap rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400`}
          >
            通知
          </button>
          <button
            type="button"
            aria-label="帮助"
            data-testid="gbc-workspace-action-help"
            className={`${compact ? "hidden md:block " : ""}whitespace-nowrap rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400`}
          >
            帮助
          </button>
          </> : null}
        </div>
      </div>
    </header>
  );
}

export default WorkspaceTopbar;
