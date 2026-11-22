const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');
const { createEnvironment } = require('./environment.cjs');

const windowsOnly = { skip: process.platform !== 'win32' };

async function fixture(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'utopia-env-test-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  const files = new Map();
  const launched = [];
  const env = {
    PATH: path.join(root, 'bin'), USERPROFILE: root, APPDATA: path.join(root, 'roaming'),
    LOCALAPPDATA: path.join(root, 'local'), SystemRoot: path.join(root, 'windows'), SystemDrive: 'Q:',
    ProgramFiles: path.join(root, 'programs'),
  };
  const resources = path.join(root, 'resources');
  const host = { paths: {}, run: undefined };
  const reply = (output = '', code = 0) => ({ code, output });
  async function file(filename, version) {
    await fs.mkdir(path.dirname(filename), { recursive: true });
    await fs.writeFile(filename, '');
    files.set(filename, version);
    return filename;
  }
  const powershell = await file(path.join(env.SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'));
  const winget = await file(path.join(env.LOCALAPPDATA, 'Microsoft', 'WindowsApps', 'winget.exe'), 'v1.12.0');
  async function run(executable, args, options) {
    launched.push({ executable, args });
    if (executable === powershell && args.at(-1).includes('Get-AppxPackage -Name OpenAI.Codex')) return reply(JSON.stringify(host.paths));
    const versionTarget = options.env.UTOPIA_TOOL_VERSION_PATH || executable;
    if (args.at(-1) === '--version' || options.env.UTOPIA_TOOL_VERSION_PATH) {
      const version = files.get(versionTarget);
      return version ? reply(version) : reply('broken', 1);
    }
    assert.ok(host.run, `Unexpected launch of ${executable}`);
    return host.run(executable, args, options);
  }
  return { env, resources, launched, host, winget, powershell, file, reply,
    environment: createEnvironment({ env, resources, platform: 'win32', run }) };
}

test('prefers bundled XeLaTeX, distinguishes SyncTeX, and ignores the generic agent alias', windowsOnly, async t => {
  const { env, resources, environment, file, launched } = await fixture(t);
  const tex = await file(path.join(resources, 'runtimes', 'texlive', 'bin', 'windows', 'xelatex.exe'), 'XeTeX (TeX Live 2026)');
  const agent = await file(path.join(env.PATH, 'agent.exe'), 'A different agent');
  const system = await file(path.join(env.PATH, 'xelatex.exe'), 'XeTeX (MiKTeX)');
  const { tools } = await environment.detect();
  assert.equal(tools.latex.path, tex);
  assert.equal(tools.latex.source, 'bundled');
  assert.equal(tools.latex.syncTeX, false);
  assert.equal(tools.cursor.installed, false);
  assert.ok(!launched.some(launch => [agent, system].includes(launch.executable)));
  assert.throws(() => environment.helpUrl('file:///elsewhere'));
  await assert.rejects(environment.install('Git.Git && something'));
});

test('reports Node prerequisites against the version required by each npm CLI', windowsOnly, async t => {
  const { environment, env, file } = await fixture(t);
  const node = path.join(env.PATH, 'node.exe');
  await file(path.join(env.PATH, 'node_modules', 'npm', 'bin', 'npm-cli.js'));
  await file(node, 'v16.20.0');
  const older = (await environment.detect()).tools;
  assert.doesNotMatch(older.codex.detail || '', /Node.js LTS/);
  assert.match(older.gemini.detail, /Node.js LTS/);
  await file(node, 'v24.0.0');
  const newer = (await environment.detect()).tools;
  assert.doesNotMatch(newer.codex.detail || '', /Node.js LTS/);
  assert.doesNotMatch(newer.gemini.detail || '', /Node.js LTS/);
});

test('serializes installation and checks the updated registry PATH before reporting success', windowsOnly, async t => {
  const { env, environment, file, host, winget, reply } = await fixture(t);
  const registered = path.join(env.LOCALAPPDATA, 'fresh Git installation');
  let allowInstallation;
  let started;
  const installing = new Promise(resolve => { started = resolve; });
  const proceed = new Promise(resolve => { allowInstallation = resolve; });
  host.run = async (executable, args, { onOutput }) => {
    assert.equal(executable, winget);
    assert.deepEqual(args.slice(0, 6), ['install', '--id', 'Git.Git', '--exact', '--source', 'winget']);
    onOutput('\x1b[32mInstalling Git for Windows\x1b[0m');
    started();
    await proceed;
    host.paths.user = registered;
    await file(path.join(registered, 'git.exe'), 'git version 2.56.1');
    return reply('Done');
  };
  const operation = environment.install('git');
  await installing;
  assert.equal((await environment.install('latex')).ok, false);
  const during = await environment.detect();
  assert.equal(during.tools.git.installing, true);
  assert.equal(during.tools.git.progress, 'Installing Git for Windows');
  assert.equal(during.tools.latex.canInstall, false);
  allowInstallation();
  assert.equal((await operation).ok, true);
  const { tools } = await environment.detect();
  assert.equal(tools.git.path, path.join(registered, 'git.exe'));
  assert.equal(tools.git.installing, false);
});

test('bootstraps Node LTS and installs the allowlisted npm package through Node', windowsOnly, async t => {
  const { env, environment, file, host, winget, reply } = await fixture(t);
  const nodeDirectory = path.join(env.ProgramFiles, 'nodejs');
  const nodePath = path.join(nodeDirectory, 'node.exe');
  const cliScript = path.join(nodeDirectory, 'node_modules', 'npm', 'bin', 'npm-cli.js');
  const prefix = path.join(env.APPDATA, 'npm');
  host.run = async (executable, args) => {
    if (executable === winget) {
      assert.deepEqual(args.slice(0, 3), ['install', '--id', 'OpenJS.NodeJS.LTS']);
      await file(nodePath, 'v24.20.0');
      await file(cliScript);
    } else {
      assert.equal(executable, nodePath);
      assert.deepEqual(args.slice(0, 6), [cliScript, 'install', '--global', '--prefix', prefix, '--registry=https://registry.npmjs.org']);
      assert.equal(args.at(-1), '@google/gemini-cli');
      await file(path.join(prefix, 'gemini.cmd'), '0.70.0');
    }
    return reply();
  };
  assert.equal((await environment.install('gemini')).ok, true);
  assert.equal((await environment.detect()).tools.gemini.version, '0.70.0');
});

test('keeps verification failures visible when an installer exits successfully without installing', windowsOnly, async t => {
  const { environment, host, reply } = await fixture(t);
  host.run = async () => reply();
  const result = await environment.install('claude');
  assert.equal(result.ok, false);
  assert.match(result.message, /仍无法启动 Claude Code/);
  assert.equal((await environment.detect()).tools.claude.detail, result.message);
});

test('preserves a broken Cursor installation directory from the clean-install script', windowsOnly, async t => {
  const { env, environment, file, launched } = await fixture(t);
  const marker = await file(path.join(env.LOCALAPPDATA, 'cursor-agent', 'existing-config'));
  const { tools } = await environment.detect();
  assert.equal(tools.cursor.canInstall, false);
  assert.match(tools.cursor.detail, /已有 Cursor CLI 安装目录/);
  assert.equal((await environment.install('cursor')).ok, false);
  assert.ok(!launched.some(launch => launch.args.at(-1).includes('https://cursor.com/install?win32=true')));
  assert.equal(await fs.readFile(marker, 'utf8'), '');
});
