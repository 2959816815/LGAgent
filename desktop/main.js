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
let quitting = false;
let restarting = false;
let restartTimer = null;

// 必须先确定数据目录，再申请单实例锁：锁文件与 Chromium 的缓存都写在
// userData 下，目录不可写会导致锁失败、渲染进程网络服务起不来。
bootLog("before resolveUserData");
app.setPath("userData", resolveUserData());
bootLog("after resolveUserData");
app.setPath("sessionData", app.getPath("userData"));

// 多开会让每个实例都启动一个 sidecar；后启动的实例会把正在运行的子 agent
// 误判为“主程序重启”。只允许一个实例，第二次启动时聚焦已有窗口。
const singleInstanceLock = app.requestSingleInstanceLock();
if (!singleInstanceLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (!mainWindow) return;
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  });
}
let restartAttempt = 0;

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
    // 开发模式：不要把数据写进 electron 的 dist —— 打包时整个 dist 会被拷贝，
    // 运行中的 Cookies 等文件被占用会直接让打包失败（EBUSY）。
    app.isPackaged ? null : path.join(PROJECT_ROOT, ".desktop-data"),
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

function readLogTail(file, limit = 20_000) {
  try {
    return fs.readFileSync(file, "utf8").slice(-limit);
  } catch {
    return "";
  }
}

function writeCrashDiagnostics(code, stderrTail) {
  try {
    const dir = path.join(app.getPath("userData"), "logs");
    fs.mkdirSync(dir, { recursive: true });
    const saved = settingsStore?.getDeepSeek();
    const report = {
      generatedAt: new Date().toISOString(),
      appVersion: app.getVersion(),
      electron: process.versions.electron,
      platform: process.platform,
      arch: process.arch,
      exitCode: code,
      stderrTail,
      dataDir: app.getPath("userData"),
      settings: saved ? { baseUrl: saved.baseUrl, model: saved.model, hasApiKey: true } : null,
      bootLog: readLogTail(BOOT_LOG),
      sidecarLog: readLogTail(path.join(dir, "sidecar.log")),
    };
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    fs.writeFileSync(path.join(dir, `crash-${stamp}.json`), JSON.stringify(report, null, 2), "utf8");
  } catch {
    // diagnostics must never block restart
  }
}

function scheduleSidecarRestart(reason) {
  if (quitting || restarting || restartTimer) return;
  restarting = true;
  restartAttempt += 1;
  const delay = Math.min(30_000, 1_000 * 2 ** Math.min(restartAttempt - 1, 4));
  console.error(`[sidecar] ${reason}; restarting in ${delay}ms (attempt ${restartAttempt})`);
  restartTimer = setTimeout(async () => {
    restartTimer = null;
    try {
      await restartBackend(true);
      restartAttempt = 0;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.reload();
      }
    } catch (error) {
      console.error("[sidecar] restart failed", error);
      restarting = false;
      scheduleSidecarRestart("restart failed");
      return;
    }
    restarting = false;
  }, delay);
}

// 每个进程固定一次：sidecar 重启后端口会变，但 token 不变，
// 这样保存设置重启后端时界面不会因为 token 轮换而 401。
const DESKTOP_TOKEN = crypto.randomBytes(24).toString("hex");

function startBackend() {
  return new Promise((resolve, reject) => {
    const token = DESKTOP_TOKEN;
    const invocation = sidecarCommand();
    if (!invocation) {
      reject(new Error("找不到 Python，请设置 DSH_PYTHON"));
      return;
    }
    const saved = settingsStore.getDeepSeek();
    // 工作区：文件面板、@ 引用和 Agent 的相对路径都以它为根。
    // 开发期用项目根；打包后用一个独立目录，避免指向 sidecar 的 _internal。
    let workspace = app.isPackaged
      ? path.join(app.getPath("userData"), "workspace")
      : PROJECT_ROOT;
    try {
      fs.mkdirSync(workspace, { recursive: true });
    } catch {
      workspace = app.getPath("userData");
    }
    // 临时目录放在用户数据目录，而不是安装目录：装到 Program Files 时
    // 安装目录通常不可写，会导致 sidecar 启动即失败。
    const sidecarTemp = path.join(app.getPath("userData"), "tmp");
    fs.mkdirSync(sidecarTemp, { recursive: true });
    // npx 型 MCP 服务器（Playwright / GitHub）需要可写的 npm 缓存。默认位置是
    // %LOCALAPPDATA%\npm-cache，在工作区被限制为低完整性时会 mkdir 失败（EPERM），
    // 所以统一指到用户数据目录下。
    const npmCache = path.join(app.getPath("userData"), "npm-cache");
    try {
      fs.mkdirSync(npmCache, { recursive: true });
    } catch {
      // 目录建不出来时退回默认缓存位置
    }
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
      cwd: workspace,
      env: {
        ...process.env,
        DSH_DESKTOP_TOKEN: token,
        DSH_DATA_DIR: app.getPath("userData"),
        DSH_WORKSPACE: workspace,
        DSH_SESSION_STORE: "sqlite",
        LLM_PROVIDER: "deepseek",
        DEEPSEEK_API_KEY: saved?.apiKey || "",
        DEEPSEEK_BASE_URL: saved?.baseUrl || DEFAULTS.baseUrl,
        DEEPSEEK_MODEL: saved?.model || DEFAULTS.model,
        TEMP: sidecarTemp,
        TMP: sidecarTemp,
        TMPDIR: sidecarTemp,
        npm_config_cache: npmCache,
        NPM_CONFIG_CACHE: npmCache,
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
        return;
      }
      if (quitting || restarting) return;
      writeCrashDiagnostics(code, stderrTail);
      scheduleSidecarRestart(`exited with code ${code}`);
    });
  });
}

async function restartBackend(fromWatchdog = false) {
  if (!fromWatchdog) restarting = true;
  try {
    if (backend) {
      backend.kill();
      backend = null;
    }
    config = await startBackend();
    return config;
  } finally {
    if (!fromWatchdog) restarting = false;
  }
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
    // 用 127.0.0.1 而不是 localhost：部分机器上 localhost 只解析到 ::1，
    // 而 Vite 绑定的是 IPv4，Chromium 会直接 ERR_FAILED。
    await mainWindow.loadURL(
      process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173",
    );
  }
  mainWindow.setBounds({ x, y, width, height });
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

ipcMain.handle("desktop:export-diagnostics", async () => {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const { canceled, filePath } = await dialog.showSaveDialog(mainWindow, {
    title: "导出诊断信息",
    defaultPath: path.join(app.getPath("downloads"), `langgraph-diagnostics-${stamp}.json`),
    filters: [{ name: "JSON", extensions: ["json"] }],
  });
  if (canceled || !filePath) return null;
  const saved = settingsStore.getDeepSeek();
  const report = {
    generatedAt: new Date().toISOString(),
    appVersion: app.getVersion(),
    electron: process.versions.electron,
    platform: process.platform,
    arch: process.arch,
    packaged: app.isPackaged,
    dataDir: app.getPath("userData"),
    settings: saved ? { baseUrl: saved.baseUrl, model: saved.model, hasApiKey: true } : null,
    bootLog: readLogTail(BOOT_LOG),
    sidecarLog: readLogTail(path.join(app.getPath("userData"), "logs", "sidecar.log")),
  };
  fs.writeFileSync(filePath, JSON.stringify(report, null, 2), "utf8");
  return filePath;
});

ipcMain.handle("desktop:config", () => config);

ipcMain.handle("desktop:get-settings", () => {
  const saved = settingsStore.getDeepSeek();
  return {
    baseUrl: saved?.baseUrl || DEFAULTS.baseUrl,
    model: saved?.model || DEFAULTS.model,
    hasApiKey: Boolean(saved?.apiKey),
  };
});

ipcMain.handle("desktop:test-settings", async (_event, payload) => {
  const saved = settingsStore.getDeepSeek();
  const apiKey = String(payload?.apiKey || "").trim() || saved?.apiKey || "";
  return testDeepSeek({
    apiKey,
    baseUrl: payload?.baseUrl,
    model: payload?.model,
  });
});

ipcMain.handle("desktop:save-settings", async (_event, payload) => {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("当前系统不支持安全存储，无法保存 API Key");
  }
  settingsStore.saveSettings(payload || {});
  return restartBackend();
});

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

async function createWindowWithRetry() {
  for (;;) {
    try {
      await createWindow();
      bootLog("window created");
      return;
    } catch (error) {
      bootLog("window creation failed", error);
      console.error(error);
      const choice = dialog.showMessageBoxSync({
        type: "error",
        title: "LangGraph Agent 启动失败",
        message: "本地服务没能启动",
        detail: String(error?.message ?? error),
        buttons: ["重试", "退出"],
        defaultId: 0,
        cancelId: 1,
      });
      if (choice !== 0) {
        app.quit();
        return;
      }
    }
  }
}

app.whenReady().then(async () => {
  bootLog("app ready");
  ensureShortcuts();
  initSettings();
  await createWindowWithRetry();
}).catch((error) => {
  bootLog("whenReady failed", error);
  console.error(error);
  dialog.showErrorBox("LangGraph Agent 启动失败", String(error?.message ?? error));
  app.quit();
});

app.on("window-all-closed", () => {
  quitting = true;
  if (restartTimer) clearTimeout(restartTimer);
  if (backend) backend.kill();
  app.quit();
});

app.on("before-quit", () => {
  quitting = true;
  if (restartTimer) clearTimeout(restartTimer);
  if (backend) backend.kill();
});
