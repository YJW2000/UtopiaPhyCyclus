const { execFile, spawn } = require('node:child_process');
const { createHash, randomUUID } = require('node:crypto');
const { existsSync } = require('node:fs');
const fs = require('node:fs/promises');
const http = require('node:http');
const path = require('node:path');
const { setTimeout: delay } = require('node:timers/promises');
const { pathToFileURL } = require('node:url');

const READY_TIMEOUT = 180_000;
const SHUTDOWN_TIMEOUT = 4_000;
const LOG_LIMIT = 5 * 1024 * 1024;
const DESKTOP_PROVIDERS = [
  'codex', 'claude', 'copilot', 'opencode', 'gemini', 'cursor', 'codebuddy', 'qoder',
  'agy', 'workbuddy', 'pi', 'grok', 'kimi', 'glm', 'deepseek', 'qwen',
];

function modelCatalog(state) {
  const runtimes = state.runtimes ?? { codex: state.runtime };
  const catalog = {};
  for (const provider of DESKTOP_PROVIDERS) {
    const runtime = runtimes[provider === 'agy' ? 'antigravity' : provider];
    if (!runtime?.connected || runtime.installed === false || runtime.hidden || !Array.isArray(runtime.models)) continue;
    const hidden = new Set(Array.isArray(runtime.hiddenModels) ? runtime.hiddenModels : []);
    const listed = new Map();
    for (const model of runtime.models) {
      if (typeof model?.id !== 'string' || !/^[a-zA-Z0-9][a-zA-Z0-9._:/@+\-]{0,199}$/.test(model.id) || hidden.has(model.id)) continue;
      const name = typeof model.name === 'string' ? model.name.trim().slice(0, 200) : '';
      listed.set(model.id, { id: model.id, name: name || model.id });
    }
    if (listed.size) catalog[provider] = [...listed.values()];
  }
  return catalog;
}

function absolutePaths(entries) {
  const directories = new Map();
  for (const value of entries) {
    const directory = value?.trim().replace(/^"|"$/g, '');
    if (!directory || !path.isAbsolute(directory)) continue;
    const name = path.normalize(directory);
    const key = process.platform === 'win32' ? name.toLowerCase() : name;
    if (!directories.has(key)) directories.set(key, name);
  }
  return [...directories.values()];
}

function sharingOptions(env) {
  if (!env.PAPILOT_PUBLIC_ORIGIN || !env.PAPILOT_TAILSCALE_LOGIN?.trim()) return undefined;
  try {
    const owner = env.PAPILOT_TAILSCALE_LOGIN.trim();
    const url = new URL(env.PAPILOT_PUBLIC_ORIGIN);
    if (url.protocol === 'https:' && url.pathname === '/' && !url.search && !url.hash && !url.username && !url.password) {
      return { origin: url.origin, owner };
    }
  } catch { /* A partially configured share remains local. */ }
}

function buildEnvironment({ inherited, node, root, data, resources, port, instance }) {
  const env = { ...inherited };
  const existing = Object.keys(env).filter(key => key.toLowerCase() === 'path').flatMap(key => env[key]?.split(path.delimiter) || []);
  const windows = process.platform === 'win32';
  const texRoot = path.join(resources, 'runtimes', 'texlive');
  const texBin = path.join(texRoot, 'bin', 'windows');
  const bundledTex = windows && existsSync(path.join(texBin, 'latexmk.exe'));
  const completeTex = windows && existing.find(directory => {
    if (!path.isAbsolute(directory)) return false;
    return ['latexmk.exe', 'xelatex.exe', 'synctex.exe'].every(executable => existsSync(path.join(directory, executable)));
  });
  const useBundle = bundledTex && (!completeTex || path.normalize(completeTex).toLowerCase() === texBin.toLowerCase());
  const latex = completeTex || (bundledTex ? texBin : undefined);
  const npmCli = path.join(root, 'runtime', 'node_modules', 'npm', 'bin', 'npm-cli.js');
  // Prevent Node loader overrides, another installation's profile, or an
  // unrelated renderer from replacing the packaged workspace on launch.
  for (const name of Object.keys(env)) {
    const key = name.toUpperCase();
    if (key.startsWith('PAPILOT_') && !key.endsWith('_BIN') ||
      ['PATH', 'NODE_OPTIONS', 'NODE_PATH', 'NO_PROXY', 'ELECTRON_RUN_AS_NODE', 'NPM_EXECPATH'].includes(key)) delete env[name];
  }
  const directories = absolutePaths([path.dirname(node), path.join(root, 'runtime', 'bin'), latex, ...existing]);
  Object.assign(env, {
    PATH: directories.join(path.delimiter),
    NODE_ENV: 'production', NODE_USE_ENV_PROXY: '1',
    PAPILOT_DESKTOP: '1', PAPILOT_DESKTOP_INSTANCE: instance,
    PAPILOT_DATA: data, PAPILOT_PORT: String(port), PAPILOT_DESKTOP_PACKAGE_UPDATES: '1',
    NO_PROXY: [inherited.NO_PROXY || inherited.no_proxy, 'localhost', '127.0.0.1', '::1'].filter(Boolean).join(','),
  });
  if (env.UTOPIA_WORKBUDDY_BIN && !env.PAPILOT_WORKBUDDY_BIN) env.PAPILOT_WORKBUDDY_BIN = env.UTOPIA_WORKBUDDY_BIN;
  const share = sharingOptions(inherited);
  if (share) {
    env.PAPILOT_PUBLIC_ORIGIN = share.origin;
    env.PAPILOT_TAILSCALE_LOGIN = share.owner;
  }
  if (existsSync(npmCli)) env.npm_execpath = npmCli;
  if (useBundle) Object.assign(env, {
    TEXMFCNF: [texRoot, path.join(texRoot, 'texmf-dist', 'web2c')].join(path.delimiter),
    TEXMFROOT: texRoot,
    TEXMFHOME: path.join(data, 'texmf-home'),
    TEXMFLOCAL: path.join(texRoot, 'texmf-local'),
    TEXMFSYSVAR: path.join(texRoot, 'texmf-var'),
    TEXMFSYSCONFIG: path.join(texRoot, 'texmf-config'),
    TEXMFVAR: path.join(data, 'texmf-var'),
    TEXMFCONFIG: path.join(data, 'texmf-config'),
    FONTCONFIG_FILE: path.join(texRoot, 'fonts.conf'),
  });
  return env;
}

function childAlive(child) {
  return child?.pid && child.exitCode === null && child.signalCode === null;
}

function childIdle(child) {
  return new Promise(resolve => {
    if (!child?.connected || !childAlive(child)) { resolve(false); return; }
    const id = randomUUID();
    const finish = idle => {
      clearTimeout(timer);
      child.off('message', receive);
      child.off('disconnect', disconnected);
      resolve(idle);
    };
    const disconnected = () => finish(false);
    const receive = message => {
      if (message?.type === 'papilot:can-switch' && message.id === id) finish(message.busy === false);
    };
    const timer = setTimeout(disconnected, 5000);
    child.on('message', receive);
    child.once('disconnect', disconnected);
    try { child.send({ type: 'papilot:can-switch', id }, error => { if (error) finish(false); }); }
    catch { finish(false); }
  });
}

function terminateTree(child) {
  if (!childAlive(child)) return;
  if (process.platform === 'win32') {
    const taskkill = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe');
    execFile(taskkill, ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, timeout: 3000 }, error => {
      if (error && childAlive(child)) child.kill();
    });
  } else {
    try { process.kill(-child.pid, 'SIGKILL'); } catch { if (childAlive(child)) child.kill('SIGKILL'); }
  }
}

function stopChild(child) {
  if (!childAlive(child)) return Promise.resolve();
  return new Promise((resolve, reject) => {
    let deadline;
    const complete = error => {
      clearTimeout(grace);
      clearTimeout(deadline);
      child.off('close', closed);
      if (error) reject(error);
      else resolve();
    };
    const closed = () => complete();
    const grace = setTimeout(() => {
      terminateTree(child);
      deadline = setTimeout(() => complete(new Error('无法确认项目工作台已关闭。')), SHUTDOWN_TIMEOUT);
    }, SHUTDOWN_TIMEOUT);
    child.once('close', closed);
    if (!child.connected) { terminateTree(child); return; }
    try {
      child.send({ type: 'papilot:shutdown' }, error => { if (error) terminateTree(child); });
    } catch { terminateTree(child); }
  });
}

function waitUntilReady(child, signal, log) {
  return new Promise((resolve, reject) => {
    const finish = (error, origin) => {
      clearTimeout(timer);
      child.off('message', receive);
      child.off('exit', exited);
      child.off('error', failed);
      signal.removeEventListener('abort', abort);
      if (error) reject(error);
      else resolve(origin);
    };
    const timer = setTimeout(() => finish(new Error(`项目工作台启动超时。日志 ${log}`)), READY_TIMEOUT);
    const exited = code => finish(new Error(`项目工作台已退出（${code ?? '进程终止'}）。日志 ${log}`));
    const failed = error => finish(error);
    const abort = () => finish(signal.reason);
    const receive = message => {
      if (message?.type !== 'papilot:ready' || typeof message.url !== 'string') return;
      let url;
      try { url = new URL(message.url); } catch { return; }
      if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port || url.username || url.password) return;
      finish(undefined, url.origin);
    };
    child.on('message', receive);
    child.once('exit', exited);
    child.once('error', failed);
    signal.addEventListener('abort', abort, { once: true });
  });
}

function localHealth(origin, signal) {
  return new Promise(resolve => {
    // Start a fresh loopback connection. A previous backend may have used the
    // same port, so a pooled socket can be stale even after the new bind.
    const request = http.get(`${origin}/api/health`, {
      agent: false, signal: AbortSignal.any([signal, AbortSignal.timeout(1500)]),
    }, async response => {
      try {
        if (response.statusCode !== 200) { response.resume(); resolve(null); return; }
        response.setEncoding('utf8');
        let body = '';
        for await (const chunk of response) {
          body += chunk;
          if (body.length > 32_768) { response.destroy(); resolve(null); return; }
        }
        resolve(JSON.parse(body));
      } catch { resolve(null); }
    });
    request.once('error', () => resolve(null));
  });
}

async function verifyIdentity(origin, instance, signal) {
  for (let attempt = 0; attempt < 4; attempt += 1) {
    signal.throwIfAborted();
    const health = await localHealth(origin, signal);
    if (health?.name === 'Papilot' && health.desktopInstance === instance) return;
    if (attempt < 3) await delay(200, undefined, { signal });
  }
  signal.throwIfAborted();
  throw new Error('本地项目工作台未通过身份校验。');
}

async function readPort(file) {
  try {
    const port = JSON.parse(await fs.readFile(file, 'utf8'));
    return Number.isInteger(port) && port > 1023 && port < 65536 ? port : 0;
  } catch { return 0; }
}

async function saveJson(file, value) {
  const temporary = `${file}.${randomUUID()}.tmp`;
  try {
    await fs.writeFile(temporary, `${JSON.stringify(value)}\n`, { flag: 'wx', mode: 0o600 });
    await fs.rename(temporary, file);
  } finally { await fs.rm(temporary, { force: true }); }
}

function validatedEnvironment(value) {
  if (value?.kind === 'native') return { kind: 'native' };
  if (process.platform === 'win32' && value?.kind === 'wsl' && typeof value.distribution === 'string' &&
    value.distribution.trim() && value.distribution.length <= 128 && !/[\r\n\0]/.test(value.distribution)) {
    return { kind: 'wsl', distribution: value.distribution.trim() };
  }
  return undefined;
}

function createPapilotRuntime({ resources, profile, resolveEnvironment = async () => process.env, node, onStopped }) {
  const root = path.join(resources, 'papilot');
  const data = path.join(profile, 'papilot');
  const desktop = path.join(root, 'desktop');
  const wsl = path.join(root, 'wsl');
  const logs = path.join(data, 'logs');
  const log = path.join(logs, 'backend.log');
  const environmentFile = path.join(data, 'environment.json');
  node ||= path.join(resources, 'runtimes', 'node', process.platform === 'win32' ? 'node.exe' : 'node');
  let running;
  let switching;

  async function getEnvironment() {
    if (running?.environment) return running.environment;
    try { return validatedEnvironment(JSON.parse(await fs.readFile(environmentFile, 'utf8'))) || { kind: 'native' }; }
    catch { return { kind: 'native' }; }
  }

  const wslBridge = () => import(pathToFileURL(path.join(desktop, 'wsl.js')).href);

  function watchExit(record) {
    record.child.on('error', () => {});
    record.child.once('close', code => {
      if (running !== record) return;
      running = undefined;
      if (!record.stopping && record.info) onStopped?.(code);
    });
  }

  async function launchWsl(record, inherited, port, bundle) {
    const bridge = await wslBridge();
    const signal = record.controller.signal;
    signal.throwIfAborted();
    record.wslStarting = bridge.startWsl({
      distribution: record.environment.distribution, assets: wsl, port, log,
      fingerprint: '', nodeMajor: bundle.node?.split('.')[0] || '', sharing: sharingOptions(inherited),
      profile: createHash('sha256').update(path.resolve(profile).toLowerCase()).digest('hex').slice(0, 24),
    }).then(backend => {
      record.wslBackend = backend;
      record.child = backend.child;
      watchExit(record);
      backend.ready.catch(() => {});
      return backend;
    });
    const backend = await record.wslStarting;
    signal.throwIfAborted();
    return backend.ready;
  }

  async function launch(record) {
    const signal = record.controller.signal;
    const loader = path.join(root, 'node_modules', 'tsx', 'dist', 'loader.mjs');
    const entry = path.join(root, 'src', 'server', 'start.ts');
    const [bundle, dependency, server, executable] = await Promise.all([
      fs.readFile(path.join(root, 'papilot-bundle.json'), 'utf8').then(JSON.parse).catch(() => null),
      fs.stat(loader).catch(() => null), fs.stat(entry).catch(() => null), fs.stat(node).catch(() => null),
    ]);
    if (!bundle?.version || !dependency?.isFile() || !server?.isFile() || !executable?.isFile()) {
      throw new Error('缺少项目工作台运行组件。请使用包含项目工作台的题库安装包。');
    }
    const selected = await getEnvironment();
    record.environment = selected;
    const portFile = path.join(data, selected.kind === 'native' ? 'desktop-port.json' :
      `desktop-port-wsl-${Buffer.from(selected.distribution).toString('hex')}.json`);
    const [, inherited, port] = await Promise.all([fs.mkdir(logs, { recursive: true, mode: 0o700 }), resolveEnvironment(), readPort(portFile)]);
    const oldLog = await fs.stat(log).catch(error => { if (error.code !== 'ENOENT') throw error; });
    if (oldLog?.size > LOG_LIMIT) await fs.rename(log, `${log}.old`);
    let origin;
    if (selected.kind === 'wsl') origin = await launchWsl(record, inherited, port, bundle);
    else {
      // A new embedded profile opens the selected question. Papilot treats an
      // empty seed marker as the user's choice to omit its standalone demo.
      if (!existsSync(path.join(data, 'papilot.sqlite')) && !existsSync(path.join(data, 'example-paper'))) {
        await fs.writeFile(path.join(data, 'desktop-demo.json'), '{}\n', { flag: 'wx', mode: 0o600 })
          .catch(error => { if (error.code !== 'EEXIST') throw error; });
      }
      const instance = randomUUID();
      const env = buildEnvironment({ inherited, node, root, data, resources, port, instance });
      const output = await fs.open(log, 'a', 0o600);
      let ready;
      try {
        signal.throwIfAborted();
        record.child = spawn(node, ['--import', pathToFileURL(loader).href, entry], {
          cwd: root, env, windowsHide: true, detached: process.platform !== 'win32', stdio: ['ignore', output.fd, output.fd, 'ipc'],
        });
        ready = waitUntilReady(record.child, signal, log);
        // Attach immediately while the native log handle is closing.
        ready.catch(() => {});
        watchExit(record);
      } finally { await output.close(); }
      origin = await ready;
      await verifyIdentity(origin, instance, signal);
    }
    signal.throwIfAborted();
    if (!childAlive(record.child)) throw new Error('项目工作台在启动后退出。');
    await saveJson(portFile, Number(new URL(origin).port)).catch(error => console.error('无法保存 Papilot 本地端口。', error.message));
    signal.throwIfAborted();
    record.info = { origin, data, version: bundle.version, commit: bundle.commit, root, desktop, wsl, environment: selected };
    return record.info;
  }

  function shutdown(record) {
    if (record.stopping) return record.stopping;
    record.controller.abort(new Error('项目工作台正在关闭。'));
    record.stopping = (async () => {
      await record.wslStarting?.catch(() => {});
      await record.wslBackend?.stop();
      await stopChild(record.child);
      if (running === record) running = undefined;
    })();
    return record.stopping;
  }

  function start() {
    if (switching) return switching.then(start);
    if (running?.stopping) return running.stopping.then(start);
    if (running) return running.ready;
    const record = { controller: new AbortController() };
    running = record;
    record.ready = launch(record).catch(async error => {
      await shutdown(record);
      throw error;
    });
    return record.ready;
  }

  function setEnvironment(value) {
    const selected = validatedEnvironment(value);
    if (!selected) return Promise.reject(new Error('请选择有效的项目工作台运行环境。'));
    if (switching) return Promise.reject(new Error('正在切换项目工作台运行环境。'));
    switching = (async () => {
      const current = await getEnvironment();
      if (selected.kind === current.kind && selected.distribution === current.distribution) return;
      if (selected.kind === 'wsl' && !(await (await wslBridge()).distributions()).includes(selected.distribution)) {
        throw new Error('请选择已安装的 WSL 发行版。');
      }
      const previous = running;
      if (previous && !previous.stopping && (!previous.info ||
        !await (previous.wslBackend ? previous.wslBackend.idle().catch(() => false) : childIdle(previous.child)))) {
        throw new Error('项目工作台正在处理任务，请等待任务完成再切换运行环境。');
      }
      if (previous) await shutdown(previous);
      await fs.mkdir(data, { recursive: true, mode: 0o700 });
      await saveJson(environmentFile, selected);
    })().finally(() => { switching = undefined; });
    return switching;
  }

  async function models() {
    // Native one-shot requests use Windows credentials and tools. WSL may
    // list models from a different account or entirely different CLIs.
    if ((await getEnvironment()).kind !== 'native') return {};
    const started = await start();
    if (started.environment.kind !== 'native') return {};
    const response = await fetch(new URL('/api/state', started.origin), {
      redirect: 'error', signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error('无法读取本机模型列表。');
    return modelCatalog(await response.json());
  }

  return {
    start, getEnvironment, setEnvironment, models,
    stop: () => running ? shutdown(running) : Promise.resolve(),
    current: () => running && !running.stopping ? running.info || null : null,
    async projectPath(windowsPath) {
      const selected = await getEnvironment();
      return selected.kind === 'native' ? windowsPath : (await wslBridge()).linuxPath(selected.distribution, windowsPath);
    },
  };
}

module.exports = { createPapilotRuntime };
