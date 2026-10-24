"use client";

import { useCallback, useRef, useState } from "react";

import { fetchJobPage, type JobPageQuery, type JobPageView } from "@/lib/jobListViews";
import type { JobSummaryRecord } from "@/lib/uiAdapters";

export interface JobPageLoaderOptions<K extends { page: number }> {
  initialKey: K;
  toQuery: (key: K) => JobPageQuery;
  /** 后端未升级（/api/jobs 仍返回全量数组）时按原口径在本地算出同样形状的分页视图。 */
  buildLocally: (jobs: JobSummaryRecord[], key: K) => JobPageView;
  onLoaded?: (view: JobPageView) => void;
  /** 服务端把越界页码收敛到最后一页时通知页面同步页码。 */
  onPageClamped?: (page: number) => void;
}

/**
 * 按"最新查询"读取 /api/jobs?view=page 的一页（任务历史、工作台明细抽屉）。
 *
 * - setKey 换成新查询并中止进行中的旧请求；load 总按最新查询取数，请求期间查询
 *   又变了就重读，旧结果不会覆盖新结果；
 * - 并发调用 load（轮询与筛选变化同时触发）共用同一轮取数，不重复请求；
 * - 页码被服务端收敛后，旁路查询同步成收敛后的页码，页面随后 setPage 不会再触发请求。
 */
export function useJobPageLoader<K extends { page: number }>(options: JobPageLoaderOptions<K>) {
  /** 当前结果及其对应的查询：页面可据此判断显示的是不是本次查询的结果。 */
  const [loaded, setLoaded] = useState<{ view: JobPageView; key: K } | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 最新分页视图的旁路引用：轮询决策在 fetch 返回的同一微任务里读取，早于重渲染。 */
  const viewRef = useRef<JobPageView | null>(null);
  const keyRef = useRef<K>(options.initialKey);
  const abortRef = useRef<AbortController | null>(null);
  const runningRef = useRef<Promise<void> | null>(null);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const run = useCallback(async () => {
    for (;;) {
      const key = keyRef.current;
      const controller = new AbortController();
      abortRef.current = controller;
      const { toQuery, buildLocally } = optionsRef.current;
      try {
        const next = await fetchJobPage(toQuery(key), (jobs) => buildLocally(jobs, key), controller.signal);
        if (key !== keyRef.current) continue;
        viewRef.current = next;
        const loadedKey = next.page === key.page ? key : { ...key, page: next.page };
        setLoaded({ view: next, key: loadedKey });
        setError(null);
        optionsRef.current.onLoaded?.(next);
        if (loadedKey !== key) {
          keyRef.current = loadedKey;
          optionsRef.current.onPageClamped?.(next.page);
        }
        return;
      } catch (loadError) {
        if (controller.signal.aborted && key !== keyRef.current) continue;
        setError(loadError instanceof Error ? loadError.message : "任务列表读取失败，请重试。");
        throw loadError;
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    }
  }, []);

  const load = useCallback(() => {
    if (!runningRef.current) {
      runningRef.current = run().finally(() => {
        runningRef.current = null;
      });
    }
    return runningRef.current;
  }, [run]);

  /** 换查询时清掉上一个查询的错误：新查询的结果出来前不沿用旧失败。 */
  const setKey = useCallback((key: K) => {
    keyRef.current = key;
    abortRef.current?.abort();
    setError(null);
  }, []);

  return { view: loaded?.view ?? null, viewKey: loaded?.key ?? null, viewRef, keyRef, error, load, setKey };
}
