import {
  Archive,
  ClipboardCheck,
  FileUp,
  GitBranch,
  History,
  LayoutDashboard,
  ListTodo,
  Settings,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

/**
 * Task 2 导航结构定义（唯一权威来源）。
 *
 * 对照原型图（`01-workbench-overview.png` 左侧导航）：
 * - 「工作区」组 5 项：工作台总览 / 上传中心 / 处理队列 / 审核工作台 / 任务历史；
 * - 「管理」组 3 项：质量管理 / 规则与版本 / 系统设置；
 * - 第 9 项「导出归档」承接存量整改包能力（决策 2=a），原型图未画出这一项，
 *   是本次实施计划新增的入口，放在「工作区」组末尾（与"处理材料的完整闭环：
 *   上传→处理→审核→归档"的产品叙事一致）。
 *
 * 角标（badgeKey）：
 * - "analyzing" -> 处理队列角标 = 当前状态为分析中的任务数；
 * - "review_required" -> 审核工作台角标 = 当前待人工复核的任务数；
 * - 其余项无角标（undefined），对应"无数据显示空而非 0"——角标数字来自
 *   真实计数时才渲染，本身没有角标语义的导航项永远不渲染数字。
 *
 * 「管理」组三项 adminOnly=true，对普通审核员不可见（Header 组件据此过滤），
 * 判定逻辑复用既有 `/api/auth/me` 的 `is_admin` 字段（与 Header.tsx 现有模式一致）。
 */
export type NavGroupId = "workspace" | "admin";

export type NavBadgeKey = "analyzing" | "review_required";

export interface NavItem {
  id: string;
  label: string;
  href: `/${string}`;
  icon: LucideIcon;
  group: NavGroupId;
  adminOnly: boolean;
  badgeKey?: NavBadgeKey;
}

export const NAV_ITEMS: NavItem[] = [
  { id: "workbench", label: "工作台总览", href: "/workbench", icon: LayoutDashboard, group: "workspace", adminOnly: false },
  { id: "upload", label: "上传中心", href: "/upload", icon: FileUp, group: "workspace", adminOnly: false },
  {
    id: "queue",
    label: "处理队列",
    href: "/queue",
    icon: ListTodo,
    group: "workspace",
    adminOnly: false,
    badgeKey: "analyzing",
  },
  {
    id: "review",
    label: "审核工作台",
    href: "/review",
    icon: ClipboardCheck,
    group: "workspace",
    adminOnly: false,
    badgeKey: "review_required",
  },
  { id: "history", label: "任务历史", href: "/history", icon: History, group: "workspace", adminOnly: false },
  { id: "archive", label: "导出归档", href: "/archive", icon: Archive, group: "workspace", adminOnly: false },
  { id: "quality", label: "质量管理", href: "/quality", icon: ShieldCheck, group: "admin", adminOnly: true },
  { id: "rules", label: "规则与版本", href: "/rules", icon: GitBranch, group: "admin", adminOnly: true },
  { id: "settings", label: "系统设置", href: "/settings", icon: Settings, group: "admin", adminOnly: true },
];

export const NAV_GROUP_LABELS: Record<NavGroupId, string> = {
  workspace: "工作区",
  admin: "管理",
};

/** 供中间件/路由守卫判断某路径是否属于「管理」分组（用于非管理员访问拒绝）。 */
export function findNavItemByPathname(pathname: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
}

export function isAdminOnlyPathname(pathname: string): boolean {
  return findNavItemByPathname(pathname)?.adminOnly ?? false;
}
