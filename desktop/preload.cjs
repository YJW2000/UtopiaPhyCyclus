const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('utopia', {
  call: (method, params) => ipcRenderer.invoke('utopia:call', method, params),
  openFile: kind => ipcRenderer.invoke('utopia:open-file', kind),
  readClipboardImage: () => ipcRenderer.invoke('utopia:read-clipboard-image'),
  saveFile: (kind, suggestedName) => ipcRenderer.invoke('utopia:save-file', kind, suggestedName),
  chooseLibrary: () => ipcRenderer.invoke('utopia:choose-library'),
  asset: path => ipcRenderer.invoke('utopia:asset', path),
  detectEnvironment: () => ipcRenderer.invoke('utopia:detect-environment'),
  installEnvironmentTool: id => ipcRenderer.invoke('utopia:install-environment-tool', id),
  openEnvironmentHelp: id => ipcRenderer.invoke('utopia:open-environment-help', id),
  tintWindowControls: color => ipcRenderer.invoke('utopia:tint-window-controls', color),
  papilotShow: bounds => ipcRenderer.invoke('utopia:papilot-show', bounds),
  papilotBounds: bounds => ipcRenderer.invoke('utopia:papilot-bounds', bounds),
  papilotHide: () => ipcRenderer.invoke('utopia:papilot-hide'),
  papilotModels: () => ipcRenderer.invoke('utopia:papilot-models'),
  papilotQuickNotes: notes => ipcRenderer.invoke('utopia:papilot-quick-notes', notes),
  papilotOpenQuestion: (id, revision, openManuscript) =>
    ipcRenderer.invoke('utopia:papilot-open-question', id, revision, openManuscript),
  onPapilotShortcut: listener => {
    const receive = () => listener();
    ipcRenderer.on('utopia:papilot-shortcut', receive);
    return () => ipcRenderer.off('utopia:papilot-shortcut', receive);
  },
});
