"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { NAV_GROUP_LABELS, NAV_ITEMS } from "./nav";
import { resolveServiceHealthState, type ServiceHealthResult } from "./serviceHealth";

/**
 * WorkspaceTopbar：原型图顶栏——面包屑 + "服务正常 · HH:MM 更新" + 搜索/通知/帮助。
 *
 * 服务状态**必须接 `/api/health` 真实结果**，不得写死"服务正常"：
 * - 组件挂载与每次轮询都真实发起请求，用 resolveServiceHealthState() 判定三态
 *   （见 serviceHealth.ts 顶部注释：healthy / unhealthy / unknown 三态而非二态）；
 * - 异常时如实显示"服务异常"或"服务状态未知"，不吞掉失败伪装成"正常"。
 *
 * 搜索/通知/帮助三个按钮本批只做占位（无具体交互逻辑），
 * 具体功能超出 Task 2"只搬骨架，不迁移业务逻辑"的范围。
 */
const POLL_INTERVAL_MS = 30_000;

function formatUpdatedAt(date: Date): string {
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

function resolveBreadcrumbLabel(pathname: string): string {
  const navItem = NAV_ITEMS.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
  return navItem?.label ?? pathname;
}

function resolveGroupLabel(pathname: string): string | null {
  const navItem = NAV_ITEMS.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
  return navItem ? NAV_GROUP_LABELS[navItem.group] : null;
}

export function WorkspaceTopbar() {
  const pathname = usePathname() ?? "";
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
  const pageLabel = resolveBreadcrumbLabel(pathname);
  const statusDotClass =
    health.state === "healthy"
      ? "bg-success-600"
      : health.state === "unhealthy"
        ? "bg-danger-600"
        : "bg-slate-400";

  return (
    <header className="flex h-16 shrink-0 items-center justify-between border-b border-border bg-white px-6">
      <div>
        <div className="text-sm text-slate-400" data-testid="gbc-workspace-breadcrumb">
          {groupLabel ? `${groupLabel} / ` : ""}
          {pageLabel}
        </div>
        <div className="text-base font-semibold text-slate-900">{pageLabel}</div>
      </div>

      <div className="flex items-center gap-4">
        <div
          className="flex items-center gap-2 text-sm text-slate-600"
          data-testid="gbc-workspace-service-status"
          data-service-state={health.state}
        >
          <span className={`h-2 w-2 rounded-full ${statusDotClass}`} aria-hidden="true" />
          <span>{health.label}</span>
          {updatedAt ? <span className="text-slate-400">· {formatUpdatedAt(updatedAt)} 更新</span> : null}
        </div>

        <div className="flex items-center gap-1 border-l border-border pl-4">
          <button
            type="button"
            aria-label="搜索"
            data-testid="gbc-workspace-action-search"
            className="rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          >
            搜索
          </button>
          <button
            type="button"
            aria-label="通知"
            data-testid="gbc-workspace-action-notification"
            className="rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          >
            通知
          </button>
          <button
            type="button"
            aria-label="帮助"
            data-testid="gbc-workspace-action-help"
            className="rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
          >
            帮助
          </button>
        </div>
      </div>
    </header>
  );
}

export default WorkspaceTopbar;
