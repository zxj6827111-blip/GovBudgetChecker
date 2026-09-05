"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * AdminOnlyGuard：客户端管理员门控，「管理」组三个页面（质量管理/规则与版本/
 * 系统设置）用它包裹页面内容。
 *
 * 为什么是客户端而不是 Server Component 门控：
 * 本仓库的鉴权在浏览器端与服务端有两套并存的既有模式（详见
 * app/lib/adminAccess.ts 的服务端模式 vs app/app/components/Header.tsx 的
 * 客户端模式）。Server Component 门控需要在 Node 进程里直接请求真实后端
 * `/api/auth/me`，但本仓库的 e2e 测试从未起真实后端进程，只用 Playwright
 * 的 `page.route()` 拦截**浏览器发出的**请求（同源模式与现有全部 admin
 * e2e 用例一致，见 e2e/tests/admin-system-management.spec.ts）——
 * `page.route()` 无法拦截 Server Component 在服务端发出的 fetch。
 * 因此「非管理员访问被拒」这条要求若用 Server Component 实现将无法在现有
 * e2e 基建下验证，故改为客户端门控，与 Header.tsx 现有的客户端鉴权模式
 * 保持一致，也让 e2e 可以复用已验证的 mock 方式。
 *
 * 反例（Task 2 权限测试要求）：
 * - 未拿到会话或 is_admin=false -> 重定向到 /workbench，不渲染 children；
 * - 加载中（尚未确认身份）-> 显示"正在校验访问权限…"过渡态，
 *   不能在校验完成前就先渲染出管理页面内容再补一个"没权限"提示——
 *   那样会让非管理员看到一闪而过的真实内容。
 */
export function AdminOnlyGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [state, setState] = useState<"checking" | "allowed" | "denied">("checking");

  useEffect(() => {
    let cancelled = false;

    async function checkAccess() {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) {
            setState("denied");
          }
          return;
        }
        const payload = (await response.json()) as { user?: { is_admin?: boolean } | null };
        if (!cancelled) {
          setState(payload.user?.is_admin ? "allowed" : "denied");
        }
      } catch {
        if (!cancelled) {
          setState("denied");
        }
      }
    }

    void checkAccess();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (state === "denied") {
      router.replace("/workbench");
    }
  }, [state, router]);

  if (state === "allowed") {
    return <>{children}</>;
  }

  return (
    <div className="p-8 text-sm text-slate-500" data-testid="gbc-workspace-admin-guard-pending">
      正在校验访问权限…
    </div>
  );
}

export default AdminOnlyGuard;
