# Installable Windows Shell (A1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a Windows NSIS installer that launches the existing React UI and a bundled Python sidecar, with a first-run DeepSeek key wizard and DPAPI-encrypted settings.

**Architecture:** Electron owns startup, secrets, and sidecar lifecycle. Settings live in `%APPDATA%/langgraph-agent/settings.json`; the API key is encrypted with `safeStorage`. The React renderer shows a setup wizard until a key exists, then boots the existing App against the sidecar.

**Tech Stack:** Electron 43, React 18, Vite 5, Node 22, Python 3.12 (`rag` env), FastAPI, PyInstaller, electron-builder.

---

### Task 1: Settings store with encrypted key

**Files:**
- Create: `desktop/settings.cjs`
- Test: `desktop/settings.test.cjs`

- [ ] **Step 1: Write the failing test**

```js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { createSettingsStore, DEFAULTS } = require("./settings.cjs");

function tempStore() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "lg-settings-"));
  const file = path.join(dir, "settings.json");
  return createSettingsStore({
    filePath: file,
    encrypt: (value) => `enc:${value}`,
    decrypt: (value) => value.replace(/^enc:/, ""),
  });
}

test("needs setup when no settings file", () => {
  assert.equal(tempStore().needsSetup(), true);
});

test("saves and reads DeepSeek settings without plaintext key", () => {
  const store = tempStore();
  store.saveDeepSeek({ apiKey: "sk-test", baseUrl: DEFAULTS.baseUrl, model: DEFAULTS.model });
  assert.equal(store.needsSetup(), false);
  assert.equal(store.getDeepSeek().apiKey, "sk-test");
  assert.equal(fs.readFileSync(store.filePath, "utf8").includes("sk-test"), false);
});

test("rejects empty api key", () => {
  const store = tempStore();
  assert.throws(() => store.saveDeepSeek({ apiKey: "  " }), /API Key/);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test desktop/settings.test.cjs`
Expected: FAIL with `Cannot find module './settings.cjs'`.

- [ ] **Step 3: Write minimal implementation**

```js
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const DEFAULTS = {
  baseUrl: "https://api.deepseek.com",
  model: "deepseek-flash",
};

function createSettingsStore({ filePath, encrypt, decrypt }) {
  const empty = () => ({ version: 1, deepseek: null });

  function read() {
    try {
      const data = JSON.parse(fs.readFileSync(filePath, "utf8"));
      return data && typeof data === "object" ? data : empty();
    } catch {
      return empty();
    }
  }

  function write(data) {
    fs.mkdirSync(path.dirname(filePath), { recursive: true });
    const temp = `${filePath}.tmp`;
    fs.writeFileSync(temp, JSON.stringify(data, null, 2), "utf8");
    fs.renameSync(temp, filePath);
  }

  function getDeepSeek() {
    const data = read();
    if (!data.deepseek || !data.deepseek.key_encrypted) return null;
    try {
      return {
        baseUrl: data.deepseek.base_url || DEFAULTS.baseUrl,
        model: data.deepseek.model || DEFAULTS.model,
        apiKey: decrypt(data.deepseek.key_encrypted),
      };
    } catch {
      return null;
    }
  }

  function needsSetup() {
    return getDeepSeek() === null;
  }

  function saveDeepSeek({ apiKey, baseUrl = DEFAULTS.baseUrl, model = DEFAULTS.model }) {
    const trimmed = String(apiKey || "").trim();
    if (!trimmed) throw new Error("API Key 不能为空");
    write({
      version: 1,
      deepseek: {
        base_url: String(baseUrl || DEFAULTS.baseUrl).trim(),
        model: String(model || DEFAULTS.model).trim(),
        key_encrypted: encrypt(trimmed),
      },
    });
  }

  return { filePath, read, needsSetup, getDeepSeek, saveDeepSeek };
}

module.exports = { createSettingsStore, DEFAULTS };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test desktop/settings.test.cjs`
Expected: 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/settings.cjs desktop/settings.test.cjs
git commit -m "feat: add encrypted desktop settings store"
```

---

### Task 2: Main process packaged startup and IPC

**Files:**
- Modify: `desktop/main.js`
- Modify: `desktop/preload.js`
- Modify: `desktop/src/global.d.ts`

- [ ] **Step 1: Add settings/IPC wiring to `main.js`**

At the top, require settings and safeStorage:

```js
const { app, BrowserWindow, ipcMain, screen, nativeTheme, dialog, safeStorage } = require("electron");
const { createSettingsStore, DEFAULTS } = require("./settings.cjs");
```

After `resolveUserData()` is applied, replace the `findPython`/`startBackend` block with:

```js
let settingsStore = null;
function initSettings() {
  settingsStore = createSettingsStore({
    filePath: path.join(app.getPath("userData"), "settings.json"),
    encrypt: (value) => safeStorage.encryptString(value).toString("base64"),
    decrypt: (value) => safeStorage.decryptString(Buffer.from(value, "base64")),
  });
}

function sidecarCommand() {
  if (app.isPackaged) {
    return {
      command: path.join(process.resourcesPath, "sidecar", "langgraph-sidecar.exe"),
      args: ["--port", "0"],
    };
  }
  return { command: findPython(), args: ["-m", "desktop_api", "--port", "0"] };
}
```

Inside `startBackend`, build env from settings:

```js
const saved = settingsStore.getDeepSeek();
const { command, args } = sidecarCommand();
const sidecarTemp = path.join(app.getPath("userData"), "tmp");
fs.mkdirSync(sidecarTemp, { recursive: true });
const logDir = path.join(app.getPath("userData"), "logs");
fs.mkdirSync(logDir, { recursive: true });
const logPath = path.join(logDir, "sidecar.log");
const log = (text) => fs.appendFileSync(logPath, text, "utf8");

backend = spawn(command, args, {
  cwd: app.getPath("userData"),
  env: {
    ...process.env,
    DSH_DESKTOP_TOKEN: token,
    DSH_DATA_DIR: app.getPath("userData"),
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
```

Delete the module-level `const SIDECAR_TEMP = path.join(PROJECT_ROOT, ".desktop-temp");` and its `mkdirSync` block; packaged install directories are read-only.

- [ ] **Step 2: Add IPC handlers**

```js
ipcMain.handle("desktop:setup-state", () => {
  const saved = settingsStore.getDeepSeek();
  return {
    needsSetup: saved === null,
    baseUrl: saved?.baseUrl || DEFAULTS.baseUrl,
    model: saved?.model || DEFAULTS.model,
  };
});

ipcMain.handle("desktop:test-deepseek", async (_event, payload) => {
  const baseUrl = String(payload.baseUrl || DEFAULTS.baseUrl).replace(/\/+$/, "");
  const response = await fetch(`${baseUrl}/chat/completions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${String(payload.apiKey || "")}`,
    },
    body: JSON.stringify({
      model: payload.model || DEFAULTS.model,
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
});

ipcMain.handle("desktop:save-deepseek", async (_event, payload) => {
  if (!safeStorage.isEncryptionAvailable()) {
    throw new Error("当前系统不支持安全存储，无法保存 API Key");
  }
  settingsStore.saveDeepSeek(payload);
  if (backend) {
    backend.kill();
    backend = null;
  }
  config = await startBackend();
  mainWindow?.reload();
  return config;
});
```

- [ ] **Step 3: Production URL and startup gating**

In `createWindow`, before creating the window:

```js
if (!settingsStore.needsSetup()) {
  await startBackend();
}
```

Replace `await mainWindow.loadURL("http://localhost:5173");` with:

```js
const devUrl = process.env.VITE_DEV_SERVER_URL;
if (app.isPackaged) {
  await mainWindow.loadFile(path.join(__dirname, "dist", "index.html"));
} else {
  await mainWindow.loadURL(devUrl || "http://localhost:5173");
}
```

In `app.whenReady()`:

```js
initSettings();
return createWindow();
```

- [ ] **Step 4: Expose IPC in preload and types**

`desktop/preload.js`:

```js
contextBridge.exposeInMainWorld("desktop", {
  getConfig: () => ipcRenderer.invoke("desktop:config"),
  getSetupState: () => ipcRenderer.invoke("desktop:setup-state"),
  testDeepSeek: (payload) => ipcRenderer.invoke("desktop:test-deepseek", payload),
  saveDeepSeek: (payload) => ipcRenderer.invoke("desktop:save-deepseek", payload),
  getPathForFile: (file) => webUtils.getPathForFile(file),
});
```

`desktop/src/global.d.ts` add:

```ts
interface SetupState { needsSetup: boolean; baseUrl: string; model: string; }
interface DeepSeekInput { apiKey: string; baseUrl: string; model: string; }
```

and methods `getSetupState(): Promise<SetupState>`, `testDeepSeek(input: DeepSeekInput): Promise<{ok: boolean}>`, `saveDeepSeek(input: DeepSeekInput): Promise<DesktopConfig>`.

- [ ] **Step 5: Verify syntax**

Run: `node --check desktop/main.js && node --check desktop/preload.js`
Expected: no output, exit 0.

- [ ] **Step 6: Commit**

```bash
git add desktop/main.js desktop/preload.js desktop/src/global.d.ts
git commit -m "feat: add packaged startup and DeepSeek setup IPC"
```

---

### Task 3: First-run setup wizard

**Files:**
- Create: `desktop/src/components/SetupWizard.tsx`
- Create: `desktop/src/components/SetupWizard.test.tsx`
- Modify: `desktop/src/main.tsx`
- Modify: `desktop/src/styles.css`

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SetupWizard } from "./SetupWizard";

describe("SetupWizard", () => {
  it("tests then saves the key", async () => {
    const saveDeepSeek = vi.fn().mockResolvedValue({ baseUrl: "x", token: "t" });
    const testDeepSeek = vi.fn().mockResolvedValue({ ok: true });
    (window as any).desktop = { saveDeepSeek, testDeepSeek };
    const onDone = vi.fn();
    render(<SetupWizard initialState={{ needsSetup: true, baseUrl: "https://api.deepseek.com", model: "deepseek-flash" }} onDone={onDone} />);
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "sk-test" } });
    fireEvent.click(screen.getByText("测试连接"));
    await waitFor(() => expect(testDeepSeek).toHaveBeenCalled());
    fireEvent.click(screen.getByText("保存并开始"));
    await waitFor(() => expect(saveDeepSeek).toHaveBeenCalled());
    expect(onDone).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && pnpm test -- --run src/components/SetupWizard.test.tsx`
Expected: FAIL because `SetupWizard` does not exist.

- [ ] **Step 3: Implement `SetupWizard.tsx`**

```tsx
import { useState } from "react";

export function SetupWizard({ initialState, onDone }: { initialState: { baseUrl: string; model: string }; onDone: () => void }) {
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState(initialState.baseUrl);
  const [model, setModel] = useState(initialState.model);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const input = { apiKey, baseUrl, model };

  async function test() {
    setBusy(true);
    setMessage("");
    try {
      await window.desktop!.testDeepSeek(input);
      setMessage("连接成功");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    try {
      await window.desktop!.saveDeepSeek(input);
      onDone();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="setup-shell">
      <form className="setup-card" onSubmit={(event) => { event.preventDefault(); void save(); }}>
        <h1>连接 DeepSeek</h1>
        <p>正常使用只需要一个 API Key。Key 会加密保存在本机。</p>
        <label>API Key<input aria-label="API Key" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} /></label>
        <label>Base URL<input aria-label="Base URL" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></label>
        <label>模型<input aria-label="模型" value={model} onChange={(e) => setModel(e.target.value)} /></label>
        <div className="setup-actions">
          <button type="button" onClick={() => void test()} disabled={busy || !apiKey}>测试连接</button>
          <button type="submit" className="primary" disabled={busy || !apiKey}>保存并开始</button>
        </div>
        {message && <p className="setup-message">{message}</p>}
      </form>
    </div>
  );
}
```

- [ ] **Step 4: Gate the app in `main.tsx`**

```tsx
function Root() {
  const [state, setState] = useState<SetupState | null>(null);
  const [done, setDone] = useState(false);
  useEffect(() => {
    void window.desktop?.getSetupState().then(setState);
  }, []);
  if (!state) return <div className="setup-shell">加载中…</div>;
  if (state.needsSetup && !done) {
    return <SetupWizard initialState={state} onDone={() => setDone(true)} />;
  }
  return <App />;
}
```

Render `<Root />` instead of `<App />`; import `useEffect/useState` and `SetupWizard`.

- [ ] **Step 5: Add CSS**

```css
.setup-shell { min-height:100vh; display:grid; place-items:center; background:#0e0e0e; color:#eee; }
.setup-card { width:min(440px,92vw); display:grid; gap:14px; padding:28px; border:1px solid #333; border-radius:12px; background:#171717; }
.setup-card label { display:grid; gap:6px; font-size:14px; }
.setup-card input { padding:10px; border:1px solid #3a3a3a; border-radius:7px; background:#0f0f0f; color:#eee; }
.setup-actions { display:flex; gap:10px; justify-content:flex-end; }
.setup-message { color:#f0b429; white-space:pre-wrap; }
```

- [ ] **Step 6: Run test**

Run: `cd desktop && pnpm test -- --run src/components/SetupWizard.test.tsx`
Expected: 1 test passes.

- [ ] **Step 7: Commit**

```bash
git add desktop/src/components/SetupWizard.tsx desktop/src/components/SetupWizard.test.tsx desktop/src/main.tsx desktop/src/styles.css
git commit -m "feat: add first-run DeepSeek setup wizard"
```

---

### Task 4: Backend writable data directory

**Files:**
- Modify: `desktop_api.py:92-100`, `desktop_api.py:676-699`
- Test: `tests/test_desktop_api.py`

- [ ] **Step 1: Add a failing test for `DSH_DATA_DIR`**

```python
def test_data_dir_env_controls_sessions(tmp_path, monkeypatch):
    import desktop_api

    captured = {}

    class FakeStore:
        def __init__(self, root):
            captured["root"] = root

    monkeypatch.setenv("DSH_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(desktop_api, "SessionStore", FakeStore)
    monkeypatch.setattr(desktop_api, "SubagentManager", lambda **kwargs: object())
    monkeypatch.setattr(desktop_api, "McpManager", lambda root: object())
    desktop_api.create_app(tmp_path / "install", "token")
    assert captured["root"] == tmp_path / ".sessions"
```

- [ ] **Step 2: Run it**

Run: `D:\Anaconda3\envs\rag\python.exe -m pytest tests/test_desktop_api.py::test_data_dir_env_controls_sessions -q`
Expected: FAIL (the current code passes `install/.sessions`).

- [ ] **Step 3: Implement**

Change `create_app` signature to `create_app(root: Path, token: str, graph_builder=build_graph, mcp_manager=None, data_root: Path | None = None)` and use:

```python
data_dir = Path(data_root or os.getenv("DSH_DATA_DIR") or root)
store = SessionStore(data_dir / ".sessions")
manager = SubagentManager(project_root=root)
mcp = mcp_manager if mcp_manager is not None else McpManager(data_dir)
```

At module bottom:

```python
data_root = Path(os.getenv("DSH_DATA_DIR", str(BASE_DIR)))
app = create_app(BASE_DIR, DEFAULT_TOKEN, data_root=data_root)
```

- [ ] **Step 4: Run tests**

Run: `D:\Anaconda3\envs\rag\python.exe -m pytest tests/test_desktop_api.py -q`
Expected: all existing tests pass, new test passes.

- [ ] **Step 5: Commit**

```bash
git add desktop_api.py tests/test_desktop_api.py
git commit -m "feat: allow packaged sidecar to use per-user data dir"
```

---

### Task 5: Sidecar and installer build

**Files:**
- Create: `packaging/build-sidecar.ps1`
- Modify: `desktop/package.json`
- Create: `desktop/electron-builder.yml`

- [ ] **Step 1: Add PyInstaller build script**

`packaging/build-sidecar.ps1`:

```powershell
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
& "D:\Anaconda3\envs\rag\python.exe" -m PyInstaller `
  --noconfirm --clean --onedir `
  --name langgraph-sidecar `
  --distpath "$root\dist-sidecar" `
  --workpath "$root\build\sidecar" `
  --specpath "$root\build" `
  --collect-submodules uvicorn `
  --collect-submodules langgraph `
  --collect-submodules langchain_core `
  --collect-submodules langchain_openai `
  desktop_api.py
```

- [ ] **Step 2: Build sidecar and smoke test**

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-sidecar.ps1
$env:DSH_DESKTOP_TOKEN="test"; $env:DSH_DATA_DIR="$env:TEMP\lg-sidecar-test"
Start-Process -FilePath ".\dist-sidecar\langgraph-sidecar\langgraph-sidecar.exe" -ArgumentList "--port","0" -NoNewWindow
```

Expected: prints `READY {"port": ...}`; stop it manually after.

- [ ] **Step 3: Add electron-builder config**

`desktop/electron-builder.yml`:

```yaml
appId: com.langgraph.agent
productName: LangGraph Agent
directories:
  output: ../release
files:
  - main.js
  - preload.js
  - package.json
  - dist/**/*
extraResources:
  - from: ../dist-sidecar/langgraph-sidecar
    to: sidecar
win:
  target:
    - nsis
  artifactName: LangGraph-Agent-Setup-${version}.exe
nsis:
  oneClick: false
  allowToChangeInstallationDirectory: true
```

- [ ] **Step 4: Add scripts/dependencies**

In `desktop/package.json` add:

```json
"scripts": {
  "dev": "concurrently -k \"vite --port 5173\" \"wait-on http://localhost:5173 && node launch-electron.cjs\"",
  "build": "vite build",
  "test": "vitest run",
  "dist:win": "pnpm build && electron-builder --win nsis --config electron-builder.yml"
},
"devDependencies": {
  "electron-builder": "^26.0.0"
}
```

Run: `cd desktop && pnpm install`
Expected: lockfile updated.

- [ ] **Step 5: Build installer**

Run:

```powershell
cd D:\Vibecoding\Langgraph
powershell -ExecutionPolicy Bypass -File packaging\build-sidecar.ps1
cd desktop
pnpm dist:win
```

Expected: `D:\Vibecoding\Langgraph\release\LangGraph-Agent-Setup-0.1.0.exe` exists.

- [ ] **Step 6: Commit**

```bash
git add packaging/build-sidecar.ps1 desktop/package.json desktop/electron-builder.yml pnpm-lock.yaml
git commit -m "build: add Windows installer and bundled sidecar"
```

---

### Task 6: Clean Windows acceptance

**Files:**
- Create: `docs/acceptance/2026-10-27-clean-windows.md`

- [ ] **Step 1: Write the checklist**

Document exact steps: clean VM, install, first-run, wrong key then correct key, chat, restart, session persistence, log check, uninstall.

- [ ] **Step 2: Execute on a clean VM or Windows Sandbox**

Record pass/fail and error screenshots in the document.

- [ ] **Step 3: Fix blockers and commit**

```bash
git add docs/acceptance/2026-10-27-clean-windows.md
git commit -m "docs: record clean Windows acceptance for A1"
```
