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

async function serviceAvailable(target) {
  if (target.kind === 'OCR') {
    try {
      const response = await fetch(target.healthUrl, { signal: AbortSignal.timeout(2500) });
      return response.ok && (await response.json()).models_ready === true;
    } catch { return false; }
  }
  return new Promise((resolve) => {
    const socket = net.createConnection({ host: target.host, port: target.port });
    const finish = (ready) => { socket.destroy(); resolve(ready); };
    socket.setTimeout(2500);
    socket.once('connect', () => finish(true));
    socket.once('timeout', () => finish(false));
    socket.once('error', () => finish(false));
  });
}

async function startContainer(target) {
  try {
    await runFile('docker', ['start', target.container], { timeout: 15000 });
  } catch {
    throw new Error(`本地 ${target.kind} 不可用。请启动 Docker 和已有容器 ${target.container}；首次部署请按 README 初始化服务。`);
  }
}

async function waitForService(target, probe) {
  const deadline = Date.now() + 60000;
  do {
    if (await probe(target)) return true;
    await new Promise((resolve) => setTimeout(resolve, 500));
  } while (Date.now() < deadline);
  return false;
}

async function ensureDevServices(env, options = {}) {
  if (String(env.DEV_START_LOCAL_SERVICES).toLowerCase() === 'false') return;
  const { probe = serviceAvailable, start = startContainer, wait = waitForService,
    log = (message) => process.stdout.write(`${message}\n`) } = options;
  for (const target of localServiceTargets(env)) {
    if (await probe(target)) continue;
    log(`[services] 正在恢复 ${target.kind} (${target.container})…`);
    await start(target);
    if (!await wait(target, probe)) {
      throw new Error(`${target.kind} 启动后未在 60 秒内就绪，请检查 ${target.container} 日志。`);
    }
    log(`[services] ${target.kind} 已就绪`);
  }
}

module.exports = { ensureDevServices, localServiceTargets };
