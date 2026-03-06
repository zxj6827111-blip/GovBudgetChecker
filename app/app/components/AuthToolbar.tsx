"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

type AuthUser = {
  username: string;
  is_admin: boolean;
};

const COLLAPSE_KEY = "gbc_auth_toolbar_collapsed";

export default function AuthToolbar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [busy, setBusy] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(COLLAPSE_KEY);
      setCollapsed(saved === null ? true : saved === "1");
    } catch {
      setCollapsed(true);
    }
  }, []);

  useEffect(() => {
    if (pathname === "/login") {
      setUser(null);
      return;
    }

    let cancelled = false;
    const fetchUser = async () => {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) {
            setUser(null);
          }
          return;
        }
        const payload = await response.json();
        if (!cancelled) {
          setUser((payload?.user ?? null) as AuthUser | null);
        }
      } catch {
        if (!cancelled) {
          setUser(null);
        }
      }
    };

    fetchUser();
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  if (pathname === "/login" || !user) {
    return null;
  }

  const setCollapsedWithPersist = (next: boolean) => {
    setCollapsed(next);
    try {
      window.localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
    } catch {
      // Ignore localStorage failures.
    }
  };

  const onLogout = async () => {
    if (busy) {
      return;
    }
    setBusy(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      router.replace("/login");
      router.refresh();
      setBusy(false);
    }
  };

  if (collapsed) {
    return (
      <div className="fixed left-4 top-4 z-[100] md:left-[14.75rem]">
        <button
          type="button"
          onClick={() => setCollapsedWithPersist(false)}
          title="展开账号工具"
          className="inline-flex items-center gap-2 rounded-full border border-slate-200 bg-white/90 px-3 py-1.5 text-xs text-slate-700 shadow-md backdrop-blur transition hover:bg-white"
        >
          <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-slate-900 text-[10px] font-semibold text-white">
            U
          </span>
          账号
        </button>
      </div>
    );
  }

  return (
    <div className="fixed left-4 top-4 z-[100] flex max-w-[calc(100vw-1rem)] items-center gap-2 rounded-full border border-slate-200 bg-white/90 px-3 py-1.5 shadow-md backdrop-blur md:left-[14.75rem] md:max-w-[calc(100vw-15.5rem)]">
      <button
        type="button"
        onClick={() => setCollapsedWithPersist(true)}
        title="隐藏账号工具"
        className="rounded-full border border-slate-300 px-2 py-1 text-[11px] text-slate-700"
      >
        收起
      </button>
      <span className="text-sm text-slate-700 whitespace-nowrap">{user.username}</span>
      <Link
        href="/account/password"
        className="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-700 whitespace-nowrap"
      >
        修改密码
      </Link>
      {user.is_admin ? (
        <Link
          href="/admin/users"
          className="rounded-full bg-slate-900 px-3 py-1 text-xs font-medium text-white whitespace-nowrap"
        >
          用户管理
        </Link>
      ) : null}
      <button
        onClick={onLogout}
        disabled={busy}
        className="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-700 disabled:opacity-60 whitespace-nowrap"
      >
        退出
      </button>
    </div>
  );
}
