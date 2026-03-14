"use client";

import { Bell, Settings, User } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

export default function Header() {
  const pathname = usePathname();
  const isAdminRoute = pathname.startsWith("/admin");

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

      <div className="flex items-center gap-4">
        <button className="relative p-2 text-slate-400 hover:text-white">
          <Bell className="h-5 w-5" />
          <span className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full border-2 border-slate-900 bg-danger-600" />
        </button>
        <button className="p-2 text-slate-400 hover:text-white">
          <Settings className="h-5 w-5" />
        </button>
        <div className="ml-2 flex items-center gap-2 border-l border-slate-700 pl-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-slate-700">
            <User className="h-4 w-4 text-slate-300" />
          </div>
          <div className="flex flex-col">
            <span className="text-sm font-medium leading-none">审校员 1</span>
            <span className="mt-1 text-xs text-slate-400">市财政局</span>
          </div>
        </div>
      </div>
    </header>
  );
}
