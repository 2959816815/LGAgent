const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  getConfig: () => ipcRenderer.invoke("desktop:config"),
  getPathForFile: (file) => webUtils.getPathForFile(file),
});
