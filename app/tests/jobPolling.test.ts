import assert from "node:assert/strict";

import type { JobSummaryRecord } from "../lib/uiAdapters";
import {
  ACTIVE_POLL_INTERVAL_MS,
  WAITING_POLL_INTERVAL_MS,
  createJobPollingController,
  resolvePollingDecision,
  type JobPollingEnvironment,
  type PollingDecision,
} from "../lib/jobPolling";

// 本文件测试修复 1（任务状态轮询）的纯逻辑层：轮询决策 + 轮询控制器。
// 该层承载的红线："全部终态必须停止轮询"、"页面隐藏必须暂停"、"卸载必须清理
// 定时器"、"请求失败不得中断轮询"、"同一时刻不得叠积请求"。
// 说明：jiti 1.x 编译到 CJS，不支持顶层 await，因此异步场景统一收进 main()。

// ---------------------------------------------------------------------------
// 假时钟环境：手动驱动 setTimeout 队列与 document.hidden
// ---------------------------------------------------------------------------

interface FakeTimerEnv extends JobPollingEnvironment {
  /** 触发所有到期定时器（推进假时钟）。 */
  advance: (ms: number) => void;
  /** 当前挂起（未触发）的定时器数量——卸载清理断言的依据。 */
  pendingCount: () => number;
  setHidden: (hidden: boolean) => void;
  /** 手动触发 visibilitychange 监听器。 */
  fireVisibilityChange: () => void;
}

function createFakeTimerEnv(initialHidden = false): FakeTimerEnv {
  interface PendingTimer {
    id: number;
    at: number;
    callback: () => void;
  }
  let now = 0;
  let nextId = 1;
  const pending = new Map<number, PendingTimer>();
  let hidden = initialHidden;
  const visibilityListeners: Array<() => void> = [];

  return {
    setTimeoutFn(callback, ms) {
      const id = nextId++;
      pending.set(id, { id, at: now + ms, callback });
      return id;
    },
    clearTimeoutFn(handle) {
      pending.delete(Number(handle));
    },
    isDocumentHidden: () => hidden,
    addVisibilityChangeListener(callback) {
      visibilityListeners.push(callback);
    },
    removeVisibilityChangeListener(callback) {
      const index = visibilityListeners.indexOf(callback);
      if (index >= 0) {
        visibilityListeners.splice(index, 1);
      }
    },
    advance(ms) {
      const deadline = now + ms;
      // 循环触发：定时器回调里可能再排新定时器（同一时刻）。
      for (;;) {
        const due = [...pending.values()]
          .filter((timer) => timer.at <= deadline)
          .sort((a, b) => a.at - b.at);
        if (due.length === 0) {
          break;
        }
        const timer = due[0];
        pending.delete(timer.id);
        now = Math.max(now, timer.at);
        timer.callback();
      }
      now = deadline;
    },
    pendingCount: () => pending.size,
    setHidden(value) {
      hidden = value;
    },
    fireVisibilityChange() {
      for (const listener of [...visibilityListeners]) {
        listener();
      }
    },
  };
}

/** 跑完当前所有已 settle 的微任务（fetch 链是 async，需要 flush 后再断言）。 */
async function flushMicrotasks(times = 5): Promise<void> {
  for (let i = 0; i < times; i++) {
    await Promise.resolve();
  }
}

function job(id: string, status: string): JobSummaryRecord {
  return { job_id: id, status };
}

async function main(): Promise<void> {
  // ---------------------------------------------------------------------------
  // resolvePollingDecision：决策纯函数
  // ---------------------------------------------------------------------------

  assert.deepEqual(
    resolvePollingDecision(null),
    { kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS },
    "REGRESSION: 尚未拉到数据(null)按活跃处理——未知即活跃，请求失败后仍持续重试",
  );
  assert.deepEqual(resolvePollingDecision(undefined), {
    kind: "active",
    intervalMs: ACTIVE_POLL_INTERVAL_MS,
  });

  assert.equal(
    resolvePollingDecision([]).kind,
    "stop",
    "REGRESSION: 空任务列表必须停止轮询，不得无条件定时打后端",
  );

  const allTerminal: JobSummaryRecord[] = [
    job("a", "done"),
    job("b", "review_required"),
    job("c", "failed"),
    job("d", "error"),
  ];
  assert.equal(
    resolvePollingDecision(allTerminal).kind,
    "stop",
    "REGRESSION: 全部终态必须停止轮询（终态任务不应继续打后端）",
  );

  const withRunning: JobSummaryRecord[] = [
    job("a", "done"),
    job("b", "queued"),
    job("c", "processing"),
  ];
  assert.deepEqual(resolvePollingDecision(withRunning), {
    kind: "active",
    intervalMs: ACTIVE_POLL_INTERVAL_MS,
  });

  assert.deepEqual(
    resolvePollingDecision([job("a", "uploaded")]),
    { kind: "waiting", intervalMs: WAITING_POLL_INTERVAL_MS },
    "REGRESSION: 仅 uploaded（待分析，静止状态）必须大幅降频，不得按活跃频率为历史待分析任务常驻轮询",
  );

  const mixedRunningAndWaiting: JobSummaryRecord[] = [job("a", "uploaded"), job("b", "processing")];
  assert.equal(
    resolvePollingDecision(mixedRunningAndWaiting).kind,
    "active",
    "存在真正在跑的任务时按活跃频率轮询（waiting 只影响纯静止场景）",
  );

  // 未知状态保守按活跃对待（宁可多问后端，不能让未知任务悄悄脱离刷新）。
  assert.equal(resolvePollingDecision([job("a", "some_unknown_state")]).kind, "active");

  // ---------------------------------------------------------------------------
  // createJobPollingController：启动 / 停止 / 隐藏暂停 / 清理 / 防叠积 / 失败续排
  // ---------------------------------------------------------------------------

  // 场景 1：有活跃任务时按 ACTIVE 间隔持续轮询；数据变为全部终态后停止。
  {
    const env = createFakeTimerEnv();
    let decision: PollingDecision = { kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS };
    let fetchCount = 0;
    const controller = createJobPollingController({
      fetcher: async () => {
        fetchCount += 1;
      },
      decide: () => decision,
      env,
    });
    controller.start();
    await flushMicrotasks();
    assert.equal(env.pendingCount(), 1, "活跃决策下应恰好挂着一个下次轮询定时器");
    env.advance(ACTIVE_POLL_INTERVAL_MS);
    await flushMicrotasks();
    assert.equal(env.pendingCount(), 1, "每次到点后应续排下一个定时器（持续轮询）");
    assert.equal(fetchCount, 2, "5 秒后应发生第二次刷新");

    // 数据全部终态：本次 fetch 返回后决策为 stop，不得再排定时器。
    decision = { kind: "stop" };
    env.advance(ACTIVE_POLL_INTERVAL_MS);
    await flushMicrotasks();
    assert.equal(fetchCount, 3, "stop 决策生效前最后一次到点仍会刷新一次");
    assert.equal(
      env.pendingCount(),
      0,
      "REGRESSION: 全部终态后不得再挂任何轮询定时器",
    );
    env.advance(ACTIVE_POLL_INTERVAL_MS * 10);
    await flushMicrotasks();
    assert.equal(fetchCount, 3, "停止后不再发生任何刷新");
    controller.dispose();
  }

  // 场景 2：卸载后定时器被清理（反例：不清理会泄漏）。
  {
    const env = createFakeTimerEnv();
    let fetchCount = 0;
    const controller = createJobPollingController({
      fetcher: async () => {
        fetchCount += 1;
      },
      decide: () => ({ kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS }),
      env,
    });
    controller.start();
    await flushMicrotasks();
    assert.equal(env.pendingCount(), 1);
    controller.dispose();
    assert.equal(
      env.pendingCount(),
      0,
      "REGRESSION: dispose 后不得残留任何定时器（组件卸载泄漏定时器会在后台永久打后端）",
    );
    env.advance(ACTIVE_POLL_INTERVAL_MS * 10);
    await flushMicrotasks();
    assert.equal(fetchCount, 1, "dispose 后到点也不得再触发刷新");
  }

  // 场景 3：页面隐藏时暂停（不排定时器），切回前台恢复并立即补一次刷新。
  {
    const env = createFakeTimerEnv();
    let fetchCount = 0;
    const controller = createJobPollingController({
      fetcher: async () => {
        fetchCount += 1;
      },
      decide: () => ({ kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS }),
      env,
    });
    controller.start();
    await flushMicrotasks();
    assert.equal(fetchCount, 1);

    // 到点时页面已隐藏：本次完成后不得续排。
    env.setHidden(true);
    env.advance(ACTIVE_POLL_INTERVAL_MS);
    await flushMicrotasks();
    assert.equal(fetchCount, 2, "隐藏前已到点的那次轮询照常执行");
    assert.equal(
      env.pendingCount(),
      0,
      "REGRESSION: 页面隐藏后不得继续挂轮询定时器（隐藏期间必须暂停）",
    );

    env.advance(ACTIVE_POLL_INTERVAL_MS * 20);
    await flushMicrotasks();
    assert.equal(fetchCount, 2, "隐藏期间不发生任何刷新");

    // 切回前台：恢复——立即补一次刷新并重新续排。
    env.setHidden(false);
    env.fireVisibilityChange();
    await flushMicrotasks();
    assert.equal(fetchCount, 3, "REGRESSION: 切回前台必须恢复轮询并立即补一次刷新");
    assert.equal(env.pendingCount(), 1, "恢复后重新挂上下次轮询定时器");
    controller.dispose();
  }

  // 场景 4：请求失败不得中断轮询（反例：一次 500 后循环永久停止）。
  {
    const env = createFakeTimerEnv();
    let fetchCount = 0;
    let shouldFail = true;
    const errors: unknown[] = [];
    const controller = createJobPollingController({
      fetcher: async () => {
        fetchCount += 1;
        if (shouldFail) {
          throw new Error("HTTP 500");
        }
      },
      decide: () => ({ kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS }),
      onError: (error) => errors.push(error),
      env,
    });
    controller.start();
    await flushMicrotasks();
    env.advance(ACTIVE_POLL_INTERVAL_MS);
    await flushMicrotasks();
    assert.equal(fetchCount, 2, "失败后的下一个周期仍发生刷新");
    assert.equal(
      errors.length,
      2,
      "REGRESSION: 每次失败（首次加载+重试）都必须通过 onError 上报，不得静默吞掉错误状态",
    );
    assert.ok(errors[0] instanceof Error && /500/.test(String(errors[0])));

    shouldFail = false;
    env.advance(ACTIVE_POLL_INTERVAL_MS);
    await flushMicrotasks();
    assert.equal(fetchCount, 3, "恢复后轮询照常成功");
    controller.dispose();
  }

  // 场景 5：防叠积——上一次请求未返回时不发新请求。
  {
    const env = createFakeTimerEnv();
    let inFlightFetches = 0;
    let maxConcurrent = 0;
    let releaseFetch: (() => void) | null = null;
    const controller = createJobPollingController({
      fetcher: () =>
        new Promise<void>((resolve) => {
          inFlightFetches += 1;
          maxConcurrent = Math.max(maxConcurrent, inFlightFetches);
          releaseFetch = () => {
            inFlightFetches -= 1;
            resolve();
          };
        }),
      decide: () => ({ kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS }),
      env,
    });
    controller.start();
    await flushMicrotasks();
    assert.equal(maxConcurrent, 1);
    assert.ok(releaseFetch);

    // 首个请求还在飞时定时器到点：不得发出第二个请求。
    env.advance(ACTIVE_POLL_INTERVAL_MS);
    await flushMicrotasks();
    assert.equal(
      maxConcurrent,
      1,
      "REGRESSION: 上一次请求未返回时不得再发新请求（防叠积）",
    );
    assert.equal(controller.isFetchInFlight(), true);

    releaseFetch();
    await flushMicrotasks();
    assert.equal(controller.isFetchInFlight(), false);
    assert.equal(env.pendingCount(), 1, "在飞请求返回后应恢复续排");
    controller.dispose();
  }

  // 场景 6：手动刷新（refreshNow）——stop 态下可用且不会恢复定时轮询。
  {
    const env = createFakeTimerEnv();
    let fetchCount = 0;
    const controller = createJobPollingController({
      fetcher: async () => {
        fetchCount += 1;
      },
      decide: () => ({ kind: "stop" }),
      env,
    });
    // 不调用 start：全部终态的页面挂载后决策即 stop，手动刷新是唯一显式恢复入口。
    await controller.refreshNow();
    await flushMicrotasks();
    assert.equal(fetchCount, 1, "stop 决策下手动刷新仍可用");
    assert.equal(env.pendingCount(), 0, "stop 决策下手动刷新后不得恢复定时轮询");
    controller.dispose();
  }

  // 场景 7：dispose 后调用 refreshNow 均为 no-op。
  {
    const env = createFakeTimerEnv();
    let fetchCount = 0;
    const controller = createJobPollingController({
      fetcher: async () => {
        fetchCount += 1;
      },
      decide: () => ({ kind: "active", intervalMs: ACTIVE_POLL_INTERVAL_MS }),
      env,
    });
    controller.dispose();
    await controller.refreshNow();
    await flushMicrotasks();
    assert.equal(fetchCount, 0, "dispose 后手动刷新不得触发请求");
    assert.equal(env.pendingCount(), 0, "dispose 后不得排任何定时器");
  }

  console.log("jobPolling.test: all assertions passed");
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
