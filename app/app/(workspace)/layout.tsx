import type { ReactNode } from "react";

import { WorkspaceShell } from "@/components/workspace/WorkspaceShell";

/**
 * `(workspace)` 路由组共享布局：承载 Task 2 的 8+1 项导航骨架。
 * 括号目录不出现在 URL 中（Next.js route group），因此
 * `app/app/(workspace)/workbench/page.tsx` 对应的真实路径是 `/workbench`。
 */
export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
