"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { usePathname, useSearchParams, useRouter } from "next/navigation";

import { EmbeddedTopbarContext } from "./EmbeddedWorkspaceTopbar";
import { WorkspaceSidebar } from "./WorkspaceSidebar";
import { WorkspaceTopbar } from "./WorkspaceTopbar";
import { WorkspaceUserCard, type WorkspaceUser } from "./WorkspaceUserCard";

/**
 * WorkspaceShell：Task 2 的应用骨架容器，组装侧栏导航 + 顶栏 + 底部用户卡 + 主体区域。
 *
 * 权限判定复用既有客户端模式（与 app/app/components/Header.tsx 现有逻辑一致）：
 * 请求 `/api/auth/me`，取 `user.is_admin` 作为「管理」组导航是否可见的依据。
 * 未拿到会话或请求失败时按非管理员处理（isAdmin=false），
 * 这是保守的失败态——宁可少显示管理入口，不可让未确认身份的用户看到管理入口。
 */
export function WorkspaceShell({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<WorkspaceUser | null>(null);
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const router = useRouter();
  const isReviewSection = pathname === "/review" || Boolean(pathname?.startsWith("/review/"));
  // 清单保留正常导航与滚动；只有单份 PDF 详情采用专注布局。
  const isReviewPage = isReviewSection && Boolean(searchParams.get("job"));
  const [narrowScreen, setNarrowScreen] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 1023px)");
    const sync = () => setNarrowScreen(media.matches);
    sync(); media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);
  const [reviewSidebarExpanded, setReviewSidebarExpanded] = useState(false);
  // 部门目录在窄窗口也需要留出正文空间，复用现有可展开的图标导航。
  const focusNavigation = isReviewPage || ((isReviewSection || pathname === "/reports" || Boolean(pathname?.startsWith("/reports/")) || Boolean(pathname?.startsWith("/supervision"))) && narrowScreen);
  const collapsed = focusNavigation && !reviewSidebarExpanded;
  // 单份 PDF 复核页让原文栏从窗口顶端开始：顶栏交给页面画在右栏，这里不再画全宽顶栏。
  const embeddedTopbar = useMemo(
    () => isReviewPage ? { sidebarCollapsed: collapsed, onToggleSidebar: () => setReviewSidebarExpanded((value) => !value) } : null,
    [isReviewPage, collapsed],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadUser() {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as {
          user?: { username?: string; is_admin?: boolean; is_read_only?: boolean } | null;
        };
        if (!cancelled && payload.user?.username) {
          setUser({
            username: payload.user.username,
            isAdmin: Boolean(payload.user.is_admin),
            isReadOnly: Boolean(payload.user.is_read_only),
          });
        }
      } catch {
        // 保持 user=null（非管理员、未登录展示态），不猜测身份。
      }
    }

    void loadUser();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (user?.isReadOnly && !pathname?.startsWith("/supervision")) router.replace("/supervision");
  }, [user, pathname, router]);

  return (
    <div className="flex h-screen overflow-hidden bg-surface-50">
      <div
        className={`flex h-full shrink-0 flex-col border-r border-border bg-white ${collapsed ? "w-16" : "w-[240px]"}`}
        data-testid="gbc-workspace-sidebar"
        data-collapsed={collapsed}
      >
        <div className="min-h-0 flex-1 overflow-hidden">
          <WorkspaceSidebar isAdmin={Boolean(user?.isAdmin)} isReadOnly={Boolean(user?.isReadOnly)} collapsed={collapsed} />
        </div>
        <WorkspaceUserCard user={user} collapsed={collapsed} />
      </div>
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {embeddedTopbar ? null : (
          <WorkspaceTopbar
            compact={focusNavigation}
            sidebarCollapsed={collapsed}
            onToggleSidebar={focusNavigation ? () => setReviewSidebarExpanded((value) => !value) : undefined}
          />
        )}
        <main className={`min-h-0 flex-1 ${isReviewPage ? "overflow-hidden" : "overflow-y-auto"}`}>
          <EmbeddedTopbarContext.Provider value={embeddedTopbar}>
            {user?.isReadOnly && !pathname?.startsWith("/supervision") ? <p className="p-6 text-sm text-slate-400">正在进入主管端…</p> : children}
          </EmbeddedTopbarContext.Provider>
        </main>
      </div>
    </div>
  );
}

export default WorkspaceShell;
