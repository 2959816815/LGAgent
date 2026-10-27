const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  getConfig: () => ipcRenderer.invoke("desktop:config"),
  getSetupState: () => ipcRenderer.invoke("desktop:setup-state"),
  testDeepSeek: (payload) => ipcRenderer.invoke("desktop:test-deepseek", payload),
  saveDeepSeek: (payload) => ipcRenderer.invoke("desktop:save-deepseek", payload),
  getPathForFile: (file) => webUtils.getPathForFile(file),
});
