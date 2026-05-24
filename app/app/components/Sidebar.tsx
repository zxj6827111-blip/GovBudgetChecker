"use client";

import type { Route } from "next";
import { Building2, ChevronRight, Folder, MapPin, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { ORG_TREE_REFRESH_EVENT } from "@/lib/orgTreeEvents";
import type { OrganizationRecord } from "@/lib/uiAdapters";
import { cn } from "@/lib/utils";

type OrganizationsResponse = {
  tree?: OrganizationRecord[];
};

const REGION_NAME = "上海市普陀区";

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      return fallback;
    }
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

function normalizeSearchValue(value: string): string {
  return value.trim().toLowerCase();
}

function buildInitialExpanded(
  nodes: OrganizationRecord[],
  depth = 0,
  result: Record<string, boolean> = {},
): Record<string, boolean> {
  for (const node of nodes) {
    if (depth < 2) {
      result[node.id] = true;
    }
    if (Array.isArray(node.children) && node.children.length > 0) {
      buildInitialExpanded(node.children, depth + 1, result);
    }
  }
  return result;
}

function findPathToNode(nodes: OrganizationRecord[], targetId: string): string[] {
  for (const node of nodes) {
    if (node.id === targetId) {
      return [node.id];
    }
    const children = Array.isArray(node.children) ? node.children : [];
    const childPath = findPathToNode(children, targetId);
    if (childPath.length > 0) {
      return [node.id, ...childPath];
    }
  }
  return [];
}

function filterOrganizations(nodes: OrganizationRecord[], query: string): OrganizationRecord[] {
  return nodes.flatMap((node) => {
    const children = Array.isArray(node.children)
      ? filterOrganizations(node.children, query)
      : [];
    const isMatch = normalizeSearchValue(node.name).includes(query);

    if (!isMatch && children.length === 0) {
      return [];
    }

    return [{ ...node, children }];
  });
}

function highlightMatch(text: string, query: string) {
  if (!query) {
    return text;
  }

  const matchIndex = text.toLowerCase().indexOf(query);
  if (matchIndex < 0) {
    return text;
  }

  const matchedText = text.slice(matchIndex, matchIndex + query.length);

  return (
    <>
      {text.slice(0, matchIndex)}
      <mark className="rounded bg-primary-100 px-0.5 text-primary-700">{matchedText}</mark>
      {text.slice(matchIndex + matchedText.length)}
    </>
  );
}

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = useMemo(() => pathname.match(/^\/department\/([^/]+)/)?.[1] ?? "", [pathname]);
  const searchQuery = searchParams.get("q") ?? "";
  const normalizedSearchQuery = useMemo(
    () => normalizeSearchValue(searchQuery),
    [searchQuery],
  );

  const [orgs, setOrgs] = useState<OrganizationRecord[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [inputValue, setInputValue] = useState(searchQuery);

  useEffect(() => {
    setInputValue(searchQuery);
  }, [searchQuery]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      const nextQuery = inputValue.trim();
      const currentQuery = searchQuery.trim();
      if (nextQuery === currentQuery) {
        return;
      }

      const params = new URLSearchParams(searchParams.toString());
      if (nextQuery) {
        params.set("q", nextQuery);
      } else {
        params.delete("q");
      }

      const nextHref = params.toString() ? `${pathname}?${params.toString()}` : pathname;
      router.replace(nextHref as Route, { scroll: false });
    }, 180);

    return () => window.clearTimeout(handle);
  }, [inputValue, pathname, router, searchParams, searchQuery]);

  useEffect(() => {
    let alive = true;

    async function load() {
      const payload = await fetchJson<OrganizationsResponse>("/api/organizations", { tree: [] });
      if (!alive) {
        return;
      }

      const tree = Array.isArray(payload.tree) ? payload.tree : [];
      setOrgs(tree);
      setExpanded((current) => {
        const nextExpanded =
          Object.keys(current).length > 0 ? { ...current } : buildInitialExpanded(tree);
        if (selectedId) {
          for (const id of findPathToNode(tree, selectedId)) {
            nextExpanded[id] = true;
          }
        }
        return nextExpanded;
      });
    }

    const handleRefresh = () => {
      void load();
    };

    void load();
    window.addEventListener(ORG_TREE_REFRESH_EVENT, handleRefresh);
    return () => {
      alive = false;
      window.removeEventListener(ORG_TREE_REFRESH_EVENT, handleRefresh);
    };
  }, [selectedId]);

  const visibleOrgs = useMemo(
    () => (normalizedSearchQuery ? filterOrganizations(orgs, normalizedSearchQuery) : orgs),
    [normalizedSearchQuery, orgs],
  );

  const toggle = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const renderOrg = (org: OrganizationRecord, depth = 0) => {
    const hasChildren = Array.isArray(org.children) && org.children.length > 0;
    const isSelected = pathname === (`/department/${org.id}` as Route);
    const isExpanded = normalizedSearchQuery ? hasChildren : Boolean(expanded[org.id]);
    const href = searchQuery
      ? (`/department/${org.id}?q=${encodeURIComponent(searchQuery)}` as Route)
      : (`/department/${org.id}` as Route);

    return (
      <div key={org.id} className="mb-1" style={{ paddingLeft: depth === 0 ? 0 : depth * 8 }}>
        <div
          className={cn(
            "group flex items-start justify-between gap-2 rounded-md px-2 py-2 text-sm transition-colors hover:bg-slate-50",
            isSelected ? "bg-primary-50 font-medium text-primary-700" : "text-slate-700",
          )}
        >
          <div className="flex min-w-0 flex-1 items-start">
            <button
              type="button"
              onClick={() => {
                if (!normalizedSearchQuery && hasChildren) {
                  toggle(org.id);
                }
              }}
              className="mr-1 mt-0.5 shrink-0 rounded p-0.5 text-slate-400 transition-colors hover:text-slate-600"
              aria-label={hasChildren ? `展开或收起 ${org.name}` : `${org.name} 无下级组织`}
            >
              {hasChildren ? (
                <ChevronRight
                  className={cn("h-4 w-4 transition-transform", isExpanded && "rotate-90")}
                />
              ) : (
                <span className="inline-block h-4 w-4" />
              )}
            </button>

            {org.level === "unit" ? (
              <Folder className="mr-2 mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            ) : (
              <Building2 className="mr-2 mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
            )}

            <Link
              href={href}
              className="min-w-0 flex-1 break-words text-[13px] leading-5"
              title={org.name}
            >
              {highlightMatch(org.name, normalizedSearchQuery)}
            </Link>
          </div>

          <div className="ml-2 flex min-w-[4.5rem] shrink-0 flex-col items-end gap-1 text-[11px] leading-none">
            {Number(org.issue_count ?? 0) > 0 ? (
              <span
                className="min-w-[4.5rem] whitespace-nowrap rounded bg-danger-50 px-2 py-1 text-center font-medium text-danger-600"
                title={`问题数：${org.issue_count}`}
              >
                问题 {org.issue_count}
              </span>
            ) : null}
            {Number(org.job_count ?? 0) > 0 ? (
              <span
                className="min-w-[4.5rem] whitespace-nowrap rounded bg-slate-100 px-2 py-1 text-center font-medium text-slate-500"
                title={`报告数：${org.job_count}`}
              >
                报告 {org.job_count}
              </span>
            ) : null}
          </div>
        </div>

        {hasChildren && isExpanded ? (
          <div className="mt-1 border-l border-slate-200 pl-2">
            {org.children?.map((child) => renderOrg(child, depth + 1))}
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <aside className="flex h-[calc(100vh-64px)] w-[320px] shrink-0 flex-col overflow-y-auto border-r border-border bg-white xl:w-[360px]">
      <div className="sticky top-0 z-20 bg-white">
        <div className="border-b border-border bg-slate-50/60 p-4">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            当前行政区划
          </div>
          <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 shadow-sm">
            <MapPin className="h-4 w-4 shrink-0 text-primary-600" />
            <span className="truncate text-sm font-medium text-slate-900">{REGION_NAME}</span>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            组织树与部门页搜索会同步联动，便于快速定位报告。
          </p>
        </div>

        <div className="border-b border-border bg-white p-4">
          <label className="relative block">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
              placeholder="搜索单位或报告..."
              className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-9 pr-10 text-sm text-slate-700 transition-colors placeholder:text-slate-400 focus:border-primary-500 focus:bg-white focus:outline-none focus:ring-2 focus:ring-primary-100"
            />
            {inputValue.trim() ? (
              <button
                type="button"
                onClick={() => setInputValue("")}
                className="absolute right-2 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded-full text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
                aria-label="清空搜索"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : null}
          </label>
        </div>

        <div className="flex items-center justify-between border-b border-border bg-white p-4">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-slate-500">组织架构</h2>
          <Link
            href={"/admin?tab=organization" as Route}
            className="rounded-md border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600 transition-colors hover:bg-slate-50 hover:text-slate-900"
          >
            后台维护
          </Link>
        </div>
      </div>

      <div className="flex-1 p-2">
        {orgs.length === 0 ? (
          <div className="p-3 text-sm text-slate-500">正在加载组织架构...</div>
        ) : visibleOrgs.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-4 text-sm text-slate-500">
            没有找到和“{searchQuery}”相关的组织或报告。
          </div>
        ) : (
          visibleOrgs.map((org) => renderOrg(org))
        )}
      </div>
    </aside>
  );
}
