const { app, BrowserWindow, ipcMain, screen, nativeTheme, dialog } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");

const PROJECT_ROOT = path.resolve(__dirname, "..");
const SIDECAR_TIMEOUT_MS = Number(process.env.DSH_SIDECAR_TIMEOUT_MS) || 60_000;
const SIDECAR_TEMP = path.join(PROJECT_ROOT, ".desktop-temp");
try {
  fs.mkdirSync(SIDECAR_TEMP, { recursive: true });
} catch {
  // 交给子进程报错；这里不阻断启动
}
let mainWindow = null;
let backend = null;
let config = null;

function resolveUserData() {
  const preferred = path.join(app.getPath("appData"), "langgraph-agent");
  try {
    fs.mkdirSync(preferred, { recursive: true });
    return preferred;
  } catch {
    const fallback = path.join(PROJECT_ROOT, ".desktop-data");
    fs.mkdirSync(fallback, { recursive: true });
    return fallback;
  }
}

app.setPath("userData", resolveUserData());
app.setPath("sessionData", app.getPath("userData"));
nativeTheme.themeSource = "dark";

function findPython() {
  const candidates = [
    process.env.DSH_PYTHON,
    "D:\\Anaconda3\\envs\\rag\\python.exe",
    "python",
  ].filter(Boolean);
  return candidates.find((candidate) => candidate === "python" || fs.existsSync(candidate));
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const token = crypto.randomBytes(24).toString("hex");
    const python = findPython();
    if (!python) {
      reject(new Error("找不到 Python，请设置 DSH_PYTHON"));
      return;
    }
    const startedAt = Date.now();
    backend = spawn(python, ["-m", "desktop_api", "--port", "0"], {
      cwd: PROJECT_ROOT,
      env: {
        ...process.env,
        DSH_DESKTOP_TOKEN: token,
        TEMP: SIDECAR_TEMP,
        TMP: SIDECAR_TEMP,
        TMPDIR: SIDECAR_TEMP,
        PLAYWRIGHT_BROWSERS_PATH: path.join(SIDECAR_TEMP, "playwright-browsers"),
      },
      stdio: ["ignore", "pipe", "pipe"],
    });

    let settled = false;
    let stderrTail = "";
    let timer = null;
    const settle = (error, value) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      if (error) reject(error);
      else resolve(value);
    };
    timer = setTimeout(() => {
      const seconds = Math.round((Date.now() - startedAt) / 1000);
      try {
        backend.kill();
      } catch {
        // best effort
      }
      settle(
        new Error(
          `sidecar 启动超时（${seconds}s）。首次启动需要加载 Python 依赖，可能被杀毒软件或磁盘冷启动拖慢；可设置 DSH_SIDECAR_TIMEOUT_MS 增大超时后重试。`,
        ),
      );
    }, SIDECAR_TIMEOUT_MS);

    const lines = readline.createInterface({ input: backend.stdout });
    lines.on("line", (line) => {
      if (!line.startsWith("READY ")) return;
      const elapsed = ((Date.now() - startedAt) / 1000).toFixed(1);
      console.log(`[sidecar] ready in ${elapsed}s`);
      const { port } = JSON.parse(line.slice(6));
      config = { baseUrl: `http://127.0.0.1:${port}`, token };
      settle(null, config);
    });
    backend.stderr.on("data", (chunk) => {
      const text = chunk.toString();
      stderrTail = (stderrTail + text).slice(-2000);
      console.error(`[sidecar] ${text}`);
    });
    backend.on("error", (error) => {
      settle(error);
    });
    backend.on("exit", (code) => {
      if (code !== 0 && code !== null) {
        console.error(`[sidecar] exited with code ${code}`);
      }
      if (!settled) {
        settle(
          new Error(
            `sidecar 在就绪前退出（code ${code}）${stderrTail ? `：${stderrTail.trim()}` : ""}`,
          ),
        );
      }
    });
  });
}

async function createWindow() {
  console.log("[main] createWindow: starting backend");
  await startBackend();
  console.log("[main] createWindow: backend ready, creating window");
  const area = screen.getPrimaryDisplay().workArea;
  const width = Math.min(960, area.width - 80);
  const height = Math.min(820, area.height - 80);
  const x = Math.round(area.x + (area.width - width) / 2);
  const y = Math.round(area.y + (area.height - height) / 2);
  mainWindow = new BrowserWindow({
    width,
    height,
    x,
    y,
    minWidth: 880,
    minHeight: 600,
    autoHideMenuBar: true,
    backgroundColor: "#0e0e0e",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.setMenuBarVisibility(false);
  console.log("[main] createWindow: window created, loading URL");
  await mainWindow.loadURL("http://localhost:5173");
  // Some Windows/DPI combinations can place a "centered" window off-screen
  // (observed at -25600,-25600). Re-assert visible bounds after load.
  mainWindow.setBounds({ x, y, width, height });
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
  console.log("[main] createWindow: URL loaded");
}

ipcMain.handle("desktop:config", () => config);

console.log("[main] module loaded");
app.whenReady().then(() => {
  console.log("[main] app ready");
  return createWindow();
}).catch((error) => {
  console.error(error);
  dialog.showErrorBox("LangGraph Agent 启动失败", String(error?.message ?? error));
  app.quit();
});

app.on("window-all-closed", () => {
  if (backend) backend.kill();
  app.quit();
});

app.on("before-quit", () => {
  if (backend) backend.kill();
});
