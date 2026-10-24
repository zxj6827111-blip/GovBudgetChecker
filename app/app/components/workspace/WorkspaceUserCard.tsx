"use client";

import { useState } from "react";
import { LogOut } from "lucide-react";

/**
 * WorkspaceUserCard：原型图侧栏底部用户卡（"张审核员 · 业务审核组 · 上海"）。
 *
 * 接真实 `/api/auth/me`：
 * - username 直接显示后端返回的用户名；
 * - is_admin=true 时显示"管理员"角色标签，否则显示"审核员"
 *   （本轮无更细粒度的角色/部门/地区字段可用，不编造原型图里的"业务审核组·上海"
 *   这类具体部门文案——那是设计稿示例，真实值只有 username 与 is_admin 两个字段，
 *   角色文案据此二值推导，其余字段留空而非填假数据）。
 */
export interface WorkspaceUser {
  username: string;
  isAdmin: boolean;
  isReadOnly?: boolean;
}

export interface WorkspaceUserCardProps {
  user: WorkspaceUser | null;
  collapsed?: boolean;
}

export function WorkspaceUserCard({ user, collapsed = false }: WorkspaceUserCardProps) {
  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutFailed, setLogoutFailed] = useState(false);
  async function logout() {
    setLoggingOut(true); setLogoutFailed(false);
    try {
      const response = await fetch("/api/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("logout failed");
      window.location.assign("/login");
    } catch { setLogoutFailed(true); setLoggingOut(false); }
  }
  if (!user) {
    return (
      <div className="border-t border-border p-3 text-xs text-slate-400" data-testid="gbc-workspace-user-card">
        未登录
      </div>
    );
  }

  return (
    <div
      className={`flex items-center border-t border-border ${collapsed ? "flex-col justify-center gap-2 p-3" : "gap-3 p-4"}`}
      data-testid="gbc-workspace-user-card"
      title={collapsed ? `${user.username} · ${user.isReadOnly ? "主管只读" : user.isAdmin ? "管理员" : "审核员"}` : undefined}
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary-100 text-sm font-semibold text-primary-700">
        {user.username.slice(0, 1).toUpperCase()}
      </div>
      <div className={collapsed ? "sr-only" : "min-w-0"}>
        <div className="truncate text-sm font-medium text-slate-900">{user.username}</div>
        <div className="truncate text-xs text-slate-400">{user.isReadOnly ? "主管只读" : user.isAdmin ? "管理员" : "审核员"}</div>
        {logoutFailed ? <span role="alert" className="text-xs text-danger-600">退出失败，请重试</span> : null}
      </div>
      {user.isReadOnly ? <button aria-label="退出登录" title="退出登录" disabled={loggingOut} onClick={logout} className="ml-auto rounded-md p-1 text-slate-500 hover:bg-surface-100 disabled:opacity-50"><LogOut className="h-4 w-4" /></button> : null}
    </div>
  );
}

export default WorkspaceUserCard;
