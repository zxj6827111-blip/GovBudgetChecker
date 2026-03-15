"use client";

import { ChevronDown, KeyRound, LogOut, Settings } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";

type AuthUser = {
  username?: string;
  is_admin?: boolean;
};

export default function Header() {
  const pathname = usePathname();
  const router = useRouter();
  const isAdminRoute = pathname.startsWith("/admin");
  const [user, setUser] = useState<AuthUser>({ username: "admin", is_admin: false });
  const [menuOpen, setMenuOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadUser() {
      try {
        const response = await fetch("/api/auth/me", { cache: "no-store" });
        if (!response.ok) {
          if (!cancelled) {
            setUser({ username: "admin", is_admin: false });
          }
          return;
        }

        const payload = (await response.json()) as { user?: AuthUser | null };
        if (!cancelled) {
          setUser({
            username: payload.user?.username?.trim() || "admin",
            is_admin: Boolean(payload.user?.is_admin),
          });
        }
      } catch {
        if (!cancelled) {
          setUser({ username: "admin", is_admin: false });
        }
      }
    }

    void loadUser();
    return () => {
      cancelled = true;
    };
  }, [pathname]);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    }

    document.addEventListener("mousedown", handleClickOutside);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, []);

  const handleLogout = async () => {
    if (loggingOut) {
      return;
    }

    setLoggingOut(true);
    try {
      await fetch("/api/auth/logout", { method: "POST" });
    } finally {
      setMenuOpen(false);
      router.replace("/login");
      router.refresh();
      setLoggingOut(false);
    }
  };

  return (
    <header className="relative z-10 flex h-16 shrink-0 items-center justify-between bg-slate-900 px-6 text-white shadow-sm">
      <div className="flex items-center gap-6">
        <Link href="/" className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded bg-primary-500 font-bold text-white shadow-inner">
            GC
          </div>
          <span className="text-lg font-semibold tracking-tight">GovBudgetChecker</span>
        </Link>
        <div className="mx-2 h-6 w-px bg-slate-700" />
        <nav className="flex items-center gap-1">
          <Link
            href="/"
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
              !isAdminRoute
                ? "bg-slate-800 text-white"
                : "text-slate-400 hover:bg-slate-800 hover:text-white",
            )}
          >
            工作台
          </Link>
          <Link
            href="/admin"
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
              isAdminRoute
                ? "bg-slate-800 text-white"
                : "text-slate-400 hover:bg-slate-800 hover:text-white",
            )}
          >
            系统管理
          </Link>
        </nav>
      </div>

      <div ref={menuRef} className="relative flex items-center border-l border-slate-700 pl-4">
        <button
          type="button"
          onClick={() => setMenuOpen((current) => !current)}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm font-medium text-white transition hover:bg-slate-800"
        >
          <span>{user.username || "admin"}</span>
          <ChevronDown
            className={cn(
              "h-4 w-4 text-slate-300 transition-transform",
              menuOpen ? "rotate-180" : "",
            )}
          />
        </button>

        {menuOpen ? (
          <div className="absolute right-0 top-[calc(100%+10px)] w-44 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 text-slate-800 shadow-xl">
            <Link
              href="/account/password"
              onClick={() => setMenuOpen(false)}
              className="flex items-center gap-2 px-4 py-2 text-sm transition hover:bg-slate-50"
            >
              <KeyRound className="h-4 w-4 text-slate-500" />
              修改密码
            </Link>
            {user.is_admin ? (
              <Link
                href="/admin"
                onClick={() => setMenuOpen(false)}
                className="flex items-center gap-2 px-4 py-2 text-sm transition hover:bg-slate-50"
              >
                <Settings className="h-4 w-4 text-slate-500" />
                系统管理
              </Link>
            ) : null}
            <button
              type="button"
              onClick={() => void handleLogout()}
              disabled={loggingOut}
              className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-red-600 transition hover:bg-red-50 disabled:opacity-60"
            >
              <LogOut className="h-4 w-4" />
              {loggingOut ? "退出中..." : "退出登录"}
            </button>
          </div>
        ) : null}
      </div>
    </header>
  );
}
