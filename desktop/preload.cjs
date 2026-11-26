const { contextBridge, ipcRenderer } = require('electron');

let papilotShortcut;
let pendingPapilotRoute;
ipcRenderer.on('utopia:papilot-shortcut', (_event, route) => {
  const target = route === 'settings' ? 'settings' : 'papilot';
  if (papilotShortcut) papilotShortcut(target);
  else pendingPapilotRoute = target;
});

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
  papilotOpenQuestion: (id, revision, openManuscript) =>
    ipcRenderer.invoke('utopia:papilot-open-question', id, revision, openManuscript),
  onPapilotShortcut: listener => {
    papilotShortcut = listener;
    if (pendingPapilotRoute) {
      const route = pendingPapilotRoute;
      pendingPapilotRoute = undefined;
      queueMicrotask(() => { if (papilotShortcut === listener) listener(route); });
    }
    return () => { if (papilotShortcut === listener) papilotShortcut = undefined; };
  },
});
