const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = name => fs.readFileSync(path.join(__dirname, '../static', name), 'utf8');

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, handler) { if (!this.listeners.has(name)) this.listeners.set(name, []); this.listeners.get(name).push(handler); }
  dispatch(name, values = {}) {
    const event = { target: this, preventDefault() { this.defaultPrevented = true; }, ...values };
    for (const handler of this.listeners.get(name) || []) handler(event);
    return event;
  }
}
function harness() {
  let time = 0, frameId = 0;
  const frames = new Map(), elements = new Map(), loads = [], focusLog = [];
  const document = new Events(), media = new Events(); media.matches = false;
  const node = (tag = 'div', id = '') => {
    const attributes = new Map(), classes = new Set();
    const result = Object.assign(new Events(), {
      tagName: tag.toUpperCase(), id, children: [], dataset: {}, hidden: false, inert: false, disabled: false, scrollTop: 0,
      style: { setProperty(key, value) { this[key] = value; }, removeProperty(key) { delete this[key]; } },
      classList: { add: (...names) => names.forEach(name => classes.add(name)), remove: (...names) => names.forEach(name => classes.delete(name)), contains: name => classes.has(name), toggle(name, yes) { yes = yes ?? !classes.has(name); yes ? classes.add(name) : classes.delete(name); return yes; } },
      setAttribute(name, value) { attributes.set(name, String(value)); }, getAttribute: name => attributes.get(name), removeAttribute: name => attributes.delete(name),
      matches(selector) {
        if (selector.includes(',')) return selector.split(',').some(item => this.matches(item.trim()));
        if (selector === '[hidden]') return this.hidden;
        if (selector === '[inert]') return this.inert;
        if (selector.startsWith('.')) return classes.has(selector.slice(1));
        const attribute = selector.match(/^\[data-([a-z-]+)(?:="([^"]*)")?\]$/);
        if (attribute) { const key = attribute[1].replace(/-([a-z])/g, (_, c) => c.toUpperCase()); return key in this.dataset && (attribute[2] === undefined || this.dataset[key] === attribute[2]); }
        return selector.toUpperCase() === this.tagName;
      },
      closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; },
      contains(child) { return child === this || this.children.some(item => item.contains(child)); },
      querySelectorAll(selector) { return this.children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); },
      querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
      append(...children) { children.forEach(child => this.insertBefore(child, null)); },
      insertBefore(child, before) { child.remove(); const index = before ? this.children.indexOf(before) : this.children.length; this.children.splice(index, 0, child); child.parentElement = this; },
      replaceChildren(...children) { for (const child of [...this.children]) child.remove(); this.append(...children); },
      remove() { if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1); this.parentElement = null; },
      before(other) { this.parentElement.insertBefore(other, this); },
      replaceWith(other) { this.before(other); this.remove(); },
      focus() { document.activeElement = this; focusLog.push(this.id); },
      getClientRects() { return this.closest('[hidden], [inert]') ? [] : [{}]; },
      getBoundingClientRect() { return { left: 0, top: 0, right: 300, bottom: 400, width: 300, height: 400 }; },
      showModal() { this.open = true; }, close() { this.open = false; this.dispatch('close'); },
    });
    Object.defineProperty(result, 'className', { get: () => [...classes].join(' '), set: value => { classes.clear(); value.split(/\s+/).filter(Boolean).forEach(name => classes.add(name)); } });
    Object.defineProperty(result, 'isConnected', { get: () => result === document.body || Boolean(result.parentElement?.isConnected) });
    if (id) elements.set(id, result);
    return result;
  };
  document.body = node('body'); document.documentElement = node('html');
  document.hidden = false; document.getElementById = id => elements.get(id);
  document.createElement = tag => node(tag); document.createComment = () => node('#comment'); document.createTextNode = text => Object.assign(node('#text'), { textContent: text });
  document.querySelector = selector => document.body.querySelector(selector); document.querySelectorAll = selector => document.body.querySelectorAll(selector);
  const host = node('dialog', 'detailDialog'); host.open = true; document.body.append(host);
  const title = node('h2', 'historyDetailTitle'), back = node('button', 'historyDetailBack'); host.append(title, back);
  const ids = { detail: 'historyDetailView', delete: 'historyDeletePanel' };
  const views = Object.fromEntries(Object.entries(ids).map(([name, id]) => { const view = node('section', id); view.hidden = name !== 'detail'; host.append(view); return [name, view]; }));
  for (const id of ['historyDeleteMode', 'historyDeleteResults', 'historyDeleteSummary', 'historyDeleteWarning', 'historyDeleteSubmit', 'historyDeleteForm']) views.delete.append(node('div', id));
  const archive = node('div', 'randomArchive'); document.body.append(archive);
  const group = node('div', 'historyWorkbenchFilter'); archive.append(group);
  const sources = ['', 'single', 'random'].map((source, index) => { const button = node('button', `source-${source || 'all'}`); button.dataset.historySource = source; button.offsetLeft = index * 100; button.offsetWidth = 100; group.append(button); return button; });
  const thumb = node('span'); thumb.className = 'history-source-thumb'; thumb.offsetLeft = 0; group.append(thumb);
  const notices = [], window = { scrollX: 0, scrollY: 480, scrollTo(value) { this.scrollX = value.left; this.scrollY = value.top; } };
  const ctx = vm.createContext({ document, window, config: {}, historyItems: [], historyPage: 3, historyPages: 1, historyRequest: 0, historyDirty: false, historyCache: new Map(), selectedRecord: null,
    ui: { gallery: { children: [], querySelector: () => null }, detailDialog: host },
    loadHistory: async page => loads.push(page), renderHistoryPage: (data, view) => { view.items = data.items; }, request: async () => ({}), renderDetail() {}, toast: value => notices.push(value),
    performance: { now: () => time }, matchMedia: () => media,
    requestAnimationFrame(callback) { frames.set(++frameId, callback); return frameId; }, cancelAnimationFrame(id) { frames.delete(id); },
    Option: function (text, value) { return Object.assign(node('option'), { text, value }); },
  });
  vm.runInContext(read('motion.js'), ctx);
  const app = read('app.js'), historyStart = app.indexOf('function historyViewState(');
  vm.runInContext(app.slice(historyStart, app.indexOf('\n}', historyStart) + 2), ctx);
  vm.runInContext(read('history-detail.js'), ctx);
  ctx.motionDialog = (dialog, open, options) => ctx.StudioMotion.dialog(dialog, open, options);
  const state = vm.runInContext('historyDetailState', ctx);
  const step = (ms = 1000 / 60) => { time += ms; const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(callback => callback(time)); };
  const drain = () => { let limit = 500; while (frames.size && limit--) step(); assert.ok(limit > 0, 'all motion must settle'); };
  return { ctx, state, elements, node, document, media, host, views, sources, group, thumb, frames, step, drain, loads, focusLog, notices, window };
}
const plain = value => JSON.parse(JSON.stringify(value));





test('detail/delete reversals retain presentation and velocity and only the latest view stays interactive', () => {
  const h = harness();
  h.ctx.setHistoryDetailView('delete'); h.step(70);
  h.ctx.setHistoryDetailView('detail', { returning: true }); h.step(40);
  const before = h.ctx.StudioMotion.snapshot(h.views.delete, 'presence');
  h.ctx.setHistoryDetailView('delete');
  const after = h.ctx.StudioMotion.snapshot(h.views.delete, 'presence');
  assert.deepEqual(plain(after.value), plain(before.value)); assert.deepEqual(plain(after.velocity), plain(before.velocity));
  assert.equal(h.views.detail.inert, true); assert.equal(h.views.delete.inert, false);
  assert.equal(h.document.activeElement.id, 'historyDetailTitle');
  h.drain();
  for (const [name, view] of Object.entries(h.views)) { assert.equal(view.hidden, name !== 'delete'); assert.equal(view.style.transform, ''); }
  assert.equal(h.frames.size, 0);
});

test('returning from deletion restores More when the original delete item was hidden', () => {
  const h = harness();
  const more = h.node('button', 'more'), panel = h.node('div', 'more-panel'), item = h.node('button', 'delete-item');
  more.dataset.detailAction = 'more'; panel.dataset.detailPanel = 'more'; panel.append(item); h.views.detail.append(more, panel);
  h.ctx.selectedRecord = { id: 1 }; h.ctx.closeDetailActionPanels = () => { panel.hidden = true; }; item.focus();
  h.ctx.setHistoryDetailView('delete'); h.step(50); h.ctx.historyDetailBack();
  assert.equal(h.document.activeElement, more); h.drain(); assert.equal(h.document.activeElement, more);
});

test('closing during a view transition settles all views without late focus', () => {
  const h = harness();
  h.ctx.setHistoryDetailView('delete'); h.step(60);
  h.host.open = false; h.ctx.resetHistoryDetail(); const focused = h.document.activeElement;
  assert.equal(h.frames.size, 0); h.drain(); assert.equal(h.document.activeElement, focused);
  assert.equal(h.views.delete.hidden, true); assert.equal(h.views.detail.hidden, false);
  assert.equal(h.state.record, null); assert.equal(h.state.owner, null);
});

test('reduced motion and hidden pages settle the current view without residual frames or transforms', () => {
  for (const reduce of [true, false]) {
    const h = harness(); h.ctx.setHistoryDetailView('delete'); h.step(60); h.ctx.setHistoryDetailView('detail', { returning: true });
    if (reduce) { h.media.matches = true; h.media.dispatch('change'); } else { h.document.hidden = true; h.document.dispatch('visibilitychange'); }
    assert.equal(h.frames.size, 0); assert.equal(h.views.detail.hidden, false); assert.equal(h.views.delete.hidden, true);
    for (const view of Object.values(h.views)) { assert.equal(view.style.transform, ''); assert.equal(view.style.willChange, ''); }
  }
});
