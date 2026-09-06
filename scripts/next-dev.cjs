const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const appDir = process.cwd();
const projectRoot = path.resolve(appDir, "..");
const nextCacheDir = path.join(appDir, ".next");
const skipClean = process.env.GBC_SKIP_NEXT_CACHE_CLEAR === "1";

const ENV_KEY_PREFIXES = ["GOVBUDGET_", "BACKEND_", "DATABASE_", "AI_EXTRACTOR_", "LOCAL_DATA_", "LOCAL_UPLOADS_", "USER_SESSION_", "READY_", "MAX_UPLOAD_", "GBC_", "GOVBUDGET_ORG_"];

function loadRootEnv(rootDir) {
  const envPath = path.join(rootDir, ".env");
  let content = "";

  try {
    content = fs.readFileSync(envPath, "utf8");
  } catch {
    return {};
  }

  const values = {};
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match) {
      continue;
    }

    const [, key, rawValue] = match;
    if (!ENV_KEY_PREFIXES.some((prefix) => key.startsWith(prefix))) {
      continue;
    }

    values[key] = rawValue.trim().replace(/^["']|["']$/g, "");
  }
  return values;
}

const rootEnv = loadRootEnv(projectRoot);

Object.entries(rootEnv).forEach(([key, value]) => {
  if (process.env[key] === undefined) {
    process.env[key] = value;
  }
});

process.env.GOVBUDGET_AUTH_ENABLED =
  process.env.GOVBUDGET_AUTH_ENABLED || "true";
// 密钥统一从根 .env 读取（GOVBUDGET_API_KEY / BACKEND_API_KEY），不再保留
// 'dev-local-key' 硬编码兜底——与后端安全模块的 fail-closed 行为对齐：
// 认证开启但 key 缺失时让请求带空 key 失败并暴露配置问题，而不是静默
// 用错误 key 运行（GPT5.6 P1-5：分别启动前后端时旧兜底导致 403）。
// 注意：rootEnv 在上方已注入 process.env（未定义时），这里再兜一次
// 是为了覆盖 process.env 里存在空字符串的场景。
if (!process.env.GOVBUDGET_API_KEY) {
  process.env.GOVBUDGET_API_KEY =
    rootEnv.GOVBUDGET_API_KEY || rootEnv.BACKEND_API_KEY || "";
}
process.env.GOVBUDGET_RATE_LIMIT = process.env.GOVBUDGET_RATE_LIMIT || "2000";

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

let shuttingDown = false;

function stopChildTree(signal) {
  if (shuttingDown) return;
  shuttingDown = true;

  if (process.platform === "win32" && child.pid) {
    const { spawnSync } = require("node:child_process");
    spawnSync("taskkill.exe", ["/pid", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
  } else if (!child.killed) {
    child.kill(signal);
  }

  setTimeout(() => process.exit(signal === "SIGINT" ? 130 : 0), 2000);
}

process.on("SIGINT", () => stopChildTree("SIGINT"));
process.on("SIGTERM", () => stopChildTree("SIGTERM"));

child.on("exit", (code, signal) => {
  if (shuttingDown) {
    process.exit(0);
    return;
  }
  if (signal) {
    process.exit(signal === "SIGINT" ? 130 : 1);
    return;
  }
  process.exit(code ?? 0);
});
