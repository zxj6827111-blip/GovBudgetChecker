const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const appDir = process.cwd();
const nextCacheDir = path.join(appDir, ".next");
const skipClean = process.env.GBC_SKIP_NEXT_CACHE_CLEAR === "1";

if (!skipClean) {
  try {
    fs.rmSync(nextCacheDir, { recursive: true, force: true });
    process.stdout.write("[next-dev] cleared app/.next cache\n");
  } catch (error) {
    process.stderr.write(`[next-dev] failed to clear cache: ${error.message}\n`);
  }
}

const nextBin = require.resolve("next/dist/bin/next", { paths: [appDir] });
const child = spawn(process.execPath, [nextBin, "dev"], {
  cwd: appDir,
  env: process.env,
  stdio: "inherit",
});

function forwardSignal(signal) {
  if (!child.killed) {
    child.kill(signal);
  }
}

process.on("SIGINT", () => forwardSignal("SIGINT"));
process.on("SIGTERM", () => forwardSignal("SIGTERM"));

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});

