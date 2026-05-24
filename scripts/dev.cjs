const { spawn } = require('node:child_process');
const path = require('node:path');

function normalizeWindowsPath(value) {
  if (!value) {
    return value;
  }

  return value
    .replace(/^Microsoft\.PowerShell\.Core\\FileSystem::/, '')
    .replace(/^\\\\\?\\/, '');
}

const packageJsonDir = process.env.npm_package_json
  ? path.dirname(process.env.npm_package_json)
  : '';
const projectRoot = normalizeWindowsPath(
  packageJsonDir || process.env.INIT_CWD || process.cwd(),
);
const frontendCwd = path.join(projectRoot, 'app');
const pythonCommand = process.platform === 'win32' ? 'python.exe' : 'python';
const frontendCommand = process.platform === 'win32' ? 'cmd.exe' : 'npm';
const frontendArgs =
  process.platform === 'win32'
    ? ['/d', '/s', '/c', 'npm run dev']
    : ['run', 'dev'];

const sharedEnv = {
  ...process.env,
  GOVBUDGET_AUTH_ENABLED: process.env.GOVBUDGET_AUTH_ENABLED || 'true',
  GOVBUDGET_API_KEY: process.env.GOVBUDGET_API_KEY || 'dev-local-key',
  GOVBUDGET_RATE_LIMIT: process.env.GOVBUDGET_RATE_LIMIT || '2000',
};

const children = [];
let shuttingDown = false;

function prefixStream(stream, prefix, target) {
  let buffer = '';

  stream.on('data', (chunk) => {
    buffer += chunk.toString();
    const lines = buffer.split(/\r?\n/);
    buffer = lines.pop() || '';

    for (const line of lines) {
      target.write(`[${prefix}] ${line}\n`);
    }
  });

  stream.on('end', () => {
    if (buffer) {
      target.write(`[${prefix}] ${buffer}\n`);
      buffer = '';
    }
  });
}

function stopChildren(exitCode = 0) {
  if (shuttingDown) {
    return;
  }

  shuttingDown = true;
  for (const child of children) {
    if (!child.killed) {
      child.kill('SIGTERM');
    }
  }

  setTimeout(() => process.exit(exitCode), 100);
}

function launch(name, command, args, options) {
  const child = spawn(command, args, {
    cwd: projectRoot,
    env: sharedEnv,
    stdio: ['inherit', 'pipe', 'pipe'],
    ...options,
  });

  children.push(child);
  prefixStream(child.stdout, name, process.stdout);
  prefixStream(child.stderr, name, process.stderr);

  child.on('exit', (code, signal) => {
    if (shuttingDown) {
      return;
    }

    if (signal) {
      process.stderr.write(`[${name}] exited with signal ${signal}\n`);
      stopChildren(1);
      return;
    }

    if (code !== 0) {
      process.stderr.write(`[${name}] exited with code ${code}\n`);
      stopChildren(code || 1);
    }
  });

  child.on('error', (error) => {
    process.stderr.write(`[${name}] failed to start: ${error.message}\n`);
    stopChildren(1);
  });

  return child;
}

launch('frontend', frontendCommand, frontendArgs, { cwd: frontendCwd });
launch('backend', pythonCommand, ['-m', 'uvicorn', 'api.main:app', '--reload', '--port', '8000']);

process.on('SIGINT', () => stopChildren(0));
process.on('SIGTERM', () => stopChildren(0));
