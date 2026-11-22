const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('utopia', {
  call: (method, params) => ipcRenderer.invoke('utopia:call', method, params),
  openFile: kind => ipcRenderer.invoke('utopia:open-file', kind),
  readClipboardImage: () => ipcRenderer.invoke('utopia:read-clipboard-image'),
  saveFile: (kind, suggestedName) => ipcRenderer.invoke('utopia:save-file', kind, suggestedName),
  chooseLibrary: () => ipcRenderer.invoke('utopia:choose-library'),
  asset: path => ipcRenderer.invoke('utopia:asset', path),
  legacy: route => ipcRenderer.invoke('utopia:legacy', route),
  detectEnvironment: () => ipcRenderer.invoke('utopia:detect-environment'),
  installEnvironmentTool: id => ipcRenderer.invoke('utopia:install-environment-tool', id),
  openEnvironmentHelp: id => ipcRenderer.invoke('utopia:open-environment-help', id),
});
