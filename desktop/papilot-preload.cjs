const { contextBridge, ipcRenderer } = require('electron');

function subscribe(channel, listener) {
  const receive = (_event, value) => listener(value);
  ipcRenderer.on(`papilot:${channel}`, receive);
  return () => ipcRenderer.off(`papilot:${channel}`, receive);
}

contextBridge.exposeInMainWorld('papilotDesktop', {
  platform: process.platform,
  minimize: () => ipcRenderer.invoke('papilot:window', 'minimize'),
  maximize: () => ipcRenderer.invoke('papilot:window', 'maximize'),
  close: () => ipcRenderer.invoke('papilot:window', 'close'),
  chooseDirectory: () => ipcRenderer.invoke('papilot:choose-directory'),
  overleafSignIn: origin => ipcRenderer.invoke('papilot:overleaf-sign-in', origin),
  openEnvironment: () => ipcRenderer.invoke('papilot:open-environment'),
  environment: () => ipcRenderer.invoke('papilot:current-environment'),
  notify: alert => ipcRenderer.invoke('papilot:notify', alert),
  onOpenTask: listener => subscribe('open-task', listener),
  takeNewPaper: () => ipcRenderer.invoke('papilot:take-new-paper'),
  onNewPaper: listener => subscribe('new-paper', listener),
  onOpenSettings: listener => subscribe('open-settings', listener),
  onOpenFolder: listener => {
    const accept = folder => { if (typeof folder === 'string') listener(folder); };
    const dispose = subscribe('open-folder', accept);
    void ipcRenderer.invoke('papilot:take-folder').then(accept, () => {});
    return dispose;
  },
  typelessStatus: () => Promise.resolve({ status: 'unavailable' }),
  typelessShortcut: () => Promise.resolve({ status: 'unavailable' }),
});

// Give the embedded workspace the app's initial warm appearance. Subsequent
// changes in the upstream appearance editor use its normal persistent state.
if (process.isMainFrame && /^http:\/\/127\.0\.0\.1:\d+$/.test(location.origin)) {
  try {
    if (!localStorage.getItem('papilot:language')) localStorage.setItem('papilot:language', 'zh-CN');
    if (!localStorage.getItem('papilot:appearance')) {
      localStorage.setItem('papilot:appearance', JSON.stringify({
        preset: 'paper', accent: 'default', pdf: 'light', review: 'pdf',
        colors: { light: { background: '#faf8f3', foreground: '#30231b' }, dark: {} },
      }));
    }
  } catch { /* Storage restrictions should not prevent opening the editor. */ }
}
