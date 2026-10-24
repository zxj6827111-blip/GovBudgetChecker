"use client";

import type { Route } from "next";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { ArrowLeft, LayoutDashboard } from "lucide-react";

import { jobCountsRequest, reviewWorklistRequest } from "@/lib/listRequests";
import { subscribeJobCounts } from "@/lib/jobListEvents";
import type { JobCountsView } from "@/lib/jobListViews";
import { REVIEW_WORKLIST_INVALIDATED, REVIEW_WORKLIST_UPDATED } from "@/lib/reviewWorklistEvents";
import { cn } from "@/lib/utils";

import { navBadgeCountsFromJobCounts, type NavBadgeCounts } from "./navBadges";
import { NAV_GROUP_LABELS, NAV_ITEMS, SUPERVISOR_NAV_ITEMS, type NavGroupId } from "./nav";

/**
 * WorkspaceSidebar：原型图左侧双分组竖向导航（工作区 5 项 + 管理 3 项）+ logo。
 *
 * 处理角标来自 `/api/jobs?view=counts`（只有计数，不下载任务摘要），复核角标来自
 * `/api/reviews/worklist` 的当前材料待办。
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
  collapsed?: boolean;
  isReadOnly?: boolean;
}

export function WorkspaceSidebar({ isAdmin, collapsed = false, isReadOnly = false }: WorkspaceSidebarProps) {
  const pathname = usePathname();
  const search = useSearchParams();
  const supervisorMode = isReadOnly || Boolean(pathname?.startsWith("/supervision"));
  const supervisorQuery = new URLSearchParams();
  for (const key of ["scope_id", "fiscal_year", "report_kind", "task_id", "reporting_year", "jurisdiction_id", "component", "department_id", "subject_id", "status", "keyword", "legacy"]) { const value = search.get(key); if (value) supervisorQuery.set(key, value); }
  const [badgeCounts, setBadgeCounts] = useState<NavBadgeCounts>({});

  useEffect(() => {
    if (supervisorMode) return;
    let cancelled = false;
    let snapshotVersion = 0;
    let reviewVersion = 0;
    async function loadReviewCount() {
      const requestVersion = ++reviewVersion;
      try {
        const data = await reviewWorklistRequest.load();
        if (!cancelled && requestVersion === reviewVersion) setBadgeCounts(previous => ({ ...previous, review_required: data.pending_total }));
      } catch {
        if (!cancelled && requestVersion === reviewVersion) setBadgeCounts(previous => ({ ...previous, review_required: undefined }));
      }
    }
    // 任务状态分布变化（有任务跑完或新开始）时顺带刷新复核待办数。
    let jobStatusSnapshot: string | null = null;
    const applyJobCounts = (counts: JobCountsView) => {
      setBadgeCounts(previous => ({ ...previous, ...navBadgeCountsFromJobCounts(counts) }));
      const signature = JSON.stringify(counts.status_counts);
      const changed = jobStatusSnapshot !== null && signature !== jobStatusSnapshot;
      jobStatusSnapshot = signature;
      return changed;
    };
    const unsubscribe = subscribeJobCounts((counts) => {
      snapshotVersion += 1;
      if (applyJobCounts(counts)) void loadReviewCount();
    });

    async function loadBadgeCounts() {
      const requestVersion = snapshotVersion;
      try {
        const counts = await jobCountsRequest.load();
        if (!cancelled && requestVersion === snapshotVersion) {
          applyJobCounts(counts);
        }
      } catch {
        // 网络异常时保持角标为空（不渲染），不猜测数字。
      }
    }

    void loadBadgeCounts();
    void loadReviewCount();
    const refreshReview = () => void loadReviewCount();
    // 计数接口只返回几个数字，可以和复核待办一起定时刷新。
    const refreshAll = () => { void loadBadgeCounts(); void loadReviewCount(); };
    const shareReviewCount = (event: Event) => {
      const count = (event as CustomEvent<unknown>).detail;
      reviewVersion += 1;
      setBadgeCounts(previous => ({ ...previous, review_required: typeof count === "number" ? count : undefined }));
    };
    window.addEventListener(REVIEW_WORKLIST_INVALIDATED, refreshReview);
    window.addEventListener(REVIEW_WORKLIST_UPDATED, shareReviewCount);
    const timer = window.setInterval(refreshAll, 30_000);
    window.addEventListener("focus", refreshAll);
    return () => {
      cancelled = true;
      unsubscribe();
      window.removeEventListener(REVIEW_WORKLIST_INVALIDATED, refreshReview);
      window.removeEventListener(REVIEW_WORKLIST_UPDATED, shareReviewCount);
      window.removeEventListener("focus", refreshAll);
      window.clearInterval(timer);
    };
  }, [supervisorMode]);

  const visibleItems = (supervisorMode ? SUPERVISOR_NAV_ITEMS : NAV_ITEMS).filter((item) => !item.adminOnly || isAdmin);
  const groups: NavGroupId[] = ["workspace", "admin"];

  return (
    <div className="flex h-full flex-col">
      <div className={cn("flex items-center gap-2 border-b border-border", collapsed ? "h-12 justify-center px-2" : "h-16 px-5")}>
        <Link href={(supervisorMode ? "/supervision" : "/workbench") as Route} data-testid="gbc-workspace-logo-home" className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded bg-brand-900 text-sm font-bold text-white">
            GBC
          </div>
          <div className={collapsed ? "sr-only" : "min-w-0"}>
            <div className="truncate text-sm font-semibold text-slate-900">GovBudgetChecker</div>
            <div className="truncate text-[11px] text-slate-400">预算审核与质量控制平台</div>
          </div>
        </Link>
      </div>

      {!supervisorMode ? (
        <Link
          href="/supervision"
          data-testid="gbc-supervisor-entry"
          title="主管驾驶舱"
          className={cn(
            "mx-2 mt-3 flex items-center gap-2 rounded-md border border-primary-200 bg-primary-50 px-3 py-2.5 text-sm font-medium text-primary-700 hover:bg-primary-100",
            collapsed && "justify-center px-2",
          )}
        >
          <LayoutDashboard className="h-4 w-4 shrink-0" />
          <span className={collapsed ? "sr-only" : "truncate"}>主管驾驶舱</span>
        </Link>
      ) : null}

      <nav className="flex-1 overflow-y-auto py-4">
        {groups.map((group) => {
          const groupItems = visibleItems.filter((item) => item.group === group);
          if (groupItems.length === 0) {
            return null;
          }
          return (
            <div key={group} className="mb-4">
              <div className={collapsed ? "sr-only" : "px-5 pb-2 text-xs font-semibold uppercase tracking-wider text-slate-400"}>
                {supervisorMode ? "主管工作区" : NAV_GROUP_LABELS[group]}
              </div>
              <ul>
                {groupItems.map((item) => {
                  const isActive = item.href === "/supervision" ? pathname === item.href : pathname === item.href || pathname?.startsWith(`${item.href}/`);
                  const badgeValue = item.badgeKey ? badgeCounts[item.badgeKey] : undefined;
                  const Icon = item.icon;
                  return (
                    <li key={item.id}>
                      <Link
                        href={(supervisorMode && supervisorQuery.size ? `${item.href}?${supervisorQuery}` : item.href) as Route}
                        data-testid={`gbc-workspace-nav-${item.id}`}
                        aria-current={isActive ? "page" : undefined}
                        aria-label={item.label}
                        title={item.id === "queue" ? "角标表示正在处理的分析运行数；列表保留历史记录" : item.id === "review" ? "角标表示当前材料未完成复核的数量" : collapsed ? item.label : undefined}
                        className={cn(
                          "relative mx-2 flex items-center rounded-md text-sm font-medium transition-colors",
                          collapsed ? "h-11 justify-center px-2" : "gap-3 px-3 py-2",
                          isActive
                            ? "bg-primary-100 text-primary-700"
                            : "text-slate-600 hover:bg-slate-50 hover:text-slate-900",
                        )}
                      >
                        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                        <span className={collapsed ? "sr-only" : "min-w-0 flex-1 truncate"}>{item.label}</span>
                        {typeof badgeValue === "number" ? (
                          <span
                            data-testid={`gbc-workspace-nav-badge-${item.id}`}
                            aria-label={item.id === "queue" ? `正在处理 ${badgeValue} 次分析` : `待办 ${badgeValue} 份材料`}
                            className={cn(
                              "shrink-0 rounded-full font-semibold",
                              collapsed ? "absolute right-0 top-0 px-1 text-[10px]" : "px-2 py-0.5 text-xs",
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
      {supervisorMode && !isReadOnly ? <Link href="/workbench" title="返回工作人员工作台" className="flex items-center justify-center border-t border-border p-4 text-xs text-slate-500"><ArrowLeft className="h-4 w-4 shrink-0" /><span className={collapsed ? "sr-only" : "ml-2"}>返回工作人员工作台</span></Link> : null}
    </div>
  );
}

export default WorkspaceSidebar;
