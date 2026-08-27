"use client";

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
}

export interface WorkspaceUserCardProps {
  user: WorkspaceUser | null;
}

export function WorkspaceUserCard({ user }: WorkspaceUserCardProps) {
  if (!user) {
    return (
      <div className="border-t border-border p-4 text-xs text-slate-400" data-testid="gbc-workspace-user-card">
        未登录
      </div>
    );
  }

  return (
    <div className="flex items-center gap-3 border-t border-border p-4" data-testid="gbc-workspace-user-card">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary-100 text-sm font-semibold text-primary-700">
        {user.username.slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0">
        <div className="truncate text-sm font-medium text-slate-900">{user.username}</div>
        <div className="truncate text-xs text-slate-400">{user.isAdmin ? "管理员" : "审核员"}</div>
      </div>
    </div>
  );
}

export default WorkspaceUserCard;
