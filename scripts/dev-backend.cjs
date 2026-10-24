const fs = require('node:fs');
const { spawn } = require('node:child_process');
const path = require('node:path');
const { ensureDevServices } = require('./dev-services.cjs');

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
const backendPort = env.GOVBUDGET_BACKEND_PORT || '18743';

env.GOVBUDGET_API_KEY =
  process.env.GOVBUDGET_API_KEY ||
  rootEnv.GOVBUDGET_API_KEY ||
  process.env.BACKEND_API_KEY ||
  rootEnv.BACKEND_API_KEY ||
  '';

let child;
function startBackend() {
  if (stopping) return;
  child = spawn(
    pythonCommand,
    ['-m', 'uvicorn', 'api.main:app', '--reload', '--port', backendPort],
    { cwd: projectRoot, env, stdio: 'inherit' },
  );
  child.on('error', (error) => {
    process.stderr.write(`[backend] failed to start: ${error.message}\n`);
    process.exitCode = 1;
  });
  child.on('exit', (code, signal) => {
    process.exitCode = signal ? (signal === 'SIGINT' ? 130 : 1) : (code ?? 1);
  });
}

let stopping = false;
function forwardSignal(signal) {
  if (stopping) return;
  stopping = true;
  if (child) child.kill(signal);
  else process.exit(signal === 'SIGINT' ? 130 : 0);
}

process.on('SIGINT', () => forwardSignal('SIGINT'));
process.on('SIGTERM', () => forwardSignal('SIGTERM'));

ensureDevServices(env).then(startBackend).catch((error) => {
  process.stderr.write(`[services] ${error.message}\n`);
  process.exitCode = 1;
});
