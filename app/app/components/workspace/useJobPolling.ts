"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  createJobPollingController,
  type JobPollingEnvironment,
  type PollingDecision,
} from "@/lib/jobPolling";

/**
 * useJobPolling（修复 1）：任务列表/详情自动刷新的 React 封装。
 *
 * 职责边界：定时与调度全部在 createJobPollingController（纯逻辑、可注入时钟，
 * 单测覆盖启动/停止/暂停/清理/防叠积/失败续排）；本 hook 只负责：
 * - 把 fetcher/decide 的最新版本透传给控制器（避免因闭包过期读到旧状态）；
 * - 组件卸载时 dispose（定时器与监听器必须清理，不得泄漏）；
 * - 维护"最后更新时间 / 上次刷新错误 / 手动刷新中"三个用户可见的同步状态，
 *   让"没在动"（全部终态，轮询已停）和"没刷新"（请求失败）可区分。
 */
export interface UseJobPollingOptions {
  /** 执行一次刷新。失败请抛错（hook 会上报 onError 并继续轮询）。 */
  fetcher: () => Promise<void>;
  /** 基于最新数据给出轮询决策（每次续排时实时调用）。 */
  decide: () => PollingDecision;
  /** 注入测试环境；生产不传。 */
  env?: JobPollingEnvironment;
}

export interface JobPollingState {
  /** 最近一次成功刷新的时间；null 表示尚未成功刷新过。 */
  lastSyncedAt: Date | null;
  /** 上次刷新失败的错误文案；null 表示最近一次刷新成功。 */
  lastErrorMessage: string | null;
  /** 手动刷新是否进行中（自动轮询不计入，避免按钮文案闪烁）。 */
  isManualRefreshing: boolean;
  /** 手动刷新入口。 */
  refreshNow: () => void;
}

function toErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return String(error ?? "刷新失败");
}

export function useJobPolling(options: UseJobPollingOptions): JobPollingState {
  const [lastSyncedAt, setLastSyncedAt] = useState<Date | null>(null);
  const [lastErrorMessage, setLastErrorMessage] = useState<string | null>(null);
  const [isManualRefreshing, setIsManualRefreshing] = useState(false);

  // fetcher/decide 走 ref：控制器只在挂载时创建一次，续排时读 ref 拿到最新闭包，
  // 既不会因依赖变化反复重建控制器，也不会读到过期状态。
  const fetcherRef = useRef(options.fetcher);
  const decideRef = useRef(options.decide);
  fetcherRef.current = options.fetcher;
  decideRef.current = options.decide;
  const envRef = useRef(options.env);
  envRef.current = options.env;

  const controllerRef = useRef<ReturnType<typeof createJobPollingController> | null>(null);

  useEffect(() => {
    const controller = createJobPollingController({
      fetcher: () => fetcherRef.current(),
      decide: () => decideRef.current(),
      onSynced: () => {
        setLastSyncedAt(new Date());
        setLastErrorMessage(null);
      },
      onError: (error) => {
        setLastErrorMessage(toErrorMessage(error));
      },
      env: envRef.current,
    });
    controllerRef.current = controller;
    controller.start();
    return () => {
      // 卸载必须清理：定时器与可见性监听随控制器一并释放。
      controllerRef.current = null;
      controller.dispose();
    };
  }, []);

  const refreshNow = useCallback(() => {
    setIsManualRefreshing(true);
    void Promise.resolve(controllerRef.current?.refreshNow()).finally(() =>
      setIsManualRefreshing(false),
    );
  }, []);

  return { lastSyncedAt, lastErrorMessage, isManualRefreshing, refreshNow };
}
