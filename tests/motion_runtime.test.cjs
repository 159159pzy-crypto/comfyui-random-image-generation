const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8');

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, fn) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(fn); }
  dispatch(name, values = {}) {
    const event = { type: name, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...values };
    for (const listener of this.listeners.get(name) || []) listener(event);
    return event;
  }
}
function element(document, tagName = 'DIV', initial = {}) {
  const attrs = new Map(), classes = new Set();
  const el = Object.assign(new Events(), {
    tagName, dataset: {}, hidden: false, inert: false, isConnected: true, children: [],
    style: { setProperty(key, value) { this[key] = value; }, removeProperty(key) { delete this[key]; } },
    classList: { contains: name => classes.has(name), add: name => classes.add(name), remove: name => classes.delete(name) },
    getAttribute: key => attrs.get(key) ?? null,
    hasAttribute: key => attrs.has(key),
    setAttribute: (key, value) => attrs.set(key, String(value)),
    removeAttribute: key => attrs.delete(key),
    matches(selector) { return selector === 'details' ? tagName === 'DETAILS' : selector === '#imagePreviewDialog' && this.id === 'imagePreviewDialog'; },
    contains(child) { return child === this || this.children.some(node => node.contains(child)); },
    closest(selector) { if (selector === 'dialog' && tagName === 'DIALOG') return this; if (selector === 'summary' && tagName === 'SUMMARY') return this; return this.parentElement?.closest(selector) || null; },
    focus() { document.activeElement = this; this.focusCount = (this.focusCount || 0) + 1; },
    getRootNode: () => document,
    querySelector: () => null,
    querySelectorAll: () => [],
    getBoundingClientRect() { return { left: 10, top: 10, right: 210, bottom: 210, width: 200, height: 200 }; },
    ...initial,
  });
  return el;
}
function harness() {
  let time = 0, frameId = 0;
  const frames = new Map(), document = new Events(), media = new Events();
  Object.assign(document, { hidden: false, activeElement: null, nodes: [], querySelectorAll(selector) { return this.nodes.filter(node => selector === 'details' ? node.tagName === 'DETAILS' : node.tagName === 'DIALOG'); }, contains: () => true });
  document.documentElement = element(document, 'HTML');
  media.matches = false;
  const ctx = vm.createContext({ document, performance: { now: () => time }, matchMedia: () => media,
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
    getComputedStyle: () => ({ paddingBottom: '0', borderBottomWidth: '0' }),
  });
  vm.runInContext(source, ctx);
  function step(ms = 1000 / 60) {
    time += ms;
    const current = [...frames.values()]; frames.clear();
    for (const callback of current) callback(time);
  }
  function drain() { let guard = 500; while (frames.size && guard--) step(); assert.ok(guard > 0, 'motion should settle and release its frame'); }
  function dialog(opener = null) {
    const node = element(document, 'DIALOG', { open: false, nativeCloseCount: 0,
      showModal() { this.open = true; this.nativeOpener = document.activeElement; this.focus(); },
      close(value) { this.open = false; this.nativeCloseCount++; if (value !== undefined) this.returnValue = value; this.nativeOpener?.focus(); this.dispatch('close'); },
    });
    document.nodes.push(node); if (opener) opener.focus(); return node;
  }
  function details({ open = false, name = '', height = 240 } = {}) {
    const node = element(document, 'DETAILS', { open, naturalHeight: height });
    const summary = element(document, 'SUMMARY', { parentElement: node, getBoundingClientRect: () => ({ top: 0, bottom: 40, height: 40 }) });
    node.children.push(summary); node.querySelector = () => summary;
    node.getBoundingClientRect = () => ({ top: 0, height: node.style.height && node.style.height !== 'auto' ? parseFloat(node.style.height) : node.open ? node.naturalHeight : 40 });
    if (name) node.setAttribute('name', name);
    document.nodes.push(node); return node;
  }
  return { motion: ctx.StudioMotion, document, media, frames, step, drain, dialog, details, element: initial => element(document, 'DIV', initial) };
}

test('one RAF serves independent owners, then releases all idle work', async () => {
  const h = harness(), first = {}, second = {};
  const a = h.motion.spring(first, 'move', { x: 100 }, { initial: { x: 0 } });
  const b = h.motion.spring(second, 'fade', { opacity: 1 }, { initial: { opacity: 0 } });
  assert.equal(h.frames.size, 1);
  h.drain();
  assert.equal((await a.finished).cancelled, false);
  assert.equal((await b.finished).cancelled, false);
  assert.equal(a.current.x, 100); assert.equal(h.frames.size, 0);
});

test('retarget preserves presentation and velocity, cancels only the superseded completion', async () => {
  const h = harness(), owner = {}, completions = [];
  const a = h.motion.spring(owner, 'move', { x: 100, y: 40 }, { initial: { x: 0, y: 0 }, onComplete: () => completions.push('old') });
  h.step(80);
  const before = h.motion.snapshot(owner, 'move');
  const b = h.motion.spring(owner, 'move', { x: -40, y: 100 }, { initial: { x: 900, y: 900 }, onComplete: () => completions.push('new') });
  assert.equal(b.current.x, before.value.x); assert.equal(b.velocity.x, before.velocity.x);
  assert.equal((await a.finished).cancelled, true);
  a.finish(); a.cancel(); assert.equal(b.active, true, 'old handles cannot affect a newer intent');
  h.drain(); await b.finished;
  assert.deepEqual(completions, ['new']); assert.equal(b.current.x, -40);
});

test('pixel precision keeps an omitted opacity field precise at start and during frames', () => {
  const h = harness(), owner = {};
  const animation = h.motion.spring(owner, 'flight', { width: 240, opacity: 0 }, {
    initial: { width: 240.25, opacity: .2 }, precision: { width: .5 },
  });
  assert.equal(animation.active, true, 'geometry within half a pixel must not finish a visible fade');
  h.step(16);
  assert.equal(animation.active, true, 'frame settling must use the opacity default instead of pixel precision');
  assert.ok(animation.current.opacity > .001 && animation.current.opacity < .2);
  h.drain();
  assert.equal(animation.current.width, 240); assert.equal(animation.current.opacity, 0);
  assert.equal(h.frames.size, 0);

  const settled = h.motion.spring({}, 'flight', { width: 240, opacity: 0 }, {
    initial: { width: 240.25, opacity: .0005 }, precision: { width: .5 },
  });
  assert.equal(settled.active, false, 'initial values already within each field tolerance finish immediately');
  assert.equal(settled.current.width, 240); assert.equal(settled.current.opacity, 0);
  assert.equal(h.frames.size, 0);
});

test('scalar and field precision settle pixel motion alike while the default remains precise', () => {
  const h = harness();
  const options = { initial: { width: 920 }, response: .24, damping: .92 };
  const scalar = h.motion.spring({}, 'flight', { width: 240 }, { ...options, precision: .5 });
  const field = h.motion.spring({}, 'flight', { width: 240 }, { ...options, precision: { width: .5 } });
  const precise = h.motion.spring({}, 'flight', { width: 240 }, options);
  for (let elapsed = 0; elapsed < 320; elapsed += 16) {
    h.step(16);
    assert.equal(scalar.current.width, field.current.width);
    assert.equal(scalar.velocity.width, field.velocity.width);
    assert.equal(scalar.active, field.active);
  }
  assert.equal(field.active, false, 'subpixel geometry should release its animation without a long tail');
  assert.equal(precise.active, true, 'unconfigured springs retain their existing tolerance');
  h.drain(); assert.equal(precise.current.width, 240);

  const settled = h.motion.spring({}, 'move', { x: 0, y: 0 }, {
    initial: { x: .25, y: -.4 }, velocity: { x: 4, y: -4 }, precision: .5,
  });
  assert.equal(settled.active, false, 'scalar precision applies to all initial values and velocities');
  assert.equal(h.frames.size, 0);
});

test('changing field precision on retarget preserves every live value and velocity', async () => {
  const h = harness(), owner = {}, completions = [];
  const first = h.motion.spring(owner, 'flight', { width: 920, opacity: 1 }, {
    initial: { width: 240, opacity: 0 }, precision: { width: .5 }, onComplete: () => completions.push('old'),
  });
  h.step(80);
  const before = h.motion.snapshot(owner, 'flight');
  const next = h.motion.spring(owner, 'flight', { width: 240, opacity: 0 }, {
    initial: { width: 1000, opacity: 1 }, precision: { width: .25, opacity: .0005 }, onComplete: () => completions.push('new'),
  });
  for (const key of ['width', 'opacity']) {
    assert.equal(next.current[key], before.value[key]); assert.equal(next.velocity[key], before.velocity[key]);
  }
  assert.equal((await first.finished).cancelled, true);
  first.finish(); first.cancel(); assert.equal(next.active, true);
  h.drain();
  assert.equal((await next.finished).cancelled, false);
  assert.deepEqual(completions, ['new']);
  assert.equal(next.current.width, 240); assert.equal(next.current.opacity, 0);
});

test('analytic springs have matching positions at 60 and 120 Hz including gesture damping', () => {
  for (const damping of [1, .85, 1.2]) {
    const sample = hz => {
      const h = harness(), owner = {};
      h.motion.spring(owner, 'move', { x: 100 }, { initial: { x: 0 }, damping });
      for (let index = 0; index < hz / 10; index++) h.step(1000 / hz);
      h.motion.spring(owner, 'move', { x: -30 }, { damping });
      for (let index = 0; index < hz / 10; index++) h.step(1000 / hz);
      return h.motion.snapshot(owner, 'move');
    };
    const a = sample(60), b = sample(120);
    assert.ok(Math.abs(a.value.x - b.value.x) < 1e-9);
    assert.ok(Math.abs(a.velocity.x - b.velocity.x) < 1e-8);
  }
});

test('presence reverses closing without a stale callback hiding the reopened element', async () => {
  const h = harness(), el = h.element({ hidden: true }), completions = [];
  h.motion.presence(el, true, { from: { y: 10, scale: .985 } }); h.step(80);
  const closing = h.motion.presence(el, false, { from: { y: 10, scale: .985 }, onComplete: () => completions.push('closed') });
  h.step(40);
  const current = h.motion.snapshot(el, 'presence');
  h.motion.presence(el, true, { from: { y: 10, scale: .985 }, onComplete: () => completions.push('open') });
  assert.equal(h.motion.snapshot(el, 'presence').value.progress, current.value.progress);
  assert.equal(el.hidden, false); assert.equal(el.inert, false);
  assert.equal((await closing.finished).cancelled, true);
  h.drain();
  assert.equal(el.hidden, false); assert.equal(el.dataset.motionState, 'open');
  assert.deepEqual(completions, ['open']); assert.equal(el.style.transform, '');
});

test('an already-visible panel can explicitly replay a content entrance', () => {
  const h = harness(), el = h.element();
  h.motion.presence(el, true); assert.equal(el.style.opacity, '0'); h.drain();
  h.motion.presence(el, true, { restart: true }); assert.equal(el.style.opacity, '0'); h.drain();
  assert.equal(el.style.opacity, '');
});

test('same-target updates reuse one presence operation and honor the latest completion', () => {
  const h = harness(), el = h.element({ hidden: true }), completed = [];
  const first = h.motion.presence(el, true, { from: { y: 10 }, onComplete: () => completed.push('old') });
  h.step(40);
  const second = h.motion.presence(el, true, { onComplete: () => completed.push('new') });
  assert.equal(first, second); assert.equal(h.frames.size, 1);
  h.drain(); assert.deepEqual(completed, ['new']);
  assert.equal(h.motion.presence(el, true), second); assert.equal(h.frames.size, 0);
  h.motion.presence(el, false); h.step(40); h.motion.presence(el, false, { immediate: true });
  assert.equal(el.hidden, true); assert.equal(h.frames.size, 0);
});

test('managed presence unlocks a drawer that was initially inert and closed', () => {
  const h = harness(), el = h.element({ hidden: true, inert: true });
  h.motion.presence(el, false); assert.equal(el.inert, true); assert.equal(h.frames.size, 0);
  h.motion.presence(el, true); assert.equal(el.inert, false); h.drain(); assert.equal(el.inert, false);
});

test('reduced motion changes and hidden pages settle the latest targets without residual RAF', async () => {
  const h = harness(), el = h.element({ hidden: true });
  h.motion.presence(el, true, { from: { y: 10 } }); h.step(50);
  const closing = h.motion.presence(el, false);
  h.media.matches = true; h.media.dispatch('change');
  assert.equal(el.hidden, true); assert.equal(h.frames.size, 0); assert.equal((await closing.finished).cancelled, false);
  h.media.matches = false;
  h.motion.presence(el, true); h.document.hidden = true; h.document.dispatch('visibilitychange');
  assert.equal(el.hidden, false); assert.equal(el.style.transform, ''); assert.equal(h.frames.size, 0);
  assert.equal(h.document.documentElement.hasAttribute('data-motion-paused'), true);
  h.document.hidden = false; h.document.dispatch('visibilitychange');
  assert.equal(h.document.documentElement.hasAttribute('data-motion-paused'), false);
});

test('freeze retains live presentation and finish commits only the current owner/channel', async () => {
  const h = harness(), el = h.element();
  const motion = h.motion.presence(el, true, { from: { y: 10 } }); h.step(60);
  const transform = el.style.transform, frozen = h.motion.freeze(el, 'presence');
  assert.ok(frozen.value.progress > 0 && frozen.value.progress < 1);
  assert.equal(el.style.transform, transform); assert.equal(h.frames.size, 0);
  assert.equal((await motion.finished).cancelled, true);
  h.motion.presence(el, true); h.motion.finish(el, 'presence');
  assert.equal(el.style.transform, ''); assert.equal(el.dataset.motionState, 'open');
});

test('disclosure reverses from the current height and restores intrinsic sizing', () => {
  const h = harness(), node = h.details();
  h.motion.disclosure(node, true); h.step(80);
  const height = node.style.height;
  h.motion.disclosure(node, false); assert.equal(node.style.height, height); h.step(40);
  const reversingHeight = node.style.height;
  h.motion.disclosure(node, true); assert.equal(node.style.height, reversingHeight);
  h.drain(); assert.equal(node.open, true); assert.equal(node.style.height, ''); assert.equal(node.style.overflow, '');
  h.motion.disclosure(node, false); h.drain(); assert.equal(node.open, false);
});

test('named disclosures keep exclusivity while both open attributes exist during exit', () => {
  const h = harness(), first = h.details({ open: true, name: 'group' }), second = h.details({ name: 'group' });
  h.motion.bindDisclosures(h.document);
  assert.equal(first.getAttribute('name'), null);
  h.motion.disclosure(second, true);
  assert.equal(first.open, true, 'old panel remains mounted during its exit');
  assert.equal(first.dataset.motionTargetOpen, 'false'); assert.equal(second.dataset.motionTargetOpen, 'true');
  h.drain(); assert.equal(first.open, false); assert.equal(second.open, true);
});

test('delegated disclosures handle newly inserted nodes and do not double-toggle one event', () => {
  const h = harness(); h.motion.bindDisclosures(h.document); h.motion.bindDisclosures(h.document);
  const node = h.details(), summary = node.children[0];
  const event = h.document.dispatch('click', { target: summary });
  assert.equal(event.defaultPrevented, true); assert.equal(node.dataset.motionTargetOpen, 'true');
  h.step(60); h.document.dispatch('click', { target: summary }); h.drain(); assert.equal(node.open, false);
});

test('dialog reopen cancels native close and stale focus restoration', async () => {
  const h = harness(), trigger = h.element(), node = h.dialog(trigger), completed = [];
  h.motion.dialog(node, true); h.step(80);
  const closing = h.motion.dialog(node, false, { onComplete: () => completed.push('closed') }); h.step(40);
  h.motion.dialog(node, true, { onComplete: () => completed.push('open') }); h.drain();
  assert.equal((await closing.finished).cancelled, true); assert.equal(node.open, true);
  assert.equal(node.nativeCloseCount, 0); assert.equal(h.document.activeElement, node); assert.deepEqual(completed, ['open']);
  h.motion.dialog(node, false); assert.equal(node.open, true); h.drain();
  assert.equal(node.open, false); assert.equal(h.document.activeElement, trigger);
});

test('closing an older modal cannot steal focus from a newer modal', () => {
  const h = harness(), trigger = h.element(), old = h.dialog(trigger), newer = h.dialog();
  h.motion.dialog(old, true, { immediate: true });
  h.motion.dialog(old, false); h.step(50);
  h.motion.dialog(newer, true); h.drain();
  assert.equal(old.open, false); assert.equal(newer.open, true); assert.equal(h.document.activeElement, newer);
});

test('nested dialog close returns focus to the exact parent trigger', () => {
  const h = harness(), outerTrigger = h.element(), parent = h.dialog(outerTrigger), child = h.dialog();
  h.motion.dialog(parent, true, { immediate: true });
  const innerTrigger = h.element({ parentElement: parent }); innerTrigger.focus();
  h.motion.dialog(child, true, { immediate: true });
  h.motion.dialog(child, false); h.drain(); assert.equal(h.document.activeElement, innerTrigger);
  h.motion.dialog(parent, false); h.drain(); assert.equal(h.document.activeElement, outerTrigger);
});

test('dialog adapters preserve Escape cancellation and method=dialog returnValue', () => {
  const h = harness(), node = h.dialog();
  let block = true;
  node.addEventListener('cancel', event => { if (block) event.preventDefault(); });
  h.motion.dialog(node, true, { immediate: true });
  node.dispatch('cancel'); h.drain(); assert.equal(node.open, true);
  block = false; node.dispatch('cancel'); assert.equal(node.open, true); h.drain(); assert.equal(node.open, false);
  h.motion.dialog(node, true, { immediate: true });
  const form = h.element(); form.setAttribute('method', 'dialog');
  const submitter = h.element({ value: 'accept' });
  const event = node.dispatch('submit', { target: form, submitter });
  assert.equal(event.defaultPrevented, true); assert.equal(node.open, true);
  h.drain(); assert.equal(node.open, false); assert.equal(node.returnValue, 'accept');
});

test('dialog Escape remembers a custom focus policy for this opening only', () => {
  const h = harness(), trigger = h.element(), node = h.dialog(trigger);
  h.motion.dialog(node, true, { immediate: true, restoreFocus: false });
  const initialFocusCount = trigger.focusCount;
  node.dispatch('cancel'); h.drain();
  assert.equal(trigger.focusCount, initialFocusCount + 1, 'only native focus restoration occurs');
  h.motion.dialog(node, true, { immediate: true });
  node.dispatch('cancel'); h.drain();
  assert.equal(trigger.focusCount, initialFocusCount + 3, 'a fresh opening restores the normal focus policy');
});

test('external close cleans an entrance, and a queued close event does not close a reopened dialog', () => {
  const h = harness(), node = h.dialog();
  h.motion.dialog(node, true); h.step(40); node.close();
  assert.equal(h.frames.size, 0); assert.equal(node.hidden, true); assert.equal(node.style.transform, '');
  h.motion.dialog(node, true); node.dispatch('close'); h.drain();
  assert.equal(node.open, true); assert.equal(node.hidden, false);
});

test('external native close during an exit removes temporary styles and cannot run stale completion', () => {
  const h = harness(), node = h.dialog(), completions = [];
  h.motion.dialog(node, true, { immediate: true });
  h.motion.dialog(node, false, { onComplete: () => completions.push('stale') }); h.step(60);
  node.close();
  assert.equal(node.hidden, true); assert.equal(node.dataset.motionState, 'closed');
  assert.equal(node.style.opacity, ''); assert.equal(node.style.transform, ''); assert.equal(node.style.willChange, '');
  assert.equal(h.frames.size, 0); assert.deepEqual(completions, []);
});
