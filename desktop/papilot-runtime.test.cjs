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
  const response = await fetch(`${first.origin}/api/health`).then(result => result.json());
  assert.equal(response.data, first.data);
  await runtime.stop();
  assert.equal(runtime.current(), null);
  const restarted = await runtime.start();
  assert.equal(restarted.origin, first.origin);
  assert.notEqual((await fetch(`${restarted.origin}/api/health`).then(result => result.json())).desktopInstance, response.desktopInstance);
});

test('backend identity failure is rejected and does not leave a current workspace', async t => {
  const { runtime } = await fixture(t, { UTOPIA_WRONG_INSTANCE: 'other-process' });
  await assert.rejects(runtime.start(), /身份校验/);
  assert.equal(runtime.current(), null);
});
