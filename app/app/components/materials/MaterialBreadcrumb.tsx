"use client";

import Link from "next/link";
import type { Route } from "next";

/**
 * MaterialBreadcrumb：材料台账三级导航路径（台账 → 区县 → 主管部门）。
 *
 * 为什么不能让顶栏兜底
 * --------------------
 * 顶栏面包屑只能显示"当前导航项"这一个层级（`材料台账`），无法表达
 * "我在普陀区 → 规划和自然资源局"这条下钻路径。用户从首页点进两级之后，
 * 必须能一眼看到自己在哪、并能逐级返回，否则只能靠浏览器后退键猜。
 *
 * 最后一级是当前页，渲染为纯文本（不可点击），避免"点自己"的无意义跳转。
 */
export interface BreadcrumbEntry {
  label: string;
  href?: string;
}

export interface MaterialBreadcrumbProps {
  entries: BreadcrumbEntry[];
  testId?: string;
}

export function MaterialBreadcrumb({ entries, testId = "gbc-material-breadcrumb" }: MaterialBreadcrumbProps) {
  return (
    <nav
      aria-label="材料台账路径"
      data-testid={testId}
      className="flex flex-wrap items-center gap-1 text-xs text-slate-500"
    >
      {entries.map((entry, index) => {
        const isLast = index === entries.length - 1;
        return (
          <span key={`${entry.label}-${index}`} className="flex items-center gap-1">
            {index > 0 ? (
              <span aria-hidden="true" className="text-slate-300">
                /
              </span>
            ) : null}
            {entry.href && !isLast ? (
              <Link
                href={entry.href as Route}
                className="rounded px-1 text-primary-700 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
                data-testid={`${testId}-link-${index}`}
              >
                {entry.label}
              </Link>
            ) : (
              <span className="px-1 text-slate-700" data-testid={`${testId}-current`}>
                {entry.label}
              </span>
            )}
          </span>
        );
      })}
    </nav>
  );
}

export default MaterialBreadcrumb;
