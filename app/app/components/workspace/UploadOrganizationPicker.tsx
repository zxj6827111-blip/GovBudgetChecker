"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Badge, Button } from "@/components/ui";
import { flattenOrganizationTree } from "./OrganizationFilterSelect";
import type { OrganizationRecordLike, PreflightResponseLike } from "./uploadCenterAdapters";
import {
  buildUploadOrganizationOptions,
  searchUploadOrganizations,
  selectUploadOrganizationCandidates,
  type UploadOrganizationOption,
} from "./uploadOrganizationOptions";

const SEARCH_PAGE_SIZE = 30;
interface OrganizationTreeResponse {
  tree?: Array<OrganizationRecordLike & { children?: OrganizationTreeResponse["tree"] }>;
}

export function UploadOrganizationPicker({ preflight, value, onSelect }: {
  preflight?: PreflightResponseLike | null;
  value: string;
  onSelect: (option: UploadOrganizationOption | null) => void;
}) {
  const id = useId();
  const [organizations, setOrganizations] = useState<OrganizationRecordLike[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [showSearch, setShowSearch] = useState(false);
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("");
  const [scopeId, setScopeId] = useState("");
  const [searchLimit, setSearchLimit] = useState(SEARCH_PAGE_SIZE);
  // 父组件每次渲染都可能传入新的回调；把它放进依赖会让加载 effect 反复执行，
  // 一次失败后又被下一次成功请求覆盖（重试按钮形同虚设）。
  const onSelectRef = useRef(onSelect);
  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setLoadError(false);
    onSelectRef.current(null);
    async function loadOrganizations() {
      try {
        const response = await fetch("/api/organizations?stats=none&purpose=upload", { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error("organization catalog unavailable");
        const payload = (await response.json()) as OrganizationTreeResponse;
        if (!Array.isArray(payload.tree)) throw new Error("invalid organization catalog");
        if (!controller.signal.aborted) setOrganizations(flattenOrganizationTree(payload.tree));
      } catch {
        if (!controller.signal.aborted) setLoadError(true);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void loadOrganizations();
    return () => controller.abort();
  }, [loadAttempt]);

  const options = useMemo(() => buildUploadOrganizationOptions(organizations), [organizations]);
  const candidates = useMemo(() => selectUploadOrganizationCandidates(options, preflight), [options, preflight]);
  const searchResults = useMemo(() => searchUploadOrganizations(options, query, level, scopeId), [options, query, level, scopeId]);
  const scopes = useMemo(() => Array.from(new Map(options.map((option) => [option.scopeId, option.scopeLabel])).entries()), [options]);
  const searching = showSearch || candidates.length === 0;

  function renderOption(option: UploadOrganizationOption) {
    return (
      <label key={option.id}
        className={`flex min-w-0 items-start gap-3 rounded-lg border p-4 ${value === option.id ? "border-primary-400 bg-primary-50" : "border-border bg-white hover:bg-surface-50"}`}
        data-testid={`gbc-upload-org-option-${option.id}`}>
        <input type="radio" name={`${id}-organization`} value={option.id} checked={value === option.id}
          onChange={() => onSelect(option)} className="mt-1 h-4 w-4 shrink-0 accent-primary-600" />
        <span className="min-w-0">
          <span className="flex flex-wrap items-center gap-2 text-sm font-medium text-slate-900">
            <span className="break-words">{option.name}</span>
            <Badge tone="neutral">{option.level === "unit" ? "单位材料" : "部门汇总"}</Badge>
          </span>
          <span className="mt-1 block break-words text-xs text-slate-500">{option.path}</span>
        </span>
      </label>
    );
  }

  if (loading) return <p role="status" className="text-sm text-slate-500">正在加载可选组织…</p>;
  if (loadError) return (
    <div role="alert" className="space-y-3 text-sm text-danger-700">
      <p>组织目录加载失败，请重试后选择归属。</p>
      <Button variant="secondary" size="sm" onClick={() => setLoadAttempt((attempt) => attempt + 1)}>重新加载组织</Button>
    </div>
  );

  return (
    <div className="space-y-3">
      {!searching ? (
        <div role="radiogroup" aria-label="相关候选" data-testid="gbc-upload-org-candidates" className="space-y-3">
          <p className="text-sm font-medium">相关候选 · 请按原件确认</p>
          {candidates.map(renderOption)}
        </div>
      ) : (
        <div className="space-y-3" data-testid="gbc-upload-org-search-panel">
          <label htmlFor={`${id}-search`} className="block text-sm font-medium">搜索可选组织</label>
          <input id={`${id}-search`} type="search" value={query}
            onChange={(event) => { setQuery(event.target.value); setSearchLimit(SEARCH_PAGE_SIZE); }}
            placeholder="输入组织名称、简称或关键词，如：信访" data-testid="gbc-upload-org-search"
            className="w-full rounded-md border border-border bg-white px-3 py-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400" />
          <div className="flex flex-wrap items-center gap-2">
            {[["", "全部"], ["department", "部门汇总"], ["unit", "单位材料"]].map(([filter, label]) => (
              <button key={filter} type="button" aria-pressed={level === filter}
                onClick={() => { setLevel(filter); setSearchLimit(SEARCH_PAGE_SIZE); }}
                className={`rounded-md border px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400 ${level === filter ? "border-primary-600 bg-primary-600 text-white" : "border-border bg-white text-slate-600"}`}>
                {label}
              </button>
            ))}
            {scopes.length > 1 ? (
              <select aria-label="管理范围" value={scopeId}
                onChange={(event) => { setScopeId(event.target.value); setSearchLimit(SEARCH_PAGE_SIZE); }}
                className="max-w-full rounded-md border border-border bg-white px-2 py-1.5 text-sm">
                <option value="">全部可用范围</option>
                {scopes.map(([scope, label]) => <option key={scope} value={scope}>{label}</option>)}
              </select>
            ) : null}
          </div>
          <p className="text-xs text-slate-500" aria-live="polite">找到 {searchResults.length} 个可选组织</p>
          <div role="radiogroup" aria-label="搜索结果" className="space-y-3">
            {searchResults.slice(0, searchLimit).map(renderOption)}
          </div>
          {searchResults.length === 0 ? <p className="text-sm text-slate-500">未找到可选组织，请更换关键词或管理范围；如目录缺失，请联系管理员核对。</p> : null}
          {searchResults.length > searchLimit ? <Button variant="secondary" size="sm" onClick={() => setSearchLimit((limit) => limit + SEARCH_PAGE_SIZE)}>再显示 {SEARCH_PAGE_SIZE} 个</Button> : null}
        </div>
      )}
      {candidates.length > 0 ? (
        <button type="button" data-testid="gbc-upload-org-search-toggle" onClick={() => setShowSearch((show) => !show)}
          className="rounded-sm py-2 text-sm text-primary-700 underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400">
          {searching ? "返回相关候选" : "不是这些组织？搜索其他组织"}
        </button>
      ) : null}
    </div>
  );
}
