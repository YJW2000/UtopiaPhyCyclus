const { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, net, protocol, session, shell } = require('electron');
const { execFileSync, spawn } = require('node:child_process');
const { createHash, randomUUID } = require('node:crypto');
const fs = require('node:fs');
const fsp = require('node:fs/promises');
const { createServer } = require('node:net');
const path = require('node:path');
const { createInterface } = require('node:readline');
const { Readable } = require('node:stream');
const { fileURLToPath, pathToFileURL } = require('node:url');
const { createEnvironment } = require('./environment.cjs');

const PRODUCT_NAME = '乌托邦高中物理题库助手';
const APPLICATION_ID = 'io.github.yjw2000.utopiaphycyclus.desktop.preview';
const SOURCE_ROOT = path.resolve(__dirname, '..');
const RESOURCES = app.isPackaged ? process.resourcesPath : path.join(SOURCE_ROOT, '.release-work', 'desktop-preview');
const environment = createEnvironment({ resources: RESOURCES });
const BACKEND = app.isPackaged ? path.join(RESOURCES, 'app-backend') : SOURCE_ROOT;
const RENDERER = path.join(__dirname, 'dist');
const RENDERER_URL = !app.isPackaged && process.env.UTOPIA_DESKTOP_VITE_URL === 'http://127.0.0.1:5173/'
  ? 'http://127.0.0.1:5173/' : 'utopia://app/';
const APP_ICON = app.isPackaged
  ? path.join(RESOURCES, 'UtopiaPhyCyclus.ico') : path.join(SOURCE_ROOT, 'static', 'UtopiaPhyCyclus-desktop.ico');
const WINDOW_CONTROLS = { color: '#00000000', symbolColor: '#26231E', height: 32 };
const MIME_TYPES = new Map(Object.entries({
  '.pdf': 'application/pdf', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.webp': 'image/webp', '.bmp': 'image/bmp', '.gif': 'image/gif', '.tif': 'image/tiff', '.tiff': 'image/tiff',
}));
const METHODS = new Set([
  'system.ping', 'library.demo', 'library.summary', 'library.search', 'library.assemble', 'library.imageInput',
  'system.check_latex',
  'workspace.fileInfo', 'workspace.saveFileInfo',
  'library.check', 'library.maintenanceStatus', 'context.chat', 'ui.load', 'ui.save', 'ui.providers',
  'workspace.list', 'workspace.open', 'workspace.resume', 'workspace.create',
  'workspace.save', 'workspace.import_pdf', 'workspace.reconstruct', 'workspace.compile', 'workspace.chat',
  'workspace.apply', 'workspace.dismiss', 'workspace.undo', 'workspace.export', 'workspace.export_tex', 'workspace.export_pdf',
  'workspace.chapters', 'task.get', 'task.cancel',
]);
const ROUTES = new Set(['entry', 'tex', 'pdf', 'browse', 'exam', 'tools', 'search', 'stats', 'intro', 'manual', 'settings', 'tags']);

protocol.registerSchemesAsPrivileged([
  { scheme: 'utopia', privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true } },
  { scheme: 'utopia-asset', privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true } },
]);
app.setName(PRODUCT_NAME);
app.setAppUserModelId(APPLICATION_ID);
app.setPath('userData', process.env.UTOPIA_DESKTOP_PROFILE || path.join(app.getPath('appData'), 'UtopiaPhyCyclusDesktop'));

let window;
let library;
let state;
let rpc;
let legacy;
let selection;
let closing;
let mayExit = false;
let reopenAfterQuit = false;
const maintenance = new Map();
const readers = new Set();
const writers = new Set();
const assetTokens = new Map();
const tokenPaths = new Map();

function inside(directory, file) {
  const relative = path.relative(directory, file);
  return Boolean(relative) && !path.isAbsolute(relative) && relative !== '..' && !relative.startsWith(`..${path.sep}`);
}

function samePath(first, second) {
  return path.relative(first, second) === '';
}

function isRenderer(url) {
  return typeof url === 'string' && url.startsWith(RENDERER_URL);
}

function isLegacy(url) {
  return Boolean(legacy?.base && typeof url === 'string' && url.startsWith(legacy.base));
}

function pythonExecutable() {
  if (app.isPackaged) return path.join(RESOURCES, 'python', 'python.exe');
  const options = [path.join(SOURCE_ROOT, '.venv', 'Scripts', 'python.exe'), path.join(RESOURCES, 'python', 'python.exe')];
  return options.find(file => fs.existsSync(file)) || 'python';
}

function startPython(mode, args, stdio, root = library, workspace = state) {
  const entry = path.join(BACKEND, ...(app.isPackaged ? [] : ['tools']), 'desktop_backend_entry.py');
  const options = {
    cwd: BACKEND,
    windowsHide: true,
    stdio,
    env: {
      ...process.env,
      UTOPIA_DESKTOP_LIBRARY: root,
      UTOPIA_DESKTOP_STATE: workspace,
      UTOPIA_DESKTOP_RESOURCES: RESOURCES,
      PYTHONPATH: '',
      PYTHONHOME: '',
      PYTHONNOUSERSITE: '1',
      STREAMLIT_SERVER_HEADLESS: 'true',
    },
  };
  return spawn(pythonExecutable(), ['-u', '-B', '-X', 'utf8', entry, mode, ...args], options);
}

function stopPython(child) {
  if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
  if (process.platform === 'win32') {
    try {
      const taskkill = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe');
      execFileSync(taskkill, ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore', timeout: 4000 });
      return;
    } catch { /* The process may have exited already. */ }
  }
  child.kill();
}

function runMaintenance(root, workspace) {
  return new Promise((resolve, reject) => {
    const child = startPython('maintenance', [], ['ignore', 'pipe', 'pipe'], root, workspace);
    let output = '';
    let diagnostic = '';
    const timer = setTimeout(() => {
      stopPython(child);
      reject(new Error('维护超时，下次打开题库时会重新检查。'));
    }, 90_000);
    child.stdout.setEncoding('utf8').on('data', data => { output = (output + data).slice(-4096); });
    child.stderr.setEncoding('utf8').on('data', data => { diagnostic = (diagnostic + data).slice(-2000); });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', code => {
      clearTimeout(timer);
      if (code !== 0) return reject(new Error(diagnostic.trim() || `题库维护进程已退出（${code}）。`));
      try { resolve(JSON.parse(output)); } catch (error) { reject(error); }
    });
  });
}

function scheduleMaintenance(root, workspace, phase) {
  // Switching back to the same bank must wait for its previous scan to finish.
  const previous = maintenance.get(workspace) || Promise.resolve();
  const current = previous.then(async () => {
    let report;
    try {
      report = { ok: true, result: await runMaintenance(root, workspace) };
    } catch (error) {
      report = { ok: false, error: error.message };
    }
    const file = path.join(workspace, `maintenance-${phase}.json`);
    const temp = `${file}.${randomUUID()}.tmp`;
    try {
      await fsp.writeFile(temp, JSON.stringify({ phase, finishedAt: new Date().toISOString(), ...report }), { flag: 'wx' });
      await fsp.rename(temp, file);
    } finally {
      await fsp.rm(temp, { force: true });
    }
  }).catch(error => console.error('无法记录题库维护结果。', error));
  maintenance.set(workspace, current);
  void current.then(() => { if (maintenance.get(workspace) === current) maintenance.delete(workspace); });
}

function createRpc() {
  const child = startPython('rpc', ['--library', library, '--state-dir', state], ['pipe', 'pipe', 'pipe']);
  const pending = new Map();
  const receiver = createInterface({ input: child.stdout, crlfDelay: Infinity });
  const service = {
    child,
    stop(reason) {
      finish(reason);
      stopPython(child);
    },
    send(method, params) {
      return new Promise((resolve, reject) => {
        const id = randomUUID();
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error('桌面服务响应超时。'));
        }, 180_000);
        pending.set(id, { resolve, reject, timer });
        child.stdin.write(`${JSON.stringify({ id, method, params })}\n`, 'utf8', error => {
          if (error) finish('无法向桌面服务发送请求。');
        });
      });
    },
  };
  let diagnostics = '';
  child.stderr.setEncoding('utf8').on('data', data => { diagnostics = (diagnostics + data).slice(-2000); });
  child.stdin.on('error', () => finish('桌面服务连接已关闭。'));
  child.once('error', error => finish(`无法启动桌面服务。${error.message}`));
  child.once('close', code => finish(`桌面服务已退出（${code ?? '已关闭'}）。${diagnostics.trim()}`));
  receiver.on('line', line => {
    let response;
    try {
      response = JSON.parse(line);
    } catch {
      return;
    }
    const operation = pending.get(response?.id);
    if (!operation) return;
    pending.delete(response.id);
    clearTimeout(operation.timer);
    if (response.error) {
      operation.reject(new Error(typeof response.error === 'string' ? response.error : response.error.message || '桌面服务操作失败。'));
    } else {
      operation.resolve(response.result);
    }
  });
  return service;

  function finish(reason) {
    if (rpc === service) rpc = undefined;
    receiver.close();
    for (const { reject, timer } of pending.values()) {
      clearTimeout(timer);
      reject(new Error(reason));
    }
    pending.clear();
  }
}

function reservePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(error => error ? reject(error) : resolve(port));
    });
  });
}

async function createLegacy(service) {
  const port = await reservePort();
  if (service !== legacy) throw new Error('题库连接已切换。');
  const prefix = `desktop-${randomUUID()}`;
  service.base = `http://127.0.0.1:${port}/${prefix}/`;
  const child = startPython('legacy', [
    '--server.address=127.0.0.1', `--server.port=${port}`, `--server.baseUrlPath=${prefix}`,
    '--server.headless=true', '--server.fileWatcherType=none', '--server.runOnSave=false',
    '--server.enableXsrfProtection=false', '--browser.gatherUsageStats=false',
  ], ['ignore', 'ignore', 'pipe']);
  service.child = child;
  let startupError;
  let diagnostics = '';
  child.stderr.setEncoding('utf8').on('data', data => { diagnostics = (diagnostics + data).slice(-2000); });
  child.once('error', error => { startupError = error; });
  child.once('close', () => {
    if (legacy === service) legacy = undefined;
  });
  const deadline = Date.now() + 45_000;
  while (legacy === service && !startupError && Date.now() < deadline) {
    try {
      const response = await fetch(`${service.base}_stcore/health`, { signal: AbortSignal.timeout(750) });
      if (response.ok && (await response.text()).trim() === 'ok') return service.base;
    } catch { /* The server is still starting. */ }
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  stopPython(child);
  throw new Error(`无法启动题库页面。${startupError?.message || diagnostics.trim()}`);
}

async function legacyUrl(route) {
  if (!ROUTES.has(route)) throw new Error('题库页面不存在。');
  if (!legacy) {
    const service = {};
    legacy = service;
    service.ready = createLegacy(service).catch(error => {
      if (legacy === service) legacy = undefined;
      throw error;
    });
  }
  const url = new URL(await legacy.ready);
  url.searchParams.set('desktop_page', route);
  return url.href;
}

function libraryRoot(directory) {
  if (typeof directory !== 'string' || !path.isAbsolute(directory)) return null;
  try {
    const selected = fs.realpathSync.native(directory);
    const root = path.basename(selected).toLowerCase() === 'chapters' ? path.dirname(selected) : selected;
    return fs.statSync(path.join(root, 'chapters')).isDirectory() ? root : null;
  } catch {
    return null;
  }
}

async function setLibrary(root) {
  if (library && samePath(library, root)) return;
  const profile = app.getPath('userData');
  const identity = createHash('sha256').update(process.platform === 'win32' ? root.toLowerCase() : root).digest('hex').slice(0, 24);
  const nextState = path.join(profile, 'workspaces', identity);
  await fsp.mkdir(nextState, { recursive: true });
  const resolvedState = await fsp.realpath(nextState);
  const temporary = path.join(profile, `library-${randomUUID()}.tmp`);
  try {
    await fsp.writeFile(temporary, JSON.stringify({ library: root }, null, 2), { mode: 0o600, flag: 'wx' });
    await fsp.rename(temporary, path.join(profile, 'library.json'));
  } finally {
    await fsp.rm(temporary, { force: true });
  }
  rpc?.stop('题库连接已切换。');
  stopPython(legacy?.child);
  legacy = undefined;
  if (library) scheduleMaintenance(library, state, 'shutdown');
  readers.clear();
  writers.clear();
  assetTokens.clear();
  tokenPaths.clear();
  library = root;
  state = resolvedState;
  scheduleMaintenance(root, state, 'startup');
}

async function connectLibrary() {
  const profile = app.getPath('userData');
  const libraryArgument = process.argv.indexOf('--library');
  const explicit = libraryArgument === -1 ? process.env.UTOPIA_DESKTOP_LIBRARY : process.argv[libraryArgument + 1];
  let saved;
  try {
    saved = JSON.parse(await fsp.readFile(path.join(profile, 'library.json'), 'utf8')).library;
  } catch { /* The setting is created on first run. */ }
  const existing = [explicit, saved, !app.isPackaged && SOURCE_ROOT, path.join(app.getPath('home'), 'UtopiaPhyCyclus')]
    .map(libraryRoot).find(Boolean);
  if (existing) {
    await setLibrary(existing);
  } else {
    const empty = path.join(profile, 'library');
    await fsp.mkdir(path.join(empty, 'chapters'), { recursive: true });
    await setLibrary(fs.realpathSync.native(empty));
  }
}

async function chooseLibrary() {
  const { canceled, filePaths } = await dialog.showOpenDialog(window, {
    title: '连接现有题库', defaultPath: library, properties: ['openDirectory'],
  });
  if (canceled) return null;
  const selected = libraryRoot(filePaths[0]);
  if (!selected) throw new Error('请选择包含 chapters 文件夹的题库根目录。');
  await setLibrary(selected);
  return library;
}

function fileOptions(kind) {
  if (kind === 'tex') return { name: 'LaTeX 源文件', extensions: ['tex'] };
  if (kind === 'pdf') return { name: 'PDF 文档', extensions: ['pdf'] };
  throw new Error('文件类型无效。');
}

function validAbsolute(file) {
  return typeof file === 'string' && file.length > 0 && file.length < 32_768 && path.isAbsolute(file) && !file.includes('\0');
}

async function resolveFile(file) {
  if (!validAbsolute(file)) throw new Error('文件路径无效。');
  const real = await fsp.realpath(file);
  if (!(await fsp.stat(real)).isFile()) throw new Error('文件不存在。');
  return real;
}

async function allowRead(file, extensions, internalFolders = ['demo', 'uploads', 'papers']) {
  const real = await resolveFile(file);
  if (!extensions.includes(path.extname(real).toLowerCase())) throw new Error('文件类型无效。');
  if (![...readers].some(grant => samePath(grant, real)) && !internalFolders.some(folder => inside(path.join(state, folder), real))) {
    throw new Error('请先通过打开文件对话框选择文件。');
  }
  return real;
}

async function savePath(file) {
  if (!validAbsolute(file)) throw new Error('保存路径无效。');
  const target = path.join(await fsp.realpath(path.dirname(file)), path.basename(file));
  try {
    const current = await fsp.realpath(target);
    if (!samePath(current, target)) throw new Error('请选择普通文件作为保存目标。');
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  return target;
}

async function normalizeCall(method, params) {
  if (!METHODS.has(method)) throw new Error('此桌面操作不可用。');
  if (!params || typeof params !== 'object' || Array.isArray(params)) throw new Error('桌面请求参数无效。');
  const result = { ...params };
  if (method === 'workspace.import_pdf') result.path = await allowRead(params.path, ['.pdf']);
  if (method === 'workspace.create') {
    if (params.path) result.path = await allowRead(params.path, ['.tex', '.pdf']);
    if (params.pdfPath) result.pdfPath = await allowRead(params.pdfPath, ['.pdf']);
    if (params.previewPath) result.previewPath = await allowRead(params.previewPath, ['.pdf'], ['demo']);
  }
  if (method === 'workspace.export_tex' || method === 'workspace.export_pdf') {
    result.path = await savePath(params.path);
    const grant = [...writers].find(file => samePath(file, result.path));
    const extension = method === 'workspace.export_pdf' ? '.pdf' : '.tex';
    if (!grant || path.extname(result.path).toLowerCase() !== extension) throw new Error('请先选择导出文件的保存位置。');
    writers.delete(grant);
  }
  return result;
}

async function assetPath(file) {
  const real = await resolveFile(file);
  if (!MIME_TYPES.has(path.extname(real).toLowerCase())) throw new Error('预览文件类型无效。');
  const chapters = path.join(library, 'chapters');
  let realChapters = chapters;
  try {
    realChapters = await fsp.realpath(chapters);
  } catch { /* The library may have been moved or disconnected. */ }
  if (!inside(state, real) && !inside(realChapters, real) && ![...readers].some(grant => samePath(grant, real))) {
    throw new Error('没有访问该预览文件的权限。');
  }
  return real;
}

async function assetUrl(file) {
  const real = await assetPath(file);
  let token = assetTokens.get(real);
  if (!token) {
    token = randomUUID();
    assetTokens.set(real, token);
    tokenPaths.set(token, real);
  }
  return `utopia-asset://local/${token}`;
}

function parseRange(header, size) {
  const match = /^bytes=(\d*)-(\d*)$/.exec(header);
  if (!match || !size || (!match[1] && !match[2])) return null;
  const first = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]));
  const last = match[1] && match[2] ? Math.min(size - 1, Number(match[2])) : size - 1;
  if (first > last || !Number.isSafeInteger(first) || !Number.isSafeInteger(last)) return null;
  return { start: first, end: last };
}

async function serveAsset(request) {
  const url = new URL(request.url);
  const token = url.host === 'local' && url.pathname.slice(1);
  if (!token || !tokenPaths.has(token)) return new Response(null, { status: 404 });
  const headers = new Headers({
    'Access-Control-Allow-Origin': RENDERER_URL.slice(0, -1),
    'Access-Control-Allow-Headers': 'Range',
    'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
    'Access-Control-Expose-Headers': 'Accept-Ranges, Content-Length, Content-Range',
    'Cross-Origin-Resource-Policy': 'cross-origin',
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
  });
  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers });
  if (request.method !== 'GET' && request.method !== 'HEAD') return new Response(null, { status: 405, headers });
  try {
    const file = await assetPath(tokenPaths.get(token));
    const { size } = await fsp.stat(file);
    const requested = request.headers.get('range');
    const range = requested && parseRange(requested, size);
    headers.set('Accept-Ranges', 'bytes');
    headers.set('Content-Type', MIME_TYPES.get(path.extname(file).toLowerCase()));
    if (requested && !range) {
      headers.set('Content-Range', `bytes */${size}`);
      return new Response(null, { status: 416, headers });
    }
    if (range) headers.set('Content-Range', `bytes ${range.start}-${range.end}/${size}`);
    headers.set('Content-Length', String(range ? range.end - range.start + 1 : size));
    const body = request.method === 'GET' ? Readable.toWeb(fs.createReadStream(file, range || undefined)) : null;
    return new Response(body, { status: range ? 206 : 200, headers });
  } catch {
    return new Response(null, { status: 404, headers });
  }
}

async function serveRenderer(request) {
  const url = new URL(request.url);
  if (url.host !== 'app' || request.method !== 'GET') return new Response(null, { status: 404 });
  const relative = decodeURIComponent(url.pathname.slice(1)) || 'index.html';
  const file = path.resolve(RENDERER, relative);
  if (!inside(RENDERER, file)) return new Response(null, { status: 404 });
  const response = await net.fetch(pathToFileURL(file).href);
  const headers = new Headers(response.headers);
  if (file.endsWith('.mjs')) headers.set('Content-Type', 'text/javascript');
  if (file.endsWith('.wasm')) headers.set('Content-Type', 'application/wasm');
  headers.set('Content-Security-Policy', [
    "default-src 'self'", "script-src 'self' 'wasm-unsafe-eval'", "style-src 'self' 'unsafe-inline'",
    "img-src 'self' utopia-asset: blob: data:", "font-src 'self' blob: data:",
    "connect-src 'self' utopia-asset:", "worker-src 'self' blob:",
    'frame-src http://127.0.0.1:*', "object-src 'none'", "base-uri 'none'", "form-action 'none'",
  ].join('; '));
  headers.set('X-Content-Type-Options', 'nosniff');
  return new Response(response.body, { status: response.status, headers });
}

async function copiedImageFile(uriList) {
  for (const entry of uriList.split(/\r?\n/)) {
    const url = URL.parse(entry);
    if (url?.protocol !== 'file:' || url.host) continue;
    let handle;
    try {
      const file = fileURLToPath(url);
      if (file.startsWith('\\\\')) continue;
      const extension = path.extname(file).toLowerCase();
      if (!['.png', '.jpg', '.jpeg'].includes(extension)) continue;
      handle = await fsp.open(file, 'r');
      if (!(await handle.stat()).isFile()) continue;
      const signature = Buffer.alloc(8);
      await handle.read(signature, 0, signature.length, 0);
      const type = extension === '.png' ? 'png' : 'jpeg';
      const prefix = type === 'png' ? '89504e470d0a1a0a' : 'ffd8ff';
      if (!signature.toString('hex').startsWith(prefix)) continue;
      const bytes = await handle.readFile();
      return { name: path.basename(file), type, data: bytes.toString('base64') };
    } catch {
      // Ignore missing files and move on to the next copied image.
    } finally {
      await handle?.close();
    }
  }
  return null;
}

function registerIpc() {
  function handle(channel, callback) {
    ipcMain.handle(`utopia:${channel}`, (event, ...args) => {
      if (event.sender !== window?.webContents || event.senderFrame !== event.sender.mainFrame || !isRenderer(event.senderFrame.url)) {
        throw new Error('此页面不能使用桌面服务。');
      }
      return callback(...args);
    });
  }
  handle('call', async (method, params = {}) => {
    const activeLibrary = library;
    const normalized = await normalizeCall(method, params);
    if (['library.summary', 'library.search', 'library.assemble', 'library.check', 'library.maintenanceStatus'].includes(method)) {
      await maintenance.get(state);
    }
    if (library !== activeLibrary) throw new Error('题库连接已切换。');
    if (method === 'library.maintenanceStatus') {
      const reports = await Promise.all(['startup', 'shutdown'].map(async phase => {
        try {
          return JSON.parse(await fsp.readFile(path.join(state, `maintenance-${phase}.json`), 'utf8'));
        } catch (error) {
          if (error.code === 'ENOENT') return null;
          throw new Error('无法读取题库维护记录。');
        }
      }));
      return { startup: reports[0], shutdown: reports[1] };
    }
    if (!rpc) rpc = createRpc();
    return rpc.send(method, normalized);
  });
  handle('legacy', legacyUrl);
  handle('asset', assetUrl);
  handle('read-clipboard-image', async () => {
    try {
      const items = await clipboard.read();
      for (const item of items) {
        for (const mime of ['image/png', 'image/jpeg']) {
          if (!item.types.includes(mime)) continue;
          const blob = await item.getType(mime);
          if (!blob.size) continue;
          const type = mime === 'image/png' ? 'png' : 'jpeg';
          const data = Buffer.from(await blob.arrayBuffer()).toString('base64');
          return { name: `剪贴板图片.${type}`, type, data };
        }
      }
      // Electron maps Explorer's copied files (CF_HDROP) to a local file URI list.
      for (const item of items) {
        if (!item.types.includes('text/uri-list')) continue;
        const image = await copiedImageFile(await (await item.getType('text/uri-list')).text());
        if (image) return image;
      }
      return null;
    } catch {
      throw new Error('无法读取剪贴板图片，请重新复制图片后再粘贴。');
    }
  });
  handle('detect-environment', environment.detect);
  handle('install-environment-tool', environment.install);
  handle('open-environment-help', id => shell.openExternal(environment.helpUrl(id)));
  handle('tint-window-controls', color => {
    if (process.platform === 'win32' && typeof color === 'string' && /^#[a-f\d]{6}$/i.test(color)) {
      window?.setTitleBarOverlay({ ...WINDOW_CONTROLS, symbolColor: color });
    }
  });
  handle('choose-library', () => {
    if (!selection) selection = chooseLibrary().finally(() => { selection = undefined; });
    return selection;
  });
  handle('open-file', async kind => {
    const { canceled, filePaths } = await dialog.showOpenDialog(window, {
      title: '打开文件', properties: ['openFile'], filters: [fileOptions(kind)],
    });
    if (canceled) return null;
    const selected = await resolveFile(filePaths[0]);
    if (path.extname(selected).toLowerCase() !== `.${kind}`) throw new Error('文件类型无效。');
    readers.add(selected);
    return selected;
  });
  handle('save-file', async (kind, suggestedName) => {
    const { canceled, filePath } = await dialog.showSaveDialog(window, {
      title: '另存为', filters: [fileOptions(kind)],
      defaultPath: typeof suggestedName === 'string' ? path.basename(suggestedName) : undefined,
    });
    if (canceled || !filePath) return null;
    const selected = await savePath(filePath);
    if (path.extname(selected).toLowerCase() !== `.${kind}`) throw new Error('文件类型无效。');
    writers.add(selected);
    return selected;
  });
}

function openWindow() {
  Menu.setApplicationMenu(null);
  window = new BrowserWindow({
    title: PRODUCT_NAME,
    icon: APP_ICON,
    width: 1440,
    height: 940,
    minWidth: 850,
    minHeight: 600,
    show: false,
    backgroundColor: '#F7F4EC',
    ...(process.platform === 'win32' ? { titleBarStyle: 'hidden', titleBarOverlay: WINDOW_CONTROLS } : {}),
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      navigateOnDragDrop: false,
    },
  });
  window.once('ready-to-show', () => window?.show());
  window.once('closed', () => { window = undefined; });
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  const restrictNavigation = event => {
    const permitted = event.isMainFrame ? isRenderer(event.url)
      : isLegacy(event.url) || event.url === 'about:blank' || event.url === 'about:srcdoc';
    if (!permitted) event.preventDefault();
  };
  window.webContents.on('will-frame-navigate', restrictNavigation);
  window.webContents.on('will-redirect', restrictNavigation);
  const opened = window;
  return opened.loadURL(RENDERER_URL).then(() => {
    if (!opened.isDestroyed() && !opened.isVisible()) opened.show();
  });
}

if (app.requestSingleInstanceLock()) {
  app.on('second-instance', () => {
    if (closing) {
      reopenAfterQuit = true;
      return;
    }
    if (!window) return;
    if (window.isMinimized()) window.restore();
    window.focus();
  });
  app.on('window-all-closed', () => app.quit());
  app.on('will-quit', event => {
    if (mayExit) return;
    event.preventDefault();
    if (closing) return;
    closing = (async () => {
      // will-quit fires after the windows close, for both the title bar and app.quit().
      if (selection) await selection.catch(() => {});
      rpc?.stop('桌面应用正在关闭。');
      stopPython(legacy?.child);
      if (library) scheduleMaintenance(library, state, 'shutdown');
      await Promise.all(maintenance.values());
    })().finally(() => {
      mayExit = true;
      if (reopenAfterQuit) app.relaunch();
      app.quit();
    });
  });
  app.whenReady().then(async () => {
    await connectLibrary();
    protocol.handle('utopia', serveRenderer);
    protocol.handle('utopia-asset', serveAsset);
    session.defaultSession.setPermissionCheckHandler((_contents, permission, origin, details) =>
      Boolean(legacy?.base?.startsWith(`${origin}/`)) &&
      (permission === 'clipboard-read' || (permission === 'media' && details.mediaType === 'audio')));
    session.defaultSession.setPermissionRequestHandler((_contents, permission, callback, details) => {
      const legacyOrigin = isLegacy(details.requestingUrl || '');
      callback(legacyOrigin && (permission === 'clipboard-read' ||
        (permission === 'media' && details.mediaTypes?.length === 1 && details.mediaTypes[0] === 'audio')));
    });
    registerIpc();
    await openWindow();
  }).catch(error => {
    dialog.showErrorBox(PRODUCT_NAME, `无法打开桌面应用。\n${error.message}`);
    app.quit();
  });
} else {
  app.quit();
}
