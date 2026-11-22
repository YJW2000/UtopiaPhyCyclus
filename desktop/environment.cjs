const { spawn } = require('node:child_process');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { bootstrapWinget } = require('./winget-bootstrap.cjs');

const TOOLS = new Map([
  ['git', { label: 'Git', command: 'git', winget: 'Git.Git', helpUrl: 'https://git-scm.com/install/windows' }],
  ['latex', { label: 'LaTeX（XeLaTeX）', command: 'xelatex', winget: 'MiKTeX.MiKTeX', helpUrl: 'https://miktex.org/howto/install-miktex' }],
  ['codex', { label: 'Codex CLI', command: 'codex', npm: '@openai/codex', node: 16, helpUrl: 'https://developers.openai.com/codex/cli/' }],
  ['claude', { label: 'Claude Code', command: 'claude', winget: 'Anthropic.ClaudeCode', helpUrl: 'https://code.claude.com/docs/en/setup' }],
  ['copilot', { label: 'GitHub Copilot CLI', command: 'copilot', winget: 'GitHub.Copilot', helpUrl: 'https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/install-copilot-cli' }],
  ['opencode', { label: 'OpenCode CLI', command: 'opencode', npm: '@opencode/cli', node: 20, helpUrl: 'https://opencode.ai/docs/' }],
  ['gemini', { label: 'Gemini CLI', command: 'gemini', npm: '@google/gemini-cli', node: 20, helpUrl: 'https://github.com/google-gemini/gemini-cli/blob/main/docs/get-started/installation.mdx' }],
  ['cursor', { label: 'Cursor CLI', command: 'cursor-agent', helpUrl: 'https://cursor.com/docs/cli/installation' }],
]);

const POWERSHELL_UTF8 = '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false);';
const WINDOWS_LOCATIONS = `${POWERSHELL_UTF8}
  $codex = Get-AppxPackage -Name OpenAI.Codex -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty InstallLocation;
  @{ machine = [Environment]::GetEnvironmentVariable('Path', 'Machine');
     user = [Environment]::GetEnvironmentVariable('Path', 'User'); codex = $codex } | ConvertTo-Json -Compress`;
const CURSOR_INSTALL = `${POWERSHELL_UTF8}
  $ErrorActionPreference = 'Stop';
  if (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA 'cursor-agent')) { throw 'Cursor CLI already has an installation directory.' }
  $source = Invoke-RestMethod -Uri 'https://cursor.com/install?win32=true' -TimeoutSec 90;
  & ([ScriptBlock]::Create($source))`;
const INSTALL_TIMEOUT = 15 * 60 * 1000;

function execute(executable, args, { env, cwd, timeout, onOutput } = {}) {
  return new Promise(resolve => {
    const child = spawn(executable, args, { env, cwd, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    let output = '';
    function record(data) {
      output = (output + data).slice(-16_384);
      onOutput?.(output);
    }
    child.stdout.setEncoding('utf8').on('data', record);
    child.stderr.setEncoding('utf8').on('data', record);
    child.once('error', error => record(error.message));
    child.once('close', code => {
      clearTimeout(timer);
      resolve({ code, output });
    });
    const timer = setTimeout(() => {
      if (child.pid && process.platform === 'win32') {
        const taskkill = path.join(env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe');
        const killer = spawn(taskkill, ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' });
        killer.once('error', () => child.kill());
        killer.once('close', code => { if (code) child.kill(); });
      } else {
        child.kill();
      }
      resolve({ code: null, output, timedOut: true });
    }, timeout);
  });
}

function usefulLines(output) {
  return output.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/[\x00-\x09\x0b\x0c\x0e-\x1f\x7f]/g, '')
    .replace(/(https?:\/\/)[^\s/@]+@/g, '$1')
    .split(/[\r\n]+/).map(line => line.trim()).filter(line => /[\p{L}\p{N}]/u.test(line));
}

function uniquePaths(entries, windows) {
  const seen = new Set();
  return entries.filter(entry => {
    if (!entry || !path.isAbsolute(entry)) return false;
    const normalized = path.normalize(entry);
    const key = windows ? normalized.toLowerCase() : normalized;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

async function isFile(filename) {
  try {
    return (await fs.stat(filename)).isFile();
  } catch (error) {
    // Windows packaged app execution aliases may only allow inspecting the link.
    return ['EACCES', 'EPERM'].includes(error.code)
      && await fs.lstat(filename).then(entry => entry.isSymbolicLink(), () => false);
  }
}

async function texLiveBins(root) {
  try {
    const entries = await fs.readdir(root, { withFileTypes: true });
    return entries.filter(entry => entry.isDirectory() && /^\d{4}$/.test(entry.name))
      .map(entry => entry.name).sort().reverse().map(year => path.join(root, year, 'bin', 'windows'));
  } catch {
    return [];
  }
}

function createEnvironment({ resources, env = process.env, platform = process.platform, run = execute }) {
  const windows = platform === 'win32';
  const home = env.USERPROFILE || env.HOME || os.homedir();
  const local = env.LOCALAPPDATA || path.join(home, 'AppData', 'Local');
  const roaming = env.APPDATA || path.join(home, 'AppData', 'Roaming');
  const prefix = path.join(roaming, 'npm');
  const cursorDirectory = path.join(local, 'cursor-agent');
  const systemRoot = env.SystemRoot || 'C:\\Windows';
  const powershell = path.join(systemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe');
  const shellArgs = ['-NoLogo', '-NoProfile', '-NonInteractive', '-Command'];
  const bundledTex = path.join(resources, 'runtimes', 'texlive', 'bin', 'windows', 'xelatex.exe');
  const bundledNode = path.join(resources, 'runtimes', 'node', 'node.exe');
  const failures = new Map();
  let active;

  async function context() {
    let registry = {};
    if (windows) {
      const response = await run(powershell, [...shellArgs, WINDOWS_LOCATIONS], { env, cwd: home, timeout: 8_000 });
      if (response.code === 0) {
        try { registry = JSON.parse(response.output.trim()); } catch { /* Use inherited PATH. */ }
      }
    }
    const pathValues = [env.PATH, registry.machine, registry.user].filter(Boolean)
      .flatMap(value => value.split(path.delimiter)).map(value => value.trim().replace(/^"|"$/g, ''));
    const defaults = [path.join(home, '.local', 'bin'), path.join(home, '.codex', 'bin')];
    if (windows) {
      const programFiles = uniquePaths([env.PROGRAMW6432, env.ProgramFiles || env.PROGRAMFILES, env['ProgramFiles(x86)'],
        path.join(local, 'Programs')], true);
      defaults.push(prefix, cursorDirectory, path.join(local, 'Microsoft', 'WindowsApps'),
        path.join(local, 'Microsoft', 'WinGet', 'Links'),
        ...programFiles.flatMap(base => [path.join(base, 'Git', 'cmd'), path.join(base, 'Git', 'bin'),
          path.join(base, 'MiKTeX', 'miktex', 'bin', 'x64'), path.join(base, 'MiKTeX', 'miktex', 'bin'), path.join(base, 'nodejs')]),
        ...await texLiveBins(path.join(`${env.SystemDrive || 'C:'}\\`, 'texlive')));
    }
    // Agent npm shims can require a newer Node than the inherited system copy.
    const runtime = windows && await isFile(bundledNode) ? [path.dirname(bundledNode)] : [];
    const directories = uniquePaths([...runtime, ...pathValues, ...defaults], windows);
    const extended = { ...env, PATH: directories.join(path.delimiter) };
    const winEnvironment = { directories, env: extended, codex: registry.codex };
    const winget = windows && await probe('winget', winEnvironment, [], ['.exe']);
    return { ...winEnvironment, winget: winget?.path || null, hasPowerShell: windows && await isFile(powershell) };
  }

  function locations(name, { directories }, extension = windows ? ['.exe', '.cmd', '.bat'] : ['']) {
    return directories.flatMap(directory => extension.map(suffix => path.join(directory, `${name}${suffix}`)));
  }

  async function probe(name, host, fallback = [], extension) {
    const preferred = name === 'xelatex' ? [bundledTex] : [];
    for (const filename of uniquePaths([...preferred, ...locations(name, host, extension), ...fallback], windows)) {
      if (!await isFile(filename)) continue;
      const shim = windows && /\.(cmd|bat)$/i.test(filename);
      const executable = shim ? powershell : filename;
      const args = shim ? [...shellArgs, `${POWERSHELL_UTF8} & $env:UTOPIA_TOOL_VERSION_PATH --version; exit $LASTEXITCODE`] : ['--version'];
      const result = await run(executable, args, {
        env: shim ? { ...host.env, UTOPIA_TOOL_VERSION_PATH: filename } : host.env, cwd: home, timeout: 6_000,
      });
      if (result.code === 0) return { path: filename, version: usefulLines(result.output)[0]?.slice(0, 200) ?? null };
    }
    return null;
  }

  async function findNpm(host, minimum = 16) {
    for (const node of locations('node', host, windows ? ['.exe'] : [''])) {
      const npm = path.join(path.dirname(node), 'node_modules', 'npm', 'bin', 'npm-cli.js');
      if (!await isFile(npm) || !await isFile(node)) continue;
      const result = await run(node, ['--version'], { env: host.env, cwd: home, timeout: 5_000 });
      if (result.code === 0 && Number(result.output.trim().match(/^v(\d+)\./)?.[1]) >= minimum) return { node, npm };
    }
    return null;
  }

  async function describe(id, host, npm) {
    const tool = TOOLS.get(id);
    const storeCodex = windows && host.codex && path.isAbsolute(host.codex)
      ? [path.join(host.codex, 'app', 'resources', 'codex.exe')] : [];
    const result = await probe(tool.command, host, id === 'codex' ? storeCodex : []);
    const bundled = result?.path === bundledTex;
    const texDirectories = result ? [path.dirname(result.path), ...host.directories] : host.directories;
    const syncTeX = id === 'latex' && result
      ? (await Promise.all(locations('synctex', { directories: texDirectories }).map(isFile))).some(Boolean) : null;
    const installing = active?.id === id;
    const installMethod = tool.npm ? 'npm' : id === 'cursor' ? 'Cursor 官方安装器' : 'WinGet';
    const canBootstrap = Boolean(host.winget || host.hasPowerShell);
    const npmReady = npm && npm.minimum >= tool.node;
    const cursorPartial = id === 'cursor' && !result && windows
      && await fs.lstat(cursorDirectory).then(() => true, error => error.code !== 'ENOENT');
    const installerAvailable = windows && (tool.npm ? npmReady || canBootstrap
      : id === 'cursor' ? host.hasPowerShell && !cursorPartial : canBootstrap);
    let detail = null;
    if (id === 'latex' && result) {
      detail = syncTeX ? '已检测到 SyncTeX，可用于 PDF 与源码定位。' : '已检测到 XeLaTeX，缺少用于 PDF 正反向定位的 SyncTeX。';
    }
    if (!result) {
      if (active && !installing) detail = '请等待当前安装完成。';
      else if (failures.has(id)) detail = failures.get(id);
      else if (!windows) detail = '自动安装适用于 Windows，请参阅官方说明。';
      else if (cursorPartial) detail = '发现已有 Cursor CLI 安装目录。请按官方说明处理现有安装后重试。';
      else if (!installerAvailable) detail = '无法启动 Windows 安装工具，请参阅官方说明。';
      else if (tool.npm && !npmReady) detail = '安装时会先准备 Node.js LTS 和 npm。';
      else if (id === 'cursor') detail = '安装时会检测 Git，缺少时会一并安装。';
      else if (!host.winget && tool.winget) detail = '必要时会先准备 Windows 程序包管理器。';
    }
    return { id, label: tool.label, installed: Boolean(result), path: result?.path ?? null, version: result?.version ?? null,
      source: result ? (bundled ? 'bundled' : 'system') : null, helpUrl: tool.helpUrl, installMethod, installing,
      canInstall: !result && !active && installerAvailable, detail, progress: installing ? active.progress : null,
      ...(id === 'latex' ? { syncTeX } : {}) };
  }

  async function detect() {
    const host = await context();
    const npm = windows && (await findNpm(host, 20) ? { minimum: 20 } : await findNpm(host, 16) ? { minimum: 16 } : null);
    return { tools: Object.fromEntries(await Promise.all([...TOOLS.keys()].map(async id => [id, await describe(id, host, npm)]))) };
  }

  function options(host) {
    const installation = active;
    return { env: host.env, cwd: home, timeout: INSTALL_TIMEOUT, onOutput: output => {
      const line = usefulLines(output).at(-1);
      if (line) installation.progress = line.slice(-350);
    } };
  }

  function ensureSuccess(result, label) {
    if (result.timedOut) throw new Error(`${label}等待超时，请检查网络和安装窗口后重试。`);
    if (result.code !== 0) {
      const detail = usefulLines(result.output).slice(-4).join(' ').slice(-700);
      throw new Error(`${label}失败${result.code === null ? '' : `（退出码 ${result.code}）`}。${detail}`);
    }
  }

  async function installWinget(host, packageId, label, extra = [], upgrade = false) {
    if (!host.winget && host.hasPowerShell) {
      active.progress = '正在准备 Windows 程序包管理器…';
      ensureSuccess(await bootstrapWinget(powershell, run, options(host)), '准备 Windows 程序包管理器');
      host = await context();
    }
    if (!host.winget) throw new Error('未找到可用的 Windows 程序包管理器，请使用官方安装说明。');
    active.progress = `正在通过 WinGet 安装 ${label}…`;
    const args = ['install', '--id', packageId, '--exact', '--source', 'winget', '--accept-package-agreements',
      '--accept-source-agreements', '--disable-interactivity', ...(upgrade ? [] : ['--no-upgrade']), ...extra];
    ensureSuccess(await run(host.winget, args, options(host)), `安装 ${label}`);
  }

  function getTool(id) {
    const tool = TOOLS.get(id);
    if (!tool) throw new Error('未识别的环境工具。');
    return tool;
  }

  async function install(id) {
    const tool = getTool(id);
    if (active) return { ok: false, tool: id, message: '另一个环境工具正在安装，请稍后重试。' };
    if (!windows) return { ok: false, tool: id, message: '请参阅官方安装说明。' };
    failures.delete(id);
    active = { id, progress: '正在检查现有安装…' };
    try {
      let host = await context();
      if ((await describe(id, host, null)).installed) return { ok: true, tool: id, message: `${tool.label} 已可用。` };
      if (tool.winget) {
        const extra = id === 'latex' ? ['--scope', 'user', '--custom', '--auto-install=yes'] : [];
        await installWinget(host, tool.winget, tool.label, extra);
      } else if (tool.npm) {
        let npm = await findNpm(host, tool.node);
        if (!npm) {
          await installWinget(host, 'OpenJS.NodeJS.LTS', 'Node.js LTS', [], true);
          host = await context();
          npm = await findNpm(host, tool.node);
        }
        if (!npm) throw new Error('安装完成后仍无法启动 npm，请检查 Node.js LTS 的安装。');
        active.progress = `正在通过 npm 安装 ${tool.label}…`;
        const npmHost = { ...host, env: { ...host.env, PATH: [path.dirname(npm.node), host.env.PATH].join(path.delimiter) } };
        ensureSuccess(await run(npm.node, [npm.npm, 'install', '--global', '--prefix', prefix,
          '--registry=https://registry.npmjs.org', '--no-audit', '--no-fund', '--ignore-scripts=false', tool.npm], options(npmHost)), `安装 ${tool.label}`);
      } else {
        if (!host.hasPowerShell) throw new Error('无法启动 Windows PowerShell，请使用官方安装说明。');
        const existing = await fs.lstat(cursorDirectory).catch(error => { if (error.code !== 'ENOENT') throw error; });
        if (existing) throw new Error('发现已有 Cursor CLI 安装目录。请按官方说明处理现有安装后重试。');
        if (!await probe('git', host)) {
          await installWinget(host, 'Git.Git', 'Git');
          host = await context();
        }
        active.progress = '正在运行 Cursor 官方安装器…';
        ensureSuccess(await run(powershell, [...shellArgs, CURSOR_INSTALL], options(host)), '安装 Cursor CLI');
      }
      active.progress = '正在验证安装结果…';
      const status = await describe(id, await context(), null);
      if (!status.installed) throw new Error(`安装程序已结束，仍无法启动 ${tool.label}。请检查官方安装说明后重新检测。`);
      return { ok: true, tool: id, message: `${tool.label} 安装成功。` };
    } catch (error) {
      const message = error.message || `${tool.label} 安装失败。`;
      failures.set(id, message);
      return { ok: false, tool: id, message };
    } finally {
      active = undefined;
    }
  }

  return { detect, install, helpUrl: id => getTool(id).helpUrl };
}

module.exports = { createEnvironment };
