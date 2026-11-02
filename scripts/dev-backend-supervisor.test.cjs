const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { createSupervisor } = require('./dev-backend-supervisor.cjs');

// 假子进程：只有 exitCode/signalCode 变化才算“已退出”，与 Node 的 ChildProcess 一致。
function fakeChild(pid = 4000) {
  const child = new EventEmitter();
  child.pid = pid;
  child.exitCode = null;
  child.signalCode = null;
  return child;
}

function finish(child, signal = 'SIGKILL', code = null) {
  child.signalCode = signal;
  child.exitCode = signal ? null : code;
  child.emit('exit', code, signal);
}

// 用可控时钟 + setImmediate 让出事件循环，避免假 sleep 造成微任务饥饿。
function harness(overrides = {}) {
  const clock = { value: 0 };
  const spawned = [];
  const signals = [];
  const logs = [];
  const sleep = (ms) =>
    new Promise((resolve) => {
      clock.value += ms;
      setImmediate(resolve);
    });
  const probeAnswers = [];
  const probe =
    overrides.probe ||
    (async () => (probeAnswers.length ? probeAnswers.shift() : false));
  const supervisor = createSupervisor({
    port: 18743,
    spawnBackend:
      overrides.spawnBackend ||
      (() => {
        const child = fakeChild(4000 + spawned.length);
        spawned.push(child);
        return child;
      }),
    probe,
    sleep,
    now: () => clock.value,
    log: (message) => logs.push(message),
    kill:
      overrides.kill ||
      ((child, signal) => {
        signals.push(signal);
        finish(child, signal);
      }),
    settings: { readyTimeoutMs: 10000, startupProbeIntervalMs: 1000, ...overrides.settings },
  });
  return { supervisor, spawned, signals, logs, probeAnswers, clock };
}

test('就绪后持续健康，不触发任何重启', async () => {
  const holder = {};
  let probeCount = 0;
  // 假时钟下真实定时器会被 setImmediate 饿死，因此用探测次数驱动停止，
  // 让用例完全确定。
  const ctx = harness({
    probe: async () => {
      probeCount += 1;
      if (probeCount > 3) void holder.supervisor.stop();
      return true;
    },
  });
  holder.supervisor = ctx.supervisor;
  assert.deepEqual(await ctx.supervisor.run(), { outcome: 'stopped' });
  assert.equal(ctx.spawned.length, 1);
  assert.deepEqual(ctx.signals, ['SIGTERM'], '停止时只应发一次 SIGTERM');
  assert.ok(!ctx.logs.some((line) => line.includes('强制重启')), '健康状态下不应重启');
});

test('启动阶段始终不健康时按超时放弃，而不是无限等待', async () => {
  const ctx = harness();
  assert.deepEqual(await ctx.supervisor.run(), { outcome: 'startup-timeout' });
  assert.equal(ctx.spawned.length, 1);
});

test('进程存活但健康检查持续失败时，SIGTERM 无效会升级为 SIGKILL', async () => {
  const ctx = harness({
    // 模拟真实故障：SIGTERM 打不动卡在 join 上的 uvicorn，只有 SIGKILL 生效。
    kill: (child, signal) => {
      ctx.signals.push(signal);
      if (signal === 'SIGKILL') finish(child, signal);
    },
    settings: { maxRestarts: 0, unhealthyGraceMs: 5000, termGraceMs: 1000 },
  });
  ctx.probeAnswers.push(true); // 启动就绪
  const result = await ctx.supervisor.run();
  assert.deepEqual(result, { outcome: 'give-up' });
  assert.deepEqual(ctx.signals, ['SIGTERM', 'SIGKILL']);
  assert.ok(
    ctx.logs.some((line) => line.includes('进程存活但不再处理请求')),
    '应记录挂死判定原因',
  );
  assert.ok(
    ctx.logs.some((line) => line.includes('SIGKILL')),
    '应记录升级强杀',
  );
});

test('挂死后自动重启，重启次数用尽才放弃', async () => {
  const ctx = harness({ settings: { maxRestarts: 1, unhealthyGraceMs: 5000, termGraceMs: 1000 } });
  // gate1 就绪 → watch1 失败到超过 grace 触发挂死 → gate2 就绪 → watch2 再次失败，
  // 此时重启次数已超过 maxRestarts，应停止自动恢复。
  ctx.probeAnswers.push(true, false, true, false);
  const result = await ctx.supervisor.run();
  assert.deepEqual(result, { outcome: 'give-up' });
  assert.equal(ctx.spawned.length, 2, '应重启过一次');
  assert.ok(ctx.logs.some((line) => line.includes('执行第 1 次后端强制重启')));
});

test('稳定运行超过阈值后清零重启计数，不会因历史故障提前放弃', async () => {
  const ctx = harness({
    settings: {
      maxRestarts: 1,
      unhealthyGraceMs: 5000,
      termGraceMs: 1000,
      resetRestartsAfterMs: 20000,
    },
  });
  // gate1 就绪 → 挂死 → 重启1 → gate2 就绪 → 健康 25 秒（触发计数清零）
  // → 再次挂死 → 重启2（若计数未清零，这里会因超过 maxRestarts 而放弃）
  // → gate3 就绪 → 再次挂死 → 此时计数为 2，才放弃。
  ctx.probeAnswers.push(true, false);
  ctx.probeAnswers.push(true, true, true, true, true, true, false);
  ctx.probeAnswers.push(true, false);
  const result = await ctx.supervisor.run();
  assert.deepEqual(result, { outcome: 'give-up' });
  assert.equal(ctx.spawned.length, 3, '应重启过两次，说明计数被清零');
  assert.ok(ctx.logs.some((line) => line.includes('重置强制重启计数')));
});

test('后端自然退出时如实上报退出码，不做自动重启', async () => {
  const child = fakeChild(4100);
  const ctx = harness({
    spawnBackend: () => {
      setImmediate(() => finish(child, null, 2));
      return child;
    },
  });
  assert.deepEqual(await ctx.supervisor.run(), { outcome: 'exited', code: 2, signal: null });
});
