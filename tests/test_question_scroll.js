// Run with: node tests/test_question_scroll.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const rootPath = path.resolve(__dirname, '..');
const focusScript = fs.readFileSync(path.join(rootPath, 'static/question_focus.js'), 'utf8').replace('__MC_QUESTION_HASH__', '"test"');
const bookmarkScript = fs.readFileSync(path.join(rootPath, 'static/browse_scroll.js'), 'utf8').replace('__MC_SCROLL_CONFIG__', '{"key":"fixture","context":"test"}');
const metadataScript = fs.readFileSync(path.join(rootPath, 'static/metadata_scroll.js'), 'utf8').replace('__MC_QUESTION_HASH__', '"test"');

function fixture(height) {
  let serial = 0;
  const frames = new Map(), timers = new Map(), listeners = new Map();
  const source = {scrollTop: 0};
  const panel = {isConnected: true, height, addEventListener() {}, removeEventListener() {},
    getBoundingClientRect: () => ({top: 1200 - main.scrollTop, bottom: 1200 - main.scrollTop + panel.height, height: panel.height}),
    querySelector: () => source};
  const anchor = {dataset: {mcBrowseQuestion: 'test'}, closest: () => panel};
  const buttons = [{textContent: '🏷️ 修改文件信息', offset: 200}, {textContent: '✅ 保存并继续修改', offset: 900}];
  for(const button of buttons){
    button.closest = () => button;
    button.getBoundingClientRect = () => ({top: panel.getBoundingClientRect().top + button.offset});
  }
  panel.querySelectorAll = () => buttons;
  panel.contains = button => buttons.includes(button);
  const main = {isConnected: true, scrollTop: 0, scrollLeft: 77, scrollHeight: 6000, clientHeight: 1000,
    getBoundingClientRect: () => ({top: 0, height: 1000}), querySelectorAll: () => [anchor],
    querySelector: () => ({}), contains: () => true};
  const doc = {getElementById: () => anchor, querySelector: () => main, querySelectorAll: () => [anchor],
    addEventListener: (type, fn) => {if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn);},
    removeEventListener: (type, fn) => listeners.get(type)?.delete(fn), body: {}, documentElement: {dataset:{}}};
  main.parentElement = doc.body;
  panel.parentElement = main;
  const host = {document: doc, innerHeight: 1000,
    requestAnimationFrame: fn => {frames.set(++serial, fn); return serial;}, cancelAnimationFrame: id => frames.delete(id),
    setTimeout: fn => {timers.set(++serial, fn); return serial;}, clearTimeout: id => timers.delete(id),
    addEventListener() {}, removeEventListener() {}, getComputedStyle: () => ({overflowY: 'auto'}),
    localStorage: {getItem: () => '{"test":{"question":"test","offset":12,"source":0}}', setItem() {}}};
  class Observer {observe() {} disconnect() {}}
  host.ResizeObserver = Observer;
  const context = vm.createContext({window: {parent: host}, MutationObserver: Observer, ResizeObserver: Observer,
    getComputedStyle: host.getComputedStyle});
  const flush = () => {for (const [id, fn] of [...frames]) {frames.delete(id); fn();}};
  return {host, main, panel, context, flush, timers, listeners, buttons};
}

for (const height of [400, 2200]) {
  const f = fixture(height);
  vm.runInContext(focusScript, f.context); f.flush();
  assert.equal(f.panel.getBoundingClientRect().top, height < 1000 ? 300 : 96);
  assert.equal(f.main.scrollLeft, 77, 'Focusing must not move the page horizontally');
  const before = f.main.scrollTop;
  vm.runInContext(bookmarkScript, f.context); f.flush();
  assert.equal(f.main.scrollTop, before, 'An older bookmark must not override the edited question');
  for (const fn of f.listeners.get('wheel')) fn({type: 'wheel'});
  assert.equal(f.host.__mcQuestionFocus.alive(), false, 'User scrolling must cancel delayed focusing');
}

const f = fixture(400);
vm.runInContext(bookmarkScript, f.context);
vm.runInContext(focusScript, f.context); f.flush();
assert.equal(f.panel.getBoundingClientRect().top, 300, 'Explicit question focus must also win when the bookmark initializes first');
f.panel.height = 2200;
for (const fn of [...f.timers.values()]) fn();
assert.equal(f.panel.getBoundingClientRect().top, 96, 'Late form expansion must keep the question in view');
assert.equal(f.main.scrollLeft, 77);
console.log('Question focus, bookmark priority, resizing and user-scroll checks passed.');

for(const buttonIndex of [0, 1]){
  const m = fixture(1400);
  m.main.scrollTop = 900;
  vm.runInContext(metadataScript, m.context);
  // Simulate an iframe replacement removing its listener while parent state
  // still contains the old capture function.
  for(const type of ['pointerdown', 'click', 'keydown'])m.listeners.get(type).delete(m.host.__mcMetadataScroll.capture);
  vm.runInContext(metadataScript, m.context);
  const button = m.buttons[buttonIndex], before = button.getBoundingClientRect().top;
  for(const fn of [...m.listeners.get('pointerdown')])fn({type: 'pointerdown', target: button});
  if(buttonIndex === 0)button.textContent = '✅ 保存文件信息';
  button.offset += 168;
  vm.runInContext(metadataScript, m.context); m.flush();
  assert.equal(button.getBoundingClientRect().top, before, 'Expansion and saving must keep the clicked metadata button at the same viewport position');
  assert.equal(m.main.scrollLeft, 77);
  vm.runInContext(bookmarkScript, m.context); m.flush();
  assert.equal(button.getBoundingClientRect().top, before, 'A page bookmark must not override metadata position restoration');
}
console.log('Metadata expansion, top/bottom saves and bookmark priority checks passed.');
