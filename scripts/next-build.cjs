const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const projectRoot = path.resolve(__dirname, "..");
const appDir = path.join(projectRoot, "app");
const nextCacheDir = path.join(appDir, ".next");
const skipClean = process.env.GBC_SKIP_NEXT_CACHE_CLEAR === "1";

if (!skipClean) {
  try {
    fs.rmSync(nextCacheDir, { recursive: true, force: true });
    process.stdout.write("[next-build] cleared app/.next cache\n");
  } catch (error) {
    process.stderr.write(`[next-build] failed to clear cache: ${error.message}\n`);
  }
}

const nextBin = require.resolve("next/dist/bin/next", { paths: [appDir] });
const result = spawnSync(process.execPath, [nextBin, "build"], {
  cwd: appDir,
  env: process.env,
  stdio: "inherit",
});

if (result.error) {
  process.stderr.write(`[next-build] failed to start: ${result.error.message}\n`);
  process.exit(1);
}

process.exit(result.status ?? 1);
