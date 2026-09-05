import type { JobSummaryRecord } from "./uiAdapters";
import { isUiTaskFinished, normalizeUiTaskStatus } from "./uiAdapters";

/**
 * 任务状态轮询的纯逻辑层（修复 1：新 UI 完全没有轮询，任务完成后界面不刷新）。
 *
 * 设计目标（对照任务书修复 1 的硬性要求）：
 * - 有真正在跑的任务（queued/processing）时才以较高频率轮询；
 * - 只有"待分析"（uploaded）这类静止的非终态任务时大幅降频——uploaded 不代表
 *   后端在干活，451 个历史 uploaded 任务如果按活跃频率轮询等于无条件常驻打后端；
 * - 全部终态时彻底停止，不留定时器；
 * - 页面隐藏（document.hidden）时暂停，切回前台恢复；
 * - 同一时刻最多一个请求在飞（防叠积）；
 * - 请求失败不中断轮询循环，也不静默吞掉错误状态（由 onError 上报给 UI）。
 *
 * 控制器与 React 解耦（createJobPollingController 不依赖任何框架），定时器与
 * 可见性监听都可注入，jiti 单测可以用假时钟完整验证启动/停止/暂停/清理行为，
 * 不需要 React 渲染器。
 */

/** 活跃任务（queued/processing 等"后端正在干活"的状态）的轮询间隔。 */
export const ACTIVE_POLL_INTERVAL_MS = 5000;
/** 仅有待分析（uploaded，静止状态）等非终态任务时的降频轮询间隔。 */
export const WAITING_POLL_INTERVAL_MS = 60_000;

/** "已上传、分析未启动"的静止非终态原始状态（见 resolvePollingDecision 内注释）。 */
const STATIC_WAITING_RAW_STATUSES = new Set(["uploaded"]);

/**
 * 轮询决策：
 * - active：存在正在执行的任务，按 ACTIVE_POLL_INTERVAL_MS 轮询；
 * - waiting：没有正在执行的，但存在待分析等静止非终态任务，按降频间隔轮询；
 * - stop：已知数据全部终态，停止轮询（恢复入口：手动刷新 / 页面重新可见）。
 */
export type PollingDecision =
  | { kind: "active"; intervalMs: number }
  | { kind: "waiting"; intervalMs: number }
  | { kind: "stop" };

/**
 * 从任务列表推导轮询决策。
 *
 * 反例（核心断言）：
 * - jobs 为 null/undefined（尚未拉到数据，或上一次请求失败）时按 active 处理——
   * "未知"按活跃对待才能保证失败后仍持续重试，不会因一次失败永久停摆；
 * - 空数组或全部终态 → stop，不得继续定时打后端；
 * - 仅 uploaded（待分析，静止）→ waiting 降频，不得按活跃频率空转。
 */
export function resolvePollingDecision(
  jobs: JobSummaryRecord[] | null | undefined,
): PollingDecision {
  if (jobs === null || jobs === undefined) {
    return { kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS };
  }
  let hasRunning = false;
  let hasWaiting = false;
  for (const job of jobs) {
    const raw = String(job.status ?? "").trim().toLowerCase();
    if (isUiTaskFinished(normalizeUiTaskStatus(job.status))) {
      continue;
    }
    // uploaded 是"已上传、分析未启动"的静止状态，后端没有在干活（与
    // "review_required 不得显示成分析完成"同一条如实原则）。normalizeUiTaskStatus
    // 在状态归一修复落地前会把它兜底成 analyzing，这里显式按原始状态识别为
    // waiting，两条路径语义一致（归一修复落地后由 pending_analysis 分支接管）。
    // status 显式放宽为 string：pending_analysis 状态在归一联合类型中随后续
    // 修复补入，轮询层不依赖该联合类型的时序。
    const status: string = normalizeUiTaskStatus(job.status);
    if (status === "pending_analysis" || STATIC_WAITING_RAW_STATUSES.has(raw)) {
      hasWaiting = true;
    } else {
      hasRunning = true;
    }
  }
  if (hasRunning) {
    return { kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS };
  }
  if (hasWaiting) {
    return { kind: "waiting", intervalMs: WAITING_POLL_INTERVAL_MS };
  }
  return { kind: "stop" };
}

/** 可注入的环境（默认绑定 window/document，SSR 或测试环境可替换）。 */
export interface JobPollingEnvironment {
  setTimeoutFn: (callback: () => void, ms: number) => unknown;
  clearTimeoutFn: (handle: unknown) => void;
  isDocumentHidden: () => boolean;
  addVisibilityChangeListener: (callback: () => void) => void;
  removeVisibilityChangeListener: (callback: () => void) => void;
}

function resolveDefaultEnvironment(): JobPollingEnvironment | null {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return null;
  }
  return {
    setTimeoutFn: (callback, ms) => window.setTimeout(callback, ms),
    clearTimeoutFn: (handle) => window.clearTimeout(handle as number),
    isDocumentHidden: () => document.hidden,
    addVisibilityChangeListener: (callback) =>
      document.addEventListener("visibilitychange", callback),
    removeVisibilityChangeListener: (callback) =>
      document.removeEventListener("visibilitychange", callback),
  };
}

export interface JobPollingControllerOptions {
  /** 执行一次刷新（拉数据并更新页面状态）。抛错视为本次刷新失败。 */
  fetcher: () => Promise<void>;
  /** 基于当前最新数据给出轮询决策。每次需要续排时实时调用，保证读到最新状态。 */
  decide: () => PollingDecision;
  /** 刷新成功回调（用于记录"最后更新时间"）。 */
  onSynced?: () => void;
  /** 刷新失败回调（用于把错误状态如实呈现给用户，不得静默吞掉）。 */
  onError?: (error: unknown) => void;
  env?: JobPollingEnvironment;
}

export interface JobPollingController {
  /** 启动：立即执行首次刷新，之后按决策续排。 */
  start: () => void;
  /** 手动刷新（防叠积：已有请求在飞时跳过，由在飞请求完成后自然续排）。 */
  refreshNow: () => Promise<void>;
  /** 释放定时器与监听器；释放后任何排程都不会再执行（防泄漏断言的依据）。 */
  dispose: () => void;
  /** 是否仍有请求在飞（测试防叠积用）。 */
  isFetchInFlight: () => boolean;
}

/**
 * 轮询控制器：单飞（in-flight guard）+ 隐藏暂停 + 终态停止 + 失败续排。
 *
 * 控制流：每次 fetch 结束（无论成败）都会走 scheduleAfterFetch 重新决策，
 * 因此任何一次失败都不会打断循环；只有 decide() 返回 stop 或 dispose 才会停。
 */
export function createJobPollingController(
  options: JobPollingControllerOptions,
): JobPollingController {
  const env = options.env ?? resolveDefaultEnvironment();
  let disposed = false;
  let timer: unknown = null;
  let inFlight = false;
  /** 隐藏期间错过的心跳：切回前台时补一次刷新再恢复循环。 */
  let pausedWhileHidden = false;

  function clearTimer(): void {
    if (timer !== null && env) {
      env.clearTimeoutFn(timer);
      timer = null;
    }
  }

  function scheduleAfterFetch(): void {
    if (disposed) {
      return;
    }
    if (!env) {
      return;
    }
    if (env.isDocumentHidden()) {
      // 页面隐藏：暂停（不再排定时器），切回前台时由可见性监听恢复。
      pausedWhileHidden = true;
      return;
    }
    const decision = options.decide();
    if (decision.kind === "stop") {
      // 全部终态：停止轮询，不留定时器。恢复入口是手动刷新或重新可见。
      return;
    }
    clearTimer();
    timer = env.setTimeoutFn(() => {
      timer = null;
      void runFetch().then(scheduleAfterFetch);
    }, decision.intervalMs);
  }

  async function runFetch(): Promise<void> {
    if (disposed || inFlight) {
      return;
    }
    inFlight = true;
    try {
      await options.fetcher();
      options.onSynced?.();
    } catch (error) {
      // 失败不上抛、不中断循环：记一次错误状态供 UI 如实呈现，循环照常续排。
      options.onError?.(error);
    } finally {
      inFlight = false;
    }
  }

  function handleVisibilityChange(): void {
    if (disposed || !env || env.isDocumentHidden()) {
      return;
    }
    if (!pausedWhileHidden) {
      // 隐藏期间本来就是停止态（全部终态）或已有请求在飞：不额外补打请求。
      // 前者靠手动刷新恢复，后者由在飞请求完成后自然续排。
      return;
    }
    pausedWhileHidden = false;
    if (inFlight) {
      return;
    }
    void runFetch().then(scheduleAfterFetch);
  }

  if (env) {
    env.addVisibilityChangeListener(handleVisibilityChange);
  }

  return {
    start() {
      if (disposed || !env) {
        return;
      }
      void runFetch().then(scheduleAfterFetch);
    },
    async refreshNow() {
      if (disposed) {
        return;
      }
      if (inFlight) {
        return;
      }
      await runFetch();
      scheduleAfterFetch();
    },
    dispose() {
      disposed = true;
      clearTimer();
      if (env) {
        env.removeVisibilityChangeListener(handleVisibilityChange);
      }
    },
    isFetchInFlight() {
      return inFlight;
    },
  };
}
