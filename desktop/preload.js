const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  getConfig: () => ipcRenderer.invoke("desktop:config"),
  getSetupState: () => ipcRenderer.invoke("desktop:setup-state"),
  getSettings: () => ipcRenderer.invoke("desktop:get-settings"),
  testDeepSeek: (payload) => ipcRenderer.invoke("desktop:test-deepseek", payload),
  testSettings: (payload) => ipcRenderer.invoke("desktop:test-settings", payload),
  saveDeepSeek: (payload) => ipcRenderer.invoke("desktop:save-deepseek", payload),
  saveSettings: (payload) => ipcRenderer.invoke("desktop:save-settings", payload),
  exportDiagnostics: () => ipcRenderer.invoke("desktop:export-diagnostics"),
  getPathForFile: (file) => webUtils.getPathForFile(file),
});
