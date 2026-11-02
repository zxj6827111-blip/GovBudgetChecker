const fs = require('node:fs');
const { spawn } = require('node:child_process');
const path = require('node:path');
const { ensureDevServices } = require('./dev-services.cjs');
const { createSupervisor } = require('./dev-backend-supervisor.cjs');

const projectRoot = path.resolve(__dirname, '..');
const noAuth = process.argv.includes('--noauth');
const venvPython =
  process.platform === 'win32'
    ? path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
    : path.join(projectRoot, '.venv', 'bin', 'python');
const pythonCommand = fs.existsSync(venvPython)
  ? venvPython
  : process.platform === 'win32'
    ? 'python.exe'
    : 'python3';

function loadRootEnv() {
  try {
    const content = fs.readFileSync(path.join(projectRoot, '.env'), 'utf8');
    const values = {};
    for (const line of content.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
      if (match) {
        values[match[1]] = match[2].trim().replace(/^['"]|['"]$/g, '');
      }
    }
    return values;
  } catch {
    return {};
  }
}

const rootEnv = loadRootEnv();
const env = {
  ...Object.fromEntries(
    Object.entries(rootEnv).filter(([key]) => process.env[key] === undefined),
  ),
  ...process.env,
  GOVBUDGET_AUTH_ENABLED: noAuth
    ? 'false'
    : process.env.GOVBUDGET_AUTH_ENABLED || rootEnv.GOVBUDGET_AUTH_ENABLED || 'true',
  GOVBUDGET_RATE_LIMIT:
    process.env.GOVBUDGET_RATE_LIMIT || rootEnv.GOVBUDGET_RATE_LIMIT || '2000',
};
const backendPort = Number(env.GOVBUDGET_BACKEND_PORT || rootEnv.GOVBUDGET_BACKEND_PORT || '18743');

env.GOVBUDGET_API_KEY =
  process.env.GOVBUDGET_API_KEY ||
  rootEnv.GOVBUDGET_API_KEY ||
  process.env.BACKEND_API_KEY ||
  rootEnv.BACKEND_API_KEY ||
  '';

// 日志必须落盘：进程挂死时，唯一的线索就是重启前后的 stderr，
// 而继承自终端的 stdout 会随终端消失一起丢掉。
const logPath = path.resolve(
  projectRoot,
  env.GOVBUDGET_BACKEND_LOG || path.join('logs', `backend_${backendPort}.log`),
);

// 终端被关掉后写入会触发 EPIPE，不应因此让 supervisor 崩掉。
process.stdout.on('error', () => {});
process.stderr.on('error', () => {});

// 整份日志共用同一个追加流：子进程输出与监督层判定按时间顺序落在一起。
let logSink = null;
function getLogSink() {
  if (logSink) return logSink;
  fs.mkdirSync(path.dirname(logPath), { recursive: true });
  logSink = fs.createWriteStream(logPath, { flags: 'a' });
  logSink.on('error', () => {});
  return logSink;
}

function spawnBackend() {
  return spawn(
    pythonCommand,
    [
      '-m', 'uvicorn', 'api.main:app', '--reload',
      // 只监听后端源码：默认监听整个仓库时，基准脚本/测试/临时脚本（.bench、tests、
      // scratch 等）一写 .py 就触发重启，队列里正在跑的分析会被整批取消
      // （pipeline_cancelled）。后端代码只在 api/ 与 src/ 下。
      '--reload-dir', 'api', '--reload-dir', 'src',
      '--port', String(backendPort),
    ],
    {
      cwd: projectRoot,
      env,
      // 独立进程组：uvicorn 的 supervisor 与 worker 需整组终止，否则孤儿 worker 会继续占端口。
      detached: process.platform !== 'win32',
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
}

function attachLogging(child) {
  const sink = getLogSink();
  sink.write(
    `\n=== ${new Date().toISOString()} backend start port=${backendPort} pid=${child.pid ?? 'n/a'} ===\n`,
  );
  let warnedPort = false;
  const forward = (stream, target) => {
    if (!stream) return;
    stream.setEncoding('utf8');
    stream.on('data', (chunk) => {
      sink.write(chunk);
      target.write(chunk);
      // 独立进程组下，上一轮残留的 worker 可能仍占着端口，给出可直接执行的排查命令。
      if (!warnedPort && chunk.includes('Address already in use')) {
        warnedPort = true;
        log(
          `端口 ${backendPort} 仍被占用，请先确认残留进程：lsof -nP -iTCP:${backendPort} -sTCP:LISTEN`,
        );
      }
    });
  };
  forward(child.stdout, process.stdout);
  forward(child.stderr, process.stderr);
}

// 监督层的关键判定（挂死、强制重启）必须和子进程输出写进同一份日志，
// 否则事后只剩“重启过一次”的痕迹，看不到原因。用同步追加，避免退出时丢缓冲。
const log = (message) => {
  const line = `${message}\n`;
  process.stdout.write(line);
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, line);
  } catch {
    /* 日志写入失败不应影响服务 */
  }
};

const supervisor = createSupervisor({
  port: backendPort,
  spawnBackend,
  attach: attachLogging,
  log,
});

let shutDown = false;
async function shutdown(code) {
  if (shutDown) return;
  shutDown = true;
  await supervisor.stop();
  process.exit(code);
}

process.on('SIGINT', () => void shutdown(130));
process.on('SIGTERM', () => void shutdown(143));

ensureDevServices(env)
  .then(async () => {
    if (shutDown) return;
    log(`后端日志：${path.relative(projectRoot, logPath)}（端口 ${backendPort}）`);
    const result = await supervisor.run();
    if (result.outcome === 'exited') {
      const reason = result.signal ? `信号 ${result.signal}` : `退出码 ${result.code ?? 1}`;
      log(`后端进程已退出（${reason}），请查看上方回溯与日志 ${logPath}`);
      process.exit(result.signal ? 1 : (result.code ?? 1));
    }
    if (result.outcome === 'startup-timeout' || result.outcome === 'startup-failed') {
      log(`后端未能启动，请查看日志 ${logPath}`);
      process.exit(1);
    }
    if (result.outcome === 'give-up') {
      log(`自动恢复已放弃，请查看日志 ${logPath}`);
      process.exit(1);
    }
  })
  .catch((error) => {
    process.stderr.write(`[services] ${error.message}\n`);
    process.exitCode = 1;
  });
