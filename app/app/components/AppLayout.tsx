"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { usePathname } from "next/navigation";
import { Suspense, useEffect, useRef, useState, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import Header from "./Header";
import Sidebar from "./Sidebar";

interface AppLayoutProps {
  children: ReactNode;
}

export default function AppLayout({ children }: AppLayoutProps) {
  const pathname = usePathname();
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);
  const shouldHideChrome = pathname === "/login" || pathname?.startsWith("/viewer/");
  const shouldAutoHideSidebar = pathname?.startsWith("/task/") ?? false;
  const lastNonTaskSidebarStateRef = useRef(true);
  const previousShouldAutoHideRef = useRef(shouldAutoHideSidebar);

  useEffect(() => {
    if (!shouldAutoHideSidebar && !previousShouldAutoHideRef.current) {
      lastNonTaskSidebarStateRef.current = isSidebarOpen;
    }
  }, [isSidebarOpen, shouldAutoHideSidebar]);

  useEffect(() => {
    const wasAutoHidingSidebar = previousShouldAutoHideRef.current;

    if (shouldAutoHideSidebar) {
      if (isSidebarOpen) {
        setIsSidebarOpen(false);
      }
    } else if (wasAutoHidingSidebar) {
      setIsSidebarOpen(lastNonTaskSidebarStateRef.current);
    }

    previousShouldAutoHideRef.current = shouldAutoHideSidebar;
  }, [isSidebarOpen, shouldAutoHideSidebar]);

  if (shouldHideChrome) {
    return <>{children}</>;
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-surface-50">
      <Header />
      <div className="relative flex flex-1 overflow-hidden">
        <div
          className={cn(
            "h-full shrink-0 overflow-hidden transition-all duration-300 ease-in-out",
            isSidebarOpen ? "w-[320px] xl:w-[360px]" : "w-0",
          )}
        >
          <div className="h-full w-[320px] xl:w-[360px]">
            <Suspense fallback={<div className="h-full border-r border-border bg-white" />}>
              <Sidebar />
            </Suspense>
          </div>
        </div>

        {!shouldAutoHideSidebar ? (
          <button
            onClick={() => setIsSidebarOpen(!isSidebarOpen)}
            className={cn(
              "absolute top-1/2 z-50 flex h-12 w-5 -translate-y-1/2 items-center justify-center rounded-r-md border border-slate-200 bg-white text-slate-400 shadow-sm transition-all duration-300 hover:bg-primary-50 hover:text-primary-600",
              isSidebarOpen ? "left-[320px] border-l-0 xl:left-[360px]" : "left-0",
            )}
            title={isSidebarOpen ? "收起侧边栏" : "展开侧边栏"}
          >
            {isSidebarOpen ? (
              <ChevronLeft className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
          </button>
        ) : null}

        <main className="relative min-w-0 flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
