const net = require('node:net');
const { execFile } = require('node:child_process');
const { promisify } = require('node:util');

const runFile = promisify(execFile);
const enabled = (value) => /^(true|1|yes|on)$/i.test(String(value || ''));

function localServiceTargets(env) {
  const targets = [];
  const add = (value, port, container, kind) => {
    if (!value) return;
    let url;
    try { url = new URL(value); } catch { return; }
    const host = url.hostname.replace(/^\[|\]$/g, '');
    if (!['localhost', '127.0.0.1', '::1'].includes(host)) return;
    const defaultPort = kind === 'OCR' ? (url.protocol === 'https:' ? 443 : 80) : port;
    if (Number(url.port || defaultPort) !== port) return;
    targets.push({ host, port, container, kind, healthUrl: `${url.origin}/health` });
  };
  add(env.DATABASE_URL, 5432, 'fiscal_postgres', 'PostgreSQL');
  if (enabled(env.OCR_ENABLED)) {
    add(env.OCR_SERVICE_URL || 'http://127.0.0.1:9010', 9010, 'fiscal_ocr', 'OCR');
  }
  return targets;
}

function tcpReachable(target, timeoutMs = 2500) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: target.host, port: target.port });
    const finish = (ready) => { socket.destroy(); resolve(ready); };
    socket.setTimeout(timeoutMs);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
  });
}

// OCR 的 /health 是串行调用的，正在处理批量识别时会排不上队。
// 因此必须把「忙」和「没起来」分开判断，否则启动会被误拦。
async function ocrHealthState(target) {
  try {
    const response = await fetch(target.healthUrl, { signal: AbortSignal.timeout(2500) });
    if (!response.ok) return 'unreachable';
    return (await response.json()).models_ready === true ? 'healthy' : 'not-ready';
  } catch {
    return 'unreachable';
  }
}

async function serviceAvailable(target) {
  if (target.kind !== 'OCR') return tcpReachable(target);

  const state = await ocrHealthState(target);
  if (state === 'healthy') return true;
  // 健康检查明确回了“模型没就绪”，那是真的要重启容器。
  if (state === 'not-ready') return false;

  // /health 超时或报错，但端口通 → 服务进程在，只是忙或被别的任务占住。
  // 此时按就绪处理并给出提示，比空转等 60 秒后报错有用。
  if (await tcpReachable(target)) {
    return {
      ready: true,
      warning: `OCR 端口可达但 /health 2.5 秒内无响应（可能正在跑批量识别），先按就绪处理；若分析报错请检查 fiscal_ocr 负载。`,
    };
  }
  return false;
}

async function startContainer(target) {
  try {
    await runFile('docker', ['start', target.container], { timeout: 15000 });
  } catch {
    throw new Error(`本地 ${target.kind} 不可用。请启动 Docker 和已有容器 ${target.container}；首次部署请按 README 初始化服务。`);
  }
}

// 探测结果既可以是布尔值，也可以是 { ready, warning }，便于把“能启动但有风险”的情况说清楚。
function normalizeProbeResult(result) {
  if (result && typeof result === 'object') {
    return { ready: result.ready === true, warning: result.warning };
  }
  return { ready: result === true };
}

async function waitForService(target, probe) {
  const deadline = Date.now() + 60000;
  do {
    if (normalizeProbeResult(await probe(target)).ready) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  } while (Date.now() < deadline);
  return false;
}

async function ensureDevServices(env, options = {}) {
  if (String(env.DEV_START_LOCAL_SERVICES).toLowerCase() === 'false') return;
  const { probe = serviceAvailable, start = startContainer, wait = waitForService,
    log = (message) => process.stdout.write(`${message}\n`) } = options;
  for (const target of localServiceTargets(env)) {
    const current = normalizeProbeResult(await probe(target));
    if (current.ready) {
      if (current.warning) log(`[services] ${current.warning}`);
      continue;
    }
    log(`[services] 正在恢复 ${target.kind} (${target.container})…`);
    await start(target);
    if (!await wait(target, probe)) {
      throw new Error(`${target.kind} 启动后未在 60 秒内就绪，请检查 ${target.container} 日志。`);
    }
    log(`[services] ${target.kind} 已就绪`);
  }
}

module.exports = { ensureDevServices, localServiceTargets, serviceAvailable };
