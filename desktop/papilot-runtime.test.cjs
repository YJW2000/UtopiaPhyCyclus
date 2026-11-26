const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const { createPapilotRuntime } = require('./papilot-runtime.cjs');

const server = `
const http = require('node:http');
let requests = 0;
const server = http.createServer((request, response) => {
  if (process.env.UTOPIA_DELAYED_HEALTH && requests++ === 0) {
    response.writeHead(503).end(); return;
  }
  response.setHeader('content-type', 'application/json');
  if (request.url === '/api/state') {
    const model = (id, name) => ({ id, name, isDefault: true });
    const ready = { installed: true, connected: true, authenticated: true, account: 'test account' };
    response.end(JSON.stringify({ csrf: 'test-token', runtimes: {
      codex: { ...ready, hiddenModels: ['hidden-model'], models: [model('known-model', 'Known model'), model('hidden-model'), model('--flag')] },
      antigravity: { ...ready, models: [model('gemini/example:variant')] },
      kimi: { ...ready, hidden: true, models: [model('kimi-k3')] },
      papilot: { ...ready, models: [model('writer-model')] },
      pi: { ...ready, connected: false, models: [model('provider/model')] },
    } }));
    return;
  }
  response.end(JSON.stringify({ name: 'Papilot', data: process.env.PAPILOT_DATA,
    desktopInstance: process.env.UTOPIA_WRONG_INSTANCE || process.env.PAPILOT_DESKTOP_INSTANCE }));
});
server.on('error', error => {
  if (error.code === 'EADDRINUSE') server.listen(0, '127.0.0.1');
  else throw error;
});
server.listen(Number(process.env.PAPILOT_PORT), '127.0.0.1', () => {
  process.send({ type: 'papilot:ready', url: 'http://127.0.0.1:' + server.address().port });
});
process.on('message', message => {
  if (message.type === 'papilot:shutdown') server.close(() => process.exit(0));
});
`;

async function fixture(t, variables) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'utopia-papilot-runtime-'));
  const root = path.join(directory, 'papilot');
  await Promise.all(['node_modules/tsx/dist', 'src/server'].map(child => fs.mkdir(path.join(root, child), { recursive: true })));
  await Promise.all([
    fs.writeFile(path.join(root, 'papilot-bundle.json'), JSON.stringify({ version: '0.14.1' })),
    fs.writeFile(path.join(root, 'node_modules/tsx/dist/loader.mjs'), 'export {};'),
    fs.writeFile(path.join(root, 'src/server/start.ts'), server),
  ]);
  const profile = path.join(directory, 'profile');
  const runtime = createPapilotRuntime({ resources: directory, profile, node: process.execPath,
    resolveEnvironment: async () => ({ ...process.env, PAPILOT_DATA: path.join(directory, 'wrong'), ...variables }) });
  t.after(async () => { await runtime.stop(); await fs.rm(directory, { recursive: true, force: true }); });
  return { runtime, profile };
}

test('one native writer per desktop session, private profile and stable port', async t => {
  const { runtime, profile } = await fixture(t, { UTOPIA_DELAYED_HEALTH: '1' });
  assert.deepEqual(await runtime.getEnvironment(), { kind: 'native' });
  const [first, concurrent] = await Promise.all([runtime.start(), runtime.start()]);
  assert.equal(first, concurrent);
  assert.equal(first.data, path.join(profile, 'papilot'));
  const demo = path.join(first.data, 'desktop-demo.json');
  assert.deepEqual(JSON.parse(await fs.readFile(demo, 'utf8')), {});
  const response = await fetch(`${first.origin}/api/health`).then(result => result.json());
  assert.equal(response.data, first.data);
  await runtime.stop();
  assert.equal(runtime.current(), null);
  const existingDemo = JSON.stringify({ id: 'existing-demo' });
  await fs.writeFile(demo, existingDemo);
  const restarted = await runtime.start();
  assert.equal(await fs.readFile(demo, 'utf8'), existingDemo);
  assert.equal(restarted.origin, first.origin);
  assert.notEqual((await fetch(`${restarted.origin}/api/health`).then(result => result.json())).desktopInstance, response.desktopInstance);
});

test('backend identity failure is rejected and does not leave a current workspace', async t => {
  const { runtime } = await fixture(t, { UTOPIA_WRONG_INSTANCE: 'other-process' });
  await assert.rejects(runtime.start(), /身份校验/);
  assert.equal(runtime.current(), null);
});

test('the native catalog exposes selectable model names and IDs only', async t => {
  const { runtime } = await fixture(t);
  assert.deepEqual(await runtime.models(), {
    codex: [{ id: 'known-model', name: 'Known model' }],
    agy: [{ id: 'gemini/example:variant', name: 'gemini/example:variant' }],
  });
});
