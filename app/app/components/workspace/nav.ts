import {
  Archive,
  ClipboardCheck,
  FileUp,
  GitBranch,
  LayoutDashboard,
  Library,
  ListTodo,
  Settings,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

/**
 * 导航结构定义（唯一权威来源）。
 *
 * 当前形态（WP2-C 收口后）：**9 项一级导航**
 * - 「工作区」组 6 项：工作台总览 / 上传中心 / 处理队列 / 审核工作台 /
 *   材料台账 / 导出归档；
 * - 「管理」组 3 项：质量管理 / 规则与版本 / 系统设置。
 *
 * 两次结构调整的来龙去脉（都是显式决定，不是顺手挪动）：
 *
 * 1. WP2-A 曾把「材料台账」临时加在**工作区组末尾**，理由是"新增能力不该移动
 *    既有项，避免既有 e2e 的相对顺序断言失效"。WP2-C 按最终信息架构把它挪到
 *    「审核工作台」之后 —— 业务动线是"审核 → 台账 → 归档"，临时位置不再是理由。
 * 2. 「任务历史」**退出一级导航**（§四十九），但 `/history` 路由**保留**：
 *    库里还有没有精确关联字段的 legacy 任务，全局材料搜索明确不猜它们属于哪个
 *    槽位（§二十四），此时删掉或重定向 `/history` 会丢失这部分运维查询能力。
 *    它现在的定位是"兼容 / 运维入口"，因此需要 `LEGACY_ROUTE_LABELS`
 *    给它一个正式标题 —— 顶栏不允许显示裸路径（§五十四）。
 *
 * 角标（badgeKey）：
 * - "analyzing" -> 处理队列角标 = 当前状态为分析中的任务数；
 * - "review_required" -> 审核工作台角标 = 当前待人工复核的任务数；
 * - 其余项无角标（undefined），对应"无数据显示空而非 0"。
 *
 * 「管理」组三项 adminOnly=true，对普通审核员不可见（WorkspaceSidebar 据此过滤），
 * 判定逻辑复用既有 `/api/auth/me` 的 `is_admin` 字段。
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
  // 「材料台账」（WP2-A 引入，WP2-C 定位于审核工作台之后）：
  // 按地区/主管部门/主体看"应收材料到没到、处理到哪一步"。
  { id: "materials", label: "材料台账", href: "/materials", icon: Library, group: "workspace", adminOnly: false },
  { id: "archive", label: "导出归档", href: "/archive", icon: Archive, group: "workspace", adminOnly: false },
  { id: "quality", label: "质量管理", href: "/quality", icon: ShieldCheck, group: "admin", adminOnly: true },
  { id: "rules", label: "规则与版本", href: "/rules", icon: GitBranch, group: "admin", adminOnly: true },
  { id: "settings", label: "系统设置", href: "/settings", icon: Settings, group: "admin", adminOnly: true },
];

export const NAV_GROUP_LABELS: Record<NavGroupId, string> = {
  workspace: "工作区",
  admin: "管理",
};

/**
 * 已退出一级导航、但路由仍然可达的兼容入口标题。
 *
 * 存在的理由：顶栏面包屑与页面标题都从导航定义取名，某项一旦不在 `NAV_ITEMS`
 * 里，标题就会退化成裸路径（`/history`）。**不能**为了显示标题把它塞回
 * `NAV_ITEMS` —— 那等于偷偷恢复一级入口。
 */
export const LEGACY_ROUTE_LABELS: Record<string, string> = {
  "/history": "任务运行历史（兼容）",
};

/** 供中间件/路由守卫判断某路径是否属于「管理」分组（用于非管理员访问拒绝）。 */
export function findNavItemByPathname(pathname: string): NavItem | undefined {
  return NAV_ITEMS.find((item) => pathname === item.href || pathname.startsWith(`${item.href}/`));
}

export function isAdminOnlyPathname(pathname: string): boolean {
  return findNavItemByPathname(pathname)?.adminOnly ?? false;
}

/**
 * 路径 → 显示标题。一级导航项优先，其次是兼容入口的正式标题；
 * 都不匹配时**原样返回路径**（未知路由不该被编一个名字）。
 */
export function resolveNavLabel(pathname: string): string {
  const navItem = findNavItemByPathname(pathname);
  if (navItem) {
    return navItem.label;
  }
  for (const [prefix, label] of Object.entries(LEGACY_ROUTE_LABELS)) {
    if (pathname === prefix || pathname.startsWith(`${prefix}/`)) {
      return label;
    }
  }
  return pathname;
}

/** 该路径是否是"兼容 / 运维"入口（用于页面上的显式提示）。 */
export function isLegacyRoutePathname(pathname: string): boolean {
  return Object.keys(LEGACY_ROUTE_LABELS).some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`),
  );
}
