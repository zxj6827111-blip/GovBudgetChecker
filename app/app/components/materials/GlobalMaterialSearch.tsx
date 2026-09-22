"use client";

import type { Route } from "next";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  SEARCH_DEBOUNCE_MS,
  SEARCH_PAGE_SIZE,
  beginSearchRequest,
  buildMaterialSearchUrl,
  createSearchSequence,
  describeSearchItem,
  isLatestSearchResponse,
  moveSelectionIndex,
  readMaterialSearchPayload,
  resolveSearchRequest,
  type MaterialSearchResult,
  type SearchRequestDecision,
} from "./materialSearchAdapters";
import { MaterialSearchResultItem } from "./MaterialSearchResultItem";

import {
  SEARCH_EMPTY_TEXT,
  SEARCH_IDLE_EXAMPLE,
  SEARCH_IDLE_HINT,
  presentRelationshipResolution,
} from "../../../lib/materialSearchPresentation";

/**
 * GlobalMaterialSearch：顶栏全局搜索（命令面板 / Dialog，WP2-C）。
 *
 * 为什么是 Dialog 而不是新开一级页面（§三十八）
 * ------------------------------------------
 * "找材料"应该发生在用户正在做的任何事情旁边，而不是让他离开当前页面再回来。
 * 因此不新增 `/search` 路由：一个面板 + Ctrl/Cmd+K，关闭后回到原页面。
 *
 * 四态必须分得开（§四十四）
 * ------------------------
 * ``loading`` / ``error`` / ``empty`` / ``normal``。空态文案只说"没有找到符合条件
 * 且你有权限查看的材料"，**不说**"该材料存在但你无权"——后者会泄露材料存在性，
 * 而"没命中"与"没权限"在服务端本来就完全同形。
 *
 * 取数纪律
 * --------
 * - **debounce 250ms**：不是每按一个键就打一次数据库（§四十二）；
 * - **过期响应丢弃**：``AbortController`` + 请求序号双保险，先发的慢响应
 *   不能覆盖后发的快响应（§四十一）；
 * - **只在面板打开时发请求**；关闭即停，输入为空不发请求（§四十三）。
 *
 * 键盘交互（§四十）：Ctrl/Cmd+K 打开（在任意 workspace 页面都生效）、
 * Escape 关闭并把焦点还给顶栏搜索按钮、上下键移动选中项、Enter 打开当前选中材料。
 */
export interface GlobalMaterialSearchProps {
  /** 覆盖默认的取数函数（e2e / 单测注入用）；返回 null 表示"这次不接管"。 */
  fetchImpl?: typeof fetch;
}

export function GlobalMaterialSearch({ fetchImpl }: GlobalMaterialSearchProps = {}) {
  const router = useRouter();
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const sequence = useRef(createSearchSequence());
  /** 首次打开前不去抢焦点：面板没开过就聚焦搜索按钮会在进入页面时夺取焦点。 */
  const hasOpened = useRef(false);

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [decision, setDecision] = useState<SearchRequestDecision>({ kind: "idle" });
  const [result, setResult] = useState<MaterialSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(-1);

  // ---- 快捷键：Ctrl+K / Cmd+K（§三十九） --------------------------------
  useEffect(() => {
    function onGlobalKeyDown(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && String(event.key).toLowerCase() === "k") {
        event.preventDefault();
        setOpen((value) => !value);
      }
    }
    window.addEventListener("keydown", onGlobalKeyDown);
    return () => window.removeEventListener("keydown", onGlobalKeyDown);
  }, []);

  // ---- 焦点：打开进输入框，关闭回搜索按钮（§四十） ----------------------
  useEffect(() => {
    if (open) {
      hasOpened.current = true;
      inputRef.current?.focus();
      return;
    }
    if (hasOpened.current) {
      triggerRef.current?.focus();
    }
  }, [open]);

  // ---- debounce：输入 → 请求决策 ----------------------------------------
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDecision(resolveSearchRequest(query));
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [query]);

  // ---- 取数 -------------------------------------------------------------
  useEffect(() => {
    if (!open) {
      return;
    }
    if (decision.kind !== "search") {
      setResult(null);
      setError(null);
      setLoading(false);
      setSelected(-1);
      return;
    }

    // 收窄一次后用局部常量：函数声明（function load）是提升的，TypeScript
    // 不会把外层的类型收窄带进它的函数体，直接读 decision.q 会报类型错误。
    const activeQuery = decision.q;
    const requestId = beginSearchRequest(sequence.current);
    const controller = new AbortController();
    const doFetch = fetchImpl ?? fetch;
    setLoading(true);

    async function load() {
      try {
        const response = await doFetch(buildMaterialSearchUrl(activeQuery, 1, SEARCH_PAGE_SIZE), {
          cache: "no-store",
          signal: controller.signal,
        });
        const payload = await response.json().catch(() => null);
        if (!isLatestSearchResponse(sequence.current, requestId)) {
          return;
        }
        if (!response.ok) {
          const detail =
            payload && typeof payload === "object" && "detail" in payload
              ? String((payload as { detail?: unknown }).detail ?? "")
              : "";
          setError(detail || `请求失败（HTTP ${response.status}）`);
          setResult(null);
          return;
        }
        const parsed = readMaterialSearchPayload(payload);
        if (parsed === null) {
          setError("返回内容不符合材料搜索契约（缺少 data/meta）");
          setResult(null);
          return;
        }
        setError(null);
        setResult(parsed);
        setSelected(parsed.items.length > 0 ? 0 : -1);
      } catch (fetchError) {
        if (controller.signal.aborted) {
          return;
        }
        if (!isLatestSearchResponse(sequence.current, requestId)) {
          return;
        }
        setError(fetchError instanceof Error ? fetchError.message : "网络请求失败");
        setResult(null);
      } finally {
        if (isLatestSearchResponse(sequence.current, requestId)) {
          setLoading(false);
        }
      }
    }

    void load();
    return () => controller.abort();
    // 依赖 decision 对象：每次输入经 debounce 后才会生成新的 decision。
  }, [decision, open, fetchImpl]);

  // useMemo：items 会被下面的 useCallback 依赖，每次渲染新建数组会让它失效。
  const items = useMemo(() => result?.items ?? [], [result]);
  const relationshipNotice = presentRelationshipResolution(
    result?.meta?.relationship_resolution,
  ).notice;

  const close = useCallback(() => setOpen(false), []);

  const openItem = useCallback(
    (index: number) => {
      const item = items[index];
      if (!item) {
        return;
      }
      const view = describeSearchItem(item);
      close();
      router.push(view.openHref as Route);
    },
    [close, items, router],
  );

  function onPanelKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelected((current) => moveSelectionIndex(current, 1, items.length));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelected((current) => moveSelectionIndex(current, -1, items.length));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      openItem(selected);
      return;
    }
    if (event.key === "Tab") {
      // 轻量焦点陷阱：面板打开时 Tab 不跑到背后的页面上。
      const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled])',
      );
      if (!focusable || focusable.length === 0) {
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      }
    }
  }

  const state = loading ? "loading" : error ? "error" : items.length > 0 ? "normal" : "empty";

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label="搜索"
        aria-haspopup="dialog"
        aria-expanded={open}
        data-testid="gbc-global-search-trigger"
        onClick={() => setOpen((value) => !value)}
        className="rounded-md p-2 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
      >
        搜索
      </button>

      {open ? (
        <div
          className="fixed inset-0 z-50 flex items-start justify-center bg-slate-900/40 p-6"
          data-testid="gbc-global-search-overlay"
          onClick={close}
        >
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label="全局材料搜索"
            data-testid="gbc-global-search-dialog"
            onClick={(event) => event.stopPropagation()}
            onKeyDown={onPanelKeyDown}
            className="mt-16 w-full max-w-3xl rounded-lg border border-border bg-white shadow-xl"
          >
            <div className="flex items-center gap-2 border-b border-border px-4 py-3">
              <input
                ref={inputRef}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="搜索材料：文件名、主管部门、单位、财政年度、预算/决算、任务 ID"
                aria-label="搜索材料"
                data-testid="gbc-global-search-input"
                className="w-full border-0 text-sm text-slate-900 outline-none placeholder:text-slate-400"
              />
              <button
                type="button"
                onClick={close}
                data-testid="gbc-global-search-close"
                aria-label="关闭搜索"
                className="rounded px-2 py-1 text-xs text-slate-500 hover:bg-slate-100 hover:text-slate-900"
              >
                Esc
              </button>
            </div>

            <div
              className="max-h-[60vh] overflow-y-auto px-4 py-3"
              data-testid="gbc-global-search-state"
              data-state={state}
            >
              {decision.kind === "idle" ? (
                <div className="text-sm text-slate-500" data-testid="gbc-global-search-hint">
                  <p>{SEARCH_IDLE_HINT}</p>
                  <p className="mt-1 text-xs text-slate-400">例如：{SEARCH_IDLE_EXAMPLE}</p>
                </div>
              ) : null}

              {decision.kind === "invalid" ? (
                <div className="text-sm text-slate-500" data-testid="gbc-global-search-invalid">
                  {decision.reason}
                </div>
              ) : null}

              {decision.kind === "search" && loading ? (
                <div className="text-sm text-slate-500" data-testid="gbc-global-search-loading">
                  正在搜索…
                </div>
              ) : null}

              {decision.kind === "search" && !loading && error ? (
                <div className="text-sm text-danger-700" data-testid="gbc-global-search-error">
                  搜索失败：{error}
                </div>
              ) : null}

              {decision.kind === "search" && !loading && !error && items.length === 0 ? (
                <div className="text-sm text-slate-500" data-testid="gbc-global-search-empty">
                  {SEARCH_EMPTY_TEXT}
                </div>
              ) : null}

              {relationshipNotice ? (
                <div
                  className="mb-2 rounded border border-amber-200 bg-amber-50 px-2 py-1 text-xs text-amber-800"
                  data-testid="gbc-global-search-relationship-warning"
                >
                  {relationshipNotice}
                </div>
              ) : null}

              {items.length > 0 ? (
                <ul className="space-y-2" data-testid="gbc-global-search-results">
                  {items.map((item, index) => (
                    <MaterialSearchResultItem
                      key={item.slot_id}
                      item={item}
                      index={index}
                      selected={index === selected}
                      onHover={setSelected}
                    />
                  ))}
                </ul>
              ) : null}
            </div>

            <div className="flex items-center justify-between border-t border-border px-4 py-2 text-xs text-slate-400">
              <span>↑↓ 选择 · Enter 打开材料 · Esc 关闭</span>
              {result ? (
                <span data-testid="gbc-global-search-total">
                  共 {result.meta.pagination.total} 条，显示前 {items.length} 条
                </span>
              ) : null}
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

export default GlobalMaterialSearch;
