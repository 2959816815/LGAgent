const { app, BrowserWindow, ipcMain } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const readline = require("readline");

const PROJECT_ROOT = path.resolve(__dirname, "..");
let mainWindow = null;
let backend = null;
let config = null;

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
  await startBackend();
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1080,
    minHeight: 700,
    backgroundColor: "#0e0e0e",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });
  await mainWindow.loadURL("http://localhost:5173");
}

ipcMain.handle("desktop:config", () => config);

app.whenReady().then(createWindow).catch((error) => {
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
