const http = require('node:http');
const { spawn } = require('node:child_process');

// 本项目后端冷启动约 7 秒，加任务缓存预热约 6 秒，因此就绪判定给足 60 秒。
//
// unhealthyGraceMs 必须显著长于一次 uvicorn 热重载的完整周期（最长约
// readyTimeoutMs）：改进 .py 文件触发的正常重载期间 /health 会连续失败十几秒，
// 若判定窗口短于重载周期，正常开发会被误判为挂死并反复强制重启。
// 真实挂死（supervisor 卡在 process.join 上）会无限期不恢复，90 秒足够区分。
const DEFAULT_SETTINGS = Object.freeze({
  readyTimeoutMs: 60000,
  startupProbeIntervalMs: 1000,
  probeIntervalMs: 5000,
  unhealthyGraceMs: 90000,
  termGraceMs: 5000,
  restartDelayMs: 2000,
  maxRestarts: 3,
  // 稳定运行足够久后清零重启计数：maxRestarts 约束的是“当前这次故障能否自愈”，
  // 而不是开发者在一天里累计遇到几次故障。
  resetRestartsAfterMs: 300000,
});

function probeHealth(port, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const request = http.request(
      { host: '127.0.0.1', port, path: '/health', method: 'GET', timeout: timeoutMs, agent: false },
      (response) => {
        response.resume();
        resolve(response.statusCode === 200);
      },
    );
    // 挂死的 uvicorn 会先完成 TCP 握手再直接断连，表现为 error/reset，
    // 与单纯的就绪延迟一样按“不健康”处理。
    request.on('timeout', () => {
      request.destroy();
      resolve(false);
    });
    request.on('error', () => resolve(false));
    request.end();
  });
}

const isRunning = (child) => Boolean(child) && child.exitCode === null && child.signalCode === null;

// uvicorn --reload 的 supervisor 与真正干活的 worker 是两个进程；只杀 supervisor
// 会留下孤儿 worker 继续占端口，所以整组一起发信号。
function signalTree(child, signal) {
  if (!child || !child.pid) return;
  if (process.platform === 'win32') {
    if (signal === 'SIGKILL') {
      spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { stdio: 'ignore' });
      return;
    }
    try {
      child.kill(signal);
    } catch {
      /* 进程可能已退出 */
    }
    return;
  }
  try {
    process.kill(-child.pid, signal);
  } catch {
    try {
      child.kill(signal);
    } catch {
      /* 进程可能已退出 */
    }
  }
}

function createSupervisor(options) {
  const {
    port,
    spawnBackend,
    attach = () => {},
    probe = () => probeHealth(port),
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    // 便于测试注入可控时钟，生产环境保持真实时间。
    now = () => Date.now(),
    log = (message) => process.stdout.write(`${message}\n`),
    kill = signalTree,
    settings = {},
  } = options;
  const config = { ...DEFAULT_SETTINGS, ...settings };

  let child = null;
  let stopping = false;
  let restarts = 0;
  let exitState = null;

  async function waitForReady() {
    const deadline = now() + config.readyTimeoutMs;
    while (now() < deadline) {
      if (stopping) return 'stopped';
      if (exitState) return 'exited';
      if (await probe()) return 'ready';
      await sleep(config.startupProbeIntervalMs);
    }
    return 'startup-timeout';
  }

  async function killTree() {
    if (!isRunning(child)) return;
    kill(child, 'SIGTERM');
    await waitForExit(config.termGraceMs);
    if (!isRunning(child)) return;
    // 卡死在 join 上的 uvicorn 不响应 SIGTERM，只能 SIGKILL。
    log('后端 SIGTERM 未能结束进程，升级为 SIGKILL');
    kill(child, 'SIGKILL');
    await waitForExit(config.termGraceMs);
  }

  async function waitForExit(timeoutMs) {
    const deadline = now() + timeoutMs;
    while (now() < deadline && isRunning(child)) {
      await sleep(200);
    }
  }

  async function watch() {
    let lastHealthyAt = now();
    let healthyStreakStart = null;
    while (!stopping) {
      await sleep(config.probeIntervalMs);
      if (stopping) return { outcome: 'stopped' };
      if (exitState) return { outcome: 'exited', ...exitState };
      if (await probe()) {
        lastHealthyAt = now();
        if (healthyStreakStart === null) healthyStreakStart = lastHealthyAt;
        if (restarts > 0 && lastHealthyAt - healthyStreakStart >= config.resetRestartsAfterMs) {
          log('后端已稳定运行，重置强制重启计数');
          restarts = 0;
        }
        continue;
      }
      healthyStreakStart = null;
      const silentMs = now() - lastHealthyAt;
      if (silentMs < config.unhealthyGraceMs) continue;
      log(
        `后端健康检查已连续 ${Math.round(silentMs / 1000)} 秒无响应：进程存活但不再处理请求，准备强制重启`,
      );
      return { outcome: 'hung' };
    }
    return { outcome: 'stopped' };
  }

  async function startOnce() {
    exitState = null;
    try {
      child = spawnBackend();
    } catch (error) {
      log(`后端启动失败：${error.message}`);
      return { outcome: 'startup-failed' };
    }
    child.once('exit', (code, signal) => {
      exitState = { code, signal };
    });
    attach(child);
    const state = await waitForReady();
    if (state === 'ready') return { outcome: 'ready' };
    // 启动阶段就退出时要把退出码带上，否则调用方无法如实上报真实原因。
    if (state === 'exited') return { outcome: 'exited', ...exitState };
    return { outcome: state };
  }

  async function run() {
    for (;;) {
      const started = await startOnce();
      if (started.outcome !== 'ready') {
        if (started.outcome === 'startup-timeout') {
          log(`等待后端就绪超过 ${Math.round(config.readyTimeoutMs / 1000)} 秒，放弃启动`);
        }
        await killTree();
        return started;
      }
      const watched = await watch();
      if (watched.outcome !== 'hung') return watched;

      restarts += 1;
      if (restarts > config.maxRestarts) {
        log(`已强制重启 ${config.maxRestarts} 次仍无法恢复，停止自动恢复`);
        await killTree();
        return { outcome: 'give-up' };
      }
      log(`执行第 ${restarts} 次后端强制重启…`);
      await killTree();
      await sleep(config.restartDelayMs);
    }
  }

  async function stop() {
    if (stopping) return;
    stopping = true;
    await killTree();
  }

  return { run, stop };
}

module.exports = {
  DEFAULT_SETTINGS,
  createSupervisor,
  probeHealth,
  signalTree,
};
