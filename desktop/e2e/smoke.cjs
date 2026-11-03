"use strict";

// Electron smoke test: boots the desktop app with a temporary userData dir,
// walks the first-run setup, then checks the main shell, settings/knowledge
// tabs, MCP panel, and a new session. It never calls a real model.

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const { _electron } = require("playwright-core");

const DESKTOP = path.resolve(__dirname, "..");
const ELECTRON = require("electron");
// Keep the app data inside the project: the DSH sandbox can block Electron
// from writing to the system temp directory, which would make the app fall
// back to the developer's .desktop-data.
const DATA_DIR = fs.mkdtempSync(path.join(DESKTOP, ".e2e-data-"));

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

function waitForHttp(url, timeoutMs = 40000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const attempt = () => {
      const request = http.get(url, (response) => {
        response.resume();
        if (response.statusCode && response.statusCode < 500) {
          resolve();
          return;
        }
        retry();
      });
      request.on("error", retry);
      request.setTimeout(2000, () => request.destroy());
    };
    const retry = () => {
      if (Date.now() - started > timeoutMs) {
        reject(new Error(`Timed out waiting for ${url}`));
        return;
      }
      setTimeout(attempt, 300);
    };
    attempt();
  });
}

function stopProcess(child) {
  if (!child || child.killed) return;
  if (process.platform === "win32" && child.pid) {
    spawnSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
    });
    return;
  }
  child.kill();
}

async function main() {
  const port = await freePort();
  const viteUrl = `http://127.0.0.1:${port}`;
  const env = {
    ...process.env,
    LANGGRAPH_DATA_DIR: DATA_DIR,
    VITE_DEV_SERVER_URL: viteUrl,
    DSH_CORS_ORIGINS: viteUrl,
  };
  delete env.ELECTRON_RUN_AS_NODE;

  // Avoid launching the default Playwright MCP server during the smoke test.
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(DATA_DIR, "mcp_servers.json"),
    JSON.stringify({ servers: [] }, null, 2),
    "utf8",
  );

  const vite = spawn(
    process.execPath,
    [
      path.join(DESKTOP, "node_modules", "vite", "bin", "vite.js"),
      "--port",
      String(port),
      "--strictPort",
    ],
    { cwd: DESKTOP, env, stdio: ["ignore", "pipe", "pipe"] },
  );
  vite.stdout.on("data", (chunk) => process.stdout.write(`[vite] ${chunk}`));
  vite.stderr.on("data", (chunk) => process.stderr.write(`[vite] ${chunk}`));

  let electronApp = null;
  let ok = false;
  try {
    await waitForHttp(viteUrl);
    electronApp = await _electron.launch({
      executablePath: ELECTRON,
      args: ["--no-sandbox", "--disable-gpu", DESKTOP],
      cwd: DESKTOP,
      env,
      timeout: 90000,
    });
    const page = await electronApp.firstWindow();
    const mainEnv = await electronApp.evaluate(() => ({
      langgraphData: process.env.LANGGRAPH_DATA_DIR,
      viteUrl: process.env.VITE_DEV_SERVER_URL,
      runAsNode: process.env.ELECTRON_RUN_AS_NODE || "",
    }));
    const userData = await electronApp.evaluate(({ app }) =>
      app.getPath("userData"),
    );
    console.error("E2E_MAIN_ENV", mainEnv);
    console.error("E2E_USER_DATA", userData);

    await page.waitForSelector(".setup-card, .app-shell", { timeout: 90000 });
    if (await page.locator(".setup-card").count()) {
      await page.getByLabel("API Key").fill("e2e-dummy-key");
      await page.getByRole("button", { name: "保存并开始" }).click();
      await page.waitForSelector(".app-shell", { timeout: 120000 });
    }

    await page
      .getByRole("button", { name: /新会话/ })
      .first()
      .waitFor({ timeout: 30000 });

    await page.getByRole("button", { name: "设置" }).first().click();
    await page.getByText("设置 · 模型").waitFor({ timeout: 15000 });
    await page.getByRole("button", { name: "知识库" }).first().click();
    try {
      await page
        .getByRole("button", { name: "添加文件夹" })
        .waitFor({ timeout: 30000 });
    } catch (error) {
      const panel = await page
        .locator(".knowledge-panel")
        .innerText()
        .catch(() => "<no knowledge panel>");
      console.error("KNOWLEDGE_PANEL:", panel);
      throw error;
    }
    await page.getByRole("button", { name: "关闭" }).first().click();

    await page.getByRole("button", { name: "MCP" }).first().click();
    await page
      .getByRole("button", { name: "添加服务" })
      .waitFor({ timeout: 15000 });

    await page.getByRole("button", { name: /新会话/ }).first().click();
    await page.waitForTimeout(1000);

    const screenshot = path.join(os.tmpdir(), "lgagent-e2e-smoke.png");
    await page.screenshot({ path: screenshot });
    console.log("E2E_SMOKE_OK", screenshot);
    ok = true;
  } finally {
    try {
      if (electronApp) await electronApp.close();
    } catch {
      // The app may already be closed.
    }
    stopProcess(vite);
    if (!ok) {
      console.error("E2E_DATA_DIR", DATA_DIR);
      try {
        const logPath = path.join(DATA_DIR, "logs", "sidecar.log");
        const tail = fs.readFileSync(logPath, "utf8").slice(-5000);
        console.error("[sidecar-log-tail]\n", tail);
      } catch {
        // No sidecar log; the failure happened before backend startup.
      }
    } else {
      try {
        fs.rmSync(DATA_DIR, { recursive: true, force: true });
      } catch {
        // A sidecar may still hold a file; the temp dir is harmless.
      }
    }
  }
}

main().catch((error) => {
  console.error("E2E_SMOKE_FAILED", error);
  process.exit(1);
});
