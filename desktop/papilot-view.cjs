const { WebContentsView, Notification, dialog, ipcMain, shell } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');

const WINDOW_CSS = `
  .desktop-window-drag { display: none !important; }
  [data-desktop] .main, [data-desktop] .toolbar { --desktop-window-space: 0px !important; }
  .manuscript-side .new-task-panel:not(.has-notes) .new-task-start {
    margin-block: 0;
    padding-block: 4px 16px;
  }
  .manuscript-side .new-task-panel:not(.has-notes) .new-task-welcome {
    flex-direction: row;
    justify-content: flex-start;
    margin-bottom: 4px;
  }
  .manuscript-side .new-task-welcome svg { width: 20px; height: 20px; }
  .manuscript-side .new-task-panel:not(.has-notes) .skill-menu { top: calc(100% + 6px); bottom: auto; }
`;

function originOf(url) {
  try { return new URL(url).origin; } catch { return ''; }
}

function paperTemplate(url) {
  try {
    const link = new URL(url);
    if (!['papilot:', 'utopia-papilot:'].includes(link.protocol) || link.hostname !== 'new') return null;
    const id = link.searchParams.get('template') || '';
    const version = link.searchParams.get('version') || '';
    return /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(id) && id.length <= 80
      && /^\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.]+)?$/.test(version) && version.length <= 40 ? { id, version } : null;
  } catch { return null; }
}

function createPapilotView({ runtime, resources, getWindow }) {
  let view;
  let origin;
  let loadedRuntime;
  let attached;
  let requested = false;
  let latestBounds;
  let showJob;
  let pendingFolder;
  let folderSequence = 0;
  let pendingTemplate;
  let pendingTask;
  let readyForFolder = false;
  const notifications = new Set();

  const desktopHelper = name => import(pathToFileURL(path.join(resources, 'papilot', 'desktop', `${name}.js`)).href);

  function checkedBounds(bounds) {
    if (!bounds || ['x', 'y', 'width', 'height'].some(key => !Number.isFinite(bounds[key]))) {
      throw new Error('项目工作台的显示区域无效。');
    }
    return bounds;
  }

  function resize() {
    const parent = attached;
    if (!view || !parent || parent.isDestroyed() || !latestBounds) return;
    const zoom = parent.webContents.getZoomFactor();
    const [width, height] = parent.getContentSize();
    const left = Math.max(0, Math.min(width, Math.round(latestBounds.x * zoom)));
    const top = Math.max(0, Math.min(height, Math.round(latestBounds.y * zoom)));
    const right = Math.max(left, Math.min(width, Math.round((latestBounds.x + latestBounds.width) * zoom)));
    const bottom = Math.max(top, Math.min(height, Math.round((latestBounds.y + latestBounds.height) * zoom)));
    view.setBounds({ x: left, y: top, width: right - left, height: bottom - top });
    view.setVisible(right > left && bottom > top);
  }

  function hide() {
    requested = false;
    if (attached && !attached.isDestroyed() && view) attached.contentView.removeChildView(view);
    if (view && !view.webContents.isDestroyed()) view.webContents.setBackgroundThrottling(true);
    attached = undefined;
  }

  function showShortcut() {
    const parent = getWindow();
    if (!parent || parent.isDestroyed()) return;
    if (parent.isMinimized()) parent.restore();
    parent.show();
    parent.focus();
    parent.webContents.send('utopia:papilot-shortcut');
  }

  function openLink(url, activate = true) {
    const choice = paperTemplate(url);
    if (!choice) return false;
    pendingTemplate = choice;
    if (activate) showShortcut();
    if (view && !view.webContents.isDestroyed()) view.webContents.send('papilot:new-paper');
    return true;
  }

  function external(url) {
    if (openLink(url, false)) return;
    try {
      const parsed = new URL(url);
      if (['https:', 'http:'].includes(parsed.protocol) && !parsed.username && !parsed.password) {
        void shell.openExternal(parsed.href).catch(() => {});
      }
    } catch { /* A malformed link has no native action. */ }
  }

  function createView() {
    const child = new WebContentsView({ webPreferences: {
      preload: path.join(__dirname, 'papilot-preload.cjs'),
      partition: 'persist:utopia-papilot',
      backgroundThrottling: false,
      sandbox: true, nodeIntegration: false, contextIsolation: true, navigateOnDragDrop: false,
    } });
    child.setBackgroundColor('#faf8f3');
    const contents = child.webContents;
    contents.session.setPermissionCheckHandler(() => false);
    contents.session.setPermissionRequestHandler((_contents, _permission, done) => done(false));
    contents.setWindowOpenHandler(({ url }) => {
      if (originOf(url) === origin) return { action: 'allow', overrideBrowserWindowOptions: {
        parent: getWindow(), webPreferences: {
          partition: 'persist:utopia-papilot', sandbox: true, contextIsolation: true, nodeIntegration: false, preload: undefined,
        },
      } };
      external(url);
      return { action: 'deny' };
    });
    contents.on('did-create-window', childWindow => {
      childWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
      childWindow.webContents.on('will-frame-navigate', details => {
        if (originOf(details.url) !== origin) details.preventDefault();
      });
    });
    contents.on('will-attach-webview', event => event.preventDefault());
    contents.on('will-frame-navigate', details => {
      if (originOf(details.url) !== origin && details.url !== 'about:blank') {
        details.preventDefault();
        if (details.isMainFrame) external(details.url);
      }
    });
    contents.on('will-redirect', details => {
      if (originOf(details.url) !== origin) details.preventDefault();
    });
    contents.on('did-start-navigation', details => {
      if (details.isMainFrame && !details.isSameDocument) readyForFolder = false;
    });
    contents.on('dom-ready', () => {
      if (originOf(contents.getURL()) === origin) void contents.insertCSS(WINDOW_CSS).catch(() => {});
    });
    contents.on('before-input-event', (event, input) => {
      if (input.type !== 'keyDown' || input.isAutoRepeat || !input.control || input.shift || input.alt) return;
      if (input.key.toLowerCase() === 'o') {
        event.preventDefault();
        void chooseDirectory().then(folder => { if (folder) return requestFolder(folder); }).catch(() => {});
      } else if (input.key === ',') {
        event.preventDefault();
        contents.send('papilot:open-settings');
      }
    });
    contents.on('destroyed', () => { if (view === child) { view = undefined; readyForFolder = false; } });
    return child;
  }

  async function show(bounds) {
    latestBounds = checkedBounds(bounds);
    requested = true;
    if (!showJob) showJob = (async () => {
      const started = await runtime.start();
      if (!requested) return;
      if (!view) view = createView();
      const target = view;
      const parent = getWindow();
      if (!parent || parent.isDestroyed() || target.webContents.isDestroyed()) return;
      if (attached !== parent) {
        // A child view can be considered hidden by Chromium even with valid
        // bounds. Papilot waits for visibility before it reads its first state.
        target.webContents.setBackgroundThrottling(false);
        parent.contentView.addChildView(target);
        attached = parent;
      }
      // The manuscript starts its initial state request only while visible.
      resize();
      if (loadedRuntime !== started || origin !== started.origin || originOf(target.webContents.getURL()) !== started.origin) {
        origin = started.origin;
        readyForFolder = false;
        await target.webContents.loadURL(origin);
        loadedRuntime = started;
      }
      if (!requested || attached !== parent || target.webContents.isDestroyed()) return;
      if (pendingTask) target.webContents.send('papilot:open-task', pendingTask);
      pendingTask = undefined;
    })().finally(() => { showJob = undefined; });
    return showJob;
  }

  async function quickNotes(notes) {
    const started = await runtime.start();
    const endpoint = new URL('/api/quick-notes', started.origin);
    const options = { redirect: 'error', signal: AbortSignal.timeout(15_000) };
    if (notes !== undefined) {
      // The upstream API issues the write token in its local state response.
      const state = await fetch(new URL('/api/state', started.origin), options);
      if (!state.ok) throw new Error('无法连接魔法笔设置，请重试。');
      const { csrf } = await state.json();
      if (!csrf) throw new Error('魔法笔设置连接已过期，请重试。');
      options.method = 'POST';
      options.headers = { 'Content-Type': 'application/json', 'X-Papilot-Token': csrf };
      options.body = JSON.stringify({ quickNotes: notes });
    }
    const response = await fetch(endpoint, options);
    if (!response.ok) throw new Error(notes === undefined ? '读取魔法笔默认语句失败。' : '保存失败，请检查语句名称和内容后重试。');
    const result = await response.json();
    if (notes !== undefined && view && !view.webContents.isDestroyed() && loadedRuntime === started) {
      view.webContents.send('papilot:quick-notes-changed');
    }
    return result.quickNotes;
  }

  async function requestFolder(folder, openManuscript = false, isCurrent = () => true) {
    if (!isCurrent()) return;
    const projectPath = await runtime.projectPath(folder);
    if (!isCurrent()) return;
    const request = { folder: projectPath, openManuscript, sequence: ++folderSequence };
    if (readyForFolder && runtime.current()?.origin === origin && view && !view.webContents.isDestroyed() && originOf(view.webContents.getURL()) === origin) {
      view.webContents.send('papilot:open-folder', request);
    } else {
      pendingFolder = request;
    }
  }

  async function chooseDirectory() {
    const parent = getWindow();
    if (!parent || parent.isDestroyed()) return null;
    const environment = await runtime.getEnvironment();
    const { canceled, filePaths } = await dialog.showOpenDialog(parent, {
      title: '打开 LaTeX 项目目录', properties: ['openDirectory'],
      ...(environment.kind === 'wsl' ? { defaultPath: `\\\\wsl.localhost\\${environment.distribution}\\home` } : {}),
    });
    return canceled ? null : filePaths[0] || null;
  }

  function authorized(event) {
    return view && !view.webContents.isDestroyed() && event.sender === view.webContents
      && event.senderFrame === event.sender.mainFrame && runtime.current()?.origin === origin && originOf(event.senderFrame?.url) === origin;
  }

  async function chooseEnvironment() {
    const { chooseEnvironment: choose } = await desktopHelper('environment-window');
    const previous = await runtime.getEnvironment();
    choose({ parent: getWindow(), current: previous, assets: path.join(resources, 'papilot', 'wsl'),
      root: path.join(resources, 'papilot'), apply: async value => {
        await runtime.setEnvironment(value);
        origin = undefined;
        readyForFolder = false;
        if (requested && latestBounds) {
          try { await show(latestBounds); }
          catch (failure) {
            await runtime.setEnvironment(previous);
            await show(latestBounds);
            throw failure;
          }
        }
      },
    });
  }

  function register() {
    function handle(name, fn) {
      ipcMain.handle(`papilot:${name}`, (event, ...args) => {
        if (!authorized(event)) throw new Error('此页面不能使用项目工作台服务。');
        return fn(...args);
      });
    }
    handle('choose-directory', async () => {
      const folder = await chooseDirectory();
      return folder ? runtime.projectPath(folder) : null;
    });
    handle('take-folder', () => {
      readyForFolder = true;
      const folder = pendingFolder;
      pendingFolder = undefined;
      return folder;
    });
    handle('take-new-paper', () => {
      const choice = pendingTemplate;
      pendingTemplate = undefined;
      return choice;
    });
    handle('current-environment', async () => {
      const environment = await runtime.getEnvironment();
      return environment.kind === 'wsl' ? `WSL · ${environment.distribution}` : 'Windows';
    });
    handle('open-environment', chooseEnvironment);
    handle('window', action => {
      const parent = getWindow();
      if (!parent || parent.isDestroyed()) return;
      if (action === 'minimize') parent.minimize();
      if (action === 'maximize') { if (parent.isMaximized()) parent.unmaximize(); else parent.maximize(); }
      if (action === 'close') parent.close();
    });
    handle('overleaf-sign-in', async value => {
      if (typeof value !== 'string') return null;
      try {
        const server = new URL(value);
        if (!['http:', 'https:'].includes(server.protocol) || server.username || server.password) return null;
        const { overleafSignIn } = await desktopHelper('overleaf-sign-in');
        return await overleafSignIn(server.origin, getWindow());
      } catch { return null; }
    });
    handle('notify', alert => {
      if (!Notification.isSupported() || !alert || typeof alert.title !== 'string' || typeof alert.body !== 'string') return false;
      const item = new Notification({ title: alert.title.slice(0, 140), body: alert.body.slice(0, 500), silent: !alert.sound });
      notifications.add(item);
      item.once('close', () => notifications.delete(item));
      item.once('failed', () => notifications.delete(item));
      item.once('click', () => {
        if (typeof alert.taskId === 'string' && alert.taskId.length <= 128) {
          if (attached && readyForFolder) view.webContents.send('papilot:open-task', alert.taskId);
          else pendingTask = alert.taskId;
        }
        showShortcut();
      });
      item.show();
      return true;
    });
  }

  async function close() {
    hide();
    for (const notification of notifications) notification.close();
    if (view && !view.webContents.isDestroyed()) view.webContents.close({ waitForBeforeUnload: false });
    return runtime.stop();
  }

  return { register, show, resize: bounds => { latestBounds = checkedBounds(bounds); resize(); }, hide, openLink, quickNotes, requestFolder, close };
}

module.exports = { createPapilotView };
