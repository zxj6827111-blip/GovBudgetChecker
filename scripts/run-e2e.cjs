const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { spawn, spawnSync } = require("node:child_process");

const projectRoot = path.resolve(__dirname, "..");
const appDir = path.join(projectRoot, "app");
const outputDir = path.join(projectRoot, "output");
const baseURL = process.env.E2E_BASE_URL || "http://127.0.0.1:3000";
const parsedBaseURL = new URL(baseURL);
const readyURL = new URL("/e2e/batch-upload", parsedBaseURL).toString();

// 预热路径：e2e 跑在 `next dev` 上，每个路由**首次请求**才会被编译。
// CI 是冷启动（没有 .next 缓存）且 runner 比开发机慢，首访编译动辄十几秒，
// 会直接吃掉用例自己的超时预算，表现为"点击超时"这种看起来毫无关联的失败
// （实测：同一个用例本机 9.9s 通过，CI 上 90s 超时）。
// 因此在跑用例前把所有被访问的页面各请求一次，把编译成本从用例预算里挪出去。
// 路径来源：e2e/tests/**/*.spec.ts 里的 page.goto()。
const WARMUP_PATHS = [
  "/e2e/batch-upload",
  "/viewer/gbc-ui-demo",
  "/task/job-001",
  "/department/dept-001",
  "/?page=settings",
  "/?page=settings&section=organization",
  "/login",
  "/dev/ui-preview",
];
const forwardedArgs = process.argv.slice(2);
let server = null;
let ownsServer = false;

fs.mkdirSync(outputDir, { recursive: true });
const stdoutFd = fs.openSync(path.join(outputDir, "e2e-webserver.log"), "a");
const stderrFd = fs.openSync(path.join(outputDir, "e2e-webserver.err.log"), "a");

function requestReady(url) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: 2000 }, (response) => {
      response.resume();
      resolve(Boolean(response.statusCode && response.statusCode < 500));
    });
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
  });
}

async function waitForServer(timeoutMs = 120000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await requestReady(readyURL)) return;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`E2E web server did not become ready: ${readyURL}`);
}

function stopServerTree() {
  if (!ownsServer || !server || !server.pid) return;
  if (process.platform === "win32") {
    spawnSync("taskkill.exe", ["/pid", String(server.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
  } else {
    try {
      process.kill(-server.pid, "SIGTERM");
    } catch {
      // The process may already have exited.
    }
  }
}

function requestOnce(url, timeoutMs) {
  return new Promise((resolve) => {
    const request = http.get(url, { timeout: timeoutMs }, (response) => {
      response.resume();
      response.once("end", () => resolve(true));
    });
    request.on("timeout", () => {
      request.destroy();
      resolve(false);
    });
    request.on("error", () => resolve(false));
  });
}

/**
 * 逐个请求被测页面，触发 next dev 的按需编译。
 * 串行是有意的：并行首访会让 dev server 同时编译多个路由，反而更慢也更容易超时。
 * 单个路径失败不阻断测试——预热只是提速手段，不是门禁。
 */
async function warmupRoutes() {
  for (const routePath of WARMUP_PATHS) {
    const url = new URL(routePath, parsedBaseURL).toString();
    const started = Date.now();
    const ok = await requestOnce(url, 120000);
    process.stdout.write(
      `[e2e-runner] warmup ${ok ? "ok " : "skip"} ${routePath} (${Date.now() - started}ms)\n`,
    );
  }
}

async function main() {
  if (!(await requestReady(readyURL))) {
    server = spawn(process.execPath, [path.join(projectRoot, "scripts", "next-dev.cjs")], {
      cwd: appDir,
      env: {
        ...process.env,
        PORT: parsedBaseURL.port || (parsedBaseURL.protocol === "https:" ? "443" : "80"),
      },
      detached: process.platform !== "win32",
      stdio: ["ignore", stdoutFd, stderrFd],
      windowsHide: true,
    });
    ownsServer = true;
  }

  await waitForServer();
  await warmupRoutes();
  const playwrightCli = require.resolve("@playwright/test/cli", { paths: [appDir] });
  const testProcess = spawn(
    process.execPath,
    [
      playwrightCli,
      "test",
      "--config",
      path.join(projectRoot, "e2e", "playwright.config.ts"),
      ...forwardedArgs,
    ],
    {
      cwd: projectRoot,
      env: { ...process.env, E2E_BASE_URL: baseURL, E2E_EXTERNAL_SERVER: "1" },
      stdio: "inherit",
      windowsHide: true,
    },
  );

  const exitCode = await new Promise((resolve, reject) => {
    testProcess.once("error", reject);
    testProcess.once("exit", (code) => resolve(code ?? 1));
  });
  process.exitCode = exitCode;
}

process.once("SIGINT", () => {
  stopServerTree();
  process.exit(130);
});
process.once("SIGTERM", () => {
  stopServerTree();
  process.exit(143);
});

main()
  .catch((error) => {
    process.stderr.write(`[e2e-runner] ${error.stack || error.message}\n`);
    process.exitCode = 1;
  })
  .finally(() => {
    stopServerTree();
    fs.closeSync(stdoutFd);
    fs.closeSync(stderrFd);
    process.exit(process.exitCode ?? 0);
  });
