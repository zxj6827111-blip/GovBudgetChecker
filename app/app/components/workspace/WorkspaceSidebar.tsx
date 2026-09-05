"use client";

import type { Route } from "next";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import type { JobSummaryRecord } from "@/lib/uiAdapters";
import { cn } from "@/lib/utils";

import { computeNavBadgeCounts } from "./navBadges";
import { NAV_GROUP_LABELS, NAV_ITEMS, type NavGroupId } from "./nav";

/**
 * WorkspaceSidebar：原型图左侧双分组竖向导航（工作区 5 项 + 管理 3 项）+ logo。
 *
 * 角标数字来自真实的 `/api/jobs` 全量拉取后在客户端计算（复用既有
 * `normalizeUiTaskStatus` 归一口径，与 gbc-ui-demo 的处理队列计数同源）。
 * 请求中或请求失败时角标不渲染（undefined），不得显示 0——
 * 0 意味着"已确认此刻数量为零"，请求失败时我们并不知道真实数量。
 *
 * 「管理」组三项在非管理员登录时整组不渲染（isAdmin=false 时被过滤），
 * 不是"渲染但置灰"，避免非管理员看到功能入口后误以为可以点击。
 *
 * `data-testid` 命名规范（供 e2e 使用，Task 2 起统一约定）：
 * - `gbc-workspace-nav-${item.id}`：每个导航项本体；
 * - `gbc-workspace-nav-badge-${item.id}`：导航项角标（仅当角标存在时渲染）；
 * - `gbc-workspace-logo-home`：logo/首页快捷入口。
 */
export interface WorkspaceSidebarProps {
  isAdmin: boolean;
}

export function WorkspaceSidebar({ isAdmin }: WorkspaceSidebarProps) {
  const pathname = usePathname();
  const [badgeCounts, setBadgeCounts] = useState<ReturnType<typeof computeNavBadgeCounts>>({});

  useEffect(() => {
    let cancelled = false;

    async function loadBadgeCounts() {
      try {
        const response = await fetch("/api/jobs", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as JobSummaryRecord[] | { items?: JobSummaryRecord[] };
        const jobs = Array.isArray(payload) ? payload : payload.items ?? [];
        if (!cancelled) {
          setBadgeCounts(computeNavBadgeCounts(jobs));
        }
      } catch {
        // 网络异常时保持角标为空（不渲染），不猜测数字。
      }
    }

    void loadBadgeCounts();
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  const visibleItems = NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin);
  const groups: NavGroupId[] = ["workspace", "admin"];

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-16 items-center gap-2 border-b border-border px-5">
        <Link href={"/workbench" as Route} data-testid="gbc-workspace-logo-home" className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded bg-brand-900 text-sm font-bold text-white">
            GBC
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-slate-900">GovBudgetChecker</div>
            <div className="truncate text-[11px] text-slate-400">预算审核与质量控制平台</div>
          </div>
        </Link>
      </div>

      <nav className="flex-1 overflow-y-auto py-4">
        {groups.map((group) => {
          const groupItems = visibleItems.filter((item) => item.group === group);
          if (groupItems.length === 0) {
            return null;
          }
          return (
            <div key={group} className="mb-4">
              <div className="px-5 pb-2 text-xs font-semibold uppercase tracking-wider text-slate-400">
                {NAV_GROUP_LABELS[group]}
              </div>
              <ul>
                {groupItems.map((item) => {
                  const isActive = pathname === item.href || pathname?.startsWith(`${item.href}/`);
                  const badgeValue = item.badgeKey ? badgeCounts[item.badgeKey] : undefined;
                  const Icon = item.icon;
                  return (
                    <li key={item.id}>
                      <Link
                        href={item.href as Route}
                        data-testid={`gbc-workspace-nav-${item.id}`}
                        aria-current={isActive ? "page" : undefined}
                        className={cn(
                          "mx-2 flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                          isActive
                            ? "bg-primary-100 text-primary-700"
                            : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                        )}
                      >
                        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate">{item.label}</span>
                        {typeof badgeValue === "number" ? (
                          <span
                            data-testid={`gbc-workspace-nav-badge-${item.id}`}
                            className={cn(
                              "shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold",
                              isActive ? "bg-primary-200 text-primary-800" : "bg-slate-100 text-slate-500",
                            )}
                          >
                            {badgeValue}
                          </span>
                        ) : null}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </nav>
    </div>
  );
}

export default WorkspaceSidebar;
