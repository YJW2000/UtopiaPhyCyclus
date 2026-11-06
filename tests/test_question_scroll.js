// Run with: node tests/test_question_scroll.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const rootPath = path.resolve(__dirname, '..');
const focusScript = fs.readFileSync(path.join(rootPath, 'static/question_focus.js'), 'utf8').replace('__MC_QUESTION_HASH__', '"test"');
const bookmarkScript = fs.readFileSync(path.join(rootPath, 'static/browse_scroll.js'), 'utf8').replace('__MC_SCROLL_CONFIG__', '{"key":"fixture","context":"test"}');

function fixture(height) {
  let serial = 0;
  const frames = new Map(), timers = new Map(), listeners = new Map();
  const source = {scrollTop: 0};
  const panel = {isConnected: true, height, addEventListener() {}, removeEventListener() {},
    getBoundingClientRect: () => ({top: 1200 - main.scrollTop, bottom: 1200 - main.scrollTop + panel.height, height: panel.height}),
    querySelector: () => source};
  const anchor = {dataset: {mcBrowseQuestion: 'test'}, closest: () => panel};
  const main = {isConnected: true, scrollTop: 0, scrollLeft: 77, scrollHeight: 6000, clientHeight: 1000,
    getBoundingClientRect: () => ({top: 0, height: 1000}), querySelectorAll: () => [anchor],
    querySelector: () => ({}), contains: () => true};
  const doc = {getElementById: () => ({closest: () => panel}), querySelector: () => main,
    addEventListener: (type, fn) => {if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn);},
    removeEventListener: (type, fn) => listeners.get(type)?.delete(fn), body: {}, documentElement: {}};
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
  return {host, main, panel, context, flush, timers, listeners};
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
