"use client";

import { createContext, useContext, type ReactNode } from "react";

import { WorkspaceTopbar } from "./WorkspaceTopbar";

/**
 * 页面内嵌顶栏：复核详情页让原文栏从窗口顶端开始，顶栏改由页面自己画在右栏顶部。
 *
 * WorkspaceShell 在这种页面不再画全宽顶栏，而是通过 context 交出侧栏开关状态；
 * 页面用 EmbeddedWorkspaceTopbar 在需要的位置渲染。没有 context（顶栏仍由外层
 * 框架统一绘制）时只渲染 leading 内容，不会出现两条顶栏，也不会丢掉页面标题。
 */
export interface EmbeddedTopbarState {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}

export const EmbeddedTopbarContext = createContext<EmbeddedTopbarState | null>(null);

export function EmbeddedWorkspaceTopbar({ leading }: { leading?: ReactNode }) {
  const state = useContext(EmbeddedTopbarContext);
  if (!state) {
    return leading ? <div className="flex h-12 shrink-0 items-center border-b border-border bg-white px-3">{leading}</div> : null;
  }
  return (
    <WorkspaceTopbar
      compact
      sidebarCollapsed={state.sidebarCollapsed}
      onToggleSidebar={state.onToggleSidebar}
      leading={leading}
    />
  );
}

export default EmbeddedWorkspaceTopbar;
