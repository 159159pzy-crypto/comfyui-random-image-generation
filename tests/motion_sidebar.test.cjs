const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const runtime = fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const functionStart = app.indexOf('function setPoolSidebarOpen(');
assert.ok(functionStart >= 0);
const sidebarSource = app.slice(functionStart, app.indexOf('\n}', functionStart) + 2);
const listenerStart = app.indexOf('matchMedia("(max-width: 620px)").addEventListener("change"');
assert.ok(listenerStart >= 0);
const listenerSource = app.slice(listenerStart, app.indexOf('\n);', listenerStart) + 3);

function setup(mobile = true) {
  let time = 0, id = 0;
  const frames = new Map(), classes = new Set(), attributes = new Map(), viewportListeners = [];
  const viewport = { matches: mobile, addEventListener(name, callback) { if (name === 'change') viewportListeners.push(callback); } };
  const reduced = { matches: false, addEventListener() {} };
  const node = { dataset: {}, inert: false,
    classList: { contains: key => classes.has(key), toggle(key, value) { if (value) classes.add(key); else classes.delete(key); } },
    style: { transform: '', willChange: '', removeProperty(name) { this[name.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = ''; } },
  };
  const button = { title: '', setAttribute: (key, value) => attributes.set(key, value) };
  const ctx = vm.createContext({ ui: { poolSidebar: node, togglePoolSidebar: button },
    matchMedia: query => query.includes('620px') ? viewport : reduced,
    performance: { now: () => time },
    requestAnimationFrame(callback) { frames.set(++id, callback); return id; },
    cancelAnimationFrame(id) { frames.delete(id); },
    document: { hidden: false, addEventListener() {}, documentElement: { removeAttribute() {} } },
  });
  vm.runInContext(runtime, ctx);
  vm.runInContext(sidebarSource + '\n' + listenerSource, ctx);
  const step = (ms = 1000 / 60) => { time += ms; const callbacks = [...frames.values()]; frames.clear(); for (const callback of callbacks) callback(time); };
  const drain = () => { let remaining = 500; while (frames.size && remaining--) step(); assert.ok(remaining > 0, 'sidebar must settle'); };
  const resize = mobile => { viewport.matches = mobile; for (const callback of viewportListeners) callback({ matches: mobile }); };
  return { ctx, node, button, attributes, frames, step, drain, resize, motion: ctx.StudioMotion, open: (value, options) => ctx.setPoolSidebarOpen(value, options) };
}

test('mobile sidebar reversals preserve position and velocity and finally release inline styles', () => {
  const h = setup();
  h.open(false, { immediate: true });
  assert.equal(h.node.inert, true); assert.equal(h.frames.size, 0);
  h.open(true); h.step(60);
  const opening = h.motion.snapshot(h.node, 'sidebar');
  h.open(false);
  assert.equal(h.motion.snapshot(h.node, 'sidebar').value.x, opening.value.x);
  assert.equal(h.motion.snapshot(h.node, 'sidebar').velocity.x, opening.velocity.x);
  assert.equal(h.node.inert, true);
  h.step(40);
  const closing = h.motion.snapshot(h.node, 'sidebar'); h.open(true);
  assert.equal(h.motion.snapshot(h.node, 'sidebar').value.x, closing.value.x);
  assert.equal(h.motion.snapshot(h.node, 'sidebar').velocity.x, closing.velocity.x);
  h.drain();
  assert.equal(h.node.inert, false); assert.equal(h.node.classList.contains('mobile-open'), true);
  assert.equal(h.attributes.get('aria-expanded'), 'true');
  assert.equal(h.node.style.transform, ''); assert.equal(h.node.style.willChange, '');
  assert.equal(h.motion.snapshot(h.node, 'sidebar').active, false); assert.equal(h.frames.size, 0);
});

test('mobile sidebar exit leaves the closed sidebar inert and with no active frame', () => {
  const h = setup(); h.open(true, { immediate: true }); h.open(false); h.drain();
  assert.equal(h.node.inert, true); assert.equal(h.node.classList.contains('mobile-open'), false);
  assert.equal(h.attributes.get('aria-expanded'), 'false');
  assert.equal(h.node.style.transform, ''); assert.equal(h.node.style.willChange, '');
  assert.equal(h.frames.size, 0);
});

test('responsive listener cancels mobile motion and releases transforms and inert on desktop', () => {
  for (const targetOpen of [true, false]) {
    const h = setup(); h.open(!targetOpen, { immediate: true }); h.open(targetOpen); h.step(50);
    assert.notEqual(h.node.style.transform, '');
    h.resize(false);
    assert.equal(h.node.inert, false); assert.equal(h.node.style.transform, ''); assert.equal(h.node.style.willChange, '');
    assert.equal(h.node.dataset.motionSidebar, undefined);
    assert.equal(h.frames.size, 0); assert.equal(h.motion.snapshot(h.node, 'sidebar').active, false);
    h.step(100); assert.equal(h.node.style.transform, '', 'an obsolete callback must not reapply mobile transforms');
    h.resize(true);
    assert.equal(h.node.inert, !targetOpen); assert.equal(h.node.classList.contains('mobile-open'), targetOpen);
    assert.equal(h.motion.snapshot(h.node, 'sidebar').value.x, targetOpen ? 0 : -102);
    assert.equal(h.node.style.transform, ''); assert.equal(h.frames.size, 0);
  }
});

test('desktop initialization keeps the permanent navigation usable without animating it', () => {
  const h = setup(false); h.open(false, { immediate: true });
  assert.equal(h.node.inert, false); assert.equal(h.node.style.transform, '');
  assert.equal(h.node.dataset.motionSidebar, undefined); assert.equal(h.frames.size, 0);
});
