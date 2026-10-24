const { app, BrowserWindow, ipcMain, screen } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");

const PROJECT_ROOT = path.resolve(__dirname, "..");
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
    backend = spawn(python, ["-m", "desktop_api", "--port", "0"], {
      cwd: PROJECT_ROOT,
      env: { ...process.env, DSH_DESKTOP_TOKEN: token },
      stdio: ["ignore", "pipe", "pipe"],
    });

    const timer = setTimeout(() => reject(new Error("sidecar 启动超时")), 15000);
    const lines = readline.createInterface({ input: backend.stdout });
    lines.on("line", (line) => {
      if (!line.startsWith("READY ")) return;
      clearTimeout(timer);
      const { port } = JSON.parse(line.slice(6));
      config = { baseUrl: `http://127.0.0.1:${port}`, token };
      resolve(config);
    });
    backend.stderr.on("data", (chunk) => {
      console.error(`[sidecar] ${chunk.toString()}`);
    });
    backend.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    backend.on("exit", (code) => {
      if (code !== 0 && code !== null) {
        console.error(`[sidecar] exited with code ${code}`);
      }
    });
  });
}

async function createWindow() {
  console.log("[main] createWindow: starting backend");
  await startBackend();
  console.log("[main] createWindow: backend ready, creating window");
  const area = screen.getPrimaryDisplay().workAreaSize;
  mainWindow = new BrowserWindow({
    width: Math.min(960, area.width - 80),
    height: Math.min(820, area.height - 80),
    minWidth: 880,
    minHeight: 600,
    center: true,
    backgroundColor: "#0e0e0e",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  mainWindow.center();
  console.log("[main] createWindow: window created, loading URL");
  await mainWindow.loadURL("http://localhost:5173");
  console.log("[main] createWindow: URL loaded");
}

ipcMain.handle("desktop:config", () => config);

console.log("[main] module loaded");
app.whenReady().then(() => {
  console.log("[main] app ready");
  return createWindow();
}).catch((error) => {
  console.error(error);
  app.quit();
});

app.on("window-all-closed", () => {
  if (backend) backend.kill();
  app.quit();
});

app.on("before-quit", () => {
  if (backend) backend.kill();
});
