const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("desktop", {
  getConfig: () => ipcRenderer.invoke("desktop:config"),
});
