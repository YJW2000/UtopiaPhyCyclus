const { contextBridge, ipcRenderer, webFrame } = require('electron');

function subscribe(channel, listener) {
  const receive = (_event, value) => listener(value);
  ipcRenderer.on(`papilot:${channel}`, receive);
  return () => ipcRenderer.off(`papilot:${channel}`, receive);
}

contextBridge.exposeInMainWorld('papilotDesktop', {
  platform: process.platform,
  workspaceName: '项目工作台',
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
    let active = true;
    let opening = false;
    let pending;
    let latestSequence = 0;
    const accept = async request => {
      if (!active || typeof request?.folder !== 'string' || request.sequence <= latestSequence) return;
      latestSequence = request.sequence;
      pending = request;
      if (opening) return;
      opening = true;
      try {
        while (active && pending) {
          const { folder, openManuscript } = pending;
          pending = undefined;
          await listener(folder, openManuscript);
        }
      } finally {
        opening = false;
      }
    };
    const receive = request => { void accept(request).catch(() => {}); };
    const dispose = subscribe('open-folder', receive);
    void ipcRenderer.invoke('papilot:take-folder').then(receive, () => {});
    return () => { active = false; dispose(); };
  },
  typelessStatus: () => Promise.resolve({ status: 'unavailable' }),
  typelessShortcut: () => Promise.resolve({ status: 'unavailable' }),
});

const PAPILOT_THEME_TOKENS = {
  '--bg': '--mc-surface',
  '--bg-subtle': '--mc-surface-soft',
  '--bg-muted': '--mc-input-bg',
  '--bg-hover': '--mc-surface-hover',
  '--bg-active': '--mc-accent-soft',
  '--sidebar': '--mc-sidebar',
  '--canvas': '--mc-bg-base',
  '--raised': '--mc-surface',
  '--thumb': '--mc-accent-soft',
  '--line': '--mc-border',
  '--line-strong': '--mc-border-strong',
  '--line-hover': '--mc-border-strong',
  '--text': '--mc-text',
  '--text-2': '--mc-muted',
  '--text-3': '--mc-muted',
  '--accent': '--mc-accent',
  '--accent-hover': '--mc-accent-dark',
  '--accent-soft': '--mc-accent-soft',
  '--accent-line': '--mc-accent',
  '--primary': '--mc-action-bg',
  '--primary-hover': '--mc-action-bg-hover',
  '--menu-selection': '--mc-action-bg',
  '--utopia-action-text': '--mc-action-text',
  '--r-lg': '--mc-control-radius',
  '--sans': '--mc-font-family',
  '--mono': '--mc-font-mono',
  'color-scheme': 'color-scheme',
};

function followDesktopTheme() {
  let theme;
  let observer;
  // Keep the view's native skin-colored background visible until the page has
  // received the desktop palette, even when the OS or prior workspace is dark.
  webFrame.insertCSS(`
    :root:not([data-utopia-theme-ready]) { visibility: hidden !important; }
    :root:not([data-utopia-theme-ready]),
    :root:not([data-utopia-theme-ready]) body { background: transparent !important; }
  `, { cssOrigin: 'user' });
  const apply = () => {
    const root = document.documentElement;
    if (root.dataset.theme !== theme['color-scheme']) root.dataset.theme = theme['color-scheme'];
    for (const [name, token] of Object.entries(PAPILOT_THEME_TOKENS)) {
      const value = theme[token];
      if (typeof value === 'string' && root.style.getPropertyValue(name) !== value) root.style.setProperty(name, value);
    }
  };

  subscribe('theme', tokens => {
    theme = tokens;
    try {
      // Keep the workbench's semantic colors and PDF reading mode in sync with
      // the desktop mode through Papilot's normal preference listener.
      const key = 'papilot:theme';
      const mode = theme['color-scheme'];
      if (localStorage.getItem(key) !== mode) {
        localStorage.setItem(key, mode);
        window.dispatchEvent(new StorageEvent('storage', { key, newValue: mode, storageArea: localStorage }));
      }
    } catch { /* The document theme can still be applied without storage. */ }
    apply();
    if (!observer) {
      // Papilot recalculates inline variables when its appearance changes and
      // copies those variables into the change review document.
      observer = new MutationObserver(apply);
      observer.observe(document.documentElement, { attributes: true, attributeFilter: ['style', 'data-theme'] });
    }
    document.documentElement.toggleAttribute('data-utopia-theme-ready', true);
  });
}

if (process.isMainFrame && /^http:\/\/127\.0\.0\.1:\d+$/.test(location.origin)) {
  // Papilot refreshes the active wheel's quick notes on window focus.
  subscribe('quick-notes-changed', () => window.dispatchEvent(new Event('focus')));
  followDesktopTheme();
  try {
    // The question bank already provides document navigation beside the PDF.
    if (!localStorage.getItem('utopia:document-layout')) {
      localStorage.setItem('papilot:sidebar', 'hidden');
      localStorage.setItem('utopia:document-layout', '1');
    }
    if (!localStorage.getItem('papilot:language')) localStorage.setItem('papilot:language', 'zh-CN');
    if (!localStorage.getItem('papilot:appearance')) {
      localStorage.setItem('papilot:appearance', JSON.stringify({
        preset: 'paper', accent: 'default', pdf: 'light', review: 'pdf',
        colors: { light: { background: '#faf8f3', foreground: '#30231b' }, dark: {} },
      }));
    }
  } catch { /* Storage restrictions should not prevent opening the editor. */ }
}
