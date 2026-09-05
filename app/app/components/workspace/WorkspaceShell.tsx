"use client";

import { useEffect, useState, type ReactNode } from "react";

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

  useEffect(() => {
    let cancelled = false;

    async function loadUser() {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (!response.ok) {
          return;
        }
        const payload = (await response.json()) as {
          user?: { username?: string; is_admin?: boolean } | null;
        };
        if (!cancelled && payload.user?.username) {
          setUser({
            username: payload.user.username,
            isAdmin: Boolean(payload.user.is_admin),
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

  return (
    <div className="flex h-screen overflow-hidden bg-surface-50">
      <div className="flex h-full w-[240px] shrink-0 flex-col border-r border-border bg-white">
        <div className="min-h-0 flex-1 overflow-hidden">
          <WorkspaceSidebar isAdmin={Boolean(user?.isAdmin)} />
        </div>
        <WorkspaceUserCard user={user} />
      </div>
      <div className="flex flex-1 flex-col overflow-hidden">
        <WorkspaceTopbar />
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}

export default WorkspaceShell;
