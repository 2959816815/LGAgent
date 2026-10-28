const {
  app,
  BrowserWindow,
  ipcMain,
  screen,
  nativeTheme,
  dialog,
  safeStorage,
  shell,
} = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");
const { createSettingsStore, DEFAULTS } = require("./settings.cjs");

const BOOT_LOG = process.env.LANGGRAPH_BOOT_LOG || path.join(os.tmpdir(), "langgraph-boot.log");
function bootLog(stage, error) {
  try {
    const detail = error ? ` error=${error?.stack || error}` : "";
    fs.appendFileSync(BOOT_LOG, `${new Date().toISOString()} pid=${process.pid} ${stage}${detail}\n`, "utf8");
  } catch {
    // Boot diagnostics must never block startup.
  }
}
bootLog("module loaded");
process.on("uncaughtException", (error) => bootLog("uncaughtException", error));
process.on("unhandledRejection", (error) => bootLog("unhandledRejection", error));

// 从快捷方式/父进程启动时，标准输出管道可能已关闭。忽略 EPIPE，
// 避免一条 console.error 让整个主进程崩溃。
for (const stream of [process.stdout, process.stderr]) {
  if (stream && typeof stream.on === "function") {
    stream.on("error", (error) => {
      if (error?.code !== "EPIPE") throw error;
    });
  }
}

const PROJECT_ROOT = path.resolve(__dirname, "..");
const SIDECAR_TIMEOUT_MS = Number(process.env.DSH_SIDECAR_TIMEOUT_MS) || 60_000;
let mainWindow = null;
let backend = null;
let config = null;
let settingsStore = null;

function canWriteDirectory(directory) {
  try {
    fs.mkdirSync(directory, { recursive: true });
    const probe = path.join(directory, `.write-test-${process.pid}`);
    fs.writeFileSync(probe, "ok", "utf8");
    fs.unlinkSync(probe);
    return true;
  } catch {
    return false;
  }
}

function resolveUserData() {
  const candidates = [
    process.env.LANGGRAPH_DATA_DIR,
    path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "langgraph-agent"),
    path.join(process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"), "langgraph-agent"),
    path.join(path.dirname(process.execPath), "data"),
    path.join(os.tmpdir(), "langgraph-agent"),
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (canWriteDirectory(candidate)) return candidate;
  }
  throw new Error(
    "找不到可写的数据目录。请设置 LANGGRAPH_DATA_DIR 指向一个当前用户可写的文件夹。",
  );
}

bootLog("before resolveUserData");
app.setPath("userData", resolveUserData());
bootLog("after resolveUserData");
app.setPath("sessionData", app.getPath("userData"));
nativeTheme.themeSource = "dark";

function ensureShortcuts() {
  if (!app.isPackaged || process.platform !== "win32") return;
  const shortcuts = [
    path.join(app.getPath("desktop"), "LangGraph Agent.lnk"),
    path.join(
      app.getPath("appData"),
      "Microsoft",
      "Windows",
      "Start Menu",
      "Programs",
      "LangGraph Agent.lnk",
    ),
  ];
  for (const shortcut of shortcuts) {
    try {
      if (fs.existsSync(shortcut)) {
        const existing = shell.readShortcutLink(shortcut);
        if (!existing.args) continue;
      }
      shell.writeShortcutLink(shortcut, "create", {
        target: process.execPath,
        args: "",
        cwd: path.dirname(process.execPath),
        description: "LangGraph Agent",
        icon: process.execPath,
        iconIndex: 0,
      });
    } catch {
      // A shortcut is a convenience; never block startup if it can't be written.
    }
  }
}

function initSettings() {
  settingsStore = createSettingsStore({
    filePath: path.join(app.getPath("userData"), "settings.json"),
    encrypt: (value) => safeStorage.encryptString(value).toString("base64"),
    decrypt: (value) => safeStorage.decryptString(Buffer.from(value, "base64")),
  });
}

function findPython() {
  const candidates = [
    process.env.DSH_PYTHON,
    "D:\\Anaconda3\\envs\\rag\\python.exe",
    "python",
  ].filter(Boolean);
  return candidates.find((candidate) => candidate === "python" || fs.existsSync(candidate));
}

function sidecarCommand() {
  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, "sidecar", "langgraph-sidecar.exe"),
      args: ["--port", "0"],
    };
  }
  const python = findPython();
  if (!python) return null;
  return { command: python, args: ["-m", "desktop_api", "--port", "0"] };
}

function startBackend() {
  return new Promise((resolve, reject) => {
    const token = crypto.randomBytes(24).toString("hex");
    const invocation = sidecarCommand();
    if (!invocation) {
      reject(new Error("找不到 Python，请设置 DSH_PYTHON"));
      return;
    }
    const saved = settingsStore.getDeepSeek();
    const sidecarTemp = path.join(path.dirname(process.execPath), "data", "tmp");
    fs.mkdirSync(sidecarTemp, { recursive: true });
    const logDir = path.join(app.getPath("userData"), "logs");
    fs.mkdirSync(logDir, { recursive: true });
    const logPath = path.join(logDir, "sidecar.log");
    const log = (text) => {
      try {
        fs.appendFileSync(logPath, text, "utf8");
      } catch {
        // logging must never block startup
      }
    };

    const startedAt = Date.now();
    backend = spawn(invocation.command, invocation.args, {
      cwd: app.isPackaged ? app.getPath("userData") : PROJECT_ROOT,
      env: {
        ...process.env,
        DSH_DESKTOP_TOKEN: token,
        DSH_DATA_DIR: app.getPath("userData"),
        DSH_SESSION_STORE: "sqlite",
        LLM_PROVIDER: "deepseek",
        DEEPSEEK_API_KEY: saved?.apiKey || "",
        DEEPSEEK_BASE_URL: saved?.baseUrl || DEFAULTS.baseUrl,
        DEEPSEEK_MODEL: saved?.model || DEFAULTS.model,
        TEMP: sidecarTemp,
        TMP: sidecarTemp,
        TMPDIR: sidecarTemp,
        PLAYWRIGHT_BROWSERS_PATH: path.join(sidecarTemp, "playwright-browsers"),
      },
      stdio: ["ignore", "pipe", "pipe"],
    });
    backend.stdout.on("data", (chunk) => log(`[out] ${chunk}`));
    backend.stderr.on("data", (chunk) => log(`[err] ${chunk}`));

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

async function restartBackend() {
  if (backend) {
    backend.kill();
    backend = null;
  }
  config = await startBackend();
  return config;
}

async function testDeepSeek({ apiKey, baseUrl, model }) {
  const key = String(apiKey || "").trim();
  if (!key) throw new Error("API Key 不能为空");
  const root = String(baseUrl || DEFAULTS.baseUrl).replace(/\/+$/, "");
  const response = await fetch(`${root}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${key}`,
    },
    body: JSON.stringify({
      model: model || DEFAULTS.model,
      messages: [{ role: "user", content: "ping" }],
      max_tokens: 1,
    }),
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`连接失败 (${response.status}): ${text.slice(0, 300)}`);
  }
  return { ok: true };
}

async function createWindow() {
  if (!settingsStore.needsSetup()) {
    await startBackend();
  }

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
  if (app.isPackaged) {
    await mainWindow.loadFile(path.join(__dirname, "dist", "index.html"));
  } else {
    await mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL || "http://localhost:5173");
  }
  mainWindow.setBounds({ x, y, width, height });
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

ipcMain.handle("desktop:config", () => config);

ipcMain.handle("desktop:setup-state", () => {
  const saved = settingsStore.getDeepSeek();
  return {
    needsSetup: saved === null,
    baseUrl: saved?.baseUrl || DEFAULTS.baseUrl,
    model: saved?.model || DEFAULTS.model,
  };
});

ipcMain.handle("desktop:test-deepseek", async (_event, payload) => {
  return testDeepSeek(payload || {});
});

ipcMain.handle("desktop:save-deepseek", async (_event, payload) => {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("当前系统不支持安全存储，无法保存 API Key");
  }
  settingsStore.saveDeepSeek(payload || {});
  return restartBackend();
});

app.whenReady().then(async () => {
  bootLog("app ready");
  ensureShortcuts();
  initSettings();
  await createWindow();
  bootLog("window created");
}).catch((error) => {
  bootLog("whenReady failed", error);
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
