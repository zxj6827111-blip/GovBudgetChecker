"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

type AuthUser = {
  username: string;
  is_admin: boolean;
};

export default function AuthToolbar() {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [busy, setBusy] = useState(false);

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

  return (
    <div className="fixed right-4 top-4 z-[100] flex items-center gap-2 rounded-full border border-slate-200 bg-white/90 px-3 py-1.5 shadow-md backdrop-blur">
      <span className="text-sm text-slate-700">{user.username}</span>
      <Link
        href="/account/password"
        className="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-700"
      >
        修改密码
      </Link>
      {user.is_admin ? (
        <Link
          href="/admin/users"
          className="rounded-full bg-slate-900 px-3 py-1 text-xs font-medium text-white"
        >
          用户管理
        </Link>
      ) : null}
      <button
        onClick={onLogout}
        disabled={busy}
        className="rounded-full border border-slate-300 px-3 py-1 text-xs text-slate-700 disabled:opacity-60"
      >
        退出
      </button>
    </div>
  );
}
