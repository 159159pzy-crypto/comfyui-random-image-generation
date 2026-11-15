const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const motionSource = fs.readFileSync(path.join(__dirname, '../static/motion.js'), 'utf8');
const filtersSource = fs.readFileSync(path.join(__dirname, '../static/history-filters.js'), 'utf8');

function harness({ mobile = false, reduced = false } = {}) {
  let time = 0, frameId = 0;
  const frames = new Map(), nodes = new Map(), listeners = new Map();
  const document = { hidden: false, activeElement: null, upperModal: null,
    getElementById: id => nodes.get(id),
    querySelector: () => document.upperModal,
    addEventListener(name, listener) { listeners.set(`document:${name}`, listener); },
    createRange() { let node; return { selectNodeContents(value) { node = value; }, getClientRects() { return node.rects || []; }, detach() {} }; },
  };
  function element(tagName = 'DIV', parentElement = null, extra = {}) {
    const attributes = new Map();
    const node = { tagName, nodeType: 1, parentElement, children: [], childNodes: [], hidden: false, inert: false,
      isConnected: true, isContentEditable: false, style: {}, dataset: {}, focusCount: 0,
      clientWidth: 600, clientHeight: 400, offsetWidth: 600, offsetHeight: 400, clientLeft: 0, clientTop: 0,
      setAttribute: (name, value) => attributes.set(name, String(value)),
      getAttribute: name => attributes.get(name), removeAttribute: name => attributes.delete(name),
      contains(target) { for (let current = target; current; current = current.parentElement) if (current === this) return true; return false; },
      focus() { document.activeElement = this; this.focusCount++; },
      getBoundingClientRect: () => ({ left: 0, top: 0, width: 600, height: 400, right: 600, bottom: 400 }),
      closest(selector) {
        const interactiveTags = new Set(['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL', 'SUMMARY', 'IMG', 'SVG', 'VIDEO', 'AUDIO', 'CANVAS']);
        for (let current = this; current; current = current.parentElement) {
          if (selector === '[hidden], [inert]') { if (current.hidden || current.inert) return current; }
          else if (interactiveTags.has(current.tagName) || current.interactive || current.isContentEditable) return current;
        }
        return null;
      },
      ...extra,
    };
    parentElement?.children.push(node); return node;
  }
  document.documentElement = element('HTML', null, { clientWidth: 1280, clientHeight: 900 });
  document.body = element('BODY', document.documentElement); document.body.style.overflow = 'auto';
  const main = element('MAIN', document.body), protectedSibling = element('ASIDE', document.body, { inert: true });
  const trigger = element('BUTTON', main), drawer = element('ASIDE', document.body, { hidden: true, inert: true });
  const input = element('INPUT', drawer), blank = element('DIV', main), outsideButton = element('BUTTON', main);
  nodes.set('openHistoryFilters', trigger); nodes.set('historyFilterDrawer', drawer); nodes.set('historyFilterSearch', input);
  const viewportMedia = { matches: mobile }, reducedMedia = { matches: reduced };
  const window = { addEventListener(name, listener) { listeners.set(`window:${name}`, listener); } };
  const ctx = vm.createContext({ document, window, URLSearchParams, setTimeout, clearTimeout,
    historyDetailState: { homeQuery: { source: '', folderId: '', q: '', contentFilters: { include: [], exclude: [] } } },
    performance: { now: () => time }, matchMedia: () => reducedMedia,
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame(id) { frames.delete(id); },
  });
  vm.runInContext(motionSource, ctx); vm.runInContext(filtersSource, ctx);
  ctx.viewportMedia = viewportMedia;
  vm.runInContext('historyFiltersState.ready = true; historyFiltersState.media = viewportMedia;', ctx);
  ctx.historyFiltersLoadCatalog = async () => null;
  ctx.historyFiltersSyncMobile();
  function step(ms = 1000 / 60) {
    time += ms; const current = [...frames.values()]; frames.clear(); for (const callback of current) callback(time);
  }
  function drain() { let guard = 500; while (frames.size && guard--) step(); assert.ok(guard > 0); }
  const state = () => vm.runInContext('historyFiltersState', ctx);
  const pointer = (target = blank, extra = {}) => ({ target, button: 0, isPrimary: true, pointerId: 1, clientX: 100, clientY: 100,
    preventDefault() { throw new Error('blank dismissal must not consume clicks'); }, stopPropagation() { throw new Error('blank dismissal must not stop propagation'); }, ...extra });
  const open = () => { ctx.historyFiltersSetOpen(true); drain(); };
  return { ctx, document, drawer, input, trigger, main, protectedSibling, blank, outsideButton, element,
    viewportMedia, reducedMedia, frames, step, drain, state, pointer, open, listeners };
}

test('real motion closes through transparent pixels before hiding, without a visible terminal pop', () => {
  const h = harness(); h.open(); h.ctx.historyFiltersSetOpen(false);
  let lastVisibleOpacity = 1, at300;
  for (let frame = 1; h.frames.size; frame++) {
    h.step();
    if (!h.drawer.hidden) {
      const opacity = Number(h.drawer.style.opacity);
      assert.ok(opacity <= lastVisibleOpacity); lastVisibleOpacity = opacity;
      if (frame === 18) at300 = opacity;
    }
  }
  assert.ok(at300 < .02, `300ms opacity was ${at300}`);
  assert.ok(lastVisibleOpacity < .002, `last visible opacity was ${lastVisibleOpacity}`);
  assert.equal(h.drawer.hidden, true); assert.equal(h.trigger.focusCount, 1);
});

test('closing and reopening reuses live position/velocity and cancels stale hide and focus restoration', () => {
  const h = harness({ mobile: true }); h.open(); h.ctx.historyFiltersSetOpen(false); h.step(65);
  const before = h.ctx.StudioMotion.snapshot(h.drawer, 'presence');
  h.ctx.historyFiltersSetOpen(true);
  const after = h.ctx.StudioMotion.snapshot(h.drawer, 'presence');
  assert.equal(after.value.progress, before.value.progress); assert.equal(after.velocity.progress, before.velocity.progress);
  h.drain();
  assert.equal(h.drawer.hidden, false); assert.equal(h.drawer.inert, false); assert.equal(h.trigger.focusCount, 0);
  assert.equal(h.main.inert, true); assert.equal(h.document.body.style.overflow, 'hidden');
  assert.equal(h.state().modalClosing, false); assert.equal(h.document.activeElement, h.input);
});

test('mobile keeps background inert and scroll locked until the current closing animation completes', () => {
  const h = harness({ mobile: true }); h.open(); h.ctx.historyFiltersSetOpen(false);
  assert.equal(h.state().modalClosing, true); assert.equal(h.main.inert, true); assert.equal(h.document.body.style.overflow, 'hidden');
  h.step(200);
  assert.equal(h.drawer.hidden, false); assert.equal(h.main.inert, true); assert.equal(h.drawer.getAttribute('aria-modal'), 'true');
  h.drain();
  assert.equal(h.main.inert, false); assert.equal(h.protectedSibling.inert, true);
  assert.equal(h.document.body.style.overflow, 'auto'); assert.equal(h.state().modalClosing, false);
  assert.equal(h.drawer.getAttribute('aria-modal'), 'false'); assert.equal(h.trigger.focusCount, 1);
});

test('reduced motion closes and releases mobile locks immediately', () => {
  const h = harness({ mobile: true, reduced: true }); h.open(); h.ctx.historyFiltersSetOpen(false);
  assert.equal(h.frames.size, 0); assert.equal(h.drawer.hidden, true); assert.equal(h.main.inert, false);
  assert.equal(h.document.body.style.overflow, 'auto'); assert.equal(h.trigger.focusCount, 1);
});

test('completed desktop close does not steal focus claimed by another control or upper modal', () => {
  for (const upperModal of [false, true]) {
    const h = harness(); h.open(); h.ctx.historyFiltersSetOpen(false); h.step(80);
    if (upperModal) h.document.upperModal = {};
    else h.outsideButton.focus();
    h.drain(); assert.equal(h.trigger.focusCount, 0);
  }
});

test('only a primary desktop blank-space click closes, without consuming events or returning focus', () => {
  const h = harness(); h.open();
  h.ctx.historyFiltersOutsideDown(h.pointer()); h.ctx.historyFiltersOutsideUp(h.pointer());
  assert.equal(h.state().open, false); h.drain(); assert.equal(h.trigger.focusCount, 0);
  const mobile = harness({ mobile: true }); mobile.open();
  mobile.ctx.historyFiltersOutsideDown(mobile.pointer()); mobile.ctx.historyFiltersOutsideUp(mobile.pointer());
  assert.equal(mobile.state().open, true);
});

test('outside interactive content, text glyphs, trigger and artwork are not blank dismissal targets', () => {
  const h = harness(); h.open();
  const text = h.element('P', h.main); text.childNodes = [{ nodeType: 3, textContent: '作品库', rects: [{ left: 80, top: 80, right: 140, bottom: 120 }] }];
  const targets = [h.outsideButton, h.trigger, h.input, text, h.element('A', h.main), h.element('LABEL', h.main),
    h.element('SUMMARY', h.main), h.element('DIV', h.main, { isContentEditable: true }), h.element('ARTICLE', h.main, { interactive: true })];
  for (const target of targets) {
    h.ctx.historyFiltersOutsideDown(h.pointer(target)); h.ctx.historyFiltersOutsideUp(h.pointer(target));
    assert.equal(h.state().open, true, target.tagName);
  }
  // Text elsewhere in a container must not disqualify its actual empty padding.
  h.ctx.historyFiltersOutsideDown(h.pointer(text, { clientX: 200 }));
  h.ctx.historyFiltersOutsideUp(h.pointer(text, { clientX: 200 }));
  assert.equal(h.state().open, false);
});

test('dragging, scrolling, cancellation, mixed endpoints and secondary pointers never dismiss', () => {
  const h = harness(); h.open();
  const up = extra => h.ctx.historyFiltersOutsideUp(h.pointer(h.blank, extra));
  h.ctx.historyFiltersOutsideDown(h.pointer(h.input)); up(); assert.equal(h.state().open, true);
  h.ctx.historyFiltersOutsideDown(h.pointer()); h.ctx.historyFiltersOutsideUp(h.pointer(h.input)); assert.equal(h.state().open, true);
  h.ctx.historyFiltersOutsideDown(h.pointer()); h.ctx.historyFiltersOutsideMove(h.pointer(h.blank, { clientX: 108 })); up(); assert.equal(h.state().open, true);
  h.ctx.historyFiltersOutsideDown(h.pointer()); h.ctx.historyFiltersOutsideCancel(); up(); assert.equal(h.state().open, true);
  h.ctx.historyFiltersOutsideDown(h.pointer()); up({ pointerId: 2 }); assert.equal(h.state().open, true);
  h.ctx.historyFiltersOutsideDown(h.pointer(h.blank, { button: 2 })); up(); assert.equal(h.state().open, true);
  h.ctx.historyFiltersOutsideDown(h.pointer(h.blank, { isPrimary: false })); up(); assert.equal(h.state().open, true);
  h.ctx.historyFiltersOutsideDown(h.pointer(h.blank, { clientX: 1280 })); up(); assert.equal(h.state().open, true);
});

test('an upper layer present on pointerdown prevents the same click dismissing the underlying drawer', () => {
  const h = harness(); h.open(); h.document.upperModal = {};
  h.ctx.historyFiltersOutsideDown(h.pointer());
  h.document.upperModal = null; // An upper-layer document handler closes it.
  h.ctx.historyFiltersOutsideUp(h.pointer()); assert.equal(h.state().open, true);
  h.ctx.historyFiltersOutsideDown(h.pointer()); h.document.upperModal = {};
  h.ctx.historyFiltersOutsideUp(h.pointer()); assert.equal(h.state().open, true);
  assert.match(filtersSource, /window\.addEventListener\("pointerdown", historyFiltersOutsideDown, true\)/);
  for (const event of ['pointercancel', 'scroll', 'dragstart', 'wheel']) assert.match(filtersSource, new RegExp(`addEventListener\\("${event}", historyFiltersOutsideCancel`));
});
