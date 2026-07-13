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
