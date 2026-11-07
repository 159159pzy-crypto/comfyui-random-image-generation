const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const runtime = fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8');
function appFunction(name) {
  const start = app.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name} must be the production function`);
  return app.slice(start, app.indexOf('\n}', start) + 2);
}
function harness() {
  let time = 0, frameId = 0, requestedFrames = 0;
  const frames = new Map(), requests = [], operations = [], notices = [];
  const document = { hidden: false, activeElement: null, addEventListener() {} };
  class Element {
    constructor(tagName) {
      this.tagName = tagName.toUpperCase(); this.children = []; this.parentElement = null;
      this.dataset = {}; this.style = {}; this.hidden = false; this.inert = false;
      this.attributes = new Map(); this.handlers = new Map(); this.classes = new Set();
      this.classList = {
        toggle: (name, active) => active ? this.classes.add(name) : this.classes.delete(name),
        contains: name => this.classes.has(name),
      };
    }
    get firstElementChild() { return this.children[0] || null; }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    removeAttribute(name) { this.attributes.delete(name); }
    closest() { return null; }
    contains(node) { return node === this || this.children.some(child => child.contains(node)); }
    focus() { document.activeElement = this; }
    append(...nodes) { for (const node of nodes) this.insertBefore(node, null); }
    insertBefore(node, anchor) {
      if (node === anchor) return node;
      node.remove();
      const index = anchor === null ? this.children.length : this.children.indexOf(anchor);
      assert.ok(index >= 0, 'insertBefore anchor must be an existing child');
      this.children.splice(index, 0, node); node.parentElement = this;
      return node;
    }
    remove() {
      if (!this.parentElement) return;
      if (this.contains(document.activeElement)) document.activeElement = null;
      const siblings = this.parentElement.children;
      siblings.splice(siblings.indexOf(this), 1); this.parentElement = null;
    }
    addEventListener(name, handler) {
      if (!this.handlers.has(name)) this.handlers.set(name, []);
      this.handlers.get(name).push(handler);
    }
    async click() { for (const handler of this.handlers.get('click') || []) await handler({ target: this }); }
  }
  document.createElement = tag => new Element(tag);
  document.documentElement = new Element('html');
  const panel = new Element('div'), badge = new Element('button');
  panel.hidden = true;
  const ctx = vm.createContext({ document, performance: { now: () => time },
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    requestAnimationFrame(callback) { requestedFrames++; frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); }, ui: { queuePanel: panel, queueBadge: badge },
    request: async (url, options) => { requests.push({ url, options }); return { queue: [] }; },
    toast: message => notices.push(message),
  });
  vm.runInContext(runtime, ctx);
  const motion = ctx.StudioMotion;
  ctx.StudioMotion = { ...motion, presence(...args) { const operation = motion.presence(...args); operations.push(operation); return operation; } };
  vm.runInContext(`let currentQueue = [];\n${appFunction('setQueuePanelOpen')}\n${appFunction('renderQueue')}`, ctx);
  const step = (elapsed = 1000 / 60) => {
    time += elapsed;
    const callbacks = [...frames.values()]; frames.clear();
    for (const callback of callbacks) callback(time);
  };
  const drain = () => {
    let remaining = 500;
    while (frames.size && remaining--) step();
    assert.ok(remaining > 0, 'queue motion must settle');
  };
  return { ctx, document, panel, badge, requests, notices, motion, operations, frames,
    requestedFrames: () => requestedFrames, step, drain };
}
const item = (id, extra = {}) => ({ queue_id: id, position: 1, count: 2, model_name: 'Anima', ...extra });
const ids = panel => panel.children.map(row => row.dataset.queueId);

test('queue polling updates existing labels without replacing or blurring a focused remove button', () => {
  const h = harness();
  h.ctx.renderQueue([item('a'), item('b', { position: 2 })]);
  const [first, second] = h.panel.children, focused = second.children[1];
  focused.focus();
  for (let update = 0; update < 5; update++) {
    h.ctx.renderQueue([item('a'), item('b', { position: 2, count: update + 3, model_name: 'Anima Custom' })]);
  }
  assert.equal(h.panel.children[0], first); assert.equal(h.panel.children[1], second);
  assert.equal(h.document.activeElement, focused);
  assert.equal(second.children[1], focused);
  assert.equal(second.firstElementChild.textContent, '2. 7 张 · Anima Custom');
  assert.equal(focused.handlers.get('click').length, 1, 'polls must not accumulate remove listeners');
  assert.equal(h.badge.textContent, '队列 2'); assert.equal(h.badge.hidden, false);
});

test('queue order changes reuse the same rows in the new server order', () => {
  const h = harness();
  h.ctx.renderQueue([item('a'), item('b'), item('c')]);
  const rows = new Map(h.panel.children.map(row => [row.dataset.queueId, row]));
  h.ctx.renderQueue([item('c', { position: 1 }), item('a', { position: 2 }), item('b', { position: 3 })]);
  assert.deepEqual(ids(h.panel), ['c', 'a', 'b']);
  for (const row of h.panel.children) assert.equal(row, rows.get(row.dataset.queueId));
  assert.equal(h.panel.children[2].firstElementChild.textContent, '3. 2 张 · Anima');
});

test('removed rows detach while retained and newly arriving rows remain in order', () => {
  const h = harness();
  h.ctx.renderQueue([item('a'), item('b')]);
  const [removed, retained] = h.panel.children;
  h.ctx.renderQueue([item('b'), item('c', { model_name: 'Anima Custom' })]);
  assert.deepEqual(ids(h.panel), ['b', 'c']);
  assert.equal(removed.parentElement, null); assert.equal(h.panel.children[0], retained);
  assert.equal(h.panel.children[1].parentElement, h.panel);
  assert.equal(h.panel.children[1].firstElementChild.textContent, '1. 2 张 · Anima Custom');
});

test('a reused remove button submits exactly once using its current row queue ID', async () => {
  const h = harness();
  h.ctx.renderQueue([item('original')]);
  const row = h.panel.children[0], button = row.children[1];
  for (let update = 0; update < 4; update++) h.ctx.renderQueue([item('original', { count: update + 1 })]);
  // The row is the current rendered identity; the callback must not capture an
  // earlier entry object if reconciliation updates that identity later.
  row.dataset.queueId = 'current-id';
  await button.click();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/api/batches/queue/current-id');
  assert.equal(h.requests[0].options.method, 'DELETE');
  assert.equal(h.panel.children.length, 0); assert.equal(h.badge.hidden, true);
  assert.deepEqual(h.notices, []);
  h.drain();
});

test('repeated empty queue polls reuse closing motion and remain idle once closed', () => {
  const h = harness();
  h.ctx.renderQueue([item('a')]); h.ctx.setQueuePanelOpen(true); h.drain();
  h.ctx.renderQueue([]); h.step(45);
  const operation = h.operations.at(-1), before = h.motion.snapshot(h.panel, 'presence');
  const requests = h.requestedFrames();
  for (let poll = 0; poll < 10; poll++) h.ctx.renderQueue([]);
  const after = h.motion.snapshot(h.panel, 'presence');
  assert.ok(h.operations.slice(-10).every(handle => handle === operation));
  assert.equal(after.value.progress, before.value.progress); assert.equal(after.velocity.progress, before.velocity.progress);
  assert.equal(h.requestedFrames(), requests, 'polls must not schedule duplicate animation frames');
  assert.equal(h.panel.hidden, false, 'the current close remains visible until it finishes');
  h.drain();
  assert.equal(h.panel.hidden, true); assert.equal(h.panel.inert, true);
  assert.equal(h.badge.getAttribute('aria-expanded'), 'false');
  const settledFrames = h.requestedFrames();
  h.ctx.renderQueue([]); h.ctx.renderQueue([]);
  assert.equal(h.frames.size, 0); assert.equal(h.requestedFrames(), settledFrames);
  assert.equal(h.panel.dataset.motionState, 'closed');
});
