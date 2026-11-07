const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const app = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function harness({ reduced = false, load = async () => ({}), copyHeight = 800, headerHeight = 64, footerHeight = 73, viewportCap = 420, parentScale = 1 } = {}) {
  const frames = new Map(), observers = [], keys = {}; let now = 0, serial = 0;
  const classList = () => { const values = new Set(); return { add: value => values.add(value), remove: value => values.delete(value), contains: value => values.has(value) }; };
  const style = () => ({ setProperty(name, value) { this[name] = value; }, removeProperty(name) { delete this[name]; if (name === 'will-change') delete this.willChange; } });
  const document = { activeElement: null, addEventListener() {}, contains: () => true };
  const button = (id, controls) => ({ id, tabIndex: 0, disabled: false, expanded: 'false',
    getAttribute(name) { return name === 'aria-controls' ? controls : this.expanded; },
    setAttribute(name, value) { if (name === 'aria-expanded') this.expanded = value; },
    closest() { return this; }, focus(options) { document.activeElement = this; this.focusOptions = options; },
  });
  const makePanel = (id, height) => {
    const item = button(`${id}-first`);
    const panel = { id, height, hidden: true, inert: true, dataset: {}, style: style(), classList: classList(),
      querySelectorAll: () => [item], contains: value => value === item };
    item.closest = () => panel.hidden || panel.inert ? panel : null;
    item.getClientRects = () => panel.hidden ? [] : [{}];
    panel.item = item; return panel;
  };
  const replay = makePanel('replayDrawer', 240), variant = makePanel('variantDrawer', 520), more = makePanel('detailMoreActions', 160);
  const panels = [replay, variant, more];
  const replayButton = button('reproduceImage', replay.id), variantButton = button('variantButton', variant.id), moreButton = button('more', more.id);
  const content = { scrollTop: 0, querySelectorAll: () => panels,
    get offsetHeight() { return Math.min(viewportCap, parseFloat(root.style['--detail-fold-room'] || Infinity), panels.find(panel => !panel.hidden)?.height || 0); },
    getBoundingClientRect() { return { height: this.offsetHeight * parentScale }; } };
  const header = { offsetHeight: headerHeight }, footer = { offsetHeight: footerHeight, addEventListener: (name, handler) => { keys[name] = handler; } };
  const copy = { clientHeight: copyHeight, querySelector: selector => selector === '.detail-header' ? header : footer };
  const root = { id: 'detailActionFold', hidden: true, inert: true, isConnected: true, dataset: {}, style: style(),
    parentElement: copy,
    get offsetHeight() { return this.hidden ? 0 : this.style.height !== undefined ? parseFloat(this.style.height) : content.offsetHeight; },
    getBoundingClientRect() { return { height: this.offsetHeight * parentScale }; } };
  document.getElementById = id => ({ detailActionFold: root, detailActionFoldContent: content })[id];
  document.querySelectorAll = selector => selector.includes('data-detail-action') ? [moreButton] : [more];
  document.querySelector = selector => selector.includes('data-detail-action') ? moreButton : more;
  document.activeElement = moreButton;
  const ctx = vm.createContext({ document, selectedRecord: { id: 7 }, regenerationTrigger: null,
    ui: { replayDrawer: replay, variantDrawer: variant, reproduceImage: replayButton, variantButton,
      detailDialog: { classList: classList() }, detailScroll: { scrollTop: 90 }, confirmReplay: replay.item },
    historyDetailVisible: () => true, loadRegenerationOptions: load,
    performance: { now: () => now }, matchMedia: () => ({ matches: reduced, addEventListener() {} }),
    requestAnimationFrame: callback => { frames.set(++serial, callback); return serial; }, cancelAnimationFrame: id => frames.delete(id),
    ResizeObserver: class { constructor(callback) { this.callback = callback; this.nodes = []; observers.push(this); } observe(node) { this.connected = true; this.nodes.push(node); } disconnect() { this.connected = false; } },
  });
  for (const file of ['motion.js', 'detail-actions.js']) vm.runInContext(fs.readFileSync(path.join(__dirname, '../static', file), 'utf8'), ctx);
  vm.runInContext('globalThis.fold = DetailActionFold', ctx);
  for (const name of ['setDetailActionPanelVisible', 'collapseRegenerationDrawer', 'closeRegenerationDrawers', 'openRegenerationDrawer', 'closeDetailActionPanels', 'toggleDetailActionPanel']) {
    let start = app.indexOf(`function ${name}(`); assert.ok(start >= 0, name);
    if (app.slice(start - 6, start) === 'async ') start -= 6;
    vm.runInContext(app.slice(start, app.indexOf('\n}', start) + 2), ctx);
  }
  const step = (ms = 16) => { now += ms; const pending = [...frames.values()]; frames.clear(); pending.forEach(callback => callback(now)); };
  const settle = () => { for (let i = 0; frames.size && i < 200; i++) step(); assert.equal(frames.size, 0); };
  return { ctx, root, content, copy, header, footer, panels, replay, variant, more, replayButton, variantButton, moreButton, document, observers, keys, frames, step, settle,
    resize() { for (const observer of observers) if (observer.connected) observer.callback(); } };
}

test('the shared fold expands by height without scrolling metadata or moving pointer focus', () => {
  const h = harness(); h.ctx.toggleDetailActionPanel('more');
  assert.equal(h.root.getBoundingClientRect().height, 0);
  h.step(80);
  assert.ok(h.root.getBoundingClientRect().height > 0 && h.root.getBoundingClientRect().height < 160);
  assert.equal(h.document.activeElement, h.moreButton);
  assert.equal(h.ctx.ui.detailScroll.scrollTop, 90);
  assert.equal(h.root.style.transform, undefined);
  h.settle(); assert.equal(h.root.style.height, undefined); assert.equal(h.root.dataset.motionState, 'open');
});

test('reopening an unfinished close preserves height and an old completion cannot hide the active fold', () => {
  const h = harness(); h.ctx.toggleDetailActionPanel('more'); h.step(80);
  h.ctx.toggleDetailActionPanel('more'); h.step(40);
  const before = h.ctx.StudioMotion.snapshot(h.root, 'detail-fold').value.height;
  assert.equal(h.more.inert, true);
  h.ctx.toggleDetailActionPanel('more');
  assert.equal(h.ctx.StudioMotion.snapshot(h.root, 'detail-fold').value.height, before);
  assert.equal(h.more.inert, false);
  h.settle(); assert.equal(h.more.hidden, false); assert.equal(h.root.hidden, false);
  h.ctx.closeDetailActionPanels({ immediate: true });
  assert.equal(h.root.hidden, true); assert.equal(h.more.hidden, true); assert.equal(h.more.inert, true);
  assert.equal(h.root.style.height, undefined); assert.equal(h.root.style.willChange, undefined);
});

test('switching actions exposes one panel and resource checks cannot reopen or focus an old action', async () => {
  let resolve; const pending = new Promise(done => { resolve = done; });
  const h = harness({ load: () => pending });
  const opening = h.ctx.openRegenerationDrawer('replay', h.replayButton); h.step(90);
  const before = h.root.getBoundingClientRect().height;
  h.ctx.toggleDetailActionPanel('more');
  assert.equal(h.root.getBoundingClientRect().height, before);
  assert.deepEqual(h.panels.map(panel => panel.hidden), [true, true, false]);
  assert.deepEqual(h.panels.map(panel => panel.inert), [true, true, false]);
  resolve({}); await opening; h.settle();
  assert.equal(h.document.activeElement, h.moreButton);
  assert.equal(h.more.hidden, false); assert.equal(h.replayButton.expanded, 'false');
});

test('settled and in-flight content changes retarget height and retain the viewport cap', () => {
  const h = harness(); h.ctx.toggleDetailActionPanel('more'); h.settle();
  h.more.height = 300; h.resize();
  assert.equal(h.root.getBoundingClientRect().height, 160, 'resize begins at the last displayed height');
  h.step(70); const before = h.root.getBoundingClientRect().height;
  h.more.height = 700; h.resize();
  assert.equal(h.root.getBoundingClientRect().height, before, 'retargeting preserves motion');
  h.settle(); assert.equal(h.root.getBoundingClientRect().height, 420);
  h.ctx.closeDetailActionPanels({ immediate: true }); assert.ok(h.observers.every(observer => !observer.connected));
});

test('reduced motion settles immediately and ArrowUp explicitly enters the open fold', () => {
  const h = harness({ reduced: true }); h.ctx.toggleDetailActionPanel('more');
  assert.equal(h.frames.size, 0); assert.equal(h.root.dataset.motionState, 'open');
  assert.equal(h.document.activeElement, h.moreButton);
  let prevented = false; h.keys.keydown({ key: 'ArrowUp', target: h.moreButton, preventDefault() { prevented = true; } });
  assert.equal(prevented, true); assert.equal(h.document.activeElement, h.more.item);
  assert.equal(h.more.item.focusOptions.preventScroll, true);
  h.ctx.closeDetailActionPanels({ restoreFocus: true });
  assert.equal(h.document.activeElement, h.moreButton); assert.equal(h.root.hidden, true);
  assert.equal(h.frames.size, 0);
});

test('a 320x568 detail keeps its two-row dock visible and leaves metadata room when actions expand', async () => {
  const h = harness({ copyHeight: 372, headerHeight: 53, footerHeight: 117, viewportCap: 568 * .4 });
  await h.ctx.openRegenerationDrawer('variant', h.variantButton); h.settle();
  assert.equal(h.root.style['--detail-fold-room'], '138px');
  assert.equal(h.root.getBoundingClientRect().height, 138);
  assert.equal(h.copy.clientHeight - h.header.offsetHeight - h.footer.offsetHeight - h.root.getBoundingClientRect().height, 64);
  assert.equal(h.ctx.ui.detailScroll.scrollTop, 90);
  const watched = h.observers.at(-1).nodes;
  assert.ok(watched.includes(h.copy) && watched.includes(h.footer) && watched.includes(h.header));
  h.copy.clientHeight = 336; h.footer.offsetHeight = 125; h.resize(); h.settle();
  assert.equal(h.root.getBoundingClientRect().height, 94, 'orientation and safe-area changes reduce the content viewport');
  h.resize(); assert.equal(h.frames.size, 0, 'unchanged layout does not restart a resize loop');
  assert.equal(h.ctx.ui.detailScroll.scrollTop, 90);
  h.ctx.closeRegenerationDrawers(false, true);
  assert.equal(h.root.hidden, true); assert.ok(h.observers.every(observer => !observer.connected));
});

test('opening actions during the detail entrance uses layout heights rather than scaled screen bounds', () => {
  const h = harness({ parentScale: .985 });
  h.ctx.toggleDetailActionPanel('more');
  assert.equal(h.ctx.StudioMotion.snapshot(h.root, 'detail-fold').target.height, 160);
  h.step(80);
  const current = h.ctx.StudioMotion.snapshot(h.root, 'detail-fold').value.height;
  assert.notEqual(h.root.getBoundingClientRect().height, current);
  h.ctx.toggleDetailActionPanel('more');
  assert.equal(h.ctx.StudioMotion.snapshot(h.root, 'detail-fold').value.height, current);
  h.step(30); const returning = h.ctx.StudioMotion.snapshot(h.root, 'detail-fold').value.height;
  h.ctx.toggleDetailActionPanel('more');
  assert.equal(h.ctx.StudioMotion.snapshot(h.root, 'detail-fold').value.height, returning);
  h.settle();
  assert.equal(h.root.offsetHeight, 160);
  assert.equal(h.ctx.StudioMotion.snapshot(h.root, 'detail-fold').value.height, h.root.offsetHeight,
    'removing the temporary height does not jump to a different natural size');
  assert.equal(h.root.style.height, undefined);
});
